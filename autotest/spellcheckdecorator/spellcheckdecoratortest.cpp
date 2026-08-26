/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QApplication>
#include <QContextMenuEvent>
#include <QMenu>
#include <QPlainTextEdit>
#include <QTest>
#include <QTextBlock>
#include <QTextCursor>
#include <QTextLayout>

#include "../../src/editor/textformatoverlaycontroller.h"
#include "../../src/spelling/spellcheckdecorator.h"

using namespace ghostwriter;

class SpellCheckDecoratorTest : public QObject
{
    Q_OBJECT

private slots:
    void rightClickOffersSpellingTools();
};

void SpellCheckDecoratorTest::rightClickOffersSpellingTools()
{
    QPlainTextEdit editor;
    editor.setPlainText(QStringLiteral("wrod"));
    editor.resize(420, 160);
    editor.show();

    TextFormatOverlayController overlays(editor.document());
    QTextCharFormat spellingFormat;
    spellingFormat.setUnderlineColor(Qt::red);
    spellingFormat.setUnderlineStyle(QTextCharFormat::SpellCheckUnderline);
    QTextLayout::FormatRange range;
    range.start = 0;
    range.length = 4;
    range.format = spellingFormat;
    overlays.setBlockFormats(TextFormatOverlayController::spellingChannel(), editor.document()->begin(), {range});
    SpellCheckDecorator decorator(&editor, &overlays);

    QTextCursor cursor(editor.document());
    cursor.setPosition(2);
    editor.setTextCursor(cursor);
    const QPoint localPosition = editor.cursorRect(cursor).center();
    QContextMenuEvent event(QContextMenuEvent::Mouse, localPosition, editor.viewport()->mapToGlobal(localPosition));
    QApplication::sendEvent(editor.viewport(), &event);

    QMenu *contextMenu = nullptr;
    QTRY_VERIFY_WITH_TIMEOUT(([&]() {
                                 for (QMenu *menu : editor.findChildren<QMenu *>()) {
                                     if (menu->isVisible()) {
                                         contextMenu = menu;
                                         return true;
                                     }
                                 }
                                 return false;
                             })(),
                             1000);
    QVERIFY(contextMenu);

    bool hasCheckSpelling = false;
    bool hasSpellingSuggestions = false;
    for (QAction *action : contextMenu->actions()) {
        hasCheckSpelling = hasCheckSpelling || action->text() == QStringLiteral("Check spelling...");
        hasSpellingSuggestions = hasSpellingSuggestions || (action->menu() && action->menu()->title() == QStringLiteral("Spelling"));
    }
    QVERIFY(hasCheckSpelling);
    QVERIFY(hasSpellingSuggestions);
    contextMenu->close();
}

QTEST_MAIN(SpellCheckDecoratorTest)
#include "spellcheckdecoratortest.moc"
