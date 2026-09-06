/*
 * SPDX-FileCopyrightText: 2014-2024 Megan Conkle <megan.conkle@kdemail.net>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QAction>
#include <QApplication>
#include <QCommandLineParser>
#include <QCoreApplication>
#include <QDate>
#include <QDateTime>
#include <QDockWidget>
#include <QIcon>
#include <QLibraryInfo>
#include <QLocale>
#include <QMenu>
#include <QMenuBar>
#include <QSettings>
#include <QTextCursor>
#include <QTranslator>
#include <QWindow>

#include <KAboutData>
#include <KToolTipHelper>

#include "documentmanager.h"
#include "editor/markdowneditor.h"
#include "logging.h"
#include "mainwindow.h"
#include "prose/credentialstore.h"
#include "prose/proseawarenesswidget.h"
#include "prose/prosecontroller.h"
#include "prose/writerengineclient.h"
#include "settings/appsettings.h"
#include "story/agentedittransactionmanager.h"
#include "story/documentactivitytracker.h"
#include "story/storyintelligencecontroller.h"
#include "story/storyintelligencewidget.h"
#include "story/storytoolharness.h"

#include "statistics/writingworkbench.h"

namespace
{
void installStoryIntelligence(ghostwriter::MainWindow *window)
{
    auto *editor = window->mainEditor();
    auto *documentManager = window->mainDocumentManager();
    auto *proseController = window->mainProseController();
    auto *proseWidget = window->mainProseAwarenessWidget();
    auto *engine = proseController ? proseController->engineClient() : nullptr;
    auto *credentials = proseController ? proseController->credentialStore() : nullptr;
    if (!editor || !documentManager || !proseController || !proseWidget || !engine || !credentials) {
        qWarning() << "Story Intelligence could not attach to the editor/engine services.";
        return;
    }

    auto *dock = new QDockWidget(window);
    dock->setObjectName(QStringLiteral("storyIntelligenceDock"));
    dock->setAllowedAreas(Qt::RightDockWidgetArea);
    // The dock separator remains draggable; the custom header only replaces the
    // title bar and does not constrain the user's chosen panel width.
    dock->setFeatures(QDockWidget::DockWidgetClosable);
    dock->setMinimumWidth(280);

    // The reference UI carries its own quiet header, so suppress the native
    // dock title bar while retaining QDockWidget's robust edge-layout logic.
    auto *nativeTitleBarReplacement = new QWidget(dock);
    nativeTitleBarReplacement->setFixedHeight(0);
    dock->setTitleBarWidget(nativeTitleBarReplacement);

    auto *widget = new ghostwriter::StoryIntelligenceWidget(dock);
    widget->setCollapseIcon(window->themedIcon(QStringLiteral("collapse-story")));
    dock->setWidget(widget);
    window->addDockWidget(Qt::RightDockWidgetArea, dock);
    // MainWindow restores its state before this optional dock is installed.
    // Restore again now that the named dock exists so Qt can recover its width.
    QSettings windowSettings;
    const QByteArray state = windowSettings.value(QStringLiteral("Window/mainWindowState")).toByteArray();
    if (!state.isEmpty()) {
        window->restoreState(state);
    } else {
        window->resizeDocks({dock}, {360}, Qt::Horizontal);
    }

    auto *transactions = new ghostwriter::AgentEditTransactionManager(
        editor, documentManager, dock);
    auto *activity = new ghostwriter::DocumentActivityTracker(
        editor, transactions, dock);
    auto *harness = new ghostwriter::StoryToolHarness(
        window, editor, documentManager, proseController, proseWidget,
        transactions, dock);

    auto *controller = new ghostwriter::StoryIntelligenceController(
        editor, widget, engine, credentials, dock);
    controller->setToolServices(harness, transactions, activity);
    QObject::connect(
        controller,
        &ghostwriter::StoryIntelligenceController::projectRootChanged,
        dock,
        [transactions, activity, lastRoot = QString()](const QString &root) mutable {
            if (root == lastRoot) {
                return;
            }
            lastRoot = root;
            transactions->resetForContext();
            activity->resetForContext();
        });
    controller->start();
    new ghostwriter::WritingWorkbench(editor, proseWidget, controller, transactions, window);

    // Keep the native services discoverable under the Story Intelligence dock
    // for diagnostics and tests without exposing arbitrary QObject access to
    // the model. The model only sees StoryToolHarness::manifest().
    harness->setObjectName(QStringLiteral("storyToolHarness"));
    transactions->setObjectName(QStringLiteral("agentEditTransactionManager"));
    activity->setObjectName(QStringLiteral("documentActivityTracker"));

    QObject::connect(
        widget,
        &ghostwriter::StoryIntelligenceWidget::annotationNavigationRequested,
        dock,
        [editor, widget](int startUtf16, int endUtf16, const QString &quote) {
            const QString manuscript = editor->toPlainText();
            if (startUtf16 < 0
                || endUtf16 <= startUtf16
                || endUtf16 > manuscript.size()
                || quote.size() != endUtf16 - startUtf16
                || manuscript.mid(startUtf16, endUtf16 - startUtf16) != quote) {
                widget->setStatusMessage(QCoreApplication::translate(
                    "main",
                    "That manuscript mark is stale because the text changed."));
                return;
            }

            QTextCursor cursor(editor->document());
            cursor.setPosition(startUtf16);
            cursor.setPosition(endUtf16, QTextCursor::KeepAnchor);
            editor->setTextCursor(cursor);
            editor->ensureCursorVisible();
            editor->setFocus();
            widget->setStatusMessage(QCoreApplication::translate(
                "main",
                "Marked passage selected"));
        });

    QObject::connect(
        widget,
        &ghostwriter::StoryIntelligenceWidget::undoAgentTransactionRequested,
        dock,
        [transactions, widget](const QString &operationId) {
            const QJsonObject result = transactions->undoTransaction(operationId);
            if (!result.value(QStringLiteral("ok")).toBool()) {
                widget->setStatusMessage(
                    result.value(QStringLiteral("error")).toString(
                        QCoreApplication::translate("main", "Could not safely undo that AI edit.")));
                return;
            }

            const QString summary = result.value(QStringLiteral("summary")).toString().trimmed();
            widget->setStatusMessage(summary.isEmpty()
                ? QCoreApplication::translate("main", "AI edit undone safely")
                : QCoreApplication::translate("main", "Undid AI edit: %1").arg(summary));
        });

    QObject::connect(
        transactions,
        &ghostwriter::AgentEditTransactionManager::transactionApplied,
        dock,
        [widget](const QJsonObject &transaction) {
            if (!transaction.value(QStringLiteral("ok")).toBool()
                || transaction.value(QStringLiteral("no_change")).toBool()) {
                return;
            }
            const QString operationId = transaction.value(QStringLiteral("operation_id")).toString();
            const QString summary = transaction.value(QStringLiteral("summary")).toString().trimmed();
            const int replacementCount = transaction.value(QStringLiteral("replacement_count")).toInt();
            const QString title = summary.isEmpty()
                ? QCoreApplication::translate("main", "AI manuscript edit")
                : summary;
            const QString detail = replacementCount > 0
                ? QCoreApplication::translate(
                      "main",
                      "Applied %1 verified change(s) · recovery checkpoint created · one-step Undo available")
                      .arg(replacementCount)
                : QCoreApplication::translate(
                      "main",
                      "Applied checkpointed native edit · one-step Undo available");
            widget->appendActivityCard(title, detail, operationId);
        });

    QObject::connect(activity, &ghostwriter::DocumentActivityTracker::activityEvent,
                     dock, [widget](const QJsonObject &event) {
        if (!event.value(QStringLiteral("visible")).toBool()) {
            return;
        }
        const QString type = event.value(QStringLiteral("type")).toString();
        const QString summary = event.value(QStringLiteral("summary")).toString();
        QString message;
        if (type == QStringLiteral("USER_UNDID_AGENT_TRANSACTION")) {
            message = QCoreApplication::translate("main", "↶ You undid the AI action: %1").arg(summary);
        } else if (type == QStringLiteral("USER_REDID_AGENT_TRANSACTION")) {
            message = QCoreApplication::translate("main", "↷ You redid the AI action: %1").arg(summary);
        } else if (type == QStringLiteral("USER_EDITED_AGENT_TARGET")) {
            const int line = event.value(QStringLiteral("line")).toInt();
            message = QCoreApplication::translate("main", "You changed a passage the AI had touched%1.")
                .arg(line > 0 ? QCoreApplication::translate("main", " near line %1").arg(line) : QString());
        } else {
            return;
        }
        widget->appendChatMessage(QStringLiteral("assistant"), message, QStringLiteral("ThothPad"));
    });

    QObject::connect(widget, &ghostwriter::StoryIntelligenceWidget::collapseRequested,
                     dock, [dock]() {
                         QSettings().setValue(QStringLiteral("story/visible"), false);
                         dock->hide();
                     });

    QAction *toggleAction = dock->toggleViewAction();
    toggleAction->setText(QCoreApplication::translate("main", "Story Intelligence"));
    toggleAction->setIcon(window->themedIcon(QStringLiteral("story-intelligence")));
    toggleAction->setShortcut(QKeySequence(QStringLiteral("Ctrl+Shift+A")));
    toggleAction->setShortcutContext(Qt::WindowShortcut);
    toggleAction->setToolTip(QCoreApplication::translate("main", "Show or hide the AI writing panel"));
    // Register with the visible window too, so hiding the dock cannot disable
    // the shortcut used to bring it back.
    window->addAction(toggleAction);

    // Keep the two primary side-panel controls together in the View menu.
    // Locate the View menu through the sidebar action's object identity so
    // insertion does not depend on translated menu titles.
    QAction *sidebarAction = window->appAction(ghostwriter::AppActions::ShowSidebar);
    if (nullptr != sidebarAction) {
        const QList<QMenu *> menus = window->findChildren<QMenu *>();
        for (QMenu *menu : menus) {
            const QList<QAction *> viewActions = menu->actions();
            const int sidebarIndex = viewActions.indexOf(sidebarAction);
            if (sidebarIndex < 0) {
                continue;
            }
            // Insert directly under "Show Sidebar", with a separator after
            // the toggle so the sidebar-visibility pair stays grouped
            // before the outline/statistics entries (matches the View-menu
            // group style in MainWindow::setupMenuBar). When the sidebar
            // action is last, insertBefore is null so the toggle appends.
            // (If the sidebar action itself is absent there is no View-menu
            // identity anchor, so the toggle stays unplaced.)
            QAction *insertBefore = (sidebarIndex + 1 < viewActions.size()) ? viewActions.at(sidebarIndex + 1) : nullptr;
            menu->insertAction(insertBefore, toggleAction);
            menu->insertSeparator(insertBefore);
            break;
        }
    }

    QSettings settings;
    const bool visible = settings.value(QStringLiteral("story/visible"), true).toBool();
    dock->setVisible(visible);
    // Save user intent, not visibilityChanged(false) emitted during shutdown.
    QObject::connect(toggleAction, &QAction::triggered, dock, [](bool requestedVisible) {
        QSettings().setValue(QStringLiteral("story/visible"), requestedVisible);
    });
}
}

int main(int argc, char *argv[])
{
    // Set up customized logging.
    qSetMessagePattern(
        "[%{time process} %{pid} %{appname} %{if-category} %{category}%{endif}] "
        "%{if-debug}DEBUG   %{endif}"
        "%{if-info}INFO    %{endif}"
        "%{if-warning}WARNING %{endif}"
        "%{if-critical}CRITICAL%{endif}"
        "%{if-fatal}FATAL   %{endif}"
        "%{if-debug}  %{function}():%{endif}"
        "  %{message}"
        "%{if-debug} (%{file}:%{line})%{endif}");
    qInstallMessageHandler(ghostwriter::logMessage);

    bool disableGPU = false;

    // Unfortunately, we must preparse the arguments for the --disable-gpu
    // option rather than using QCommandLineParser since we must set the
    // software rendering attribute before creating the QApplication.
    //
    for (int i = 0; i < argc; i++) {
        if (0 == strcmp(argv[i], "--disable-gpu")) {
            disableGPU = true;
            break;
        }
    }

    if (disableGPU) {
        QCoreApplication::setAttribute(Qt::AA_UseSoftwareOpenGL);
    }

    // Disable icons in menus for now, since matching their colors to the
    // current theme is not supported yet.
    // QCoreApplication::setAttribute(Qt::AA_DontShowIconsInMenus, true);

    QApplication app(argc, argv);
    QCoreApplication::setOrganizationName(QStringLiteral("ThothPad"));
    QCoreApplication::setOrganizationDomain(QStringLiteral("org.thothpad"));
    QCoreApplication::setApplicationName(QStringLiteral("thothpad"));
    QGuiApplication::setApplicationDisplayName(QStringLiteral("ThothPad"));

    qApp->installEventFilter(KToolTipHelper::instance());

#if defined(Q_OS_LINUX)
    QGuiApplication::setDesktopFileName("org.thothpad.ThothPad");
#endif

    KAboutData aboutData("thothpad", QCoreApplication::translate("main", "ThothPad"), APPVERSION);

    aboutData.setOrganizationDomain("org.thothpad");
    aboutData.setShortDescription(QCoreApplication::translate("main", "A prose-aware writing studio"));

    aboutData.setOtherText(QCoreApplication::translate("main",
                                                       "<p><strong>See what your prose is doing.</strong></p>"
                                                       "<p>Local, writer-controlled observations for drafting and revision.</p>"));
    aboutData.addAuthor("Megan Conkle", "Developer",
        "megan.conkle@kdemail.net");
    aboutData.addCredit("Graeme Gott",
        QCoreApplication::translate("main",
            "FocusWriter developer, whose Qt code mentored me"),
        "graeme@gottcode.org",
        "gottcode.org");
    aboutData.addCredit("Dmitry Shachnev",
        QCoreApplication::translate("main",
            "ReText developer, whose algorithms helped immensely"),
        QString(),
        "https://github.com/retext-project/retext");
    aboutData.addCredit("Gabriel M. Beddingfield",
        QCoreApplication::translate("main",
            "StretchPlayer developer, whose application showed me how to make frameless windows in Qt"),
        QString(),
        "https://www.teuton.org/~gabriel/stretchplayer/");
    aboutData.addCredit("Wolf Vollprecht",
        QCoreApplication::translate("main",
            "UberWriter (now Apostrophe) developer, for providing inspiration"),
        QString(),
        "https://www.wolfvollprecht.de");
    aboutData.addCredit(QCoreApplication::translate("main", "Other Contributors"),
        QCoreApplication::translate("main",
            "Everyone who provided translations, documentation, bug fixes, or new features over the years"),
        QString(),
        QString());
    aboutData.addComponent("cmark-gfm",
        QCoreApplication::translate("main",
            "An extended version of the C reference implementation of CommonMark"),
        QString(),
        "https://github.com/github/cmark-gfm");
    aboutData.addComponent("React", QCoreApplication::translate("main", "A JavaScript library for building user interfaces"), QString(), "https://reactjs.org");
    aboutData.addComponent("MathJax",
        QCoreApplication::translate("main",
            "A JavaScript display engine for mathematics"),
        QString(),
        "https://www.mathjax.org/");
    aboutData.setLicense(KAboutLicense::GPL_V3);
    aboutData.setCopyrightStatement(
        QCoreApplication::translate("main", "Copyright 2014-%1 The Ghostwriter and ThothPad contributors").arg(QDateTime::currentDateTime().date().year()));
    aboutData.setDesktopFileName("org.thothpad.ThothPad");

    // Set the application metadata.
    KAboutData::setApplicationData(aboutData);

    // Call this to force settings initialization before the application
    // fully launches.
    //
    ghostwriter::AppSettings::instance();

    QString filePath = QString();

    QCommandLineParser clParser;
    aboutData.setupCommandLine(&clParser);
    clParser.setApplicationDescription(QCoreApplication::translate("main", "A prose-aware writing studio. See what your prose is doing."));
    clParser.addPositionalArgument("file",
        QCoreApplication::translate("main", "(Optional) File to open."));

    QCommandLineOption renderingOption("disable-gpu",
        QCoreApplication::translate("main", "Disables GPU acceleration."));

    clParser.addOption(renderingOption);
    clParser.process(app);
    aboutData.processCommandLine(&clParser);

    QStringList posArgs = clParser.positionalArguments();

    app.setWindowIcon(QIcon::fromTheme(QStringLiteral("thothpad"), QIcon(QStringLiteral(":/resources/icons/sc-apps-thothpad.svg"))));

    if (posArgs.size() > 0) {
        filePath = posArgs.first();
    }

    // Note: --disable-gpu option was already processed. We added it here
    //       only so it is displayed in the help output.
    ghostwriter::MainWindow window(filePath);
    installStoryIntelligence(&window);

    window.show();
    return app.exec();
}
