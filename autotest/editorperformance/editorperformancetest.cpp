/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QDebug>
#include <QElapsedTimer>
#include <QSignalSpy>
#include <QTest>
#include <QTextCursor>
#include <QTimer>

#include "editor/markdowneditor.h"
#include "markdown/cmarkgfmapi.h"
#include "markdown/markdownast.h"
#include "statistics/documentstatistics.h"

using namespace ghostwriter;

namespace
{
QString proseWithWordCount(int wordCount)
{
    QString text;
    text.reserve(wordCount * 7);
    for (int index = 0; index < wordCount; ++index) {
        text += QStringLiteral("word");
        if ((index + 1) % 20 == 0) {
            text += QStringLiteral(".\n\n");
        } else {
            text += QLatin1Char(' ');
        }
    }
    return text;
}

void compareWithFreshStatistics(const MarkdownDocument &document, const DocumentStatistics &statistics)
{
    MarkdownDocument freshDocument(document.toPlainText());
    DocumentStatistics freshStatistics(&freshDocument);
    QCOMPARE(statistics.wordCount(), freshStatistics.wordCount());
    QCOMPARE(statistics.characterCount(), freshStatistics.characterCount());
    QCOMPARE(statistics.paragraphCount(), freshStatistics.paragraphCount());
    QCOMPARE(statistics.sentenceCount(), freshStatistics.sentenceCount());
    QCOMPARE(statistics.pageCount(), freshStatistics.pageCount());
    QCOMPARE(statistics.readingTime(), freshStatistics.readingTime());
}

MarkdownNode *legacyFindBlockAtLine(MarkdownAST *ast, int lineNumber)
{
    MarkdownNode *candidate = nullptr;
    MarkdownNode *current = ast->root() == nullptr ? nullptr : ast->root()->firstChild();

    while ((current != nullptr) && current->isBlockType() && (current->type() != MarkdownNode::TableCell)) {
        if ((current->startLine() <= lineNumber) && ((lineNumber <= current->endLine()) || (current->endLine() == 0))) {
            candidate = current;
            switch (current->type()) {
            case MarkdownNode::ListItem:
            case MarkdownNode::TaskListItem:
                return candidate;
            case MarkdownNode::Heading:
                if (((current->endLine() - current->startLine() + 1) > 2) && (lineNumber == current->endLine())) {
                    current = current->next();
                } else {
                    current = current->firstChild();
                }
                break;
            default:
                current = current->firstChild();
                break;
            }
        } else if (current->startLine() > lineNumber) {
            return candidate;
        } else {
            current = current->next();
        }
    }

    return candidate;
}
}

class EditorPerformanceTest : public QObject
{
    Q_OBJECT

private slots:
    void debouncesFullParse_data();
    void debouncesFullParse();
    void asynchronousParseAndReplacementStayCurrent();
    void indexedLookupMatchesLegacyTraversal();
    void rehighlightsOnlyStructurallyChangedBlocks();
    void updatesOnlyAffectedStatisticsBlocks_data();
    void updatesOnlyAffectedStatisticsBlocks();
    void statisticsRemainExactAcrossStructuralEdits();
    void asynchronousTextSnapshotTracksEdits();
    void closingDoesNotWaitForMarkdownParse();
};

void EditorPerformanceTest::closingDoesNotWaitForMarkdownParse()
{
    MarkdownDocument document;
    auto *editor = new MarkdownEditor(&document, ColorScheme{});
    editor->setPlainText(proseWithWordCount(500'000));
    editor->ensureDocumentParsed();

    QElapsedTimer timer;
    timer.start();
    delete editor;
    QVERIFY2(timer.elapsed() < 250, qPrintable(QStringLiteral("editor close waited %1 ms for a background parse").arg(timer.elapsed())));
}

void EditorPerformanceTest::asynchronousTextSnapshotTracksEdits()
{
    MarkdownDocument document;
    MarkdownEditor editor(&document, ColorScheme{});
    editor.setPlainText(QStringLiteral("Alpha \U0001f600 beta."));
    QTextCursor cursor(&document);
    cursor.movePosition(QTextCursor::End);
    cursor.insertText(QStringLiteral("\nGamma."));

    QFuture<QString> snapshot = editor.textSnapshot();
    snapshot.waitForFinished();
    QCOMPARE(snapshot.result(), QStringLiteral("Alpha \U0001f600 beta.\nGamma."));
}

void EditorPerformanceTest::debouncesFullParse_data()
{
    QTest::addColumn<int>("wordCount");
    QTest::newRow("10k") << 10'000;
    QTest::newRow("100k") << 100'000;
    QTest::newRow("500k") << 500'000;
}

void EditorPerformanceTest::debouncesFullParse()
{
    QFETCH(int, wordCount);

    MarkdownDocument document;
    MarkdownEditor editor(&document, ColorScheme{});
    QSignalSpy parseSpy(&editor, &MarkdownEditor::markdownASTUpdated);
    QElapsedTimer initialParseTimer;
    initialParseTimer.start();
    editor.setPlainText(proseWithWordCount(wordCount));
    QTRY_COMPARE_WITH_TIMEOUT(parseSpy.count(), 1, 30'000);
    const qint64 initialParseMilliseconds = initialParseTimer.elapsed();
    parseSpy.clear();

    QTextCursor cursor(&document);
    cursor.movePosition(QTextCursor::End);
    QElapsedTimer editTimer;
    editTimer.start();
    for (int index = 0; index < 20; ++index) {
        cursor.insertText(QStringLiteral("x"));
    }
    const qint64 editMicroseconds = editTimer.nsecsElapsed() / 1000;

    QCOMPARE(parseSpy.count(), 0);
    QVERIFY2(editMicroseconds < 100'000, qPrintable(QStringLiteral("20 edits blocked the GUI thread for %1 us").arg(editMicroseconds)));

    cursor.insertText(QStringLiteral("\n# Latest heading\n"));
    QCOMPARE(parseSpy.count(), 0);
    QTest::qWait(150);
    QCOMPARE(parseSpy.count(), 0);
    QTRY_COMPARE_WITH_TIMEOUT(parseSpy.count(), 1, 30'000);

    qInfo().noquote() << QStringLiteral("debounced-parse %1 words: 20-edit burst %2 ms, one parse").arg(wordCount).arg(editMicroseconds / 1000.0, 0, 'f', 3);
    qInfo().noquote() << QStringLiteral("initial-load-and-parse %1 words: %2 ms").arg(wordCount).arg(initialParseMilliseconds);

    MarkdownAST *ast = document.markdownAST();
    QVERIFY(ast != nullptr);
    QCOMPARE(ast->headings().size(), 1);

    if (wordCount == 500'000) {
        QElapsedTimer lookupTimer;
        lookupTimer.start();
        for (int line = 1; line <= document.blockCount(); ++line) {
            ast->findBlockAtLine(line);
        }
        const qint64 lookupMicroseconds = lookupTimer.nsecsElapsed() / 1000;
        qInfo().noquote() << QStringLiteral("indexed-lookup %1 blocks: %2 ms").arg(document.blockCount()).arg(lookupMicroseconds / 1000.0, 0, 'f', 3);
        QVERIFY2(lookupMicroseconds < 100'000, qPrintable(QStringLiteral("indexed lookup took %1 us").arg(lookupMicroseconds)));

        parseSpy.clear();
        cursor.insertText(QStringLiteral("tail"));
        int heartbeatCount = 0;
        QTimer heartbeat;
        connect(&heartbeat, &QTimer::timeout, [&heartbeatCount]() {
            ++heartbeatCount;
        });
        heartbeat.start(1);
        QElapsedTimer schedulingTimer;
        schedulingTimer.start();
        editor.ensureDocumentParsed();
        const qint64 schedulingMicroseconds = schedulingTimer.nsecsElapsed() / 1000;
        QVERIFY2(schedulingMicroseconds < 20'000, qPrintable(QStringLiteral("parse scheduling blocked for %1 us").arg(schedulingMicroseconds)));
        QTRY_COMPARE_WITH_TIMEOUT(parseSpy.count(), 1, 30'000);
        heartbeat.stop();
        QVERIFY2(heartbeatCount > 0, "the GUI event loop did not advance while the 500k parse ran");
        qInfo().noquote() << QStringLiteral("500k parse scheduling: %1 ms; GUI heartbeats while parsing: %2")
                                 .arg(schedulingMicroseconds / 1000.0, 0, 'f', 3)
                                 .arg(heartbeatCount);
    }
}

void EditorPerformanceTest::asynchronousParseAndReplacementStayCurrent()
{
    MarkdownDocument document;
    MarkdownEditor editor(&document, ColorScheme{});
    QSignalSpy parseSpy(&editor, &MarkdownEditor::markdownASTUpdated);

    editor.setPlainText(QStringLiteral("# First\n"));
    QTRY_COMPARE_WITH_TIMEOUT(parseSpy.count(), 1, 2'000);
    QCOMPARE(document.markdownAST()->headings().size(), 1);

    parseSpy.clear();
    QTextCursor cursor(&document);
    cursor.movePosition(QTextCursor::End);
    cursor.insertText(QStringLiteral("\n## Second\n"));
    QCOMPARE(parseSpy.count(), 0);

    editor.ensureDocumentParsed();
    QCOMPARE(parseSpy.count(), 0);
    QTRY_COMPARE_WITH_TIMEOUT(parseSpy.count(), 1, 2'000);
    QCOMPARE(document.markdownAST()->headings().size(), 2);
    QTest::qWait(250);
    QCOMPARE(parseSpy.count(), 1);

    parseSpy.clear();
    cursor.movePosition(QTextCursor::End);
    cursor.insertText(QStringLiteral("\n# Pending stale parse\n"));
    QCOMPARE(parseSpy.count(), 0);
    editor.setPlainText(QStringLiteral("plain replacement"));
    QCOMPARE(parseSpy.count(), 0);
    QTRY_COMPARE_WITH_TIMEOUT(parseSpy.count(), 1, 2'000);
    QCOMPARE(document.markdownAST()->headings().size(), 0);
    QTest::qWait(250);
    QCOMPARE(parseSpy.count(), 1);
}

void EditorPerformanceTest::indexedLookupMatchesLegacyTraversal()
{
    const QString markdown = QStringLiteral(
        "# Heading *one*\n\n"
        "> quoted **text**\n> second line\n\n"
        "1. first\n2. second\n\n"
        "- [x] task\n\n"
        "```cpp\nint value = 1;\n```\n\n"
        "| Name | Value |\n| --- | --- |\n| one | two |\n\n"
        "Setext heading\n===\n");

    QScopedPointer<MarkdownAST> ast(CmarkGfmAPI::instance()->parse(markdown, false));
    const int lines = markdown.count(QLatin1Char('\n')) + 1;
    for (int line = 1; line <= lines; ++line) {
        QCOMPARE(ast->findBlockAtLine(line), legacyFindBlockAtLine(ast.data(), line));
    }

    const QVector<quint64> signatures = ast->lineSignatures();
    QVERIFY(signatures.size() >= lines);
}

void EditorPerformanceTest::rehighlightsOnlyStructurallyChangedBlocks()
{
    MarkdownDocument document;
    MarkdownEditor editor(&document, ColorScheme{});
    QSignalSpy parseSpy(&editor, &MarkdownEditor::markdownASTUpdated);
    QSignalSpy highlightSpy(editor.highlighter(), SIGNAL(blockHighlightingCompleted(QTextBlock)));

    editor.setPlainText(proseWithWordCount(20'000));
    QTRY_COMPARE_WITH_TIMEOUT(parseSpy.count(), 1, 5'000);
    QTest::qWait(100);
    parseSpy.clear();
    highlightSpy.clear();

    QTextCursor cursor(&document);
    cursor.setPosition(document.characterCount() / 2);
    cursor.insertText(QStringLiteral("**focused** "));
    editor.ensureDocumentParsed();
    QTRY_COMPARE_WITH_TIMEOUT(parseSpy.count(), 1, 5'000);
    QTest::qWait(100);

    QVERIFY2(highlightSpy.count() < 50, qPrintable(QStringLiteral("a local edit rehighlighted %1 blocks").arg(highlightSpy.count())));
}

void EditorPerformanceTest::updatesOnlyAffectedStatisticsBlocks_data()
{
    QTest::addColumn<int>("wordCount");
    QTest::newRow("10k") << 10'000;
    QTest::newRow("100k") << 100'000;
}

void EditorPerformanceTest::updatesOnlyAffectedStatisticsBlocks()
{
    QFETCH(int, wordCount);

    MarkdownDocument document(proseWithWordCount(wordCount));
    DocumentStatistics statistics(&document);
    QCOMPARE(statistics.wordCount(), wordCount);

    QSignalSpy updateSpy(&statistics, &DocumentStatistics::blocksRecalculated);
    QTextCursor cursor(&document);
    cursor.setPosition(document.characterCount() / 2);

    QElapsedTimer editTimer;
    editTimer.start();
    cursor.insertText(QStringLiteral("x"));
    const qint64 editMicroseconds = editTimer.nsecsElapsed() / 1000;

    QCOMPARE(updateSpy.count(), 1);
    QCOMPARE(updateSpy.first().at(0).toInt(), 1);
    QCOMPARE(updateSpy.first().at(1).toBool(), false);
    QCOMPARE(statistics.wordCount(), wordCount);
    qInfo().noquote() << QStringLiteral("incremental-statistics %1 words: %2 block in %3 ms")
                             .arg(wordCount)
                             .arg(updateSpy.first().at(0).toInt())
                             .arg(editMicroseconds / 1000.0, 0, 'f', 3);
    QVERIFY2(editMicroseconds < 50'000, qPrintable(QStringLiteral("one-block statistics update took %1 us").arg(editMicroseconds)));
}

void EditorPerformanceTest::statisticsRemainExactAcrossStructuralEdits()
{
    MarkdownDocument document(QStringLiteral("alpha beta.\ngamma delta.\n\nlast line"));
    DocumentStatistics statistics(&document);
    compareWithFreshStatistics(document, statistics);

    QTextCursor cursor(&document);
    cursor.setPosition(5);
    cursor.insertText(QStringLiteral(" new"));
    compareWithFreshStatistics(document, statistics);

    cursor.setPosition(10);
    cursor.insertBlock();
    compareWithFreshStatistics(document, statistics);

    QTextBlock firstBlock = document.firstBlock();
    cursor.setPosition(firstBlock.position() + firstBlock.length() - 1);
    cursor.deleteChar();
    compareWithFreshStatistics(document, statistics);

    QTextBlock secondBlock = document.firstBlock().next();
    QVERIFY(secondBlock.isValid());
    cursor.setPosition(document.firstBlock().position() + 2);
    cursor.setPosition(secondBlock.position() + 3, QTextCursor::KeepAnchor);
    cursor.removeSelectedText();
    compareWithFreshStatistics(document, statistics);

    cursor.setPosition(1);
    cursor.setPosition(qMin(document.characterCount() - 1, document.lastBlock().position() + 4), QTextCursor::KeepAnchor);
    cursor.insertText(QStringLiteral("joined replacement"));
    compareWithFreshStatistics(document, statistics);

    document.setPlainText(QStringLiteral("replacement words here.\n\nAnother paragraph!"));
    compareWithFreshStatistics(document, statistics);

    document.clear();
    compareWithFreshStatistics(document, statistics);
}

QTEST_MAIN(EditorPerformanceTest)

#include "editorperformancetest.moc"
