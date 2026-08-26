/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QJsonDocument>
#include <QJsonObject>
#include <QTest>
#include <QtConcurrentRun>

#include "../../src/prose/proseinstrumentation.h"

using namespace ghostwriter;

namespace
{
QJsonObject summaryObject(ProseInstrumentation *instruments)
{
    return QJsonDocument::fromJson(instruments->toJson().toUtf8()).object();
}
}

class ProseInstrumentationTest : public QObject
{
    Q_OBJECT

private slots:
    void countersIncrementThroughPublicApi();
    void guiWorkSamplesComputePercentile();
    void concurrentRecordingStaysConsistent();
    void dumpWritesOnlyWhenEnvironmentRequested();
};

void ProseInstrumentationTest::countersIncrementThroughPublicApi()
{
    ProseInstrumentation *instruments = ProseInstrumentation::instance();
    instruments->reset();

    instruments->recordClearChannel(QStringLiteral("prose"));
    instruments->recordClearChannel(QStringLiteral("prose"));
    instruments->recordClearChannel(QStringLiteral("spelling"));
    instruments->recordPatchFrame(120);
    instruments->recordPatchFrame(80);
    instruments->recordResyncEvent(2048);
    instruments->beginHydrationCycle();
    instruments->recordHydrationTick();
    instruments->recordHydrationTick();
    instruments->recordSpansApplied(5);
    instruments->recordBlocksPreserved(7);
    instruments->recordBlocksPreserved(0);
    instruments->recordPreviewExport(12);
    instruments->recordGuiThreadWork(3);

    const QJsonObject summary = summaryObject(instruments);
    const QJsonObject clearChannels = summary.value(QStringLiteral("clear_channels")).toObject();
    QCOMPARE(clearChannels.value(QStringLiteral("prose")).toInt(), 2);
    QCOMPARE(clearChannels.value(QStringLiteral("spelling")).toInt(), 1);
    const QJsonObject patches = summary.value(QStringLiteral("patches")).toObject();
    QCOMPARE(patches.value(QStringLiteral("frames")).toInt(), 2);
    QCOMPARE(patches.value(QStringLiteral("bytes")).toInt(), 200);
    const QJsonObject resyncs = summary.value(QStringLiteral("resyncs")).toObject();
    QCOMPARE(resyncs.value(QStringLiteral("events")).toInt(), 1);
    QCOMPARE(resyncs.value(QStringLiteral("bytes")).toInt(), 2048);
    const QJsonObject hydration = summary.value(QStringLiteral("hydration")).toObject();
    QCOMPARE(hydration.value(QStringLiteral("ticks")).toInt(), 2);
    QCOMPARE(hydration.value(QStringLiteral("spans_applied")).toInt(), 5);
    QCOMPARE(hydration.value(QStringLiteral("blocks_preserved")).toInt(), 7);
    QVERIFY(hydration.value(QStringLiteral("first_visible_span_ms")).toDouble() >= 0.0);
    const QJsonObject preview = summary.value(QStringLiteral("preview_exports")).toObject();
    QCOMPARE(preview.value(QStringLiteral("exports")).toInt(), 1);
    QCOMPARE(preview.value(QStringLiteral("total_ms")).toInt(), 12);
    QCOMPARE(summary.value(QStringLiteral("gui_thread_work_ms")).toObject().value(QStringLiteral("count")).toInt(), 1);

    instruments->reset();
    const QJsonObject cleared = summaryObject(instruments);
    QCOMPARE(cleared.value(QStringLiteral("patches")).toObject().value(QStringLiteral("frames")).toInt(), 0);
    QVERIFY(cleared.value(QStringLiteral("clear_channels")).toObject().isEmpty());
}

void ProseInstrumentationTest::guiWorkSamplesComputePercentile()
{
    ProseInstrumentation *instruments = ProseInstrumentation::instance();
    instruments->reset();

    for (int ms = 1; ms <= 100; ++ms) {
        instruments->recordGuiThreadWork(ms);
    }

    const QJsonObject gui = summaryObject(instruments).value(QStringLiteral("gui_thread_work_ms")).toObject();
    QCOMPARE(gui.value(QStringLiteral("count")).toInt(), 100);
    QCOMPARE(gui.value(QStringLiteral("total_ms")).toInt(), 5050);
    QCOMPARE(gui.value(QStringLiteral("max_ms")).toInt(), 100);
    QCOMPARE(gui.value(QStringLiteral("p95_ms")).toInt(), 95);
}

void ProseInstrumentationTest::concurrentRecordingStaysConsistent()
{
    ProseInstrumentation *instruments = ProseInstrumentation::instance();
    instruments->reset();

    QtConcurrent::run([instruments]() {
        for (int index = 0; index < 1000; ++index) {
            instruments->recordPatchFrame(1);
        }
    }).waitForFinished();
    for (int index = 0; index < 1000; ++index) {
        instruments->recordPatchFrame(1);
    }

    const QJsonObject patches = summaryObject(instruments).value(QStringLiteral("patches")).toObject();
    QCOMPARE(patches.value(QStringLiteral("frames")).toInt(), 2000);
    QCOMPARE(patches.value(QStringLiteral("bytes")).toInt(), 2000);
}

void ProseInstrumentationTest::dumpWritesOnlyWhenEnvironmentRequested()
{
    ProseInstrumentation *instruments = ProseInstrumentation::instance();
    instruments->reset();

    qputenv("THOTHPAD_DUMP_INSTRUMENTS", "1");
    QVERIFY(instruments->dumpIfRequested());
    QVERIFY(!instruments->toJson().isEmpty());

    qunsetenv("THOTHPAD_DUMP_INSTRUMENTS");
    QVERIFY(!instruments->dumpIfRequested());
}

QTEST_MAIN(ProseInstrumentationTest)
#include "prosinstrumentationtest.moc"
