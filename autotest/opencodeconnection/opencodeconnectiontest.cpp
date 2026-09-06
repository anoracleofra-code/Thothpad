/* SPDX-License-Identifier: GPL-3.0-or-later */
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QElapsedTimer>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QProcess>
#include <QProcessEnvironment>
#include <QStandardPaths>
#include <QTest>
#include <qt6keychain/keychain.h>

class OpenCodeConnectionTest : public QObject
{
    Q_OBJECT
private slots:
    QString readConfiguredCredential()
    {
        const QString credentialId = qEnvironmentVariable("THOTHPAD_OPENCODE_CREDENTIAL_ID");
        if (credentialId.isEmpty()) {
            QTest::qFail("Pass the credential identity, never an API key.", __FILE__, __LINE__);
            return {};
        }
        auto *job = new QKeychain::ReadPasswordJob(QStringLiteral("ThothPad"), this);
        job->setKey(credentialId);
        QString secret, readError;
        connect(job, &QKeychain::Job::finished, this, [&] {
            if (job->error()) readError = job->errorString();
            else secret = job->textData();
        });
        job->start();
        QElapsedTimer wait;
        wait.start();
        while (secret.isNull() && readError.isNull() && wait.elapsed() < 10000) {
            QCoreApplication::processEvents(QEventLoop::AllEvents, 50);
        }
        if (!readError.isEmpty()) {
            QTest::qFail(qPrintable(readError), __FILE__, __LINE__);
            return {};
        }
        if (secret.isEmpty()) {
            QTest::qFail("The OpenCode credential is empty or could not be loaded.", __FILE__, __LINE__);
        }
        return secret;
    }

    void configuredCredentialReachesZen()
    {
        if (!qEnvironmentVariableIsSet("THOTHPAD_OPENCODE_CONNECTION_CHECK"))
            QSKIP("Opt-in live OpenCode connection check.");
        const QString secret = readConfiguredCredential();
        QVERIFY(!secret.isEmpty());

        QNetworkAccessManager network;
        const QStringList models{"deepseek-v4-flash-free", "mimo-v2.5-free", "ling-3.0-flash-fin-free",
                                 "nemotron-3-ultra-free", "muse-spark-1.3-contributor-free", "big-pickle"};
        QStringList failures;
        for (const auto &model : models) {
            QNetworkRequest request(QUrl(QStringLiteral("https://opencode.ai/zen/v1/chat/completions")));
            request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
            request.setRawHeader("Authorization", QByteArray("Bearer ") + secret.toUtf8());
            const QJsonObject payload{{"model", model},
                                      {"messages", QJsonArray{QJsonObject{{"role", "user"}, {"content", "Reply only OK."}}}},
                                      {"temperature", 0}, {"max_tokens", 128}};
            auto *reply = network.post(request, QJsonDocument(payload).toJson(QJsonDocument::Compact));
            QTRY_VERIFY_WITH_TIMEOUT(reply->isFinished(), 30000);
            const int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
            const QString body = QString::fromUtf8(reply->readAll()).left(1000);
            qInfo().noquote() << "OpenCode live check" << model << "HTTP" << status << body;
            const auto response = QJsonDocument::fromJson(body.toUtf8()).object();
            const auto choices = response.value("choices").toArray();
            const auto text = choices.isEmpty() ? QString() : choices.first().toObject().value("message").toObject().value("content").toString();
            if (status >= 200 && status < 300 && !text.trimmed().isEmpty()) return;
            failures << model + QStringLiteral(": HTTP %1 %2").arg(status).arg(body);
        }
        QFAIL(qPrintable(failures.join('\n')));
    }

    void configuredCredentialReachesZenThroughPythonSidecar()
    {
        if (!qEnvironmentVariableIsSet("THOTHPAD_OPENCODE_CONNECTION_CHECK"))
            QSKIP("Opt-in live OpenCode sidecar check.");
        const QString secret = readConfiguredCredential();
        QVERIFY(!secret.isEmpty());
        const QString python = QStandardPaths::findExecutable(QStringLiteral("python.exe"));
        QVERIFY2(!python.isEmpty(), "Python was not found.");
        const QString engineRoot = QDir(QCoreApplication::applicationDirPath()).absoluteFilePath(QStringLiteral("../../writer-engine"));
        QVERIFY2(QFileInfo::exists(QDir(engineRoot).filePath(QStringLiteral("backend/llm_clients.py"))),
                 "The development writer engine was not found.");
        QProcess process;
        process.setWorkingDirectory(engineRoot);
        auto environment = QProcessEnvironment::systemEnvironment();
        environment.insert(QStringLiteral("THOTHPAD_TEST_OPENCODE_KEY"), secret);
        process.setProcessEnvironment(environment);
        const QString script = QStringLiteral(
            "from backend.llm_clients import complete_chat; import json, os; "
            "r=complete_chat([{'role':'user','content':'Reply only OK.'}], "
            "{'provider':'opencode_zen','base_url':'https://opencode.ai/zen/v1',"
            "'model':'nemotron-3-ultra-free','api_key':os.environ['THOTHPAD_TEST_OPENCODE_KEY'],"
            "'_desktop_no_environment':True,'temperature':0,'max_tokens':128,'timeout':30}); "
            "print(json.dumps({'text':bool(r.text.strip()),'error':r.error}))");
        process.start(python, {QStringLiteral("-c"), script});
        QVERIFY2(process.waitForFinished(45000), "The Python sidecar connection check timed out.");
        const QJsonObject result = QJsonDocument::fromJson(process.readAllStandardOutput()).object();
        const QString error = result.value(QStringLiteral("error")).toString();
        QVERIFY2(process.exitStatus() == QProcess::NormalExit && process.exitCode() == 0,
                 qPrintable(QString::fromUtf8(process.readAllStandardError()).left(1000)));
        QVERIFY2(error.isEmpty(), qPrintable(error));
        QVERIFY(result.value(QStringLiteral("text")).toBool());
    }
};

QTEST_MAIN(OpenCodeConnectionTest)
#include "opencodeconnectiontest.moc"
