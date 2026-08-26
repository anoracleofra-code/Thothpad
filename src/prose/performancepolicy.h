/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef PERFORMANCE_POLICY_H
#define PERFORMANCE_POLICY_H

#include <QJsonObject>

namespace ghostwriter
{
struct PerformancePolicy {
    bool automatic = true;
    int logicalProcessors = 1;
    int backgroundThreads = 1;
    int memoryLimitMb = 768;
    int analysisDelayMs = 2200;
    int overlayBudgetMs = 4;
    bool previewAcceleration = true;

    static PerformancePolicy detected();
    static PerformancePolicy load();
    void save() const;
    QJsonObject toJson() const;
};
}

#endif
