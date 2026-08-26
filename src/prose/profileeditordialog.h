/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef PROFILE_EDITOR_DIALOG_H
#define PROFILE_EDITOR_DIALOG_H

#include <QDialog>
#include <QJsonArray>
#include <QJsonObject>

class QLineEdit;
class QPlainTextEdit;

namespace ghostwriter
{
class ProfileEditorDialog : public QDialog
{
    Q_OBJECT

public:
    explicit ProfileEditorDialog(const QJsonObject &profile, QWidget *parent = nullptr);

    QJsonObject profile() const;

public slots:
    void accept() override;

private:
    static QString listText(const QJsonValue &value);
    static QJsonArray textList(const QString &text);
    static QString objectText(const QJsonValue &value);
    static QJsonObject objectValue(const QPlainTextEdit *editor);

    QJsonObject m_original;
    QLineEdit *m_name;
    QLineEdit *m_registerTarget;
    QPlainTextEdit *m_hardBans;
    QPlainTextEdit *m_softFlags;
    QPlainTextEdit *m_prefer;
    QPlainTextEdit *m_avoid;
    QPlainTextEdit *m_weights;
    QPlainTextEdit *m_thresholds;
    QPlainTextEdit *m_dialogueExclusions;
    QPlainTextEdit *m_markdownExclusions;
    QPlainTextEdit *m_lenses;
    QPlainTextEdit *m_voiceStats;
    QPlainTextEdit *m_voiceFingerprint;
    QLineEdit *m_calibrationProfile;
};
}

#endif
