/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#include "providersettingsdialog.h"
#include "../messageboxhelper.h"
#include "credentialstore.h"
#include "writerengineclient.h"
#include <QCryptographicHash>
#include <QDesktopServices>
#include <QFile>
#include <QFileDialog>
#include <QComboBox>
#include <QCompleter>
#include <QDialogButtonBox>
#include <QDoubleSpinBox>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QPushButton>
#include <QSettings>
#include <QSignalBlocker>
#include <QShowEvent>
#include <QSpinBox>
#include <QTimer>
#include <QUrl>
#include <QVBoxLayout>
#include <algorithm>
namespace ghostwriter
{
namespace
{
bool publicCatalog(const QString &provider)
{
    return provider == QStringLiteral("openrouter") || provider == QStringLiteral("opencode_zen") || provider == QStringLiteral("opencode_go");
}

QString defaultEndpoint(const QString &provider)
{
    if (provider == QStringLiteral("opencode_zen")) return QStringLiteral("https://opencode.ai/zen/v1");
    if (provider == QStringLiteral("opencode_go")) return QStringLiteral("https://opencode.ai/zen/go/v1");
    if (provider == QStringLiteral("codex")) {
        return QStringLiteral("https://chatgpt.com");
    }
    if (provider == QStringLiteral("gemini") || provider == QStringLiteral("gemini_oauth")) {
        return QStringLiteral("https://generativelanguage.googleapis.com/v1beta");
    }
    if (provider == QStringLiteral("openai")) {
        return QStringLiteral("https://api.openai.com/v1");
    }
    if (provider == QStringLiteral("openrouter")) {
        return QStringLiteral("https://openrouter.ai/api/v1");
    }
    if (provider == QStringLiteral("anthropic")) {
        return QStringLiteral("https://api.anthropic.com/v1");
    }
    if (provider == QStringLiteral("ollama")) {
        return QStringLiteral("http://127.0.0.1:11434/api");
    }
    if (provider == QStringLiteral("llama_cpp")) {
        return QStringLiteral("http://127.0.0.1:8080/v1");
    }
    return QStringLiteral("http://127.0.0.1:1234/v1");
}

QJsonObject savedProviderSettings(const QString &provider)
{
    QSettings settings;
    if (settings.value(QStringLiteral("prose/provider/provider"), QStringLiteral("openai_compatible")).toString() != provider) {
        return QJsonObject::fromVariantMap(settings.value(QStringLiteral("prose/providerPresets/") + provider).toMap());
    }
    settings.beginGroup(QStringLiteral("prose/provider"));
    QJsonObject result;
    for (const auto &key : settings.childKeys()) {
        if (key == QStringLiteral("provider") || key == QStringLiteral("endpoint") || key == QStringLiteral("model")
            || key == QStringLiteral("temperature") || key == QStringLiteral("max_tokens")
            || key == QStringLiteral("passes") || key == QStringLiteral("timeout") || key == QStringLiteral("credential_id")) {
            const QVariant value = settings.value(key);
            result.insert(key == QStringLiteral("endpoint") ? QStringLiteral("base_url") : key,
                          key == QStringLiteral("temperature") ? QJsonValue(value.toDouble())
                          : key == QStringLiteral("max_tokens") || key == QStringLiteral("passes") || key == QStringLiteral("timeout")
                          ? QJsonValue(value.toInt()) : QJsonValue(value.toString()));
        }
    }
    return result;
}
}

ProviderSettingsDialog::ProviderSettingsDialog(
    CredentialStore *credentials,
    QWidget *parent,
    QNetworkAccessManager *network)
    : QDialog(parent)
    , m_credentials(credentials)
    , m_provider(new QComboBox(this))
    , m_endpoint(new QLineEdit(this))
    , m_model(new QComboBox(this))
    , m_updateModels(new QPushButton(tr("Update"), this))
    , m_modelStatus(new QLabel(this))
    , m_keyHint(new QLabel(this))
    , m_network(network ? network : new QNetworkAccessManager(this))
    , m_catalogRefresh(new QTimer(this))
    , m_apiKey(new QLineEdit(this))
    , m_temperature(new QDoubleSpinBox(this))
    , m_maxTokens(new QSpinBox(this))
    , m_passes(new QSpinBox(this))
    , m_timeout(new QSpinBox(this))
    , m_signIn(new QPushButton(tr("Sign in…"), this))
    , m_disconnect(new QPushButton(tr("Disconnect"), this))
    , m_authStatus(new QLabel(this))
    , m_authPoll(new QTimer(this))
{
    setWindowTitle(tr("Model Settings"));
    setMinimumWidth(470);
    m_provider->setObjectName(QStringLiteral("modelProvider"));
    m_endpoint->setObjectName(QStringLiteral("modelEndpoint"));
    m_signIn->setObjectName(QStringLiteral("providerSignIn"));
    m_disconnect->setObjectName(QStringLiteral("providerDisconnect"));
    m_authStatus->setObjectName(QStringLiteral("providerAuthStatus"));
    m_signIn->setAutoDefault(false);
    m_disconnect->setAutoDefault(false);
    m_authStatus->setWordWrap(true);
    m_authStatus->setTextFormat(Qt::PlainText);
    m_authPoll->setInterval(750);
    m_catalogRefresh->setSingleShot(true);
    connect(m_catalogRefresh, &QTimer::timeout, this, [this]() {
        if (isVisible() && publicCatalog(m_provider->currentData().toString())) updateModels();
    });
    m_model->setObjectName(QStringLiteral("modelSelector"));
    m_model->setAccessibleName(tr("Model"));
    m_apiKey->setObjectName(QStringLiteral("providerApiKey"));
    m_updateModels->setObjectName(QStringLiteral("updateModelsButton"));
    m_modelStatus->setObjectName(QStringLiteral("modelCatalogStatus"));
    m_keyHint->setObjectName(QStringLiteral("modelKeyHint"));
    m_model->setEditable(true);
    m_model->setInsertPolicy(QComboBox::NoInsert);
    m_model->setSizeAdjustPolicy(QComboBox::AdjustToMinimumContentsLengthWithIcon);
    m_model->setMinimumContentsLength(24);
    m_model->setMaxVisibleItems(15);
    m_model->completer()->setFilterMode(Qt::MatchContains);
    m_model->completer()->setCaseSensitivity(Qt::CaseInsensitive);
    m_model->setToolTip(tr("Choose a model or type its exact model ID. OpenRouter :free models appear first, with newer models first in each group."));
    m_updateModels->setAutoDefault(false);
    m_updateModels->setToolTip(tr("Refresh the public OpenRouter model catalog. No API key or manuscript text is sent."));
    for (QLabel *label : {m_modelStatus, m_keyHint}) {
        label->setWordWrap(true);
        label->setTextFormat(Qt::PlainText);
    }
    m_provider->addItem(tr("Google Gemini"), QStringLiteral("gemini"));
    m_provider->addItem(tr("Google Gemini — OAuth"), QStringLiteral("gemini_oauth"));
    m_provider->addItem(tr("OpenAI compatible"), QStringLiteral("openai_compatible"));
    m_provider->addItem(tr("OpenAI"), QStringLiteral("openai"));
    m_provider->addItem(tr("OpenAI — Codex / ChatGPT sign-in"), QStringLiteral("codex"));
    m_provider->addItem(tr("OpenRouter"), QStringLiteral("openrouter"));
    m_provider->addItem(tr("OpenCode Zen"), QStringLiteral("opencode_zen"));
    m_provider->addItem(tr("OpenCode Go"), QStringLiteral("opencode_go"));
    m_provider->addItem(tr("Anthropic"), QStringLiteral("anthropic"));
    m_provider->addItem(tr("Ollama"), QStringLiteral("ollama"));
    m_provider->addItem(tr("LM Studio"), QStringLiteral("lmstudio"));
    m_provider->addItem(tr("llama.cpp"), QStringLiteral("llama_cpp"));
    m_apiKey->setEchoMode(QLineEdit::Password);
    m_apiKey->setPlaceholderText(tr("Stored in the operating system credential store"));
    m_temperature->setRange(0.0, 2.0);
    m_temperature->setSingleStep(0.1);
    m_maxTokens->setRange(64, 32768);
    m_maxTokens->setToolTip(tr("Maximum generated tokens (not context size). ThothPad supports up to 32,768; individual models may allow less."));
    m_passes->setRange(1, 5);
    m_timeout->setRange(5, 600);
    m_timeout->setSuffix(tr(" seconds"));
    auto *form = new QFormLayout;
    form->addRow(tr("Provider"), m_provider);
    form->addRow(tr("Endpoint"), m_endpoint);
    auto *modelRow = new QHBoxLayout;
    modelRow->addWidget(m_model, 1);
    modelRow->addWidget(m_updateModels);
    form->addRow(tr("Model"), modelRow);
    form->addRow(QString(), m_modelStatus);
    form->addRow(tr("API key"), m_apiKey);
    form->addRow(QString(), m_keyHint);
    auto *authRow = new QHBoxLayout;
    authRow->addWidget(m_signIn);
    authRow->addWidget(m_disconnect);
    authRow->addStretch();
    form->addRow(QString(), authRow);
    form->addRow(QString(), m_authStatus);
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

    connect(m_provider, QOverload<int>::of(&QComboBox::currentIndexChanged), this, &ProviderSettingsDialog::updateModelProvider);
    connect(m_updateModels, &QPushButton::clicked, this, &ProviderSettingsDialog::updateModels);
    connect(m_model, &QComboBox::currentTextChanged, this, &ProviderSettingsDialog::updateKeyHint);
    connect(m_endpoint, &QLineEdit::textChanged, this, &ProviderSettingsDialog::updateKeyHint);
    connect(m_endpoint, &QLineEdit::textChanged, this, &ProviderSettingsDialog::updateModelProvider);
    connect(m_signIn, &QPushButton::clicked, this, &ProviderSettingsDialog::signIn);
    connect(m_disconnect, &QPushButton::clicked, this, &ProviderSettingsDialog::disconnectProvider);
    connect(m_authPoll, &QTimer::timeout, this, [this]() {
        if (m_accessRequestId.isEmpty() && !m_authSession.isEmpty()) {
            accessRequest({{QStringLiteral("action"), QStringLiteral("poll")}, {QStringLiteral("session_id"), m_authSession}});
        }
    });
    connect(this, &QDialog::finished, this, &ProviderSettingsDialog::cancelAccess);
    connect(m_credentials, &CredentialStore::loaded, this, [this](const QString &id, const QString &secret) {
        if (id == m_catalogCredentialId && !id.isEmpty()) {
            m_catalogCredentialId.clear();
            fetchProviderModels(secret);
        }
    });
    connect(m_credentials, &CredentialStore::error, this, [this](const QString &id, const QString &) {
        if (id == m_removingCredentialId && !id.isEmpty()) {
            m_removingCredentialId.clear();
            m_authStatus->setText(tr("Could not remove the saved connection from secure storage. Try again."));
        }
        if (id == m_catalogCredentialId && !id.isEmpty()) {
            m_catalogCredentialId.clear();
            m_updateModels->setEnabled(true);
            m_modelStatus->setText(tr("Cannot load the saved credential. Enter your key or sign in again, then Update."));
        }
    });
    connect(m_credentials, &CredentialStore::removed, this, [this](const QString &id) {
        if (id == m_removingCredentialId && !id.isEmpty()) {
            m_removingCredentialId.clear();
            QSettings settings;
            if (settings.value(QStringLiteral("prose/provider/credential_id")) == id) {
                settings.remove(QStringLiteral("prose/provider/credential_id"));
            }
            const QString presetKey = QStringLiteral("prose/providerPresets/") + m_provider->currentData().toString();
            auto preset = settings.value(presetKey).toMap();
            if (preset.value(QStringLiteral("credential_id")) == id) {
                preset.remove(QStringLiteral("credential_id"));
                settings.setValue(presetKey, preset);
            }
            m_authStatus->setText(tr("Saved connection removed. Revoke access on the provider's website if desired."));
            updateKeyHint();
        }
    });
    connect(this, &QDialog::finished, this, &ProviderSettingsDialog::cancelModelUpdate);

    connect(m_credentials, &CredentialStore::written, this,
        [this](const QString &credentialId) {
            if (credentialId == m_pendingCredentialId) {
                m_apiKey->clear();
                saveNonSecretSettings();
                m_pendingCredentialId.clear();
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
    updateModelProvider();
}
ProviderSettingsDialog::~ProviderSettingsDialog()
{
    cancelAccess();
    cancelModelUpdate();
}

void ProviderSettingsDialog::cancelModelUpdate()
{
    m_catalogRefresh->stop();
    if (m_modelsReply) {
        auto *reply = m_modelsReply;
        m_modelsReply = nullptr;
        reply->disconnect(this);
        reply->abort();
        reply->deleteLater();
    }
    m_updateModels->setEnabled(true);
}

void ProviderSettingsDialog::showEvent(QShowEvent *event)
{
    QDialog::showEvent(event);
    if (publicCatalog(m_provider->currentData().toString())) m_catalogRefresh->start(0);
}

void ProviderSettingsDialog::updateModelProvider()
{
    m_liveOpenRouterCatalog = false;
    cancelAccess();
    cancelModelUpdate();
    const QString kind = m_provider->currentData().toString();
    if (kind != m_activeProvider) {
        if (!m_activeProvider.isEmpty()) {
            auto draft = nonSecretSettings();
            draft.insert(QStringLiteral("provider"), m_activeProvider);
            m_providerDrafts.insert(m_activeProvider, draft);
        }
        m_activeProvider = kind;
        applyProviderSettings(m_providerDrafts.value(kind, savedProviderSettings(kind)));
    }
    const QString selected = m_model->currentText();
    const bool openRouter = kind == QStringLiteral("openrouter");
    const bool oauth = kind == QStringLiteral("gemini_oauth") || kind == QStringLiteral("codex");
    m_endpoint->setReadOnly(oauth || kind.startsWith(QStringLiteral("opencode_")));
    m_apiKey->clear();
    m_apiKey->setReadOnly(oauth);
    m_apiKey->setEnabled(kind != QStringLiteral("codex"));
    m_temperature->setEnabled(kind != QStringLiteral("codex"));
    m_maxTokens->setEnabled(kind != QStringLiteral("codex"));
    m_signIn->setVisible(oauth || openRouter);
    m_disconnect->setVisible(oauth || openRouter);
    m_authStatus->setVisible(oauth || openRouter);
    m_authStatus->setText(kind == QStringLiteral("codex")
        ? tr("Optional official Codex CLI required. Sign in with ChatGPT to use your Codex allowance. This connection is separate from other Codex apps. Codex controls generation limits.")
        : kind == QStringLiteral("gemini_oauth")
        ? tr("Sign in imports a Google Desktop OAuth client JSON. Enable the Generative Language API and configure its consent screen first. No account or client credentials are bundled. Google project quotas/billing apply.")
        : tr("You can enter an API key or sign in with OpenRouter in your browser. Click Save after signing in."));
    m_model->clear();
    if (openRouter) {
        m_model->addItems(m_openRouterModels);
    } else {
        m_model->addItems(QSettings().value(catalogKey()).toStringList());
    }
    m_model->setCurrentText(selected);
    m_updateModels->setVisible(true);
    m_modelStatus->setVisible(true);
    m_updateModels->setToolTip(publicCatalog(kind) ? tr("Refresh the provider's public catalog. No credentials or manuscript text are sent.")
                                        : tr("Request models from the selected provider using its configured credential. No manuscript text is sent. Local providers list available/installed models."));
    m_modelStatus->setText(m_model->count() == 0 ? tr("Click Update to load available models, or enter a model ID.")
                                               : tr("%1 cached models. Click Update for the latest list.").arg(m_model->count()));
    updateKeyHint();
    if (publicCatalog(kind) && isVisible()) m_catalogRefresh->start(0);
}

void ProviderSettingsDialog::updateKeyHint()
{
    const bool configured = savedCredentialId() == credentialId();
    m_apiKey->setPlaceholderText(configured ? tr("Secure key configured · enter a new key to replace it")
                                          : tr("Stored in the operating system credential store"));
    m_keyHint->setVisible(m_provider->currentData() != QStringLiteral("codex"));
    m_keyHint->setText(configured ? tr("No API key is needed to update the list. A secure key is configured for this model.")
                                : tr("No API key is needed to update the list. Enter your OpenRouter API key to use this model. Keys are saved per model."));
    if (m_provider->currentData() != QStringLiteral("openrouter")) {
        m_keyHint->setText(configured ? tr("A secure credential is configured for this model.")
            : m_provider->currentData() == QStringLiteral("gemini_oauth") ? tr("Sign in to connect Google. Credentials are stored securely per model when you Save.")
            : tr("Cloud model lists usually require your API key. Local servers may not. Credentials are stored securely per model."));
        if (m_provider->currentData() == QStringLiteral("opencode_zen")) {
            m_keyHint->setText(tr("Public catalog; free-priced models first. Enter and save your OpenCode key before chatting. Free offers, quotas and data-use policies can change."));
        } else if (m_provider->currentData() == QStringLiteral("opencode_go")) {
            m_keyHint->setText(tr("Public catalog. Chat requires your OpenCode API key and an active Go subscription; included usage is not a free tier. Save after selecting a model."));
        }
    } else if (m_liveOpenRouterCatalog && !m_model->currentText().isEmpty() && !m_openRouterModels.contains(m_model->currentText())) {
        m_keyHint->setText(m_keyHint->text() + tr(" Your selected model is not in the current catalog; choose an available model or verify its ID."));
    }
}

void ProviderSettingsDialog::updateModels()
{
    m_catalogRefresh->stop();
    if (m_modelsReply || !m_accessRequestId.isEmpty() || !m_catalogCredentialId.isEmpty()) {
        return;
    }
    const QString kind = m_provider->currentData().toString();
    if (!publicCatalog(kind)) {
        if (!endpointIsAllowed()) {
            m_modelStatus->setText(tr("Use an HTTPS endpoint, or HTTP on localhost only."));
            return;
        }
        if (!m_apiKey->text().isEmpty() || m_provider->currentData() == QStringLiteral("codex")) {
            fetchProviderModels(m_apiKey->text());
        } else if (savedCredentialId() == credentialId()) {
            m_catalogCredentialId = credentialId();
            m_updateModels->setEnabled(false);
            m_credentials->read(m_catalogCredentialId);
        } else {
            fetchProviderModels(QString());
        }
        return;
    }
    // The public catalog is independent of custom inference endpoints. Never
    // attach stored credentials or user content to this discovery request.
    const QString providerName = m_provider->currentText();
    const bool openRouter = kind == QStringLiteral("openrouter");
    const QString cacheKey = openRouter ? QStringLiteral("prose/openrouterModels") : catalogKey();
    QNetworkRequest request(QUrl(defaultEndpoint(kind) + QStringLiteral("/models")));
    request.setRawHeader("Accept", "application/json");
    request.setRawHeader("Cache-Control", "no-cache");
    request.setRawHeader("Pragma", "no-cache");
    request.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::ManualRedirectPolicy);
    request.setAttribute(QNetworkRequest::CacheLoadControlAttribute, QNetworkRequest::AlwaysNetwork);
    request.setAttribute(QNetworkRequest::CacheSaveControlAttribute, false);
    request.setTransferTimeout(20000);
    m_liveOpenRouterCatalog = false;
    updateKeyHint();
    m_updateModels->setEnabled(false);
    m_modelStatus->setText(tr("Updating %1 models…").arg(providerName));
    auto *reply = m_network->get(request);
    m_modelsReply = reply;
    constexpr qint64 maximumCatalogBytes = 8 * 1024 * 1024;
    connect(reply, &QIODevice::readyRead, this, [reply]() {
        if (reply->bytesAvailable() > maximumCatalogBytes) {
            reply->abort();
        }
    });
    // A total deadline also bounds servers that keep trickling bytes.
    QTimer::singleShot(30000, reply, [reply]() {
        if (!reply->isFinished()) {
            reply->abort();
        }
    });
    connect(reply, &QNetworkReply::finished, this, [this, reply, openRouter, cacheKey, providerName]() {
        m_modelsReply = nullptr;
        m_updateModels->setEnabled(true);
        reply->deleteLater();
        const int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        if (reply->error() != QNetworkReply::NoError || status != 200 || reply->bytesAvailable() > maximumCatalogBytes) {
            m_modelStatus->setText(tr("Live %1 refresh failed. The list is cached, not verified current. Your existing list and model are unchanged. Try Update again.").arg(providerName));
            return;
        }
        const auto document = QJsonDocument::fromJson(reply->readAll());
        const auto data = document.object().value(QStringLiteral("data"));
        QList<QPair<qint64, QString>> models;
        if (data.isArray()) {
            for (const auto &value : data.toArray()) {
                const auto model = value.toObject();
                const QString id = model.value(QStringLiteral("id")).toString().trimmed();
                if (!id.isEmpty() && id.size() <= 256 && !id.contains(QChar::Null)) {
                    models.append({model.value(QStringLiteral("created")).toInteger(), id});
                }
            }
        }
        if (models.isEmpty()) {
            m_modelStatus->setText(tr("%1 returned no usable models. The list is cached, not verified current. Your existing list and model are unchanged. Try Update again later.").arg(providerName));
            return;
        }
        std::sort(models.begin(), models.end(), [openRouter](const auto &left, const auto &right) {
            const auto freeModel = [openRouter](const QString &id) {
                return openRouter ? id.endsWith(QStringLiteral(":free"))
                    : id.endsWith(QStringLiteral("-free")) || id == QStringLiteral("big-pickle");
            };
            const bool leftFree = freeModel(left.second);
            const bool rightFree = freeModel(right.second);
            if (leftFree != rightFree) {
                return leftFree;
            }
            return left.first != right.first ? left.first > right.first : left.second < right.second;
        });
        QStringList ids;
        for (const auto &model : models) {
            ids.append(model.second);
        }
        ids.removeDuplicates();
        const QString selected = m_model->currentText();
        if (openRouter) m_openRouterModels = ids;
        m_model->clear();
        m_model->addItems(ids);
        m_model->setCurrentText(selected);
        QSettings().setValue(cacheKey, ids);
        m_modelStatus->setText(tr("Updated: %1 models from %2's live catalog. %3 Choose a model, then Save.").arg(ids.size()).arg(providerName,
            openRouter ? tr(":free models first; newest first in each group.") : tr("Free-designated models first; then catalog date and ID. Dates may not represent release dates.")));
        m_liveOpenRouterCatalog = openRouter;
        updateKeyHint();
    });
}
void ProviderSettingsDialog::loadSettings()
{
    QSettings settings;
    m_openRouterModels = settings.value(QStringLiteral("prose/openrouterModels")).toStringList();
    // Older caches have no creation timestamps; preserve their relative order.
    std::stable_partition(m_openRouterModels.begin(), m_openRouterModels.end(), [](const QString &id) {
        return id.endsWith(QStringLiteral(":free"));
    });
    const QString provider = settings.value(QStringLiteral("prose/provider/provider"), QStringLiteral("openai_compatible")).toString();
    const int providerIndex = m_provider->findData(provider);
    const QSignalBlocker providerSignals(m_provider);
    m_provider->setCurrentIndex(providerIndex < 0 ? 0 : providerIndex);
    m_activeProvider = m_provider->currentData().toString();
    applyProviderSettings(savedProviderSettings(m_activeProvider));
}

void ProviderSettingsDialog::applyProviderSettings(const QJsonObject &settings)
{
    const QSignalBlocker endpointSignals(m_endpoint);
    const QSignalBlocker modelSignals(m_model);
    m_endpoint->setText(settings.value(QStringLiteral("base_url")).toString(defaultEndpoint(m_activeProvider)));
    m_model->setCurrentText(settings.value(QStringLiteral("model")).toString());
    m_temperature->setValue(settings.value(QStringLiteral("temperature")).toDouble(0.7));
    m_maxTokens->setValue(settings.value(QStringLiteral("max_tokens")).toInt(4096));
    m_passes->setValue(settings.value(QStringLiteral("passes")).toInt(1));
    m_timeout->setValue(settings.value(QStringLiteral("timeout")).toInt(180));
}

QString ProviderSettingsDialog::savedCredentialId() const
{
    return savedProviderSettings(m_provider->currentData().toString()).value(QStringLiteral("credential_id")).toString();
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
    if (!m_authSession.isEmpty() || !m_queuedAccess.isEmpty() || !m_accessRequestId.isEmpty()) {
        m_authStatus->setText(tr("Wait for the current request or cancel sign-in before saving."));
        return;
    }
    if (!m_pendingCredentialId.isEmpty()) {
        return;
    }
    if (!endpointIsAllowed()) {
        MessageBoxHelper::warning(this,
                                  tr("Endpoint not allowed"),
                                  tr("Remote model endpoints must use HTTPS. HTTP is permitted only for a local model on this computer."));
        return;
    }
    if (m_model->currentText().trimmed().isEmpty()) {
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
    // Preserve the previously saved provider when migrating the single-provider
    // settings. Unsaved drafts stay in this dialog and Cancel never persists them.
    const QString previousProvider = settings.value(QStringLiteral("prose/provider/provider"), QStringLiteral("openai_compatible")).toString();
    const auto previousSettings = savedProviderSettings(previousProvider);
    settings.setValue(QStringLiteral("prose/providerPresets/") + previousProvider,
                      previousSettings.toVariantMap());
    const QString previousCredentialId = previousSettings.value(QStringLiteral("credential_id")).toString();
    const QString currentProvider = m_provider->currentData().toString();
    const auto selectedSettings = savedProviderSettings(currentProvider);
    const QString selectedCredentialId = selectedSettings.value(QStringLiteral("credential_id")).toString();
    const auto sameOrigin = [](const QString &left, const QString &right) {
        const QUrl a(left), b(right);
        return a.scheme().compare(b.scheme(), Qt::CaseInsensitive) == 0
            && a.host().compare(b.host(), Qt::CaseInsensitive) == 0
            && a.port(a.scheme().compare(QStringLiteral("https"), Qt::CaseInsensitive) == 0 ? 443 : 80)
                == b.port(b.scheme().compare(QStringLiteral("https"), Qt::CaseInsensitive) == 0 ? 443 : 80);
    };
    const bool openCodeCredential = currentProvider == QStringLiteral("opencode_zen") || currentProvider == QStringLiteral("opencode_go");
    const QString endpoint = m_endpoint->text().trimmed();
    const bool preservePreviousOpenCodeKey = openCodeCredential && previousProvider == currentProvider && !previousCredentialId.isEmpty()
        && sameOrigin(previousSettings.value(QStringLiteral("base_url")).toString(), endpoint);
    const bool preserveSelectedOpenCodeKey = openCodeCredential && !selectedCredentialId.isEmpty()
        && sameOrigin(selectedSettings.value(QStringLiteral("base_url")).toString(), endpoint);
    settings.beginGroup(QStringLiteral("prose/provider"));
    settings.setValue(QStringLiteral("provider"), currentProvider);
    settings.setValue(QStringLiteral("endpoint"), endpoint);
    settings.setValue(QStringLiteral("model"), m_model->currentText().trimmed());
    settings.setValue(QStringLiteral("temperature"), m_temperature->value());
    settings.setValue(QStringLiteral("max_tokens"), m_maxTokens->value());
    settings.setValue(QStringLiteral("passes"), m_passes->value());
    settings.setValue(QStringLiteral("timeout"), m_timeout->value());
    const QString currentCredentialId = credentialId();
    if (!m_pendingCredentialId.isEmpty()) {
        settings.setValue(QStringLiteral("credential_id"), m_pendingCredentialId);
    } else if (previousCredentialId == currentCredentialId || preservePreviousOpenCodeKey) {
        settings.setValue(QStringLiteral("credential_id"), previousCredentialId);
    } else if (selectedCredentialId == currentCredentialId) {
        settings.setValue(QStringLiteral("credential_id"), selectedCredentialId);
    } else if (preserveSelectedOpenCodeKey) {
        settings.setValue(QStringLiteral("credential_id"), selectedCredentialId);
    } else {
        // OpenCode keys are reusable for models on the same official origin.
        // Other provider credentials remain tied to their exact model identity.
        settings.remove(QStringLiteral("credential_id"));
    }
    settings.endGroup();
    settings.setValue(QStringLiteral("prose/providerPresets/") + m_provider->currentData().toString(),
                      savedProviderSettings(m_provider->currentData().toString()).toVariantMap());
}
QJsonObject ProviderSettingsDialog::nonSecretSettings() const
{
    QJsonObject result;
    result.insert(QStringLiteral("provider"), m_provider->currentData().toString());
    result.insert(QStringLiteral("base_url"), m_endpoint->text().trimmed());
    result.insert(QStringLiteral("model"), m_model->currentText().trimmed());
    result.insert(QStringLiteral("temperature"), m_temperature->value());
    result.insert(QStringLiteral("max_tokens"), m_maxTokens->value());
    result.insert(QStringLiteral("passes"), m_passes->value());
    result.insert(QStringLiteral("timeout"), m_timeout->value());
    return result;
}
QString ProviderSettingsDialog::credentialId() const
{
    return CredentialStore::providerCredentialId(m_provider->currentData().toString(), QUrl(m_endpoint->text().trimmed()), m_model->currentText().trimmed());
}

QString ProviderSettingsDialog::catalogKey() const
{
    const QByteArray identity = m_provider->currentData().toString().toUtf8() + '\n' + QUrl(m_endpoint->text().trimmed()).toEncoded();
    return QStringLiteral("prose/modelCatalogs/") + QString::fromLatin1(QCryptographicHash::hash(identity, QCryptographicHash::Sha256).toHex());
}

void ProviderSettingsDialog::cancelAccess()
{
    m_authPoll->stop();
    m_authSession.clear();
    m_accessRequestId.clear();
    m_queuedAccess = {};
    m_catalogCredentialId.clear();
    m_removingCredentialId.clear();
    if (m_accessEngine) {
        m_accessEngine->stop();
    }
    m_signIn->setText(tr("Sign in…"));
    m_updateModels->setEnabled(true);
}

void ProviderSettingsDialog::accessRequest(const QJsonObject &payload)
{
    if (!m_accessEngine) {
        m_accessEngine = new WriterEngineClient(this);
        connect(m_accessEngine, &WriterEngineClient::responseReceived, this, &ProviderSettingsDialog::accessResponse);
        connect(m_accessEngine, &WriterEngineClient::readyChanged, this, [this](bool ready) {
            if (ready && !m_queuedAccess.isEmpty()) {
                const auto queued = m_queuedAccess;
                m_queuedAccess = {};
                if (!m_accessEngine->supportsOperation(QStringLiteral("provider_access"))) {
                    cancelAccess();
                    m_authStatus->setText(tr("Update the ThothPad engine to enable provider sign-in and model discovery."));
                    return;
                }
                accessRequest(queued);
            }
        });
        connect(m_accessEngine, &WriterEngineClient::engineError, this, [this](const QString &) {
            cancelAccess();
            m_authStatus->setText(tr("The provider connection stopped. Check the engine installation and try again."));
            m_modelStatus->setText(tr("Request failed; the selected model and cached list are unchanged."));
        });
    }
    if (!m_accessEngine->isReady()) {
        m_queuedAccess = payload;
        m_accessEngine->start();
        return;
    }
    m_accessAction = payload.value(QStringLiteral("action")).toString();
    m_accessRequestId = m_accessEngine->send(QStringLiteral("provider_access"), payload);
}

void ProviderSettingsDialog::fetchProviderModels(const QString &secret)
{
    m_updateModels->setEnabled(false);
    m_modelStatus->setText(tr("Updating available models…"));
    auto provider = nonSecretSettings();
    if (!secret.isEmpty()) provider.insert(QStringLiteral("api_key"), secret);
    accessRequest({{QStringLiteral("action"), QStringLiteral("models")}, {QStringLiteral("provider"), provider}});
}

void ProviderSettingsDialog::signIn()
{
    if (!m_authSession.isEmpty() || !m_queuedAccess.isEmpty() || !m_accessRequestId.isEmpty()) {
        cancelAccess();
        m_authStatus->setText(tr("Sign-in cancelled. You can close the browser tab."));
        return;
    }
    if (!m_credentials->isAvailable() && m_provider->currentData() != QStringLiteral("codex")) {
        m_authStatus->setText(tr("Secure credential storage is required for browser sign-in."));
        return;
    }
    const QString kind = m_provider->currentData().toString();
    if (kind != QStringLiteral("codex") && m_endpoint->text().trimmed() != defaultEndpoint(kind)) {
        m_authStatus->setText(tr("Browser sign-in requires the provider's official default endpoint."));
        return;
    }
    QJsonObject payload{{QStringLiteral("action"), QStringLiteral("begin")}, {QStringLiteral("kind"), kind}};
    if (kind == QStringLiteral("gemini_oauth")) {
        const QString path = QFileDialog::getOpenFileName(this, tr("Import Google Desktop OAuth client JSON"), QString(), tr("JSON files (*.json)"));
        if (path.isEmpty()) return;
        QFile file(path);
        if (!file.open(QIODevice::ReadOnly) || file.size() > 65536) {
            m_authStatus->setText(tr("Could not read the OAuth client JSON (maximum 64 KiB)."));
            return;
        }
        const auto client = QJsonDocument::fromJson(file.readAll()).object();
        if (!client.value(QStringLiteral("installed")).isObject()) {
            m_authStatus->setText(tr("Choose a Desktop app OAuth client JSON from Google Cloud, not a web client."));
            return;
        }
        payload.insert(QStringLiteral("client"), client);
    }
    m_signIn->setText(tr("Cancel sign-in"));
    m_updateModels->setEnabled(false);
    m_authStatus->setText(tr("Starting secure browser sign-in…"));
    accessRequest(payload);
}

void ProviderSettingsDialog::disconnectProvider()
{
    cancelAccess();
    m_apiKey->clear();
    if (m_provider->currentData() == QStringLiteral("codex")) {
        m_authStatus->setText(tr("Disconnecting ThothPad's Codex session…"));
        accessRequest({{QStringLiteral("action"), QStringLiteral("logout_codex")}});
    } else {
        m_removingCredentialId = credentialId();
        m_authStatus->setText(tr("Removing the saved connection. This does not revoke access on the provider's website."));
        m_credentials->remove(m_removingCredentialId);
    }
}

void ProviderSettingsDialog::accessResponse(const QString &requestId, const QJsonObject &response)
{
    if (requestId != m_accessRequestId || requestId.isEmpty()) return;
    m_accessRequestId.clear();
    const auto result = response.value(QStringLiteral("result")).toObject();
    if (!response.value(QStringLiteral("ok")).toBool() || result.contains(QStringLiteral("error"))) {
        QString message = result.value(QStringLiteral("error")).toString();
        if (message.isEmpty()) message = response.value(QStringLiteral("error")).toObject().value(QStringLiteral("message")).toString();
        const bool models = m_accessAction == QStringLiteral("models");
        cancelAccess();
        if (models) m_modelStatus->setText(message + tr(" Your existing list and model are unchanged."));
        else m_authStatus->setText(message);
        return;
    }
    if (m_accessAction == QStringLiteral("models")) {
        QStringList ids;
        for (const auto &value : result.value(QStringLiteral("models")).toArray()) {
            const QString id = value.toString();
            if (!id.isEmpty() && id.size() <= 256) ids.append(id);
        }
        ids.removeDuplicates();
        if (!ids.isEmpty()) {
            const QString selected = m_model->currentText();
            m_model->clear();
            m_model->addItems(ids);
            m_model->setCurrentText(selected);
            QSettings().setValue(catalogKey(), ids);
            m_modelStatus->setText(tr("Updated: %1 available models. Choose a model, then Save.").arg(ids.size()));
        } else {
            m_modelStatus->setText(tr("No models returned. Your existing list is unchanged."));
        }
        m_updateModels->setEnabled(true);
    } else if (m_accessAction == QStringLiteral("begin")) {
        m_authSession = result.value(QStringLiteral("session_id")).toString();
        const QUrl url(result.value(QStringLiteral("url")).toString());
        const QStringList hosts{QStringLiteral("accounts.google.com"), QStringLiteral("openrouter.ai"), QStringLiteral("auth.openai.com"), QStringLiteral("chatgpt.com")};
        if (m_authSession.isEmpty() || url.scheme() != QStringLiteral("https") || !hosts.contains(url.host()) || !QDesktopServices::openUrl(url)) {
            cancelAccess();
            m_authStatus->setText(tr("Could not open the provider's sign-in page. Try again."));
            return;
        }
        m_authStatus->setText(tr("Complete sign-in in your browser, then return here. No manuscript is sent during sign-in."));
        m_authPoll->start();
    } else if (m_accessAction == QStringLiteral("poll") && !result.value(QStringLiteral("pending")).toBool()) {
        m_authPoll->stop();
        m_authSession.clear();
        m_signIn->setText(tr("Sign in…"));
        m_updateModels->setEnabled(true);
        const QString credential = result.value(QStringLiteral("credential")).toString();
        if (!credential.isEmpty()) m_apiKey->setText(credential);
        m_authStatus->setText(tr("Signed in. Click Update to choose a model, then Save. The browser authorization itself is already complete."));
    } else if (m_accessAction == QStringLiteral("logout_codex")) {
        m_authStatus->setText(tr("ThothPad's Codex session is disconnected. Other Codex apps are unchanged."));
    }
}
}
