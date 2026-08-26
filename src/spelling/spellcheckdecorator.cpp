/*
 * SPDX-FileCopyrightText: 2022-2023 Megan Conkle <megan.conkle@kdemail.net>
 * SPDX-FileCopyrightText: 2006 Jacob R Rideout <kde@jacobrideout.net>
   SPDX-FileCopyrightText: 2006 Martin Sandsmark <martin.sandsmark@kde.org>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QAction>
#include <QContextMenuEvent>
#include <QElapsedTimer>
#include <QList>
#include <QMenu>
#include <QStringList>
#include <QTextBlock>
#include <QTextBoundaryFinder>
#include <QTextCharFormat>
#include <QTextCursor>
#include <QTextLayout>
#include <QTimer>
#include <QVector>

#include <Sonnet/BackgroundChecker>
#include <Sonnet/Dialog>
#include <Sonnet/GuessLanguage>
#include <Sonnet/Settings>
#include <Sonnet/Speller>

#include "../editor/textformatoverlaycontroller.h"
#include "spellcheckdecorator.h"
#include "spellcheckdialog.h"

namespace ghostwriter
{
class SpellCheckDecoratorPrivate
{
    Q_DECLARE_PUBLIC(SpellCheckDecorator)

public:
    struct Position {
        int start, length;
    };

    /**
     * This structure abstracts the positions of breaks in the test. As per the
     * unicode annex, both the start and end of the text are returned.
     */
    typedef QList<Position> Positions;

    SpellCheckDecoratorPrivate(SpellCheckDecorator *decorator)
        : q_ptr(decorator)
    {
        ;
    }
    ~SpellCheckDecoratorPrivate()
    {
    }

    static Sonnet::Settings *settings;

    SpellCheckDecorator *q_ptr;
    QPlainTextEdit *editor;
    TextFormatOverlayController *overlayController;
    Sonnet::Speller *speller;
    QColor errorColor;
    SpellCheckDialog *spellCheckDialog;
    // Coalesces contents-change bursts and defers the block sweep off the
    // change event so loading a large document never freezes the GUI.
    QTimer *recheckTimer = nullptr;
    int recheckStart = 0;
    int recheckEnd = -1;

    QMenu *createContextMenu();

    QMenu *createSpellingMenu(const QString &misspelledWord, const QTextCursor &cursorForWord, QMenu *parentMenu);

    QString getMisspelledWordAtCursor(QTextCursor &cursorForWord) const;

    void onContentsChanged(int position, int charsRemoved, int charsAdded);
    void processPendingRecheck();
    void spellCheckBlock(QTextBlock &block) const;
    Positions wordBreaks(const QString &text) const;
    Positions sentenceBreaks(const QString &text) const;
};

Sonnet::Settings *SpellCheckDecoratorPrivate::settings = nullptr;

SpellCheckDecorator::SpellCheckDecorator(QPlainTextEdit *editor, TextFormatOverlayController *overlayController)
    : QObject(editor)
    , d_ptr(new SpellCheckDecoratorPrivate(this))
{
    Q_D(SpellCheckDecorator);

    if (nullptr == d->settings) {
        d->settings = new Sonnet::Settings();
    }

    d->editor = editor;
    d->overlayController = overlayController;

    Q_ASSERT(nullptr != d->editor);
    Q_ASSERT(nullptr != d->overlayController);

    d->editor->viewport()->installEventFilter(this);

    connect(d->editor->document(),
            static_cast<void (QTextDocument::*)(int, int, int)>(&QTextDocument::contentsChange),
            this,
            [d](int position, int charsRemoved, int charsAdded) {
                d->onContentsChanged(position, charsRemoved, charsAdded);
            });

    d->recheckTimer = new QTimer(this);
    d->recheckTimer->setSingleShot(true);
    d->recheckTimer->setInterval(150);
    connect(d->recheckTimer, &QTimer::timeout, this, [d]() {
        d->processPendingRecheck();
    });

    d->speller = new Sonnet::Speller();
    d->speller->setLanguage(d->settings->defaultLanguage());
}

SpellCheckDecorator::~SpellCheckDecorator()
{
    delete d_ptr->speller;
}

QColor SpellCheckDecorator::errorColor() const
{
    Q_D(const SpellCheckDecorator);

    return d->errorColor;
}

void SpellCheckDecorator::setErrorColor(const QColor &color)
{
    Q_D(SpellCheckDecorator);

    d->errorColor = color;
    this->rehighlight();
}

void SpellCheckDecorator::settingsChanged()
{
    Q_D(SpellCheckDecorator);

    if (d->settings) {
        delete d->settings;
        d->settings = new Sonnet::Settings(this);
    }

    d->speller->setLanguage(d->settings->defaultLanguage());
    this->rehighlight();
}

void SpellCheckDecorator::rehighlight()
{
    Q_D(SpellCheckDecorator);

    if (!d->settings->checkerEnabledByDefault()) {
        d->overlayController->clearChannel(TextFormatOverlayController::spellingChannel());
        d->recheckStart = 0;
        d->recheckEnd = -1;
        return;
    }

    // Queue a budgeted whole-document sweep instead of blocking the GUI.
    d->recheckStart = 0;
    d->recheckEnd = d->editor->document()->characterCount();
    d->recheckTimer->start();
}

bool SpellCheckDecorator::eventFilter(QObject *watched, QEvent *event)
{
    Q_D(SpellCheckDecorator);

    if (watched != d->editor->viewport() || event->type() != QEvent::ContextMenu || d->editor->isReadOnly()) {
        return false;
    }

    // Check spelling of text block under mouse or at cursor position.
    QContextMenuEvent *contextEvent = static_cast<QContextMenuEvent *>(event);

    QTextCursor cursorForWord;

    // If the context menu event was triggered by pressing the menu key, use the
    // current text cursor rather than the event position to get a cursor
    // position, since the event position is the mouse position rather than the
    // text cursor position.
    if (QContextMenuEvent::Keyboard == contextEvent->reason()) {
        cursorForWord = d->editor->textCursor();
    }
    // Else process as mouse event.
    else {
        cursorForWord = d->editor->cursorForPosition(contextEvent->pos());
    }

    QMenu *popupMenu = d->createContextMenu();
    QString misspelledWord = d->getMisspelledWordAtCursor(cursorForWord);

    if (!misspelledWord.isNull() && !misspelledWord.isEmpty()) {
        QMenu *spellingMenu = d->createSpellingMenu(misspelledWord, cursorForWord, popupMenu);
        const QList<QAction *> actions = popupMenu->actions();
        QAction *firstAction = actions.isEmpty() ? nullptr : actions.first();
        popupMenu->insertMenu(firstAction, spellingMenu);
        popupMenu->insertSeparator(firstAction);
    }

    // Show context menu
    QPoint menuPos;

    // If event was triggered by a key press, use the text cursor coordinates to
    // display the popup menu.
    if (QContextMenuEvent::Keyboard == contextEvent->reason()) {
        QRect cr = d->editor->cursorRect();
        menuPos.setX(cr.x());
        menuPos.setY(cr.y() + (cr.height() / 2));
        menuPos = d->editor->viewport()->mapToGlobal(menuPos);
    }
    // Else use the mouse coordinates from the context menu event.
    else {
        menuPos = contextEvent->globalPos();
    }

    popupMenu->setAttribute(Qt::WA_DeleteOnClose);
    popupMenu->popup(menuPos);

    return true;
}

QMenu *SpellCheckDecoratorPrivate::createContextMenu()
{
    Q_Q(SpellCheckDecorator);

    // Add spell check action to the standard context menu that comes with
    // the editor.
    QMenu *popupMenu = this->editor->createStandardContextMenu();

    QAction *checkSpellingAction = new QAction(SpellCheckDecorator::tr("Check spelling..."), popupMenu);

    q->connect(checkSpellingAction, &QAction::triggered, q, [this, q]() {
        spellCheckDialog = new SpellCheckDialog(this->editor);
        q->connect(spellCheckDialog, &SpellCheckDialog::finished, q, &SpellCheckDecorator::rehighlight);
        spellCheckDialog->show();
    });

    popupMenu->addAction(checkSpellingAction);

    return popupMenu;
}

QMenu *SpellCheckDecoratorPrivate::createSpellingMenu(const QString &misspelledWord, const QTextCursor &cursorForWord, QMenu *parentMenu)
{
    Q_Q(SpellCheckDecorator);
    QMenu *spellingMenu = new QMenu(SpellCheckDecorator::tr("Spelling"), parentMenu);

    if (this->settings->autodetectLanguage()) {
        QString text = cursorForWord.block().text();
        int pos = cursorForWord.position() - cursorForWord.block().position();

        for (auto sentenceBreak : sentenceBreaks(text)) {
            if (pos < (sentenceBreak.start + sentenceBreak.length)) {
                QString sentence = text.mid(sentenceBreak.start, sentenceBreak.length);
                QString language = Sonnet::GuessLanguage().identify(sentence);

                if (!language.isNull()) {
                    this->speller->setLanguage(language);
                }

                break;
            }
        }
    }

    QStringList suggestions = this->speller->suggest(misspelledWord);

    if (!suggestions.empty()) {
        // Add suggested spellings to the popup menu.
        for (const QString &suggestion : suggestions) {
            QAction *suggestionAction = new QAction(suggestion, spellingMenu);

            // If a suggested spelling is selected from the popup menu,
            // replace the misspelled word selection with the new spelling.
            q->connect(suggestionAction, &QAction::triggered, [cursorForWord, suggestionAction]() {
                QTextCursor cursor(cursorForWord);
                cursor.insertText(suggestionAction->data().toString());
            });

            // Need the following line because KDE Plasma 5 will insert a hidden
            // ampersand into the menu text as a keyboard accelerator.  Go off
            // of the data in the QAction rather than the text to avoid this.
            suggestionAction->setData(suggestion);

            // Add suggested spelling action to the popup menu.
            spellingMenu->addAction(suggestionAction);
        }
    } else {
        QAction *noSuggestionsAction = new QAction(SpellCheckDecorator::tr("No spelling suggestions found"), spellingMenu);
        noSuggestionsAction->setEnabled(false);
        spellingMenu->addAction(noSuggestionsAction);
    }

    spellingMenu->addSeparator();
    QAction *addWordToDictionaryAction = new QAction(SpellCheckDecorator::tr("Add word to dictionary"), spellingMenu);
    q->connect(addWordToDictionaryAction, &QAction::triggered, [this, q, cursorForWord, misspelledWord]() {
        this->editor->setTextCursor(cursorForWord);
        this->speller->addToPersonal(misspelledWord);
        q->rehighlight();
    });
    spellingMenu->addAction(addWordToDictionaryAction);

    return spellingMenu;
}

QString SpellCheckDecoratorPrivate::getMisspelledWordAtCursor(QTextCursor &cursorForWord) const
{
    int blockPosition = cursorForWord.positionInBlock();
    int misspelledWordStartPos = 0;
    int misspelledWordLength = 0;

    QTextLayout::FormatRange spellingRange;
    if (overlayController->findFormatAt(TextFormatOverlayController::spellingChannel(), cursorForWord.block(), blockPosition, spellingRange)) {
        misspelledWordStartPos = spellingRange.start;
        misspelledWordLength = spellingRange.length;
    } else if (blockPosition > 0
               && overlayController->findFormatAt(TextFormatOverlayController::spellingChannel(), cursorForWord.block(), blockPosition - 1, spellingRange)) {
        misspelledWordStartPos = spellingRange.start;
        misspelledWordLength = spellingRange.length;
    }

    if (misspelledWordLength == 0) {
        QTextCursor candidate(cursorForWord);
        candidate.select(QTextCursor::WordUnderCursor);
        const QString word = candidate.selectedText();
        if (word.isEmpty() || !speller->isMisspelled(word)) {
            return QString();
        }
        cursorForWord = candidate;
        return word;
    }

    // Select the misspelled word.
    cursorForWord.movePosition(QTextCursor::PreviousCharacter, QTextCursor::MoveAnchor, blockPosition - misspelledWordStartPos);

    cursorForWord.movePosition(QTextCursor::NextCharacter, QTextCursor::KeepAnchor, misspelledWordLength);

    return cursorForWord.selectedText();
}

void SpellCheckDecoratorPrivate::onContentsChanged(int position, int charsRemoved, int charsAdded)
{
    if (!this->settings->checkerEnabledByDefault()) {
        return;
    }

    int endPosition = position + charsAdded;

    if (charsRemoved > 0) {
        ++endPosition;
    }

    QTextBlock firstBlock = editor->document()->findBlock(position);

    if (!firstBlock.isValid()) {
        return;
    }

    QTextBlock lastBlock = editor->document()->findBlock(endPosition);
    const int rangeStart = firstBlock.position();
    const int rangeEnd = lastBlock.isValid() ? lastBlock.position() + lastBlock.length() : editor->document()->characterCount();

    // Union with any pending range; slight over-coverage after edits is
    // harmless (a few extra blocks re-checked) and keeps the merge O(1).
    if (recheckEnd < recheckStart) {
        recheckStart = rangeStart;
        recheckEnd = rangeEnd;
    } else {
        recheckStart = qMin(recheckStart, rangeStart);
        recheckEnd = qMax(recheckEnd, rangeEnd);
    }
    recheckTimer->start();
}

void SpellCheckDecoratorPrivate::processPendingRecheck()
{
    if (recheckEnd < recheckStart || !settings->checkerEnabledByDefault()) {
        recheckStart = 0;
        recheckEnd = -1;
        return;
    }

    QTextDocument *document = editor->document();
    QElapsedTimer budgetTimer;
    budgetTimer.start();
    // Sweep in bounded ticks: a whole-document recheck (document load,
    // settings change) paints progressively instead of freezing the GUI for
    // seconds in a single event-loop turn.
    const int budgetMs = 4;
    int position = recheckStart;
    while (position < recheckEnd) {
        QTextBlock block = document->findBlock(position);
        if (!block.isValid()) {
            break;
        }
        spellCheckBlock(block);
        position = block.position() + block.length();
        if (budgetTimer.elapsed() >= budgetMs) {
            recheckStart = position;
            recheckTimer->start(0);
            return;
        }
    }
    recheckStart = 0;
    recheckEnd = -1;
}

void SpellCheckDecoratorPrivate::spellCheckBlock(QTextBlock &block) const
{
    QString text = block.text();
    QList<QTextLayout::FormatRange> formats;

    if (this->settings->autodetectLanguage()) {
        // Language detection is the dominant cost of this path; identifying
        // once per block instead of once per sentence keeps mixed-language
        // blocks rare enough that the block-level answer is a good trade.
        QString language = Sonnet::GuessLanguage().identify(text);

        if (!language.isNull()) {
            this->speller->setLanguage(language);
        } else {
            this->speller->setLanguage(this->settings->defaultLanguage());
        }
    }

    for (auto sentenceSegment : sentenceBreaks(text)) {
        QString sentence = text.mid(sentenceSegment.start, sentenceSegment.length);

        for (auto wordSegment : wordBreaks(sentence)) {
            int wordStart = sentenceSegment.start + wordSegment.start;
            QString word = text.mid(wordStart, wordSegment.length);

            if (speller->isMisspelled(word)) {
                QTextCharFormat spellingErrorFormat;
                spellingErrorFormat.setUnderlineColor(this->errorColor);
                spellingErrorFormat.setUnderlineStyle(QTextCharFormat::SpellCheckUnderline);

                QTextLayout::FormatRange range;
                range.start = wordStart;
                range.length = wordSegment.length;
                range.format = spellingErrorFormat;

                formats.append(range);
            }
        }
    }

    overlayController->setBlockFormats(TextFormatOverlayController::spellingChannel(), block, formats);
}

// Code is lifted from KDE Frameworks' Sonnet library, because we know it
// just works.  :)
SpellCheckDecoratorPrivate::Positions SpellCheckDecoratorPrivate::wordBreaks(const QString &text) const
{
    Positions breaks;

    if (text.isEmpty()) {
        return breaks;
    }

    QTextBoundaryFinder boundaryFinder(QTextBoundaryFinder::Word, text);

    while (boundaryFinder.position() < text.length()) {
        if (!(boundaryFinder.boundaryReasons().testFlag(QTextBoundaryFinder::StartOfItem))) {
            if (boundaryFinder.toNextBoundary() == -1) {
                break;
            }
            continue;
        }

        Position pos;
        pos.start = boundaryFinder.position();
        int end = boundaryFinder.toNextBoundary();
        if (end == -1) {
            break;
        }
        pos.length = end - pos.start;
        if (pos.length < 1) {
            continue;
        }
        breaks.append(pos);

        if (boundaryFinder.toNextBoundary() == -1) {
            break;
        }
    }
    return breaks;
}

// Code is lifted from KDE Frameworks' Sonnet library, because we know it
// just works.  :)
SpellCheckDecoratorPrivate::Positions SpellCheckDecoratorPrivate::sentenceBreaks(const QString &text) const
{
    Positions breaks;

    if (text.isEmpty()) {
        return breaks;
    }

    QTextBoundaryFinder boundaryFinder(QTextBoundaryFinder::Sentence, text);

    while (boundaryFinder.position() < text.length()) {
        Position pos;
        pos.start = boundaryFinder.position();
        int end = boundaryFinder.toNextBoundary();
        if (end == -1) {
            break;
        }
        pos.length = end - pos.start;
        if (pos.length < 1) {
            continue;
        }
        breaks.append(pos);
    }
    return breaks;
}

} // namespace ghostwriter
