/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QTest>

#include "../../src/prose/prosediagnostic.h"

using namespace ghostwriter;

class ProseDiagnosticTest : public QObject
{
    Q_OBJECT

private slots:
    void shiftsFindingsAfterInsertion();
    void removesFindingsTouchedByEdit();
    void usesQtUtf16Coordinates();
    void parsesGrammarReplacementCandidates();
    void shiftsExtraSpansWithPrimary();
    void parsesExtraSpansFromJson();
};

void ProseDiagnosticTest::shiftsFindingsAfterInsertion()
{
    ProseDiagnostic before;
    before.start = 1;
    before.end = 4;
    ProseDiagnostic after;
    after.start = 8;
    after.end = 12;

    const QList<ProseDiagnostic> adjusted = adjustedDiagnosticsAfterEdit(
        {before, after}, 5, 0, 3);
    QCOMPARE(adjusted.size(), 2);
    QCOMPARE(adjusted.at(0).start, 1);
    QCOMPARE(adjusted.at(1).start, 11);
    QCOMPARE(adjusted.at(1).end, 15);
}

void ProseDiagnosticTest::removesFindingsTouchedByEdit()
{
    ProseDiagnostic finding;
    finding.start = 4;
    finding.end = 10;
    QVERIFY(adjustedDiagnosticsAfterEdit({finding}, 6, 2, 1).isEmpty());
}

void ProseDiagnosticTest::usesQtUtf16Coordinates()
{
    const QString text = QStringLiteral("A\U0001F600" "B");
    QCOMPARE(text.size(), 4);
    ProseDiagnostic finding;
    finding.start = 3;
    finding.end = 4;
    const auto adjusted = adjustedDiagnosticsAfterEdit({finding}, 1, 2, 1);
    QCOMPARE(adjusted.at(0).start, 2);
    QCOMPARE(adjusted.at(0).end, 3);
}

void ProseDiagnosticTest::parsesGrammarReplacementCandidates()
{
    QJsonObject object;
    object.insert(QStringLiteral("rule_id"), QStringLiteral("grammar.harper.article"));
    object.insert(QStringLiteral("replacements"), QJsonArray{QStringLiteral("a"), QStringLiteral("the")});
    const ProseDiagnostic finding = ProseDiagnostic::fromJson(object);
    QCOMPARE(finding.replacements, QStringList({QStringLiteral("a"), QStringLiteral("the")}));
}

void ProseDiagnosticTest::shiftsExtraSpansWithPrimary()
{
    ProseDiagnostic pair;
    pair.start = 33;
    pair.end = 40;
    pair.extraSpans.append({0, 7});

    // Insertion before both spans shifts primary and extras alike.
    const auto afterInsert = adjustedDiagnosticsAfterEdit({pair}, 0, 0, 5);
    QCOMPARE(afterInsert.size(), 1);
    QCOMPARE(afterInsert.at(0).start, 38);
    QCOMPARE(afterInsert.at(0).end, 45);
    QCOMPARE(afterInsert.at(0).extraSpans.size(), 1);
    QCOMPARE(afterInsert.at(0).extraSpans.at(0).first, 5);
    QCOMPARE(afterInsert.at(0).extraSpans.at(0).second, 12);

    // An edit overlapping the primary drops the diagnostic, extras included.
    QVERIFY(adjustedDiagnosticsAfterEdit({pair}, 35, 2, 1).isEmpty());
}

void ProseDiagnosticTest::parsesExtraSpansFromJson()
{
    QJsonObject object;
    object.insert(QStringLiteral("rule_id"), QStringLiteral("repetition.repetition"));
    object.insert(QStringLiteral("start_utf16"), 33);
    object.insert(QStringLiteral("end_utf16"), 40);
    object.insert(QStringLiteral("extra_spans_utf16"), QJsonArray{QJsonArray{0, 7}, QJsonArray{20, 26}});
    const ProseDiagnostic finding = ProseDiagnostic::fromJson(object);
    QCOMPARE(finding.extraSpans.size(), 2);
    QCOMPARE(finding.extraSpans.at(0).first, 0);
    QCOMPARE(finding.extraSpans.at(0).second, 7);
    QCOMPARE(finding.extraSpans.at(1).first, 20);
    QCOMPARE(finding.extraSpans.at(1).second, 26);

    // Malformed spans are ignored; missing field yields an empty list.
    object.insert(QStringLiteral("extra_spans_utf16"), QJsonArray{QJsonArray{5}, QStringLiteral("x")});
    const ProseDiagnostic tolerant = ProseDiagnostic::fromJson(object);
    QCOMPARE(tolerant.extraSpans.size(), 0);
    QVERIFY(ProseDiagnostic::fromJson(QJsonObject{}).extraSpans.isEmpty());
}

QTEST_MAIN(ProseDiagnosticTest)

#include "prosediagnostictest.moc"
