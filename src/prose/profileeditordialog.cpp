/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#include "profileeditordialog.h"
#include "../messageboxhelper.h"
#include <QDialogButtonBox>
#include <QFormLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QLineEdit>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QRegularExpression>
#include <QSet>
#include <QTabWidget>
#include <QVBoxLayout>
#include <QWidget>
namespace ghostwriter
{
ProfileEditorDialog::ProfileEditorDialog(
    const QJsonObject &profile,
    QWidget *parent)
    : QDialog(parent)
    , m_original(profile)
    , m_name(new QLineEdit(profile.value(QStringLiteral("name")).toString(), this))
    , m_registerTarget(new QLineEdit(
          profile.value(QStringLiteral("register_target")).toString(), this))
    , m_hardBans(new QPlainTextEdit(listText(
          profile.value(QStringLiteral("hard_bans"))), this))
    , m_softFlags(new QPlainTextEdit(listText(
          profile.value(QStringLiteral("soft_flags"))), this))
    , m_prefer(new QPlainTextEdit(listText(
          profile.value(QStringLiteral("prefer"))), this))
    , m_avoid(new QPlainTextEdit(listText(
          profile.value(QStringLiteral("avoid"))), this))
    , m_weights(new QPlainTextEdit(QString::fromUtf8(QJsonDocument(
          profile.value(QStringLiteral("analyzer_weights")).toObject())
              .toJson(QJsonDocument::Indented)), this))
    , m_thresholds(new QPlainTextEdit(objectText(
          profile.value(QStringLiteral("thresholds"))), this))
    , m_dialogueExclusions(new QPlainTextEdit(objectText(
          profile.value(QStringLiteral("dialogue_exclusions"))), this))
    , m_markdownExclusions(new QPlainTextEdit(objectText(
          profile.value(QStringLiteral("markdown_exclusions"))), this))
    , m_lenses(new QPlainTextEdit(objectText(
          profile.value(QStringLiteral("lenses"))), this))
    , m_voiceStats(new QPlainTextEdit(objectText(
          profile.value(QStringLiteral("voice_stats"))), this))
    , m_voiceFingerprint(new QPlainTextEdit(objectText(
          profile.value(QStringLiteral("voice_fingerprint"))), this))
    , m_calibrationProfile(new QLineEdit(
          profile.value(QStringLiteral("calibration_profile")).toString(), this))
{
    setWindowTitle(tr("Edit Prose Profile"));
    resize(760, 720);
    m_name->setReadOnly(true);
    for (QPlainTextEdit *editor : {m_hardBans, m_softFlags, m_prefer, m_avoid}) {
        editor->setMaximumBlockCount(500);
        editor->setPlaceholderText(tr("One phrase or preference per line"));
    }
    for (QPlainTextEdit *editor : {
             m_weights,
             m_thresholds,
             m_dialogueExclusions,
             m_markdownExclusions,
             m_lenses,
             m_voiceStats,
             m_voiceFingerprint,
         }) {
        editor->setMaximumBlockCount(1000);
        editor->setTabChangesFocus(true);
    }
    m_weights->setPlaceholderText(tr("JSON object mapping analyzer names to numeric weights"));
    m_thresholds->setPlaceholderText(tr("JSON object mapping rules or categories to thresholds"));
    m_dialogueExclusions->setPlaceholderText(tr("JSON object mapping analyzers to dialogue behavior"));
    m_markdownExclusions->setPlaceholderText(tr("JSON object mapping Markdown regions to exclusions"));
    m_lenses->setPlaceholderText(tr("JSON object containing lens modes, colors, priorities, and decorations"));
    m_voiceStats->setPlaceholderText(tr("JSON object containing measured sentence, paragraph, and dialogue habits"));
    m_voiceFingerprint->setPlaceholderText(tr("JSON object containing measured voice characteristics"));
    m_calibrationProfile->setPlaceholderText(tr("Saved calibration name"));
    auto *writingTab = new QWidget(this);
    auto *writingForm = new QFormLayout(writingTab);
    writingForm->addRow(tr("Profile"), m_name);
    writingForm->addRow(tr("Register target"), m_registerTarget);
    writingForm->addRow(tr("General rules"), m_hardBans);
    writingForm->addRow(tr("Soft flags"), m_softFlags);
    writingForm->addRow(tr("Prefer"), m_prefer);
    writingForm->addRow(tr("Avoid"), m_avoid);
    auto *analysisTab = new QWidget(this);
    auto *analysisForm = new QFormLayout(analysisTab);
    analysisForm->addRow(tr("Analyzer weights"), m_weights);
    analysisForm->addRow(tr("Thresholds"), m_thresholds);
    analysisForm->addRow(tr("Dialogue exclusions"), m_dialogueExclusions);
    analysisForm->addRow(tr("Markdown exclusions"), m_markdownExclusions);
    auto *presentationTab = new QWidget(this);
    auto *presentationForm = new QFormLayout(presentationTab);
    presentationForm->addRow(tr("Lenses"), m_lenses);
    presentationForm->addRow(tr("Voice statistics"), m_voiceStats);
    presentationForm->addRow(tr("Voice fingerprint"), m_voiceFingerprint);
    presentationForm->addRow(tr("Calibration profile"), m_calibrationProfile);
    auto *tabs = new QTabWidget(this);
    tabs->addTab(writingTab, tr("Writing"));
    tabs->addTab(analysisTab, tr("Analysis"));
    tabs->addTab(presentationTab, tr("Lenses and Voice"));
    auto *buttons = new QDialogButtonBox(
        QDialogButtonBox::Save | QDialogButtonBox::Cancel, this);
    connect(buttons, &QDialogButtonBox::accepted, this, &ProfileEditorDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);
    auto *layout = new QVBoxLayout(this);
    layout->addWidget(tabs, 1);
    layout->addWidget(buttons);
}
QString ProfileEditorDialog::listText(const QJsonValue &value)
{
    QStringList lines;
    for (const QJsonValue &item : value.toArray()) {
        if (item.isString()) {
            lines.append(item.toString());
        }
    }
    return lines.join(QLatin1Char('\n'));
}
QJsonArray ProfileEditorDialog::textList(const QString &text)
{
    QJsonArray result;
    QSet<QString> seen;
    for (const QString &line : text.split(QLatin1Char('\n'))) {
        const QString item = line.trimmed();
        if (!item.isEmpty() && !seen.contains(item.toCaseFolded())) {
            result.append(item);
            seen.insert(item.toCaseFolded());
        }
    }
    return result;
}
QString ProfileEditorDialog::objectText(const QJsonValue &value)
{
    return QString::fromUtf8(QJsonDocument(value.toObject())
                                 .toJson(QJsonDocument::Indented));
}
QJsonObject ProfileEditorDialog::objectValue(const QPlainTextEdit *editor)
{
    return QJsonDocument::fromJson(editor->toPlainText().toUtf8()).object();
}
QJsonObject ProfileEditorDialog::profile() const
{
    QJsonObject result = m_original;
    result.insert(QStringLiteral("name"), m_name->text().trimmed());
    result.insert(QStringLiteral("register_target"), m_registerTarget->text().trimmed());
    result.insert(QStringLiteral("hard_bans"), textList(m_hardBans->toPlainText()));
    result.insert(QStringLiteral("soft_flags"), textList(m_softFlags->toPlainText()));
    result.insert(QStringLiteral("prefer"), textList(m_prefer->toPlainText()));
    result.insert(QStringLiteral("avoid"), textList(m_avoid->toPlainText()));
    result.insert(QStringLiteral("analyzer_weights"), objectValue(m_weights));
    result.insert(QStringLiteral("thresholds"), objectValue(m_thresholds));
    result.insert(QStringLiteral("dialogue_exclusions"), objectValue(m_dialogueExclusions));
    result.insert(QStringLiteral("markdown_exclusions"), objectValue(m_markdownExclusions));
    result.insert(QStringLiteral("lenses"), objectValue(m_lenses));
    result.insert(QStringLiteral("voice_stats"), objectValue(m_voiceStats));
    result.insert(QStringLiteral("voice_fingerprint"), objectValue(m_voiceFingerprint));
    const QString calibration = m_calibrationProfile->text().trimmed();
    if (calibration.isEmpty()) {
        result.remove(QStringLiteral("calibration_profile"));
    } else {
        result.insert(QStringLiteral("calibration_profile"), calibration);
    }
    return result;
}
void ProfileEditorDialog::accept()
{
    const QString calibration = m_calibrationProfile->text().trimmed();
    static const QRegularExpression calibrationName(
        QStringLiteral("^[A-Za-z0-9][A-Za-z0-9_-]{0,63}(?:\\.json)?$"));
    if (!calibration.isEmpty() && !calibrationName.match(calibration).hasMatch()) {
        MessageBoxHelper::warning(this, tr("Invalid calibration profile"), tr("Calibration profile must be a saved calibration name, not a path."));
        return;
    }
    const QList<QPair<QString, QPlainTextEdit *>> objects = {
        {tr("Analyzer weights"), m_weights},
        {tr("Thresholds"), m_thresholds},
        {tr("Dialogue exclusions"), m_dialogueExclusions},
        {tr("Markdown exclusions"), m_markdownExclusions},
        {tr("Lenses"), m_lenses},
        {tr("Voice statistics"), m_voiceStats},
        {tr("Voice fingerprint"), m_voiceFingerprint},
    };
    for (const auto &[label, editor] : objects) {
        QJsonParseError error;
        const QJsonDocument document = QJsonDocument::fromJson(
            editor->toPlainText().toUtf8(), &error);
        if (error.error != QJsonParseError::NoError || !document.isObject()) {
            MessageBoxHelper::warning(this, tr("Invalid profile setting"), tr("%1 must be a valid JSON object.").arg(label));
            return;
        }
    }
    const QJsonObject weightsObject = objectValue(m_weights);
    for (auto item = weightsObject.constBegin(); item != weightsObject.constEnd(); ++item) {
        if (!item.value().isDouble()) {
            MessageBoxHelper::warning(this, tr("Invalid weight"), tr("Every analyzer weight must be a number."));
            return;
        }
    }
    QDialog::accept();
}
}
