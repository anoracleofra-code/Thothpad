/* SPDX-License-Identifier: GPL-3.0-or-later */
#include <QComboBox>
#include <QCompleter>
#include <QDialogButtonBox>
#include <QDoubleSpinBox>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QLineEdit>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QJsonObject>
#include <QJsonDocument>
#include <QPushButton>
#include <QSettings>
#include <QSpinBox>
#include <QTemporaryDir>
#include <QTest>
#include <cstring>

#include "../../src/prose/credentialstore.h"
#include "../../src/prose/providersettingsdialog.h"

using namespace ghostwriter;

class CatalogReply : public QNetworkReply
{
public:
    explicit CatalogReply(const QNetworkRequest &request, QObject *parent) : QNetworkReply(parent)
    {
        setRequest(request);
        setUrl(request.url());
        open(QIODevice::ReadOnly);
    }
    void complete(QByteArray body, int status = 200)
    {
        m_body = body;
        setAttribute(QNetworkRequest::HttpStatusCodeAttribute, status);
        if (status != 200) setError(QNetworkReply::ContentAccessDenied, QStringLiteral("Test failure"));
        emit readyRead();
        if (!isFinished()) {
            setFinished(true);
            emit finished();
        }
    }
    void abort() override
    {
        aborted = true;
        setError(QNetworkReply::OperationCanceledError, QStringLiteral("Cancelled"));
        setFinished(true);
        emit finished();
    }
    qint64 bytesAvailable() const override { return m_body.size() - m_offset + QIODevice::bytesAvailable(); }
    bool aborted = false;
protected:
    qint64 readData(char *data, qint64 maximum) override
    {
        const auto length = qMin(maximum, qint64(m_body.size() - m_offset));
        if (length == 0) return -1;
        std::memcpy(data, m_body.constData() + m_offset, size_t(length));
        m_offset += length;
        return length;
    }
private:
    QByteArray m_body;
    qint64 m_offset = 0;
};

class CatalogNetwork : public QNetworkAccessManager
{
public:
    CatalogReply *reply = nullptr;
    int requests = 0;
    Operation operation;
protected:
    QNetworkReply *createRequest(Operation op, const QNetworkRequest &request, QIODevice *outgoing) override
    {
        Q_ASSERT(!outgoing);
        operation = op;
        ++requests;
        reply = new CatalogReply(request, this);
        return reply;
    }
};

class ProviderSettingsDialogTest : public QObject
{
    Q_OBJECT
private slots:
    void initTestCase()
    {
        QVERIFY(m_settings.isValid());
        QCoreApplication::setOrganizationName(QStringLiteral("ThothPadTests"));
        QCoreApplication::setApplicationName(QStringLiteral("ModelCatalog"));
        QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, m_settings.path());
    }
    void init()
    {
        QSettings settings;
        settings.clear();
        settings.setValue(QStringLiteral("prose/provider/provider"), QStringLiteral("openrouter"));
        settings.setValue(QStringLiteral("prose/provider/model"), QStringLiteral("custom/keep-me"));
    }
    void manualRefreshIsPublicAndPreservesSelection()
    {
        CatalogNetwork network;
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials, nullptr, &network);
        auto *model = dialog.findChild<QComboBox *>(QStringLiteral("modelSelector"));
        auto *update = dialog.findChild<QPushButton *>(QStringLiteral("updateModelsButton"));
        QVERIFY(model && update);
        QVERIFY(model->isEditable());
        QCOMPARE(model->completer()->filterMode(), Qt::MatchContains);
        QCOMPARE(network.requests, 0);
        dialog.findChild<QLineEdit *>(QStringLiteral("providerApiKey"))->setText(QStringLiteral("test-secret-not-for-catalog"));
        update->click();
        QVERIFY(!update->isEnabled());
        update->click();
        QCOMPARE(network.requests, 1);
        QCOMPARE(network.operation, QNetworkAccessManager::GetOperation);
        QCOMPARE(network.reply->url(), QUrl(QStringLiteral("https://openrouter.ai/api/v1/models")));
        QVERIFY(!network.reply->request().hasRawHeader("Authorization"));
        QCOMPARE(network.reply->request().rawHeader("Cache-Control"), QByteArray("no-cache"));
        QCOMPARE(network.reply->request().attribute(QNetworkRequest::CacheLoadControlAttribute).toInt(), int(QNetworkRequest::AlwaysNetwork));
        QCOMPARE(network.reply->request().attribute(QNetworkRequest::RedirectPolicyAttribute).toInt(), int(QNetworkRequest::ManualRedirectPolicy));
        // A user may keep typing while the asynchronous request is running.
        model->setCurrentText(QStringLiteral("custom/typed-during-update"));
        network.reply->complete(R"({"data":[{"id":"old/model","created":1},{"id":"new/model","created":99},{"id":"old/model","created":1},{"name":"missing id"}]})");
        QVERIFY(update->isEnabled());
        QCOMPARE(model->count(), 2);
        QCOMPARE(model->itemText(0), QStringLiteral("new/model"));
        QCOMPARE(model->currentText(), QStringLiteral("custom/typed-during-update"));
        QCOMPARE(dialog.nonSecretSettings().value(QStringLiteral("model")).toString(), model->currentText());
        QCOMPARE(QSettings().value(QStringLiteral("prose/provider/model")).toString(), QStringLiteral("custom/keep-me"));
        ProviderSettingsDialog reopened(&credentials, nullptr, &network);
        QCOMPARE(reopened.findChild<QComboBox *>(QStringLiteral("modelSelector"))->count(), 2);
        QCOMPARE(network.requests, 1);
        // A later catalog replaces outdated entries, while keeping custom input.
        update->click();
        network.reply->complete(R"({"data":[{"id":"latest/model","created":100}]})");
        QCOMPARE(model->count(), 1);
        QCOMPARE(model->itemText(0), QStringLiteral("latest/model"));
        QCOMPARE(model->currentText(), QStringLiteral("custom/typed-during-update"));
    }
    void visibleOpenRouterAlwaysRefreshesAndCancelsQueuedRequests()
    {
        QSettings().setValue(QStringLiteral("prose/openrouterModels"), QStringList{QStringLiteral("old/model:free")});
        CatalogNetwork network;
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials, nullptr, &network);
        dialog.show();
        QTRY_COMPARE(network.requests, 1);
        network.reply->complete(R"({"data":[{"id":"new/model:free","created":200}]})");
        QCOMPARE(dialog.m_model->itemText(0), QStringLiteral("new/model:free"));
        QVERIFY(dialog.m_keyHint->text().contains(QStringLiteral("not in the current catalog")));
        dialog.m_model->setCurrentIndex(0);
        QVERIFY(!dialog.m_keyHint->text().contains(QStringLiteral("not in the current catalog")));
        QCoreApplication::processEvents();
        QCOMPARE(network.requests, 1);
        dialog.reject();
        dialog.show();
        QTRY_COMPARE(network.requests, 2);
        auto *oldReply = network.reply;
        dialog.m_provider->setCurrentIndex(dialog.m_provider->findData(QStringLiteral("gemini_oauth")));
        QVERIFY(oldReply->aborted);
        QVERIFY(dialog.m_model->currentText().isEmpty());
        QCoreApplication::processEvents();
        QCOMPARE(network.requests, 2);
        dialog.m_provider->setCurrentIndex(dialog.m_provider->findData(QStringLiteral("openrouter")));
        emit dialog.m_provider->activated(dialog.m_provider->currentIndex());
        QTRY_COMPARE(network.requests, 3);
        network.reply->complete("unavailable", 503);
        QVERIFY(dialog.m_modelStatus->text().contains(QStringLiteral("not verified current")));
        QCOMPARE(dialog.m_model->itemText(0), QStringLiteral("new/model:free"));
        dialog.reject();
        dialog.show();
        dialog.reject();
        QCoreApplication::processEvents();
        QCOMPARE(network.requests, 3);
    }
    void providerSettingsAndDraftsAreIsolated()
    {
        QSettings settings;
        settings.setValue(QStringLiteral("prose/provider/temperature"), QStringLiteral("1.25"));
        settings.setValue(QStringLiteral("prose/provider/max_tokens"), 99999);
        settings.setValue(QStringLiteral("prose/provider/passes"), 4);
        settings.setValue(QStringLiteral("prose/provider/timeout"), 360);
        CatalogNetwork network;
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials, nullptr, &network);
        const auto original = dialog.nonSecretSettings();
        QCOMPARE(dialog.m_temperature->value(), 1.25);
        QCOMPARE(dialog.m_maxTokens->value(), 32768);
        QCOMPARE(dialog.m_maxTokens->maximum(), 32768);
        dialog.m_apiKey->setText(QStringLiteral("never-transfer-this"));
        for (int i = 0; i < dialog.m_provider->count(); ++i) {
            if (dialog.m_provider->itemData(i) == QStringLiteral("openrouter")) continue;
            dialog.m_provider->setCurrentIndex(i);
            emit dialog.m_provider->activated(i);
            QVERIFY(dialog.m_model->currentText().isEmpty());
            QCOMPARE(dialog.m_model->count(), 0);
            QVERIFY(dialog.m_apiKey->text().isEmpty());
            QCOMPARE(dialog.m_temperature->value(), 0.7);
            QCOMPARE(dialog.m_maxTokens->value(), 4096);
            QCOMPARE(dialog.m_passes->value(), 1);
            QCOMPARE(dialog.m_timeout->value(), 180);
        }
        dialog.m_provider->setCurrentIndex(dialog.m_provider->findData(QStringLiteral("openrouter")));
        QCOMPARE(dialog.nonSecretSettings(), original);
        QVERIFY(dialog.m_apiKey->text().isEmpty());
        dialog.m_provider->setCurrentIndex(dialog.m_provider->findData(QStringLiteral("lmstudio")));
        dialog.m_endpoint->setText(QStringLiteral("http://localhost:4567/v1"));
        dialog.m_model->setCurrentText(QStringLiteral("local/custom"));
        dialog.m_maxTokens->setValue(8192);
        const auto localDraft = dialog.nonSecretSettings();
        dialog.m_provider->setCurrentIndex(dialog.m_provider->findData(QStringLiteral("openrouter")));
        dialog.m_provider->setCurrentIndex(dialog.m_provider->findData(QStringLiteral("lmstudio")));
        emit dialog.m_provider->activated(dialog.m_provider->currentIndex());
        QCOMPARE(dialog.nonSecretSettings(), localDraft);
        dialog.reject();
        QVERIFY(!settings.contains(QStringLiteral("prose/providerPresets/lmstudio")));
        ProviderSettingsDialog reopened(&credentials, nullptr, &network);
        QCOMPARE(reopened.nonSecretSettings(), original);
        reopened.m_provider->setCurrentIndex(reopened.m_provider->findData(QStringLiteral("lmstudio")));
        QVERIFY(reopened.m_model->currentText().isEmpty());
        QCOMPARE(reopened.m_maxTokens->value(), 4096);
    }
    void savedProvidersRoundTripWithoutMixingCredentialIdentities()
    {
        CatalogNetwork network;
        CredentialStore credentials;
        ProviderSettingsDialog first(&credentials, nullptr, &network);
        const auto router = first.nonSecretSettings();
        const QString routerCredential = first.credentialId();
        QSettings().setValue(QStringLiteral("prose/provider/credential_id"), routerCredential);
        first.m_provider->setCurrentIndex(first.m_provider->findData(QStringLiteral("gemini_oauth")));
        first.m_model->setCurrentText(QStringLiteral("google-test-model"));
        first.m_temperature->setValue(0.3);
        first.m_maxTokens->setValue(8000);
        const auto google = first.nonSecretSettings();
        first.accept();
        QCOMPARE(first.result(), int(QDialog::Accepted));
        QVERIFY(!QSettings().contains(QStringLiteral("prose/provider/credential_id")));
        ProviderSettingsDialog second(&credentials, nullptr, &network);
        QCOMPARE(second.nonSecretSettings(), google);
        second.m_provider->setCurrentIndex(second.m_provider->findData(QStringLiteral("openrouter")));
        QCOMPARE(second.nonSecretSettings(), router);
        QVERIFY(second.m_keyHint->text().contains(QStringLiteral("secure key is configured")));
        second.accept();
        QCOMPARE(QSettings().value(QStringLiteral("prose/provider/credential_id")).toString(), routerCredential);
        ProviderSettingsDialog third(&credentials, nullptr, &network);
        third.m_provider->setCurrentIndex(third.m_provider->findData(QStringLiteral("gemini_oauth")));
        QCOMPARE(third.nonSecretSettings(), google);
        QVERIFY(third.savedCredentialId().isEmpty());
        third.m_provider->setCurrentIndex(third.m_provider->findData(QStringLiteral("openrouter")));
        third.m_model->setCurrentText(QStringLiteral("different/model"));
        third.accept();
        QVERIFY(!QSettings().contains(QStringLiteral("prose/provider/credential_id")));
    }
    void opencodeCatalogsRefreshIndependentlyWithoutKeys()
    {
        CatalogNetwork network;
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials, nullptr, &network);
        dialog.m_provider->setCurrentIndex(dialog.m_provider->findData(QStringLiteral("opencode_zen")));
        dialog.show();
        QTRY_COMPARE(network.requests, 1);
        QCOMPARE(network.reply->url(), QUrl(QStringLiteral("https://opencode.ai/zen/v1/models")));
        QVERIFY(!network.reply->request().hasRawHeader("Authorization"));
        network.reply->complete(R"({"data":[{"id":"paid","created":9},{"id":"latest-free","created":5},{"id":"big-pickle","created":1}]})");
        QCOMPARE(dialog.m_model->itemText(0), QStringLiteral("latest-free"));
        QCOMPARE(dialog.m_model->itemText(1), QStringLiteral("big-pickle"));
        dialog.m_model->setCurrentIndex(0);
        const QString zenCache = dialog.catalogKey();
        dialog.m_provider->setCurrentIndex(dialog.m_provider->findData(QStringLiteral("opencode_go")));
        QVERIFY(dialog.m_model->currentText().isEmpty());
        QCOMPARE(dialog.m_model->count(), 0);
        QTRY_COMPARE(network.requests, 2);
        QCOMPARE(network.reply->url(), QUrl(QStringLiteral("https://opencode.ai/zen/go/v1/models")));
        network.reply->complete(R"({"data":[{"id":"go-model","created":10}]})");
        QVERIFY(dialog.m_keyHint->text().contains(QStringLiteral("subscription")));
        QCOMPARE(dialog.m_model->itemText(0), QStringLiteral("go-model"));
        QVERIFY(zenCache != dialog.catalogKey());
        dialog.m_provider->setCurrentIndex(dialog.m_provider->findData(QStringLiteral("opencode_zen")));
        QCOMPARE(dialog.m_model->currentText(), QStringLiteral("latest-free"));
        QCOMPARE(dialog.m_model->count(), 3);
        dialog.reject();
    }
    void openCodeKeySurvivesModelChangesOnTheSameOfficialEndpoint()
    {
        const QString credential = QStringLiteral("provider/opencode_zen/test/deepseek-v4-flash-free");
        QSettings settings;
        settings.setValue(QStringLiteral("prose/provider/provider"), QStringLiteral("opencode_zen"));
        settings.setValue(QStringLiteral("prose/provider/endpoint"), QStringLiteral("https://opencode.ai/zen/v1"));
        settings.setValue(QStringLiteral("prose/provider/model"), QStringLiteral("deepseek-v4-flash-free"));
        settings.setValue(QStringLiteral("prose/provider/credential_id"), credential);
        CatalogNetwork network;
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials, nullptr, &network);
        dialog.m_model->setCurrentText(QStringLiteral("nemotron-3-ultra-free"));
        dialog.accept();
        QCOMPARE(QSettings().value(QStringLiteral("prose/provider/credential_id")).toString(), credential);
    }
    void freeModelsComeFirstInFreshAndLegacyCatalogs()
    {
        QSettings().setValue(QStringLiteral("prose/openrouterModels"), QStringList{
            QStringLiteral("paid/new"), QStringLiteral("free/new:free"),
            QStringLiteral("paid/old"), QStringLiteral("free/old:free")});
        CatalogNetwork network;
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials, nullptr, &network);
        auto *model = dialog.findChild<QComboBox *>(QStringLiteral("modelSelector"));
        QCOMPARE(model->itemText(0), QStringLiteral("free/new:free"));
        QCOMPARE(model->itemText(1), QStringLiteral("free/old:free"));
        QCOMPARE(model->itemText(2), QStringLiteral("paid/new"));
        QCOMPARE(model->itemText(3), QStringLiteral("paid/old"));
        QCOMPARE(network.requests, 0);
        dialog.findChild<QPushButton *>(QStringLiteral("updateModelsButton"))->click();
        network.reply->complete(R"({"data":[
            {"id":"paid/new","created":1000},
            {"id":"free/old:free","created":1},
            {"id":"free/z:free","created":10},
            {"id":"free/a:free","created":10},
            {"id":"free/a:free","created":10},
            {"id":"paid/:free-preview","created":500}
        ]})");
        const QStringList expected{QStringLiteral("free/a:free"), QStringLiteral("free/z:free"),
            QStringLiteral("free/old:free"), QStringLiteral("paid/new"), QStringLiteral("paid/:free-preview")};
        QCOMPARE(model->count(), expected.size());
        for (int i = 0; i < expected.size(); ++i) QCOMPARE(model->itemText(i), expected.at(i));
        QCOMPARE(model->currentText(), QStringLiteral("custom/keep-me"));
        QCOMPARE(QSettings().value(QStringLiteral("prose/openrouterModels")).toStringList(), expected);
        ProviderSettingsDialog reopened(&credentials, nullptr, &network);
        auto *cached = reopened.findChild<QComboBox *>(QStringLiteral("modelSelector"));
        for (int i = 0; i < expected.size(); ++i) QCOMPARE(cached->itemText(i), expected.at(i));
    }
    void failuresKeepCacheAndModel_data()
    {
        QTest::addColumn<QByteArray>("body");
        QTest::addColumn<int>("status");
        QTest::newRow("invalid-json") << QByteArray("invalid") << 200;
        QTest::newRow("empty") << QByteArray(R"({"data":[]})") << 200;
        QTest::newRow("wrong-shape") << QByteArray(R"({"data":{}})") << 200;
        QTest::newRow("auth-error") << QByteArray("denied") << 401;
        QTest::newRow("server-error") << QByteArray("unavailable") << 503;
        QTest::newRow("too-large") << QByteArray(8 * 1024 * 1024 + 1, 'x') << 200;
    }
    void failuresKeepCacheAndModel()
    {
        QFETCH(QByteArray, body);
        QFETCH(int, status);
        const QStringList cached{QStringLiteral("cached/model")};
        QSettings().setValue(QStringLiteral("prose/openrouterModels"), cached);
        CatalogNetwork network;
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials, nullptr, &network);
        auto *update = dialog.findChild<QPushButton *>(QStringLiteral("updateModelsButton"));
        update->click();
        network.reply->complete(body, status);
        QVERIFY(update->isEnabled());
        QCOMPARE(dialog.findChild<QComboBox *>(QStringLiteral("modelSelector"))->count(), 1);
        QCOMPARE(dialog.nonSecretSettings().value(QStringLiteral("model")).toString(), QStringLiteral("custom/keep-me"));
        QCOMPARE(QSettings().value(QStringLiteral("prose/openrouterModels")).toStringList(), cached);
        QVERIFY(dialog.findChild<QLabel *>(QStringLiteral("modelCatalogStatus"))->text().contains(QStringLiteral("unchanged")));
    }
    void providerSwitchAndCloseCancelRequests()
    {
        CatalogNetwork network;
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials, nullptr, &network);
        auto *provider = dialog.findChild<QComboBox *>(QStringLiteral("modelProvider"));
        auto *update = dialog.findChild<QPushButton *>(QStringLiteral("updateModelsButton"));
        update->click();
        auto *oldReply = network.reply;
        provider->setCurrentIndex(provider->findData(QStringLiteral("ollama")));
        QVERIFY(oldReply->aborted);
        QVERIFY(!update->isHidden());
        oldReply->complete(R"({"data":[{"id":"stale/model"}]})");
        QCOMPARE(dialog.findChild<QComboBox *>(QStringLiteral("modelSelector"))->count(), 0);
        provider->setCurrentIndex(provider->findData(QStringLiteral("openrouter")));
        update->click();
        dialog.reject();
        QVERIFY(network.reply->aborted);
        QVERIFY(!QSettings().contains(QStringLiteral("prose/openrouterModels")));
    }
    void keyHintTracksModelIdentity()
    {
        CatalogNetwork network;
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials, nullptr, &network);
        QSettings().setValue(QStringLiteral("prose/provider/credential_id"), dialog.credentialId());
        auto *model = dialog.findChild<QComboBox *>(QStringLiteral("modelSelector"));
        auto *hint = dialog.findChild<QLabel *>(QStringLiteral("modelKeyHint"));
        model->setCurrentText(QStringLiteral("new/model"));
        QVERIFY(hint->text().contains(QStringLiteral("Enter your OpenRouter API key")));
        model->setCurrentText(QStringLiteral("custom/keep-me"));
        QVERIFY(hint->text().contains(QStringLiteral("secure key is configured")));
    }
    void chosenModelIsSavedWithoutReusingAnotherModelsKey()
    {
        CatalogNetwork network;
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials, nullptr, &network);
        QSettings().setValue(QStringLiteral("prose/provider/credential_id"), dialog.credentialId());
        dialog.findChild<QPushButton *>(QStringLiteral("updateModelsButton"))->click();
        network.reply->complete(R"({"data":[{"id":"new/model","created":100}]})");
        dialog.findChild<QComboBox *>(QStringLiteral("modelSelector"))->setCurrentIndex(0);
        dialog.findChild<QDialogButtonBox *>()->button(QDialogButtonBox::Save)->click();
        QCOMPARE(dialog.result(), int(QDialog::Accepted));
        QCOMPARE(QSettings().value(QStringLiteral("prose/provider/model")).toString(), QStringLiteral("new/model"));
        QVERIFY(!QSettings().contains(QStringLiteral("prose/provider/credential_id")));
        QVERIFY(!dialog.nonSecretSettings().contains(QStringLiteral("api_key")));
    }
    void publicCatalogLiveSmoke()
    {
        if (!qEnvironmentVariableIsSet("THOTHPAD_TEST_LIVE_CATALOG")) {
            QSKIP("Opt-in live OpenRouter check; regular tests use deterministic replies.");
        }
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials);
        auto *update = dialog.findChild<QPushButton *>(QStringLiteral("updateModelsButton"));
        dialog.show();
        QTRY_VERIFY(!update->isEnabled());
        QTRY_VERIFY_WITH_TIMEOUT(update->isEnabled(), 35000);
        const QString status = dialog.findChild<QLabel *>(QStringLiteral("modelCatalogStatus"))->text();
        QVERIFY2(status.startsWith(QStringLiteral("Updated:")), qPrintable(status));
        QVERIFY(dialog.findChild<QComboBox *>(QStringLiteral("modelSelector"))->count() > 0);
        bool paidSeen = false;
        for (int i = 0; i < dialog.m_model->count(); ++i) {
            const QString id = dialog.m_model->itemText(i);
            if (id.endsWith(QStringLiteral(":free"))) QVERIFY(!paidSeen);
            else paidSeen = true;
            if (i < 5) qInfo().noquote() << "Catalog entry" << i + 1 << id;
        }
        QCOMPARE(dialog.nonSecretSettings().value(QStringLiteral("model")).toString(), QStringLiteral("custom/keep-me"));
        qInfo().noquote() << status;
        const QString imagePath = qEnvironmentVariable("THOTHPAD_TEST_CATALOG_IMAGE");
        if (!imagePath.isEmpty()) {
            dialog.show();
            QTest::qWait(100);
            QVERIFY(dialog.grab().save(imagePath));
        }
    }
    void oauthChoicesAreExplicitAndProviderSwitchClearsSecrets()
    {
        CatalogNetwork network;
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials, nullptr, &network);
        auto *provider = dialog.findChild<QComboBox *>(QStringLiteral("modelProvider"));
        auto *endpoint = dialog.findChild<QLineEdit *>(QStringLiteral("modelEndpoint"));
        auto *key = dialog.findChild<QLineEdit *>(QStringLiteral("providerApiKey"));
        auto *signin = dialog.findChild<QPushButton *>(QStringLiteral("providerSignIn"));
        auto choose = [provider](const QString &kind) {
            const int index = provider->findData(kind);
            QVERIFY(index >= 0);
            provider->setCurrentIndex(index);
            emit provider->activated(index);
        };
        key->setText(QStringLiteral("do-not-carry-between-providers"));
        choose(QStringLiteral("codex"));
        QVERIFY(key->text().isEmpty());
        QVERIFY(!key->isEnabled());
        QVERIFY(!signin->isHidden());
        QVERIFY(endpoint->isReadOnly());
        QCOMPARE(endpoint->text(), QStringLiteral("https://chatgpt.com"));
        choose(QStringLiteral("gemini_oauth"));
        QVERIFY(key->isReadOnly());
        QVERIFY(!signin->isHidden());
        QCOMPARE(endpoint->text(), QStringLiteral("https://generativelanguage.googleapis.com/v1beta"));
        QVERIFY(!dialog.nonSecretSettings().contains(QStringLiteral("api_key")));
        choose(QStringLiteral("anthropic"));
        QVERIFY(signin->isHidden());
        QVERIFY(!key->isReadOnly());
        QVERIFY(!endpoint->isReadOnly());
        QCOMPARE(network.requests, 0);
        const QString imagePath = qEnvironmentVariable("THOTHPAD_TEST_AUTH_IMAGE");
        if (!imagePath.isEmpty()) {
            choose(QStringLiteral("codex"));
            dialog.show();
            QTest::qWait(100);
            QVERIFY(dialog.grab().save(imagePath));
        }
    }
    void otherProviderCatalogCachesAreEndpointScopedAndIgnoreStaleReplies()
    {
        CatalogNetwork network;
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials, nullptr, &network);
        auto *provider = dialog.findChild<QComboBox *>(QStringLiteral("modelProvider"));
        auto *model = dialog.findChild<QComboBox *>(QStringLiteral("modelSelector"));
        auto *endpoint = dialog.findChild<QLineEdit *>(QStringLiteral("modelEndpoint"));
        provider->setCurrentIndex(provider->findData(QStringLiteral("lmstudio")));
        endpoint->setText(QStringLiteral("http://127.0.0.1:1234/v1"));
        dialog.m_accessAction = QStringLiteral("models");
        dialog.m_accessRequestId = QStringLiteral("fresh");
        dialog.accessResponse(QStringLiteral("fresh"), QJsonObject{
            {QStringLiteral("ok"), true},
            {QStringLiteral("result"), QJsonObject{{QStringLiteral("models"), QJsonArray{QStringLiteral("new/model")}}}}
        });
        QCOMPARE(model->count(), 1);
        QVERIFY(model->currentText().isEmpty());
        dialog.m_accessRequestId = QStringLiteral("stale");
        endpoint->setText(QStringLiteral("http://127.0.0.1:1234/other/v1"));
        QCOMPARE(model->count(), 0);
        dialog.accessResponse(QStringLiteral("stale"), QJsonObject{
            {QStringLiteral("ok"), true},
            {QStringLiteral("result"), QJsonObject{{QStringLiteral("models"), QJsonArray{QStringLiteral("stale/model")}}}}
        });
        QCOMPARE(model->count(), 0);
        endpoint->setText(QStringLiteral("http://127.0.0.1:1234/v1"));
        QCOMPARE(model->itemText(0), QStringLiteral("new/model"));
    }
    void oauthResultIsNotSavedUntilSecureStoreConfirms()
    {
        CatalogNetwork network;
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials, nullptr, &network);
        dialog.m_accessAction = QStringLiteral("poll");
        dialog.m_accessRequestId = QStringLiteral("oauth-result");
        dialog.accessResponse(QStringLiteral("oauth-result"), QJsonObject{
            {QStringLiteral("ok"), true},
            {QStringLiteral("result"), QJsonObject{{QStringLiteral("pending"), false}, {QStringLiteral("credential"), QStringLiteral("test-oauth-secret")}}}
        });
        QCOMPARE(dialog.findChild<QLineEdit *>(QStringLiteral("providerApiKey"))->text(), QStringLiteral("test-oauth-secret"));
        QVERIFY(!QSettings().contains(QStringLiteral("prose/provider/credential_id")));
        QVERIFY(!QJsonDocument(dialog.nonSecretSettings()).toJson().contains("test-oauth-secret"));
        dialog.m_pendingCredentialId = dialog.credentialId();
        emit credentials.written(dialog.m_pendingCredentialId);
        QCOMPARE(dialog.result(), int(QDialog::Accepted));
        QCOMPARE(QSettings().value(QStringLiteral("prose/provider/credential_id")).toString(), dialog.credentialId());
        for (const auto &key : QSettings().allKeys()) {
            QVERIFY(!QSettings().value(key).toString().contains(QStringLiteral("test-oauth-secret")));
        }
    }
    void providerAccessEngineSmoke()
    {
        if (!qEnvironmentVariableIsSet("THOTHPAD_TEST_PROVIDER_ACCESS")) {
            QSKIP("Opt-in native-to-engine provider access test. No credentials or remote request needed.");
        }
        qputenv("THOTHPAD_DATA_DIR", m_settings.path().toUtf8());
        CredentialStore credentials;
        ProviderSettingsDialog dialog(&credentials);
        auto *provider = dialog.findChild<QComboBox *>(QStringLiteral("modelProvider"));
        const int index = provider->findData(QStringLiteral("openai"));
        provider->setCurrentIndex(index);
        emit provider->activated(index);
        auto *update = dialog.findChild<QPushButton *>(QStringLiteral("updateModelsButton"));
        update->click();
        QTRY_VERIFY_WITH_TIMEOUT(update->isEnabled(), 60000);
        const QString status = dialog.findChild<QLabel *>(QStringLiteral("modelCatalogStatus"))->text();
        QVERIFY2(status.contains(QStringLiteral("API key")), qPrintable(status));
        QVERIFY(dialog.nonSecretSettings().value(QStringLiteral("model")).toString().isEmpty());
    }
private:
    QTemporaryDir m_settings;
};

QTEST_MAIN(ProviderSettingsDialogTest)
#include "providersettingsdialogtest.moc"
