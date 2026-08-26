/*
 * SPDX-FileCopyrightText: 2014-2023 Megan Conkle <megan.conkle@kdemail.net>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef TEXTBLOCKDATA_H
#define TEXTBLOCKDATA_H

#include <atomic>
#include <functional>

#include <QObject>
#include <QTextBlock>
#include <QTextBlockUserData>

#include "markdown/markdownast.h"

#include "markdowndocument.h"

namespace ghostwriter
{
/**
 * User data for use with the MarkdownHighlighter and DocumentStatistics.
 */
class TextBlockData : public QTextBlockUserData
{
public:

    typedef struct MarkupRange
    {
        int start;
        int end;
        MarkdownNode::NodeType type;
    } MarkupRange;

    typedef QVector<MarkupRange> MarkupRanges;

    /**
     * Constructor.
     */
    TextBlockData()
    {
        overlayIdentity = nextOverlayIdentity.fetch_add(1, std::memory_order_relaxed);
        wordCount = 0;
        alphaNumericCharacterCount = 0;
        sentenceCount = 0;
        lixLongWordCount = 0;
    }

    /**
     * Destructor.
     */
    virtual ~TextBlockData()
    {
        if (overlayCleanup) {
            overlayCleanup(overlayIdentity);
        }
    }

    void clearMarkup()
    {
        markup.clear();
    }

    int wordCount;
    int alphaNumericCharacterCount;
    int sentenceCount;
    int lixLongWordCount;
    quint64 overlayIdentity;
    std::function<void(quint64)> overlayCleanup;

    MarkupRanges markup;

private:
    inline static std::atomic<quint64> nextOverlayIdentity{1};
};
} // namespace ghostwriter

#endif // TEXTBLOCKDATA_H
