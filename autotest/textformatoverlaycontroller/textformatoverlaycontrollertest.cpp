/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <algorithm>

#include <QAbstractTextDocumentLayout>
#include <QApplication>
#include <QElapsedTimer>
#include <QHelpEvent>
#include <QImage>
#include <QMouseEvent>
#include <QPainter>
#include <QPlainTextEdit>
#include <QSignalSpy>
#include <QStringList>
#include <QSyntaxHighlighter>
#include <QTest>
#include <QTextCursor>
#include <QTextDocument>
#include <QToolTip>

#include "../../src/editor/textformatoverlaycontroller.h"

using namespace ghostwriter;

namespace
{
class TestHighlighter : public QSyntaxHighlighter
{
public:
    explicit TestHighlighter(QTextDocument *document)
        : QSyntaxHighlighter(document)
    {
    }

protected:
    void highlightBlock(const QString &text) override
    {
        if (!text.isEmpty()) {
            QTextCharFormat format;
            format.setForeground(Qt::red);
            setFormat(0, std::min(5, static_cast<int>(text.length())), format);
        }
    }
};

QTextLayout::FormatRange makeRange(int start, int length, const QTextCharFormat &format)
{
    QTextLayout::FormatRange range;
    range.start = start;
    range.length = length;
    range.format = format;
    return range;
}

QColor blendedOverWhite(const QColor &source)
{
    const qreal alpha = source.alphaF();
    const qreal inverse = 1.0 - alpha;
    return QColor(qRound(source.red() * alpha + 255 * inverse), qRound(source.green() * alpha + 255 * inverse), qRound(source.blue() * alpha + 255 * inverse));
}

bool imageContainsColor(const QImage &image, const QColor &target, int tolerance = 8)
{
    for (int y = 0; y < image.height(); ++y) {
        for (int x = 0; x < image.width(); ++x) {
            const QColor pixel = image.pixelColor(x, y);
            if (qAbs(pixel.red() - target.red()) <= tolerance && qAbs(pixel.green() - target.green()) <= tolerance
                && qAbs(pixel.blue() - target.blue()) <= tolerance) {
                return true;
            }
        }
    }
    return false;
}

QImage renderBlockPaint(const QTextDocument &document, const TextFormatOverlayController &controller)
{
    QImage image(240, 80, QImage::Format_ARGB32_Premultiplied);
    image.fill(Qt::white);
    QPainter painter(&image);
    controller.paintBlock(painter, document.begin(), QRectF(12, 12, 200, 50));
    painter.end();
    return image;
}
}

class TextFormatOverlayControllerTest : public QObject
{
    Q_OBJECT

private slots:
    void preservesSyntaxFormatsAcrossRehighlight();
    void tooltipListsEveryOverlappingFindingByPriority();
    void overlapRanksPaintTopFillAndOutline();
    void threeChannelOverlapCapsAtTwoOutlineRings();
    void sameChannelOverlapKeepsSingleFill();
    void hitTestingReturnsHighestRankedOverlap();
    void clearsOnlyRequestedChannel();
    void indexedStorageScalesAcrossManyBlocks();
    void channelReplacementEmitsOneUpdate();
    void editsInvalidateOnlyAffectedBlocks();
    void splitAndJoinAreAmortized();
    void paintsDecorationAfterOverlappingBackground();
    void documentReplacementInvalidatesCache();
    void rejectsForeignBlocks();
    void identicalAndUncachedUpdatesAreNoOps();
    void trimsWhitespaceFromVisualRanges();
    void hoverDwellShowsTooltip();
    void emptySpaceDoesNotShowNearestHighlightTooltip();
    void doesNotModifyDocumentState();
    void differentialUpdatesPreserveUnchangedBlocks();
    void denseSpansStayWithinBatchBudgetOnLargeDocument();
    void indexedHitTestingScalesWithinDenseBlock();
};

void TextFormatOverlayControllerTest::preservesSyntaxFormatsAcrossRehighlight()
{
    QTextDocument document(QStringLiteral("alpha beta"));
    TestHighlighter highlighter(&document);
    TextFormatOverlayController controller(&document);
    const QTextBlock block = document.begin();
    QTextCharFormat overlay;
    overlay.setBackground(Qt::yellow);
    overlay.setForeground(Qt::green);

    controller.setBlockFormats(QStringLiteral("prose"), block, {makeRange(6, 4, overlay)});
    highlighter.rehighlight();

    const auto syntaxFormats = block.layout()->formats();
    QVERIFY(std::any_of(syntaxFormats.cbegin(), syntaxFormats.cend(), [](const QTextLayout::FormatRange &range) {
        return range.format.foreground().color() == Qt::red;
    }));
    const auto overlays = controller.formatsForBlock(block, QStringLiteral("prose"));
    QCOMPARE(overlays.size(), 1);
    QVERIFY(overlays.first().format.hasProperty(QTextFormat::BackgroundBrush));
    QVERIFY(!overlays.first().format.hasProperty(QTextFormat::ForegroundBrush));
}

void TextFormatOverlayControllerTest::clearsOnlyRequestedChannel()
{
    QTextDocument document(QStringLiteral("alpha beta"));
    TextFormatOverlayController controller(&document);
    const QTextBlock block = document.begin();
    QTextCharFormat spelling;
    spelling.setUnderlineStyle(QTextCharFormat::SpellCheckUnderline);
    QTextCharFormat prose;
    prose.setBackground(Qt::yellow);

    controller.setBlockFormats(TextFormatOverlayController::spellingChannel(), block, {makeRange(0, 5, spelling)});
    controller.setBlockFormats(QStringLiteral("prose"), block, {makeRange(6, 4, prose)});
    controller.clearChannel(TextFormatOverlayController::spellingChannel());

    QVERIFY(controller.formatsForBlock(block, TextFormatOverlayController::spellingChannel()).isEmpty());
    QCOMPARE(controller.formatsForBlock(block, QStringLiteral("prose")).size(), 1);
}

void TextFormatOverlayControllerTest::indexedStorageScalesAcrossManyBlocks()
{
    constexpr int BlockCount = 5000;
    QStringList lines;
    lines.reserve(BlockCount);
    for (int index = 0; index < BlockCount; ++index) {
        lines.append(QStringLiteral("word %1").arg(index));
    }
    QTextDocument document(lines.join(QLatin1Char('\n')));
    TextFormatOverlayController controller(&document);
    QTextCharFormat overlay;
    overlay.setBackground(Qt::yellow);
    QElapsedTimer timer;
    timer.start();
    for (QTextBlock block = document.begin(); block.isValid(); block = block.next()) {
        controller.setBlockFormats(QStringLiteral("prose"), block, {makeRange(0, 4, overlay)});
    }
    controller.clearChannel(QStringLiteral("prose"));
    QVERIFY2(timer.elapsed() < 10000, "Indexed overlay operations exceeded ten seconds");
}

void TextFormatOverlayControllerTest::channelReplacementEmitsOneUpdate()
{
    QTextDocument document(QStringLiteral("alpha\nbeta\ngamma"));
    TextFormatOverlayController controller(&document);
    QTextCharFormat overlay;
    overlay.setBackground(Qt::yellow);
    QHash<int, QList<QTextLayout::FormatRange>> replacements;
    for (QTextBlock block = document.begin(); block.isValid(); block = block.next()) {
        replacements.insert(block.position(), {makeRange(0, block.text().length(), overlay)});
    }

    QSignalSpy changedSpy(&controller, &TextFormatOverlayController::overlaysChanged);
    controller.replaceChannelFormats(QStringLiteral("prose"), replacements);

    QCOMPARE(changedSpy.count(), 1);
    QCOMPARE(controller.cachedBlockCount(), 3);
}

void TextFormatOverlayControllerTest::editsInvalidateOnlyAffectedBlocks()
{
    QTextDocument document(QStringLiteral("zero\nalpha\nbeta"));
    document.documentLayout();
    TextFormatOverlayController controller(&document);
    QTextCharFormat overlay;
    overlay.setBackground(Qt::yellow);
    const QTextBlock first = document.begin();
    const QTextBlock second = first.next();
    const QTextBlock third = second.next();
    controller.setBlockFormats(QStringLiteral("prose"), second, {makeRange(0, 5, overlay)});
    controller.setBlockFormats(QStringLiteral("prose"), third, {makeRange(0, 4, overlay)});

    QSignalSpy contentsChangeSpy(&document, &QTextDocument::contentsChange);
    QTextCursor cursor(&document);
    cursor.setPosition(second.position() + 2);
    cursor.insertText(QStringLiteral("x"));

    QCOMPARE(contentsChangeSpy.count(), 1);
    QVERIFY(controller.formatsForBlock(second).isEmpty());
    QCOMPARE(controller.formatsForBlock(third).size(), 1);
}

void TextFormatOverlayControllerTest::splitAndJoinAreAmortized()
{
    constexpr int BlockCount = 5000;
    QStringList lines(BlockCount, QStringLiteral("word"));
    QTextDocument document(lines.join(QLatin1Char('\n')));
    TextFormatOverlayController controller(&document);
    QTextCharFormat overlay;
    overlay.setBackground(Qt::yellow);
    for (QTextBlock block = document.begin(); block.isValid(); block = block.next()) {
        controller.setBlockFormats(QStringLiteral("prose"), block, {makeRange(0, 4, overlay)});
    }

    QElapsedTimer timer;
    timer.start();
    QTextCursor cursor(&document);
    cursor.setPosition(2);
    int stableCacheCount = -1;
    for (int index = 0; index < 600; ++index) {
        cursor.insertBlock();
        cursor.insertText(QStringLiteral("x"));
        controller.setBlockFormats(QStringLiteral("temporary"), cursor.block(), {makeRange(0, 1, overlay)});
        cursor.select(QTextCursor::BlockUnderCursor);
        cursor.removeSelectedText();
        cursor.deletePreviousChar();
        if (stableCacheCount < 0) {
            stableCacheCount = controller.cachedBlockCount();
        } else {
            QCOMPARE(controller.cachedBlockCount(), stableCacheCount);
        }
    }
    QVERIFY2(timer.elapsed() < 2000, "Split/join edits scanned the full overlay cache");
    QVERIFY(stableCacheCount >= 0);
    QVERIFY(stableCacheCount <= BlockCount);
}

void TextFormatOverlayControllerTest::paintsDecorationAfterOverlappingBackground()
{
    QTextDocument document(QStringLiteral("alpha"));
    document.setTextWidth(200);
    document.documentLayout()->documentSize();
    TextFormatOverlayController controller(&document);
    QTextCharFormat spelling;
    spelling.setUnderlineColor(Qt::red);
    spelling.setUnderlineStyle(QTextCharFormat::SingleUnderline);
    QTextCharFormat prose;
    prose.setBackground(Qt::yellow);
    controller.setBlockFormats(TextFormatOverlayController::spellingChannel(), document.begin(), {makeRange(0, 5, spelling)});
    controller.setBlockFormats(QStringLiteral("prose"), document.begin(), {makeRange(0, 5, prose)});

    QImage image(220, 80, QImage::Format_ARGB32_Premultiplied);
    image.fill(Qt::transparent);
    QPainter painter(&image);
    controller.paintBlock(painter, document.begin(), QRectF(0, 0, 200, 60));
    painter.end();

    bool foundRedDecoration = false;
    for (int y = 0; y < image.height() && !foundRedDecoration; ++y) {
        for (int x = 0; x < image.width(); ++x) {
            if (qRed(image.pixel(x, y)) > 200 && qGreen(image.pixel(x, y)) < 80) {
                foundRedDecoration = true;
                break;
            }
        }
    }
    QVERIFY(foundRedDecoration);
}

void TextFormatOverlayControllerTest::documentReplacementInvalidatesCache()
{
    QTextDocument document(QStringLiteral("old document"));
    TextFormatOverlayController controller(&document);
    QTextCharFormat overlay;
    overlay.setBackground(Qt::yellow);
    controller.setBlockFormats(QStringLiteral("prose"), document.begin(), {makeRange(0, 3, overlay)});
    controller.resetForDocumentReplacement();
    document.setPlainText(QStringLiteral("replacement"));
    QVERIFY(controller.formatsForBlock(document.begin()).isEmpty());
}

void TextFormatOverlayControllerTest::rejectsForeignBlocks()
{
    QTextDocument document(QStringLiteral("owned"));
    QTextDocument foreignDocument(QStringLiteral("foreign"));
    TextFormatOverlayController controller(&document);
    QTextCharFormat overlay;
    overlay.setBackground(Qt::yellow);
    controller.setBlockFormats(QStringLiteral("prose"), foreignDocument.begin(), {makeRange(0, 4, overlay)});
    QVERIFY(controller.formatsForBlock(foreignDocument.begin()).isEmpty());
}

void TextFormatOverlayControllerTest::identicalAndUncachedUpdatesAreNoOps()
{
    QTextDocument document(QStringLiteral("alpha\nbeta"));
    TextFormatOverlayController controller(&document);
    const QTextBlock first = document.begin();
    const QTextBlock second = first.next();
    QTextCharFormat overlay;
    overlay.setBackground(Qt::yellow);
    const auto formats = QList<QTextLayout::FormatRange>{makeRange(0, 5, overlay)};
    controller.setBlockFormats(QStringLiteral("prose"), first, formats);
    QSignalSpy changedSpy(&controller, &TextFormatOverlayController::overlaysChanged);
    controller.setBlockFormats(QStringLiteral("prose"), first, formats);
    controller.clearBlockFormats(QStringLiteral("prose"), second);
    QCOMPARE(changedSpy.count(), 0);
}

void TextFormatOverlayControllerTest::trimsWhitespaceFromVisualRanges()
{
    QTextDocument document(QStringLiteral("  alpha  \n   \nbeta"));
    TextFormatOverlayController controller(&document);
    QTextCharFormat overlay;
    overlay.setBackground(Qt::yellow);

    const QTextBlock first = document.begin();
    const QTextBlock blank = first.next();
    controller.setBlockFormats(QStringLiteral("prose"), first, {makeRange(0, first.text().length(), overlay)});
    controller.setBlockFormats(QStringLiteral("prose"), blank, {makeRange(0, blank.text().length(), overlay)});

    const auto visibleRanges = controller.formatsForBlock(first, QStringLiteral("prose"));
    QCOMPARE(visibleRanges.size(), 1);
    QCOMPARE(visibleRanges.first().start, 2);
    QCOMPARE(visibleRanges.first().length, 5);
    QVERIFY(controller.formatsForBlock(blank, QStringLiteral("prose")).isEmpty());
}

void TextFormatOverlayControllerTest::tooltipListsEveryOverlappingFindingByPriority()
{
    QPlainTextEdit editor;
    editor.setPlainText(QStringLiteral("quietly"));
    editor.resize(320, 120);
    editor.show();
    TextFormatOverlayController controller(editor.document());
    controller.installToolTips(&editor);

    QTextCharFormat lowerPriority;
    lowerPriority.setBackground(Qt::yellow);
    lowerPriority.setToolTip(QStringLiteral("Filter/filler\nFiltering verbs and removable filler words."));
    TextFormatOverlayController::setPriority(lowerPriority, 10);
    QTextCharFormat higherPriority;
    higherPriority.setBackground(Qt::cyan);
    higherPriority.setToolTip(QStringLiteral("Adverbs\nWords that may be functioning as adverbs."));
    TextFormatOverlayController::setPriority(higherPriority, 20);
    controller.setBlockFormats(QStringLiteral("prose"), editor.document()->begin(), {makeRange(0, 7, lowerPriority), makeRange(0, 7, higherPriority)});

    QTextCursor cursor(editor.document());
    cursor.setPosition(3);
    const QPoint localPosition = editor.cursorRect(cursor).center();
    QHelpEvent event(QEvent::ToolTip, localPosition, editor.viewport()->mapToGlobal(localPosition));
    QApplication::sendEvent(editor.viewport(), &event);

    QTRY_COMPARE(QToolTip::text(),
                 QStringLiteral("Adverbs\nWords that may be functioning as adverbs.\n\nFilter/filler\nFiltering verbs and removable filler words."));
    QToolTip::hideText();
}

void TextFormatOverlayControllerTest::overlapRanksPaintTopFillAndOutline()
{
    QTextDocument document(QStringLiteral("alpha"));
    document.setTextWidth(200);
    document.documentLayout()->documentSize();
    TextFormatOverlayController controller(&document);
    const QColor verbColor(255, 0, 0, 90);
    const QColor echoColor(0, 0, 255, 90);
    QTextCharFormat verbFormat;
    verbFormat.setBackground(verbColor);
    TextFormatOverlayController::setPriority(verbFormat, 20);
    QTextCharFormat echoFormat;
    echoFormat.setBackground(echoColor);
    TextFormatOverlayController::setPriority(echoFormat, 10);
    controller.setBlockFormats(QStringLiteral("verbs"), document.begin(), {makeRange(0, 5, verbFormat)});
    controller.setBlockFormats(QStringLiteral("echo"), document.begin(), {makeRange(0, 5, echoFormat)});

    const QImage image = renderBlockPaint(document, controller);

    QVERIFY(imageContainsColor(image, blendedOverWhite(verbColor)));
    QVERIFY(imageContainsColor(image, blendedOverWhite(echoColor)));
    // Stacking the translucent fills (the old renderer) would blend to about
    // RGB(107,107,197); ranked painting must never produce it.
    QVERIFY(!imageContainsColor(image, QColor(107, 107, 197), 6));
}

void TextFormatOverlayControllerTest::threeChannelOverlapCapsAtTwoOutlineRings()
{
    QTextDocument document(QStringLiteral("alpha"));
    document.setTextWidth(200);
    document.documentLayout()->documentSize();
    TextFormatOverlayController controller(&document);
    struct Channel {
        const char *name;
        int priority;
        QColor color;
    };
    const Channel channels[] = {
        {"c0", 1, QColor(255, 0, 255, 90)},
        {"c1", 5, QColor(255, 255, 0, 90)},
        {"c2", 15, QColor(0, 128, 0, 90)},
        {"c3", 25, QColor(255, 0, 0, 90)},
    };
    for (const Channel &channel : channels) {
        QTextCharFormat format;
        format.setBackground(channel.color);
        TextFormatOverlayController::setPriority(format, channel.priority);
        controller.setBlockFormats(QString::fromLatin1(channel.name), document.begin(), {makeRange(0, 5, format)});
    }

    const QImage image = renderBlockPaint(document, controller);

    QVERIFY(imageContainsColor(image, blendedOverWhite(channels[3].color)));
    QVERIFY(imageContainsColor(image, blendedOverWhite(channels[2].color)));
    QVERIFY(imageContainsColor(image, blendedOverWhite(channels[1].color)));
    QVERIFY(!imageContainsColor(image, blendedOverWhite(channels[0].color), 6));
    QCOMPARE(controller.formatsForBlock(document.begin()).size(), 4);
}

void TextFormatOverlayControllerTest::sameChannelOverlapKeepsSingleFill()
{
    QTextDocument document(QStringLiteral("alpha"));
    document.setTextWidth(200);
    document.documentLayout()->documentSize();
    TextFormatOverlayController controller(&document);
    const QColor topColor(255, 255, 0, 90);
    const QColor bottomColor(0, 255, 255, 90);
    QTextCharFormat topFormat;
    topFormat.setBackground(topColor);
    TextFormatOverlayController::setPriority(topFormat, 20);
    QTextCharFormat bottomFormat;
    bottomFormat.setBackground(bottomColor);
    TextFormatOverlayController::setPriority(bottomFormat, 10);
    controller.setBlockFormats(QStringLiteral("prose"), document.begin(), {makeRange(0, 7, topFormat), makeRange(2, 3, bottomFormat)});

    const QImage image = renderBlockPaint(document, controller);

    QVERIFY(imageContainsColor(image, blendedOverWhite(topColor)));
    QVERIFY(!imageContainsColor(image, blendedOverWhite(bottomColor), 6));
}

void TextFormatOverlayControllerTest::hitTestingReturnsHighestRankedOverlap()
{
    QTextDocument document(QStringLiteral("alpha"));
    TextFormatOverlayController controller(&document);
    QTextCharFormat lowerPriority;
    lowerPriority.setBackground(Qt::yellow);
    TextFormatOverlayController::setPriority(lowerPriority, 10);
    QTextCharFormat higherPriority;
    higherPriority.setBackground(Qt::cyan);
    TextFormatOverlayController::setPriority(higherPriority, 20);
    controller.setBlockFormats(QStringLiteral("prose"), document.begin(), {makeRange(0, 5, lowerPriority), makeRange(0, 5, higherPriority)});

    QTextLayout::FormatRange found;
    QVERIFY(controller.findFormatAt(QString(), document.begin(), 3, found));
    QCOMPARE(found.format.background().color(), QColor(Qt::cyan));
    QVERIFY(controller.findFormatAt(QStringLiteral("prose"), document.begin(), 3, found));
    QCOMPARE(found.format.background().color(), QColor(Qt::cyan));
}

void TextFormatOverlayControllerTest::hoverDwellShowsTooltip()
{
    QPlainTextEdit editor;
    editor.setPlainText(QStringLiteral("quietly"));
    editor.resize(320, 120);
    editor.show();
    TextFormatOverlayController controller(editor.document());
    controller.installToolTips(&editor);

    QTextCharFormat format;
    format.setBackground(Qt::yellow);
    format.setToolTip(QStringLiteral("Adverb\nWords that may be functioning as adverbs."));
    TextFormatOverlayController::setPriority(format, 20);
    controller.setBlockFormats(QStringLiteral("prose"), editor.document()->begin(), {makeRange(0, 7, format)});

    QTextCursor cursor(editor.document());
    cursor.setPosition(3);
    const QPoint localPosition = editor.cursorRect(cursor).center();
    QMouseEvent event(QEvent::MouseMove,
                      QPointF(localPosition),
                      QPointF(editor.viewport()->mapToGlobal(localPosition)),
                      Qt::NoButton,
                      Qt::NoButton,
                      Qt::NoModifier);
    QApplication::sendEvent(editor.viewport(), &event);
    QTRY_COMPARE_WITH_TIMEOUT(QToolTip::text(), QStringLiteral("Adverb\nWords that may be functioning as adverbs."), 900);
    QToolTip::hideText();
}

void TextFormatOverlayControllerTest::emptySpaceDoesNotShowNearestHighlightTooltip()
{
    QPlainTextEdit editor;
    editor.setPlainText(QStringLiteral("quietly"));
    editor.resize(320, 120);
    editor.show();
    TextFormatOverlayController controller(editor.document());
    controller.installToolTips(&editor);

    QTextCharFormat format;
    format.setBackground(Qt::yellow);
    format.setToolTip(QStringLiteral("Adverb"));
    controller.setBlockFormats(QStringLiteral("prose"), editor.document()->begin(), {makeRange(0, 7, format)});

    QTextCursor cursor(editor.document());
    cursor.setPosition(3);
    const QPoint wordPosition = editor.cursorRect(cursor).center();
    const QPoint emptyPosition(editor.viewport()->width() - 8, wordPosition.y());
    QHelpEvent event(QEvent::ToolTip, emptyPosition, editor.viewport()->mapToGlobal(emptyPosition));
    QApplication::sendEvent(editor.viewport(), &event);
    QTest::qWait(50);
    QVERIFY(QToolTip::text().isEmpty());
}

void TextFormatOverlayControllerTest::doesNotModifyDocumentState()
{
    QTextDocument document(QStringLiteral("alpha beta"));
    TextFormatOverlayController controller(&document);
    document.clearUndoRedoStacks();
    document.setModified(false);
    QSignalSpy contentsChangeSpy(&document, &QTextDocument::contentsChange);
    QTextCharFormat overlay;
    overlay.setBackground(Qt::yellow);
    controller.setBlockFormats(QStringLiteral("prose"), document.begin(), {makeRange(0, 5, overlay)});
    controller.clearAll();
    QCOMPARE(contentsChangeSpy.count(), 0);
    QVERIFY(!document.isModified());
    QVERIFY(!document.isUndoAvailable());
    QVERIFY(!document.isRedoAvailable());
}

void TextFormatOverlayControllerTest::differentialUpdatesPreserveUnchangedBlocks()
{
    QTextDocument document(QStringLiteral("alpha\nbeta\ngamma"));
    TextFormatOverlayController controller(&document);
    QTextCharFormat yellow;
    yellow.setBackground(Qt::yellow);
    QTextCharFormat cyan;
    cyan.setBackground(Qt::cyan);
    const QTextBlock first = document.begin();
    const QTextBlock second = first.next();
    controller.replaceChannelFormats(QStringLiteral("prose"), {{first.position(), {makeRange(0, 5, yellow)}}, {second.position(), {makeRange(0, 4, yellow)}}});

    QSignalSpy changedSpy(&controller, &TextFormatOverlayController::overlaysChanged);
    controller.updateChannelFormats(QStringLiteral("prose"), {{first.position(), {makeRange(0, 5, cyan)}}});

    QCOMPARE(changedSpy.count(), 1);
    QCOMPARE(controller.formatsForBlock(first, QStringLiteral("prose")).first().format.background().color(), QColor(Qt::cyan));
    QCOMPARE(controller.formatsForBlock(second, QStringLiteral("prose")).first().format.background().color(), QColor(Qt::yellow));

    changedSpy.clear();
    controller.replaceChannelFormats(QStringLiteral("prose"), {{first.position(), {makeRange(0, 5, cyan)}}, {second.position(), {makeRange(0, 4, yellow)}}});
    QCOMPARE(changedSpy.count(), 0);
}

void TextFormatOverlayControllerTest::denseSpansStayWithinBatchBudgetOnLargeDocument()
{
    constexpr int ParagraphCount = 10000;
    constexpr int WordsPerParagraph = 50;
    constexpr int SpansPerParagraph = 20;
    constexpr int BlocksPerBatch = 16;
    QString paragraph;
    for (int index = 0; index < WordsPerParagraph; ++index) {
        paragraph += QStringLiteral("word ");
    }
    QStringList paragraphs(ParagraphCount, paragraph.trimmed());
    QTextDocument document(paragraphs.join(QLatin1Char('\n')));
    QCOMPARE(document.blockCount(), ParagraphCount);
    TextFormatOverlayController controller(&document);
    QTextCharFormat overlay;
    overlay.setBackground(Qt::yellow);
    overlay.setToolTip(QStringLiteral("Dense finding"));

    QVector<qint64> batchNanoseconds;
    QHash<int, QList<QTextLayout::FormatRange>> batch;
    int blockInBatch = 0;
    for (QTextBlock block = document.begin(); block.isValid(); block = block.next()) {
        QList<QTextLayout::FormatRange> ranges;
        ranges.reserve(SpansPerParagraph);
        for (int span = 0; span < SpansPerParagraph; ++span) {
            ranges.append(makeRange(span * 10, 4, overlay));
        }
        batch.insert(block.position(), ranges);
        ++blockInBatch;
        if (blockInBatch == BlocksPerBatch || !block.next().isValid()) {
            QElapsedTimer timer;
            timer.start();
            controller.updateChannelFormats(QStringLiteral("prose"), batch);
            batchNanoseconds.append(timer.nsecsElapsed());
            batch.clear();
            blockInBatch = 0;
        }
    }

    QCOMPARE(controller.cachedBlockCount(), ParagraphCount);
    QCOMPARE(ParagraphCount * SpansPerParagraph, 200000);
    std::sort(batchNanoseconds.begin(), batchNanoseconds.end());
    const qint64 p95 = batchNanoseconds.at((batchNanoseconds.size() - 1) * 95 / 100);
    qInfo().noquote() << QStringLiteral("dense_overlay_500k_words spans=200000 batch_blocks=%1 p95_ms=%2").arg(BlocksPerBatch).arg(p95 / 1000000.0, 0, 'f', 3);
    QVERIFY2(p95 <= 4000000, qPrintable(QStringLiteral("Dense overlay p95 batch time was %1 ms").arg(p95 / 1000000.0, 0, 'f', 3)));
}

void TextFormatOverlayControllerTest::indexedHitTestingScalesWithinDenseBlock()
{
    constexpr int SpanCount = 100000;
    QString text;
    text.reserve(SpanCount * 2);
    for (int index = 0; index < SpanCount; ++index) {
        text += QStringLiteral("x ");
    }
    QTextDocument document(text);
    TextFormatOverlayController controller(&document);
    QTextCharFormat overlay;
    overlay.setBackground(Qt::yellow);
    QList<QTextLayout::FormatRange> ranges;
    ranges.reserve(SpanCount);
    for (int index = 0; index < SpanCount; ++index) {
        ranges.append(makeRange(index * 2, 1, overlay));
    }
    controller.setBlockFormats(QStringLiteral("prose"), document.begin(), ranges);

    QTextLayout::FormatRange found;
    QElapsedTimer timer;
    timer.start();
    for (int index = 0; index < 10000; ++index) {
        QVERIFY(controller.findFormatAt(QStringLiteral("prose"), document.begin(), (index * 7919 % SpanCount) * 2, found));
    }
    const qint64 elapsed = timer.nsecsElapsed();
    qInfo().noquote() << QStringLiteral("indexed_hit_test spans=100000 lookups=10000 total_ms=%1").arg(elapsed / 1000000.0, 0, 'f', 3);
    QVERIFY2(elapsed < 200000000, qPrintable(QStringLiteral("10,000 indexed hit tests took %1 ms").arg(elapsed / 1000000.0, 0, 'f', 3)));
}

QTEST_MAIN(TextFormatOverlayControllerTest)
#include "textformatoverlaycontrollertest.moc"
