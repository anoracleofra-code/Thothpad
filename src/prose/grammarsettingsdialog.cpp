/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#include "grammarsettingsdialog.h"
#include "../messageboxhelper.h"
#include "credentialstore.h"
#include <QCheckBox>
#include <QComboBox>
#include <QCryptographicHash>
#include <QDialogButtonBox>
#include <QFormLayout>
#include <QJsonArray>
#include <QLineEdit>
#include <QMessageBox>
#include <QSettings>
#include <QSpinBox>
#include <QUrl>
#include <QVBoxLayout>
namespace ghostwriter
{
namespace
{
QString pwaLanguage(const QString &language)
{
    if (language == QStringLiteral("en-GB"))
        return QStringLiteral("en_UK");
    if (language == QStringLiteral("en-AU"))
        return QStringLiteral("en_AU");
    if (language == QStringLiteral("en-CA"))
        return QStringLiteral("en_CA");
    return QStringLiteral("en_US");
}
}
GrammarSettingsDialog::GrammarSettingsDialog(CredentialStore *credentials, QWidget *parent)
    : QDialog(parent)
    , m_credentials(credentials)
    , m_provider(new QComboBox(this))
    , m_language(new QComboBox(this))
    , m_style(new QComboBox(this))
    , m_endpoint(new QLineEdit(this))
    , m_username(new QLineEdit(this))
    , m_apiKey(new QLineEdit(this))
    , m_includeSpelling(new QCheckBox(tr("Include spelling suggestions"), this))
    , m_timeout(new QSpinBox(this))
{
    setWindowTitle(tr("Grammar Settings"));
    m_provider->addItem(tr("Harper (offline)"), QStringLiteral("harper"));
    m_provider->addItem(tr("LanguageTool"), QStringLiteral("languagetool"));
    m_provider->addItem(tr("ProWritingAid"), QStringLiteral("prowritingaid"));
    m_language->addItem(tr("English (United States)"), QStringLiteral("en-US"));
    m_language->addItem(tr("English (United Kingdom)"), QStringLiteral("en-GB"));
    m_language->addItem(tr("English (Canada)"), QStringLiteral("en-CA"));
    m_language->addItem(tr("English (Australia)"), QStringLiteral("en-AU"));
    m_language->addItem(tr("English (India)"), QStringLiteral("en-IN"));
    for (const QString &style : {QStringLiteral("Creative"),
                                 QStringLiteral("General"),
                                 QStringLiteral("Academic"),
                                 QStringLiteral("Business"),
                                 QStringLiteral("Technical"),
                                 QStringLiteral("Casual"),
                                 QStringLiteral("Web"),
                                 QStringLiteral("Script"),
                                 QStringLiteral("Legal")}) {
        m_style->addItem(style, style);
    }
    m_apiKey->setEchoMode(QLineEdit::Password);
    m_apiKey->setPlaceholderText(tr("Stored in the operating system credential store"));
    m_timeout->setRange(5, 120);
    m_timeout->setSuffix(tr(" seconds"));
    auto *form = new QFormLayout;
    form->addRow(tr("Review provider"), m_provider);
    form->addRow(tr("Language"), m_language);
    form->addRow(tr("Endpoint"), m_endpoint);
    form->addRow(tr("Account"), m_username);
    form->addRow(tr("API key"), m_apiKey);
    form->addRow(tr("Writing style"), m_style);
    form->addRow(QString(), m_includeSpelling);
    form->addRow(tr("Timeout"), m_timeout);
    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Save | QDialogButtonBox::Cancel, this);
    connect(buttons, &QDialogButtonBox::accepted, this, &GrammarSettingsDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);
    connect(m_provider, &QComboBox::currentIndexChanged, this, &GrammarSettingsDialog::updateProviderUi);
    connect(m_credentials, &CredentialStore::written, this, [this](const QString &credentialId) {
        if (credentialId == m_pendingCredentialId) {
            m_pendingCredentialId.clear();
            m_apiKey->clear();
            saveSettings();
            QDialog::accept();
        }
    });
    connect(m_credentials, &CredentialStore::error, this, [this](const QString &credentialId, const QString &message) {
        if (credentialId == m_pendingCredentialId) {
            m_pendingCredentialId.clear();
            setEnabled(true);
            MessageBoxHelper::warning(this, tr("Credential not saved"), message);
        }
    });
    auto *layout = new QVBoxLayout(this);
    layout->addLayout(form);
    layout->addWidget(buttons);
    loadSettings();
}
void GrammarSettingsDialog::loadSettings()
{
    const QJsonObject settings = configuredSettings();
    m_provider->setCurrentIndex(qMax(0, m_provider->findData(settings.value(QStringLiteral("provider")).toString())));
    QString language = settings.value(QStringLiteral("language")).toString();
    language.replace(QLatin1Char('_'), QLatin1Char('-'));
    if (language == QStringLiteral("en-UK"))
        language = QStringLiteral("en-GB");
    m_language->setCurrentIndex(qMax(0, m_language->findData(language)));
    m_endpoint->setText(settings.value(QStringLiteral("url")).toString());
    m_username->setText(settings.value(QStringLiteral("username")).toString());
    m_style->setCurrentIndex(qMax(0, m_style->findData(settings.value(QStringLiteral("style")).toString())));
    m_includeSpelling->setChecked(settings.value(QStringLiteral("include_spelling")).toBool(false));
    m_timeout->setValue(settings.value(QStringLiteral("timeout")).toInt(20));
    updateProviderUi();
}
void GrammarSettingsDialog::updateProviderUi()
{
    const QString provider = m_provider->currentData().toString();
    const bool languageTool = provider == QStringLiteral("languagetool");
    const bool pwa = provider == QStringLiteral("prowritingaid");
    m_endpoint->setVisible(languageTool);
    m_username->setVisible(languageTool);
    m_apiKey->setVisible(languageTool || pwa);
    m_style->setVisible(pwa);
    m_includeSpelling->setVisible(provider == QStringLiteral("harper"));
    if (languageTool && m_endpoint->text().isEmpty()) {
        m_endpoint->setText(QStringLiteral("http://127.0.0.1:8081/v2/check"));
    }
}
bool GrammarSettingsDialog::endpointIsAllowed() const
{
    const QUrl url(m_endpoint->text().trimmed());
    if (!url.isValid() || url.host().isEmpty() || !url.userInfo().isEmpty() || !url.fragment().isEmpty()) {
        return false;
    }
    if (url.scheme() == QStringLiteral("https"))
        return true;
    const QString host = url.host().toLower();
    return url.scheme() == QStringLiteral("http")
        && (host == QStringLiteral("localhost") || host == QStringLiteral("127.0.0.1") || host == QStringLiteral("::1"));
}
void GrammarSettingsDialog::accept()
{
    if (!m_pendingCredentialId.isEmpty())
        return;
    const QString provider = m_provider->currentData().toString();
    if (provider == QStringLiteral("languagetool") && !endpointIsAllowed()) {
        MessageBoxHelper::warning(this,
                                  tr("Endpoint not allowed"),
                                  tr("Remote LanguageTool endpoints must use HTTPS. HTTP is permitted only for a local server on this computer."));
        return;
    }
    if (!m_apiKey->text().isEmpty()) {
        if (!m_credentials->isAvailable()) {
            MessageBoxHelper::warning(this,
                                      tr("Secure storage unavailable"),
                                      tr("This build cannot store an API key securely. Harper and a local LanguageTool server remain available."));
            return;
        }
        QJsonObject settings;
        settings.insert(QStringLiteral("provider"), provider);
        settings.insert(QStringLiteral("url"), m_endpoint->text().trimmed());
        m_pendingCredentialId = credentialId(settings);
        setEnabled(false);
        m_credentials->write(m_pendingCredentialId, m_apiKey->text());
        return;
    }
    saveSettings();
    QDialog::accept();
}
void GrammarSettingsDialog::saveSettings()
{
    QSettings settings;
    settings.beginGroup(QStringLiteral("prose/grammar"));
    settings.setValue(QStringLiteral("provider"), m_provider->currentData().toString());
    settings.setValue(QStringLiteral("language"), m_language->currentData().toString());
    settings.setValue(QStringLiteral("endpoint"), m_endpoint->text().trimmed());
    settings.setValue(QStringLiteral("username"), m_username->text().trimmed());
    settings.setValue(QStringLiteral("style"), m_style->currentData().toString());
    settings.setValue(QStringLiteral("include_spelling"), m_includeSpelling->isChecked());
    settings.setValue(QStringLiteral("timeout"), m_timeout->value());
    settings.endGroup();
}
QJsonObject GrammarSettingsDialog::configuredSettings()
{
    QSettings settings;
    settings.beginGroup(QStringLiteral("prose/grammar"));
    const QString provider = settings.value(QStringLiteral("provider"), QStringLiteral("harper")).toString();
    const QString language = settings.value(QStringLiteral("language"), QStringLiteral("en-US")).toString();
    QJsonObject result;
    result.insert(QStringLiteral("provider"), provider);
    result.insert(QStringLiteral("timeout"), settings.value(QStringLiteral("timeout"), 20).toInt());
    result.insert(QStringLiteral("max_findings"), 100000);
    if (provider == QStringLiteral("harper")) {
        result.insert(QStringLiteral("dialect"), language);
        result.insert(QStringLiteral("include_spelling"), settings.value(QStringLiteral("include_spelling"), false).toBool());
    } else if (provider == QStringLiteral("languagetool")) {
        result.insert(QStringLiteral("url"), settings.value(QStringLiteral("endpoint"), QStringLiteral("http://127.0.0.1:8081/v2/check")).toString());
        result.insert(QStringLiteral("language"), language);
        result.insert(QStringLiteral("username"), settings.value(QStringLiteral("username")).toString());
    } else {
        result.insert(QStringLiteral("url"), QStringLiteral("https://api.prowritingaid.com"));
        result.insert(QStringLiteral("language"), pwaLanguage(language));
        result.insert(QStringLiteral("style"), settings.value(QStringLiteral("style"), QStringLiteral("Creative")).toString());
        result.insert(QStringLiteral("reports"), QJsonArray{QStringLiteral("grammar"), QStringLiteral("style"), QStringLiteral("spelling")});
    }
    settings.endGroup();
    return result;
}
QString GrammarSettingsDialog::credentialId(const QJsonObject &settings)
{
    const QString provider = settings.value(QStringLiteral("provider")).toString();
    if (provider == QStringLiteral("prowritingaid")) {
        return QStringLiteral("grammar/prowritingaid/api.prowritingaid.com");
    }
    const QUrl url(settings.value(QStringLiteral("url")).toString());
    const QString origin =
        QStringLiteral("%1://%2:%3").arg(url.scheme().toLower(), url.host().toLower()).arg(url.port(url.scheme() == QStringLiteral("https") ? 443 : 80));
    const QString endpointId = QString::fromLatin1(QCryptographicHash::hash(origin.toUtf8(), QCryptographicHash::Sha256).toHex().left(16));
    return QStringLiteral("grammar/%1/%2").arg(provider, endpointId);
}
}
