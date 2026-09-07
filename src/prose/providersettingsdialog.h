/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef PROVIDER_SETTINGS_DIALOG_H
#define PROVIDER_SETTINGS_DIALOG_H

#include <QDialog>
#include <QJsonObject>
#include <QMap>
#include <QStringList>

class QComboBox;
class QDoubleSpinBox;
class QLineEdit;
class QLabel;
class QPushButton;
class QNetworkAccessManager;
class QNetworkReply;
class QSpinBox;
class QTimer;
class ProviderSettingsDialogTest;

namespace ghostwriter
{
class CredentialStore;
class WriterEngineClient;

class ProviderSettingsDialog : public QDialog
{
    Q_OBJECT

public:
    explicit ProviderSettingsDialog(CredentialStore *credentials, QWidget *parent = nullptr, QNetworkAccessManager *network = nullptr);
    ~ProviderSettingsDialog() override;

    QJsonObject nonSecretSettings() const;
    QString credentialId() const;

protected:
    void accept() override;
    void showEvent(QShowEvent *event) override;

private:
    friend class ::ProviderSettingsDialogTest;
    void loadSettings();
    void applyProviderSettings(const QJsonObject &settings);
    QString savedCredentialId() const;
    void saveNonSecretSettings();
    bool endpointIsAllowed() const;
    void updateModels();
    void cancelModelUpdate();
    void updateModelProvider();
    void updateKeyHint();
    void signIn();
    void disconnectProvider();
    void accessRequest(const QJsonObject &payload);
    void accessResponse(const QString &requestId, const QJsonObject &response);
    void cancelAccess();
    void fetchProviderModels(const QString &secret);
    QString catalogKey() const;

    CredentialStore *m_credentials;
    QComboBox *m_provider;
    QLineEdit *m_endpoint;
    QComboBox *m_model;
    QPushButton *m_updateModels;
    QLabel *m_modelStatus;
    QLabel *m_keyHint;
    QNetworkAccessManager *m_network;
    QNetworkReply *m_modelsReply = nullptr;
    QStringList m_openRouterModels;
    bool m_liveOpenRouterCatalog = false;
    QTimer *m_catalogRefresh;
    QString m_activeProvider;
    QMap<QString, QJsonObject> m_providerDrafts;
    QLineEdit *m_apiKey;
    QDoubleSpinBox *m_temperature;
    QSpinBox *m_maxTokens;
    QSpinBox *m_passes;
    QSpinBox *m_timeout;
    QString m_pendingCredentialId;
    QPushButton *m_signIn;
    QPushButton *m_disconnect;
    QLabel *m_authStatus;
    QTimer *m_authPoll;
    WriterEngineClient *m_accessEngine = nullptr;
    QString m_accessRequestId;
    QString m_accessAction;
    QString m_authSession;
    QString m_catalogCredentialId;
    QString m_removingCredentialId;
    QJsonObject m_queuedAccess;
};
}

#endif
