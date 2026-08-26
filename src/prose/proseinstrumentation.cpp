/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "proseinstrumentation.h"

#include <algorithm>

#include <QJsonDocument>
#include <QJsonObject>
#include <QTextStream>

namespace ghostwriter
{

ProseInstrumentation *ProseInstrumentation::instance()
{
    static ProseInstrumentation singleton;
    return &singleton;
}

ProseInstrumentation::~ProseInstrumentation()
{
    dumpIfRequested();
}

void ProseInstrumentation::reset()
{
    const QMutexLocker locker(&m_mutex);
    m_clearChannelCounts.clear();
    m_guiWorkCount = 0;
    m_guiWorkTotalMs = 0;
    m_guiWorkMaxMs = 0;
    m_guiWorkSamples.clear();
    m_patchFrames = 0;
    m_patchBytes = 0;
    m_resyncEvents = 0;
    m_resyncBytes = 0;
    m_hydrationTicks = 0;
    m_spansApplied = 0;
    m_blocksPreserved = 0;
    m_hydrationTimer.invalidate();
    m_firstVisibleSpanMs = -1;
    m_firstSpanRecorded = false;
    m_previewExports = 0;
    m_previewExportTotalMs = 0;
}

void ProseInstrumentation::recordClearChannel(const QString &channelName)
{
    const QMutexLocker locker(&m_mutex);
    ++m_clearChannelCounts[channelName];
}

void ProseInstrumentation::recordGuiThreadWork(qint64 milliseconds)
{
    const QMutexLocker locker(&m_mutex);
    ++m_guiWorkCount;
    m_guiWorkTotalMs += milliseconds;
    m_guiWorkMaxMs = qMax(m_guiWorkMaxMs, milliseconds);
    if (m_guiWorkSamples.size() < MaximumGuiWorkSamples) {
        m_guiWorkSamples.insert(std::lower_bound(m_guiWorkSamples.begin(), m_guiWorkSamples.end(), milliseconds), milliseconds);
    }
}

void ProseInstrumentation::recordPatchFrame(int bytes)
{
    const QMutexLocker locker(&m_mutex);
    ++m_patchFrames;
    m_patchBytes += bytes;
}

void ProseInstrumentation::recordResyncEvent(int bytes)
{
    const QMutexLocker locker(&m_mutex);
    ++m_resyncEvents;
    m_resyncBytes += bytes;
}

void ProseInstrumentation::beginHydrationCycle()
{
    const QMutexLocker locker(&m_mutex);
    m_hydrationTimer.start();
    m_firstSpanRecorded = false;
}

void ProseInstrumentation::recordHydrationTick()
{
    const QMutexLocker locker(&m_mutex);
    ++m_hydrationTicks;
}

void ProseInstrumentation::recordSpansApplied(int count)
{
    if (count <= 0) {
        return;
    }
    const QMutexLocker locker(&m_mutex);
    m_spansApplied += quint64(count);
    if (!m_firstSpanRecorded && m_hydrationTimer.isValid()) {
        m_firstVisibleSpanMs = m_hydrationTimer.elapsed();
        m_firstSpanRecorded = true;
    }
}

void ProseInstrumentation::recordBlocksPreserved(int count)
{
    if (count <= 0) {
        return;
    }
    const QMutexLocker locker(&m_mutex);
    m_blocksPreserved += quint64(count);
}

void ProseInstrumentation::recordPreviewExport(qint64 milliseconds)
{
    const QMutexLocker locker(&m_mutex);
    ++m_previewExports;
    m_previewExportTotalMs += milliseconds;
}

bool ProseInstrumentation::dumpIfRequested() const
{
    const QString requested = qEnvironmentVariable("THOTHPAD_DUMP_INSTRUMENTS");
    if (requested.compare(QStringLiteral("1"), Qt::CaseInsensitive) != 0 && requested.compare(QStringLiteral("on"), Qt::CaseInsensitive) != 0
        && requested.compare(QStringLiteral("true"), Qt::CaseInsensitive) != 0) {
        return false;
    }
    QTextStream stream(stderr);
    stream << "THOTHPAD_INSTRUMENTS " << toJson() << Qt::flush;
    return true;
}

QString ProseInstrumentation::toJson() const
{
    QJsonObject guiWork;
    QJsonObject patches;
    QJsonObject resyncs;
    QJsonObject hydration;
    QJsonObject preview;
    QJsonObject clearChannels;
    {
        const QMutexLocker locker(&m_mutex);
        const qsizetype sampleCount = m_guiWorkSamples.size();
        qint64 guiWorkP95Ms = 0;
        if (sampleCount > 0) {
            const qsizetype rank = qMin(sampleCount, (sampleCount * 95 + 99) / 100);
            guiWorkP95Ms = m_guiWorkSamples.at(rank - 1);
        }
        guiWork.insert(QStringLiteral("count"), double(m_guiWorkCount));
        guiWork.insert(QStringLiteral("total_ms"), double(m_guiWorkTotalMs));
        guiWork.insert(QStringLiteral("max_ms"), double(m_guiWorkMaxMs));
        guiWork.insert(QStringLiteral("p95_ms"), double(guiWorkP95Ms));
        patches.insert(QStringLiteral("frames"), double(m_patchFrames));
        patches.insert(QStringLiteral("bytes"), double(m_patchBytes));
        resyncs.insert(QStringLiteral("events"), double(m_resyncEvents));
        resyncs.insert(QStringLiteral("bytes"), double(m_resyncBytes));
        hydration.insert(QStringLiteral("ticks"), double(m_hydrationTicks));
        hydration.insert(QStringLiteral("spans_applied"), double(m_spansApplied));
        hydration.insert(QStringLiteral("blocks_preserved"), double(m_blocksPreserved));
        hydration.insert(QStringLiteral("first_visible_span_ms"), double(m_firstVisibleSpanMs));
        preview.insert(QStringLiteral("exports"), double(m_previewExports));
        preview.insert(QStringLiteral("total_ms"), double(m_previewExportTotalMs));
        for (auto iterator = m_clearChannelCounts.constBegin(); iterator != m_clearChannelCounts.constEnd(); ++iterator) {
            clearChannels.insert(iterator.key(), double(iterator.value()));
        }
    }
    QJsonObject root;
    root.insert(QStringLiteral("clear_channels"), clearChannels);
    root.insert(QStringLiteral("gui_thread_work_ms"), guiWork);
    root.insert(QStringLiteral("patches"), patches);
    root.insert(QStringLiteral("resyncs"), resyncs);
    root.insert(QStringLiteral("hydration"), hydration);
    root.insert(QStringLiteral("preview_exports"), preview);
    return QString::fromUtf8(QJsonDocument(root).toJson(QJsonDocument::Indented)).trimmed();
}
} // namespace ghostwriter
