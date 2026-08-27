/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "agentedittransactionmanager.h"

#include "../documentmanager.h"
#include "../editor/markdowndocument.h"
#include "../editor/markdowneditor.h"

#include <algorithm>
#include <exception>

#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QSaveFile>
#include <QStandardPaths>
#include <QTextCursor>
#include <QTextDocument>
#include <QTimer>
#include <QUuid>

namespace ghostwriter
{
namespace
{
constexpr int MaximumReplacementCount = 500;
constexpr qsizetype MaximumReplacementTextUnits = 2 * 1024 * 1024;
constexpr int MaximumCheckpointCount = 50;
constexpr qint64 MaximumCheckpointBytes = 200LL * 1024LL * 1024LL;
constexpr int MaximumRecentTransactions = 40;
constexpr int MaximumSummaryLength = 240;
constexpr int MaximumExcerptLength = 180;

QString excerpt(const QString &text)
{
    if (text.size() <= MaximumExcerptLength) {
        return text;
    }
    return text.left(MaximumExcerptLength - 1) + QChar(0x2026);
}
}

AgentEditTransactionManager::AgentEditTransactionManager(
    MarkdownEditor *editor,
    DocumentManager *documentManager,
    QObject *parent)
    : QObject(parent)
    , m_editor(editor)
    , m_documentManager(documentManager)
{
    Q_ASSERT(m_editor);
    Q_ASSERT(m_documentManager);
}

void AgentEditTransactionManager::setProjectRoot(const QString &root)
{
    m_projectRoot = root.isEmpty() ? QString() : QDir(root).absolutePath();
}

QString AgentEditTransactionManager::projectRoot() const
{
    return m_projectRoot;
}

bool AgentEditTransactionManager::inTransaction() const
{
    return m_inTransaction;
}

QString AgentEditTransactionManager::textHash(const QString &text)
{
    return QString::fromLatin1(
        QCryptographicHash::hash(text.toUtf8(), QCryptographicHash::Sha256).toHex());
}

QString AgentEditTransactionManager::boundedSummary(const QString &summary)
{
    const QString trimmed = summary.trimmed();
    if (trimmed.size() <= MaximumSummaryLength) {
        return trimmed;
    }
    return trimmed.left(MaximumSummaryLength - 1) + QChar(0x2026);
}

QJsonObject AgentEditTransactionManager::failureRecord(
    const QString &toolId,
    const QString &summary,
    const QString &message)
{
    QJsonObject result;
    result.insert(QStringLiteral("ok"), false);
    result.insert(QStringLiteral("tool_id"), toolId);
    result.insert(QStringLiteral("summary"), boundedSummary(summary));
    result.insert(QStringLiteral("error"), message);
    return result;
}

QString AgentEditTransactionManager::checkpointDirectory() const
{
    if (!m_projectRoot.isEmpty()) {
        return QDir(m_projectRoot).filePath(QStringLiteral(".thothpad/recovery"));
    }
    return QDir(QStandardPaths::writableLocation(QStandardPaths::AppLocalDataLocation))
        .filePath(QStringLiteral("agent-recovery"));
}

QString AgentEditTransactionManager::writeCheckpoint(
    const QString &operationId,
    const QString &summary,
    const QString &beforeHash,
    QString *errorMessage)
{
    const QString directoryPath = checkpointDirectory();
    QDir directory(directoryPath);
    if (!directory.exists() && !directory.mkpath(QStringLiteral("."))) {
        if (errorMessage) {
            *errorMessage = tr("Could not create the agent recovery directory.");
        }
        return {};
    }

    const QString stamp = QDateTime::currentDateTimeUtc().toString(QStringLiteral("yyyyMMdd'T'HHmmsszzz'Z'"));
    const QString baseName = QStringLiteral("%1-%2.snapshot").arg(stamp, operationId);
    const QString snapshotPath = directory.filePath(baseName);
    const QByteArray bytes = m_editor->toPlainText().toUtf8();

    QSaveFile snapshot(snapshotPath);
    if (!snapshot.open(QIODevice::WriteOnly) || snapshot.write(bytes) != bytes.size() || !snapshot.commit()) {
        if (errorMessage) {
            *errorMessage = snapshot.errorString().isEmpty()
                ? tr("Could not write the pre-edit recovery snapshot.")
                : snapshot.errorString();
        }
        snapshot.cancelWriting();
        return {};
    }

    QJsonObject metadata;
    metadata.insert(QStringLiteral("schema"), 1);
    metadata.insert(QStringLiteral("operation_id"), operationId);
    metadata.insert(QStringLiteral("summary"), boundedSummary(summary));
    metadata.insert(QStringLiteral("created_at_utc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs));
    metadata.insert(QStringLiteral("document_path"), m_documentManager->document()->filePath());
    metadata.insert(QStringLiteral("document_revision"), m_editor->document()->revision());
    metadata.insert(QStringLiteral("sha256"), beforeHash);
    metadata.insert(QStringLiteral("snapshot_file"), QFileInfo(snapshotPath).fileName());

    const QString metadataPath = snapshotPath + QStringLiteral(".json");
    const QByteArray metadataBytes = QJsonDocument(metadata).toJson(QJsonDocument::Indented);
    QSaveFile metadataFile(metadataPath);
    if (!metadataFile.open(QIODevice::WriteOnly)
        || metadataFile.write(metadataBytes) != metadataBytes.size()
        || !metadataFile.commit()) {
        QFile::remove(snapshotPath);
        if (errorMessage) {
            *errorMessage = metadataFile.errorString().isEmpty()
                ? tr("Could not write recovery snapshot metadata.")
                : metadataFile.errorString();
        }
        metadataFile.cancelWriting();
        return {};
    }

    pruneCheckpoints();
    return snapshotPath;
}

void AgentEditTransactionManager::pruneCheckpoints() const
{
    QDir directory(checkpointDirectory());
    const QFileInfoList snapshots = directory.entryInfoList(
        QStringList{QStringLiteral("*.snapshot")},
        QDir::Files | QDir::Readable,
        QDir::Time);

    qint64 retainedBytes = 0;
    for (int index = 0; index < snapshots.size(); ++index) {
        const QFileInfo &info = snapshots.at(index);
        const bool keepByCount = index < MaximumCheckpointCount;
        const bool keepByBytes = retainedBytes + info.size() <= MaximumCheckpointBytes;
        if (keepByCount && keepByBytes) {
            retainedBytes += info.size();
            continue;
        }
        QFile::remove(info.absoluteFilePath());
        QFile::remove(info.absoluteFilePath() + QStringLiteral(".json"));
    }
}

QJsonObject AgentEditTransactionManager::beginTransactionRecord(
    const QString &operationId,
    const QString &summary,
    const QString &toolId,
    const QString &beforeHash) const
{
    QJsonObject record;
    record.insert(QStringLiteral("ok"), true);
    record.insert(QStringLiteral("operation_id"), operationId);
    record.insert(QStringLiteral("tool_id"), toolId);
    record.insert(QStringLiteral("summary"), boundedSummary(summary));
    record.insert(QStringLiteral("started_at_utc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs));
    record.insert(QStringLiteral("document_path"), m_documentManager->document()->filePath());
    record.insert(QStringLiteral("before_revision"), m_editor->document()->revision());
    record.insert(QStringLiteral("before_sha256"), beforeHash);
    return record;
}

QJsonObject AgentEditTransactionManager::finishTransactionRecord(
    QJsonObject record,
    const QString &checkpointPath,
    int replacementCount)
{
    const QString afterText = m_editor->toPlainText();
    record.insert(QStringLiteral("checkpoint_path"), checkpointPath);
    record.insert(QStringLiteral("after_revision"), m_editor->document()->revision());
    record.insert(QStringLiteral("after_sha256"), textHash(afterText));
    record.insert(QStringLiteral("replacement_count"), replacementCount);
    record.insert(QStringLiteral("finished_at_utc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs));

    m_recentTransactions.append(record);
    while (m_recentTransactions.size() > MaximumRecentTransactions) {
        m_recentTransactions.removeFirst();
    }
    return record;
}

void AgentEditTransactionManager::scheduleAutosave()
{
    MarkdownDocument *document = m_documentManager->document();
    if (!m_documentManager->autoSaveEnabled()
        || !document
        || document->isNew()
        || document->isReadOnly()) {
        return;
    }

    // The pre-edit checkpoint is already durable. Saving shortly after a
    // successful transaction keeps the on-disk manuscript current without
    // bypassing the user's existing autosave preference.
    QTimer::singleShot(650, m_documentManager, [manager = m_documentManager]() {
        MarkdownDocument *current = manager->document();
        if (current && !current->isNew() && !current->isReadOnly() && current->isModified()) {
            manager->saveFile();
        }
    });
}

QJsonObject AgentEditTransactionManager::applyVerifiedReplacements(
    const QJsonArray &replacements,
    const QString &summary,
    const QString &toolId)
{
    if (m_inTransaction) {
        return failureRecord(toolId, summary, tr("Another agent edit transaction is already active."));
    }
    MarkdownDocument *document = m_documentManager->document();
    if (!document || document->isReadOnly() || m_editor->isReadOnly()) {
        return failureRecord(toolId, summary, tr("The current document is read-only."));
    }
    if (replacements.isEmpty() || replacements.size() > MaximumReplacementCount) {
        return failureRecord(
            toolId,
            summary,
            tr("Agent replacements must contain between 1 and %1 changes.").arg(MaximumReplacementCount));
    }

    const QString beforeText = m_editor->toPlainText();
    QList<Replacement> parsed;
    parsed.reserve(replacements.size());
    qsizetype replacementUnits = 0;
    QJsonArray changeSummary;

    for (const QJsonValue &value : replacements) {
        if (!value.isObject()) {
            return failureRecord(toolId, summary, tr("A replacement entry was not an object."));
        }
        const QJsonObject object = value.toObject();
        const QJsonValue startValue = object.value(QStringLiteral("start_utf16"));
        const QJsonValue endValue = object.value(QStringLiteral("end_utf16"));
        if (!startValue.isDouble() || !endValue.isDouble()) {
            return failureRecord(toolId, summary, tr("Replacement offsets must be integers."));
        }
        const double startDouble = startValue.toDouble(-1);
        const double endDouble = endValue.toDouble(-1);
        const int start = static_cast<int>(startDouble);
        const int end = static_cast<int>(endDouble);
        if (startDouble != start || endDouble != end || start < 0 || end <= start || end > beforeText.size()) {
            return failureRecord(toolId, summary, tr("A replacement range is outside the current document."));
        }
        if (!object.value(QStringLiteral("expected")).isString()
            || !object.value(QStringLiteral("replacement")).isString()) {
            return failureRecord(toolId, summary, tr("Replacement text fields must be strings."));
        }
        Replacement replacement;
        replacement.start = start;
        replacement.end = end;
        replacement.expected = object.value(QStringLiteral("expected")).toString();
        replacement.replacement = object.value(QStringLiteral("replacement")).toString();
        if (replacement.expected.size() != replacement.end - replacement.start
            || beforeText.mid(replacement.start, replacement.end - replacement.start) != replacement.expected) {
            return failureRecord(toolId, summary, tr("The manuscript changed and an agent replacement is stale."));
        }
        replacementUnits += replacement.replacement.size();
        if (replacementUnits > MaximumReplacementTextUnits) {
            return failureRecord(toolId, summary, tr("The requested edit batch is too large."));
        }
        parsed.append(replacement);

        QJsonObject change;
        change.insert(QStringLiteral("start_utf16"), start);
        change.insert(QStringLiteral("end_utf16"), end);
        change.insert(QStringLiteral("before"), excerpt(replacement.expected));
        change.insert(QStringLiteral("after"), excerpt(replacement.replacement));
        changeSummary.append(change);
    }

    std::sort(parsed.begin(), parsed.end(), [](const Replacement &left, const Replacement &right) {
        return left.start > right.start;
    });
    int previousStart = beforeText.size() + 1;
    for (const Replacement &replacement : parsed) {
        if (replacement.end > previousStart) {
            return failureRecord(toolId, summary, tr("Overlapping agent replacements are not allowed."));
        }
        previousStart = replacement.start;
    }

    const QString operationId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    const QString beforeHash = textHash(beforeText);
    QJsonObject record = beginTransactionRecord(operationId, summary, toolId, beforeHash);
    record.insert(QStringLiteral("changes"), changeSummary);

    QString checkpointError;
    const QString checkpoint = writeCheckpoint(operationId, summary, beforeHash, &checkpointError);
    if (checkpoint.isEmpty()) {
        QJsonObject failed = failureRecord(toolId, summary, checkpointError);
        failed.insert(QStringLiteral("operation_id"), operationId);
        emit transactionFailed(failed);
        return failed;
    }

    record.insert(QStringLiteral("checkpoint_path"), checkpoint);
    m_inTransaction = true;
    emit transactionStarted(record);

    QTextCursor editBlock(m_editor->document());
    editBlock.beginEditBlock();
    for (const Replacement &replacement : parsed) {
        QTextCursor cursor(m_editor->document());
        cursor.setPosition(replacement.start);
        cursor.setPosition(replacement.end, QTextCursor::KeepAnchor);
        cursor.insertText(replacement.replacement);
    }
    editBlock.endEditBlock();
    m_inTransaction = false;

    record = finishTransactionRecord(record, checkpoint, parsed.size());
    emit transactionApplied(record);
    scheduleAutosave();
    return record;
}

QJsonObject AgentEditTransactionManager::runEditorCommand(
    const QString &summary,
    const QString &toolId,
    const std::function<void()> &command)
{
    if (m_inTransaction) {
        return failureRecord(toolId, summary, tr("Another agent edit transaction is already active."));
    }
    MarkdownDocument *document = m_documentManager->document();
    if (!document || document->isReadOnly() || m_editor->isReadOnly()) {
        return failureRecord(toolId, summary, tr("The current document is read-only."));
    }
    if (!command) {
        return failureRecord(toolId, summary, tr("The native edit command is unavailable."));
    }

    const QString beforeText = m_editor->toPlainText();
    const QString operationId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    const QString beforeHash = textHash(beforeText);
    QJsonObject record = beginTransactionRecord(operationId, summary, toolId, beforeHash);

    QString checkpointError;
    const QString checkpoint = writeCheckpoint(operationId, summary, beforeHash, &checkpointError);
    if (checkpoint.isEmpty()) {
        QJsonObject failed = failureRecord(toolId, summary, checkpointError);
        failed.insert(QStringLiteral("operation_id"), operationId);
        emit transactionFailed(failed);
        return failed;
    }

    record.insert(QStringLiteral("checkpoint_path"), checkpoint);
    m_inTransaction = true;
    emit transactionStarted(record);

    QTextCursor editBlock(m_editor->document());
    editBlock.beginEditBlock();
    try {
        command();
    } catch (const std::exception &exception) {
        editBlock.endEditBlock();
        m_inTransaction = false;
        m_editor->undo();
        QJsonObject failed = failureRecord(toolId, summary, QString::fromUtf8(exception.what()));
        failed.insert(QStringLiteral("operation_id"), operationId);
        failed.insert(QStringLiteral("checkpoint_path"), checkpoint);
        emit transactionFailed(failed);
        return failed;
    } catch (...) {
        editBlock.endEditBlock();
        m_inTransaction = false;
        m_editor->undo();
        QJsonObject failed = failureRecord(toolId, summary, tr("The native editor command failed."));
        failed.insert(QStringLiteral("operation_id"), operationId);
        failed.insert(QStringLiteral("checkpoint_path"), checkpoint);
        emit transactionFailed(failed);
        return failed;
    }
    editBlock.endEditBlock();
    m_inTransaction = false;

    const QString afterHash = textHash(m_editor->toPlainText());
    if (afterHash == beforeHash) {
        QFile::remove(checkpoint);
        QFile::remove(checkpoint + QStringLiteral(".json"));
        record.insert(QStringLiteral("no_change"), true);
        record.insert(QStringLiteral("after_revision"), m_editor->document()->revision());
        record.insert(QStringLiteral("after_sha256"), afterHash);
        return record;
    }

    record = finishTransactionRecord(record, checkpoint);
    emit transactionApplied(record);
    scheduleAutosave();
    return record;
}

QJsonArray AgentEditTransactionManager::recentTransactions(int limit) const
{
    const int boundedLimit = std::max(0, std::min(limit, MaximumRecentTransactions));
    QJsonArray result;
    const int start = std::max(0, m_recentTransactions.size() - boundedLimit);
    for (int index = start; index < m_recentTransactions.size(); ++index) {
        result.append(m_recentTransactions.at(index));
    }
    return result;
}
}
