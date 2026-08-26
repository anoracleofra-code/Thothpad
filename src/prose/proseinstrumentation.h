/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef PROSE_INSTRUMENTATION_H
#define PROSE_INSTRUMENTATION_H

#include <QElapsedTimer>
#include <QHash>
#include <QMutex>
#include <QString>
#include <QVector>

namespace ghostwriter
{

/**
 * Thread-safe measurement singleton for the prose-analysis path. Call sites
 * are guarded by THOTHPAD_INSTRUMENTATION so release builds carry no cost;
 * when built, counters accumulate until process exit, where a JSON summary
 * is written to stderr if THOTHPAD_DUMP_INSTRUMENTS=1 is set.
 */
class ProseInstrumentation
{
public:
    static constexpr qsizetype MaximumGuiWorkSamples = 4096;

    static ProseInstrumentation *instance();

    ~ProseInstrumentation();

    void reset();

    void recordClearChannel(const QString &channelName);
    void recordGuiThreadWork(qint64 milliseconds);
    void recordPatchFrame(int bytes);
    void recordResyncEvent(int bytes);
    void beginHydrationCycle();
    void recordHydrationTick();
    void recordSpansApplied(int count);
    void recordBlocksPreserved(int count);
    void recordPreviewExport(qint64 milliseconds);

    bool dumpIfRequested() const;
    QString toJson() const;

private:
    ProseInstrumentation() = default;

    mutable QMutex m_mutex;
    QHash<QString, quint64> m_clearChannelCounts;
    quint64 m_guiWorkCount = 0;
    qint64 m_guiWorkTotalMs = 0;
    qint64 m_guiWorkMaxMs = 0;
    QVector<qint64> m_guiWorkSamples;
    quint64 m_patchFrames = 0;
    qint64 m_patchBytes = 0;
    quint64 m_resyncEvents = 0;
    qint64 m_resyncBytes = 0;
    quint64 m_hydrationTicks = 0;
    quint64 m_spansApplied = 0;
    quint64 m_blocksPreserved = 0;
    QElapsedTimer m_hydrationTimer;
    qint64 m_firstVisibleSpanMs = -1;
    bool m_firstSpanRecorded = false;
    quint64 m_previewExports = 0;
    qint64 m_previewExportTotalMs = 0;
};

} // namespace ghostwriter

#endif
