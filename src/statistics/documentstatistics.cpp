/*
 * SPDX-FileCopyrightText: 2016-2023 Megan Conkle <megan.conkle@kdemail.net>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QTextBoundaryFinder>
#include <QVector>
#include <QtCore/qmath.h>

#include "documentstatistics.h"

namespace ghostwriter
{
class DocumentStatisticsPrivate
{
    Q_DECLARE_PUBLIC(DocumentStatistics)

public:
    struct BlockStatistics {
        int length = 0;
        int wordCount = 0;
        int wordCharacterCount = 0;
        int sentenceCount = 0;
        int lixLongWordCount = 0;
        bool paragraph = false;
    };

    DocumentStatisticsPrivate(DocumentStatistics *q_ptr)
        : q_ptr(q_ptr)
    {
        ;
    }

    ~DocumentStatisticsPrivate()
    {
        ;
    }

    static const QString LESS_THAN_ONE_MINUTE_STR;
    static const QString VERY_EASY_READING_EASE_STR;
    static const QString EASY_READING_EASE_STR;
    static const QString MEDIUM_READING_EASE_STR;
    static const QString DIFFICULT_READING_EASE_STR;
    static const QString VERY_DIFFICULT_READING_EASE_STR;

    DocumentStatistics *q_ptr;
    MarkdownDocument *document;

    int wordCount; // may be count of selected text only or entire document
    int totalWordCount; // word count of entire document

    // Count of characters that are "word" characters.
    int wordCharacterCount;

    int sentenceCount;
    int paragraphCount;
    int pageCount;
    int lixLongWordCount;
    int readTimeMinutes;
    QVector<BlockStatistics> blocks;

    void updateStatistics();
    void resetTotals();
    void addBlockStatistics(const BlockStatistics &statistics);
    void subtractBlockStatistics(const BlockStatistics &statistics);
    BlockStatistics calculateBlockStatistics(QTextBlock block);
    void rebuildAllStatistics();
    bool updateAffectedBlocks(int position, int charsRemoved, int charsAdded, int &updatedBlockCount);
    void countWords
    (
        const QString &text,
        int &words,
        int &lixLongWords,
        int &alphaNumericCharacters
    );
    int countSentences(const QString &text);
    int calculatePageCount(int words);
    int calculateCLI(int characters, int words, int sentences);
    int calculateLIX(int totalWords, int longWords, int sentences);
    int calculateComplexWords(int totalWords, int longWords);
    int calculateReadingTime(int words);
};

DocumentStatistics::DocumentStatistics(MarkdownDocument *document, QObject *parent)
    : QObject(parent), d_ptr(new DocumentStatisticsPrivate(this))
{
    Q_D(DocumentStatistics);

    d->document = document;
    d->wordCount = 0;
    d->totalWordCount = 0;
    d->wordCharacterCount = 0;
    d->sentenceCount = 0;
    d->paragraphCount = 0;
    d->pageCount = 0;
    d->lixLongWordCount = 0;
    d->readTimeMinutes = 0;

    connect(d->document, SIGNAL(contentsChange(int, int, int)), this, SLOT(onTextChanged(int, int, int)));
    connect(d->document, &MarkdownDocument::cleared, [d]() {
        d->rebuildAllStatistics();
    });
    d->rebuildAllStatistics();
}

DocumentStatistics::~DocumentStatistics()
{
    ;
}

int DocumentStatistics::wordCount() const
{
    Q_D(const DocumentStatistics);
    
    return d->wordCount;
}

int DocumentStatistics::characterCount() const
{
    Q_D(const DocumentStatistics);

    return d->document->characterCount() - 1;
}

int DocumentStatistics::paragraphCount() const
{
    Q_D(const DocumentStatistics);

    return d->paragraphCount;
}

int DocumentStatistics::sentenceCount() const
{
    Q_D(const DocumentStatistics);

    return d->sentenceCount;
}

int DocumentStatistics::pageCount() const
{
    Q_D(const DocumentStatistics);

    return d->pageCount;
}

int DocumentStatistics::readingTime() const
{
    Q_D(const DocumentStatistics);

    return d->readTimeMinutes;
}

void DocumentStatistics::onTextSelected
(
    const QString &selectedText,
    int selectionStart,
    int selectionEnd
)
{
    Q_D(DocumentStatistics);
    
    int selectionWordCount;
    int selectionLixLongWordCount;
    int selectionWordCharacterCount;

    d->countWords
    (
        selectedText,
        selectionWordCount,
        selectionLixLongWordCount,
        selectionWordCharacterCount
    );

    int selectionSentenceCount = d->countSentences(selectedText);

    // Count the number of selected paragraphs.
    int selectedParagraphCount = 0;

    QTextBlock block = d->document->findBlock(selectionStart);
    QTextBlock end = d->document->findBlock(selectionEnd).next();

    while (block != end) {
        TextBlockData *blockData = (TextBlockData *) block.userData();

        if ((nullptr != blockData) && (block.text().trimmed().length() > 0)) {
            selectedParagraphCount++;
        }

        block = block.next();
    }

    emit wordCountChanged(selectionWordCount);
    emit characterCountChanged(selectedText.length());
    emit sentenceCountChanged(selectionSentenceCount);
    emit paragraphCountChanged(selectedParagraphCount);
    emit pageCountChanged(d->calculatePageCount(selectionWordCount));
    emit complexWordsChanged(d->calculateComplexWords(selectionWordCount, selectionLixLongWordCount));
    emit readingTimeChanged(d->calculateReadingTime(selectionWordCount));
    emit lixReadingEaseChanged(d->calculateLIX(selectionWordCount, selectionLixLongWordCount, selectionSentenceCount));
    emit readabilityIndexChanged(d->calculateCLI(selectionWordCharacterCount, selectionWordCount, selectionSentenceCount));
}

void DocumentStatistics::onTextDeselected()
{
    Q_D(DocumentStatistics);
    
    d->updateStatistics();
}

void DocumentStatistics::onTextChanged(int position, int charsRemoved, int charsAdded)
{
    Q_D(DocumentStatistics);

    int updatedBlockCount = 0;
    if (!d->updateAffectedBlocks(position, charsRemoved, charsAdded, updatedBlockCount)) {
        d->rebuildAllStatistics();
        return;
    }

    emit blocksRecalculated(updatedBlockCount, false);
    d->updateStatistics();
}

void DocumentStatisticsPrivate::resetTotals()
{
    wordCount = 0;
    totalWordCount = 0;
    wordCharacterCount = 0;
    sentenceCount = 0;
    paragraphCount = 0;
    pageCount = 0;
    lixLongWordCount = 0;
    readTimeMinutes = 0;
}

void DocumentStatisticsPrivate::addBlockStatistics(const BlockStatistics &statistics)
{
    wordCount += statistics.wordCount;
    wordCharacterCount += statistics.wordCharacterCount;
    sentenceCount += statistics.sentenceCount;
    lixLongWordCount += statistics.lixLongWordCount;
    paragraphCount += statistics.paragraph ? 1 : 0;
}

void DocumentStatisticsPrivate::subtractBlockStatistics(const BlockStatistics &statistics)
{
    wordCount -= statistics.wordCount;
    wordCharacterCount -= statistics.wordCharacterCount;
    sentenceCount -= statistics.sentenceCount;
    lixLongWordCount -= statistics.lixLongWordCount;
    paragraphCount -= statistics.paragraph ? 1 : 0;
}

DocumentStatisticsPrivate::BlockStatistics DocumentStatisticsPrivate::calculateBlockStatistics(QTextBlock block)
{
    BlockStatistics statistics;
    statistics.length = block.length();
    const QString text = block.text();

    countWords(text, statistics.wordCount, statistics.lixLongWordCount, statistics.wordCharacterCount);
    statistics.sentenceCount = countSentences(text);
    statistics.paragraph = !text.trimmed().isEmpty();

    TextBlockData *blockData = static_cast<TextBlockData *>(block.userData());
    if (nullptr == blockData) {
        blockData = new TextBlockData();
        block.setUserData(blockData);
    }
    blockData->wordCount = statistics.wordCount;
    blockData->alphaNumericCharacterCount = statistics.wordCharacterCount;
    blockData->sentenceCount = statistics.sentenceCount;
    blockData->lixLongWordCount = statistics.lixLongWordCount;

    return statistics;
}

void DocumentStatisticsPrivate::rebuildAllStatistics()
{
    Q_Q(DocumentStatistics);

    resetTotals();
    blocks.clear();
    blocks.reserve(document->blockCount());

    QTextBlock block = document->firstBlock();
    int updatedBlockCount = 0;
    while (block.isValid()) {
        const BlockStatistics statistics = calculateBlockStatistics(block);
        blocks.append(statistics);
        addBlockStatistics(statistics);
        ++updatedBlockCount;
        block = block.next();
    }

    totalWordCount = wordCount;
    emit q->blocksRecalculated(updatedBlockCount, true);
    updateStatistics();
}

bool DocumentStatisticsPrivate::updateAffectedBlocks(int position, int charsRemoved, int charsAdded, int &updatedBlockCount)
{
    if (blocks.isEmpty() || document->blockCount() <= 0) {
        return false;
    }

    const int lastPosition = qMax(0, document->characterCount() - 1);
    QTextBlock startBlock = document->findBlock(qBound(0, position, lastPosition));
    if (!startBlock.isValid()) {
        return false;
    }

    const int startIndex = startBlock.blockNumber();
    if (startIndex < 0 || startIndex >= blocks.size()) {
        return false;
    }

    const int oldRangeEnd = qMax(0, position - startBlock.position()) + qMax(0, charsRemoved);
    int oldAffectedCount = 1;
    int oldCoveredLength = blocks.at(startIndex).length;
    while ((startIndex + oldAffectedCount) < blocks.size() && oldCoveredLength <= oldRangeEnd) {
        oldCoveredLength += blocks.at(startIndex + oldAffectedCount).length;
        ++oldAffectedCount;
    }

    const int newRangeEnd = qBound(0, position + qMax(0, charsAdded), lastPosition);
    QTextBlock endBlock = document->findBlock(newRangeEnd);
    if (!endBlock.isValid() || endBlock.blockNumber() < startIndex) {
        return false;
    }

    const int newAffectedCount = endBlock.blockNumber() - startIndex + 1;
    QVector<BlockStatistics> replacements;
    replacements.reserve(newAffectedCount);
    QTextBlock block = startBlock;
    for (int index = 0; index < newAffectedCount; ++index) {
        if (!block.isValid()) {
            return false;
        }
        replacements.append(calculateBlockStatistics(block));
        block = block.next();
    }

    for (int index = 0; index < oldAffectedCount; ++index) {
        subtractBlockStatistics(blocks.at(startIndex + index));
    }
    blocks.remove(startIndex, oldAffectedCount);

    for (int index = 0; index < replacements.size(); ++index) {
        blocks.insert(startIndex + index, replacements.at(index));
        addBlockStatistics(replacements.at(index));
    }

    if (blocks.size() != document->blockCount()) {
        return false;
    }

    totalWordCount = wordCount;
    updatedBlockCount = replacements.size();
    return true;
}

void DocumentStatisticsPrivate::updateStatistics()
{
    Q_Q(DocumentStatistics);

    this->pageCount = calculatePageCount(wordCount);
    this->readTimeMinutes = calculateReadingTime(wordCount);
    
    emit q->wordCountChanged(wordCount);
    emit q->totalWordCountChanged(wordCount);
    emit q->characterCountChanged(document->characterCount() - 1);
    emit q->sentenceCountChanged(sentenceCount);
    emit q->paragraphCountChanged(paragraphCount);
    emit q->pageCountChanged(pageCount);
    emit q->complexWordsChanged(calculateComplexWords(wordCount, lixLongWordCount));
    emit q->readingTimeChanged(this->readTimeMinutes);
    emit q->lixReadingEaseChanged(calculateLIX(wordCount, lixLongWordCount, sentenceCount));
    emit q->readabilityIndexChanged(calculateCLI(wordCharacterCount, wordCount, sentenceCount));
}

void DocumentStatisticsPrivate::countWords
(
    const QString &text,
    int &words,
    int &lixLongWords,
    int &alphaNumericCharacters
)
{
    bool inWord = false;
    int separatorCount = 0;
    int wordLen = 0;

    words = 0;
    lixLongWords = 0;
    alphaNumericCharacters = 0;

    for (int i = 0; i < text.length(); i++) {
        if (text[i].isLetterOrNumber()) {
            inWord = true;
            separatorCount = 0;
            wordLen++;
            alphaNumericCharacters++;
        } else if (text[i].isSpace() && inWord) {
            inWord = false;
            words++;

            if (separatorCount > 0) {
                wordLen--;
                alphaNumericCharacters--;
            }

            separatorCount = 0;

            if (wordLen > 6) {
                lixLongWords++;
            }

            wordLen = 0;
        } else {
            // This is to handle things like double dashes (`--`)
            // that separate words, while still counting hyphenated
            // words as a single word.
            //
            separatorCount++;

            if (inWord) {
                if (separatorCount > 1) {
                    separatorCount = 0;
                    inWord = false;
                    words++;
                    wordLen--;
                    alphaNumericCharacters--;

                    if (wordLen > 6) {
                        lixLongWords++;
                    }

                    wordLen = 0;
                } else {
                    wordLen++;
                    alphaNumericCharacters++;
                }
            }
        }
    }

    if (inWord) {
        words++;

        if (separatorCount > 0) {
            wordLen--;
            alphaNumericCharacters--;
        }

        if (wordLen > 6) {
            lixLongWords++;
        }
    }
}

int DocumentStatisticsPrivate::countSentences(const QString &text)
{
    int count = 0;

    QString trimmedText = text.trimmed();

    if (trimmedText.length() > 0) {
        QTextBoundaryFinder boundaryFinder(QTextBoundaryFinder::Sentence, trimmedText);
        int nextSentencePos = 0;

        boundaryFinder.setPosition(0);

        while (nextSentencePos >= 0) {
            int oldPos = nextSentencePos;
            nextSentencePos = boundaryFinder.toNextBoundary();

            if
            (
                ((nextSentencePos - oldPos) > 1) ||
                (((nextSentencePos - oldPos) > 0) &&
                 !trimmedText[oldPos].isSpace())
            ) {
                count++;
            }
        }
    }

    return count;
}

int DocumentStatisticsPrivate::calculatePageCount(int words)
{
    return words / 250;
}

int DocumentStatisticsPrivate::calculateCLI(int characters, int words, int sentences)
{
    int cli = 0;

    if ((sentences > 0) && (words > 0)) {
        cli = qCeil
              (
                  (5.88 * (qreal)((float)characters / (float)words))
                  -
                  (29.6 * ((qreal)sentences / (qreal)words))
                  -
                  15.8
              );

        if (cli < 0) {
            cli = 0;
        }
    }

    return cli;
}

int DocumentStatisticsPrivate::calculateLIX(int totalWords, int longWords, int sentences)
{
    int lix = 0;

    if ((totalWords > 0) && sentences > 0) {
        lix = qCeil
              (
                  ((qreal)totalWords / (qreal)sentences)
                  +
                  (((qreal)longWords / (qreal)totalWords) * 100.0)
              );
    }

    return lix;
}

int DocumentStatisticsPrivate::calculateComplexWords(int totalWords, int longWords)
{
    int complexWordsPercentage = 0;

    if (totalWords > 0) {
        complexWordsPercentage = qCeil(((qreal)longWords / (qreal)totalWords) * 100.0);
    }

    return complexWordsPercentage;
}

int DocumentStatisticsPrivate::calculateReadingTime(int words)
{
    return words / 270;
}
}
