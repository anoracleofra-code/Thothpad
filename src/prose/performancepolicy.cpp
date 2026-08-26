/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "performancepolicy.h"

#include <QSettings>
#include <QThread>

namespace ghostwriter
{
namespace
{
constexpr auto SettingsGroup = "prose/performance";

int boundedProcessors()
{
    return qMax(1, QThread::idealThreadCount());
}
}

PerformancePolicy PerformancePolicy::detected()
{
    PerformancePolicy policy;
    policy.logicalProcessors = boundedProcessors();
    if (policy.logicalProcessors <= 2) {
        policy.backgroundThreads = 1;
        policy.memoryLimitMb = 768;
        policy.analysisDelayMs = 2200;
    } else if (policy.logicalProcessors <= 4) {
        policy.backgroundThreads = 1;
        policy.memoryLimitMb = 1024;
        policy.analysisDelayMs = 1900;
    } else {
        policy.backgroundThreads = qMin(4, qMax(2, policy.logicalProcessors / 4));
        policy.memoryLimitMb = 1536;
        policy.analysisDelayMs = 1500;
    }
    return policy;
}

PerformancePolicy PerformancePolicy::load()
{
    PerformancePolicy policy = detected();
    QSettings settings;
    settings.beginGroup(QString::fromLatin1(SettingsGroup));
    policy.automatic = settings.value(QStringLiteral("automatic"), true).toBool();
    policy.previewAcceleration = settings.value(QStringLiteral("preview_acceleration"), true).toBool();
    if (!policy.automatic) {
        policy.backgroundThreads = qBound(1, settings.value(QStringLiteral("background_threads"), policy.backgroundThreads).toInt(), boundedProcessors());
        policy.memoryLimitMb = qBound(256, settings.value(QStringLiteral("memory_limit_mb"), policy.memoryLimitMb).toInt(), 8192);
        policy.analysisDelayMs = qBound(500, settings.value(QStringLiteral("analysis_delay_ms"), policy.analysisDelayMs).toInt(), 10000);
        policy.overlayBudgetMs = qBound(1, settings.value(QStringLiteral("overlay_budget_ms"), policy.overlayBudgetMs).toInt(), 8);
    }
    settings.endGroup();
    return policy;
}

void PerformancePolicy::save() const
{
    QSettings settings;
    settings.beginGroup(QString::fromLatin1(SettingsGroup));
    settings.setValue(QStringLiteral("automatic"), automatic);
    settings.setValue(QStringLiteral("background_threads"), qBound(1, backgroundThreads, boundedProcessors()));
    settings.setValue(QStringLiteral("memory_limit_mb"), qBound(256, memoryLimitMb, 8192));
    settings.setValue(QStringLiteral("analysis_delay_ms"), qBound(500, analysisDelayMs, 10000));
    settings.setValue(QStringLiteral("overlay_budget_ms"), qBound(1, overlayBudgetMs, 8));
    settings.setValue(QStringLiteral("preview_acceleration"), previewAcceleration);
    settings.endGroup();
}

QJsonObject PerformancePolicy::toJson() const
{
    return {
        {QStringLiteral("mode"), automatic ? QStringLiteral("automatic") : QStringLiteral("manual")},
        {QStringLiteral("logical_processors"), logicalProcessors},
        {QStringLiteral("background_threads"), backgroundThreads},
        {QStringLiteral("memory_limit_mb"), memoryLimitMb},
        {QStringLiteral("analysis_delay_ms"), analysisDelayMs},
        {QStringLiteral("overlay_budget_ms"), overlayBudgetMs},
        {QStringLiteral("preview_acceleration"), previewAcceleration},
        {QStringLiteral("core_gpu_acceleration"), false},
    };
}
}
