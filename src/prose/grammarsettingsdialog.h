/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef GRAMMAR_SETTINGS_DIALOG_H
#define GRAMMAR_SETTINGS_DIALOG_H

#include <QDialog>
#include <QJsonObject>

class QCheckBox;
class QComboBox;
class QLineEdit;
class QSpinBox;

namespace ghostwriter
{
class CredentialStore;

class GrammarSettingsDialog : public QDialog
{
    Q_OBJECT

public:
    explicit GrammarSettingsDialog(CredentialStore *credentials, QWidget *parent = nullptr);

    static QJsonObject configuredSettings();
    static QString credentialId(const QJsonObject &settings);

protected:
    void accept() override;

private:
    void loadSettings();
    void saveSettings();
    void updateProviderUi();
    bool endpointIsAllowed() const;

    CredentialStore *m_credentials;
    QComboBox *m_provider;
    QComboBox *m_language;
    QComboBox *m_style;
    QLineEdit *m_endpoint;
    QLineEdit *m_username;
    QLineEdit *m_apiKey;
    QCheckBox *m_includeSpelling;
    QSpinBox *m_timeout;
    QString m_pendingCredentialId;
};
}

#endif
