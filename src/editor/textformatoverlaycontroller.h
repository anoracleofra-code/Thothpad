/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef TEXT_FORMAT_OVERLAY_CONTROLLER_H
#define TEXT_FORMAT_OVERLAY_CONTROLLER_H

#include <QColor>
#include <QHash>
#include <QList>
#include <QObject>
#include <QPoint>
#include <QPointer>
#include <QSet>
#include <QString>
#include <QTextBlock>
#include <QTextFormat>
#include <QTextLayout>
#include <QTimer>
#include <QVector>

class QTextDocument;
class QPainter;
class QPlainTextEdit;

namespace ghostwriter
{
/**
 * Stores transient, named formatting channels for the editor paint pass.
 *
 * Background, underline, priority, and tooltip properties are accepted from
 * callers. The controller never changes the QTextDocument's contents,
 * character formats, syntax formats, modified state, or undo history.
 */
class TextFormatOverlayController : public QObject
{
    Q_OBJECT

public:
    explicit TextFormatOverlayController(QTextDocument *document, QObject *parent = nullptr);

    static QString spellingChannel();
    static void setPriority(QTextCharFormat &format, int priority);
    void installToolTips(QPlainTextEdit *editor);

    void setBlockFormats(const QString &channel, const QTextBlock &block, const QList<QTextLayout::FormatRange> &formats);
    void updateChannelFormats(const QString &channel, const QHash<int, QList<QTextLayout::FormatRange>> &formatsByBlockPosition);
    void replaceChannelFormats(const QString &channel, const QHash<int, QList<QTextLayout::FormatRange>> &formatsByBlockPosition);
    void clearBlockFormats(const QString &channel, const QTextBlock &block);
    void clearChannel(const QString &channel);
    void clearAll();
    int cachedBlockCount() const;

    QList<QTextLayout::FormatRange> formatsForBlock(const QTextBlock &block, const QString &channel = QString()) const;
    bool findFormatAt(const QString &channel, const QTextBlock &block, int positionInBlock, QTextLayout::FormatRange &format) const;
    void paintBlock(QPainter &painter, const QTextBlock &block, const QRectF &blockRect) const;

    /**
     * Invalidates cached and queued overlays before replacing all document
     * text. No current layout is repainted because replacement is imminent.
     */
    void resetForDocumentReplacement();

    static bool isOwnedFormat(const QTextCharFormat &format, const QString &channel = QString());

public slots:
    // Kept as a compatibility hook; syntax highlighting cannot erase overlays.
    void scheduleBlockReapply(const QTextBlock &block);

signals:
    void overlaysChanged();

protected:
    bool eventFilter(QObject *watched, QEvent *event) override;

private slots:
    void onContentsChange(int position, int charsRemoved, int charsAdded);

private:
    using FormatRanges = QList<QTextLayout::FormatRange>;

    struct IndexedRanges {
        FormatRanges ranges;
        QVector<int> prefixMaximumEnds;
    };

    struct BlockEntry {
        QTextBlock block;
        QHash<QString, IndexedRanges> channels;
        IndexedRanges hitRanges;
        FormatRanges paintRuns;
        bool needsGraphemeGeometry{false};
    };

    static constexpr int OverlayOwnerProperty = QTextFormat::UserProperty + 0x5750;
    static constexpr int OverlayChannelProperty = QTextFormat::UserProperty + 0x5751;
    static constexpr int OverlayPriorityProperty = QTextFormat::UserProperty + 0x5752;

    QPointer<QTextDocument> m_document;
    QPointer<QPlainTextEdit> m_toolTipEditor;
    QTimer m_toolTipTimer;
    QPoint m_toolTipPosition;
    QHash<quint64, BlockEntry> m_blocks;
    QHash<QString, QSet<quint64>> m_channelBlocks;
    int m_characterCount{0};

    static QTextCharFormat overlayFormat(const QTextCharFormat &source, const QString &channel);
    static bool formatRangesEqual(const FormatRanges &left, const FormatRanges &right);
    static IndexedRanges indexRanges(FormatRanges ranges);
    static bool findIndexedFormat(const IndexedRanges &ranges, int positionInBlock, QTextLayout::FormatRange &format, bool requireToolTip = false);
    static void collectIndexedFormats(const IndexedRanges &ranges, int positionInBlock, FormatRanges &results);
    static bool paintOrderLessThan(const QTextLayout::FormatRange &left, const QTextLayout::FormatRange &right);
    static QColor overlayOutlineColor(const QTextCharFormat &format);
    static bool formatsCanMerge(const QTextLayout::FormatRange &left, const QTextLayout::FormatRange &right);
    static bool textNeedsGraphemeGeometry(const QString &text);
    QString toolTipAt(const QPoint &position) const;
    void showToolTipAt(const QPoint &position);
    bool isBlockFromDocument(const QTextBlock &block) const;
    quint64 blockIdentity(const QTextBlock &block) const;
    quint64 ensureBlockIdentity(const QTextBlock &block);
    bool removeBlockFromCache(quint64 identity);
    void rebuildBlockCache(BlockEntry &entry);
    void setBlockFormatsInUpdate(const QString &channel, const QTextBlock &block, const FormatRanges &formats);
    void invalidateCache();
    void beginUpdate();
    void endUpdate();
    void notifyChanged();

    int m_updateDepth{0};
    bool m_updateChanged{false};
};
} // namespace ghostwriter

#endif
