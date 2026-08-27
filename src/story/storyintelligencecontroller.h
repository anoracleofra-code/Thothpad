/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef STORY_INTELLIGENCE_CONTROLLER_H
#define STORY_INTELLIGENCE_CONTROLLER_H

#include <QJsonArray>
#include <QJsonObject>
#include <QObject>
#include <QString>

namespace ghostwriter
{
class CredentialStore;
class MarkdownEditor;
class StoryIntelligenceWidget;
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

private:
    struct PendingChat {
        QString prompt;
        QJsonObject provider;
        QString credentialId;
        int revision = -1;
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
    void applyAnnotations(const QJsonArray &annotations, int responseRevision);
    QJsonObject defaultMetadata() const;
    void appendHistory(const QString &role, const QString &content, const QString &speaker = QString());
    QJsonArray boundedHistory() const;
    QString currentDocumentPath() const;

    MarkdownEditor *m_editor;
    StoryIntelligenceWidget *m_widget;
    WriterEngineClient *m_engine;
    CredentialStore *m_credentials;
    QString m_projectRoot;
    QJsonObject m_metadata;
    QJsonArray m_history;
    QJsonArray m_annotations;
    PendingChat m_pendingChat;
    QString m_chatRequestId;
    int m_revision = 0;
};
}

#endif
