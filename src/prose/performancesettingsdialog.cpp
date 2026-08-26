/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "performancesettingsdialog.h"

#include <QCheckBox>
#include <QDialogButtonBox>
#include <QFormLayout>
#include <QLabel>
#include <QSpinBox>
#include <QThread>
#include <QVBoxLayout>

#include "performancepolicy.h"

namespace ghostwriter
{
PerformanceSettingsDialog::PerformanceSettingsDialog(QWidget *parent)
    : QDialog(parent)
    , m_automatic(new QCheckBox(tr("Tune resources automatically"), this))
    , m_backgroundThreads(new QSpinBox(this))
    , m_memoryLimit(new QSpinBox(this))
    , m_analysisDelay(new QSpinBox(this))
    , m_overlayBudget(new QSpinBox(this))
    , m_previewAcceleration(new QCheckBox(tr("Allow preview hardware acceleration"), this))
    , m_effectivePolicy(new QLabel(this))
{
    setWindowTitle(tr("Performance Settings"));
    setModal(true);

    m_backgroundThreads->setRange(1, qMax(1, QThread::idealThreadCount()));
    m_memoryLimit->setRange(256, 8192);
    m_memoryLimit->setSuffix(tr(" MB"));
    m_analysisDelay->setRange(500, 10000);
    m_analysisDelay->setSingleStep(100);
    m_analysisDelay->setSuffix(tr(" ms"));
    m_overlayBudget->setRange(1, 8);
    m_overlayBudget->setSuffix(tr(" ms"));
    m_effectivePolicy->setWordWrap(true);

    const PerformancePolicy saved = PerformancePolicy::load();
    m_automatic->setChecked(saved.automatic);
    m_backgroundThreads->setValue(saved.backgroundThreads);
    m_memoryLimit->setValue(saved.memoryLimitMb);
    m_analysisDelay->setValue(saved.analysisDelayMs);
    m_overlayBudget->setValue(saved.overlayBudgetMs);
    m_previewAcceleration->setChecked(saved.previewAcceleration);

    auto *form = new QFormLayout;
    form->addRow(tr("Background threads"), m_backgroundThreads);
    form->addRow(tr("Engine memory ceiling"), m_memoryLimit);
    form->addRow(tr("Full review delay"), m_analysisDelay);
    form->addRow(tr("Overlay frame budget"), m_overlayBudget);

    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, this);
    auto *layout = new QVBoxLayout(this);
    layout->addWidget(m_automatic);
    layout->addWidget(m_effectivePolicy);
    layout->addLayout(form);
    layout->addWidget(m_previewAcceleration);
    auto *gpuNote = new QLabel(tr("Deterministic prose analysis remains CPU-only. Local model providers manage their own GPU use."), this);
    gpuNote->setWordWrap(true);
    layout->addWidget(gpuNote);
    layout->addWidget(buttons);

    connect(m_automatic, &QCheckBox::toggled, this, &PerformanceSettingsDialog::updateControls);
    connect(m_backgroundThreads, &QSpinBox::valueChanged, this, &PerformanceSettingsDialog::updateControls);
    connect(m_memoryLimit, &QSpinBox::valueChanged, this, &PerformanceSettingsDialog::updateControls);
    connect(m_analysisDelay, &QSpinBox::valueChanged, this, &PerformanceSettingsDialog::updateControls);
    connect(m_overlayBudget, &QSpinBox::valueChanged, this, &PerformanceSettingsDialog::updateControls);
    connect(m_previewAcceleration, &QCheckBox::toggled, this, &PerformanceSettingsDialog::updateControls);
    connect(buttons, &QDialogButtonBox::accepted, this, &PerformanceSettingsDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);
    updateControls();
}

void PerformanceSettingsDialog::updateControls()
{
    const bool manual = !m_automatic->isChecked();
    m_backgroundThreads->setEnabled(manual);
    m_memoryLimit->setEnabled(manual);
    m_analysisDelay->setEnabled(manual);
    m_overlayBudget->setEnabled(manual);
    const PerformancePolicy effective = manual ? PerformancePolicy{
                                                      false,
                                                      qMax(1, QThread::idealThreadCount()),
                                                      m_backgroundThreads->value(),
                                                      m_memoryLimit->value(),
                                                      m_analysisDelay->value(),
                                                      m_overlayBudget->value(),
                                                      m_previewAcceleration->isChecked(),
                                                  }
                                                : PerformancePolicy::detected();
    m_effectivePolicy->setText(tr("Effective policy: %1 background thread(s), %2 MB engine ceiling, %3 ms review delay, %4 ms overlay budget.")
                                   .arg(effective.backgroundThreads)
                                   .arg(effective.memoryLimitMb)
                                   .arg(effective.analysisDelayMs)
                                   .arg(effective.overlayBudgetMs));
}

void PerformanceSettingsDialog::accept()
{
    PerformancePolicy policy = PerformancePolicy::detected();
    policy.automatic = m_automatic->isChecked();
    policy.previewAcceleration = m_previewAcceleration->isChecked();
    if (!policy.automatic) {
        policy.backgroundThreads = m_backgroundThreads->value();
        policy.memoryLimitMb = m_memoryLimit->value();
        policy.analysisDelayMs = m_analysisDelay->value();
        policy.overlayBudgetMs = m_overlayBudget->value();
    }
    policy.save();
    QDialog::accept();
}
}
