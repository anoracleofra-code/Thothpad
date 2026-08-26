/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "textformatoverlaycontroller.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <utility>

#include <QHelpEvent>
#include <QMouseEvent>
#include <QPainter>
#include <QPen>
#include <QPlainTextEdit>
#include <QStringList>
#include <QTextBoundaryFinder>
#include <QTextDocument>
#include <QTextLine>
#include <QToolTip>

#include "textblockdata.h"

namespace ghostwriter
{
TextFormatOverlayController::TextFormatOverlayController(QTextDocument *document, QObject *parent)
    : QObject(parent)
    , m_document(document)
    , m_characterCount(document ? document->characterCount() : 0)
{
    Q_ASSERT(nullptr != document);
    connect(document, &QTextDocument::contentsChange, this, &TextFormatOverlayController::onContentsChange);
    m_toolTipTimer.setSingleShot(true);
    m_toolTipTimer.setInterval(300);
    connect(&m_toolTipTimer, &QTimer::timeout, this, [this]() {
        showToolTipAt(m_toolTipPosition);
    });
}

QString TextFormatOverlayController::spellingChannel()
{
    return QStringLiteral("spelling");
}

void TextFormatOverlayController::setPriority(QTextCharFormat &format, int priority)
{
    format.setProperty(OverlayPriorityProperty, priority);
}

void TextFormatOverlayController::installToolTips(QPlainTextEdit *editor)
{
    if (m_toolTipEditor) {
        m_toolTipEditor->viewport()->removeEventFilter(this);
    }
    m_toolTipEditor = editor;
    if (m_toolTipEditor) {
        m_toolTipEditor->viewport()->setMouseTracking(true);
        m_toolTipEditor->viewport()->installEventFilter(this);
    }
}

void TextFormatOverlayController::setBlockFormats(const QString &channel, const QTextBlock &block, const QList<QTextLayout::FormatRange> &formats)
{
    beginUpdate();
    setBlockFormatsInUpdate(channel, block, formats);
    endUpdate();
}

void TextFormatOverlayController::setBlockFormatsInUpdate(const QString &channel, const QTextBlock &block, const FormatRanges &formats)
{
    if (channel.isEmpty() || !isBlockFromDocument(block)) {
        return;
    }

    FormatRanges validFormats;
    const QString blockText = block.text();
    const int blockLength = blockText.length();
    for (QTextLayout::FormatRange range : formats) {
        if (range.start < 0 || range.length <= 0 || range.start >= blockLength) {
            continue;
        }
        range.length = std::min(range.length, blockLength - range.start);
        while (range.length > 0 && blockText.at(range.start).isSpace()) {
            ++range.start;
            --range.length;
        }
        while (range.length > 0 && blockText.at(range.start + range.length - 1).isSpace()) {
            --range.length;
        }
        if (range.length == 0) {
            continue;
        }
        range.format = overlayFormat(range.format, channel);
        validFormats.append(range);
    }
    IndexedRanges indexed = indexRanges(std::move(validFormats));

    const quint64 identity = indexed.ranges.isEmpty() ? blockIdentity(block) : ensureBlockIdentity(block);
    if (identity == 0) {
        return;
    }

    auto entryIt = m_blocks.find(identity);
    if (entryIt != m_blocks.end()) {
        const auto cached = entryIt->channels.constFind(channel);
        if (cached != entryIt->channels.cend() && formatRangesEqual(cached->ranges, indexed.ranges)) {
            return;
        }
    } else if (indexed.ranges.isEmpty()) {
        return;
    }

    if (indexed.ranges.isEmpty()) {
        entryIt->channels.remove(channel);
        m_channelBlocks[channel].remove(identity);
        if (m_channelBlocks.value(channel).isEmpty()) {
            m_channelBlocks.remove(channel);
        }
        if (entryIt->channels.isEmpty()) {
            m_blocks.erase(entryIt);
        } else {
            rebuildBlockCache(*entryIt);
        }
    } else {
        if (entryIt == m_blocks.end()) {
            BlockEntry entry;
            entry.block = block;
            entryIt = m_blocks.insert(identity, entry);
        }
        entryIt->block = block;
        entryIt->channels.insert(channel, std::move(indexed));
        m_channelBlocks[channel].insert(identity);
        rebuildBlockCache(*entryIt);
    }
    notifyChanged();
}

void TextFormatOverlayController::updateChannelFormats(const QString &channel, const QHash<int, QList<QTextLayout::FormatRange>> &formatsByBlockPosition)
{
    if (channel.isEmpty() || !m_document || formatsByBlockPosition.isEmpty()) {
        return;
    }
    beginUpdate();
    for (auto iterator = formatsByBlockPosition.cbegin(); iterator != formatsByBlockPosition.cend(); ++iterator) {
        setBlockFormatsInUpdate(channel, m_document->findBlock(iterator.key()), iterator.value());
    }
    endUpdate();
}

void TextFormatOverlayController::replaceChannelFormats(const QString &channel, const QHash<int, QList<QTextLayout::FormatRange>> &formatsByBlockPosition)
{
    if (channel.isEmpty() || !m_document) {
        return;
    }
    beginUpdate();
    QSet<quint64> retained;
    for (auto iterator = formatsByBlockPosition.cbegin(); iterator != formatsByBlockPosition.cend(); ++iterator) {
        const QTextBlock block = m_document->findBlock(iterator.key());
        setBlockFormatsInUpdate(channel, block, iterator.value());
        const quint64 identity = blockIdentity(block);
        if (identity != 0 && !iterator.value().isEmpty()) {
            retained.insert(identity);
        }
    }
    const QSet<quint64> stale = m_channelBlocks.value(channel) - retained;
    for (quint64 identity : stale) {
        auto entryIt = m_blocks.find(identity);
        if (entryIt == m_blocks.end()) {
            continue;
        }
        entryIt->channels.remove(channel);
        m_channelBlocks[channel].remove(identity);
        if (entryIt->channels.isEmpty()) {
            m_blocks.erase(entryIt);
        } else {
            rebuildBlockCache(*entryIt);
        }
        notifyChanged();
    }
    if (m_channelBlocks.value(channel).isEmpty()) {
        m_channelBlocks.remove(channel);
    }
    endUpdate();
}

void TextFormatOverlayController::clearBlockFormats(const QString &channel, const QTextBlock &block)
{
    setBlockFormats(channel, block, {});
}

void TextFormatOverlayController::clearChannel(const QString &channel)
{
    const QSet<quint64> identities = m_channelBlocks.take(channel);
    if (identities.isEmpty()) {
        return;
    }
    for (quint64 identity : identities) {
        auto entryIt = m_blocks.find(identity);
        if (entryIt == m_blocks.end()) {
            continue;
        }
        entryIt->channels.remove(channel);
        if (entryIt->channels.isEmpty()) {
            m_blocks.erase(entryIt);
        } else {
            rebuildBlockCache(*entryIt);
        }
    }
    notifyChanged();
}

void TextFormatOverlayController::clearAll()
{
    if (m_blocks.isEmpty()) {
        return;
    }
    m_blocks.clear();
    m_channelBlocks.clear();
    notifyChanged();
}

int TextFormatOverlayController::cachedBlockCount() const
{
    return m_blocks.size();
}

QList<QTextLayout::FormatRange> TextFormatOverlayController::formatsForBlock(const QTextBlock &block, const QString &channel) const
{
    const quint64 identity = blockIdentity(block);
    const auto entry = m_blocks.constFind(identity);
    if (identity == 0 || entry == m_blocks.cend() || !isBlockFromDocument(entry->block)) {
        return {};
    }
    if (!channel.isEmpty()) {
        return entry->channels.value(channel).ranges;
    }
    return entry->hitRanges.ranges;
}

bool TextFormatOverlayController::findFormatAt(const QString &channel, const QTextBlock &block, int positionInBlock, QTextLayout::FormatRange &format) const
{
    const quint64 identity = blockIdentity(block);
    const auto entry = m_blocks.constFind(identity);
    if (identity == 0 || entry == m_blocks.cend() || !isBlockFromDocument(entry->block)) {
        return false;
    }
    if (channel.isEmpty()) {
        return findIndexedFormat(entry->hitRanges, positionInBlock, format);
    }
    const auto indexed = entry->channels.constFind(channel);
    if (indexed == entry->channels.cend()) {
        return false;
    }
    return findIndexedFormat(*indexed, positionInBlock, format);
}

bool TextFormatOverlayController::eventFilter(QObject *watched, QEvent *event)
{
    if (!m_toolTipEditor || watched != m_toolTipEditor->viewport()) {
        return QObject::eventFilter(watched, event);
    }

    if (event->type() == QEvent::MouseMove) {
        const auto *mouseEvent = static_cast<QMouseEvent *>(event);
        m_toolTipPosition = mouseEvent->position().toPoint();
        if (toolTipAt(m_toolTipPosition).isEmpty()) {
            m_toolTipTimer.stop();
            QToolTip::hideText();
        } else {
            m_toolTipTimer.start();
        }
        return QObject::eventFilter(watched, event);
    }
    if (event->type() == QEvent::Leave || event->type() == QEvent::MouseButtonPress || event->type() == QEvent::Wheel) {
        m_toolTipTimer.stop();
        QToolTip::hideText();
        return QObject::eventFilter(watched, event);
    }
    if (event->type() != QEvent::ToolTip) {
        return QObject::eventFilter(watched, event);
    }

    auto *helpEvent = static_cast<QHelpEvent *>(event);
    if (toolTipAt(helpEvent->pos()).isEmpty()) {
        QToolTip::hideText();
        return QObject::eventFilter(watched, event);
    }

    showToolTipAt(helpEvent->pos());
    event->accept();
    return true;
}

QString TextFormatOverlayController::toolTipAt(const QPoint &position) const
{
    if (!m_toolTipEditor) {
        return {};
    }
    const QTextCursor cursor = m_toolTipEditor->cursorForPosition(position);
    const QTextBlock block = cursor.block();
    const int positionInBlock = cursor.position() - block.position();
    const quint64 identity = blockIdentity(block);
    const auto entry = m_blocks.constFind(identity);
    if (identity == 0 || entry == m_blocks.cend()) {
        return {};
    }
    FormatRanges matches;
    collectIndexedFormats(entry->hitRanges, positionInBlock, matches);
    matches.erase(std::remove_if(matches.begin(),
                                 matches.end(),
                                 [](const QTextLayout::FormatRange &range) {
                                     return range.format.toolTip().isEmpty();
                                 }),
                  matches.end());
    if (matches.isEmpty()) {
        return {};
    }
    std::sort(matches.begin(), matches.end(), paintOrderLessThan);
    QStringList tooltips;
    tooltips.reserve(matches.size());
    for (int index = matches.size() - 1; index >= 0; --index) {
        tooltips.append(matches.at(index).format.toolTip());
    }
    return tooltips.join(QStringLiteral("\n\n"));
}

void TextFormatOverlayController::showToolTipAt(const QPoint &position)
{
    if (!m_toolTipEditor) {
        return;
    }
    const QString text = toolTipAt(position);
    if (text.isEmpty()) {
        QToolTip::hideText();
        return;
    }
    QToolTip::showText(m_toolTipEditor->viewport()->mapToGlobal(position), text, m_toolTipEditor->viewport());
}

void TextFormatOverlayController::paintBlock(QPainter &painter, const QTextBlock &block, const QRectF &blockRect) const
{
    QTextLayout *layout = block.layout();
    if (!layout) {
        return;
    }
    const quint64 identity = blockIdentity(block);
    const auto entry = m_blocks.constFind(identity);
    if (identity == 0 || entry == m_blocks.cend()) {
        return;
    }
    const FormatRanges &formats = entry->paintRuns;
    const QString text = block.text();
    const auto visualRects = [&text, &blockRect, &entry](const QTextLine &line, int logicalStart, int logicalEnd) {
        QList<QRectF> rects;
        if (!entry->needsGraphemeGeometry) {
            const qreal x1 = line.cursorToX(logicalStart);
            const qreal x2 = line.cursorToX(logicalEnd);
            rects.append(QRectF(blockRect.left() + std::min(x1, x2), blockRect.top() + line.y(), std::abs(x2 - x1), line.height()));
            return rects;
        }
        QTextBoundaryFinder finder(QTextBoundaryFinder::Grapheme, text);
        finder.setPosition(logicalStart);
        int segmentStart = finder.isAtBoundary() ? logicalStart : finder.toPreviousBoundary();
        while (segmentStart >= 0 && segmentStart < logicalEnd) {
            finder.setPosition(segmentStart);
            const int segmentEnd = finder.toNextBoundary();
            if (segmentEnd <= segmentStart) {
                break;
            }
            const qreal x1 = line.cursorToX(segmentStart);
            const qreal x2 = line.cursorToX(std::min(segmentEnd, logicalEnd));
            rects.append(QRectF(blockRect.left() + std::min(x1, x2), blockRect.top() + line.y(), std::abs(x2 - x1), line.height()));
            segmentStart = segmentEnd;
        }
        std::sort(rects.begin(), rects.end(), [](const QRectF &left, const QRectF &right) {
            return left.left() < right.left();
        });
        QList<QRectF> merged;
        for (const QRectF &rect : std::as_const(rects)) {
            if (!merged.isEmpty() && rect.left() <= merged.last().right() + 0.5) {
                merged.last() = merged.last().united(rect);
            } else {
                merged.append(rect);
            }
        }
        return merged;
    };

    // Background pass: overlapping findings are ranked instead of
    // alpha-blended. The highest-ranked finding fills exactly as before;
    // lower-ranked findings from different channels render as 1px outline
    // rings around the filled box, one ring per rank outward, capped at two
    // rings. Same-channel overlaps keep only the top fill.
    FormatRanges backgroundRuns;
    for (const QTextLayout::FormatRange &range : formats) {
        if (range.format.hasProperty(QTextFormat::BackgroundBrush)) {
            backgroundRuns.append(range);
        }
    }
    if (!backgroundRuns.isEmpty()) {
        QVector<int> boundaries;
        boundaries.reserve(backgroundRuns.size() * 2);
        for (const QTextLayout::FormatRange &range : std::as_const(backgroundRuns)) {
            boundaries.append(range.start);
            boundaries.append(range.start + range.length);
        }
        std::sort(boundaries.begin(), boundaries.end());
        boundaries.erase(std::unique(boundaries.begin(), boundaries.end()), boundaries.end());

        constexpr int MaxOutlineRings = 2;
        FormatRanges ranked;
        for (int boundaryIndex = 0; boundaryIndex + 1 < boundaries.size(); ++boundaryIndex) {
            const int segmentStart = boundaries.at(boundaryIndex);
            const int segmentEnd = boundaries.at(boundaryIndex + 1);
            // Segment edges are the background runs' own boundaries, so any
            // background range meeting this segment contains segmentStart.
            collectIndexedFormats(entry->hitRanges, segmentStart, ranked);
            ranked.erase(std::remove_if(ranked.begin(),
                                        ranked.end(),
                                        [](const QTextLayout::FormatRange &range) {
                                            return !range.format.hasProperty(QTextFormat::BackgroundBrush);
                                        }),
                         ranked.end());
            if (ranked.isEmpty()) {
                continue;
            }
            std::sort(ranked.begin(), ranked.end(), paintOrderLessThan);
            const QTextLayout::FormatRange &topRange = ranked.constLast();
            const QString topChannel = topRange.format.stringProperty(OverlayChannelProperty);
            for (int lineIndex = 0; lineIndex < layout->lineCount(); ++lineIndex) {
                const QTextLine line = layout->lineAt(lineIndex);
                const int start = std::max(segmentStart, line.textStart());
                const int end = std::min(segmentEnd, line.textStart() + line.textLength());
                if (end <= start) {
                    continue;
                }
                for (const QRectF &rect : visualRects(line, start, end)) {
                    painter.fillRect(rect, topRange.format.background());
                    for (int rank = 1; rank < ranked.size() && rank <= MaxOutlineRings; ++rank) {
                        const QTextLayout::FormatRange &underlying = ranked.at(ranked.size() - 1 - rank);
                        if (underlying.format.stringProperty(OverlayChannelProperty) == topChannel) {
                            continue;
                        }
                        const qreal outset = 0.5 + static_cast<qreal>(rank - 1);
                        QPen pen(overlayOutlineColor(underlying.format));
                        pen.setWidthF(1.0);
                        painter.save();
                        painter.setPen(pen);
                        painter.drawRect(rect.adjusted(-outset, -outset, outset, outset));
                        painter.restore();
                    }
                }
            }
        }
    }

    // Underline decorations are unchanged.
    for (const QTextLayout::FormatRange &range : formats) {
        if (range.format.underlineStyle() == QTextCharFormat::NoUnderline) {
            continue;
        }
        const int rangeEnd = range.start + range.length;
        for (int lineIndex = 0; lineIndex < layout->lineCount(); ++lineIndex) {
            const QTextLine line = layout->lineAt(lineIndex);
            const int start = std::max(range.start, line.textStart());
            const int end = std::min(rangeEnd, line.textStart() + line.textLength());
            if (end <= start) {
                continue;
            }
            for (const QRectF &rect : visualRects(line, start, end)) {
                QPen pen(range.format.underlineColor());
                pen.setWidthF(1.0);
                painter.save();
                painter.setPen(pen);
                painter.drawLine(rect.bottomLeft() - QPointF(0, 1), rect.bottomRight() - QPointF(0, 1));
                painter.restore();
            }
        }
    }
}

void TextFormatOverlayController::resetForDocumentReplacement()
{
    invalidateCache();
}

bool TextFormatOverlayController::isOwnedFormat(const QTextCharFormat &format, const QString &channel)
{
    if (!format.property(OverlayOwnerProperty).toBool()) {
        return false;
    }
    return channel.isEmpty() || format.property(OverlayChannelProperty).toString() == channel;
}

void TextFormatOverlayController::scheduleBlockReapply(const QTextBlock &block)
{
    Q_UNUSED(block)
}

void TextFormatOverlayController::onContentsChange(int position, int charsRemoved, int charsAdded)
{
    const int previousCharacterCount = m_characterCount;
    m_characterCount = m_document ? m_document->characterCount() : 0;
    if (position == 0 && previousCharacterCount > 1 && charsRemoved >= previousCharacterCount - 1) {
        invalidateCache();
        return;
    }
    if (!m_document || m_blocks.isEmpty()) {
        return;
    }

    int endPosition = position + std::max(1, charsAdded);
    QTextBlock block = m_document->findBlock(position);
    const QTextBlock endBlock = m_document->findBlock(endPosition);
    while (block.isValid()) {
        const QTextBlock next = block.next();
        const quint64 identity = blockIdentity(block);
        if (identity != 0) {
            removeBlockFromCache(identity);
        }
        if (!endBlock.isValid() || block == endBlock) {
            break;
        }
        block = next;
    }
    notifyChanged();
}

QTextCharFormat TextFormatOverlayController::overlayFormat(const QTextCharFormat &source, const QString &channel)
{
    QTextCharFormat result;
    if (source.hasProperty(QTextFormat::BackgroundBrush)) {
        result.setBackground(source.background());
    }
    if (source.hasProperty(QTextFormat::TextUnderlineColor)) {
        result.setUnderlineColor(source.underlineColor());
    }
    if (source.hasProperty(QTextFormat::TextUnderlineStyle)) {
        result.setUnderlineStyle(source.underlineStyle());
    }
    if (source.hasProperty(QTextFormat::TextToolTip)) {
        result.setToolTip(source.toolTip());
    }
    if (source.hasProperty(OverlayPriorityProperty)) {
        result.setProperty(OverlayPriorityProperty, source.property(OverlayPriorityProperty));
    }
    result.setProperty(OverlayOwnerProperty, true);
    result.setProperty(OverlayChannelProperty, channel);
    return result;
}

bool TextFormatOverlayController::formatRangesEqual(const FormatRanges &left, const FormatRanges &right)
{
    if (left.size() != right.size()) {
        return false;
    }
    for (qsizetype index = 0; index < left.size(); ++index) {
        if (left.at(index).start != right.at(index).start || left.at(index).length != right.at(index).length
            || left.at(index).format != right.at(index).format) {
            return false;
        }
    }
    return true;
}

TextFormatOverlayController::IndexedRanges TextFormatOverlayController::indexRanges(FormatRanges ranges)
{
    std::stable_sort(ranges.begin(), ranges.end(), [](const QTextLayout::FormatRange &left, const QTextLayout::FormatRange &right) {
        if (left.start != right.start) {
            return left.start < right.start;
        }
        if (left.length != right.length) {
            return left.length < right.length;
        }
        const int leftPriority = left.format.intProperty(OverlayPriorityProperty);
        const int rightPriority = right.format.intProperty(OverlayPriorityProperty);
        if (leftPriority != rightPriority) {
            return leftPriority < rightPriority;
        }
        return left.format.stringProperty(OverlayChannelProperty) < right.format.stringProperty(OverlayChannelProperty);
    });

    FormatRanges merged;
    merged.reserve(ranges.size());
    for (const QTextLayout::FormatRange &range : std::as_const(ranges)) {
        if (!merged.isEmpty() && formatsCanMerge(merged.last(), range)) {
            const int end = std::max(merged.last().start + merged.last().length, range.start + range.length);
            merged.last().length = end - merged.last().start;
        } else {
            merged.append(range);
        }
    }

    IndexedRanges result;
    result.ranges = std::move(merged);
    result.prefixMaximumEnds.reserve(result.ranges.size());
    int maximumEnd = 0;
    for (const QTextLayout::FormatRange &range : std::as_const(result.ranges)) {
        maximumEnd = std::max(maximumEnd, range.start + range.length);
        result.prefixMaximumEnds.append(maximumEnd);
    }
    return result;
}

bool TextFormatOverlayController::findIndexedFormat(const IndexedRanges &ranges, int positionInBlock, QTextLayout::FormatRange &format, bool requireToolTip)
{
    const auto firstAfter =
        std::upper_bound(ranges.ranges.cbegin(), ranges.ranges.cend(), positionInBlock, [](int position, const QTextLayout::FormatRange &candidate) {
            return position < candidate.start;
        });
    int index = static_cast<int>(std::distance(ranges.ranges.cbegin(), firstAfter)) - 1;
    bool found = false;
    int highestPriority = std::numeric_limits<int>::min();
    for (; index >= 0 && ranges.prefixMaximumEnds.at(index) > positionInBlock; --index) {
        const QTextLayout::FormatRange &candidate = ranges.ranges.at(index);
        if (positionInBlock < candidate.start || positionInBlock >= candidate.start + candidate.length
            || (requireToolTip && candidate.format.toolTip().isEmpty())) {
            continue;
        }
        const int priority = candidate.format.intProperty(OverlayPriorityProperty);
        if (!found || priority > highestPriority) {
            format = candidate;
            highestPriority = priority;
            found = true;
        }
    }
    return found;
}

void TextFormatOverlayController::collectIndexedFormats(const IndexedRanges &ranges, int positionInBlock, FormatRanges &results)
{
    results.clear();
    const auto firstAfter =
        std::upper_bound(ranges.ranges.cbegin(), ranges.ranges.cend(), positionInBlock, [](int position, const QTextLayout::FormatRange &candidate) {
            return position < candidate.start;
        });
    int index = static_cast<int>(std::distance(ranges.ranges.cbegin(), firstAfter)) - 1;
    for (; index >= 0 && ranges.prefixMaximumEnds.at(index) > positionInBlock; --index) {
        const QTextLayout::FormatRange &candidate = ranges.ranges.at(index);
        if (positionInBlock < candidate.start || positionInBlock >= candidate.start + candidate.length) {
            continue;
        }
        results.append(candidate);
    }
}

bool TextFormatOverlayController::paintOrderLessThan(const QTextLayout::FormatRange &left, const QTextLayout::FormatRange &right)
{
    const int leftPriority = left.format.intProperty(OverlayPriorityProperty);
    const int rightPriority = right.format.intProperty(OverlayPriorityProperty);
    if (leftPriority != rightPriority) {
        return leftPriority < rightPriority;
    }
    const QString leftChannel = left.format.stringProperty(OverlayChannelProperty);
    const QString rightChannel = right.format.stringProperty(OverlayChannelProperty);
    if (leftChannel != rightChannel) {
        return leftChannel < rightChannel;
    }
    if (left.start != right.start) {
        return left.start < right.start;
    }
    if (left.length != right.length) {
        return left.length < right.length;
    }
    return left.format.background().color().rgba() < right.format.background().color().rgba();
}

QColor TextFormatOverlayController::overlayOutlineColor(const QTextCharFormat &format)
{
    if (format.hasProperty(QTextFormat::BackgroundBrush)) {
        const QColor color = format.background().color();
        if (color.isValid()) {
            return color;
        }
    }
    return format.underlineColor();
}

bool TextFormatOverlayController::formatsCanMerge(const QTextLayout::FormatRange &left, const QTextLayout::FormatRange &right)
{
    return left.format == right.format && right.start <= left.start + left.length;
}

bool TextFormatOverlayController::textNeedsGraphemeGeometry(const QString &text)
{
    for (const QChar character : text) {
        switch (character.direction()) {
        case QChar::DirR:
        case QChar::DirAL:
        case QChar::DirRLE:
        case QChar::DirRLO:
        case QChar::DirRLI:
            return true;
        default:
            break;
        }
        switch (character.category()) {
        case QChar::Mark_NonSpacing:
        case QChar::Mark_SpacingCombining:
        case QChar::Mark_Enclosing:
            return true;
        default:
            break;
        }
    }
    return false;
}

void TextFormatOverlayController::rebuildBlockCache(BlockEntry &entry)
{
    FormatRanges allRanges;
    for (const IndexedRanges &channel : std::as_const(entry.channels)) {
        allRanges.append(channel.ranges);
    }
    entry.hitRanges = indexRanges(allRanges);

    std::stable_sort(allRanges.begin(), allRanges.end(), paintOrderLessThan);
    entry.paintRuns.clear();
    entry.paintRuns.reserve(allRanges.size());
    for (const QTextLayout::FormatRange &range : std::as_const(allRanges)) {
        if (!entry.paintRuns.isEmpty() && formatsCanMerge(entry.paintRuns.last(), range)) {
            const int end = std::max(entry.paintRuns.last().start + entry.paintRuns.last().length, range.start + range.length);
            entry.paintRuns.last().length = end - entry.paintRuns.last().start;
        } else {
            entry.paintRuns.append(range);
        }
    }
    entry.needsGraphemeGeometry = textNeedsGraphemeGeometry(entry.block.text());
}

bool TextFormatOverlayController::isBlockFromDocument(const QTextBlock &block) const
{
    return m_document && block.isValid() && block.document() == m_document.data();
}

quint64 TextFormatOverlayController::blockIdentity(const QTextBlock &block) const
{
    if (!isBlockFromDocument(block)) {
        return 0;
    }
    auto *data = dynamic_cast<TextBlockData *>(block.userData());
    return data ? data->overlayIdentity : 0;
}

quint64 TextFormatOverlayController::ensureBlockIdentity(const QTextBlock &block)
{
    if (!isBlockFromDocument(block)) {
        return 0;
    }
    auto *data = dynamic_cast<TextBlockData *>(block.userData());
    if (!data) {
        data = new TextBlockData();
        QTextBlock mutableBlock = block;
        mutableBlock.setUserData(data);
    }
    QPointer<TextFormatOverlayController> guard(this);
    data->overlayCleanup = [guard](quint64 identity) {
        if (guard) {
            guard->removeBlockFromCache(identity);
        }
    };
    return data->overlayIdentity;
}

bool TextFormatOverlayController::removeBlockFromCache(quint64 identity)
{
    auto entry = m_blocks.find(identity);
    if (entry == m_blocks.end()) {
        return false;
    }
    for (auto channel = entry->channels.cbegin(); channel != entry->channels.cend(); ++channel) {
        m_channelBlocks[channel.key()].remove(identity);
        if (m_channelBlocks.value(channel.key()).isEmpty()) {
            m_channelBlocks.remove(channel.key());
        }
    }
    m_blocks.erase(entry);
    return true;
}

void TextFormatOverlayController::invalidateCache()
{
    m_blocks.clear();
    m_channelBlocks.clear();
    notifyChanged();
}

void TextFormatOverlayController::beginUpdate()
{
    ++m_updateDepth;
}

void TextFormatOverlayController::endUpdate()
{
    Q_ASSERT(m_updateDepth > 0);
    --m_updateDepth;
    if (m_updateDepth == 0 && m_updateChanged) {
        m_updateChanged = false;
        emit overlaysChanged();
    }
}

void TextFormatOverlayController::notifyChanged()
{
    if (m_updateDepth > 0) {
        m_updateChanged = true;
    } else {
        emit overlaysChanged();
    }
}
} // namespace ghostwriter
