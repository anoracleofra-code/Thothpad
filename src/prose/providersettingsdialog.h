/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef PROVIDER_SETTINGS_DIALOG_H
#define PROVIDER_SETTINGS_DIALOG_H

#include <QDialog>
#include <QJsonObject>

class QComboBox;
class QDoubleSpinBox;
class QLineEdit;
class QSpinBox;

namespace ghostwriter
{
class CredentialStore;

class ProviderSettingsDialog : public QDialog
{
    Q_OBJECT

public:
    explicit ProviderSettingsDialog(CredentialStore *credentials, QWidget *parent = nullptr);

    QJsonObject nonSecretSettings() const;
    QString credentialId() const;

protected:
    void accept() override;

private:
    void loadSettings();
    void saveNonSecretSettings();
    bool endpointIsAllowed() const;

    CredentialStore *m_credentials;
    QComboBox *m_provider;
    QLineEdit *m_endpoint;
    QLineEdit *m_model;
    QLineEdit *m_apiKey;
    QDoubleSpinBox *m_temperature;
    QSpinBox *m_maxTokens;
    QSpinBox *m_passes;
    QSpinBox *m_timeout;
    QString m_pendingCredentialId;
};
}

#endif
