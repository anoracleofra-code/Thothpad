/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef STORY_TOOL_HARNESS_H
#define STORY_TOOL_HARNESS_H

#include <utility>

#include <QJsonArray>
#include <QJsonObject>
#include <QObject>
#include <QString>

class QAction;
class QMainWindow;
class KActionCollection;

namespace ghostwriter
{
class AgentEditTransactionManager;
class DocumentManager;
class MarkdownEditor;
class ProseAwarenessWidget;
class ProseController;

/**
 * Declarative, allowlisted native tool surface for Story Intelligence.
 *
 * The model never receives QAction/QObject pointers or arbitrary command
 * names. It requests one of the manifest IDs; this object validates arguments,
 * enforces the caller-provided edit authorization and invokes the same native
 * application paths used by the human UI.
 */
class StoryToolHarness : public QObject
{
    Q_OBJECT

public:
    StoryToolHarness(
        QMainWindow *window,
        MarkdownEditor *editor,
        DocumentManager *documentManager,
        ProseController *proseController,
        ProseAwarenessWidget *proseWidget,
        AgentEditTransactionManager *transactions,
        QObject *parent = nullptr);

    QJsonArray manifest() const;
    QJsonObject snapshot() const;

    /**
     * Read the current prose state for native UI/authorization decisions
     * without advancing the manuscript revision exposed to the model.
     */
    QJsonObject proseSnapshot(int findingLimit = 60) const
    {
        return proseState(findingLimit);
    }

    /**
     * Mutation tools are only valid against the manuscript revision exposed
     * to the model. Any intervening human or native edit forces a fresh model
     * turn before Story Intelligence may mutate the document.
     */
    static bool mutationRevisionCurrent(int exposedRevision, int currentRevision)
    {
        return exposedRevision >= 0 && exposedRevision == currentRevision;
    }

    /**
     * Any native tool call returned by the model must still target the exact
     * document context that was exposed for that model turn. This prevents a
     * late response from acting on a different manuscript after navigation,
     * while keeping harmless text-only responses non-destructive.
     */
    static bool modelDocumentContextCurrent(
        int exposedRevision,
        int currentRevision,
        const QString &exposedPath,
        const QString &currentPath)
    {
        return mutationRevisionCurrent(exposedRevision, currentRevision)
            && exposedPath == currentPath;
    }

    /**
     * Native-only progress state used by StoryIntelligenceController to resume
     * asynchronous tool rounds. This object is deliberately not inserted into
     * the model prompt.
     */
    QJsonObject completionState() const;

    QJsonObject execute(
        const QString &toolId,
        const QJsonObject &arguments = {},
        bool allowBoundedEdits = false,
        bool allowBulkEdits = false);

signals:
    void toolExecuted(const QString &toolId, const QJsonObject &result);

private:
    QAction *action(const QString &stableId) const;
    QJsonObject appState() const;
    QJsonObject editorState() const;
    QJsonObject proseState(int findingLimit = 60) const;
    QJsonObject setCheckedAction(const QString &stableId, bool desired);
    QJsonObject setPanelVisible(const QString &panel, bool visible);
    QJsonObject navigateRange(int start, int end, bool select);
    QJsonObject runProseScan(const QJsonObject &arguments);
    QJsonObject hydrateProseCategory(const QJsonObject &arguments);
    QJsonObject runIndent(bool wholeDocument, bool unindent, bool allowBulkEdits, bool allowBoundedEdits);
    QJsonObject findProseFinding(const QString &findingId, bool navigate) const;
    QJsonObject applyObjectiveGrammarFixes(bool allowBulkEdits);
    QString modelSafeDocumentPath() const;
    static QJsonObject success(const QJsonObject &payload = {});
    static QJsonObject failure(const QString &message, const QString &code = QStringLiteral("tool_error"));

    QMainWindow *m_window;
    MarkdownEditor *m_editor;
    DocumentManager *m_documentManager;
    ProseController *m_proseController;
    ProseAwarenessWidget *m_proseWidget;
    AgentEditTransactionManager *m_transactions;
    KActionCollection *m_actions;
    mutable int m_modelDocumentRevision = -1;
};
}

#endif
