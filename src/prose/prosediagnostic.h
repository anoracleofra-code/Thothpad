/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef PROSE_DIAGNOSTIC_H
#define PROSE_DIAGNOSTIC_H

#include <QJsonArray>
#include <QJsonObject>
#include <QList>
#include <QMetaType>
#include <QString>

namespace ghostwriter
{
struct ProseDiagnostic
{
    QString id;
    QString ruleId;
    QString analyzer;
    QString category;
    QString level;
    QString excerpt;
    QString explanation;
    QString suggestion;
    QString source;
    QStringList replacements;
    QList<QPair<int, int>> extraSpans;
    int start = 0;
    int end = 0;
    int revision = 0;
    double confidence = 1.0;

    static ProseDiagnostic fromJson(const QJsonObject &object)
    {
        ProseDiagnostic result;
        result.id = object.value(QStringLiteral("id")).toString();
        result.ruleId = object.value(QStringLiteral("rule_id")).toString();
        result.analyzer = object.value(QStringLiteral("analyzer")).toString();
        result.category = object.value(QStringLiteral("category")).toString(result.analyzer);
        result.level = object.value(QStringLiteral("level")).toString();
        result.excerpt = object.value(QStringLiteral("excerpt")).toString();
        result.explanation = object.value(QStringLiteral("explanation")).toString();
        result.suggestion = object.value(QStringLiteral("suggestion")).toString();
        result.source = object.value(QStringLiteral("source")).toString();
        for (const QJsonValue &value : object.value(QStringLiteral("replacements")).toArray()) {
            if (value.isString()) {
                result.replacements.append(value.toString());
            }
        }
        // Related occurrences (e.g., the earlier half of a repetition pair).
        // Optional field: envelopes from older engines simply lack it.
        for (const QJsonValue &value : object.value(QStringLiteral("extra_spans_utf16")).toArray()) {
            const QJsonArray span = value.toArray();
            if (span.size() == 2) {
                const int spanStart = span.at(0).toInt(-1);
                const int spanEnd = span.at(1).toInt(-1);
                if (spanStart >= 0 && spanEnd > spanStart) {
                    result.extraSpans.append({spanStart, spanEnd});
                }
            }
        }
        result.start = object.value(QStringLiteral("start_utf16")).toInt();
        result.end = object.value(QStringLiteral("end_utf16")).toInt();
        result.revision = object.value(QStringLiteral("revision")).toInt();
        result.confidence = object.value(QStringLiteral("confidence")).toDouble(1.0);
        return result;
    }
};

inline QList<ProseDiagnostic> adjustedDiagnosticsAfterEdit(
    const QList<ProseDiagnostic> &diagnostics,
    int position,
    int removed,
    int added)
{
    const int oldChangeEnd = position + removed;
    const int delta = added - removed;
    QList<ProseDiagnostic> adjusted;
    adjusted.reserve(diagnostics.size());
    for (ProseDiagnostic diagnostic : diagnostics) {
        if (diagnostic.end <= position) {
            for (QPair<int, int> &span : diagnostic.extraSpans) {
                span.first += delta;
                span.second += delta;
            }
            adjusted.append(diagnostic);
        } else if (diagnostic.start >= oldChangeEnd) {
            diagnostic.start += delta;
            diagnostic.end += delta;
            for (QPair<int, int> &span : diagnostic.extraSpans) {
                span.first += delta;
                span.second += delta;
            }
            adjusted.append(diagnostic);
        }
    }
    return adjusted;
}
}

Q_DECLARE_METATYPE(ghostwriter::ProseDiagnostic)

#endif
