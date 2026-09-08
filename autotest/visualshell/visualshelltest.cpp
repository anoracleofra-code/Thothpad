// SPDX-License-Identifier: GPL-3.0-or-later
#include "mainwindow.h"
#include "prose/credentialstore.h"
#include "prose/writerengineclient.h"
#include "statistics/writingworkbench.h"
#include "story/agentedittransactionmanager.h"
#include "story/storyintelligencecontroller.h"
#include "story/storyintelligencewidget.h"
#include "story/storylabdialog.h"
#include "story/storytoolharness.h"
#include <QApplication>
#include <QComboBox>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QDockWidget>
#include <QFile>
#include <QFontDatabase>
#include <QInputDialog>
#include <QJsonDocument>
#include <QKeySequence>
#include <QLabel>
#include <QLineEdit>
#include <QListView>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QScrollArea>
#include <QScrollBar>
#include <QSettings>
#include <QSignalSpy>
#include <QStandardPaths>
#include <QTabWidget>
#include <QTableWidget>
#include <QTemporaryDir>
#include <QTest>
#include <QTimer>
#include <QToolButton>
#include <QTreeWidget>

using namespace ghostwriter;

class VisualShellTest : public QObject
{
    Q_OBJECT
private slots:
    void storyEngineToolBridgePreservesNativeEpistemicAuthority()
    {
        QTemporaryDir dir;
        QVERIFY(dir.isValid());
        MainWindow window;
        auto *editor = window.mainEditor();
        auto *doc = static_cast<MarkdownDocument *>(editor->document());
        doc->setFilePath(dir.filePath(QStringLiteral("chapter.md")));
        editor->setPlainText(QStringLiteral("# Chapter One\n\nMara finds the bell."));
        editor->ensureDocumentParsed();
        QTRY_VERIFY(editor->isDocumentParsed());

        StoryIntelligenceWidget widget(&window);
        WriterEngineClient engine;
        CredentialStore credentials;
        StoryIntelligenceController controller(editor, &widget, &engine, &credentials);
        controller.m_projectRoot = dir.path();
        controller.m_epistemicMode = QStringLiteral("cold_reader");
        controller.m_pendingChat.prompt = QStringLiteral("What can the reader know?");
        controller.m_pendingChat.activeStoryUnit = QStringLiteral("trusted-unit");
        const QJsonArray coldManifest = controller.storyEngineReadManifest();
        // Tool exposure is runtime-gated. This test intentionally uses an
        // unstarted engine client, so no Story Engine tools should be exposed.
        // The assertions below exercise the epistemic argument hardening
        // independently of sidecar availability.
        QVERIFY(coldManifest.isEmpty());

        const QJsonObject contextArgs =
            controller.boundedStoryEngineArguments(QStringLiteral("query_project_story_context"),
                                                   QJsonObject{{QStringLiteral("prompt"), QStringLiteral("Inspect this")},
                                                               {QStringLiteral("mode"), QStringLiteral("author_omniscient")},
                                                               {QStringLiteral("active_story_unit"), QStringLiteral("attacker-unit")},
                                                               {QStringLiteral("branch_id"), QStringLiteral("ALT-999")},
                                                               {QStringLiteral("maximum_chars"), 250000}});
        QCOMPARE(contextArgs.value(QStringLiteral("mode")).toString(), QStringLiteral("cold_reader"));
        QCOMPARE(contextArgs.value(QStringLiteral("active_story_unit")).toString(), QStringLiteral("trusted-unit"));
        QCOMPARE(contextArgs.value(QStringLiteral("maximum_chars")).toInt(), 40000);
        QCOMPARE(contextArgs.value(QStringLiteral("branch_id")).toString(), QStringLiteral("mainline"));
        QCOMPARE(controller.backendStoryEngineToolId(QStringLiteral("query_project_story_context")), QStringLiteral("get_story_context"));

        const QJsonObject claimsArgs = controller.boundedStoryEngineArguments(QStringLiteral("query_claims"),
                                                                              QJsonObject{{QStringLiteral("entity"), QStringLiteral("Mara")},
                                                                                          {QStringLiteral("branch_id"), QStringLiteral("ALT-999")},
                                                                                          {QStringLiteral("limit"), 9999}});
        QCOMPARE(claimsArgs.value(QStringLiteral("entity")).toString(), QStringLiteral("Mara"));
        QCOMPARE(claimsArgs.value(QStringLiteral("limit")).toInt(), 100);
        QVERIFY(!claimsArgs.contains(QStringLiteral("branch_id")));

        controller.m_epistemicMode = QStringLiteral("author_omniscient");
        const QJsonArray authorManifest = controller.storyEngineReadManifest();
        QVERIFY(authorManifest.isEmpty());
        const QJsonObject causalArgs = controller.boundedStoryEngineArguments(QStringLiteral("trace_causality"),
                                                                              QJsonObject{{QStringLiteral("record_kind"), QStringLiteral("decision")},
                                                                                          {QStringLiteral("record_id"), QStringLiteral("D1")},
                                                                                          {QStringLiteral("direction"), QStringLiteral("omniscient-hack")},
                                                                                          {QStringLiteral("maximum_depth"), 999},
                                                                                          {QStringLiteral("branch_id"), QStringLiteral("ALT-999")}});
        QCOMPARE(causalArgs.value(QStringLiteral("direction")).toString(), QStringLiteral("both"));
        QCOMPARE(causalArgs.value(QStringLiteral("maximum_depth")).toInt(), 32);
        QVERIFY(!causalArgs.contains(QStringLiteral("branch_id")));

        const QString oversizedKind(400, QLatin1Char('k'));
        const QString oversizedId(600, QLatin1Char('i'));
        const QJsonObject whyArgs = controller.boundedStoryEngineArguments(QStringLiteral("explain_story_record"),
                                                                           QJsonObject{{QStringLiteral("record_kind"), oversizedKind},
                                                                                       {QStringLiteral("record_id"), oversizedId},
                                                                                       {QStringLiteral("branch_id"), QStringLiteral("ALT-ATTACK")}});
        QCOMPARE(whyArgs.value(QStringLiteral("record_kind")).toString().size(), 120);
        QCOMPARE(whyArgs.value(QStringLiteral("record_id")).toString().size(), 240);
        QVERIFY(!whyArgs.contains(QStringLiteral("branch_id")));

        const QJsonObject acceptanceArgs = controller.boundedStoryEngineArguments(
            QStringLiteral("run_operational_acceptance"),
            QJsonObject{{QStringLiteral("prompt"), QString(6000, QLatin1Char('p'))}, {QStringLiteral("branch_id"), QStringLiteral("ALT-ATTACK")}});
        QCOMPARE(acceptanceArgs.value(QStringLiteral("prompt")).toString().size(), 4000);
        QVERIFY(!acceptanceArgs.contains(QStringLiteral("branch_id")));

        controller.m_activeBranch = QStringLiteral("ALT-TRUSTED");
        const QJsonObject diagnosticArgs = controller.boundedStoryEngineArguments(
            QStringLiteral("get_security_audit"),
            QJsonObject{{QStringLiteral("branch_id"), QStringLiteral("ALT-ATTACK")}, {QStringLiteral("unexpected"), QStringLiteral("ignored")}});
        QCOMPARE(diagnosticArgs.value(QStringLiteral("branch_id")).toString(), QStringLiteral("ALT-TRUSTED"));
        QVERIFY(!diagnosticArgs.contains(QStringLiteral("unexpected")));
        controller.m_activeBranch = QStringLiteral("mainline");
        controller.m_epistemicMode = QStringLiteral("cold_reader");

        controller.m_pendingChat.storyEngineTool = StoryIntelligenceController::PendingStoryEngineTool{
            true,
            QStringLiteral("story-request"),
            QStringLiteral("story-call"),
            QStringLiteral("query_project_story_context"),
            controller.m_projectRoot,
            controller.currentDocumentPath(),
            controller.currentStoryContextHash(),
            controller.m_revision,
        };
        // This unit test exercises the bridge response path without starting a
        // provider turn; production resumes the same non-empty co-writer turn.
        controller.m_pendingChat.prompt.clear();
        controller.handleResponse(
            QStringLiteral("story-request"),
            QJsonObject{{QStringLiteral("ok"), true}, {QStringLiteral("result"), QJsonObject{{QStringLiteral("context"), QJsonArray{}}}}});
        QVERIFY(!controller.m_pendingChat.storyEngineTool.active);
        QCOMPARE(controller.m_pendingChat.toolResults.size(), 1);
        QVERIFY(controller.m_pendingChat.toolResults.last().toObject().value(QStringLiteral("ok")).toBool());

        controller.m_pendingChat.storyEngineTool = StoryIntelligenceController::PendingStoryEngineTool{
            true,
            QStringLiteral("stale-request"),
            QStringLiteral("stale-call"),
            QStringLiteral("query_project_story_context"),
            controller.m_projectRoot,
            controller.currentDocumentPath(),
            controller.currentStoryContextHash(),
            controller.m_revision,
        };
        controller.m_epistemicMode = QStringLiteral("reader");
        controller.handleResponse(
            QStringLiteral("stale-request"),
            QJsonObject{{QStringLiteral("ok"), true}, {QStringLiteral("result"), QJsonObject{{QStringLiteral("context"), QJsonArray{}}}}});
        const QJsonObject stale = controller.m_pendingChat.toolResults.last().toObject();
        QVERIFY(!stale.value(QStringLiteral("ok")).toBool());
        QVERIFY(stale.value(QStringLiteral("stale")).toBool());
    }

    void storyLabReleaseControlsRemainKeyboardAccessible()
    {
        WriterEngineClient engine;
        StoryLabDialog dialog(&engine);

        auto *tabs = dialog.findChild<QTabWidget *>(QStringLiteral("storyLabTabs"));
        auto *readerRun = dialog.findChild<QPushButton *>(QStringLiteral("storyLabReaderRun"));
        auto *advancedRun = dialog.findChild<QPushButton *>(QStringLiteral("storyLabAdvancedRun"));
        auto *advancedOutput = dialog.findChild<QPlainTextEdit *>(QStringLiteral("storyLabAdvancedOutput"));
        auto *proposalTable = dialog.findChild<QTableWidget *>(QStringLiteral("storyLabProposalTable"));
        auto *backup = dialog.findChild<QPushButton *>(QStringLiteral("storyLabStateBackup"));
        auto *recover = dialog.findChild<QPushButton *>(QStringLiteral("storyLabStateRecover"));
        auto *restore = dialog.findChild<QPushButton *>(QStringLiteral("storyLabStateRestore"));

        QVERIFY(tabs && readerRun && advancedRun && advancedOutput && proposalTable && backup && recover && restore);
        QVERIFY(!tabs->accessibleName().isEmpty());
        QVERIFY(!readerRun->accessibleName().isEmpty());
        QVERIFY(!advancedRun->accessibleName().isEmpty());
        QVERIFY(!advancedOutput->accessibleName().isEmpty());
        QVERIFY(!proposalTable->accessibleName().isEmpty());
        QVERIFY(!backup->accessibleName().isEmpty());
        QVERIFY(!recover->accessibleName().isEmpty());
        QVERIFY(!restore->accessibleName().isEmpty());
        QCOMPARE(readerRun->shortcut(), QKeySequence(QStringLiteral("Alt+R")));
        QCOMPARE(advancedRun->shortcut(), QKeySequence(QStringLiteral("Alt+A")));
        QVERIFY(readerRun->focusPolicy() != Qt::NoFocus);
        QVERIFY(advancedRun->focusPolicy() != Qt::NoFocus);
    }

    void windowDestructionWithoutCloseDoesNotReloadOutline()
    {
        MainWindow window;
        window.mainEditor()->setPlainText(QStringLiteral("# A heading\n\nA paragraph."));
        window.mainEditor()->ensureDocumentParsed();
        QTRY_VERIFY(window.mainEditor()->isDocumentParsed());
        window.mainEditor()->document()->setModified(false);
        // Exercise destruction without closeEvent, including assertion unwinding.
    }

    void writingTabsNavigateEditAndPersist()
    {
        QTemporaryDir dir;
        MainWindow window;
        window.resize(1500, 1050);
        window.show();
        auto *editor = window.mainEditor();
        auto *doc = static_cast<MarkdownDocument *>(editor->document());
        doc->setFilePath(dir.filePath("dialogue.md"));
        const QString manuscript =
            "# Chapter One\n\nMara said, \"Stay here.\"\n\n## At the gate\n\nBezu asked, \"Why here?\"\n\n\"No one knows.\"\n\n# Chapter Two\n\nMara said, "
            "\"Come here.\"";
        editor->setPlainText(manuscript);
        editor->ensureDocumentParsed();
        StoryIntelligenceWidget storyWidget(&window);
        storyWidget.hide();
        WriterEngineClient engine;
        CredentialStore credentials;
        StoryIntelligenceController controller(editor, &storyWidget, &engine, &credentials);
        AgentEditTransactionManager transactions(editor, window.mainDocumentManager());
        controller.start();
        auto mara = StoryWorkspace::agentTemplate("character");
        mara.insert("name", "Mara");
        auto bezu = StoryWorkspace::agentTemplate("character");
        bezu.insert("name", "Bezu");
        controller.m_workspace.putAgent(mara);
        controller.m_workspace.putAgent(bezu);
        QVERIFY(controller.saveWorkspace());
        auto *prose = window.mainProseAwarenessWidget();
        WritingWorkbench workbench(editor, prose, &controller, &transactions);
        const auto clickTab = [prose](const QString &title) {
            for (auto *b : prose->findChildren<QToolButton *>("writingWorkspaceTab"))
                if (b->text() == title) {
                    b->click();
                    return;
                }
            QFAIL("Missing writing tab");
        };
        clickTab("Dialogue");
        auto *lines = prose->findChild<QTreeWidget *>("dialogueResults");
        QTRY_COMPARE(lines->topLevelItemCount(), 4);
        QCOMPARE(lines->topLevelItem(0)->data(0, Qt::UserRole).toJsonObject().value("speaker").toString(), QString("Mara"));
        auto *speaker = prose->findChild<QComboBox *>("dialogueSpeaker");
        speaker->setCurrentIndex(speaker->findData(mara.value("id").toString()));
        QTRY_COMPARE(lines->topLevelItemCount(), 2);
        lines->setCurrentItem(lines->topLevelItem(0));
        const auto clickAction = [&workbench, lines](const QString &title) {
            auto *card = lines->currentItem() ? lines->itemWidget(lines->currentItem(), 0) : nullptr;
            if (card && (title == "Go to" || title == "Edit line…")) {
                auto *b = card->findChild<QToolButton *>(title == "Go to" ? "dialogueLocate" : "dialogueEdit");
                QVERIFY(b);
                b->click();
                return;
            }
            if (card && title == "Speaker…") {
                card->findChild<QPushButton *>("dialogueAssign")->click();
                return;
            }
            for (auto *b : workbench.dialoguePage()->findChildren<QPushButton *>())
                if (b->text() == title) {
                    b->click();
                    return;
                }
            QFAIL("Missing dialogue action");
        };
        clickAction("Go to");
        QCOMPARE(editor->textCursor().selectedText(), QString("\"Stay here.\""));
        clickAction("Edit line…");
        QVERIFY(workbench.m_inlineEditor);
        workbench.m_inlineEditor->setPlainText("Wait here.");
        clickAction("Save");
        QTRY_VERIFY(editor->toPlainText().contains("Mara said, \"Wait here.\""));
        QCOMPARE(transactions.recentTransactions().size(), 1);
        editor->undo();
        QCOMPARE(editor->toPlainText(), manuscript);
        workbench.refresh();
        QTRY_VERIFY(workbench.current());
        QTRY_COMPARE(lines->topLevelItem(0)->data(0, Qt::UserRole).toJsonObject().value("content").toString(), QString("Stay here."));
        lines->setCurrentItem(lines->topLevelItem(0));
        clickAction("Edit line…");
        editor->moveCursor(QTextCursor::End);
        editor->insertPlainText(" Changed while editing.");
        workbench.m_inlineEditor->setPlainText("Must not apply");
        workbench.refresh();
        QVERIFY(workbench.m_inlineEditor);
        clickAction("Save");
        QCOMPARE(workbench.m_inlineEditor->toPlainText(), QString("Must not apply"));
        QVERIFY(!editor->toPlainText().contains("Must not apply"));
        clickAction("Cancel");
        editor->undo();
        QCOMPARE(editor->toPlainText(), manuscript);
        workbench.refresh();
        QTRY_VERIFY(workbench.current());
        QTRY_VERIFY(workbench.dialoguePage()->isVisible());
        // New metadata is persisted with the same workspace and survives reload.
        QTest::qWait(150);
        QTimer::singleShot(0, [] {
            auto *dialog = qobject_cast<QInputDialog *>(QApplication::activeModalWidget());
            QVERIFY(dialog);
            dialog->setTextValue("M\nMara Vale");
            dialog->accept();
        });
        clickAction("Aliases…");
        QTRY_VERIFY(controller.m_workspace.data.value("writing_review").toObject().value("aliases").toObject().contains(mara.value("id").toString()));
        StoryWorkspace disk;
        QString error;
        QVERIFY(disk.open(controller.m_workspace.path(), {}, &error));
        QCOMPARE(disk.data.value("writing_review"), controller.m_workspace.data.value("writing_review"));
        workbench.refresh();
        QTRY_VERIFY(workbench.current());
        // Bulk replacement is confined to the selected character and one Undo block.
        QTimer::singleShot(0, [] {
            auto *dialog = qobject_cast<QDialog *>(QApplication::activeModalWidget());
            QVERIFY(dialog);
            const auto inputs = dialog->findChildren<QLineEdit *>();
            QCOMPARE(inputs.size(), 2);
            inputs[0]->setText("here");
            inputs[1]->setText("there");
            auto *preview = dialog->findChild<QPlainTextEdit *>();
            QVERIFY(preview);
            QVERIFY(preview->toPlainText().startsWith("2 lines will change"));
            QVERIFY(!preview->toPlainText().contains("Why here?"));
            dialog->findChild<QDialogButtonBox *>()->button(QDialogButtonBox::Apply)->click();
        });
        clickAction("Find / replace in these lines…");
        QVERIFY(editor->toPlainText().contains("Stay there."));
        QVERIFY(editor->toPlainText().contains("Come there."));
        QVERIFY(editor->toPlainText().contains("Why here?"));
        editor->undo();
        QCOMPARE(editor->toPlainText(), manuscript);
        workbench.refresh();
        QTRY_VERIFY(workbench.current());
        // A chapter heading alone must never masquerade as a scene.
        editor->moveCursor(QTextCursor::Start);
        workbench.m_dialogueScope->setCurrentIndex(workbench.m_dialogueScope->findData("*scene"));
        QCOMPARE(lines->topLevelItemCount(), 0);
        QVERIFY(workbench.scopeId(workbench.m_dialogueScope).isEmpty());
        workbench.m_dialogueScope->setCurrentIndex(workbench.m_dialogueScope->findData("*chapter"));
        QCOMPARE(lines->topLevelItemCount(), 1);
        workbench.m_dialogueScope->setCurrentIndex(0);
        // Manual speaker corrections are saved without changing manuscript text.
        speaker->setCurrentIndex(speaker->findData("unknown"));
        QCOMPARE(lines->topLevelItemCount(), 1);
        lines->setCurrentItem(lines->topLevelItem(0));
        QTimer::singleShot(0, [] {
            auto *dialog = qobject_cast<QInputDialog *>(QApplication::activeModalWidget());
            QVERIFY(dialog);
            auto *combo = dialog->findChild<QComboBox *>();
            QVERIFY(combo);
            combo->setCurrentText("Bezu");
            dialog->accept();
        });
        clickAction("Speaker…");
        workbench.refresh();
        QTRY_VERIFY(workbench.current());
        QCOMPARE(lines->topLevelItemCount(), 0);
        QCOMPARE(editor->toPlainText(), manuscript);
        speaker->setCurrentIndex(speaker->findData(bezu.value("id").toString()));
        QCOMPARE(lines->topLevelItemCount(), 2);
        QCOMPARE(lines->topLevelItem(1)->data(0, Qt::UserRole).toJsonObject().value("confidence").toString(), QString("Confirmed by you"));
        lines->setCurrentItem(lines->topLevelItem(1));
        clickAction("Edit line…");
        QVERIFY(workbench.m_inlineEditor);
        workbench.m_inlineEditor->setPlainText("No one is certain.");
        clickAction("Save");
        workbench.refresh();
        QTRY_VERIFY(workbench.current());
        QCOMPARE(lines->topLevelItemCount(), 2);
        QCOMPARE(lines->topLevelItem(1)->data(0, Qt::UserRole).toJsonObject().value("confidence").toString(), QString("Confirmed by you"));
        editor->undo();
        QCOMPARE(editor->toPlainText(), manuscript);
        workbench.refresh();
        QTRY_VERIFY(workbench.current());
        QCOMPARE(lines->topLevelItemCount(), 2);
        QCOMPARE(lines->topLevelItem(1)->data(0, Qt::UserRole).toJsonObject().value("confidence").toString(), QString("Confirmed by you"));
        clickTab("Analytics");
        auto *reports = prose->findChild<QTreeWidget *>("writingAnalyticsResults");
        QTRY_VERIFY(reports->topLevelItemCount() > 0);
        QTRY_VERIFY(workbench.current());
        workbench.saveSnapshot();
        workbench.refresh();
        QTRY_VERIFY(workbench.current());
        QCOMPARE(controller.m_workspace.data.value("writing_review").toObject().value("snapshots").toArray().size(), 1);
        QCOMPARE(workbench.m_reportKind->currentIndex(), 7);
        workbench.m_reportKind->setCurrentIndex(0);
        QVERIFY(window.grab().save("writing-analytics.png"));
        // Narrow header buttons and the collapse control must all remain on-screen.
        for (auto *b : prose->findChildren<QToolButton *>("writingWorkspaceTab"))
            QVERIFY(prose->rect().contains(QRect(b->mapTo(prose, QPoint()), b->size())));
        clickTab("Dialogue");
        QTest::qWait(100);
        QVERIFY(window.grab().save("writing-dialogue.png"));
        // Detected names can become real character cards through the same workspace.
        editor->moveCursor(QTextCursor::End);
        editor->insertPlainText("\n\nTala said, \"Follow me.\"");
        workbench.refresh();
        QTRY_VERIFY(workbench.current());
        const int detected = speaker->findData("detected:tala");
        QVERIFY(detected >= 0);
        speaker->setCurrentIndex(detected);
        QCOMPARE(lines->topLevelItemCount(), 1);
        workbench.m_saveCharacter->click();
        workbench.refresh();
        QTRY_VERIFY(workbench.current());
        bool foundTala = false;
        for (const auto &v : controller.m_workspace.data.value("agents").toArray())
            if (v.toObject().value("name").toString() == "Tala")
                foundTala = true;
        QVERIFY(foundTala);
        QTRY_COMPARE(speaker->findData("detected:tala"), -1);
        editor->moveCursor(QTextCursor::End);
        editor->insertPlainText("\n\nTala said, \"" + QString("A longer line needs room to wrap without hiding its words. ").repeated(8).trimmed() + "\"");
        workbench.refresh();
        QTRY_VERIFY(workbench.current());
        speaker->setCurrentIndex(0);
        auto *last = lines->topLevelItem(lines->topLevelItemCount() - 1);
        auto *card = lines->itemWidget(last, 0);
        QVERIFY(card);
        lines->scrollToItem(last);
        QTest::qWait(50);
        auto *words = card->findChild<QLabel *>("dialogueWords");
        QVERIFY(words);
        QVERIFY(window.grab().save("writing-dialogue-wrapped.png"));
        QVERIFY(words->height() >= words->heightForWidth(words->width()));
        QVERIFY(card->rect().contains(words->geometry()));
        QVERIFY(window.grab().save("writing-dialogue-wrapped.png"));
        doc->setModified(false);
        QVERIFY(window.close());
    }

    void chatActionsPersistAndReplayThroughTheEngine()
    {
        QTemporaryDir dir;
        MainWindow window;
        window.show();
        auto *editor = window.mainEditor();
        auto *doc = qobject_cast<MarkdownDocument *>(editor->document());
        QVERIFY(doc);
        doc->setFilePath(dir.filePath("chat.md"));
        editor->setPlainText("# Chapter one\n\nA chapter without scenes.\n\n## Scene A\n\nA real scene.");
        editor->ensureDocumentParsed();
        QTRY_VERIFY(editor->isDocumentParsed());
        StoryIntelligenceWidget widget(&window);
        WriterEngineClient engine;
        CredentialStore credentials;
        StoryIntelligenceController controller(editor, &widget, &engine, &credentials);
        controller.start();
        widget.scopeModeChanged("scene");
        auto *heading = widget.findChild<QLabel *>("storyContextHeadingLabel");
        QVERIFY(heading->text().startsWith("Chapter:"));
        QCOMPARE(controller.m_scopeMode, QString("chapter"));
        QVERIFY(heading->text().contains("Chapter: Chapter one"));
        widget.scopeModeChanged("manuscript");
        engine.setEnginePath(QStringLiteral(STORY_CHAT_FIXTURE));
        engine.start();
        QTRY_VERIFY(engine.isReady());
        controller.sendChat("First prompt");
        QTRY_COMPARE(controller.m_history.size(), 2);
        const auto originalSession = controller.m_sessionId;
        controller.sendChat("Later prompt that must not leak into a retry");
        QTRY_COMPARE(controller.m_history.size(), 4);
        const auto originalHistory = controller.m_history;
        widget.messageActionRequested(originalHistory[1].toObject().value("id").toString(), "retry");
        QTRY_COMPARE(controller.m_history.size(), 2);
        QVERIFY(controller.m_sessionId != originalSession);
        QCOMPARE(controller.m_history.last().toObject().value("content").toString(), QString("Reply to [First prompt], prior messages: 0"));
        QTimer::singleShot(0, []() {
            auto *dialog = qobject_cast<QInputDialog *>(QApplication::activeModalWidget());
            QVERIFY(dialog);
            dialog->setTextValue("A revised prompt");
            dialog->accept();
        });
        widget.messageActionRequested(controller.m_history.first().toObject().value("id").toString(), "edit");
        QTRY_COMPARE(controller.m_history.size(), 2);
        QCOMPARE(controller.m_history.last().toObject().value("content").toString(), QString("Reply to [A revised prompt], prior messages: 0"));
        QVERIFY(controller.reviseChatHistory(1, "edit", "Author-edited answer"));
        QCOMPARE(controller.m_history.size(), 2);
        QCOMPARE(controller.m_history.last().toObject().value("content").toString(), QString("Author-edited answer"));
        QVERIFY(controller.m_history.last().toObject().value("edited").toBool());
        const auto answerId = controller.m_history.last().toObject().value("id").toString();
        controller.m_workspace.data.insert("markers",
                                           QJsonArray{QJsonObject{{"id", "owned"}, {"session_id", controller.m_sessionId}, {"message_id", answerId}}});
        QTimer::singleShot(0, []() {
            auto *box = qobject_cast<QMessageBox *>(QApplication::activeModalWidget());
            QVERIFY(box);
            box->button(QMessageBox::No)->click();
        });
        widget.messageActionRequested(answerId, "delete");
        QCOMPARE(controller.m_history.size(), 2);
        QTimer::singleShot(0, []() {
            auto *box = qobject_cast<QMessageBox *>(QApplication::activeModalWidget());
            QVERIFY(box);
            box->button(QMessageBox::Yes)->click();
        });
        widget.messageActionRequested(answerId, "delete");
        QCOMPARE(controller.m_history.size(), 1);
        QVERIFY(controller.m_workspace.data.value("markers").toArray().isEmpty());
        QVERIFY(controller.reviseChatHistory(0, "delete"));
        QVERIFY(controller.m_history.isEmpty());
        const auto emptySession = controller.m_sessionId;
        StoryWorkspace disk;
        QString error;
        QVERIFY(disk.open(controller.m_workspace.path(), {}, &error));
        bool foundEmpty = false;
        for (const auto &v : disk.data.value("sessions").toArray()) {
            const auto s = v.toObject();
            if (s.value("id").toString() == emptySession) {
                foundEmpty = true;
                QVERIFY(s.value("messages").toArray().isEmpty());
            }
            if (s.value("id").toString() == originalSession)
                QCOMPARE(s.value("messages").toArray(), originalHistory);
        }
        QVERIFY(foundEmpty);
        controller.selectSession(originalSession);
        QCOMPARE(controller.m_history, originalHistory);
        controller.newSession();
        controller.sendChat("fail-once");
        QTRY_VERIFY(controller.m_pendingChat.prompt.isEmpty());
        QCOMPARE(controller.m_history.size(), 1);
        widget.messageActionRequested({}, "retry");
        QTRY_COMPARE(controller.m_history.size(), 2);
        QCOMPARE(controller.m_history.last().toObject().value("content").toString(), QString("Reply to [fail-once], prior messages: 0"));
        controller.selectSession(originalSession);
        engine.stop();
        QTRY_VERIFY(!engine.isReady());
        QVERIFY(!controller.reviseChatHistory(1, "retry"));
        QCOMPARE(controller.m_history, originalHistory);
        QCOMPARE(controller.m_sessionId, originalSession);
        QVERIFY(disk.open(controller.m_workspace.path(), {}, &error));
        disk.data.insert("external_revision", 1);
        QVERIFY(disk.save(&error));
        QVERIFY(!controller.reviseChatHistory(0, "delete"));
        QCOMPARE(controller.m_history, originalHistory);
        doc->setModified(false);
        QVERIFY(window.close());
    }

    void workspaceWiringPreservesScopesSessionsAndSafeEdits()
    {
        QTemporaryDir dir;
        MainWindow window;
        window.show();
        QTest::qWait(100);
        auto *editor = window.mainEditor();
        auto *doc = qobject_cast<MarkdownDocument *>(editor->document());
        QVERIFY(doc);
        const QString manuscript = "# One\n\nThe lamp burned low.\n\n## Scene\n\nA quiet evening.\n\n# Two\n\nRain arrived.";
        doc->setFilePath(dir.filePath("novel.md"));
        editor->setPlainText(manuscript);
        editor->ensureDocumentParsed();
        QTRY_VERIFY(editor->isDocumentParsed());
        QCOMPARE(editor->toPlainText(), manuscript);
        StoryIntelligenceWidget widget(&window);
        widget.hide();
        WriterEngineClient engine;
        CredentialStore credentials;
        AgentEditTransactionManager transactions(editor, window.mainDocumentManager());
        StoryToolHarness harness(&window, editor, window.mainDocumentManager(), window.mainProseController(), window.mainProseAwarenessWidget(), &transactions);
        StoryIntelligenceController controller(editor, &widget, &engine, &credentials);
        controller.setToolServices(&harness, &transactions, nullptr);
        controller.start();
        QVERIFY(controller.m_workspace.path().endsWith(".thothpad/novel.md.story.json"));
        auto a = StoryWorkspace::agentTemplate("character");
        a.insert("name", "Mara");
        a.insert("knowledge", "Mara private knowledge");
        auto b = StoryWorkspace::agentTemplate("character");
        b.insert("name", "Bezu");
        b.insert("knowledge", "Bezu private knowledge");
        controller.m_workspace.putAgent(a);
        controller.m_workspace.putAgent(b);
        auto root = controller.m_workspace.scope("manuscript");
        auto settings = root.value("settings").toObject();
        settings.insert("cast", QJsonArray{a.value("id"), b.value("id")});
        root.insert("settings", settings);
        controller.m_workspace.setScope(root);
        controller.refreshWorkspace();
        widget.setActiveCharacter(a.value("id").toString());
        controller.newSession();
        controller.appendHistory("user", "Private conversation with Mara");
        const auto privateSession = controller.m_sessionId;
        QVERIFY(!privateSession.isEmpty());
        auto context = QString::fromUtf8(QJsonDocument(controller.workspaceContext()).toJson());
        QVERIFY(context.contains("Mara private knowledge"));
        QVERIFY(!context.contains("Bezu private knowledge"));
        widget.setActiveCharacter(b.value("id").toString());
        controller.newSession();
        QVERIFY(controller.m_history.isEmpty());
        context = QString::fromUtf8(QJsonDocument(controller.workspaceContext()).toJson());
        QVERIFY(!context.contains("Mara private knowledge"));
        QVERIFY(context.contains("Bezu private knowledge"));
        QVERIFY(!controller.authorizeTool("replace_verified_range", {}));
        for (const auto &v : controller.allowedManifest())
            QVERIFY(v.toObject().value("risk").toString() != "R3" && v.toObject().value("risk").toString() != "R4");
        controller.m_scopeMode = "scene";
        auto cursor = editor->textCursor();
        cursor.setPosition(manuscript.indexOf("quiet"));
        editor->setTextCursor(cursor);
        controller.refreshWorkspace();
        QCOMPARE(editor->toPlainText(), manuscript);
        QCOMPARE(controller.m_workspace.scope(controller.m_scopeId).value("title").toString(), QString("Scene"));
        QCOMPARE(controller.workspaceTool("read_story_scope", {{"scope_id", controller.m_scopeId}}).value("ok").toBool(), true);
        QCOMPARE(controller.workspaceTool("search_manuscript", {{"query", "lamp"}}).value("matches").toArray().size(), 1);
        QCOMPARE(controller.workspaceTool("read_story_scope", {{"scope_id", controller.m_scopeId}, {"offset", -1}}).value("ok").toBool(), false);
        QTimer::singleShot(0, []() {
            auto *dialog = qobject_cast<QDialog *>(QApplication::activeModalWidget());
            QVERIFY(dialog);
            dialog->findChild<QPlainTextEdit *>("storyField_setting")->setPlainText("A moonlit room");
            dialog->findChild<QDialogButtonBox *>()->button(QDialogButtonBox::Save)->click();
        });
        controller.editSceneContext();
        QCOMPARE(controller.workspaceContext().value("scene_context").toObject().value("setting").toString(), QString("A moonlit room"));
        controller.m_scopeMode = "chapter";
        controller.refreshWorkspace();
        const auto chapterScope = controller.m_scopeId;
        widget.newSessionRequested();
        controller.appendHistory("user", "First approach to chapter one");
        const auto firstChapterSession = controller.m_sessionId;
        widget.newSessionRequested();
        controller.appendHistory("user", "Alternative approach to chapter one");
        const auto secondChapterSession = controller.m_sessionId;
        QVERIFY(firstChapterSession != secondChapterSession);
        widget.sessionSelected(firstChapterSession);
        QCOMPARE(controller.m_scopeId, chapterScope);
        QCOMPARE(controller.m_scopeMode, QString("chapter"));
        QCOMPARE(controller.m_history.size(), 1);
        QCOMPARE(controller.m_history.first().toObject().value("content").toString(), QString("First approach to chapter one"));
        auto *sessionPicker = widget.findChild<QComboBox *>("storySessionCombo");
        auto *scopePicker = widget.findChild<QComboBox *>("storyScopeCombo");
        QVERIFY(sessionPicker && scopePicker);
        QCOMPARE(sessionPicker->currentData().toString(), firstChapterSession);
        QVERIFY(sessionPicker->findData(secondChapterSession) >= 0);
        QCOMPARE(scopePicker->currentText(), QString("Current chapter"));
        QCOMPARE(widget.findChild<QLabel *>("storyContextHeadingLabel")->text(), QString("Chapter: One"));
        cursor.setPosition(manuscript.indexOf("Rain"));
        editor->setTextCursor(cursor);
        controller.refreshWorkspace();
        QVERIFY(controller.m_history.isEmpty());
        QCOMPARE(scopePicker->currentText(), QString("Current chapter"));
        QCOMPARE(widget.findChild<QLabel *>("storyContextHeadingLabel")->text(), QString("Chapter: Two"));
        cursor.setPosition(manuscript.indexOf("lamp"));
        editor->setTextCursor(cursor);
        controller.refreshWorkspace();
        QCOMPARE(controller.m_sessionId, firstChapterSession);
        widget.sessionSelected(secondChapterSession);
        QCOMPARE(controller.m_history.first().toObject().value("content").toString(), QString("Alternative approach to chapter one"));
        controller.m_pendingChat.prompt = "In flight";
        widget.sessionSelected(firstChapterSession);
        QCOMPARE(controller.m_sessionId, secondChapterSession);
        controller.resetPendingChat();
        widget.findChild<QToolButton *>("storyNewSessionButton")->click();
        QVERIFY(controller.m_history.isEmpty());
        const auto blankSession = controller.m_sessionId;
        QVERIFY(!blankSession.isEmpty() && blankSession != secondChapterSession);
        QFile persisted(controller.m_workspace.path());
        QVERIFY(persisted.open(QIODevice::ReadOnly));
        const auto savedWorkspace = QJsonDocument::fromJson(persisted.readAll()).object();
        persisted.close();
        QCOMPARE(savedWorkspace.value("active_session").toString(), blankSession);
        QCOMPARE(savedWorkspace.value("last_sessions").toObject().value(chapterScope).toString(), blankSession);
        controller.m_workspace.data.insert("markers", QJsonArray{QJsonObject{{"id", "discarded-mark"}, {"session_id", blankSession}}});
        QTimer::singleShot(0, []() {
            auto *box = qobject_cast<QMessageBox *>(QApplication::activeModalWidget());
            QVERIFY(box);
            box->button(QMessageBox::Cancel)->click();
        });
        controller.deleteSession(blankSession);
        QCOMPARE(controller.m_sessionId, blankSession);
        QTimer::singleShot(0, []() {
            auto *box = qobject_cast<QMessageBox *>(QApplication::activeModalWidget());
            QVERIFY(box);
            box->button(QMessageBox::Yes)->click();
        });
        controller.deleteSession(blankSession);
        QVERIFY(controller.m_sessionId != blankSession);
        QCOMPARE(controller.m_sessionId, secondChapterSession);
        QCOMPARE(controller.m_history.first().toObject().value("content").toString(), QString("Alternative approach to chapter one"));
        bool retainedBlank = false;
        for (const auto &v : controller.m_workspace.data.value("sessions").toArray())
            retainedBlank = retainedBlank || v.toObject().value("id").toString() == blankSession;
        QVERIFY(!retainedBlank);
        for (const auto &v : controller.m_workspace.data.value("markers").toArray())
            QVERIFY(v.toObject().value("session_id").toString() != blankSession);
        widget.sessionSelected(privateSession);
        QCOMPARE(widget.activeCharacterId(), a.value("id").toString());
        QCOMPARE(controller.m_history.first().toObject().value("content").toString(), QString("Private conversation with Mara"));
        widget.sessionSelected(firstChapterSession);
        QVERIFY(widget.activeCharacterId().isEmpty());
        QCOMPARE(controller.m_scopeId, chapterScope);
        settings.insert("cast", QJsonArray{b.value("id")});
        root.insert("settings", settings);
        controller.m_workspace.setScope(root);
        widget.sessionSelected(privateSession);
        QCOMPARE(controller.m_sessionId, firstChapterSession);
        QCOMPARE(controller.m_history.size(), 1);
        QVERIFY(!controller.m_history.first().toObject().value("content").toString().contains("Mara"));
        widget.scopeModeChanged("manuscript");
        QVERIFY(controller.m_history.isEmpty());
        QVERIFY(widget.activeCharacterId().isEmpty());
        widget.sessionSelected(firstChapterSession);
        QCOMPARE(controller.m_sessionId, firstChapterSession);
        settings.insert("cast", QJsonArray{a.value("id"), b.value("id")});
        root.insert("settings", settings);
        controller.m_workspace.setScope(root);
        widget.scopeModeChanged("manuscript");
        widget.setActiveCharacter(QString());
        controller.newSession();
        controller.appendHistory("user", "Mark the lamp.");
        controller.m_pendingChat.prompt = "Mark the lamp.";
        controller.m_pendingChat.revision = controller.m_revision;
        controller.m_pendingChat.storyContextHash = controller.currentStoryContextHash();
        controller.m_pendingChat.speaker = "Co-Writer";
        const int start = manuscript.indexOf("lamp");
        QJsonObject mark{{"id", "test-mark"},
                         {"start_utf16", start},
                         {"end_utf16", start + 4},
                         {"quote", "lamp"},
                         {"comment", "Try a different light source."},
                         {"category", "rewrite"},
                         {"replacement", "candle"},
                         {"document_revision", controller.m_revision}};
        controller.finishChatTurn({{"message", "Consider the lamp."},
                                   {"annotations", QJsonArray{mark}},
                                   {"memory_proposals", QJsonArray{QJsonObject{{"title", "Lighting"}, {"body", "The room is dim."}}}}},
                                  {});
        QCOMPARE(controller.m_history.size(), 2);
        QCOMPARE(controller.m_history.last().toObject().value("references").toArray().size(), 1);
        QCOMPARE(controller.m_workspace.data.value("memories").toArray().size(), 0);
        QCOMPARE(controller.m_workspace.data.value("markers").toArray().size(), 1);
        QVERIFY(widget.findChild<QPushButton *>("storyQuoteLink"));
        QVERIFY(widget.findChild<QPushButton *>("storyProposalButton"));
        editor->setPlainText(manuscript + "\nAn unrelated change.");
        controller.restoreMarkers();
        QVERIFY(controller.m_annotations.first().toObject().value("stale").toBool());
        controller.applySuggestion("test-mark");
        QVERIFY(editor->toPlainText().contains("lamp"));
        QVERIFY(!editor->toPlainText().contains("candle"));
        editor->setPlainText(manuscript);
        controller.restoreMarkers();
        QVERIFY(!controller.m_annotations.first().toObject().value("stale").toBool());
        QTimer::singleShot(0, []() {
            auto *dialog = qobject_cast<QDialog *>(QApplication::activeModalWidget());
            QVERIFY(dialog);
            dialog->findChild<QDialogButtonBox *>()->button(QDialogButtonBox::Apply)->click();
        });
        controller.applySuggestion("test-mark");
        QVERIFY(editor->toPlainText().contains("candle"));
        editor->undo();
        QCOMPARE(editor->toPlainText(), manuscript);
        const auto savedSession = controller.m_sessionId;
        const auto path = doc->filePath();
        doc->clear();
        editor->setPlainText("# Other\n\nAnother book.");
        doc->setFilePath(dir.filePath("other.md"));
        controller.openWorkspaceForDocument();
        QVERIFY(controller.m_workspace.agent(a.value("id").toString()).isEmpty());
        QVERIFY(controller.m_history.isEmpty());
        doc->clear();
        editor->setPlainText(manuscript);
        doc->setFilePath(path);
        editor->ensureDocumentParsed();
        QTRY_VERIFY(editor->isDocumentParsed());
        controller.openWorkspaceForDocument();
        QCOMPARE(controller.m_sessionId, savedSession);
        QCOMPARE(controller.m_history.size(), 2);
        QVERIFY(!controller.m_workspace.agent(a.value("id").toString()).isEmpty());
        bool retained = false;
        for (const auto &v : controller.m_workspace.data.value("sessions").toArray())
            if (v.toObject().value("id").toString() == privateSession)
                retained = true;
        QVERIFY(retained);
        doc->setFilePath(dir.filePath("copy.md"));
        controller.openWorkspaceForDocument();
        QVERIFY(controller.saveWorkspace());
        QCOMPARE(controller.m_history.size(), 2);
        QVERIFY(QFileInfo::exists(dir.filePath(".thothpad/copy.md.story.json")));
        QTimer::singleShot(0, []() {
            auto *dialog = qobject_cast<QDialog *>(QApplication::activeModalWidget());
            QVERIFY(dialog);
            QVERIFY(dialog->grab().save("story-workspace-dialog.png"));
            dialog->reject();
        });
        controller.editWorkspace();
        editor->document()->setModified(false);
        QVERIFY(window.close());
    }
    void shellKeepsManuscriptVisibleWhenEitherPaneResizes()
    {
        MainWindow window;
        window.resize(1920, 1080);
        auto *dock = new QDockWidget(&window);
        dock->setObjectName(QStringLiteral("storyIntelligenceDock"));
        dock->setTitleBarWidget(new QWidget(dock));
        dock->titleBarWidget()->setFixedHeight(0);
        auto *story = new StoryIntelligenceWidget(dock);
        dock->setWidget(story);
        window.addDockWidget(Qt::RightDockWidgetArea, dock);
        window.resizeDocks({dock}, {320}, Qt::Horizontal);
        auto *editor = window.mainEditor();
        const QString paragraph = QStringLiteral(
            "The wind moved through the trees, carrying the scent of rain. "
            "Mara closed her notebook and watched the last light settle on the distant hills. "
            "There was still time to take the long way home.");
        const QString manuscript = QStringLiteral("# **CHAPTER ONE: THE LONG WAY HOME**\n\n") + QString(paragraph + QStringLiteral("\n\n")).repeated(12);
        editor->setPlainText(manuscript);
        auto *prose = window.mainProseAwarenessWidget();
        prose->setProfiles({QStringLiteral("creative-default")}, QStringLiteral("creative-default"));
        prose->setEngineReady(true);
        ProseDiagnostic finding;
        finding.id = QStringLiteral("sample-filter");
        finding.category = QStringLiteral("filter_words");
        finding.ruleId = QStringLiteral("filter.sample");
        finding.excerpt = QStringLiteral("watched");
        finding.explanation = QStringLiteral("Consider whether the perception filter adds distance or serves the character's voice.");
        prose->setDiagnostics({finding});
        prose->selectDiagnostic(finding);
        const QFont font = editor->font();
        story->setProviderSummary(QStringLiteral("OpenRouter"), QStringLiteral("a-provider/a-long-model-name:free"), false, QStringLiteral("openrouter"));
        story->setSceneContext({{QStringLiteral("setting"), QStringLiteral("A quiet valley at the end of summer. Rain gathering beyond the hills.")},
                                {QStringLiteral("goal"), QStringLiteral("Mara must reach home before the storm.")}});
        story->setCharacters({QJsonObject{{QStringLiteral("id"), QStringLiteral("mara")}, {QStringLiteral("name"), QStringLiteral("Mara")}}});
        story->setWorkspaceContext("chapter", "**CHAPTER ONE: THE LONG WAY HOME**", "Co-Writer", "Explore the ending");
        story->setSessions({QJsonObject{{"id", "first"}, {"label", "Explore the ending · Chapter one · Co-Writer"}},
                            QJsonObject{{"id", "second"}, {"label", "Alternate ending · Chapter one · Co-Writer"}}},
                           "first");
        story->appendChatMessage("assistant", "We could let Mara pause at the gate before choosing to go home.", "Co-Writer", {}, "visual-answer");
        window.show();
        QTest::qWait(150);
        QVERIFY(window.grab().save(QStringLiteral("visual-shell-wide.png")));
        auto *leftScroll = prose->findChild<QScrollArea *>("proseAwarenessScrollArea");
        QVERIFY(leftScroll);
        // Font metrics and offscreen window limits differ across platforms.
        // Check the review controls themselves, not a pixel-perfect scroll range.
        auto *findingList = prose->findChild<QListView *>("proseAwarenessFindingView");
        QVERIFY(findingList);
        QVERIFY(leftScroll->viewport()->rect().contains(QRect(findingList->mapTo(leftScroll->viewport(), QPoint()), findingList->size())));
        auto *lensSurface = prose->findChild<QWidget *>("proseAwarenessLensesSurface");
        QVERIFY(lensSurface);
        auto *lensHint = lensSurface->findChild<QLabel *>("proseAwarenessHelperLabel");
        auto *lensList = lensSurface->findChild<QTreeWidget *>();
        QVERIFY(lensHint && lensList);
        QVERIFY(lensList->y() - lensHint->geometry().bottom() <= 8);
        for (const auto *name : {"deleteActionButton", "backObservationButton", "nextObservationButton", "undoActionButton", "findingActionsButton"}) {
            auto *button = prose->findChild<QToolButton *>(name);
            QVERIFY(button);
            QVERIFY(prose->rect().contains(QRect(button->mapTo(prose, QPoint()), button->size())));
        }
        auto *splitter = qobject_cast<QSplitter *>(window.centralWidget());
        QVERIFY(splitter);
        for (int rightWidth : {800, 1100, 320}) {
            window.resizeDocks({dock}, {rightWidth}, Qt::Horizontal);
            QTest::qWait(80);
            QVERIFY(editor->viewport()->width() > 100);
            QVERIFY(editor->cursorRect(QTextCursor(editor->document())).left() >= 0);
            QVERIFY(editor->cursorRect(QTextCursor(editor->document())).left() < editor->viewport()->width());
            QCOMPARE(editor->font(), font);
            QCOMPARE(editor->toPlainText(), manuscript);
            if (rightWidth == 1100) {
                QVERIFY(window.grab().save(QStringLiteral("visual-shell-right-expanded.png")));
            }
        }
        splitter->setSizes({950, 600});
        QTest::qWait(80);
        QVERIFY(editor->viewport()->width() > 100);
        QCOMPARE(editor->toPlainText(), manuscript);
        QVERIFY(window.grab().save(QStringLiteral("visual-shell-narrow.png")));
        for (auto writingWidth : {EditorWidthNarrow, EditorWidthMedium, EditorWidthWide, EditorWidthFull}) {
            editor->setEditorWidth(writingWidth);
            QTest::qWait(20);
            QVERIFY(editor->viewport()->width() > 100);
            QCOMPARE(editor->font(), font);
            QCOMPARE(editor->toPlainText(), manuscript);
        }
        editor->moveCursor(QTextCursor::End);
        editor->insertPlainText(QStringLiteral(" A final sentence."));
        editor->undo();
        QCOMPARE(editor->toPlainText(), manuscript);
        auto *scroll = window.mainProseAwarenessWidget()->findChild<QScrollArea *>(QStringLiteral("proseAwarenessScrollArea"));
        QVERIFY(scroll);
        scroll->verticalScrollBar()->setValue(scroll->verticalScrollBar()->maximum());
        QVERIFY(scroll->widget()->height() >= scroll->viewport()->height());
        // Destruction should not offer to save the synthetic fixture.
        editor->document()->setModified(false);
        QVERIFY(window.close());
    }
};

int main(int argc, char **argv)
{
    QApplication app(argc, argv);
#ifdef Q_OS_WIN
    const QDir fonts(QDir(qEnvironmentVariable("WINDIR")).filePath(QStringLiteral("Fonts")));
    for (const auto &name : {"georgia.ttf", "georgiab.ttf", "segoeui.ttf", "segoeuib.ttf", "cour.ttf"}) {
        QFontDatabase::addApplicationFont(fonts.filePath(QString::fromLatin1(name)));
    }
    app.setFont(QFont(QStringLiteral("Segoe UI"), 10));
#endif
    QCoreApplication::setOrganizationName(QStringLiteral("ThothPadTests"));
    QCoreApplication::setApplicationName(QStringLiteral("VisualShell"));
    QStandardPaths::setTestModeEnabled(true);
    QTemporaryDir settingsDir;
    QSettings::setDefaultFormat(QSettings::IniFormat);
    QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, settingsDir.path());
    QSettings settings;
    settings.setValue(QStringLiteral("Application/welcomeVersion"), QStringLiteral(APPVERSION));
    settings.setValue(QStringLiteral("Application/showWelcomeAfterUpdates"), false);
    settings.setValue(QStringLiteral("Save/autoSave"), false);
    settings.setValue(QStringLiteral("Session/restoreSession"), false);
    qputenv("THOTHPAD_ENGINE", "no-engine-for-visual-test");
    VisualShellTest test;
    return QTest::qExec(&test, argc, argv);
}

#include "visualshelltest.moc"
