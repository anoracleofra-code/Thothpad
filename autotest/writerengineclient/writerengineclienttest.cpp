/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QMetaObject>
#include <QProcess>
#include <QSignalSpy>
#include <QTest>

#include "../../src/prose/performancepolicy.h"
#include "../../src/prose/writerengineclient.h"

using namespace ghostwriter;

class WriterEngineClientTest : public QObject
{
    Q_OBJECT

private slots:
    void stderrIsNotReportedAsAProtocolFailure();
    void processExitInvalidatesOutstandingRequestState();
    void automaticPerformancePolicyIsBoundedAndCpuOnly();
    void capabilitiesAreEmptyBeforeNegotiation();
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

QTEST_MAIN(WriterEngineClientTest)
#include "writerengineclienttest.moc"
