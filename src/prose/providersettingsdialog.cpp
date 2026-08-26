/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#include "providersettingsdialog.h"
#include "../messageboxhelper.h"
#include "credentialstore.h"
#include <QComboBox>
#include <QCryptographicHash>
#include <QDialogButtonBox>
#include <QDoubleSpinBox>
#include <QFormLayout>
#include <QLineEdit>
#include <QMessageBox>
#include <QSettings>
#include <QSpinBox>
#include <QUrl>
#include <QVBoxLayout>
namespace ghostwriter
{
ProviderSettingsDialog::ProviderSettingsDialog(
    CredentialStore *credentials,
    QWidget *parent)
    : QDialog(parent)
    , m_credentials(credentials)
    , m_provider(new QComboBox(this))
    , m_endpoint(new QLineEdit(this))
    , m_model(new QLineEdit(this))
    , m_apiKey(new QLineEdit(this))
    , m_temperature(new QDoubleSpinBox(this))
    , m_maxTokens(new QSpinBox(this))
    , m_passes(new QSpinBox(this))
    , m_timeout(new QSpinBox(this))
{
    setWindowTitle(tr("Model Settings"));
    m_provider->addItem(tr("OpenAI compatible"), QStringLiteral("openai_compatible"));
    m_provider->addItem(tr("OpenAI"), QStringLiteral("openai"));
    m_provider->addItem(tr("OpenRouter"), QStringLiteral("openrouter"));
    m_provider->addItem(tr("Anthropic"), QStringLiteral("anthropic"));
    m_provider->addItem(tr("Ollama"), QStringLiteral("ollama"));
    m_provider->addItem(tr("LM Studio"), QStringLiteral("lmstudio"));
    m_provider->addItem(tr("llama.cpp"), QStringLiteral("llama_cpp"));
    m_apiKey->setEchoMode(QLineEdit::Password);
    m_apiKey->setPlaceholderText(tr("Stored in the operating system credential store"));
    m_temperature->setRange(0.0, 2.0);
    m_temperature->setSingleStep(0.1);
    m_maxTokens->setRange(64, 131072);
    m_passes->setRange(1, 5);
    m_timeout->setRange(5, 600);
    m_timeout->setSuffix(tr(" seconds"));
    auto *form = new QFormLayout;
    form->addRow(tr("Provider"), m_provider);
    form->addRow(tr("Endpoint"), m_endpoint);
    form->addRow(tr("Model"), m_model);
    form->addRow(tr("API key"), m_apiKey);
    form->addRow(tr("Temperature"), m_temperature);
    form->addRow(tr("Maximum tokens"), m_maxTokens);
    form->addRow(tr("Rewrite passes"), m_passes);
    form->addRow(tr("Timeout"), m_timeout);
    auto *buttons = new QDialogButtonBox(
        QDialogButtonBox::Save | QDialogButtonBox::Cancel, this);
    connect(buttons, &QDialogButtonBox::accepted, this, &ProviderSettingsDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);
    auto *layout = new QVBoxLayout(this);
    layout->addLayout(form);
    layout->addWidget(buttons);
    connect(m_credentials, &CredentialStore::written, this,
        [this](const QString &credentialId) {
            if (credentialId == m_pendingCredentialId) {
                m_pendingCredentialId.clear();
                m_apiKey->clear();
                saveNonSecretSettings();
                QDialog::accept();
            }
        });
    connect(m_credentials, &CredentialStore::error, this,
        [this](const QString &credentialId, const QString &message) {
            if (credentialId == m_pendingCredentialId) {
                m_pendingCredentialId.clear();
                setEnabled(true);
                MessageBoxHelper::warning(this, tr("Credential not saved"), message);
            }
        });
    loadSettings();
}
void ProviderSettingsDialog::loadSettings()
{
    QSettings settings;
    settings.beginGroup(QStringLiteral("prose/provider"));
    const QString provider = settings.value(QStringLiteral("provider"), QStringLiteral("openai_compatible")).toString();
    const int providerIndex = m_provider->findData(provider);
    m_provider->setCurrentIndex(providerIndex < 0 ? 0 : providerIndex);
    m_endpoint->setText(settings.value(
        QStringLiteral("endpoint"), QStringLiteral("http://127.0.0.1:1234/v1")).toString());
    m_model->setText(settings.value(QStringLiteral("model"), QStringLiteral("local-model")).toString());
    m_temperature->setValue(settings.value(QStringLiteral("temperature"), 0.7).toDouble());
    m_maxTokens->setValue(settings.value(QStringLiteral("max_tokens"), 4096).toInt());
    m_passes->setValue(settings.value(QStringLiteral("passes"), 1).toInt());
    m_timeout->setValue(settings.value(QStringLiteral("timeout"), 180).toInt());
    settings.endGroup();
}
bool ProviderSettingsDialog::endpointIsAllowed() const
{
    const QUrl url(m_endpoint->text().trimmed());
    if (!url.isValid() || url.host().isEmpty() || !url.userInfo().isEmpty() || !url.fragment().isEmpty()) {
        return false;
    }
    if (url.scheme() == QStringLiteral("https")) {
        return true;
    }
    const QString host = url.host().toLower();
    return url.scheme() == QStringLiteral("http")
        && (host == QStringLiteral("localhost")
            || host == QStringLiteral("127.0.0.1")
            || host == QStringLiteral("::1"));
}
void ProviderSettingsDialog::accept()
{
    if (!m_pendingCredentialId.isEmpty()) {
        return;
    }
    if (!endpointIsAllowed()) {
        MessageBoxHelper::warning(this,
                                  tr("Endpoint not allowed"),
                                  tr("Remote model endpoints must use HTTPS. HTTP is permitted only for a local model on this computer."));
        return;
    }
    if (m_model->text().trimmed().isEmpty()) {
        MessageBoxHelper::warning(this, tr("Model required"), tr("Choose a model before saving."));
        return;
    }
    if (!m_apiKey->text().isEmpty()) {
        if (!m_credentials->isAvailable()) {
            MessageBoxHelper::warning(this,
                                      tr("Secure storage unavailable"),
                                      tr("This build cannot store an API key securely. Local providers that do not require a key remain available."));
            return;
        }
        m_pendingCredentialId = credentialId();
        const QString secret = m_apiKey->text();
        setEnabled(false);
        m_credentials->write(m_pendingCredentialId, secret);
        return;
    }
    saveNonSecretSettings();
    QDialog::accept();
}
void ProviderSettingsDialog::saveNonSecretSettings()
{
    QSettings settings;
    settings.beginGroup(QStringLiteral("prose/provider"));
    settings.setValue(QStringLiteral("provider"), m_provider->currentData().toString());
    settings.setValue(QStringLiteral("endpoint"), m_endpoint->text().trimmed());
    settings.setValue(QStringLiteral("model"), m_model->text().trimmed());
    settings.setValue(QStringLiteral("temperature"), m_temperature->value());
    settings.setValue(QStringLiteral("max_tokens"), m_maxTokens->value());
    settings.setValue(QStringLiteral("passes"), m_passes->value());
    settings.setValue(QStringLiteral("timeout"), m_timeout->value());
    settings.endGroup();
}
QJsonObject ProviderSettingsDialog::nonSecretSettings() const
{
    QJsonObject result;
    result.insert(QStringLiteral("provider"), m_provider->currentData().toString());
    result.insert(QStringLiteral("base_url"), m_endpoint->text().trimmed());
    result.insert(QStringLiteral("model"), m_model->text().trimmed());
    result.insert(QStringLiteral("temperature"), m_temperature->value());
    result.insert(QStringLiteral("max_tokens"), m_maxTokens->value());
    result.insert(QStringLiteral("passes"), m_passes->value());
    result.insert(QStringLiteral("timeout"), m_timeout->value());
    return result;
}
QString ProviderSettingsDialog::credentialId() const
{
    const QUrl url(m_endpoint->text().trimmed());
    const QString origin = QStringLiteral("%1://%2:%3")
        .arg(url.scheme().toLower(), url.host().toLower())
        .arg(url.port(url.scheme().compare(QStringLiteral("https"), Qt::CaseInsensitive) == 0
                ? 443 : 80));
    const QString endpointId = QString::fromLatin1(QCryptographicHash::hash(
        origin.toUtf8(), QCryptographicHash::Sha256).toHex().left(16));
    return QStringLiteral("provider/%1/%2/%3").arg(
        m_provider->currentData().toString(), endpointId, m_model->text().trimmed());
}
}
