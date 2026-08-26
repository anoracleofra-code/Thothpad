/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <algorithm>

#include <QColor>
#include <QTest>
#include <QTextBlock>
#include <QTextCharFormat>
#include <QTextCursor>
#include <QTextDocument>
#include <QTextLayout>

#include "../../src/editor/markdowneditor.h"
#include "../../src/editor/textformatoverlaycontroller.h"
#include "../../src/prose/proseoverlayformats.h"

using namespace ghostwriter;

namespace
{
const QString ProseChannel = QStringLiteral("prose");

QTextLayout::FormatRange makeRange(int start, int length, const QString &colorName)
{
    QTextLayout::FormatRange range;
    range.start = start;
    range.length = length;
    QTextCharFormat format;
    format.setBackground(QColor(colorName));
    range.format = format;
    return range;
}

QList<QTextLayout::FormatRange> singleRange(int start, int length, const QString &colorName)
{
    return {makeRange(start, length, colorName)};
}

QString paragraphOfLength(int targetLength)
{
    QString paragraph;
    while (paragraph.size() < targetLength) {
        paragraph += QStringLiteral("word ");
    }
    return paragraph.left(targetLength - 1);
}

QString blockColor(int blockNumber)
{
    static const QStringList palette = {QStringLiteral("#f87171"),
                                        QStringLiteral("#facc15"),
                                        QStringLiteral("#60a5fa"),
                                        QStringLiteral("#34d399"),
                                        QStringLiteral("#c084fc"),
                                        QStringLiteral("#fb923c")};
    return palette.at(blockNumber % palette.size());
}
}

class ProseOverlayDeltaTest : public QObject
{
    Q_OBJECT

private slots:
    void adjustedFormatsMirrorDiagnosticAdjustmentOnInsert();
    void adjustedFormatsShiftAndDropAcrossMultiBlockDeletion();
    void identicalIncomingSpansLeaveBlocksUntouched();
    void refreshKeepsDistantBlocksPaintedWhileEditedBlockRecovers();
};

void ProseOverlayDeltaTest::adjustedFormatsMirrorDiagnosticAdjustmentOnInsert()
{
    QHash<int, QList<QTextLayout::FormatRange>> cached;
    cached.insert(0,
                  QList<QTextLayout::FormatRange>{makeRange(0, 4, QStringLiteral("#ff0000")),
                                                  makeRange(5, 3, QStringLiteral("#00ff00")),
                                                  makeRange(9, 3, QStringLiteral("#0000ff"))});
    cached.insert(20, singleRange(2, 5, QStringLiteral("#ff0000")));
    cached.insert(40, singleRange(1, 6, QStringLiteral("#ff0000")));

    // Insert three characters inside the first block. Ranges overlapping the
    // edit drop, later ranges in the same block shift by the delta, and blocks
    // past the edit move keys without touching their internal layout.
    const QHash<int, QList<QTextLayout::FormatRange>> result = adjustedOverlayFormatsAfterEdit(cached, 7, 0, 3);

    QCOMPARE(result.size(), 3);
    const QList<QTextLayout::FormatRange> firstBlock = result.value(0);
    QCOMPARE(firstBlock.size(), 2);
    QVERIFY(firstBlock.contains(makeRange(0, 4, QStringLiteral("#ff0000"))));
    QVERIFY(firstBlock.contains(makeRange(12, 3, QStringLiteral("#0000ff"))));
    QCOMPARE(result.value(23), singleRange(2, 5, QStringLiteral("#ff0000")));
    QCOMPARE(result.value(43), singleRange(1, 6, QStringLiteral("#ff0000")));
}

void ProseOverlayDeltaTest::adjustedFormatsShiftAndDropAcrossMultiBlockDeletion()
{
    QHash<int, QList<QTextLayout::FormatRange>> cached;
    cached.insert(0, singleRange(2, 4, QStringLiteral("#ff0000")));
    cached.insert(20, singleRange(2, 5, QStringLiteral("#00ff00")));
    cached.insert(40, singleRange(1, 6, QStringLiteral("#0000ff")));

    // Deleting [22, 30) erases every range of the second block; the third
    // block shifts keys while keeping its internal offsets intact.
    const QHash<int, QList<QTextLayout::FormatRange>> result = adjustedOverlayFormatsAfterEdit(cached, 22, 8, 0);

    QCOMPARE(result.size(), 2);
    QVERIFY(result.contains(0));
    QCOMPARE(result.value(0), singleRange(2, 4, QStringLiteral("#ff0000")));
    QVERIFY(!result.contains(20));
    QVERIFY(result.contains(32));
    QCOMPARE(result.value(32), singleRange(1, 6, QStringLiteral("#0000ff")));
}

void ProseOverlayDeltaTest::identicalIncomingSpansLeaveBlocksUntouched()
{
    QHash<int, QList<QTextLayout::FormatRange>> applied;
    applied.insert(10, QList<QTextLayout::FormatRange>{makeRange(1, 4, QStringLiteral("#ff0000")), makeRange(7, 2, QStringLiteral("#00ff00"))});
    applied.insert(30, singleRange(0, 9, QStringLiteral("#0000ff")));

    // A snapshot that lands the same spans in a different order must still
    // compare equal so preserved blocks are never repainted.
    QHash<int, QList<QTextLayout::FormatRange>> incoming;
    incoming.insert(10, QList<QTextLayout::FormatRange>{makeRange(7, 2, QStringLiteral("#00ff00")), makeRange(1, 4, QStringLiteral("#ff0000"))});
    incoming.insert(30, singleRange(0, 9, QStringLiteral("#0000ff")));

    int preservedBlocks = 0;
    QVector<int> updatedPositions;
    for (auto iterator = applied.constBegin(); iterator != applied.constEnd(); ++iterator) {
        const auto fresh = incoming.constFind(iterator.key());
        if (fresh != incoming.constEnd() && overlayFormatListsEqual(iterator.value(), fresh.value())) {
            ++preservedBlocks;
        } else {
            updatedPositions.append(iterator.key());
        }
    }
    QCOMPARE(preservedBlocks, 2);
    QVERIFY(updatedPositions.isEmpty());

    // A changed span-set flags exactly its own block.
    incoming[30] = singleRange(0, 5, QStringLiteral("#0000ff"));
    for (auto iterator = applied.constBegin(); iterator != applied.constEnd(); ++iterator) {
        const auto fresh = incoming.constFind(iterator.key());
        if (!(fresh != incoming.constEnd() && overlayFormatListsEqual(iterator.value(), fresh.value()))) {
            updatedPositions.append(iterator.key());
        }
    }
    QCOMPARE(updatedPositions, QVector<int>{30});
}

void ProseOverlayDeltaTest::refreshKeepsDistantBlocksPaintedWhileEditedBlockRecovers()
{
    MarkdownDocument document;
    MarkdownEditor editor(&document, ColorScheme{});
    QStringList paragraphs;
    for (int index = 0; index < 24; ++index) {
        paragraphs.append(paragraphOfLength(60));
    }
    editor.setPlainText(paragraphs.join(QStringLiteral("\n")));

    TextFormatOverlayController *overlays = editor.textFormatOverlayController();

    // Simulate the applied state after a completed overlay hydration: one
    // highlighted word per block, each with its own color. Block handles are
    // captured alongside so assertions survive position shifts.
    QList<QPair<QTextBlock, QList<QTextLayout::FormatRange>>> applied;
    for (QTextBlock block = document.firstBlock(); block.isValid(); block = block.next()) {
        if (block.length() > 12) {
            applied.append({block, singleRange(2, 4, blockColor(block.blockNumber()))});
        }
    }
    QCOMPARE(applied.size(), 24);
    QHash<int, QList<QTextLayout::FormatRange>> appliedByPosition;
    QList<QPair<QTextBlock, QList<QTextLayout::FormatRange>>> painted;
    for (const auto &entry : applied) {
        appliedByPosition.insert(entry.first.position(), entry.second);
    }
    overlays->updateChannelFormats(ProseChannel, appliedByPosition);
    for (const auto &entry : applied) {
        const QList<QTextLayout::FormatRange> stored = overlays->formatsForBlock(entry.first, ProseChannel);
        QCOMPARE(stored.size(), 1);
        painted.append({entry.first, stored});
    }

    // Type inside the fourth block: the contentsChange eviction removes that
    // block's cached channel entry only.
    const QTextBlock editedBlock = applied.at(3).first;
    const int editedOriginalPosition = editedBlock.position();
    const int editPosition = editedOriginalPosition + 20;
    QTextCursor cursor(&document);
    cursor.setPosition(editPosition);
    cursor.insertText(QStringLiteral("xx"));

    // Blocks outside the edited region keep their exact painted formats: the
    // refresh path never tears down the prose channel wholesale.
    QVERIFY(overlays->formatsForBlock(editedBlock, ProseChannel).isEmpty());
    for (int index = 0; index < painted.size(); ++index) {
        if (index == 3) {
            continue;
        }
        QVERIFY(painted.at(index).first.isValid());
        QCOMPARE(overlays->formatsForBlock(painted.at(index).first, ProseChannel), painted.at(index).second);
    }

    // Delta rehydration: rebase the cached snapshot across the edit, then push
    // every evicted block that still owns survivor formats back to the
    // controller so highlights reappear immediately instead of blinking until
    // the next snapshot.
    const QHash<int, QList<QTextLayout::FormatRange>> rebased = adjustedOverlayFormatsAfterEdit(appliedByPosition, editPosition, 0, 2);
    const QTextBlock endEvicted = document.findBlock(editPosition + qMax(1, 2));
    QSet<int> evictedPositions;
    for (QTextBlock block = document.findBlock(editPosition); block.isValid(); block = block.next()) {
        evictedPositions.insert(block.position());
        if (!endEvicted.isValid() || block == endEvicted) {
            break;
        }
    }
    QCOMPARE(evictedPositions, QSet<int>{editedOriginalPosition});
    QHash<int, QList<QTextLayout::FormatRange>> reapplied;
    for (const int blockPosition : std::as_const(evictedPositions)) {
        const auto survivors = rebased.constFind(blockPosition);
        if (survivors != rebased.constEnd()) {
            reapplied.insert(blockPosition, survivors.value());
        }
    }
    QCOMPARE(reapplied.size(), 1);
    overlays->updateChannelFormats(ProseChannel, reapplied);

    const QList<QTextLayout::FormatRange> recovered = overlays->formatsForBlock(editedBlock, ProseChannel);
    QCOMPARE(recovered.size(), 1);
    QCOMPARE(recovered.first().start, 2);
    QCOMPARE(recovered.first().length, 4);
    QVERIFY(overlayFormatListsEqual(recovered, painted.at(3).second));

    // And the untouched blocks still hold their pre-refresh formats.
    for (int index = 0; index < painted.size(); ++index) {
        if (index == 3) {
            continue;
        }
        QCOMPARE(overlays->formatsForBlock(painted.at(index).first, ProseChannel), painted.at(index).second);
    }
}

QTEST_MAIN(ProseOverlayDeltaTest)
#include "proseoverlaydeltatest.moc"
