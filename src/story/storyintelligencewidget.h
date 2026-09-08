/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef STORY_INTELLIGENCE_WIDGET_H
#define STORY_INTELLIGENCE_WIDGET_H

#include <QIcon>
#include <QJsonArray>
#include <QJsonObject>
#include <QString>
#include <QWidget>

class QFrame;
class QComboBox;
class QLabel;
class QPlainTextEdit;
class QPushButton;
class QScrollArea;
class QToolButton;
class QVBoxLayout;

namespace ghostwriter
{
class StoryIntelligenceWidget : public QWidget
{
    Q_OBJECT

public:
    explicit StoryIntelligenceWidget(QWidget *parent = nullptr);

    void setCollapseIcon(const QIcon &icon);
    void setProviderSummary(const QString &provider, const QString &model, bool credentialConfigured, const QString &authKind = QString());
    void setProjectFolder(const QString &path);
    void setProjectUnderstanding(const QJsonObject &understanding);
    void setBranches(const QJsonArray &branches, const QString &activeBranch);
    void setContextInspector(const QJsonObject &inspector);
    void setSceneContext(const QJsonObject &context);
    void setCharacters(const QJsonArray &characters);
    void setActiveCharacter(const QString &characterId);
    void setAnnotations(const QJsonArray &annotations);
    void appendChatMessage(const QString &role,
                           const QString &text,
                           const QString &speaker = QString(),
                           const QJsonArray &references = {},
                           const QString &messageId = QString());
    void appendProposal(const QString &kind, const QJsonObject &proposal);
    void
    setWorkspaceContext(const QString &mode, const QString &title, const QString &agentName, const QString &sessionTitle, const QString &scopeKind = QString());
    void setSessions(const QJsonArray &sessions, const QString &activeSessionId);
    void showChatError(const QString &message);
    void appendActivityCard(const QString &title, const QString &detail, const QString &operationId = QString());
    void clearChat();
    void setBusy(bool busy);
    void setStatusMessage(const QString &message);
    QString activeCharacterId() const;

signals:
    void collapseRequested();
    void workspaceRequested();
    void newSessionRequested();
    void deleteSessionRequested(const QString &sessionId);
    void sessionSelected(const QString &sessionId);
    void messageActionRequested(const QString &messageId, const QString &action);
    void scopeModeChanged(const QString &mode);
    void rememberRequested(const QString &text);
    void proposalReviewRequested(const QString &kind, const QJsonObject &proposal);
    void modelSettingsRequested();
    void projectFolderRequested();
    void projectUnderstandingReviewRequested();
    void manuscriptOrderRequested();
    void storyLabRequested();
    void routingSettingsRequested();
    void branchChanged(const QString &branchId);
    void createBranchRequested();
    void branchDetailsRequested();
    void epistemicModeChanged(const QString &mode);
    void editSceneRequested();
    void addCharacterRequested();
    void editCharactersRequested();
    void characterActivated(const QString &characterId);
    void chatRequested(const QString &message);
    void clearAnnotationsRequested();
    void annotationNavigationRequested(int startUtf16, int endUtf16, const QString &quote);
    void applySuggestionRequested(const QString &annotationId);
    void dismissSuggestionRequested(const QString &annotationId);
    void undoAgentTransactionRequested(const QString &operationId);

protected:
    bool eventFilter(QObject *watched, QEvent *event) override;

private:
    QFrame *makeCard(const QString &objectName);
    QLabel *makeSectionTitle(const QString &text);
    void rebuildCharacters();
    void rebuildAnnotations();
    void scrollChatToBottom();
    void submitChat();
    QString characterId(const QJsonObject &character) const;

    QToolButton *m_collapseButton;
    QComboBox *m_scopeCombo = nullptr;
    QComboBox *m_sessionCombo = nullptr;
    QLabel *m_scopeLabel = nullptr;
    QLabel *m_contextLabel = nullptr;
    QToolButton *m_workspaceButton = nullptr;
    QToolButton *m_newSessionButton = nullptr;
    QToolButton *m_deleteSessionButton = nullptr;
    QPushButton *m_modelSettingsButton;
    QPushButton *m_routingSettingsButton = nullptr;
    QLabel *m_providerLabel;
    QLabel *m_modelLabel;
    QLabel *m_keyStateLabel;
    QLabel *m_projectPathLabel;
    QLabel *m_projectUnderstandingLabel = nullptr;
    QLabel *m_projectRolesLabel = nullptr;
    QLabel *m_contextInspectorLabel = nullptr;
    QLabel *m_contextSourcesLabel = nullptr;
    QComboBox *m_epistemicCombo = nullptr;
    QPushButton *m_projectReviewButton = nullptr;
    QPushButton *m_manuscriptOrderButton = nullptr;
    QPushButton *m_storyLabButton = nullptr;
    QComboBox *m_branchCombo = nullptr;
    QPushButton *m_branchCreateButton = nullptr;
    QPushButton *m_branchDetailsButton = nullptr;
    QLabel *m_settingLabel;
    QLabel *m_goalLabel;
    QLabel *m_contextDetailLabel;
    QWidget *m_charactersContainer;
    QVBoxLayout *m_charactersLayout;
    QWidget *m_annotationsSection;
    QWidget *m_annotationsContainer;
    QVBoxLayout *m_annotationsLayout;
    QWidget *m_chatContainer;
    QVBoxLayout *m_chatLayout;
    QScrollArea *m_chatScrollArea;
    QPlainTextEdit *m_chatInput;
    QPushButton *m_sendButton;
    QLabel *m_statusLabel;
    QJsonArray m_characters;
    QJsonArray m_sessions;
    QJsonArray m_annotations;
    QString m_activeCharacterId;
    bool m_busy = false;
};
}

#endif
