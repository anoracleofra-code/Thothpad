/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QTest>
#include <QTextDocument>

#include "../../src/editor/breathmapwidget.h"
#include "../../src/editor/markdowneditor.h"
#include "../../src/outlinewidget.h"

using namespace ghostwriter;

class OutlineWidgetTest : public QObject
{
    Q_OBJECT

private slots:
    void pacesChaptersIntoDistinctBuckets();
    void neutralSectionGetsNoTint();
    void readerModeRestoresState();
    void breathMapModelsSentenceLengths();
};

/*
 * Chapter 1 is nearly all quoted dialogue (fast); chapter 2 is long-sentence
 * narration with no dialogue (slow). The outline must bucket them apart.
 */
void OutlineWidgetTest::pacesChaptersIntoDistinctBuckets()
{
    auto *document = new MarkdownDocument();
    // Heap-allocated editor per the house test pattern: editor teardown
    // ordering (highlighter dtor firing contentsChange) crashes stack
    // layouts, and leaking in a test process is acceptable.
    auto *editor = new MarkdownEditor(document, ColorScheme{});
    QString text = QStringLiteral("# Fast\n\n");
    for (int i = 0; i < 12; ++i) {
        text += QStringLiteral("\u201CLine %1 of quick talk,\u201D she said.\n").arg(i);
    }
    text += QStringLiteral("\n# Slow\n\n");
    for (int i = 0; i < 4; ++i) {
        text += QStringLiteral(
                    "A very long winding narration sentence number %1 travels through the quiet valley "
                    "past the old stone mill and over the ridge where the crows gather at dusk "
                    "before settling into the pines for the night.\n")
                    .arg(i);
    }
    editor->setPlainText(text);

    OutlineWidget outline(editor);
    // reloadOutline runs on contentsChange; the pacing pass is debounced 500ms.
    QTest::qWait(700);

    QCOMPARE(outline.count(), 2);
    const int fastBucket = outline.item(0) ? outline.item(0)->data(OutlineWidget::PACING_BUCKET_ROLE).toInt() : OutlineWidget::PACING_NEUTRAL;
    const int slowBucket = outline.item(1) ? outline.item(1)->data(OutlineWidget::PACING_BUCKET_ROLE).toInt() : OutlineWidget::PACING_NEUTRAL;
    QCOMPARE(fastBucket, OutlineWidget::PACING_FAST);
    QCOMPARE(slowBucket, OutlineWidget::PACING_SLOW);
}

void OutlineWidgetTest::neutralSectionGetsNoTint()
{
    auto *document = new MarkdownDocument();
    auto *editor = new MarkdownEditor(document, ColorScheme{});
    QString text = QStringLiteral("# Mixed\n\n");
    for (int i = 0; i < 6; ++i) {
        text += QStringLiteral("The walker counted %1 coins by the gate before the rain arrived.\n").arg(i);
        text += QStringLiteral("\u201CTypical weather,\u201D he muttered.\n");
    }
    editor->setPlainText(text);

    OutlineWidget outline(editor);
    QTest::qWait(700);

    QCOMPARE(outline.count(), 1);
    const int bucket = outline.item(0)->data(OutlineWidget::PACING_BUCKET_ROLE).toInt();
    QCOMPARE(bucket, OutlineWidget::PACING_NEUTRAL);
    QVERIFY(outline.item(0)->data(Qt::BackgroundRole).isNull());
}

void OutlineWidgetTest::readerModeRestoresState()
{
    auto *document = new MarkdownDocument();
    auto *editor = new MarkdownEditor(document, ColorScheme{});
    editor->setPlainText(QStringLiteral("# Heading\n\nSome prose to highlight.\n"));
    editor->setFocusMode(FocusModeSentence);
    QVERIFY(editor->highlighter() != nullptr);
    QVERIFY(editor->highlighter()->document() != nullptr);

    editor->setReaderMode(true);
    QCOMPARE(editor->readerMode(), true);
    QCOMPARE(editor->focusMode(), FocusModeTypewriter);
    QVERIFY(editor->highlighter()->document() == nullptr);

    // Idempotent toggle must not clobber the saved state.
    editor->setReaderMode(true);

    editor->setReaderMode(false);
    QCOMPARE(editor->readerMode(), false);
    QCOMPARE(editor->focusMode(), FocusModeSentence);
    QVERIFY(editor->highlighter()->document() != nullptr);
}

void OutlineWidgetTest::breathMapModelsSentenceLengths()
{
    MarkdownDocument document;
    BreathMapWidget breathMap;
    breathMap.setDocument(&document);
    // The widget skips updates while hidden; the model test shows it first.
    breathMap.show();

    // Five consecutive 5-word sentences (monotony run) then a 40-word one.
    QString text;
    for (int i = 0; i < 5; ++i) {
        text += QStringLiteral("The scout %1 the ridge.\n").arg(QChar('a' + i));
    }
    text += QStringLiteral(
        "A very long winding narration sentence travels through the quiet valley past the old "
        "stone mill and over the ridge where the crows gather.\n");
    document.setPlainText(text);
    breathMap.refresh();

    const QVector<int> counts = breathMap.sentenceWordCounts();
    QCOMPARE(counts.size(), 6);
    for (int i = 0; i < 5; ++i) {
        QCOMPARE(counts.at(i), 5);
    }
    QVERIFY(counts.at(5) >= 20);
    QVERIFY(breathMap.monotonyRunDetected());

    // Varied lengths clear the monotony flag.
    document.setPlainText(QStringLiteral("One.\nA pair now.\nThree words here.\nAnd four in this one.\nFinally five words total.\n"));
    breathMap.refresh();
    QVERIFY(!breathMap.monotonyRunDetected());
    QCOMPARE(breathMap.sentenceWordCounts().size(), 5);

    // Hidden widgets must not recompute (zero-cost-when-invisible rule).
    breathMap.hide();
    const QVector<int> frozen = breathMap.sentenceWordCounts();
    document.setPlainText(QStringLiteral("Completely different text arrives while hidden.\n"));
    breathMap.refresh();
    QCOMPARE(breathMap.sentenceWordCounts(), frozen);
}

QTEST_MAIN(OutlineWidgetTest)

#include "outlinewidgettest.moc"
