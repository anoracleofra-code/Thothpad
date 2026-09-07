// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "writinganalysis.h"
#include <QFutureWatcher>
#include <QObject>
#include <QTimer>
#include <QPointer>
class QPlainTextEdit;
class QComboBox;
class QLabel;
class QLineEdit;
class QPushButton;
class QTreeWidget;
class QTreeWidgetItem;
class QWidget;
class VisualShellTest;
namespace ghostwriter
{
class MarkdownEditor;
class ProseAwarenessWidget;
class StoryIntelligenceController;
class AgentEditTransactionManager;
class WritingWorkbench : public QObject
{
    Q_OBJECT
public:
    WritingWorkbench(MarkdownEditor *editor,
                     ProseAwarenessWidget *prose,
                     StoryIntelligenceController *story,
                     AgentEditTransactionManager *transactions,
                     QObject *parent = nullptr);
    QWidget *analyticsPage() const
    {
        return m_analyticsPage;
    }
    QWidget *dialoguePage() const
    {
        return m_dialoguePage;
    }
    void refresh();

protected:
    bool eventFilter(QObject *object, QEvent *event) override;

private:
    friend class ::VisualShellTest;
    QComboBox *createScope(QWidget *page);
    void populateScopes();
    QPair<int, int> range(QComboBox *combo) const;
    QString scopeId(QComboBox *combo) const;
    void render();
    void renderAnalytics();
    void renderDialogue();
    void navigate(const QJsonObject &record);
    bool current() const;
    bool saveReview(const QJsonObject &review);
    void editDialogue();
    void resizeDialogueRows();
    void finishDialogueEdit(bool save);
    void retainSpeakerAssignments(const QJsonArray &changes);
    void bulkEditDialogue();
    void assignSpeaker();
    void editAliases();
    void exportReport();
    void saveSnapshot();
    void setStatus(const QString &message);
    void setActionsEnabled(bool enabled);
    MarkdownEditor *m_editor;
    ProseAwarenessWidget *m_prose;
    StoryIntelligenceController *m_story;
    AgentEditTransactionManager *m_transactions;
    QWidget *m_analyticsPage;
    QWidget *m_dialoguePage;
    QComboBox *m_analyticsScope;
    QComboBox *m_dialogueScope;
    QComboBox *m_reportKind;
    QComboBox *m_speaker;
    QLineEdit *m_search;
    QTreeWidget *m_reports;
    QTreeWidget *m_lines;
    QLabel *m_summary;
    QLabel *m_voiceSummary;
    QPointer<QPlainTextEdit> m_inlineEditor;
    QJsonObject m_editLine;
    QLabel *m_analyticsStatus;
    QLabel *m_dialogueStatus;
    QPushButton *m_saveCharacter;
    QWidget *m_rhythmChart;
    WritingAnalysis m_analysis;
    QJsonObject m_workspace;
    QString m_documentPath;
    int m_revision = -1;
    bool m_analysisPending = false;
    QTimer m_timer;
    QFutureWatcher<WritingAnalysis> m_worker;
};
}
