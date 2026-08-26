/*
 * SPDX-FileCopyrightText: 2014-2026 Megan Conkle <megan.conkle@kdemail.net>
 * SPDX-FileCopyrightText: 2009-2014 Graeme Gott <graeme@gottcode.org>
 * SPDX-FileCopyrightText: 2026 Nate Peterson
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#include <QApplication>
#include <QClipboard>
#include <QDesktopServices>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFont>
#include <QFontDialog>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QIODevice>
#include <QIcon>
#include <QImageReader>
#include <QLabel>
#include <QLocale>
#include <QMenu>
#include <QMenuBar>
#include <QMimeDatabase>
#include <QMimeType>
#include <QPushButton>
#include <QScrollBar>
#include <QSettings>
#include <QSizePolicy>
#include <QStatusBar>
#include <QStyleFactory>
#include <QTextDocumentFragment>
#include <QTimer>
#include <QToolButton>
#include <QWhatsThis>

#include <KAboutData>
#include <KActionCollection>
#include <KHelpMenu>
#include <KStandardAction>

#include "export/exporter.h"
#include "export/exporterfactory.h"
#include "settings/localedialog.h"
#include "settings/preferencesdialog.h"
#include "settings/previewoptionsdialog.h"
#include "settings/simplefontdialog.h"
#include "theme/stylesheetbuilder.h"
#include "theme/themeselectiondialog.h"
#include "spelling/spellcheckdecorator.h"
#include "spelling/spellcheckdialog.h"

#include "findreplace.h"
#include "library.h"
#include "mainwindow.h"
#include "messageboxhelper.h"
#include "welcomedialog.h"

#define GW_MAIN_WINDOW_GEOMETRY_KEY "Window/mainWindowGeometry"
#define GW_MAIN_WINDOW_STATE_KEY "Window/mainWindowState"
#define GW_SPLITTER_GEOMETRY_KEY "Window/splitterGeometry"
#define THOTHPAD_WELCOME_VERSION_KEY "Application/welcomeVersion"
#define THOTHPAD_SHOW_WELCOME_UPDATES_KEY "Application/showWelcomeAfterUpdates"

#define MAX_RECENT_FILES (AppActions::OpenLeastRecent - AppActions::OpenMostRecent + 1)

namespace ghostwriter
{
using namespace std::placeholders;

namespace
{
constexpr int ProseActivityRailWidth = 56;
constexpr int ProsePaneWidth = 320;
constexpr int ProseSidebarWidth = ProseActivityRailWidth + ProsePaneWidth;
}

enum SidebarTabIndex {
    FirstSidebarTab,
    FolderViewSidebarTab = FirstSidebarTab,
    OutlineSidebarTab,
    ProseAwarenessSidebarTab,
    SessionStatsSidebarTab,
    DocumentStatsSidebarTab,
    CheatSheetSidebarTab,
    LastSidebarTab = CheatSheetSidebarTab
};

MainWindow::MainWindow(const QString &filePath, QWidget *parent)
    : QMainWindow(parent)
{
    Bookmark fileToOpen(filePath);

    focusModeEnabled = false;
    appSettings = AppSettings::instance();

    loadTheme();
    m_actionCollection = new KActionCollection(this);
    m_actions = new AppActions(actionCollection(), primaryIconTheme, this);

    setupGui();
    setupActions();

    setWindowTitle(documentManager->document()->displayName() + "[*] - " + qAppName());

    // If the file specified as a command line argument does not exist, then
    // create it.
    if (!fileToOpen.isValid() && !fileToOpen.isNull()) {
        QFile file(fileToOpen.filePath());
        if (!file.open(QIODevice::WriteOnly)) {
            // Trigger opening an untitled, new document instead.
            fileToOpen = Bookmark();
            MessageBoxHelper::critical(this, tr("Could not create file: %1").arg(filePath), file.errorString());
        } else {
            file.close();
        }
    } else if (appSettings->restoreSessionEnabled()) {
        if (!fileToOpen.isValid()) {
            Bookmark lastOpened = Library().lastOpened();

            if (lastOpened.isValid()) {
                fileToOpen = lastOpened;
            }
        }
    }

    connect(appSettings, &AppSettings::autoSaveChanged, documentManager, &DocumentManager::setAutoSaveEnabled);
    connect(appSettings, &AppSettings::backupFileChanged, documentManager, &DocumentManager::setFileBackupEnabled);
    connect(appSettings, &AppSettings::backupLocationChanged, documentManager, &DocumentManager::setBackupLocation);
    connect(appSettings, &AppSettings::focusModeChanged, this, &MainWindow::changeFocusMode);
    connect(appSettings, &AppSettings::hideMenuBarInFullScreenChanged, this, &MainWindow::toggleHideMenuBarInFullScreen);
    connect(appSettings, &AppSettings::fileHistoryChanged, this, &MainWindow::toggleFileHistoryEnabled);
    connect(appSettings, &AppSettings::folderViewShowAllFilesChanged, this, &MainWindow::toggleFolderViewShowAllFilesEnabled);
    connect(appSettings, &AppSettings::displayTimeInFullScreenChanged, this, &MainWindow::toggleDisplayTimeInFullScreen);
    connect(appSettings, &AppSettings::editorWidthChanged, this, &MainWindow::changeEditorWidth);
    connect(appSettings, &AppSettings::interfaceStyleChanged, this, &MainWindow::changeInterfaceStyle);
    connect(appSettings, &AppSettings::previewTextFontChanged, this, &MainWindow::applyTheme);
    connect(appSettings, &AppSettings::previewCodeFontChanged, this, &MainWindow::applyTheme);

    connect(documentManager, &DocumentManager::documentLoaded, documentManager, [this]() {
        sessionStats->startNewSession(documentStats->wordCount());
        refreshRecentFiles();

        folderViewWidget->reloadFolderViewFromPath(documentManager->document()->filePath(), appSettings->folderViewShowAllFilesEnabled());
    });

    connect(documentManager, &DocumentManager::documentClosed, documentManager, [this]() {
        sessionStats->startNewSession(0);
    });

    connect(folderViewWidget, &FolderViewWidget::fileSelected, documentManager, [this](const QString &filePath) {
        documentManager->openFileAt(Bookmark(filePath), true);
    });

    qApp->installEventFilter(this);

    toggleHideMenuBarInFullScreen(appSettings->hideMenuBarInFullScreenEnabled());
    menuBarMenuActivated = false;

    // Need this call for GTK / Gnome 42 segmentation fault workaround.
    qApp->processEvents();

    show();

    // Apply the theme only after show() is called on all the widgets,
    // since the Outline scrollbars can end up transparent in Windows if
    // the theme is applied before show().  We cannot call show() and
    // then apply the theme in the constructor due to a bug with
    // Wayland + GTK that causes a segmentation fault.
    //
    applyTheme();
    adjustEditor();

    // Show the theme right away before loading any files.
    qApp->processEvents();

    // Load file from command line or last session if valid, otherwise create
    // an untitled document.
    if (fileToOpen.isValid()) {
        documentManager->openFileAt(fileToOpen);
    } else {
        documentManager->createUntitled();
    }

    if (appSettings->htmlPreviewVisible()) {
        QTimer::singleShot(0, this, [this]() {
            toggleHtmlPreview(true);
        });
    }

    QTimer::singleShot(0, this, &MainWindow::showWelcomeIfNeeded);
}

MainWindow::~MainWindow()
{
    if (primaryIconTheme) {
        delete primaryIconTheme;
        primaryIconTheme = nullptr;
    }

    if (secondaryIconTheme) {
        delete secondaryIconTheme;
        secondaryIconTheme = nullptr;
    }
}

QSize MainWindow::sizeHint() const
{
    return QSize(800, 500);
}

void MainWindow::resizeEvent(QResizeEvent *event)
{
    int width = event->size().width();

    if (width < (0.5 * qApp->primaryScreen()->size().width())) {
        this->sidebar->setVisible(false);
        this->sidebar->setAutoHideEnabled(true);
        this->sidebarHiddenForResize = true;
    }
    else {
        this->sidebarHiddenForResize = false;

        if (!this->focusModeEnabled && this->appSettings->sidebarVisible()) {
            this->sidebar->setAutoHideEnabled(false);
            this->sidebar->setVisible(true);
        }
        else {
            this->sidebar->setAutoHideEnabled(true);
            this->sidebar->setVisible(false);
        }
    }

    adjustEditor();
}

void MainWindow::keyPressEvent(QKeyEvent *e)
{
    int key = e->key();

    switch (key) {
    case Qt::Key_Escape:
    case Qt::Key_F11:
        if (this->isFullScreen()) {
            toggleFullScreen(false);
        }
        break;
    case Qt::Key_Alt:
        if (this->isFullScreen() && appSettings->hideMenuBarInFullScreenEnabled()) {
            if (!this->menuBar()->isVisible()) {
                this->menuBar()->show();
            } else {
                this->menuBar()->hide();
            }
        }
        break;
    case Qt::Key_Tab:
        if (findReplace->isVisible() && findReplace->hasFocus()) {
            findReplace->keyPressEvent(e);
            return;
        }
        else if (!this->editor->hasFocus()) {
            QMainWindow::keyPressEvent(e);
        }
        break;
    default:
        break;
    }

    QMainWindow::keyPressEvent(e);
}

bool MainWindow::eventFilter(QObject *obj, QEvent *event)
{
    if (this->isFullScreen() && appSettings->hideMenuBarInFullScreenEnabled()) {
        if ((this->menuBar() == obj) 
                && (QEvent::Leave == event->type()) 
                && !menuBarMenuActivated) {
            this->menuBar()->hide();
        } else if (QEvent::MouseMove == event->type()) {
            QMouseEvent *mouseEvent = static_cast<QMouseEvent *>(event);

            if ((mouseEvent->globalPosition().y() <= 0) && !this->menuBar()->isVisible()) {
                this->menuBar()->show();
            }
        } else if ((this == obj) 
                && (((QEvent::Leave == event->type()) && !menuBarMenuActivated) 
                    || (QEvent::WindowDeactivate == event->type()))) {
            this->menuBar()->hide();
        }
    }

    return false;
}

void MainWindow::closeEvent(QCloseEvent *event)
{
    if (documentManager->close()) {
        this->quitApplication();
    } else {
        event->ignore();
    }
}

void MainWindow::quitApplication()
{
    if (documentManager->close()) {
        appSettings->store();

        QSettings windowSettings;

        windowSettings.setValue(GW_MAIN_WINDOW_GEOMETRY_KEY, saveGeometry());
        windowSettings.setValue(GW_MAIN_WINDOW_STATE_KEY, saveState());
        windowSettings.setValue(GW_SPLITTER_GEOMETRY_KEY, splitter->saveState());
        windowSettings.sync();

        this->editor->document()->disconnect();
        this->editor->disconnect();
        if (this->htmlPreview) {
            this->htmlPreview->disconnect();
        }
        StyleSheetBuilder::clearCache();

        qApp->quit();
    }
}

void MainWindow::changeTheme()
{
    ThemeSelectionDialog *themeDialog = new ThemeSelectionDialog(theme.name(), appSettings->darkModeEnabled(), this);
    themeDialog->setAttribute(Qt::WA_DeleteOnClose);

    this->connect(themeDialog, &ThemeSelectionDialog::finished, themeDialog, [this, themeDialog](int result) {
        Q_UNUSED(result)
        this->theme = themeDialog->theme();
        applyTheme();
    });

    themeDialog->open();
}

void MainWindow::openPreferencesDialog()
{
    PreferencesDialog *preferencesDialog = new PreferencesDialog(this);
    preferencesDialog->setAttribute(Qt::WA_DeleteOnClose);
    preferencesDialog->show();
}

void MainWindow::toggleHtmlPreview(bool checked)
{
#ifndef THOTHPAD_HAS_WEBENGINE
    Q_UNUSED(checked)
    appSettings->setHtmlPreviewVisible(false);
    appAction(AppActions::Preview)->setChecked(false);
    return;
#else
    if (checked) {
        ensureHtmlPreview();
        htmlPreview->setVisible(true);
        htmlPreview->updatePreview();
    } else if (htmlPreview) {
        htmlPreview->setVisible(false);
    }
    appSettings->setHtmlPreviewVisible(checked);
    this->update();
    adjustEditor();
#endif
}

void MainWindow::toggleHemingwayMode(bool checked)
{
    if (checked) {
        editor->setHemingWayModeEnabled(true);
    } else {
        editor->setHemingWayModeEnabled(false);
    }
}

void MainWindow::toggleFocusMode(bool checked)
{
    this->focusModeEnabled = checked;

    if (checked) {
        editor->setFocusMode(appSettings->focusMode());
        sidebar->setVisible(false);
        sidebar->setAutoHideEnabled(true);
    } else {
        editor->setFocusMode(FocusModeDisabled);

        if (!this->sidebarHiddenForResize && this->appSettings->sidebarVisible()) {
            sidebar->setAutoHideEnabled(false);
            sidebar->setVisible(true);
        }
    }
}

void MainWindow::toggleReaderMode(bool checked)
{
    if (checked && !isFullScreen()) {
        readerModeEnteredFullScreen = true;
        toggleFullScreen(true);
    } else if (!checked && readerModeEnteredFullScreen) {
        readerModeEnteredFullScreen = false;
        toggleFullScreen(false);
    }
    if (nullptr != editor) {
        editor->setReaderMode(checked);
    }
}

void MainWindow::toggleFullScreen(bool checked)
{
    static bool lastStateWasMaximized = false;

    if (this->isFullScreen() || !checked) {
        if (appSettings->displayTimeInFullScreenEnabled()) {
            timeIndicator->hide();
        }

        // If the window had been maximized prior to entering
        // full screen mode, then put the window back to
        // to maximized.  Don't call showNormal(), as that
        // doesn't restore the window to maximized.
        //
        if (lastStateWasMaximized) {
            showMaximized();
        }
        // Put the window back to normal (not maximized).
        else {
            showNormal();
        }

        if (appSettings->hideMenuBarInFullScreenEnabled()) {
            this->menuBar()->show();
        }
    } else {
        if (appSettings->displayTimeInFullScreenEnabled()) {
            timeIndicator->show();
        }

        if (this->isMaximized()) {
            lastStateWasMaximized = true;
        } else {
            lastStateWasMaximized = false;
        }

        showFullScreen();

        if (appSettings->hideMenuBarInFullScreenEnabled()) {
            this->menuBar()->hide();
        }
    }
}

void MainWindow::toggleHideMenuBarInFullScreen(bool checked)
{
    if (this->isFullScreen()) {
        if (checked) {
            this->menuBar()->hide();
        } else {
            this->menuBar()->show();
        }
    }
}

void MainWindow::toggleFileHistoryEnabled(bool checked)
{
    if (!checked) {
        this->clearRecentFileHistory();
    }

    documentManager->setFileHistoryEnabled(checked);
}

void MainWindow::toggleFolderViewShowAllFilesEnabled(bool checked)
{
    if (folderViewWidget != nullptr) {
        folderViewWidget->setShowAllFiles(checked);
    }
}

void MainWindow::toggleDisplayTimeInFullScreen(bool checked)
{
    if (this->isFullScreen()) {
        if (checked) {
            this->timeIndicator->show();
        } else {
            this->timeIndicator->hide();
        }
    }
}

void MainWindow::changeEditorWidth(EditorWidth editorWidth)
{
    editor->setEditorWidth(editorWidth);
    adjustEditor();
}

void MainWindow::changeInterfaceStyle(InterfaceStyle style)
{
    Q_UNUSED(style);

    applyTheme();
}

void MainWindow::showQuickReferenceGuide()
{
    QDesktopServices::openUrl(QUrl("https://ghostwriter.kde.org/documentation"));
}

void MainWindow::showWikiPage()
{
    QDesktopServices::openUrl(QUrl("https://github.com/KDE/ghostwriter/wiki"));
}

void MainWindow::changeFocusMode(FocusMode focusMode)
{
    if (FocusModeDisabled != editor->focusMode()) {
        editor->setFocusMode(focusMode);
    }
}

void MainWindow::refreshRecentFiles()
{
    if (appSettings->fileHistoryEnabled()) {
        Library library;
        BookmarkList recentFiles = library.recentFiles(MAX_RECENT_FILES);

        for (int i = 0; i < recentFilesActions.size(); i++) {
            QAction *action = recentFilesActions.at(i);

            if (i < recentFiles.size()) {
                QString path = recentFiles.at(i).filePath();
                action->setText(path);
                action->setData(path);
                action->setVisible(true);
            } else {
                action->setText("");
                action->setData(QVariant());
                action->setVisible(false);
            }
        }

        appAction(AppActions::ReopenLastClosed)->setEnabled(!recentFiles.isEmpty());
    } else {
        appAction(AppActions::ReopenLastClosed)->setEnabled(false);
    }
}

void MainWindow::clearRecentFileHistory()
{
    Library library;
    library.clearHistory();

    for (auto action : recentFilesActions) {
        action->setText("");
        action->setData(QVariant());
        action->setVisible(false);
    }
}

void MainWindow::changeDocumentDisplayName(const QString &displayName)
{
    setWindowTitle(displayName + QString("[*] - ") + qAppName());

    if (documentManager->document()->isModified()) {
        setWindowModified(!appSettings->autoSaveEnabled());
    } else {
        setWindowModified(false);
    }
}

void MainWindow::onOperationStarted(const QString &description)
{
    if (!description.isNull()) {
        statusIndicator->setText(description);
    }

    statisticsIndicator->hide();
    statusIndicator->show();
    this->update();
    qApp->processEvents();
}

void MainWindow::onOperationFinished()
{
    statusIndicator->setText(QString());
    statisticsIndicator->show();
    statusIndicator->hide();
    this->update();
    qApp->processEvents();
}

void MainWindow::changeFont()
{
    bool success;

    QFont font =
        SimpleFontDialog::font(&success, editor->font(), this);

    if (success) {
        editor->setFont(font.family(), font.pointSize());
        appSettings->setEditorFont(font);
    }
}

void MainWindow::onFontSizeChanged(int size)
{
    QFont font = editor->font();
    font.setPointSize(size);
    appSettings->setEditorFont(font);
}

void MainWindow::onSetLocale()
{
    LocaleDialog *dialog = new LocaleDialog(this);
    dialog->setAttribute(Qt::WA_DeleteOnClose);
    dialog->show();
}

void MainWindow::copyHtml()
{
    Exporter *htmlExporter = appSettings->currentHtmlExporter();

    if (nullptr != htmlExporter) {
        QTextCursor c = editor->textCursor();
        QString markdownText;
        QString html;

        if (c.hasSelection()) {
            // Get only selected text from the document.
            markdownText = c.selection().toPlainText();
        } else {
            // Get all text from the document.
            markdownText = editor->toPlainText();
        }

        // Convert Markdown to HTML.
        htmlExporter->exportToHtml(markdownText, html);

        // Insert HTML into clipboard.
        QClipboard *clipboard = QApplication::clipboard();
        clipboard->setText(html);
    }
}

void MainWindow::showPreviewOptions()
{
    PreviewOptionsDialog *dialog = new PreviewOptionsDialog(this);
    dialog->setAttribute(Qt::WA_DeleteOnClose);
    dialog->setModal(false);
    dialog->show();
}

void MainWindow::onAboutToHideMenuBarMenu()
{
    menuBarMenuActivated = false;

    if (!this->menuBar()->underMouse()
            && this->isFullScreen()
            && appSettings->hideMenuBarInFullScreenEnabled()
            && this->menuBar()->isVisible()) {
        this->menuBar()->hide();
    }
}

void MainWindow::onAboutToShowMenuBarMenu()
{
    menuBarMenuActivated = true;

    if (this->isFullScreen()
            && appSettings->hideMenuBarInFullScreenEnabled()
            && !this->menuBar()->isVisible()) {
        this->menuBar()->show();
    }
}

void MainWindow::onSidebarVisibilityChanged(bool visible)
{
    if (QAction *showSidebarAction = appAction(AppActions::ShowSidebar)) {
        showSidebarAction->setChecked(visible);
    }
    if (auto *showToolsButton = findChild<QToolButton *>(QStringLiteral("showSidebarButton"))) {
        showToolsButton->setVisible(!visible);
    }

    if (!visible) {
        editor->setFocus();
    }

    this->adjustEditor();
}

void MainWindow::toggleSidebarVisible(bool visible)
{
    this->appSettings->setSidebarVisible(visible);

    if (QAction *showSidebarAction = appAction(AppActions::ShowSidebar)) {
        showSidebarAction->setChecked(visible);
    }
    if (auto *showToolsButton = findChild<QToolButton *>(QStringLiteral("showSidebarButton"))) {
        showToolsButton->setVisible(!visible);
    }

    if (!this->sidebarHiddenForResize
            && !this->focusModeEnabled
            && this->appSettings->sidebarVisible()) {
        sidebar->setAutoHideEnabled(false);
    }
    else {
        sidebar->setAutoHideEnabled(true);
    }

    this->sidebar->setVisible(visible);
    this->sidebar->setFocus();
    adjustEditor();
}

KActionCollection *MainWindow::actionCollection() const
{
    return m_actionCollection;
}

QMenu *MainWindow::addMenuBarMenu(const QString &name)
{
    QMenu *menu = new QMenu(name, this);
    connect(menu, &QMenu::aboutToShow, this, &MainWindow::onAboutToShowMenuBarMenu);
    connect(menu, &QMenu::aboutToHide, this, &MainWindow::onAboutToHideMenuBarMenu);
    menuBar()->addMenu(menu);

    return menu;
}

QAction *MainWindow::appAction(AppActions::ActionType actionType) const
{
    auto action = m_actions->get(actionType);

    if (nullptr) {
        qCritical() << "Unknown action type:" << actionType;
    }

    return action;
}

void MainWindow::loadTheme()
{
    QString err;
    QString themeName = appSettings->themeName();
    ThemeRepository themeRepo(appSettings->themeDirectoryPath());

    theme = themeRepo.loadTheme(themeName, err);

    if (!theme.name().isEmpty()) {
        appSettings->setThemeName(theme.name());
    }

    ColorScheme colorScheme;

    if (appSettings->darkModeEnabled()) {
        colorScheme = theme.darkColorScheme();
    } else {
        colorScheme = theme.lightColorScheme();
    }

    ChromeColors chromeColors(colorScheme);

    primaryIconTheme = new SvgIconTheme(":/icons");
    primaryIconTheme->setColor(QIcon::Normal, chromeColors.color(ChromeColors::SecondaryLabel, ChromeColors::NormalState));
    primaryIconTheme->setColor(QIcon::Active, chromeColors.color(ChromeColors::SecondaryLabel, ChromeColors::ActiveState));
    primaryIconTheme->setColor(QIcon::Selected, chromeColors.color(ChromeColors::SecondaryLabel, ChromeColors::PressedState));
    primaryIconTheme->setColor(QIcon::Disabled, chromeColors.color(ChromeColors::SecondaryLabel, ChromeColors::DisabledState));

    secondaryIconTheme = new SvgIconTheme(":/icons");
    secondaryIconTheme->setColor(QIcon::Normal, chromeColors.color(ChromeColors::SecondaryLabel, ChromeColors::NormalState));
    secondaryIconTheme->setColor(QIcon::Active, chromeColors.color(ChromeColors::SecondaryLabel, ChromeColors::NormalState));
    secondaryIconTheme->setColor(QIcon::Selected, chromeColors.color(ChromeColors::SecondaryLabel, ChromeColors::PressedState));
    secondaryIconTheme->setColor(QIcon::Disabled, chromeColors.color(ChromeColors::SecondaryLabel, ChromeColors::DisabledState));

    if (proseAwarenessWidget) {
        proseAwarenessWidget->setCollapseIcon(primaryIconTheme->icon("collapse-sidebar"));
    }
}

void MainWindow::setupActions()
{
    // File Menu Actions

    m_actions->connect(AppActions::New, documentManager, &DocumentManager::createUntitled);
    m_actions->connect(AppActions::Open, documentManager, &DocumentManager::open);

    auto reopenLastAction = appAction(AppActions::ReopenLastClosed);

    // Get open recent files actions.
    for (int i = AppActions::OpenMostRecent; i <= AppActions::OpenLeastRecent; i++) {
        int index = i - AppActions::OpenMostRecent;
        bool enableReopenLast = false;
        auto action = appAction((AppActions::ActionType)i);

        Library library;
        BookmarkList recentFiles = library.recentFiles();

        if (recentFiles.length() > index) {
            auto filePath = recentFiles.at(index).filePath();
            action->setText(filePath);
            action->setData(filePath);
            action->setVisible(true);
            enableReopenLast = true;
        } else {
            action->setVisible(false);
        }

        reopenLastAction->setEnabled(enableReopenLast);
        recentFilesActions.append(action);

        m_actions->connect((AppActions::ActionType)i, this, [this, action](bool checked) {
            Q_UNUSED(checked)

            if (action->data().isValid()) {
                // Use the action's data for access to the actual file
                // path, since KDE Plasma will add a keyboard
                // accelerator to the action's text by inserting an
                // ampersand (&) into it.
                //
                Library library;
                Bookmark location = library.lookup(action->data().toString());

                if (location.isNull()) {
                    location = Bookmark(action->data().toString());
                }

                documentManager->openFileAt(location);
                refreshRecentFiles();
            }
        });
    }

    m_actions->connect(AppActions::ClearRecentFilesList, this, &MainWindow::clearRecentFileHistory);
    m_actions->connect(AppActions::Save, documentManager, &DocumentManager::saveFile);
    m_actions->connect(AppActions::SaveAs, documentManager, &DocumentManager::saveAs);
    m_actions->connect(AppActions::RenameFile, documentManager, &DocumentManager::rename);
    m_actions->connect(AppActions::Reload, documentManager, &DocumentManager::reload);
    m_actions->connect(AppActions::Export, documentManager, &DocumentManager::exportFile);
    m_actions->connect(AppActions::Quit, this, &MainWindow::quitApplication);

    // Edit Menu Actions

    m_actions->connect(AppActions::Undo, editor, &MarkdownEditor::undo);
    m_actions->connect(AppActions::Redo, editor, &MarkdownEditor::redo);
    m_actions->connect(AppActions::Cut, editor, &MarkdownEditor::cut);
    m_actions->connect(AppActions::Copy, editor, &MarkdownEditor::copy);
    m_actions->connect(AppActions::Paste, editor, &MarkdownEditor::paste);
    m_actions->connect(AppActions::CopyHTML, this, &MainWindow::copyHtml);
    m_actions->connect(AppActions::SelectAll, editor, &MarkdownEditor::selectAll);
    m_actions->connect(AppActions::Deselect, editor, &MarkdownEditor::deselectText);
    m_actions->connect(AppActions::InsertImage, editor, &MarkdownEditor::insertImage);
    // TODO: add Deselect method to editor.
    m_actions->connect(AppActions::Find, findReplace, &FindReplace::showFindView);
    m_actions->connect(AppActions::Replace, findReplace, &FindReplace::showReplaceView);
    m_actions->connect(AppActions::FindNext, findReplace, &FindReplace::findNext);
    m_actions->connect(AppActions::FindPrev, findReplace, &FindReplace::findPrevious);
    m_actions->connect(AppActions::Spelling, this, &MainWindow::runSpellCheck);

    // Format Menu Actions

    m_actions->connect(AppActions::Strong, editor, &MarkdownEditor::bold);
    m_actions->connect(AppActions::Emphasis, editor, &MarkdownEditor::italic);
    m_actions->connect(AppActions::Strikethrough, editor, &MarkdownEditor::strikethrough);
    m_actions->connect(AppActions::InsertHTMLComment, editor, &MarkdownEditor::insertComment);
    m_actions->connect(AppActions::IndentText, editor, &MarkdownEditor::indentText);
    m_actions->connect(AppActions::UnindentText, editor, &MarkdownEditor::unindentText);
    m_actions->connect(AppActions::CodeFences, editor, &MarkdownEditor::insertCodeFences);
    m_actions->connect(AppActions::BlockQuote, editor, &MarkdownEditor::createBlockquote);
    m_actions->connect(AppActions::StripBlockQuote, editor, &MarkdownEditor::removeBlockquote);
    m_actions->connect(AppActions::BulletListAsterisk, editor, &MarkdownEditor::createBulletListWithAsteriskMarker);
    m_actions->connect(AppActions::BulletListMinus, editor, &MarkdownEditor::createBulletListWithMinusMarker);
    m_actions->connect(AppActions::BulletListPlus, editor, &MarkdownEditor::createBulletListWithPlusMarker);
    m_actions->connect(AppActions::NumberedListPeriod, editor, &MarkdownEditor::createNumberedListWithPeriodMarker);
    m_actions->connect(AppActions::NumberedListParenthesis, editor, &MarkdownEditor::createNumberedListWithParenthesisMarker);
    m_actions->connect(AppActions::TaskList, editor, &MarkdownEditor::createTaskList);
    m_actions->connect(AppActions::TaskComplete, editor, &MarkdownEditor::toggleTaskComplete);

    // View Menu Actions

    appAction(AppActions::FullScreen)->setChecked(isFullScreen());
    m_actions->connect(AppActions::FullScreen, this, &MainWindow::toggleFullScreen);
    appAction(AppActions::DistractionFreeMode)->setChecked(false);
    m_actions->connect(AppActions::DistractionFreeMode, this, &MainWindow::toggleFocusMode);
    appAction(AppActions::Preview)->setChecked(appSettings->htmlPreviewVisible());
    m_actions->connect(AppActions::Preview, this, &MainWindow::toggleHtmlPreview);
#ifndef THOTHPAD_HAS_WEBENGINE
    appAction(AppActions::Preview)->setChecked(false);
    appAction(AppActions::Preview)->setEnabled(false);
#endif
    m_actions->connect(AppActions::HemingwayMode, this, &MainWindow::toggleHemingwayMode);
    appAction(AppActions::DarkMode)->setChecked(appSettings->darkModeEnabled());
    m_actions->connect(AppActions::DarkMode, this, [this](bool enabled) {
        appSettings->setDarkModeEnabled(enabled);
        applyTheme();
    });
    appAction(AppActions::ShowSidebar)->setChecked(appSettings->sidebarVisible());
    m_actions->connect(AppActions::ShowSidebar, this, &MainWindow::toggleSidebarVisible);
    m_actions->connect(AppActions::ShowOutline, this, [this]() {
        toggleSidebarVisible(true);
        sidebar->setCurrentTabIndex(OutlineSidebarTab);
    });
    m_actions->connect(AppActions::ShowSessionStatistics, this, [this]() {
        toggleSidebarVisible(true);
        sidebar->setCurrentTabIndex(SessionStatsSidebarTab);
    });
    m_actions->connect(AppActions::ShowDocumentStatistics, this, [this]() {
        toggleSidebarVisible(true);
        sidebar->setCurrentTabIndex(DocumentStatsSidebarTab);
    });
    m_actions->connect(AppActions::ShowCheatSheet, this, [this]() {
        toggleSidebarVisible(true);
        sidebar->setCurrentTabIndex(CheatSheetSidebarTab);
    });
    m_actions->connect(AppActions::ZoomIn, editor, &MarkdownEditor::increaseFontSize);
    m_actions->connect(AppActions::ZoomOut, editor, &MarkdownEditor::decreaseFontSize);

    // Settings Menu Actions

    m_actions->connect(AppActions::ChangeTheme, this, &MainWindow::changeTheme);
    m_actions->connect(AppActions::ChangeFont, this, &MainWindow::changeFont);
    m_actions->connect(AppActions::SwitchApplicationLanguage, this, &MainWindow::onSetLocale);
    m_actions->connect(AppActions::PreviewOptions, this, &MainWindow::showPreviewOptions);
    m_actions->connect(AppActions::Preferences, this, &MainWindow::openPreferencesDialog);

    // Help Menu Actions

    m_helpMenu = new KHelpMenu(this);
    m_actions->connect(AppActions::Welcome, this, &MainWindow::showWelcome);
    m_actions->connect(AppActions::AboutApp, m_helpMenu, &KHelpMenu::aboutApplication);
    m_actions->connect(AppActions::AboutKDE, m_helpMenu, &KHelpMenu::aboutKDE);
    m_actions->connect(AppActions::HelpContents, this, &MainWindow::showQuickReferenceGuide);
    m_actions->connect(AppActions::ReportBug, m_helpMenu, &KHelpMenu::reportBug);
    m_actions->connect(AppActions::Donate, m_helpMenu, &KHelpMenu::donate);
    m_actions->connect(AppActions::WhatsThis, this, &QWhatsThis::enterWhatsThisMode);
}

void MainWindow::setupGui()
{
    setObjectName("mainWindow");
    setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Preferred);

    MarkdownDocument *document = new MarkdownDocument();

    editor = new MarkdownEditor(document, theme.lightColorScheme(), this);
    editor->setMinimumWidth(0.1 * qApp->primaryScreen()->size().width());
    editor->setFont(appSettings->editorFont().family(), appSettings->editorFont().pointSize());
    editor->setUseUnderlineForEmphasis(appSettings->useUnderlineForEmphasis());
    editor->setEnableLargeHeadingSizes(appSettings->largeHeadingSizesEnabled());
    editor->setAutoMatchEnabled(appSettings->autoMatchEnabled());
    editor->setBulletPointCyclingEnabled(appSettings->bulletPointCyclingEnabled());
    editor->setPlainText("");
    editor->setEditorWidth((EditorWidth)appSettings->editorWidth());
    editor->setEditorCorners((InterfaceStyle)appSettings->interfaceStyle());
    editor->setItalicizeBlockquotes(appSettings->italicizeBlockquotes());
    editor->setTabulationWidth(appSettings->tabWidth());
    editor->setInsertSpacesForTabs(appSettings->insertSpacesForTabsEnabled());
    editor->setShowUnbreakableSpaces(appSettings->showUnbreakableSpaceEnabled());

    editor->setAutoMatchEnabled('\"', appSettings->autoMatchCharEnabled('\"'));
    editor->setAutoMatchEnabled('\'', appSettings->autoMatchCharEnabled('\''));
    editor->setAutoMatchEnabled('(', appSettings->autoMatchCharEnabled('('));
    editor->setAutoMatchEnabled('[', appSettings->autoMatchCharEnabled('['));
    editor->setAutoMatchEnabled('{', appSettings->autoMatchCharEnabled('{'));
    editor->setAutoMatchEnabled('*', appSettings->autoMatchCharEnabled('*'));
    editor->setAutoMatchEnabled('_', appSettings->autoMatchCharEnabled('_'));
    editor->setAutoMatchEnabled('`', appSettings->autoMatchCharEnabled('`'));
    editor->setAutoMatchEnabled('<', appSettings->autoMatchCharEnabled('<'));
    connect(appSettings, &AppSettings::tabWidthChanged, editor, &MarkdownEditor::setTabulationWidth);
    connect(appSettings, &AppSettings::insertSpacesForTabsChanged, editor, &MarkdownEditor::setInsertSpacesForTabs);
    connect(appSettings, &AppSettings::showUnbreakableSpaceEnabledChanged, editor, &MarkdownEditor::setShowUnbreakableSpaces);
    connect(appSettings, &AppSettings::useUnderlineForEmphasisChanged, editor, &MarkdownEditor::setUseUnderlineForEmphasis);
    connect(appSettings, &AppSettings::italicizeBlockquotesChanged, editor, &MarkdownEditor::setItalicizeBlockquotes);
    connect(appSettings, &AppSettings::largeHeadingSizesChanged, editor, &MarkdownEditor::setEnableLargeHeadingSizes);
    connect(appSettings, &AppSettings::autoMatchChanged, editor, QOverload<bool>::of(&MarkdownEditor::setAutoMatchEnabled));
    connect(appSettings, &AppSettings::autoMatchCharChanged, editor, QOverload<QChar, bool>::of(&MarkdownEditor::setAutoMatchEnabled));
    connect(appSettings, &AppSettings::bulletPointCyclingChanged, editor, &MarkdownEditor::setBulletPointCyclingEnabled);

    connect(editor, &MarkdownEditor::fontSizeChanged, this, &MainWindow::onFontSizeChanged);
    setFocusProxy(editor);

    documentManager = new DocumentManager(editor, this);
    documentManager->setAutoSaveEnabled(appSettings->autoSaveEnabled());
    documentManager->setFileBackupEnabled(appSettings->backupFileEnabled());
    documentManager->setDraftLocation(appSettings->draftLocation());
    documentManager->setBackupLocation(appSettings->backupLocation());
    documentManager->setFileHistoryEnabled(appSettings->fileHistoryEnabled());
    documentManager->setRestoreSessionEnabled(appSettings->restoreSessionEnabled());
    connect(documentManager, &DocumentManager::documentDisplayNameChanged, this, &MainWindow::changeDocumentDisplayName);
    connect(documentManager, &DocumentManager::documentModifiedChanged, this, &MainWindow::setWindowModified);
    connect(documentManager, &DocumentManager::operationStarted, this, &MainWindow::onOperationStarted);
    connect(documentManager, &DocumentManager::operationUpdate, this, &MainWindow::onOperationStarted);
    connect(documentManager, &DocumentManager::operationFinished, this, &MainWindow::onOperationFinished);
    connect(documentManager, &DocumentManager::sessionHistoryChanged, this, &MainWindow::refreshRecentFiles);

    spelling = new SpellCheckDecorator(
        editor,
        editor->textFormatOverlayController());
    connect(appSettings, &AppSettings::spellCheckSettingsChanged, spelling, &SpellCheckDecorator::settingsChanged);

    findReplace = new FindReplace(editor, this);
    statusBarWidgets.append(findReplace);
    findReplace->setVisible(false);
    findReplace->setMatchCaseIcon(primaryIconTheme->icon("match-case"));
    findReplace->setWholeWordIcon(primaryIconTheme->icon("whole-word"));
    findReplace->setRegexSearchIcon(primaryIconTheme->icon("regex-search"));
    findReplace->setHighlightMatchesIcon(primaryIconTheme->icon("highlight-matches"));
    findReplace->setFindNextIcon(primaryIconTheme->icon("find-next"));
    findReplace->setFindPreviousIcon(primaryIconTheme->icon("find-previous"));
    findReplace->setCloseIcon(primaryIconTheme->icon("close"));

    setupSidebar();
    proseController = new ProseController(editor, proseAwarenessWidget, this);
    proseController->start();
    setupMenuBar();
    setupStatusBar();

    // Set dimensions for the main window.  This is best done before
    // building the status bar, so that we can determine whether the full
    // screen button should be checked.
    //
    QSettings windowSettings;

    if (windowSettings.contains(GW_MAIN_WINDOW_GEOMETRY_KEY)) {
        restoreGeometry(windowSettings.value(GW_MAIN_WINDOW_GEOMETRY_KEY).toByteArray());
        restoreState(windowSettings.value(GW_MAIN_WINDOW_STATE_KEY).toByteArray());
    } else {
        adjustSize();
    }

    splitter = new QSplitter(this);
    // Editor plus the sentence-rhythm strip ride together as one pane.
    auto *editorArea = new QWidget(this);
    auto *editorLayout = new QHBoxLayout(editorArea);
    editorLayout->setContentsMargins(0, 0, 0, 0);
    editorLayout->setSpacing(0);
    editorLayout->addWidget(editor);
    breathMap = new BreathMapWidget(editorArea);
    breathMap->setDocument(document);
    editorLayout->addWidget(breathMap);
    splitter->addWidget(sidebar);
    splitter->addWidget(editorArea);
    splitter->setChildrenCollapsible(false);
    splitter->setStretchFactor(0, 0);
    splitter->setStretchFactor(1, 2);

    // Set default sizes for splitter.
    QList<int> sizes;
    // The ThothPad tool pane is intentionally fixed to the redesign's
    // 56 px activity rail + 320 px content pane.  A percentage-based default
    // grows the native sidebar far beyond the reference on large displays.
    int sidebarWidth = ProseSidebarWidth;
    int otherWidth = width() - sidebarWidth;
    sizes.append(sidebarWidth);
    sizes.append(otherWidth);

    splitter->setSizes(sizes);

    connect(splitter, &QSplitter::splitterMoved, splitter, [this](int pos, int index) {
        Q_UNUSED(pos)
        Q_UNUSED(index)
        adjustEditor();
    });

    setCentralWidget(splitter);
}

void MainWindow::setupMenuBar()
{
    QMenu *menu;

    // File Menu

    menu = addMenuBarMenu(tr("&File"));
    menu->addAction(appAction(AppActions::New));
    menu->addAction(appAction(AppActions::Open));

    QAction *openRecentAction = appAction(AppActions::OpenRecent);
    QMenu *submenu = menu->addMenu(openRecentAction->icon(), openRecentAction->text());
    connect(submenu, &QMenu::aboutToShow, this, &MainWindow::onAboutToShowMenuBarMenu);
    connect(submenu, &QMenu::aboutToHide, this, &MainWindow::onAboutToHideMenuBarMenu);

    submenu->addAction(appAction(AppActions::ReopenLastClosed));
    submenu->addSeparator();

    for (int i = AppActions::OpenMostRecent; i <= AppActions::OpenLeastRecent; i++) {
        submenu->addAction(appAction((AppActions::ActionType)i));
    }

    submenu->addSeparator();
    submenu->addAction(appAction(AppActions::ClearRecentFilesList));

    menu->addAction(appAction(AppActions::Save));
    menu->addAction(appAction(AppActions::SaveAs));
    menu->addAction(appAction(AppActions::RenameFile));
    menu->addAction(appAction(AppActions::Reload));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::Export));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::Quit));

    // Edit Menu

    menu = addMenuBarMenu(tr("&Edit"));
    menu->addAction(appAction(AppActions::Undo));
    menu->addAction(appAction(AppActions::Redo));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::Cut));
    menu->addAction(appAction(AppActions::Copy));
    menu->addAction(appAction(AppActions::Paste));
    menu->addAction(appAction(AppActions::CopyHTML));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::SelectAll));
    menu->addAction(appAction(AppActions::Deselect));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::InsertImage));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::Find));
    menu->addAction(appAction(AppActions::Replace));
    menu->addAction(appAction(AppActions::FindNext));
    menu->addAction(appAction(AppActions::FindPrev));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::Spelling));

    // Format Menu

    menu = addMenuBarMenu("&Format");
    menu->addAction(appAction(AppActions::Strong));
    menu->addAction(appAction(AppActions::Emphasis));
    menu->addAction(appAction(AppActions::Strikethrough));
    menu->addAction(appAction(AppActions::InsertHTMLComment));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::IndentText));
    menu->addAction(appAction(AppActions::UnindentText));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::CodeFences));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::BlockQuote));
    menu->addAction(appAction(AppActions::StripBlockQuote));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::BulletListAsterisk));
    menu->addAction(appAction(AppActions::BulletListMinus));
    menu->addAction(appAction(AppActions::BulletListPlus));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::NumberedListPeriod));
    menu->addAction(appAction(AppActions::NumberedListParenthesis));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::TaskList));
    menu->addAction(appAction(AppActions::TaskComplete));

    // View Menu

    menu = addMenuBarMenu(tr("&View"));
    menu->addAction(appAction(AppActions::FullScreen));
    menu->addAction(appAction(AppActions::DistractionFreeMode));
    QAction *readerModeAction = menu->addAction(tr("Reader &Mode"));
    readerModeAction->setCheckable(true);
    readerModeAction->setShortcut(QKeySequence(QStringLiteral("Ctrl+Alt+R")));
    connect(readerModeAction, &QAction::toggled, this, &MainWindow::toggleReaderMode);
    menu->addAction(appAction(AppActions::Preview));
    menu->addAction(appAction(AppActions::HemingwayMode));
    menu->addAction(appAction(AppActions::DarkMode));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::ShowSidebar));
    menu->addAction(appAction(AppActions::ShowOutline));
    menu->addAction(appAction(AppActions::ShowSessionStatistics));
    menu->addAction(appAction(AppActions::ShowDocumentStatistics));
    menu->addAction(appAction(AppActions::ShowCheatSheet));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::ZoomIn));
    menu->addAction(appAction(AppActions::ZoomOut));

    // Settings Menu

    menu = addMenuBarMenu(tr("&Settings"));
    menu->addAction(appAction(AppActions::ChangeTheme));
    menu->addAction(appAction(AppActions::ChangeFont));
    menu->addAction(appAction(AppActions::SwitchApplicationLanguage));
    menu->addAction(appAction(AppActions::PreviewOptions));
    menu->addAction(appAction(AppActions::Preferences));

    // Help Menu

    menu = addMenuBarMenu(tr("&Help"));
    menu->addAction(appAction(AppActions::Welcome));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::HelpContents));
    menu->addAction(appAction(AppActions::WhatsThis));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::ReportBug));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::Donate));
    menu->addSeparator();
    menu->addAction(appAction(AppActions::AboutApp));
    menu->addAction(appAction(AppActions::AboutKDE));

    // The reference shell keeps theme controls and the ThothPad identity in
    // the menu bar itself.  Keeping these as real actions preserves the
    // existing theme plumbing while giving the native window the same quiet,
    // deliberate top-right chrome as the redesigned surface.
    QWidget *chromeControls = new QWidget(menuBar());
    chromeControls->setObjectName(QStringLiteral("thothpadChromeControls"));
    auto *chromeLayout = new QHBoxLayout(chromeControls);
    chromeLayout->setContentsMargins(6, 0, 8, 0);
    chromeLayout->setSpacing(2);

    auto makeChromeButton = [chromeControls](const QString &objectName, const QString &toolTip) {
        auto *button = new QToolButton(chromeControls);
        button->setObjectName(objectName);
        button->setToolTip(toolTip);
        button->setAutoRaise(true);
        button->setFocusPolicy(Qt::NoFocus);
        button->setIconSize(QSize(16, 16));
        return button;
    };

    QToolButton *lightModeButton = makeChromeButton(QStringLiteral("thothpadLightModeButton"), tr("Use light theme"));
    lightModeButton->setIcon(primaryIconTheme->icon("light-mode"));
    lightModeButton->setCheckable(true);

    QToolButton *darkModeButton = makeChromeButton(QStringLiteral("thothpadDarkModeButton"), tr("Toggle dark theme"));
    darkModeButton->setIcon(primaryIconTheme->icon("dark-mode"));
    darkModeButton->setCheckable(true);

    QToolButton *themeButton = makeChromeButton(QStringLiteral("thothpadThemeButton"), tr("Use teal theme"));
    themeButton->setIcon(primaryIconTheme->icon("theme"));
    themeButton->setCheckable(true);

    const auto setChromeTheme = [this, lightModeButton, darkModeButton, themeButton](const QString &themeName, bool darkMode) {
        ThemeRepository themeRepository(appSettings->themeDirectoryPath());
        QString error;
        Theme selectedTheme = themeRepository.loadTheme(themeName, error);
        if (selectedTheme.name().isEmpty()) {
            return;
        }

        theme = selectedTheme;
        appSettings->setThemeName(theme.name());
        appSettings->setDarkModeEnabled(darkMode);
        appAction(AppActions::DarkMode)->setChecked(darkMode);
        lightModeButton->setChecked(themeName == QStringLiteral("Kanagawa Lotus") && !darkMode);
        darkModeButton->setChecked(themeName == QStringLiteral("Kanagawa Lotus") && darkMode);
        themeButton->setChecked(themeName == QStringLiteral("ThothPad Teal"));
        applyTheme();
    };

    connect(lightModeButton, &QToolButton::clicked, this, [setChromeTheme]() {
        setChromeTheme(QStringLiteral("Kanagawa Lotus"), false);
    });
    connect(darkModeButton, &QToolButton::clicked, this, [setChromeTheme]() {
        setChromeTheme(QStringLiteral("Kanagawa Lotus"), true);
    });
    connect(themeButton, &QToolButton::clicked, this, [setChromeTheme]() {
        setChromeTheme(QStringLiteral("ThothPad Teal"), false);
    });

    lightModeButton->setChecked(theme.name() == QStringLiteral("Kanagawa Lotus") && !appSettings->darkModeEnabled());
    darkModeButton->setChecked(theme.name() == QStringLiteral("Kanagawa Lotus") && appSettings->darkModeEnabled());
    themeButton->setChecked(theme.name() == QStringLiteral("ThothPad Teal"));

    auto *separator = new QFrame(chromeControls);
    separator->setObjectName(QStringLiteral("thothpadChromeSeparator"));
    separator->setFrameShape(QFrame::VLine);
    separator->setFrameShadow(QFrame::Plain);

    auto *wordmark = new QLabel(QStringLiteral("THOTHPAD"), chromeControls);
    wordmark->setObjectName(QStringLiteral("thothpadWordmark"));
    wordmark->setAlignment(Qt::AlignCenter);

    chromeLayout->addWidget(lightModeButton);
    chromeLayout->addWidget(darkModeButton);
    chromeLayout->addWidget(themeButton);
    chromeLayout->addSpacing(6);
    chromeLayout->addWidget(separator);
    chromeLayout->addSpacing(10);
    chromeLayout->addWidget(wordmark);
    menuBar()->setCornerWidget(chromeControls, Qt::TopRightCorner);

    // Refresh the recent files list with the latest and greatest.
    if (appSettings->fileHistoryEnabled()) {
        refreshRecentFiles();
    }

    // Hide menu bar in full screen mode if enabled to do so.
    if (isFullScreen() && appSettings->hideMenuBarInFullScreenEnabled()) {
        menuBar()->hide();
    }
}

void MainWindow::setupStatusBar()
{
    QGridLayout *statusBarLayout = new QGridLayout();
    statusBarLayout->setSpacing(0);
    statusBarLayout->setContentsMargins(0, 0, 0, 0);

    statusBarLayout->addWidget(findReplace, 0, 0, 1, 3);

    // Divide the status bar into thirds for placing widgets.
    QFrame *leftWidget = new QFrame(statusBar());
    leftWidget->setObjectName("leftStatusBarWidget");
    QFrame *midWidget = new QFrame(statusBar());
    midWidget->setObjectName("midStatusBarWidget");
    QFrame *rightWidget = new QFrame(statusBar());
    rightWidget->setObjectName("rightStatusBarWidget");

    QHBoxLayout *leftLayout = new QHBoxLayout(leftWidget);
    leftWidget->setLayout(leftLayout);
    leftLayout->setContentsMargins(0,0,0,0);
    QHBoxLayout *midLayout = new QHBoxLayout(midWidget);
    midWidget->setLayout(midLayout);
    midLayout->setContentsMargins(0,0,0,0);
    QHBoxLayout *rightLayout = new QHBoxLayout(rightWidget);
    rightWidget->setLayout(rightLayout);
    rightLayout->setContentsMargins(0,0,0,0);

    // Keep a discoverable recovery affordance when the tools pane is hidden.
    // It disappears while the pane is open, preserving the reference shell.
    QToolButton *showToolsButton = new QToolButton(leftWidget);
    showToolsButton->setObjectName(QStringLiteral("showSidebarButton"));
    showToolsButton->setIcon(primaryIconTheme->icon("show-sidebar"));
    showToolsButton->setText(tr("Show tools"));
    showToolsButton->setToolButtonStyle(Qt::ToolButtonTextBesideIcon);
    showToolsButton->setToolTip(tr("Show tools sidebar (Ctrl+Space)"));
    showToolsButton->setAccessibleName(tr("Show tools sidebar"));
    showToolsButton->setFocusPolicy(Qt::NoFocus);
    showToolsButton->setVisible(!sidebar->isVisible());
    connect(showToolsButton, &QToolButton::clicked, this, [this]() {
        toggleSidebarVisible(true);
    });
    leftLayout->addWidget(showToolsButton, 0, Qt::AlignLeft | Qt::AlignVCenter);
    statusBarWidgets.append(showToolsButton);

    timeIndicator = new TimeLabel(this);
    leftLayout->addWidget(timeIndicator, 0, Qt::AlignLeft);
    leftWidget->setContentsMargins(0, 0, 0, 0);
    statusBarWidgets.append(timeIndicator);

    if (!isFullScreen() || appSettings->displayTimeInFullScreenEnabled()) {
        timeIndicator->hide();
    }

    statusBarLayout->addWidget(leftWidget, 1, 0, 1, 1, Qt::AlignLeft);

    // Add middle widgets to status bar.
    statusIndicator = new QLabel();
    midLayout->addWidget(statusIndicator, 0, Qt::AlignCenter);
    statusIndicator->hide();

    statisticsIndicator = new StatisticsIndicator(this->documentStats, this->sessionStats, this);
    connect(proseController, &ProseController::dialogueStatsChanged, statisticsIndicator, [this](int spanCount, int ratioPercent) {
        Q_UNUSED(spanCount);
        statisticsIndicator->setDialogueRatio(ratioPercent);
    });

    if ((appSettings->favoriteStatistic() >= 0)
            && (appSettings->favoriteStatistic() < statisticsIndicator->count())) {
        statisticsIndicator->setCurrentIndex(appSettings->favoriteStatistic());
    }
    else {
        statisticsIndicator->setCurrentIndex(0);
    }

    connect(statisticsIndicator, QOverload<int>::of(&QComboBox::currentIndexChanged), appSettings, &AppSettings::setFavoriteStatistic);

    midLayout->addWidget(statisticsIndicator, 0, Qt::AlignCenter);
    midWidget->setContentsMargins(0, 0, 0, 0);
    statusBarLayout->addWidget(midWidget, 1, 1, 1, 1, Qt::AlignCenter);
    statusBarWidgets.append(statisticsIndicator);

    // Add right-most widgets to status bar.
    QToolButton *button = new QToolButton();
    button->setDefaultAction(appAction(AppActions::Preview));
    button->setIcon(secondaryIconTheme->icon("live-preview"));
    button->setIconSize(QSize(16, 16));
    button->setFocusPolicy(Qt::NoFocus);
    rightLayout->addWidget(button, 0, Qt::AlignRight);
    statusBarWidgets.append(button);

    button = new QToolButton();
    button->setDefaultAction(appAction(AppActions::HemingwayMode));
    button->setIcon(secondaryIconTheme->icon("hemingway-mode"));
    button->setFocusPolicy(Qt::NoFocus);
    rightLayout->addWidget(button, 0, Qt::AlignRight);
    statusBarWidgets.append(button);

    button = new QToolButton();
    button->setDefaultAction(appAction(AppActions::DistractionFreeMode));
    button->setIcon(secondaryIconTheme->icon("distraction-free-mode"));
    button->setFocusPolicy(Qt::NoFocus);
    rightLayout->addWidget(button, 0, Qt::AlignRight);
    statusBarWidgets.append(button);

    button = new QToolButton();
    button->setDefaultAction(appAction(AppActions::FullScreen));
    button->setIcon(secondaryIconTheme->icon("full-screen"));
    button->setFocusPolicy(Qt::NoFocus);
    button->setObjectName("fullscreenButton");
    rightLayout->addWidget(button, 0, Qt::AlignRight);
    statusBarWidgets.append(button);

    rightWidget->setContentsMargins(0, 0, 0, 0);
    statusBarLayout->addWidget(rightWidget, 1, 2, 1, 1, Qt::AlignRight);
    
    QWidget *container = new QWidget(this);
    container->setObjectName("statusBarWidgetContainer");
    container->setLayout(statusBarLayout);
    container->setContentsMargins(0, 0, 2, 0);
    container->setStyleSheet("#statusBarWidgetContainer { border: 0; margin: 0; padding: 0 }");

    statusBar()->addWidget(container, 1);
    statusBar()->setSizeGripEnabled(false);
}

void MainWindow::setupSidebar()
{
    cheatSheetWidget = new QListWidget(this);

    cheatSheetWidget->setSelectionMode(QAbstractItemView::NoSelection);
    cheatSheetWidget->setAlternatingRowColors(false);

    cheatSheetWidget->addItem(tr("# Heading 1"));
    cheatSheetWidget->addItem(tr("## Heading 2"));
    cheatSheetWidget->addItem(tr("### Heading 3"));
    cheatSheetWidget->addItem(tr("#### Heading 4"));
    cheatSheetWidget->addItem(tr("##### Heading 5"));
    cheatSheetWidget->addItem(tr("###### Heading 6"));
    cheatSheetWidget->addItem(tr("*Emphasis* _Emphasis_"));
    cheatSheetWidget->addItem(tr("**Strong** __Strong__"));
    cheatSheetWidget->addItem(tr("1. Numbered List"));
    cheatSheetWidget->addItem(tr("* Bullet List"));
    cheatSheetWidget->addItem(tr("+ Bullet List"));
    cheatSheetWidget->addItem(tr("- Bullet List"));
    cheatSheetWidget->addItem(tr("> Block Quote"));
    cheatSheetWidget->addItem(tr("`Code Span`"));
    cheatSheetWidget->addItem(tr("``` Code Block"));
    cheatSheetWidget->addItem(tr("[Link](http://url.com \"Title\")"));
    cheatSheetWidget->addItem(tr("[Reference Link][ID]"));
    cheatSheetWidget->addItem(tr("[ID]: http://url.com \"Reference Definition\""));
    cheatSheetWidget->addItem(tr("![Image](./image.jpg \"Title\")"));
    cheatSheetWidget->addItem(tr("--- *** ___ Horizontal Rule"));

    documentStatsWidget = new DocumentStatisticsWidget(this);
    documentStatsWidget->setSelectionMode(QAbstractItemView::NoSelection);
    documentStatsWidget->setAlternatingRowColors(false);

    sessionStatsWidget = new SessionStatisticsWidget(this);
    sessionStatsWidget->setSelectionMode(QAbstractItemView::NoSelection);
    sessionStatsWidget->setAlternatingRowColors(false);

    outlineWidget = new OutlineWidget(editor, this);
    outlineWidget->setAlternatingRowColors(false);

    documentStats = new DocumentStatistics((MarkdownDocument *) editor->document(), this);
    connect(documentStats, &DocumentStatistics::wordCountChanged,
            documentStatsWidget, &DocumentStatisticsWidget::setWordCount);
    connect(documentStats, &DocumentStatistics::characterCountChanged,
            documentStatsWidget, &DocumentStatisticsWidget::setCharacterCount);
    connect(documentStats, &DocumentStatistics::sentenceCountChanged,
            documentStatsWidget, &DocumentStatisticsWidget::setSentenceCount);
    connect(documentStats, &DocumentStatistics::paragraphCountChanged,
            documentStatsWidget, &DocumentStatisticsWidget::setParagraphCount);
    connect(documentStats, &DocumentStatistics::pageCountChanged,
            documentStatsWidget, &DocumentStatisticsWidget::setPageCount);
    connect(documentStats, &DocumentStatistics::complexWordsChanged,
            documentStatsWidget, &DocumentStatisticsWidget::setComplexWords);
    connect(documentStats, &DocumentStatistics::readingTimeChanged,
            documentStatsWidget, &DocumentStatisticsWidget::setReadingTime);
    connect(documentStats, &DocumentStatistics::lixReadingEaseChanged,
            documentStatsWidget, &DocumentStatisticsWidget::setLixReadingEase);
    connect(documentStats, &DocumentStatistics::readabilityIndexChanged,
            documentStatsWidget, &DocumentStatisticsWidget::setReadabilityIndex);
    connect(editor, &MarkdownEditor::textSelected, documentStats, &DocumentStatistics::onTextSelected);
    connect(editor, &MarkdownEditor::textDeselected, documentStats, &DocumentStatistics::onTextDeselected);

    sessionStats = new SessionStatistics(this);
    connect(documentStats, &DocumentStatistics::totalWordCountChanged, sessionStats, &SessionStatistics::onDocumentWordCountChanged);
    connect(sessionStats, &SessionStatistics::wordCountChanged, sessionStatsWidget, &SessionStatisticsWidget::setWordCount);
    connect(sessionStats, &SessionStatistics::pageCountChanged, sessionStatsWidget, &SessionStatisticsWidget::setPageCount);
    connect(sessionStats, &SessionStatistics::wordsPerMinuteChanged, sessionStatsWidget, &SessionStatisticsWidget::setWordsPerMinute);
    connect(sessionStats, &SessionStatistics::writingTimeChanged, sessionStatsWidget, &SessionStatisticsWidget::setWritingTime);
    connect(sessionStats, &SessionStatistics::idleTimePercentageChanged, sessionStatsWidget, &SessionStatisticsWidget::setIdleTime);
    connect(editor, &MarkdownEditor::typingPaused, sessionStats, &SessionStatistics::onTypingPaused);
    connect(editor, &MarkdownEditor::typingResumed, sessionStats, &SessionStatistics::onTypingResumed);

    sidebar = new Sidebar(this);
    sidebar->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Preferred);
    sidebar->setMinimumWidth(ProseSidebarWidth);
    sidebar->setMaximumWidth(ProseSidebarWidth);

    folderViewWidget = new FolderViewWidget(this);
    sidebar->addTab(primaryIconTheme->icon("open-file"), folderViewWidget, tr("Folder View"));
    sidebar->addTab(primaryIconTheme->icon("outline"), outlineWidget, tr("Outline"));
    proseAwarenessWidget = new ProseAwarenessWidget(this);
    sidebar->addTab(primaryIconTheme->icon("prose-awareness"), proseAwarenessWidget, tr("Prose Awareness"));
    // Session statistics still powers the status indicator and its View
    // action, but it is intentionally not a primary activity-rail tool in
    // the redesigned shell.
    sidebar->addTab(primaryIconTheme->icon("session-statistics"), sessionStatsWidget, tr("Session Statistics"), QStringLiteral("sessionStatsTab"));
    sidebar->addTab(primaryIconTheme->icon("document-statistics"), documentStatsWidget, tr("Document Statistics"));
    sidebar->addTab(primaryIconTheme->icon("cheat-sheet"), cheatSheetWidget, tr("Cheat Sheet"), "cheatSheetTab");
    if (auto *sessionStatsTab = sidebar->findChild<QPushButton *>(QStringLiteral("sessionStatsTab"))) {
        sessionStatsTab->hide();
    }
    connect(proseAwarenessWidget, &ProseAwarenessWidget::collapseRequested, this, [this]() {
        toggleSidebarVisible(false);
    });

    QSettings sidebarSettings;
    const bool hasLegacyStoredTab = sidebarSettings.contains("sidebarCurrentTab");
    int tabIndex = sidebarSettings.value("sidebarCurrentTab", (int)ProseAwarenessSidebarTab).toInt();
    // Translate the previous rail order once for installations that retained
    // the old prose-tab index even though this version puts it third.
    if (hasLegacyStoredTab && !sidebarSettings.contains("sidebarTabLayoutVersion")) {
        static const int migratedIndexes[] = {0, 1, 3, 4, 5, 2};
        if (tabIndex >= 0 && tabIndex < 6) {
            tabIndex = migratedIndexes[tabIndex];
        }
    }
    sidebarSettings.setValue("sidebarTabLayoutVersion", 2);

    if ((tabIndex < 0) || (tabIndex >= sidebar->tabCount())) {
        tabIndex = (int) FirstSidebarTab;
    }

    sidebar->setCurrentTabIndex(tabIndex);

    if (!sidebarHiddenForResize && !focusModeEnabled && appSettings->sidebarVisible()) {
        sidebar->setAutoHideEnabled(false);
        sidebar->setVisible(true);
    } else {
        sidebar->setAutoHideEnabled(true);
        sidebar->setVisible(false);
    }

    connect(sidebar, &Sidebar::visibilityChanged, this, &MainWindow::onSidebarVisibilityChanged);

    sidebar->setMinimumWidth(ProseSidebarWidth);
}

void MainWindow::adjustEditor()
{
    // Make sure editor size is updated.
    qApp->processEvents();

    int width = this->width();
    int sidebarWidth = 0;

    // Make sure live preview does not crowd out editor.
    // It should not take up more than 50% of the window space
    // left after the sidebar is accounted for.
    //
    if (sidebar->isVisible()) {
        sidebarWidth = sidebar->width();
    }

    if (htmlPreview) {
        htmlPreview->setMaximumWidth((width - sidebarWidth) / 2);
    }

    // Resize the editor's margins.
    editor->setupPaperMargins();

    // Scroll to cursor position.
    editor->centerCursor();
}

void MainWindow::applyTheme()
{
    if (!theme.name().isNull() && !theme.name().isEmpty()) {
        appSettings->setThemeName(theme.name());
    }

    ColorScheme colorScheme = theme.lightColorScheme();

    if (appSettings->darkModeEnabled()) {
        colorScheme = theme.darkColorScheme();
    }

    ChromeColors chromeColors(colorScheme);

    primaryIconTheme->setColor(QIcon::Normal, chromeColors.color(ChromeColors::SecondaryLabel, ChromeColors::NormalState));
    primaryIconTheme->setColor(QIcon::Active, chromeColors.color(ChromeColors::Label, ChromeColors::NormalState));
    primaryIconTheme->setColor(QIcon::Selected, chromeColors.color(ChromeColors::Label, ChromeColors::NormalState));
    primaryIconTheme->setColor(QIcon::Disabled, chromeColors.color(ChromeColors::SecondaryLabel, ChromeColors::DisabledState));

    secondaryIconTheme->setColor(QIcon::Normal, chromeColors.color(ChromeColors::SecondaryLabel, ChromeColors::NormalState));
    secondaryIconTheme->setColor(QIcon::Active, chromeColors.color(ChromeColors::SecondaryLabel, ChromeColors::ActiveState));
    secondaryIconTheme->setColor(QIcon::Selected, chromeColors.color(ChromeColors::SecondaryLabel, ChromeColors::PressedState));
    secondaryIconTheme->setColor(QIcon::Disabled, chromeColors.color(ChromeColors::SecondaryLabel, ChromeColors::DisabledState));

    if (proseAwarenessWidget) {
        proseAwarenessWidget->setCollapseIcon(primaryIconTheme->icon("collapse-sidebar"));
    }

    StyleSheetBuilder styler(chromeColors,
                             secondaryIconTheme,
                             (InterfaceStyleRounded == appSettings->interfaceStyle()),
                             appSettings->editorFont(),
                             appSettings->previewTextFont(),
                             appSettings->previewCodeFont());

    editor->setColorScheme(colorScheme);
    spelling->setErrorColor(colorScheme.error);

    // Do not call MainWindow::setStyleSheet().  Calling it more than once
    // (i.e., when changing a theme) causes a crash in Qt 5.11.  Instead,
    // change the main window's style sheet via qApp.
    //
    QString styleSheet = styler.widgetStyleSheet();

    if (styleSheet.isNull()) {
        qCritical() << "Invalid widget style sheet provided.";
    } else {
        qApp->style()->unpolish(qApp);
        qApp->style()->unpolish(this);
        qApp->setStyleSheet(styleSheet);
        qApp->style()->polish(qApp);
        qApp->style()->polish(this);
    }

    // Keep the three reference theme controls synchronized when a theme is
    // changed from Settings or the View menu instead of only through the
    // top-right buttons.
    if (auto *lightModeButton = findChild<QToolButton *>(QStringLiteral("thothpadLightModeButton"))) {
        lightModeButton->setChecked(theme.name() == QStringLiteral("Kanagawa Lotus") && !appSettings->darkModeEnabled());
    }
    if (auto *darkModeButton = findChild<QToolButton *>(QStringLiteral("thothpadDarkModeButton"))) {
        darkModeButton->setChecked(theme.name() == QStringLiteral("Kanagawa Lotus") && appSettings->darkModeEnabled());
    }
    if (auto *themeButton = findChild<QToolButton *>(QStringLiteral("thothpadThemeButton"))) {
        themeButton->setChecked(theme.name() == QStringLiteral("ThothPad Teal"));
    }

    if (htmlPreview) {
        styleSheet = styler.htmlPreviewStyleSheet();

        if (styleSheet.isNull()) {
            qCritical() << "Invalid HTML preview style sheet provided.";
        } else {
            htmlPreview->setStyleSheet(styleSheet);
        }
    }

    adjustEditor();
}

void MainWindow::ensureHtmlPreview()
{
    if (htmlPreview) {
        return;
    }

    htmlPreview = new HtmlPreview(documentManager->document(), appSettings->currentHtmlExporter(), this);
    connect(editor, &MarkdownEditor::typingPausedScaled, htmlPreview, &HtmlPreview::updatePreview);
    connect(documentManager, &DocumentManager::documentLoaded, htmlPreview, &HtmlPreview::updatePreview);
    connect(documentManager, &DocumentManager::documentClosed, htmlPreview, &HtmlPreview::updatePreview);
    connect(outlineWidget, &OutlineWidget::headingNumberNavigated, htmlPreview, &HtmlPreview::navigateToHeading);
    connect(appSettings, &AppSettings::currentHtmlExporterChanged, htmlPreview, &HtmlPreview::setHtmlExporter);

    htmlPreview->setMinimumWidth(0.1 * qApp->primaryScreen()->size().width());
    htmlPreview->setObjectName(QStringLiteral("htmlpreview"));
    splitter->addWidget(htmlPreview);
    splitter->setStretchFactor(2, 1);

    QSettings windowSettings;
    if (windowSettings.contains(GW_SPLITTER_GEOMETRY_KEY)) {
        splitter->restoreState(windowSettings.value(GW_SPLITTER_GEOMETRY_KEY).toByteArray());
    }

    // Reassert the reference width after restoring legacy splitter state.  The
    // editor and preview keep the remaining space, while the tools pane stays
    // visually stable across launches and display sizes.
    const int availableWidth = qMax(0, width() - ProseSidebarWidth);
    if (sidebar->isVisible()) {
        splitter->setSizes({ProseSidebarWidth, availableWidth * 2 / 3, availableWidth / 3});
    }

    applyTheme();
}

void MainWindow::showWelcome()
{
    auto *dialog = new WelcomeDialog(QStringLiteral(APPVERSION), this);
    QSettings settings;
    dialog->setShowAfterUpdates(settings.value(THOTHPAD_SHOW_WELCOME_UPDATES_KEY, true).toBool());

    connect(dialog, &WelcomeDialog::newDocumentRequested, appAction(AppActions::New), &QAction::trigger);
    connect(dialog, &WelcomeDialog::openDocumentRequested, appAction(AppActions::Open), &QAction::trigger);
    connect(dialog, &WelcomeDialog::proseAwarenessRequested, this, [this]() {
        sidebar->setVisible(true);
        sidebar->setCurrentTabIndex(ProseAwarenessSidebarTab);
    });
    connect(dialog, &QDialog::finished, this, [dialog](int) {
        QSettings settings;
        settings.setValue(THOTHPAD_SHOW_WELCOME_UPDATES_KEY, dialog->showAfterUpdates());
    });
    dialog->open();
}

void MainWindow::showWelcomeIfNeeded()
{
    QSettings settings;
    const QString currentVersion = QStringLiteral(APPVERSION);
    const QString welcomeVersion = settings.value(THOTHPAD_WELCOME_VERSION_KEY).toString();
    const bool firstLaunch = welcomeVersion.isEmpty();
    const bool showAfterUpdates = settings.value(THOTHPAD_SHOW_WELCOME_UPDATES_KEY, true).toBool();

    if (firstLaunch || (showAfterUpdates && welcomeVersion != currentVersion)) {
        settings.setValue(THOTHPAD_WELCOME_VERSION_KEY, currentVersion);
        showWelcome();
    }
}

void MainWindow::runSpellCheck()
{
    SpellCheckDialog *dialog = new SpellCheckDialog(editor);
    connect(dialog, &SpellCheckDialog::finished, spelling, &SpellCheckDecorator::rehighlight);

    dialog->show();
}

} // namespace ghostwriter
