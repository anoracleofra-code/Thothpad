/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "documentactivitytracker.h"

#include "agentedittransactionmanager.h"
#include "../editor/markdowneditor.h"

#include <algorithm>

#include <QCryptographicHash>
#include <QDateTime>
#include <QJsonArray>
#include <QTextBlock>
#include <QTextCursor>
#include <QTextDocument>

namespace ghostwriter
{
namespace
{
constexpr int MaximumActivityEvents = 100;
constexpr int MaximumTrackedTransactions = 24;
constexpr int MaximumExcerptLength = 180;
constexpr int MinimumMeaningfulDeletion = 4;
constexpr int MinimumMeaningfulReplacement = 4;

int changeEnd(int position, int removed, int added)
{
    return position + std::max(removed, added);
}
}

DocumentActivityTracker::DocumentActivityTracker(
    MarkdownEditor *editor,
    AgentEditTransactionManager *transactions,
    QObject *parent)
    : QObject(parent)
    , m_editor(editor)
    , m_transactions(transactions)
    , m_shadowText(editor ? editor->toPlainText() : QString())
    , m_lastSettledHash(textHash(m_shadowText))
{
    Q_ASSERT(m_editor);
    Q_ASSERT(m_transactions);

    m_hashTimer.setSingleShot(true);
    m_hashTimer.setInterval(120);

    connect(m_editor->document(), &QTextDocument::contentsChange,
            this, &DocumentActivityTracker::documentChanged);
    connect(m_transactions, &AgentEditTransactionManager::transactionStarted,
            this, &DocumentActivityTracker::transactionStarted);
    connect(m_transactions, &AgentEditTransactionManager::transactionApplied,
            this, &DocumentActivityTracker::transactionApplied);
    connect(m_transactions, &AgentEditTransactionManager::transactionFailed,
            this, &DocumentActivityTracker::transactionFailed);
    connect(&m_hashTimer, &QTimer::timeout,
            this, &DocumentActivityTracker::detectUndoRedo);
}

void DocumentActivityTracker::resetForContext()
{
    m_hashTimer.stop();
    m_events = {};
    m_recentAgentTransactions = {};
    m_agentMutationDepth = 0;
    m_shadowText = m_editor->toPlainText();
    m_lastSettledHash = textHash(m_shadowText);
}

QString DocumentActivityTracker::textHash(const QString &text)
{
    return QString::fromLatin1(
        QCryptographicHash::hash(text.toUtf8(), QCryptographicHash::Sha256).toHex());
}

QString DocumentActivityTracker::boundedExcerpt(const QString &text)
{
    QString normalized = text;
    normalized.replace(QChar(0x2029), QChar('\n'));
    if (normalized.size() <= MaximumExcerptLength) {
        return normalized;
    }
    return normalized.left(MaximumExcerptLength - 1) + QChar(0x2026);
}

QString DocumentActivityTracker::insertedText(int position, int charsAdded) const
{
    if (charsAdded <= 0) {
        return {};
    }
    QTextCursor cursor(m_editor->document());
    const int maximum = std::max(0, m_editor->document()->characterCount() - 1);
    const int start = std::max(0, std::min(position, maximum));
    const int end = std::max(start, std::min(position + charsAdded, maximum));
    cursor.setPosition(start);
    cursor.setPosition(end, QTextCursor::KeepAnchor);
    QString result = cursor.selectedText();
    result.replace(QChar(0x2029), QChar('\n'));
    return result;
}

int DocumentActivityTracker::lineForPosition(int position) const
{
    QTextBlock block = m_editor->document()->findBlock(std::max(0, position));
    return block.isValid() ? block.blockNumber() + 1 : 1;
}

void DocumentActivityTracker::appendEvent(QJsonObject event, bool visible)
{
    event.insert(QStringLiteral("timestamp_utc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs));
    event.insert(QStringLiteral("document_revision"), m_editor->document()->revision());
    event.insert(QStringLiteral("visible"), visible);
    m_events.append(event);
    while (m_events.size() > MaximumActivityEvents) {
        m_events.removeAt(0);
    }
    emit activityEvent(event);
}

bool DocumentActivityTracker::overlapsRecentAgentTarget(
    int position,
    int removed,
    int added,
    QString *operationId) const
{
    const int end = changeEnd(position, removed, added);
    for (int transactionIndex = m_recentAgentTransactions.size() - 1;
         transactionIndex >= 0;
         --transactionIndex) {
        const QJsonObject transaction = m_recentAgentTransactions.at(transactionIndex).toObject();
        const QJsonArray changes = transaction.value(QStringLiteral("changes")).toArray();
        for (const QJsonValue &changeValue : changes) {
            const QJsonObject change = changeValue.toObject();
            const int start = change.value(QStringLiteral("start_utf16")).toInt(-1);
            const int changeRangeEnd = change.value(QStringLiteral("end_utf16")).toInt(-1);
            if (start < 0 || changeRangeEnd <= start) {
                continue;
            }
            if (position < changeRangeEnd && end > start) {
                if (operationId) {
                    *operationId = transaction.value(QStringLiteral("operation_id")).toString();
                }
                return true;
            }
        }
    }
    return false;
}

void DocumentActivityTracker::documentChanged(int position, int charsRemoved, int charsAdded)
{
    const int shadowSize = static_cast<int>(m_shadowText.size());
    const int safePosition = std::max(0, std::min(position, shadowSize));
    const int safeRemoved = std::max(0, std::min(charsRemoved, shadowSize - safePosition));
    const QString removedText = m_shadowText.mid(safePosition, safeRemoved);
    const QString addedText = insertedText(position, charsAdded);
    m_shadowText.replace(safePosition, safeRemoved, addedText);

    if (m_agentMutationDepth > 0) {
        return;
    }

    QString relatedOperation;
    const bool overlapsAgent = overlapsRecentAgentTarget(
        position, charsRemoved, charsAdded, &relatedOperation);
    const bool meaningfulDeletion = charsRemoved >= MinimumMeaningfulDeletion && charsAdded == 0;
    const bool meaningfulReplacement = charsRemoved > 0
        && (charsRemoved >= MinimumMeaningfulReplacement || charsAdded >= MinimumMeaningfulReplacement);

    if (overlapsAgent || meaningfulDeletion || meaningfulReplacement) {
        QJsonObject event;
        if (overlapsAgent) {
            event.insert(QStringLiteral("type"), QStringLiteral("USER_EDITED_AGENT_TARGET"));
            event.insert(QStringLiteral("related_operation_id"), relatedOperation);
        } else if (meaningfulDeletion) {
            event.insert(QStringLiteral("type"), QStringLiteral("USER_DELETED_TEXT"));
        } else {
            event.insert(QStringLiteral("type"), QStringLiteral("USER_REPLACED_TEXT"));
        }
        event.insert(QStringLiteral("start_utf16"), position);
        event.insert(QStringLiteral("line"), lineForPosition(position));
        event.insert(QStringLiteral("before"), boundedExcerpt(removedText));
        event.insert(QStringLiteral("after"), boundedExcerpt(addedText));
        appendEvent(event, overlapsAgent);
    }

    if (!m_recentAgentTransactions.isEmpty()) {
        m_hashTimer.start();
    }
}

void DocumentActivityTracker::transactionStarted(const QJsonObject &transaction)
{
    Q_UNUSED(transaction)
    ++m_agentMutationDepth;
}

void DocumentActivityTracker::transactionApplied(const QJsonObject &transaction)
{
    if (m_agentMutationDepth > 0) {
        --m_agentMutationDepth;
    }
    m_shadowText = m_editor->toPlainText();
    m_lastSettledHash = transaction.value(QStringLiteral("after_sha256")).toString(textHash(m_shadowText));

    if (transaction.value(QStringLiteral("no_change")).toBool()) {
        return;
    }

    m_recentAgentTransactions.append(transaction);
    while (m_recentAgentTransactions.size() > MaximumTrackedTransactions) {
        m_recentAgentTransactions.removeAt(0);
    }

    QJsonObject event;
    event.insert(QStringLiteral("type"), QStringLiteral("AGENT_TRANSACTION_APPLIED"));
    event.insert(QStringLiteral("operation_id"), transaction.value(QStringLiteral("operation_id")));
    event.insert(QStringLiteral("tool_id"), transaction.value(QStringLiteral("tool_id")));
    event.insert(QStringLiteral("summary"), transaction.value(QStringLiteral("summary")));
    event.insert(QStringLiteral("replacement_count"), transaction.value(QStringLiteral("replacement_count")));
    appendEvent(event, false);
}

void DocumentActivityTracker::transactionFailed(const QJsonObject &transaction)
{
    if (m_agentMutationDepth > 0) {
        --m_agentMutationDepth;
    }
    m_shadowText = m_editor->toPlainText();
    m_lastSettledHash = textHash(m_shadowText);

    QJsonObject event;
    event.insert(QStringLiteral("type"), QStringLiteral("AGENT_TRANSACTION_FAILED"));
    event.insert(QStringLiteral("operation_id"), transaction.value(QStringLiteral("operation_id")));
    event.insert(QStringLiteral("tool_id"), transaction.value(QStringLiteral("tool_id")));
    event.insert(QStringLiteral("summary"), transaction.value(QStringLiteral("summary")));
    event.insert(QStringLiteral("error"), transaction.value(QStringLiteral("error")));
    appendEvent(event, false);
}

void DocumentActivityTracker::detectUndoRedo()
{
    if (m_recentAgentTransactions.isEmpty()) {
        return;
    }
    const QString currentHash = textHash(m_editor->toPlainText());
    if (currentHash == m_lastSettledHash) {
        return;
    }

    for (int index = m_recentAgentTransactions.size() - 1; index >= 0; --index) {
        const QJsonObject transaction = m_recentAgentTransactions.at(index).toObject();
        const QString beforeHash = transaction.value(QStringLiteral("before_sha256")).toString();
        const QString afterHash = transaction.value(QStringLiteral("after_sha256")).toString();
        if (!beforeHash.isEmpty() && currentHash == beforeHash) {
            QJsonObject event;
            event.insert(QStringLiteral("type"), QStringLiteral("USER_UNDID_AGENT_TRANSACTION"));
            event.insert(QStringLiteral("operation_id"), transaction.value(QStringLiteral("operation_id")));
            event.insert(QStringLiteral("tool_id"), transaction.value(QStringLiteral("tool_id")));
            event.insert(QStringLiteral("summary"), transaction.value(QStringLiteral("summary")));
            appendEvent(event, true);
            break;
        }
        if (!afterHash.isEmpty() && currentHash == afterHash) {
            QJsonObject event;
            event.insert(QStringLiteral("type"), QStringLiteral("USER_REDID_AGENT_TRANSACTION"));
            event.insert(QStringLiteral("operation_id"), transaction.value(QStringLiteral("operation_id")));
            event.insert(QStringLiteral("tool_id"), transaction.value(QStringLiteral("tool_id")));
            event.insert(QStringLiteral("summary"), transaction.value(QStringLiteral("summary")));
            appendEvent(event, true);
            break;
        }
    }
    m_lastSettledHash = currentHash;
}

void DocumentActivityTracker::noteSuggestionDecision(
    const QString &type,
    const QString &suggestionId,
    const QString &summary)
{
    QJsonObject event;
    event.insert(QStringLiteral("type"), type);
    event.insert(QStringLiteral("suggestion_id"), suggestionId);
    event.insert(QStringLiteral("summary"), boundedExcerpt(summary));
    appendEvent(event, true);
}

QJsonArray DocumentActivityTracker::recentEvents(int limit) const
{
    const int boundedLimit = std::max(0, std::min(limit, MaximumActivityEvents));
    QJsonArray result;
    const int start = std::max(0, static_cast<int>(m_events.size()) - boundedLimit);
    for (int index = start; index < m_events.size(); ++index) {
        result.append(m_events.at(index));
    }
    return result;
}
}
