/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef AGENT_EDIT_TRANSACTION_MANAGER_H
#define AGENT_EDIT_TRANSACTION_MANAGER_H

#include <functional>

#include <QJsonArray>
#include <QJsonObject>
#include <QObject>
#include <QString>

namespace ghostwriter
{
class DocumentManager;
class MarkdownEditor;

/**
 * Owns every Story Intelligence manuscript mutation.
 *
 * The model never writes to QTextDocument directly. A transaction creates a
 * durable pre-edit snapshot, groups all edits into one normal Qt undo block,
 * records before/after hashes and revisions, and optionally schedules the
 * existing autosave path after success.
 */
class AgentEditTransactionManager : public QObject
{
    Q_OBJECT

public:
    AgentEditTransactionManager(
        MarkdownEditor *editor,
        DocumentManager *documentManager,
        QObject *parent = nullptr);

    void setProjectRoot(const QString &root);
    QString projectRoot() const;
    bool inTransaction() const;

    /**
     * Applies exact UTF-16 replacements. Each array item must contain
     * start_utf16, end_utf16, expected and replacement. Targets are validated
     * against the current document before any write occurs.
     */
    QJsonObject applyVerifiedReplacements(
        const QJsonArray &replacements,
        const QString &summary,
        const QString &toolId);

    /**
     * Wraps an existing native editor command in the same checkpoint + grouped
     * undo semantics. Intended for commands such as indent/unindent where the
     * editor already owns the mutation logic.
     */
    QJsonObject runEditorCommand(
        const QString &summary,
        const QString &toolId,
        const std::function<void()> &command);

    /**
     * Undo one specific journaled agent transaction only when the current
     * manuscript exactly matches that transaction's recorded post-edit hash.
     * This prevents an old activity-card button from undoing later human work.
     */
    QJsonObject undoTransaction(const QString &operationId);

    QJsonArray recentTransactions(int limit = 20) const;

signals:
    void transactionStarted(const QJsonObject &transaction);
    void transactionApplied(const QJsonObject &transaction);
    void transactionFailed(const QJsonObject &transaction);

private:
    struct Replacement {
        int start = 0;
        int end = 0;
        QString expected;
        QString replacement;
    };

    QString checkpointDirectory() const;
    QString writeCheckpoint(
        const QString &operationId,
        const QString &summary,
        const QString &beforeHash,
        QString *errorMessage);
    void pruneCheckpoints() const;
    void scheduleAutosave();
    QJsonObject beginTransactionRecord(
        const QString &operationId,
        const QString &summary,
        const QString &toolId,
        const QString &beforeHash) const;
    QJsonObject finishTransactionRecord(
        QJsonObject record,
        const QString &checkpointPath,
        int replacementCount = 0);
    static QString textHash(const QString &text);
    static QString boundedSummary(const QString &summary);
    static QJsonObject failureRecord(
        const QString &toolId,
        const QString &summary,
        const QString &message);

    MarkdownEditor *m_editor;
    DocumentManager *m_documentManager;
    QString m_projectRoot;
    QJsonArray m_recentTransactions;
    bool m_inTransaction{false};
};
}

#endif
