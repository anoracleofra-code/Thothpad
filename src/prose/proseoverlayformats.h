/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef PROSE_OVERLAY_FORMATS_H
#define PROSE_OVERLAY_FORMATS_H

#include <QHash>
#include <QList>
#include <QSet>
#include <QTextCharFormat>
#include <QTextLayout>

#include <algorithm>

namespace ghostwriter
{

// Source-cache metadata: category identity must not depend on a customizable
// color or translated tooltip. The renderer strips this private property.
constexpr int ProseOverlayCategoryProperty = QTextFormat::UserProperty + 0x5753;

inline void updateAppliedOverlayFormats(QHash<int, QList<QTextLayout::FormatRange>> &applied,
                                       const QHash<int, QList<QTextLayout::FormatRange>> &updates)
{
    for (auto it = updates.constBegin(); it != updates.constEnd(); ++it) {
        if (it.value().isEmpty()) {
            applied.remove(it.key());
        } else {
            applied.insert(it.key(), it.value());
        }
    }
}

// Return only changed blocks, including empty lists to remove their overlays.
// This runs synchronously before starting an asynchronous snapshot refresh.
inline QHash<int, QList<QTextLayout::FormatRange>>
overlayUpdatesForVisibleCategories(const QHash<int, QList<QTextLayout::FormatRange>> &applied, const QSet<QString> &visible)
{
    QHash<int, QList<QTextLayout::FormatRange>> updates;
    for (auto it = applied.constBegin(); it != applied.constEnd(); ++it) {
        auto ranges = it.value();
        ranges.removeIf([&visible](const QTextLayout::FormatRange &range) {
            return !visible.contains(range.format.property(ProseOverlayCategoryProperty).toString());
        });
        if (ranges.size() != it.value().size()) {
            updates.insert(it.key(), ranges);
        }
    }
    return updates;
}

// Compares two per-block format lists independent of the order in which spans
// landed, so a rehydrated snapshot with identical content compares equal even
// when hydration processed spans in a different sequence.
inline bool overlayFormatListsEqual(QList<QTextLayout::FormatRange> left, QList<QTextLayout::FormatRange> right)
{
    const auto orderedByPosition = [](const QTextLayout::FormatRange &rangeA, const QTextLayout::FormatRange &rangeB) {
        return rangeA.start != rangeB.start ? rangeA.start < rangeB.start : rangeA.length < rangeB.length;
    };
    std::sort(left.begin(), left.end(), orderedByPosition);
    std::sort(right.begin(), right.end(), orderedByPosition);
    return left == right;
}

// Rebases cached per-block overlay formats across an edit, mirroring
// adjustedDiagnosticsAfterEdit: ranges entirely before the edit survive
// verbatim, ranges entirely after it shift by the length delta, and ranges
// overlapping the edited region are dropped. Blocks past the edit shift keys
// by the same delta because their absolute positions moved.
inline QHash<int, QList<QTextLayout::FormatRange>>
adjustedOverlayFormatsAfterEdit(const QHash<int, QList<QTextLayout::FormatRange>> &cachedFormats, int position, int removed, int added)
{
    const int changeEnd = position + removed;
    const int delta = added - removed;
    QHash<int, QList<QTextLayout::FormatRange>> result;
    result.reserve(cachedFormats.size());
    for (auto iterator = cachedFormats.constBegin(); iterator != cachedFormats.constEnd(); ++iterator) {
        const int blockPosition = iterator.key();
        const bool blockShifted = blockPosition >= changeEnd;
        QList<QTextLayout::FormatRange> adjustedRanges;
        for (const QTextLayout::FormatRange &range : iterator.value()) {
            const int absoluteStart = blockPosition + range.start;
            const int absoluteEnd = absoluteStart + range.length;
            if (absoluteEnd <= position) {
                adjustedRanges.append(range);
            } else if (absoluteStart >= changeEnd) {
                QTextLayout::FormatRange shifted = range;
                if (!blockShifted) {
                    // Same block: text before the range changed length, so
                    // only its block-relative offset moves. A block wholly
                    // past the edit keeps its internal layout untouched.
                    shifted.start += delta;
                }
                adjustedRanges.append(shifted);
            }
        }
        if (!adjustedRanges.isEmpty()) {
            result.insert(blockPosition + (blockShifted ? delta : 0), adjustedRanges);
        }
    }
    return result;
}
}

#endif
