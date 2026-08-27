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
#include <QStyle>
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

namespace
{
void installStoryIntelligence(ghostwriter::MainWindow *window)
{
    auto *editor = window->findChild<ghostwriter::MarkdownEditor *>();
    auto *documentManager = window->findChild<ghostwriter::DocumentManager *>();
    auto *proseController = window->findChild<ghostwriter::ProseController *>();
    auto *proseWidget = window->findChild<ghostwriter::ProseAwarenessWidget *>();
    auto *engine = proseController ? proseController->findChild<ghostwriter::WriterEngineClient *>() : nullptr;
    auto *credentials = proseController ? proseController->findChild<ghostwriter::CredentialStore *>() : nullptr;
    if (!editor || !documentManager || !proseController || !proseWidget || !engine || !credentials) {
        qWarning() << "Story Intelligence could not attach to the editor/engine services.";
        return;
    }

    auto *dock = new QDockWidget(window);
    dock->setObjectName(QStringLiteral("storyIntelligenceDock"));
    dock->setAllowedAreas(Qt::RightDockWidgetArea);
    dock->setFeatures(QDockWidget::NoDockWidgetFeatures);
    dock->setMinimumWidth(320);
    dock->setMaximumWidth(320);

    // The reference UI carries its own quiet header, so suppress the native
    // dock title bar while retaining QDockWidget's robust edge-layout logic.
    auto *nativeTitleBarReplacement = new QWidget(dock);
    nativeTitleBarReplacement->setFixedHeight(0);
    dock->setTitleBarWidget(nativeTitleBarReplacement);

    auto *widget = new ghostwriter::StoryIntelligenceWidget(dock);
    widget->setCollapseIcon(window->style()->standardIcon(QStyle::SP_ArrowRight));
    dock->setWidget(widget);
    window->addDockWidget(Qt::RightDockWidgetArea, dock);

    auto *transactions = new ghostwriter::AgentEditTransactionManager(
        editor, documentManager, dock);
    auto *activity = new ghostwriter::DocumentActivityTracker(
        editor, transactions, dock);
    auto *harness = new ghostwriter::StoryToolHarness(
        window, editor, documentManager, proseController, proseWidget,
        transactions, dock);

    auto *controller = new ghostwriter::StoryIntelligenceController(
        editor, widget, engine, credentials, dock);
    controller->start();
    transactions->setProjectRoot(controller->projectRoot());

    // Keep the services discoverable under the Story Intelligence dock while
    // the structured multi-round tool loop is wired into the controller.
    harness->setObjectName(QStringLiteral("storyToolHarness"));
    transactions->setObjectName(QStringLiteral("agentEditTransactionManager"));
    activity->setObjectName(QStringLiteral("documentActivityTracker"));

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
                     dock, &QDockWidget::hide);

    QAction *toggleAction = dock->toggleViewAction();
    toggleAction->setText(QCoreApplication::translate("main", "Story Intelligence"));
    toggleAction->setShortcut(QKeySequence(QStringLiteral("Ctrl+Alt+I")));
    toggleAction->setShortcutContext(Qt::WindowShortcut);

    // Put the recovery/toggle affordance alongside the existing View tools.
    for (QAction *menuAction : window->menuBar()->actions()) {
        QMenu *menu = menuAction->menu();
        if (!menu) {
            continue;
        }
        QString title = menu->title();
        title.remove(QChar('&'));
        if (title.compare(QStringLiteral("View"), Qt::CaseInsensitive) == 0) {
            menu->addSeparator();
            menu->addAction(toggleAction);
            break;
        }
    }

    QSettings settings;
    const bool visible = settings.value(QStringLiteral("story/visible"), true).toBool();
    dock->setVisible(visible);
    QObject::connect(dock, &QDockWidget::visibilityChanged, dock, [](bool nowVisible) {
        QSettings().setValue(QStringLiteral("story/visible"), nowVisible);
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
