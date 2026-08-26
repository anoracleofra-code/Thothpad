/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef PERFORMANCE_SETTINGS_DIALOG_H
#define PERFORMANCE_SETTINGS_DIALOG_H

#include <QDialog>

class QCheckBox;
class QLabel;
class QSpinBox;

namespace ghostwriter
{
class PerformanceSettingsDialog : public QDialog
{
    Q_OBJECT

public:
    explicit PerformanceSettingsDialog(QWidget *parent = nullptr);

protected:
    void accept() override;

private:
    void updateControls();

    QCheckBox *m_automatic;
    QSpinBox *m_backgroundThreads;
    QSpinBox *m_memoryLimit;
    QSpinBox *m_analysisDelay;
    QSpinBox *m_overlayBudget;
    QCheckBox *m_previewAcceleration;
    QLabel *m_effectivePolicy;
};
}

#endif
