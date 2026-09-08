/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QApplication>
#include <QClipboard>
#include <QColor>
#include <QComboBox>
#include <QFont>
#include <QFontDatabase>
#include <QImage>
#include <QJsonArray>
#include <QJsonObject>
#include <QLabel>
#include <QMainWindow>
#include <QPixmap>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QScrollArea>
#include <QScrollBar>
#include <QSignalSpy>
#include <QSplitter>
#include <QTabWidget>
#include <QTemporaryDir>
#include <QTest>
#include <QToolButton>
#include <QWidget>
#include <algorithm>

#include "../../src/appactions.h"
#include "../../src/prose/proseawarenesswidget.h"
#include "../../src/story/storyintelligencewidget.h"
#include "../../src/story/storyresponse.h"
#include "../../src/story/storytoolharness.h"
#include "../../src/theme/chromecolors.h"
#include "../../src/theme/stylesheetbuilder.h"
#include "../../src/theme/svgicontheme.h"
#include "../../src/theme/theme.h"
#include "../../src/theme/themerepository.h"

using namespace ghostwriter;

class StoryIntelligenceWidgetTest : public QObject
{
    Q_OBJECT

private slots:
    void scopeAndSessionControlsStayExplicit();
    void chatMessageActionsUseStableIdentityAndRespectBusyState();
    void goToEmitsExactAnnotationRange();
    void activityCardEmitsOperationSpecificUndo();
    void activityCardWithoutOperationHasNoUndoButton();
    void currentModelRevisionAllowsMutation();
    void staleModelRevisionRejectsMutation();
    void currentModelDocumentContextAllowsTools();
    void staleModelDocumentContextRejectsTools();
    void storyRailCarriesNoLocalStylesheet();
    void storyRailIsResizableAndWrapsStatusText();
    void conversationUsesAvailableHeightAndPreservesFullMessages();
    void appStylesheetCoversAllStoryObjectNames();
    void kanagawaLotusPixelsMatchLeftSidebar();
    void lotusDarkSchemeHasNoWhiteSurfaces();
    void allBuiltInThemesKeepTextReadable();
    void chatKeyboardAndBusyState();
    void chatFailuresAreVisibleAndReleaseBusyState();
    void storyResponseErrorsTakePriorityOverTextAndTools();
    void sceneNotesAreVisible();
    void storyLabelsInheritCardSurfaces();
    void nativePaletteFollowsThemeSwitches();
    void chatDoesNotInheritManuscriptFont();
    void storyShortcutHasNoCompetingAppAction();
    void characterCardsRetainMouseAndKeyboardActivation();
    void projectUnderstandingAndContextInspectorAreInspectable();
};

void StoryIntelligenceWidgetTest::scopeAndSessionControlsStayExplicit()
{
    StoryIntelligenceWidget widget;
    auto *modelCard = widget.findChild<QWidget *>("storyIntelligenceSetupSurface");
    auto *projectCard = widget.findChild<QWidget *>("storyIntelligenceProjectSurface");
    QVERIFY(modelCard && projectCard);
    QVERIFY(!modelCard->isAncestorOf(projectCard));
    QVERIFY(modelCard->findChild<QPushButton *>("storyIntelligenceApiKeyButton"));
    QVERIFY(projectCard->findChild<QPushButton *>("storyIntelligencePrimaryButton"));
    auto *input = widget.findChild<QPlainTextEdit *>("storyIntelligenceChatInput");
    auto *scope = widget.findChild<QComboBox *>("storyScopeCombo");
    auto *sessions = widget.findChild<QComboBox *>("storySessionCombo");
    auto *newSession = widget.findChild<QToolButton *>("storyNewSessionButton");
    auto *deleteSession = widget.findChild<QToolButton *>("storyDeleteSessionButton");
    QVERIFY(input && scope && sessions && newSession && deleteSession);
    QVERIFY(input->placeholderText().isEmpty());
    QSignalSpy scopeSpy(&widget, &StoryIntelligenceWidget::scopeModeChanged);
    QSignalSpy sessionSpy(&widget, &StoryIntelligenceWidget::sessionSelected);
    QSignalSpy newSpy(&widget, &StoryIntelligenceWidget::newSessionRequested);
    widget.setWorkspaceContext("chapter", "**CHAPTER THREE: NOTHING**", "Co-Writer", "Revision");
    QCOMPARE(scope->currentData().toString(), QString("chapter"));
    QCOMPARE(scope->currentText(), QString("Current chapter"));
    auto *context = widget.findChild<QLabel *>("storyContextHeadingLabel");
    QVERIFY(context);
    QCOMPARE(context->text(), QString("Chapter: CHAPTER THREE: NOTHING"));
    widget.setWorkspaceContext("chapter", "**CHAPTER FOUR**", "Co-Writer", "Revision");
    QCOMPARE(scope->currentText(), QString("Current chapter"));
    QCOMPARE(context->text(), QString("Chapter: CHAPTER FOUR"));
    widget.setWorkspaceContext("scene", "**CHAPTER FOUR**", "Co-Writer", "Revision", "chapter");
    QCOMPARE(scope->count(), 2);
    QCOMPARE(scope->findData("scene"), -1);
    QCOMPARE(scope->currentText(), QString("Current chapter"));
    QCOMPARE(context->text(), QString("Chapter: CHAPTER FOUR"));
    QVERIFY(!context->text().contains("Scene: CHAPTER"));
    QCOMPARE(scopeSpy.count(), 0);
    const QJsonArray choices{QJsonObject{{"id", "a"}, {"label", "First draft · Chapter one · Co-Writer"}},
                             QJsonObject{{"id", "b"}, {"label", "Alternative · Chapter one · Co-Writer"}}};
    widget.setSessions(choices, "a");
    QCOMPARE(sessions->count(), 2);
    QCOMPARE(sessions->currentData().toString(), QString("a"));
    QVERIFY(deleteSession->isEnabled());
    QCOMPARE(sessionSpy.count(), 0);
    sessions->setCurrentIndex(1);
    sessions->activated(1);
    QCOMPARE(sessionSpy.takeFirst().first().toString(), QString("b"));
    QSignalSpy deleteSpy(&widget, &StoryIntelligenceWidget::deleteSessionRequested);
    deleteSession->click();
    QCOMPARE(deleteSpy.takeFirst().first().toString(), QString("b"));
    newSession->click();
    QCOMPARE(newSpy.count(), 1);
    widget.setBusy(true);
    QVERIFY(!sessions->isEnabled() && !newSession->isEnabled() && !deleteSession->isEnabled() && !scope->isEnabled());
    widget.setBusy(false);
    QVERIFY(sessions->isEnabled() && newSession->isEnabled() && deleteSession->isEnabled() && scope->isEnabled());
    widget.setSessions({}, QString());
    QVERIFY(!deleteSession->isEnabled());
}

void StoryIntelligenceWidgetTest::chatMessageActionsUseStableIdentityAndRespectBusyState()
{
    StoryIntelligenceWidget widget;
    widget.appendChatMessage("assistant", "An answer", {}, {}, "answer-id");
    QSignalSpy actions(&widget, &StoryIntelligenceWidget::messageActionRequested);
    int count = 0;
    for (auto *button : widget.findChildren<QToolButton *>("storyChatActionButton")) {
        QCOMPARE(button->property("messageId").toString(), QString("answer-id"));
        const auto action = button->property("chatAction").toString();
        button->click();
        if (action == "copy") {
            QCOMPARE(QApplication::clipboard()->text(), QString("An answer"));
        } else {
            QCOMPARE(actions.last().at(0).toString(), QString("answer-id"));
            QCOMPARE(actions.last().at(1).toString(), action);
        }
        ++count;
    }
    QCOMPARE(count, 4);
    QCOMPARE(actions.count(), 3);
    widget.setBusy(true);
    widget.appendChatMessage("user", "New prompt", {}, {}, "prompt-id");
    for (auto *button : widget.findChildren<QToolButton *>("storyChatActionButton"))
        QCOMPARE(button->isEnabled(), button->property("chatAction").toString() == "copy");
    widget.setBusy(false);
    for (auto *button : widget.findChildren<QToolButton *>("storyChatActionButton"))
        QVERIFY(button->isEnabled());
}

void StoryIntelligenceWidgetTest::characterCardsRetainMouseAndKeyboardActivation()
{
    StoryIntelligenceWidget widget;
    widget.findChild<QTabWidget *>("storyIntelligenceContextTabs")->setCurrentIndex(2);
    widget.setCharacters({QJsonObject{{QStringLiteral("id"), QStringLiteral("mara")},
                                      {QStringLiteral("name"), QStringLiteral("Mara")},
                                      {QStringLiteral("role"), QStringLiteral("The traveller")}}});
    widget.resize(320, 900);
    widget.show();
    QTest::qWait(20);
    auto *card = widget.findChild<QPushButton *>(QStringLiteral("storyIntelligenceCharacterCard"));
    QVERIFY(card);
    QVERIFY(card->accessibleName().contains(QStringLiteral("Mara")));
    auto *avatar = card->findChild<QLabel *>(QStringLiteral("storyIntelligenceAvatar"));
    QVERIFY(avatar);
    QVERIFY(avatar->testAttribute(Qt::WA_TransparentForMouseEvents));
    QSignalSpy activated(&widget, &StoryIntelligenceWidget::characterActivated);
    QTest::mouseClick(card, Qt::LeftButton);
    QCOMPARE(widget.activeCharacterId(), QStringLiteral("mara"));
    QCOMPARE(activated.count(), 1);
    QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
    card = widget.findChild<QPushButton *>(QStringLiteral("storyIntelligenceCharacterCard"));
    QVERIFY(card);
    card->setFocus();
    QTest::keyClick(card, Qt::Key_Space);
    QVERIFY(widget.activeCharacterId().isEmpty());
    QCOMPARE(activated.count(), 2);
}

void StoryIntelligenceWidgetTest::projectUnderstandingAndContextInspectorAreInspectable()
{
    StoryIntelligenceWidget widget;
    auto *tabs = widget.findChild<QTabWidget *>(QStringLiteral("storyIntelligenceContextTabs"));
    QVERIFY(tabs);
    QCOMPARE(tabs->count(), 5);
    QCOMPARE(tabs->tabText(3), QStringLiteral("Project"));
    QCOMPARE(tabs->tabText(4), QStringLiteral("AI Context"));

    widget.setProjectUnderstanding(QJsonObject{
        {QStringLiteral("source_count"), 42},
        {QStringLiteral("entity_count"), 17},
        {QStringLiteral("open_conflict_count"), 2},
        {QStringLiteral("role_counts"), QJsonObject{{QStringLiteral("manuscript"), 5}, {QStringLiteral("world_reference"), 12}}},
    });
    auto *summary = widget.findChild<QLabel *>(QStringLiteral("storyProjectUnderstandingSummary"));
    QVERIFY(summary);
    QVERIFY(summary->text().contains(QStringLiteral("42 sources")));
    QVERIFY(summary->text().contains(QStringLiteral("17 entities")));
    QPushButton *orderButton = nullptr;
    for (auto *button : widget.findChildren<QPushButton *>()) {
        if (button->text() == QStringLiteral("Manuscript order…")) {
            orderButton = button;
            break;
        }
    }
    QVERIFY(orderButton);
    QVERIFY(orderButton->isEnabled());
    QSignalSpy orderSpy(&widget, &StoryIntelligenceWidget::manuscriptOrderRequested);
    orderButton->click();
    QCOMPARE(orderSpy.count(), 1);

    widget.setContextInspector(QJsonObject{
        {QStringLiteral("mode"), QStringLiteral("cold_reader")},
        {QStringLiteral("budget"), QJsonObject{{QStringLiteral("used_chars"), 1200}, {QStringLiteral("maximum_chars"), 40000}}},
        {QStringLiteral("epistemic_boundary"),
         QJsonObject{{QStringLiteral("position_bounded"), true}, {QStringLiteral("cross_file_order"), QStringLiteral("unresolved")}}},
        {QStringLiteral("current_document_masked"), true},
        {QStringLiteral("included"),
         QJsonArray{QJsonObject{{QStringLiteral("path"), QStringLiteral("chapter.md")},
                                {QStringLiteral("authority"), QStringLiteral("MANUSCRIPT_OBSERVED")},
                                {QStringLiteral("reason"), QStringLiteral("task relevance")}}}},
        {QStringLiteral("excluded"),
         QJsonArray{QJsonObject{{QStringLiteral("path"), QStringLiteral("future.md")},
                                {QStringLiteral("reason"), QStringLiteral("cross-file manuscript order unresolved; excluded to prevent future leakage")}}}},
    });
    auto *context = widget.findChild<QLabel *>(QStringLiteral("storyContextInspectorSummary"));
    QVERIFY(context);
    QVERIFY(context->text().contains(QStringLiteral("cold reader")));
    QVERIFY(context->text().contains(QStringLiteral("1 sources included")));
    QVERIFY(context->text().contains(QStringLiteral("withheld until Manuscript Order is set")));
    QVERIFY(context->text().contains(QStringLiteral("later current-document text withheld")));
    bool exclusionVisible = false;
    for (auto *label : widget.findChildren<QLabel *>()) {
        if (label->text().contains(QStringLiteral("future.md")) && label->text().contains(QStringLiteral("future leakage"))) {
            exclusionVisible = true;
            break;
        }
    }
    QVERIFY(exclusionVisible);

    auto *mode = widget.findChild<QComboBox *>(QStringLiteral("storyEpistemicModeCombo"));
    QVERIFY(mode);
    QSignalSpy spy(&widget, &StoryIntelligenceWidget::epistemicModeChanged);
    mode->setCurrentIndex(mode->findData(QStringLiteral("manuscript_only")));
    QCOMPARE(spy.last().at(0).toString(), QStringLiteral("manuscript_only"));
}

void StoryIntelligenceWidgetTest::storyResponseErrorsTakePriorityOverTextAndTools()
{
    QVERIFY(storyProviderRequiresSavedCredential(QStringLiteral("openrouter")));
    QVERIFY(storyProviderRequiresSavedCredential(QStringLiteral("opencode_zen")));
    QVERIFY(storyProviderRequiresSavedCredential(QStringLiteral("opencode_go")));
    QVERIFY(!storyProviderRequiresSavedCredential(QStringLiteral("ollama")));
    QVERIFY(!storyProviderRequiresSavedCredential(QStringLiteral("openai_compatible")));
    QJsonObject story{{QStringLiteral("message"), QStringLiteral("Hello")},
                      {QStringLiteral("tool_calls"), QJsonArray{QJsonObject{{QStringLiteral("id"), QStringLiteral("edit")}}}}};
    QJsonObject result{{QStringLiteral("story_intelligence"), story},
                       {QStringLiteral("llm_errors"), QJsonArray{QStringLiteral("max_tokens must be between 1 and 32768")}}};
    QJsonObject response{{QStringLiteral("ok"), true}, {QStringLiteral("result"), result}};
    QCOMPARE(storyResponseError(response), QStringLiteral("max_tokens must be between 1 and 32768"));
    result.remove(QStringLiteral("llm_errors"));
    story.remove(QStringLiteral("message"));
    result.insert(QStringLiteral("story_intelligence"), story);
    response.insert(QStringLiteral("result"), result);
    QVERIFY(storyResponseError(response).isEmpty()); // Tool-only rounds are valid.
    story.remove(QStringLiteral("tool_calls"));
    result.insert(QStringLiteral("story_intelligence"), story);
    response.insert(QStringLiteral("result"), result);
    QVERIFY(storyResponseError(response).contains(QStringLiteral("no text")));
    response.insert(QStringLiteral("ok"), false);
    response.insert(QStringLiteral("error"), QJsonObject{{QStringLiteral("message"), QStringLiteral("HTTP 401")}});
    QCOMPARE(storyResponseError(response), QStringLiteral("HTTP 401"));
}

void StoryIntelligenceWidgetTest::chatFailuresAreVisibleAndReleaseBusyState()
{
    StoryIntelligenceWidget widget;
    widget.setBusy(true);
    widget.showChatError(QStringLiteral("OpenRouter requires an API key."));
    bool errorSeen = false;
    bool failedStatus = false;
    for (auto *label : widget.findChildren<QLabel *>()) {
        if (label->text() == QStringLiteral("OpenRouter requires an API key."))
            errorSeen = true;
        if (label->text().startsWith(QStringLiteral("Response failed")))
            failedStatus = true;
        QVERIFY(label->text() != QStringLiteral("Response complete"));
    }
    QVERIFY(errorSeen);
    QVERIFY(failedStatus);
    QPushButton *send = nullptr;
    for (auto *button : widget.findChildren<QPushButton *>()) {
        if (button->property("storySend").toBool())
            send = button;
    }
    QVERIFY(send);
    QVERIFY(send->isEnabled());
}

namespace
{
QPushButton *buttonWithText(QWidget &root, const QString &text)
{
    const QList<QPushButton *> buttons = root.findChildren<QPushButton *>();
    for (QPushButton *button : buttons) {
        if (button->text() == text) {
            return button;
        }
    }
    return nullptr;
}

Theme loadBuiltInTheme(const QString &name, QString *error = nullptr)
{
    QTemporaryDir themeDir;
    ThemeRepository repository(themeDir.isValid() ? themeDir.path() : QString());
    QString loadError;
    Theme theme = repository.loadTheme(name, loadError);
    if (error) {
        *error = loadError;
    }
    return theme;
}

// Compiles the app-wide widgets.qss exactly like MainWindow::applyTheme does:
// ChromeColors -> StyleSheetBuilder -> qApp stylesheet. The test binary maps
// :/resources/widgets.qss at the real source file via storyintelligencewidget.qrc.
QString compileAppStyleSheet(const ChromeColors &chrome)
{
    QTemporaryDir iconDir;
    if (!iconDir.isValid()) {
        return {};
    }
    SvgIconTheme iconTheme(iconDir.path());
    const QFont font(QStringLiteral("Segoe UI"), 10);
    StyleSheetBuilder builder(chrome, &iconTheme, true, font, font, font);
    return builder.widgetStyleSheet();
}

bool colorsClose(const QColor &actual, const QColor &expected, int tolerance = 6)
{
    return qAbs(actual.red() - expected.red()) <= tolerance && qAbs(actual.green() - expected.green()) <= tolerance
        && qAbs(actual.blue() - expected.blue()) <= tolerance;
}

QString hexOf(const QColor &color)
{
    return color.isValid() ? color.name(QColor::HexRgb) : QStringLiteral("<invalid>");
}

void showForGrab(QWidget &widget)
{
    widget.resize(340, 720);
    widget.show();
    QApplication::processEvents();
    QApplication::processEvents();
    QTest::qWait(50);
    QApplication::processEvents();
}

QColor grabPoint(const QPixmap &grab, QWidget &root, QWidget &child, const QPoint &pointInChild)
{
    const QPoint mapped = child.mapTo(&root, pointInChild);
    const QImage image = grab.toImage();
    if (!image.rect().contains(mapped)) {
        return {};
    }
    return image.pixelColor(mapped);
}

double channelLuminance(double channel)
{
    return channel <= 0.03928 ? channel / 12.92 : qPow((channel + 0.055) / 1.055, 2.4);
}

double relativeLuminance(const QColor &color)
{
    return 0.2126 * channelLuminance(color.redF()) + 0.7152 * channelLuminance(color.greenF()) + 0.0722 * channelLuminance(color.blueF());
}

double contrastRatio(const QColor &first, const QColor &second)
{
    const double brightest = qMax(relativeLuminance(first), relativeLuminance(second));
    const double darkest = qMin(relativeLuminance(first), relativeLuminance(second));
    return (brightest + 0.05) / (darkest + 0.05);
}

void checkThemeModeReadable(const QString &name, const QString &mode, const ChromeColors &chrome)
{
    const QColor text = chrome.color(ChromeColors::Text);
    const QColor panel = chrome.color(ChromeColors::PanelFill);
    const QColor page = chrome.color(ChromeColors::SecondaryBackground);
    if (contrastRatio(text, panel) < 3.0 || contrastRatio(text, page) < 3.0 || panel == QColor(Qt::white) || page == QColor(Qt::white)) {
        qCritical() << name << mode << "text" << hexOf(text) << "PanelFill" << hexOf(panel) << "SecondaryBackground" << hexOf(page);
    }
    QVERIFY2(contrastRatio(text, panel) >= 3.0,
             qPrintable(QStringLiteral("%1 %2: text %3 on PanelFill %4 is unreadable").arg(name, mode, hexOf(text), hexOf(panel))));
    QVERIFY2(contrastRatio(text, page) >= 3.0,
             qPrintable(QStringLiteral("%1 %2: text %3 on SecondaryBackground %4 is unreadable").arg(name, mode, hexOf(text), hexOf(page))));
    QVERIFY2(panel != QColor(Qt::white), qPrintable(QStringLiteral("%1 %2: card surface is pure white").arg(name, mode)));
    QVERIFY2(page != QColor(Qt::white), qPrintable(QStringLiteral("%1 %2: panel surface is pure white").arg(name, mode)));
}
}

void StoryIntelligenceWidgetTest::goToEmitsExactAnnotationRange()
{
    StoryIntelligenceWidget widget;
    QJsonObject annotation;
    annotation.insert(QStringLiteral("id"), QStringLiteral("mark-1"));
    annotation.insert(QStringLiteral("category"), QStringLiteral("voice"));
    annotation.insert(QStringLiteral("comment"), QStringLiteral("Inspect this phrase."));
    annotation.insert(QStringLiteral("quote"), QStringLiteral("ember"));
    annotation.insert(QStringLiteral("start_utf16"), 7);
    annotation.insert(QStringLiteral("end_utf16"), 12);
    widget.setAnnotations(QJsonArray{annotation});

    QSignalSpy spy(&widget, &StoryIntelligenceWidget::annotationNavigationRequested);
    QPushButton *goTo = buttonWithText(widget, QStringLiteral("Go to"));
    QVERIFY(goTo);

    goTo->click();

    QCOMPARE(spy.count(), 1);
    const QList<QVariant> arguments = spy.takeFirst();
    QCOMPARE(arguments.at(0).toInt(), 7);
    QCOMPARE(arguments.at(1).toInt(), 12);
    QCOMPARE(arguments.at(2).toString(), QStringLiteral("ember"));
}

void StoryIntelligenceWidgetTest::activityCardEmitsOperationSpecificUndo()
{
    StoryIntelligenceWidget widget;
    QSignalSpy spy(&widget, &StoryIntelligenceWidget::undoAgentTransactionRequested);

    widget.appendActivityCard(QStringLiteral("Apply objective grammar fixes"), QStringLiteral("Applied 3 verified changes"), QStringLiteral("operation-123"));

    QPushButton *undo = buttonWithText(widget, QStringLiteral("Undo AI edit"));
    QVERIFY(undo);
    undo->click();

    QCOMPARE(spy.count(), 1);
    QCOMPARE(spy.takeFirst().at(0).toString(), QStringLiteral("operation-123"));
}

void StoryIntelligenceWidgetTest::activityCardWithoutOperationHasNoUndoButton()
{
    StoryIntelligenceWidget widget;
    widget.appendActivityCard(QStringLiteral("Prose scan completed"), QStringLiteral("Fresh findings are available"));

    QVERIFY(!buttonWithText(widget, QStringLiteral("Undo AI edit")));
}

void StoryIntelligenceWidgetTest::currentModelRevisionAllowsMutation()
{
    QVERIFY(StoryToolHarness::mutationRevisionCurrent(12, 12));
}

void StoryIntelligenceWidgetTest::staleModelRevisionRejectsMutation()
{
    QVERIFY(!StoryToolHarness::mutationRevisionCurrent(12, 13));
    QVERIFY(!StoryToolHarness::mutationRevisionCurrent(-1, 0));
}

void StoryIntelligenceWidgetTest::currentModelDocumentContextAllowsTools()
{
    QVERIFY(StoryToolHarness::modelDocumentContextCurrent(12, 12, QStringLiteral("/project/chapter.md"), QStringLiteral("/project/chapter.md")));
    QVERIFY(StoryToolHarness::modelDocumentContextCurrent(0, 0, QString(), QString()));
}

void StoryIntelligenceWidgetTest::staleModelDocumentContextRejectsTools()
{
    QVERIFY(!StoryToolHarness::modelDocumentContextCurrent(12, 13, QStringLiteral("/project/chapter.md"), QStringLiteral("/project/chapter.md")));
    QVERIFY(!StoryToolHarness::modelDocumentContextCurrent(12, 12, QStringLiteral("/project/chapter-one.md"), QStringLiteral("/project/chapter-two.md")));
    QVERIFY(!StoryToolHarness::modelDocumentContextCurrent(-1, 0, QString(), QString()));
}

void StoryIntelligenceWidgetTest::storyRailCarriesNoLocalStylesheet()
{
    StoryIntelligenceWidget widget;
    QVERIFY2(widget.styleSheet().isEmpty(),
             "StoryIntelligenceWidget must not carry a widget-local stylesheet; "
             "it is themed by the app-wide widgets.qss");
}

void StoryIntelligenceWidgetTest::storyRailIsResizableAndWrapsStatusText()
{
    StoryIntelligenceWidget widget;
    QVERIFY(widget.minimumWidth() <= 280);
    QVERIFY(widget.maximumWidth() > 1000);
    QVERIFY(widget.sizePolicy().horizontalPolicy() != QSizePolicy::Fixed);
    widget.setProviderSummary(QStringLiteral("OpenRouter"), QStringLiteral("long/model-name"), false, QStringLiteral("openrouter"));
    auto *status = widget.findChild<QLabel *>(QStringLiteral("storyIntelligenceKeyState"));
    QVERIFY(status);
    QVERIFY(status->wordWrap());
    for (int width : {280, 360, 520}) {
        widget.resize(width, 700);
        widget.show();
        QTest::qWait(20);
        QCOMPARE(widget.width(), width);
        QVERIFY(status->width() <= widget.width());
    }
}

void StoryIntelligenceWidgetTest::conversationUsesAvailableHeightAndPreservesFullMessages()
{
#ifdef Q_OS_WIN
    QFontDatabase::addApplicationFont("C:/Windows/Fonts/segoeui.ttf");
    QFontDatabase::addApplicationFont("C:/Windows/Fonts/seguisb.ttf");
#endif
    const QString previousStyle = qApp->styleSheet();
    qApp->setStyleSheet(compileAppStyleSheet(loadBuiltInTheme("Kanagawa Lotus").lightChromeColors()));
    StoryIntelligenceWidget widget;
    widget.setProviderSummary("OpenCode Zen", "nemotron-3-ultra-free", true, "opencode_zen");
    widget.appendChatMessage("user", "Can you help me strengthen the dialogue in this chapter?", {}, {}, "user-1");
    const QString paragraph =
        "The dialogue gives each character a clear aim. Let the disagreement build through their choices, and keep the final exchange brief enough to leave "
        "room for the reader.\n\n";
    widget.appendChatMessage("assistant", paragraph.repeated(5), {}, {}, "answer-1");
    auto *scroll = widget.findChild<QScrollArea *>("storyIntelligenceChatScroll");
    auto *sections = widget.findChild<QSplitter *>("storyIntelligenceSections");
    QVERIFY(scroll && sections);
    auto *tabs = widget.findChild<QTabWidget *>("storyIntelligenceContextTabs");
    QVERIFY(tabs);
    QCOMPARE(tabs->count(), 5);
    QCOMPARE(tabs->tabText(0), QString("Model"));
    QCOMPARE(tabs->tabText(1), QString("Scene Context"));
    QCOMPARE(tabs->tabText(2), QString("Characters"));
    QCOMPARE(tabs->tabText(3), QString("Project"));
    QCOMPARE(tabs->tabText(4), QString("AI Context"));
    for (int width : {340, 560, 280, 340}) {
        widget.resize(width, 1000);
        widget.show();
        sections->setSizes({290, 550});
        QTest::qWait(80);
        QVERIFY2(scroll->height() > 350, qPrintable(QString("Chat height %1 at sidebar width %2").arg(scroll->height()).arg(width)));
        const auto messages = widget.findChildren<QLabel *>("storyIntelligenceBubbleText");
        QCOMPARE(messages.size(), 2);
        for (auto *message : messages) {
            QVERIFY2(message->height() >= message->heightForWidth(message->width()), "Message text is clipped after sidebar resizing");
            QVERIFY(message->parentWidget()->rect().contains(message->geometry()));
        }
        QVERIFY(scroll->verticalScrollBar()->maximum() > 0);
        scroll->verticalScrollBar()->setValue(0);
        QVERIFY(scroll->viewport()->rect().contains(messages.first()->mapTo(scroll->viewport(), QPoint(0, 0))));
        if (width == 340)
            QVERIFY(widget.grab().save("chat-layout-narrow.png"));
        if (width == 560)
            QVERIFY(widget.grab().save("chat-layout-wide.png"));
    }
    for (int index : {1, 2, 3, 4, 0}) {
        tabs->setCurrentIndex(index);
        QTest::qWait(30);
        QVERIFY(tabs->currentWidget()->isVisible());
        QVERIFY(widget.grab().save(QString("chat-context-tab-%1.png").arg(index)));
    }
    auto *panel = widget.findChild<QWidget *>("storyIntelligenceChatPanel");
    QVERIFY(panel);
    for (bool dark : {false, true}) {
        const auto theme = loadBuiltInTheme("Kanagawa Lotus");
        const ChromeColors chrome(dark ? theme.darkColorScheme() : theme.lightColorScheme(), theme.name());
        qApp->setStyleSheet(compileAppStyleSheet(chrome));
        QTest::qWait(30);
        const QColor surface = panel->grab().toImage().pixelColor(2, 2);
        QVERIFY2(colorsClose(surface, chrome.color(ChromeColors::SecondaryBackground)), qPrintable(hexOf(surface)));
        tabs->setCurrentIndex(2);
        QTest::qWait(20);
        const QColor contextSurface = tabs->currentWidget()->grab().toImage().pixelColor(2, 2);
        QVERIFY2(colorsClose(contextSurface, chrome.color(ChromeColors::SecondaryBackground)), qPrintable(hexOf(contextSurface)));
    }
    qApp->setStyleSheet(previousStyle);
}

void StoryIntelligenceWidgetTest::appStylesheetCoversAllStoryObjectNames()
{
    QString error;
    const Theme theme = loadBuiltInTheme(QStringLiteral("Kanagawa Lotus"), &error);
    QVERIFY2(error.isNull(), qPrintable(error));
    const QString compiled = compileAppStyleSheet(theme.lightChromeColors());
    QVERIFY2(!compiled.isNull(), "StyleSheetBuilder failed to compile widgets.qss");
    QVERIFY(!compiled.isEmpty());

    // Every objectName the rail's former local QSS referenced must have an
    // equivalent rule in the app stylesheet (same selectors, theme $variables).
    const QStringList objectNames = {
        QStringLiteral("storyIntelligenceHeaderSurface"),   QStringLiteral("storyIntelligenceComposer"),
        QStringLiteral("storyIntelligenceSetupSurface"),    QStringLiteral("storyIntelligenceSceneCard"),
        QStringLiteral("storyIntelligenceSuggestionCard"),  QStringLiteral("storyIntelligenceActivityCard"),
        QStringLiteral("storyIntelligenceCharacterCard"),   QStringLiteral("storyIntelligenceTitle"),
        QStringLiteral("storyIntelligenceSectionTitle"),    QStringLiteral("storyIntelligenceCaption"),
        QStringLiteral("storyIntelligenceCardHeading"),     QStringLiteral("storyIntelligenceBubbleSpeaker"),
        QStringLiteral("storyIntelligenceActivityTitle"),   QStringLiteral("storyIntelligenceMutedLabel"),
        QStringLiteral("storyIntelligenceKeyState"),        QStringLiteral("storyIntelligenceStatus"),
        QStringLiteral("storyIntelligenceSuggestionQuote"), QStringLiteral("storyIntelligenceActivityCaption"),
        QStringLiteral("storyIntelligenceActivityDetail"),  QStringLiteral("storyIntelligenceSuggestionReplacement"),
        QStringLiteral("storyIntelligencePrimaryButton"),   QStringLiteral("storyIntelligenceApplyButton"),
        QStringLiteral("storyIntelligenceUndoButton"),      QStringLiteral("storyIntelligenceApiKeyButton"),
        QStringLiteral("storyIntelligenceTextButton"),      QStringLiteral("storyIntelligenceCollapseButton"),
        QStringLiteral("storyIntelligenceAssistantBubble"), QStringLiteral("storyIntelligenceUserBubble"),
        QStringLiteral("storyIntelligenceChatInput"),       QStringLiteral("storyIntelligenceScrollArea"),
        QStringLiteral("storyIntelligenceChatScroll"),      QStringLiteral("storyIntelligenceContent"),
        QStringLiteral("storyIntelligenceChatContent"),     QStringLiteral("storyIntelligenceDivider"),
    };
    for (const QString &name : objectNames) {
        QVERIFY2(compiled.contains(QLatin1Char('#') + name), qPrintable(QStringLiteral("App stylesheet has no rule for #%1").arg(name)));
    }
    QVERIFY(compiled.contains(QStringLiteral("ghostwriter--StoryIntelligenceWidget")));
    // The app sheet is theme-driven: no system-palette roles may leak into it.
    QVERIFY2(!compiled.contains(QStringLiteral("palette(")), "App stylesheet must not use palette() roles");
}

void StoryIntelligenceWidgetTest::kanagawaLotusPixelsMatchLeftSidebar()
{
    QString error;
    const Theme theme = loadBuiltInTheme(QStringLiteral("Kanagawa Lotus"), &error);
    QVERIFY2(error.isNull(), qPrintable(error));
    const ChromeColors chrome = theme.lightChromeColors();
    const QColor expectedPanel = chrome.color(ChromeColors::SecondaryBackground);
    const QColor expectedCard = chrome.color(ChromeColors::PanelFill);
    const QColor expectedHeader = chrome.color(ChromeColors::TertiaryFill);
    QCOMPARE(hexOf(expectedPanel), QStringLiteral("#efe6c1"));
    QCOMPARE(hexOf(expectedCard), QStringLiteral("#eae1b9"));
    QCOMPARE(hexOf(expectedHeader), QStringLiteral("#ebe0b6"));

    qApp->setStyleSheet(compileAppStyleSheet(chrome));

    StoryIntelligenceWidget story;
    ProseAwarenessWidget prose;
    showForGrab(story);
    showForGrab(prose);

    const QPixmap storyGrab = story.grab();
    const QPixmap proseGrab = prose.grab();
    QVERIFY(!storyGrab.isNull());
    QVERIFY(!proseGrab.isNull());

    QWidget *storyCard = story.findChild<QWidget *>(QStringLiteral("storyIntelligenceSetupSurface"));
    QWidget *storyContent = story.findChild<QWidget *>(QStringLiteral("storyIntelligenceContent"));
    QWidget *storyHeader = story.findChild<QWidget *>(QStringLiteral("storyIntelligenceHeaderSurface"));
    QWidget *proseContent = prose.findChild<QWidget *>(QStringLiteral("proseAwarenessContent"));
    QVERIFY(storyCard);
    QVERIFY(storyContent);
    QVERIFY(storyHeader);
    QVERIFY(proseContent);

    // Sample inside borders/margins so only the surface fill is read: cards
    // carry a 1px border with 7px corner radii, content widgets a 14-16px
    // layout margin.
    const QColor cardPixel = grabPoint(storyGrab, story, *storyCard, QPoint(2, storyCard->height() / 2));
    const QColor panelPixel = grabPoint(storyGrab, story, *storyContent, QPoint(7, 7));
    const QColor headerPixel = grabPoint(storyGrab, story, *storyHeader, QPoint(2, storyHeader->height() / 2));
    const QColor prosePixel = grabPoint(proseGrab, prose, *proseContent, QPoint(8, 8));
    QVERIFY(cardPixel.isValid());
    QVERIFY(panelPixel.isValid());
    QVERIFY(headerPixel.isValid());
    QVERIFY(prosePixel.isValid());

    QVERIFY2(colorsClose(panelPixel, expectedPanel),
             qPrintable(QStringLiteral("Rail panel %1 != SecondaryBackground %2").arg(hexOf(panelPixel), hexOf(expectedPanel))));
    QVERIFY2(colorsClose(cardPixel, expectedCard), qPrintable(QStringLiteral("Rail card %1 != PanelFill %2").arg(hexOf(cardPixel), hexOf(expectedCard))));
    QVERIFY2(colorsClose(headerPixel, expectedHeader),
             qPrintable(QStringLiteral("Rail header %1 != TertiaryFill %2").arg(hexOf(headerPixel), hexOf(expectedHeader))));
    // "One app": the right rail panel and the left sidebar panel resolve to
    // the same pixels because both use SecondaryBackground.
    QVERIFY2(colorsClose(prosePixel, expectedPanel),
             qPrintable(QStringLiteral("Sidebar panel %1 != SecondaryBackground %2").arg(hexOf(prosePixel), hexOf(expectedPanel))));
    QVERIFY2(colorsClose(panelPixel, prosePixel, 1), qPrintable(QStringLiteral("Rail panel %1 != sidebar panel %2").arg(hexOf(panelPixel), hexOf(prosePixel))));

    qApp->setStyleSheet(QString());
}

void StoryIntelligenceWidgetTest::lotusDarkSchemeHasNoWhiteSurfaces()
{
    QString error;
    const Theme theme = loadBuiltInTheme(QStringLiteral("Kanagawa Lotus"), &error);
    QVERIFY2(error.isNull(), qPrintable(error));
    const ChromeColors chrome = theme.darkChromeColors();
    const QColor expectedPanel = chrome.color(ChromeColors::SecondaryBackground);
    const QColor expectedCard = chrome.color(ChromeColors::PanelFill);
    QCOMPARE(hexOf(expectedPanel), QStringLiteral("#0f0f0f"));
    QCOMPARE(hexOf(expectedCard), QStringLiteral("#141414"));

    qApp->setStyleSheet(compileAppStyleSheet(chrome));

    StoryIntelligenceWidget story;
    showForGrab(story);
    const QPixmap grab = story.grab();
    QVERIFY(!grab.isNull());

    QWidget *storyCard = story.findChild<QWidget *>(QStringLiteral("storyIntelligenceSetupSurface"));
    QWidget *storyContent = story.findChild<QWidget *>(QStringLiteral("storyIntelligenceContent"));
    QVERIFY(storyCard);
    QVERIFY(storyContent);

    const QColor cardPixel = grabPoint(grab, story, *storyCard, QPoint(2, storyCard->height() / 2));
    const QColor panelPixel = grabPoint(grab, story, *storyContent, QPoint(7, 7));
    QVERIFY(cardPixel.isValid());
    QVERIFY(panelPixel.isValid());

    QVERIFY2(colorsClose(panelPixel, expectedPanel),
             qPrintable(QStringLiteral("Dark rail panel %1 != SecondaryBackground %2").arg(hexOf(panelPixel), hexOf(expectedPanel))));
    QVERIFY2(colorsClose(cardPixel, expectedCard), qPrintable(QStringLiteral("Dark rail card %1 != PanelFill %2").arg(hexOf(cardPixel), hexOf(expectedCard))));
    for (const QColor &pixel : {panelPixel, cardPixel}) {
        QVERIFY2(pixel.red() < 64 && pixel.green() < 64 && pixel.blue() < 64,
                 qPrintable(QStringLiteral("Dark rail surface is not dark: %1").arg(hexOf(pixel))));
    }

    qApp->setStyleSheet(QString());
}

void StoryIntelligenceWidgetTest::allBuiltInThemesKeepTextReadable()
{
    QTemporaryDir themeDir;
    QVERIFY(themeDir.isValid());
    ThemeRepository repository(themeDir.path());
    const QStringList themes = repository.availableThemes();
    QVERIFY(themes.size() >= 7);
    for (const QString &name : themes) {
        QString error;
        const Theme theme = repository.loadTheme(name, error);
        QVERIFY2(error.isNull(), qPrintable(name + QStringLiteral(": ") + error));
        // Note: ChromeColors instances must be constructed in place and passed
        // by reference; ChromeColors is not safely copyable.
        {
            const ChromeColors chrome(theme.lightColorScheme(), name);
            checkThemeModeReadable(name, QStringLiteral("light"), chrome);
        }
        {
            const ChromeColors chrome(theme.darkColorScheme(), name);
            checkThemeModeReadable(name, QStringLiteral("dark"), chrome);
        }
    }
}

void StoryIntelligenceWidgetTest::chatKeyboardAndBusyState()
{
    StoryIntelligenceWidget widget;
    showForGrab(widget);
    auto *input = widget.findChild<QPlainTextEdit *>(QStringLiteral("storyIntelligenceChatInput"));
    auto *status = widget.findChild<QLabel *>(QStringLiteral("storyIntelligenceStatus"));
    QVERIFY(input);
    QVERIFY(status);
    QSignalSpy spy(&widget, &StoryIntelligenceWidget::chatRequested);
    input->setFocus();
    input->setPlainText(QStringLiteral("First line"));
    QTest::keyClick(input, Qt::Key_End);
    QTest::keyClick(input, Qt::Key_Return, Qt::ShiftModifier);
    QCOMPARE(spy.count(), 0);
    QVERIFY(input->toPlainText().contains(QChar('\n')));
    QTest::keyClick(input, Qt::Key_Return);
    QCOMPARE(spy.count(), 1);
    QCOMPARE(spy.takeFirst().at(0).toString(), QStringLiteral("First line"));
    QVERIFY(input->toPlainText().isEmpty());
    QVERIFY(status->isHidden());
    input->setPlainText(QStringLiteral("Keep this draft"));
    widget.setBusy(true);
    QVERIFY(!status->isHidden());
    QTest::keyClick(input, Qt::Key_Return, Qt::ControlModifier);
    QCOMPARE(spy.count(), 0);
    QCOMPARE(input->toPlainText(), QStringLiteral("Keep this draft"));
    widget.setBusy(false);
    QVERIFY(status->isHidden());
    widget.setStatusMessage(QStringLiteral("Engine unavailable"));
    QVERIFY(!status->isHidden());
}

void StoryIntelligenceWidgetTest::sceneNotesAreVisible()
{
    StoryIntelligenceWidget widget;
    widget.setSceneContext(QJsonObject{{QStringLiteral("notes"), QStringLiteral("Keep the secret hidden.")}});
    const auto labels = widget.findChildren<QLabel *>();
    QVERIFY(std::any_of(labels.cbegin(), labels.cend(), [](const QLabel *label) {
        return label->text().contains(QStringLiteral("Keep the secret hidden.")) && !label->isHidden();
    }));
}

void StoryIntelligenceWidgetTest::storyLabelsInheritCardSurfaces()
{
    const Theme theme = loadBuiltInTheme(QStringLiteral("Kanagawa Lotus"));
    qApp->setStyleSheet(compileAppStyleSheet(theme.lightChromeColors()));
    StoryIntelligenceWidget widget;
    showForGrab(widget);
    auto *label = widget.findChild<QLabel *>(QStringLiteral("storyIntelligenceProviderLabel"));
    QVERIFY(label);
    const QColor pixel = grabPoint(widget.grab(), widget, *label, QPoint(label->width() - 2, 1));
    QVERIFY2(colorsClose(pixel, theme.lightChromeColors().color(ChromeColors::PanelFill)), qPrintable(hexOf(pixel)));
    qApp->setStyleSheet(QString());
}

void StoryIntelligenceWidgetTest::nativePaletteFollowsThemeSwitches()
{
    const QPalette original = qApp->palette();
    const Theme theme = loadBuiltInTheme(QStringLiteral("Kanagawa Lotus"));
    QWidget nativeSurface;
    for (const bool dark : {false, true, false}) {
        const ChromeColors chrome(dark ? theme.darkColorScheme() : theme.lightColorScheme(), theme.name());
        qApp->setPalette(StyleSheetBuilder::widgetPalette(chrome));
        QApplication::processEvents();
        QCOMPARE(nativeSurface.palette().color(QPalette::Window), chrome.color(ChromeColors::Background));
        QCOMPARE(nativeSurface.palette().color(QPalette::Text), chrome.color(ChromeColors::Text));
        QCOMPARE(nativeSurface.palette().color(QPalette::Disabled, QPalette::Text), chrome.color(ChromeColors::Text, ChromeColors::DisabledState));
    }
    qApp->setPalette(original);
}

void StoryIntelligenceWidgetTest::chatDoesNotInheritManuscriptFont()
{
    const Theme theme = loadBuiltInTheme(QStringLiteral("Kanagawa Lotus"));
    QTemporaryDir iconDir;
    SvgIconTheme icons(iconDir.path());
    const QFont manuscriptFont(QStringLiteral("Courier New"), 28);
    StyleSheetBuilder builder(theme.lightChromeColors(), &icons, true, manuscriptFont, manuscriptFont, manuscriptFont);
    qApp->setStyleSheet(builder.widgetStyleSheet());
    QMainWindow window;
    auto *story = new StoryIntelligenceWidget(&window);
    window.setCentralWidget(story);
    showForGrab(window);
    auto *input = story->findChild<QPlainTextEdit *>(QStringLiteral("storyIntelligenceChatInput"));
    QVERIFY(input);
    QVERIFY(input->font().family() != manuscriptFont.family());
    QVERIFY(input->font().pointSizeF() < manuscriptFont.pointSizeF());
    qApp->setStyleSheet(QString());
}

void StoryIntelligenceWidgetTest::storyShortcutHasNoCompetingAppAction()
{
    QMainWindow window;
    SvgIconTheme icons(QStringLiteral(":/icons"));
    KActionCollection collection(&window);
    AppActions actions(&collection, &icons);
    const QKeySequence shortcut(QStringLiteral("Ctrl+Shift+A"));
    for (QAction *action : collection.actions()) {
        QVERIFY2(!action->shortcuts().contains(shortcut), qPrintable(action->text()));
    }
    QCOMPARE(actions.get(AppActions::Deselect)->shortcut(), QKeySequence(QStringLiteral("Ctrl+Alt+Shift+A")));
}

QTEST_MAIN(StoryIntelligenceWidgetTest)

#include "storyintelligencewidgettest.moc"
