/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef DOCUMENT_ACTIVITY_TRACKER_H
#define DOCUMENT_ACTIVITY_TRACKER_H

#include <QJsonArray>
#include <QJsonObject>
#include <QObject>
#include <QString>
#include <QTimer>

namespace ghostwriter
{
class AgentEditTransactionManager;
class MarkdownEditor;

/**
 * Produces bounded, local feedback events from meaningful human edits.
 *
 * Raw keystrokes are not persisted to chat. The tracker records useful
 * preference signals such as undoing an agent transaction or materially
 * replacing/deleting text, with short excerpts only.
 */
class DocumentActivityTracker : public QObject
{
    Q_OBJECT

public:
    DocumentActivityTracker(
        MarkdownEditor *editor,
        AgentEditTransactionManager *transactions,
        QObject *parent = nullptr);

    QJsonArray recentEvents(int limit = 24) const;
    void noteSuggestionDecision(
        const QString &type,
        const QString &suggestionId,
        const QString &summary);

signals:
    void activityEvent(const QJsonObject &event);

private slots:
    void documentChanged(int position, int charsRemoved, int charsAdded);
    void transactionStarted(const QJsonObject &transaction);
    void transactionApplied(const QJsonObject &transaction);
    void transactionFailed(const QJsonObject &transaction);
    void detectUndoRedo();

private:
    static QString textHash(const QString &text);
    static QString boundedExcerpt(const QString &text);
    QString insertedText(int position, int charsAdded) const;
    int lineForPosition(int position) const;
    void appendEvent(QJsonObject event, bool visible = false);
    bool overlapsRecentAgentTarget(int position, int removed, int added, QString *operationId) const;

    MarkdownEditor *m_editor;
    AgentEditTransactionManager *m_transactions;
    QString m_shadowText;
    QString m_lastSettledHash;
    QJsonArray m_events;
    QJsonArray m_recentAgentTransactions;
    QTimer m_hashTimer;
    int m_agentMutationDepth{0};
};
}

#endif
