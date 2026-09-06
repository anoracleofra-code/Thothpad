// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <QJsonArray>
#include <QJsonObject>
#include <QString>

namespace ghostwriter
{
// All ranges are half-open Qt UTF-16 offsets into the unchanged manuscript.
struct WritingAnalysis {
    QString text;
    QString body;
    QJsonArray dialogue;
    QJsonArray sentences;
    QJsonArray scopes;
    static WritingAnalysis build(const QString &text, const QJsonObject &workspace);
    QJsonObject report(int start, int end, const QString &speaker = QString()) const;
    static QString anchor(const QString &text, int start, int end);
};
}
