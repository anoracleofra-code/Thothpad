// SPDX-License-Identifier: GPL-3.0-or-later
#include "statistics/writinganalysis.h"
#include <QTest>
#include <QFile>
#include <QElapsedTimer>
using namespace ghostwriter;
class WritingAnalysisTest : public QObject
{
    Q_OBJECT
    QJsonObject workspace()
    {
        return {{"agents",
                 QJsonArray{QJsonObject{{"id", "mara"}, {"kind", "character"}, {"name", "Mara Vale"}},
                            QJsonObject{{"id", "bezu"}, {"kind", "character"}, {"name", "Bezu"}}}},
                {"writing_review", QJsonObject{{"aliases", QJsonObject{{"mara", QJsonArray{"Mara"}}}}}}};
    }
private slots:
    void actionBeatsAndInterruptedSpeech()
    {
        const QString text = "Bezu rode into view. \"Your mount is lost.\"\n\n"
            "\"Aye.\" Lazan spat onto the sand. \"It will return.\" He pulled his sleeves down. \"Soon.\"\n\n"
            "\"Poetic,\" Lazan said, searching for water. \"Becoming a bard?\"\n\n"
            "Bezu quietly asked,\n\"Why, Lazan?\"\n\n"
            "\"Lazan said it was safe.\"\n\n"
            "Lazan smiled. Bezu shook his head. \"Which one?\"";
        const auto a = WritingAnalysis::build(text, {});
        QCOMPARE(a.dialogue.size(), 9);
        QCOMPARE(a.dialogue[0].toObject().value("speaker").toString(), QString("Bezu"));
        for (int i = 1; i <= 5; ++i) QCOMPARE(a.dialogue[i].toObject().value("speaker").toString(), QString("Lazan"));
        QCOMPARE(a.dialogue[6].toObject().value("speaker").toString(), QString("Bezu"));
        QVERIFY(a.dialogue[7].toObject().value("speaker_id").toString().isEmpty());
        QVERIFY(a.dialogue[8].toObject().value("speaker_id").toString().isEmpty());
        QVERIFY(a.dialogue[0].toObject().value("confidence").toString().startsWith("Inferred"));
        const auto indented = WritingAnalysis::build("   Bezu nodded. “Yes.”\n   “Who knows?”", {});
        QCOMPARE(indented.dialogue.size(), 2);
        QVERIFY(indented.dialogue[1].toObject().value("speaker_id").toString().isEmpty());
        const auto quotedName = WritingAnalysis::build("\"Zoran said hello.\"\n\nZoran smiled. \"Greetings.\"", {});
        QVERIFY(quotedName.dialogue[0].toObject().value("speaker_id").toString().isEmpty());
    }
    void optionalLocalCorpus()
    {
        const QString filename = qEnvironmentVariable("THOTHPAD_DIALOGUE_CORPUS");
        if (filename.isEmpty()) QSKIP("Optional read-only corpus check");
        QFile file(filename);
        QVERIFY(file.open(QIODevice::ReadOnly));
        const QString text = QString::fromUtf8(file.readAll());
        QElapsedTimer clock; clock.start();
        const auto a = WritingAnalysis::build(text, {});
        QHash<QString, int> counts;
        for (const auto &v : a.dialogue) {
            const auto line = v.toObject();
            QCOMPARE(text.mid(line.value("start").toInt(), line.value("end").toInt() - line.value("start").toInt()), line.value("quote").toString());
            ++counts[line.value("confidence").toString()];
        }
        qInfo() << "Dialogue lines:" << a.dialogue.size() << "Elapsed ms:" << clock.elapsed() << "Attribution categories:" << counts;
        QVERIFY(!a.dialogue.isEmpty());
    }
    void namesAreDiscoveredWithoutInventingPronounAttribution()
    {
        const QString text = "\"Hello.\" Lazan said.\n\nBezu asked, \"Why?\"\n\n\"No idea.\" he replied.\n\n\"Neither do I.\" She said.";
        const auto a = WritingAnalysis::build(text, {});
        QCOMPARE(a.dialogue.size(), 4);
        QCOMPARE(a.dialogue[0].toObject().value("speaker_id").toString(), QString("detected:lazan"));
        QCOMPARE(a.dialogue[1].toObject().value("speaker_id").toString(), QString("detected:bezu"));
        QVERIFY(a.dialogue[2].toObject().value("speaker_id").toString().isEmpty());
        QVERIFY(a.dialogue[3].toObject().value("speaker_id").toString().isEmpty());
    }
    void quotesTagsUnicodeAndScopes()
    {
        const QString text = QString::fromUtf8(
            "# Chapter one\n\n🌙 ‘Don't wait,’ Mara said.\n\n## The gate\n\nBezu asked, \"Why now?\"\n\n\"No tag here.\"\n\n```cpp\n\"not dialogue\"\n```\n");
        auto w = workspace();
        const int scene = text.indexOf("##");
        w.insert("scopes",
                 QJsonArray{QJsonObject{{"id", "one"}, {"title", "Chapter one"}, {"level", 1}, {"start", 0}, {"end", text.size()}},
                            QJsonObject{{"id", "gate"}, {"title", "The gate"}, {"level", 2}, {"start", scene}, {"end", text.size()}}});
        const auto analysis = WritingAnalysis::build(text, w);
        QCOMPARE(analysis.dialogue.size(), 3);
        const auto first = analysis.dialogue[0].toObject(), second = analysis.dialogue[1].toObject(), third = analysis.dialogue[2].toObject();
        QCOMPARE(first.value("speaker_id").toString(), QString("mara"));
        QCOMPARE(first.value("content").toString(), QString("Don't wait,"));
        QCOMPARE(first.value("confidence").toString(), QString("Explicit tag"));
        QVERIFY(first.value("scene").toString().isEmpty());
        QCOMPARE(second.value("speaker_id").toString(), QString("bezu"));
        QCOMPARE(second.value("scene").toString(), QString("The gate"));
        QVERIFY(third.value("speaker_id").toString().isEmpty());
        for (const auto &v : analysis.dialogue) {
            const auto d = v.toObject();
            QCOMPARE(text.mid(d.value("start").toInt(), d.value("end").toInt() - d.value("start").toInt()), d.value("quote").toString());
        }
        const auto report = analysis.report(scene, text.size());
        QCOMPARE(report.value("dialogue_lines").toInt(), 2);
        QCOMPARE(report.value("unknown_lines").toInt(), 1);
        QCOMPARE(report.value("dialogue_words").toInt(), 5);
        QCOMPARE(analysis.report(0, text.size(), "mara").value("dialogue_lines").toInt(), 1);
        QVERIFY(report.value("dialogue_percent").toDouble() <= 100);
    }
    void correctionsSurviveUnrelatedPrefixButNeverAmbiguousCopies()
    {
        const QString text = QString(100, 'x') + "\n\n\"Wait here.\"\n\n" + QString(100, 'z');
        auto w = workspace();
        auto a = WritingAnalysis::build(text, w);
        QCOMPARE(a.dialogue.size(), 1);
        auto review = w.value("writing_review").toObject();
        review.insert("assignments", QJsonObject{{a.dialogue[0].toObject().value("anchor").toString(), "mara"}});
        w.insert("writing_review", review);
        a = WritingAnalysis::build("A new opening.\n" + text, w);
        QCOMPARE(a.dialogue[0].toObject().value("confidence").toString(), QString("Confirmed by you"));
        a = WritingAnalysis::build(text + "\n\n" + text, w);
        QCOMPARE(a.dialogue.size(), 2);
        for (const auto &v : a.dialogue) {
            QVERIFY(v.toObject().value("speaker_id").toString().isEmpty());
            QVERIFY(!v.toObject().value("unique_anchor").toBool());
        }
    }
    void uncertainTagsAndBrokenQuotesStayUnassigned()
    {
        auto w = workspace();
        const QString text = "Mara said, \"Stay.\" Bezu said.\n\n\"Unclosed quote\n\n\"A fresh paragraph.\"\n\nDon't mistake contractions for dialogue.";
        const auto a = WritingAnalysis::build(text, w);
        QCOMPARE(a.dialogue.size(), 2);
        QVERIFY(a.dialogue[0].toObject().value("speaker_id").toString().isEmpty());
        QCOMPARE(a.dialogue[1].toObject().value("content").toString(), QString("A fresh paragraph."));
    }
    void metricsHaveScopedCountsAndNoFabricatedReadability()
    {
        const QString text = "# Heading\n\nOne two three. One two three!\n\n# Next\n\nFour five.\n";
        const auto a = WritingAnalysis::build(text, {});
        const auto all = a.report(0, text.size()), first = a.report(0, text.indexOf("# Next"));
        QCOMPARE(all.value("words").toInt(), 8);
        QCOMPARE(first.value("words").toInt(), 6);
        QCOMPARE(all.value("sentences").toInt(), 3);
        QCOMPARE(first.value("mean_length").toDouble(), 3.0);
        QCOMPARE(first.value("length_variation").toDouble(), 0.0);
        QCOMPARE(first.value("phrases").toArray().first().toObject().value("text").toString(), QString("one two three"));
        QCOMPARE(a.report(0, 0).value("words").toInt(), 0);
        QCOMPARE(a.report(0, 0).value("coleman_liau").toDouble(), 0.0);
    }
};
QTEST_GUILESS_MAIN(WritingAnalysisTest)
#include "writinganalysistest.moc"
