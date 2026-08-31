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
    QString projectRoot() const;
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
    bool saveProjectMetadata(QString *errorMessage = nullptr) const;
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
    PendingChat m_pendingChat;
    QString m_chatRequestId;
    QTimer m_toolWaitTimer;
    int m_revision = 0;
};
}

#endif
