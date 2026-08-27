/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef STORY_TOOL_HARNESS_H
#define STORY_TOOL_HARNESS_H

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
    QJsonObject runIndent(bool wholeDocument, bool unindent, bool allowBulkEdits, bool allowBoundedEdits);
    QJsonObject findProseFinding(const QString &findingId, bool navigate) const;
    static QJsonObject success(const QJsonObject &payload = {});
    static QJsonObject failure(const QString &message, const QString &code = QStringLiteral("tool_error"));

    QMainWindow *m_window;
    MarkdownEditor *m_editor;
    DocumentManager *m_documentManager;
    ProseController *m_proseController;
    ProseAwarenessWidget *m_proseWidget;
    AgentEditTransactionManager *m_transactions;
    KActionCollection *m_actions;
};
}

#endif
