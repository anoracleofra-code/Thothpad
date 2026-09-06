/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef STORY_INTELLIGENCE_CONTROLLER_H
#define STORY_INTELLIGENCE_CONTROLLER_H

#include <QJsonArray>
#include <QJsonObject>
#include <QObject>
#include <QString>
#include <QTimer>
#include "storyworkspace.h"

class VisualShellTest;

namespace ghostwriter
{
class AgentEditTransactionManager;
class CredentialStore;
class DocumentActivityTracker;
class MarkdownEditor;
class StoryIntelligenceWidget;
class StoryToolHarness;
class WriterEngineClient;

class StoryIntelligenceController : public QObject
{
    Q_OBJECT

public:
    StoryIntelligenceController(
        MarkdownEditor *editor,
        StoryIntelligenceWidget *widget,
        WriterEngineClient *engine,
        CredentialStore *credentials,
        QObject *parent = nullptr);

    void start();
    ~StoryIntelligenceController() override;
    QString projectRoot() const;
    QJsonObject reviewWorkspace();
    bool saveWritingReview(const QString &workspaceId, const QJsonObject &review);
    bool saveDetectedCharacter(const QString &workspaceId, const QString &name, const QString &detectedId);
    void setToolServices(
        StoryToolHarness *harness,
        AgentEditTransactionManager *transactions,
        DocumentActivityTracker *activity);

signals:
    void projectRootChanged(const QString &root);

private slots:
    void chooseProjectFolder();
    void openModelSettings();
    void editSceneContext();
    void addCharacter();
    void editCharacters();
    void sendChat(const QString &message);
    void handleResponse(const QString &requestId, const QJsonObject &response);
    void handleCredentialLoaded(const QString &credentialId, const QString &secret);
    void handleCredentialError(const QString &credentialId, const QString &message);
    void clearAnnotations();
    void applySuggestion(const QString &suggestionId);
    void dismissSuggestion(const QString &suggestionId);
    void pollPendingTool();

private:
    friend class ::VisualShellTest;
    struct PendingAsyncTool {
        bool active = false;
        QString callId;
        QString toolId;
        QString waitKind;
        QString category;
        QString baselineAnalysisId;
        qint64 targetGeneration = 0;
        int revision = -1;
        int elapsedMs = 0;
    };

    struct PendingChat {
        QString prompt;
        QJsonObject provider;
        QString credentialId;
        QString apiKey;
        QString documentPath;
        QString storyContextHash;
        QString speaker;
        QJsonArray toolResults;
        PendingAsyncTool asyncTool;
        int revision = -1;
        int toolRound = 0;
        bool waitingForCredential = false;
    };

    void refreshProviderSummary();
    QJsonObject providerSettings() const;
    QString providerCredentialId(const QJsonObject &provider) const;
    bool providerMayNeedCredential(const QJsonObject &provider) const;
    void dispatchPendingChat(const QString &apiKey = QString());
    void loadProject(const QString &root);
    void loadProjectMetadata();
    bool saveWorkspace();
    void openWorkspaceForDocument();
    void refreshWorkspace();
    void reconcileWorkspaceHeadings();
    void editWorkspace(int tab = 0);
    void restoreSession();
    void newSession();
    void deleteSession(const QString &sessionId);
    void selectSession(const QString &sessionId);
    bool sessionAvailable(const QJsonObject &session) const;
    void handleMessageAction(const QString &messageId, const QString &action);
    bool reviseChatHistory(int index, const QString &action, const QString &replacement = QString());
    void rememberMessage(const QString &text);
    void reviewProposal(const QString &kind, const QJsonObject &proposal);
    void storeSession();
    QJsonObject activeAgent() const;
    QJsonObject workspaceContext() const;
    QJsonArray allowedManifest() const;
    QJsonObject workspaceTool(const QString &toolId, const QJsonObject &arguments) const;
    void restoreMarkers();
    QString metadataPath() const;
    QJsonObject activeCharacter() const;
    QString currentStoryContextHash() const;
    void applyAnnotations(const QJsonArray &annotations, int responseRevision);
    void refreshAnnotationPresentation();
    QJsonObject defaultMetadata() const;
    void appendHistory(const QString &role, const QString &content, const QString &speaker = QString());
    QJsonArray boundedHistory() const;
    QString currentDocumentPath() const;
    QString modelSafePath(const QString &path) const;
    QJsonObject modelSafeToolResult(const QJsonObject &result) const;
    QString toolRisk(const QString &toolId) const;
    bool authorizeTool(const QString &toolId, const QJsonObject &arguments);
    bool executeToolCalls(const QJsonArray &toolCalls);
    void beginPendingTool(
        const QString &callId,
        const QString &toolId,
        const QJsonObject &nativeResult);
    void finishPendingTool(bool completed, const QString &error = QString());
    bool pendingToolCompleted() const;
    void finishChatTurn(const QJsonObject &story, const QJsonObject &result);
    void failChatTurn(const QString &message);
    void resetPendingChat();

    MarkdownEditor *m_editor;
    StoryIntelligenceWidget *m_widget;
    WriterEngineClient *m_engine;
    CredentialStore *m_credentials;
    StoryToolHarness *m_harness{nullptr};
    AgentEditTransactionManager *m_transactions{nullptr};
    DocumentActivityTracker *m_activity{nullptr};
    QString m_projectRoot;
    QJsonObject m_metadata;
    QJsonArray m_history;
    QJsonArray m_annotations;
    StoryWorkspace m_workspace;
    QString m_workspaceDocument;
    QString m_scopeId = QStringLiteral("manuscript");
    QString m_scopeMode = QStringLiteral("manuscript");
    QString m_sessionId;
    bool m_started = false;
    bool m_loadingWorkspace = false;
    bool m_documentCleared = false;
    QTimer m_workspaceTimer;
    PendingChat m_pendingChat;
    QString m_chatRequestId;
    QTimer m_toolWaitTimer;
    int m_revision = 0;
};
}

#endif
