/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QJsonDocument>
#include <QMetaObject>
#include <QProcess>
#include <QSignalSpy>
#include <QTest>

#include "../../src/prose/performancepolicy.h"
#include "../../src/prose/writerengineclient.h"

using namespace ghostwriter;

namespace
{
QByteArray responseFrame(const QString &requestId, const QJsonObject &result = {})
{
    QJsonObject response;
    response.insert(QStringLiteral("request_id"), requestId);
    response.insert(QStringLiteral("ok"), true);
    response.insert(QStringLiteral("result"), result);
    const QByteArray body = QJsonDocument(response).toJson(QJsonDocument::Compact);
    return QByteArrayLiteral("Content-Length: ") + QByteArray::number(body.size()) + QByteArrayLiteral("\r\n\r\n") + body;
}
}

class WriterEngineClientTest : public QObject
{
    Q_OBJECT

private slots:
    void stderrIsNotReportedAsAProtocolFailure();
    void processExitInvalidatesOutstandingRequestState();
    void automaticPerformancePolicyIsBoundedAndCpuOnly();
    void capabilitiesAreEmptyBeforeNegotiation();
    void parsesConsecutiveFramesWithoutLosingResponses();
};

void WriterEngineClientTest::stderrIsNotReportedAsAProtocolFailure()
{
    WriterEngineClient client;
    QSignalSpy errorSpy(&client, &WriterEngineClient::engineError);

    QVERIFY(QMetaObject::invokeMethod(&client, "readStandardError", Qt::DirectConnection));
    QCOMPARE(errorSpy.count(), 0);
}

void WriterEngineClientTest::processExitInvalidatesOutstandingRequestState()
{
    WriterEngineClient client;
    QSignalSpy invalidatedSpy(&client, &WriterEngineClient::requestsInvalidated);

    QVERIFY(QMetaObject::invokeMethod(&client, "processFinished", Qt::DirectConnection, Q_ARG(int, 1), Q_ARG(QProcess::ExitStatus, QProcess::CrashExit)));
    QCOMPARE(invalidatedSpy.count(), 1);
}

void WriterEngineClientTest::automaticPerformancePolicyIsBoundedAndCpuOnly()
{
    const PerformancePolicy policy = PerformancePolicy::detected();
    QVERIFY(policy.logicalProcessors >= 1);
    QVERIFY(policy.backgroundThreads >= 1);
    QVERIFY(policy.backgroundThreads <= qMax(1, policy.logicalProcessors));
    QCOMPARE(policy.overlayBudgetMs, 4);

    const QJsonObject serialized = policy.toJson();
    QCOMPARE(serialized.value(QStringLiteral("core_gpu_acceleration")).toBool(), false);
    QCOMPARE(serialized.value(QStringLiteral("background_threads")).toInt(), policy.backgroundThreads);
}

void WriterEngineClientTest::capabilitiesAreEmptyBeforeNegotiation()
{
    WriterEngineClient client;
    QCOMPARE(client.negotiatedProtocolMinor(), 0);
    QVERIFY(!client.supportsOperation(QStringLiteral("open_document")));
}

void WriterEngineClientTest::parsesConsecutiveFramesWithoutLosingResponses()
{
    WriterEngineClient client;
    QSignalSpy responseSpy(&client, &WriterEngineClient::responseReceived);
    QSignalSpy errorSpy(&client, &WriterEngineClient::engineError);

    client.m_pendingOperations.insert(QStringLiteral("first"), QStringLiteral("list_profiles"));
    client.m_pendingOperations.insert(QStringLiteral("second"), QStringLiteral("open_document"));
    client.m_pendingOperations.insert(QStringLiteral("third"), QStringLiteral("analyze_document"));
    client.m_buffer = responseFrame(QStringLiteral("first"))
        + responseFrame(QStringLiteral("second"))
        + responseFrame(QStringLiteral("third"));

    client.parseMessages();

    QTRY_COMPARE_WITH_TIMEOUT(responseSpy.count(), 3, 2000);
    QCOMPARE(errorSpy.count(), 0);
    QCOMPARE(responseSpy.at(0).at(0).toString(), QStringLiteral("first"));
    QCOMPARE(responseSpy.at(1).at(0).toString(), QStringLiteral("second"));
    QCOMPARE(responseSpy.at(2).at(0).toString(), QStringLiteral("third"));
    QCOMPARE(client.m_bufferOffset, client.m_buffer.size());
}

QTEST_MAIN(WriterEngineClientTest)
#include "writerengineclienttest.moc"
