/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QElapsedTimer>
#include <QPainter>
#include <QRegularExpression>
#include <QTextBlock>
#include <QTextBoundaryFinder>
#include <QTimer>

#include "breathmapwidget.h"

namespace ghostwriter
{
namespace
{
const int STRIP_WIDTH = 28;
const int UPDATE_DEBOUNCE_MS = 400;
const int MAX_BAR_WORDS = 60;
// A "monotony run" is this many consecutive sentences within tolerance.
const int MONOTONY_RUN_MIN = 5;
const int MONOTONY_TOLERANCE_WORDS = 2;
} // namespace

BreathMapWidget::BreathMapWidget(QWidget *parent)
    : QWidget(parent)
{
    setFixedWidth(STRIP_WIDTH);
    setObjectName(QStringLiteral("breathMapStrip"));
    setAccessibleName(tr("Sentence rhythm map"));
}

QSize BreathMapWidget::sizeHint() const
{
    return QSize(STRIP_WIDTH, height());
}

void BreathMapWidget::setDocument(QTextDocument *newDocument)
{
    document = newDocument;
    monotonyRun = false;
    sentenceWords.clear();

    if (nullptr != document) {
        QTimer *debounce = new QTimer(this);
        debounce->setSingleShot(true);
        debounce->setInterval(UPDATE_DEBOUNCE_MS);
        connect(debounce, &QTimer::timeout, this, &BreathMapWidget::refresh);
        // Debounce lives with the widget; one per document binding is fine
        // because setDocument is called once per document in practice.
        connect(document, &QTextDocument::contentsChange, debounce, static_cast<void (QTimer::*)()>(&QTimer::start));
    }
    refresh();
}

void BreathMapWidget::refresh()
{
    // Zero cost while hidden: the strip only works when the user can see it.
    if (!isVisible() || nullptr == document) {
        return;
    }

    sentenceWords.clear();
    for (QTextBlock block = document->firstBlock(); block.isValid(); block = block.next()) {
        const QString text = block.text();
        if (text.trimmed().isEmpty()) {
            continue;
        }
        QTextBoundaryFinder finder(QTextBoundaryFinder::Sentence, text);
        int sentenceStart = 0;
        while (finder.toNextBoundary() != -1) {
            const int sentenceEnd = finder.position();
            const QString sentence = text.mid(sentenceStart, sentenceEnd - sentenceStart);
            const QStringList words = sentence.split(QRegularExpression(QStringLiteral("\\s+")), Qt::SkipEmptyParts);
            if (!words.isEmpty()) {
                sentenceWords.append(words.size());
            }
            sentenceStart = sentenceEnd;
        }
    }

    monotonyRun = false;
    for (int index = 0; index + MONOTONY_RUN_MIN <= sentenceWords.size(); ++index) {
        int lowest = sentenceWords.at(index);
        int highest = lowest;
        bool uniform = true;
        for (int run = 1; run < MONOTONY_RUN_MIN; ++run) {
            const int count = sentenceWords.at(index + run);
            lowest = qMin(lowest, count);
            highest = qMax(highest, count);
            if (highest - lowest > MONOTONY_TOLERANCE_WORDS) {
                uniform = false;
                break;
            }
        }
        if (uniform) {
            monotonyRun = true;
            break;
        }
    }

    update();
}

QVector<int> BreathMapWidget::sentenceWordCounts() const
{
    return sentenceWords;
}

bool BreathMapWidget::monotonyRunDetected() const
{
    return monotonyRun;
}

void BreathMapWidget::paintEvent(QPaintEvent *event)
{
    Q_UNUSED(event)
    if (sentenceWords.isEmpty()) {
        return;
    }

    QPainter painter(this);
    const int barHeight = qMax(2, height() / qMax(1, sentenceWords.size()));
    const int usableWidth = width() - 6;
    int y = 0;
    for (const int count : sentenceWords) {
        const int barWidth = qMin(usableWidth, (count * usableWidth) / MAX_BAR_WORDS + 2);
        painter.fillRect(3, y, barWidth, qMax(1, barHeight - 1), QColor(120, 144, 168, 190));
        y += barHeight;
        if (y > height()) {
            break;
        }
    }
}
} // namespace ghostwriter
