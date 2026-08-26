/*
 * SPDX-FileCopyrightText: 2014-2022 Megan Conkle <megan.conkle@kdemail.net>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QElapsedTimer>
#include <QFile>
#include <QListWidgetItem>
#include <QPointer>
#include <QString>
#include <QTextBlock>
#include <QTextStream>
#include <QTimer>
#include <QVariant>

#include "editor/textblockdata.h"
#include "outlinewidget.h"

namespace ghostwriter
{

namespace
{
// Pacing tints keep the decoration legible under any theme; buckets live on
// OutlineWidget (PACING_*) so tests can assert bucket assignments.
// Spans longer than this are pairing artifacts (mirrors dialogue.py).
const int MAX_DIALOGUE_SPAN_CHARS = 5000;

// Port of backend/analyzers/dialogue.py _dialogue_spans: the same four
// quote styles and pairing rules so C++ and engine agree on what counts
// as dialogue.
QVector<QPair<int, int>> dialogueSpans(const QString &text)
{
    const QString openings = QStringLiteral("\"'\u201C\u2018");
    const QString closings = QStringLiteral("\"'\u201D\u2019");
    QHash<QChar, QChar> closingToOpening;
    closingToOpening.insert(QLatin1Char('"'), QLatin1Char('"'));
    closingToOpening.insert(QLatin1Char('\''), QLatin1Char('\''));
    closingToOpening.insert(QChar(0x201D), QChar(0x201C));
    closingToOpening.insert(QChar(0x2019), QChar(0x2018));

    QHash<QChar, int> starts;
    QVector<QPair<int, int>> spans;
    for (int index = 0; index < text.size(); ++index) {
        const QChar character = text.at(index);
        if (!openings.contains(character) && !closings.contains(character)) {
            continue;
        }
        if (index && text.at(index - 1) == QLatin1Char('\\')) {
            continue;
        }
        const QChar previous = index ? text.at(index - 1) : QChar();
        const QChar following = index + 1 < text.size() ? text.at(index + 1) : QChar();
        if ((character == QLatin1Char('\'') || character == QChar(0x2019)) && previous.isLetterOrNumber() && following.isLetterOrNumber()) {
            continue;
        }
        const QChar opening = closingToOpening.value(character, character);
        if (starts.contains(opening) && character == closingToOpening.value(opening, character)) {
            if (!previous.isNull() && !previous.isSpace() && !following.isLetterOrNumber()) {
                const int spanStart = starts.take(opening);
                if (index + 1 - spanStart <= MAX_DIALOGUE_SPAN_CHARS) {
                    spans.append({spanStart, index + 1});
                }
            }
        } else if (openings.contains(character) && !previous.isLetterOrNumber() && !following.isNull() && !following.isSpace()) {
            starts.insert(character, index);
        }
    }
    std::sort(spans.begin(), spans.end());
    return spans;
}
} // namespace

class OutlineWidgetPrivate
{
    Q_DECLARE_PUBLIC(OutlineWidget)

public:
    OutlineWidgetPrivate(OutlineWidget *q_ptr, MarkdownEditor *editor)
        : q_ptr(q_ptr)
    {
        this->editor = editor;
    }

    ~OutlineWidgetPrivate()
    {
        ;
    }

    static const int DOCUMENT_POSITION_ROLE;

    OutlineWidget *q_ptr;
    QPointer<MarkdownEditor> editor;
    // Pacing tints recompute on a debounce so active typing never pays for
    // the section walk.
    QTimer *pacingTimer = nullptr;

    void applyPacingTints();

    /*
    * Invoked when the user selects one of the headings in the outline
    * in order to navigate to a different position in the document.
    */
    void onOutlineHeadingSelected(QListWidgetItem *item);

    void reloadOutline();

    /*
    * Gets the document position stored in the given item.
    */
    int documentPosition(QListWidgetItem *item);

    /*
    * Binary search of the outline tree.  Returns row of the matching
    * QListWidgetItem, or else -1 if the item with the given document
    * position is not found.
    *
    * If exactMatch is false and the item is not found, this method will
    * return the row number where the heading would belong if it were in
    * the tree (i.e., an ideal insertion point for a new heading).
    */
    int findHeading(int position, bool exactMatch = true);
};

const int OutlineWidgetPrivate::DOCUMENT_POSITION_ROLE = Qt::UserRole + 1;

const int OutlineWidget::PACING_BUCKET_ROLE = Qt::UserRole + 2;
const int OutlineWidget::PACING_NEUTRAL = 0;
const int OutlineWidget::PACING_SLOW = 1;
const int OutlineWidget::PACING_FAST = 2;

OutlineWidget::OutlineWidget(MarkdownEditor *editor, QWidget *parent)
    : QListWidget(parent),
      d_ptr(new OutlineWidgetPrivate(this, editor))
{
    Q_D(OutlineWidget);

    d->pacingTimer = new QTimer(this);
    d->pacingTimer->setSingleShot(true);
    d->pacingTimer->setInterval(500);
    this->connect(d->pacingTimer, &QTimer::timeout, this, [d]() {
        d->applyPacingTints();
    });

    this->connect
    (
        this,
        &OutlineWidget::itemActivated,
        [d](QListWidgetItem * item) {
            d->onOutlineHeadingSelected(item);
        }
    );
    this->connect
    (
        this,
        &OutlineWidget::itemClicked,
        [d](QListWidgetItem * item) {
            d->onOutlineHeadingSelected(item);
        }
    );
    this->connect
    (
        editor,
        &MarkdownEditor::cursorPositionChanged,
        this,
        &OutlineWidget::updateCurrentNavigationHeading
    );

    // Receiver (this) is REQUIRED here: without a context object the
    // connection would outlive the outline and fire this lambda with a
    // dangling d-pointer during editor teardown.
    this->connect(editor->document(), &MarkdownDocument::contentsChange, this, [d](int, int, int) {
        d->reloadOutline();
    });
    // The AST arrives asynchronously after the debounced background parse;
    // without this connection the outline stays empty until the next edit.
    this->connect(editor, &MarkdownEditor::markdownASTUpdated, this, [d](quint64) {
        d->reloadOutline();
    });
}

OutlineWidget::~OutlineWidget()
{
    ;
}

void OutlineWidget::updateCurrentNavigationHeading(int position)
{
    Q_D(OutlineWidget);

    // Make sure editor and document haven't been deleted.
    // Otherwise, application may crash on exit.
    //
    if (!d->editor) {
        return;
    }

    if ((this->count() > 0) && (position >= 0)) {
        // Find out in which subsection of the document the cursor presently is
        // located.
        //
        int row = d->findHeading(position, false);

        // If findHeading call recommended an insertion point for a new
        // heading rather than a matching row, then back up one row
        // for the actual heading under which the document position falls.
        //
        if
        (
            (row == this->count())
            ||
            (
                (row >= 0) &&
                (row < this->count()) &&
                (d->documentPosition(this->item(row)) != position)
            )
        ) {
            row--;
        }

        if (row >= 0) {
            QListWidgetItem *itemToHighlight = this->item(row);
            setCurrentItem(itemToHighlight);
            this->scrollToItem
            (
                itemToHighlight,
                QAbstractItemView::PositionAtCenter
            );
        } else {
            // Document position is before the first heading.  Deselect
            // any selected headings, and scroll to the top.
            //
            setCurrentItem(nullptr);
            this->scrollToTop();
        }
    }
}

void OutlineWidgetPrivate::onOutlineHeadingSelected(QListWidgetItem *item)
{
    Q_Q(OutlineWidget);

    // Make sure editor and document haven't been deleted.
    // Otherwise, application may crash on exit.
    //
    if (!editor) {
        return;
    }

    editor->navigateDocument(documentPosition(item));
    emit q->headingNumberNavigated(q->row(item) + 1);
}

void OutlineWidgetPrivate::reloadOutline()
{
    Q_Q(OutlineWidget);

    // Make sure editor and document haven't been deleted.
    // Otherwise, application may crash on exit.
    //
    if (!editor) {
        return;
    }

    q->clear();

    if ((nullptr == editor) || (nullptr == editor->document())) {
        return;
    }

    MarkdownAST *ast = ((MarkdownDocument *) editor->document())->markdownAST();

    if (nullptr == ast) {
        return;
    }

    QVector<MarkdownNode *> headings = ast->headings();

    for (MarkdownNode *heading : headings) {
        QString headingText("   ");

        for (int i = 1; i < heading->headingLevel(); i++) {
            headingText += "    ";
        }

        QTextBlock block = editor->document()->findBlockByNumber(heading->startLine() - 1);

        QRegularExpression headingRegex("^\\s*#*(.*?)\\s*#*?\\s*$");
        QRegularExpressionMatch match = headingRegex.match(block.text());

        if (match.isValid() && match.hasMatch()) {
            headingText += match.captured(1);
        }

        if (block.isValid()) {
            QListWidgetItem *item = new QListWidgetItem();
            item->setText(headingText);
            item->setData(DOCUMENT_POSITION_ROLE, QVariant::fromValue(block.position()));
            q->insertItem(q->count(), item);
        }
    }

    q->updateCurrentNavigationHeading(editor->textCursor().position());
    pacingTimer->start();
}

void OutlineWidgetPrivate::applyPacingTints()
{
    Q_Q(OutlineWidget);

    if (!editor || !editor->document() || q->count() == 0) {
        return;
    }

    QTextDocument *document = editor->document();
    const int documentEnd = document->characterCount();

    for (int row = 0; row < q->count(); ++row) {
        QListWidgetItem *item = q->item(row);
        const int sectionStart = documentPosition(item);
        const int sectionEnd = (row + 1 < q->count()) ? documentPosition(q->item(row + 1)) : documentEnd;
        if (sectionEnd <= sectionStart) {
            item->setData(OutlineWidget::PACING_BUCKET_ROLE, OutlineWidget::PACING_NEUTRAL);
            continue;
        }

        qint64 words = 0;
        qint64 sentences = 0;
        qint64 dialogueChars = 0;
        QString sectionText;
        QTextBlock block = document->findBlock(sectionStart);
        while (block.isValid() && block.position() < sectionEnd) {
            const QString blockText = block.text();
            sectionText += blockText;
            sectionText += QLatin1Char('\n');
            const TextBlockData *blockData = static_cast<TextBlockData *>(block.userData());
            if (nullptr != blockData) {
                words += blockData->wordCount;
                sentences += blockData->sentenceCount;
            }
            block = block.next();
        }
        if (words <= 0) {
            words = sectionText.split(QRegularExpression(QStringLiteral("\\s+")), Qt::SkipEmptyParts).size();
        }
        // TextBlockData counters are populated by DocumentStatistics; when
        // they have not been calculated yet, fall back to terminator counts
        // so pacing still reflects reality.
        if (sentences <= 0 && words > 0) {
            for (const QChar &character : sectionText) {
                if (character == QLatin1Char('.') || character == QLatin1Char('!') || character == QLatin1Char('?')) {
                    ++sentences;
                }
            }
            sentences = qMax<qint64>(sentences, 1);
        }

        int bucket = OutlineWidget::PACING_NEUTRAL;
        if (words > 0) {
            for (const auto &span : dialogueSpans(sectionText)) {
                dialogueChars += (span.second - span.first);
            }
            const double dialogueShare = sectionText.isEmpty() ? 0.0 : static_cast<double>(dialogueChars) / static_cast<double>(sectionText.size());
            const double wordsPerSentence = sentences > 0 ? static_cast<double>(words) / static_cast<double>(sentences) : 0.0;
            if (dialogueShare >= 0.5) {
                bucket = OutlineWidget::PACING_FAST;
            } else if (wordsPerSentence >= 30.0) {
                bucket = OutlineWidget::PACING_SLOW;
            }
        }
        item->setData(OutlineWidget::PACING_BUCKET_ROLE, bucket);
        if (bucket == OutlineWidget::PACING_SLOW) {
            item->setData(Qt::BackgroundRole, QColor(70, 130, 200, 46));
        } else if (bucket == OutlineWidget::PACING_FAST) {
            item->setData(Qt::BackgroundRole, QColor(230, 160, 60, 46));
        } else {
            item->setData(Qt::BackgroundRole, QVariant());
        }
    }
}

int OutlineWidgetPrivate::documentPosition(QListWidgetItem *item)
{
    return item->data(DOCUMENT_POSITION_ROLE).value<int>();
}

int OutlineWidgetPrivate::findHeading(int position, bool exactMatch)
{
    Q_Q(OutlineWidget);

    int low = 0;
    int high = q->count() - 1;
    int mid = 0;

    while (low <= high) {
        mid = low + ((high - low) / 2);
        int itemPos = documentPosition(q->item(mid));

        // Check if desired heading at document position is at row "mid".
        if (itemPos == position) {
            return mid;
        }
        // Else if document position is greater than the current item's
        // position, ignore the first half of the list.
        //
        else if (itemPos < position) {
            mid++;
            low = mid;
        }
        // Else if document position is smaller, ignore the last half of the
        // list.
        //
        else {
            high = mid - 1;
        }
    }

    // Heading with desired document position is not present.

    if (exactMatch) {
        return -1;
    } else {
        return mid;
    }
}
} // namespace ghostwriter
