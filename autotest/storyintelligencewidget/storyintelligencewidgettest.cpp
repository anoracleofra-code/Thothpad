/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QApplication>
#include <QColor>
#include <QFont>
#include <QImage>
#include <QJsonArray>
#include <QJsonObject>
#include <QPixmap>
#include <QPushButton>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QTest>
#include <QWidget>

#include "../../src/prose/proseawarenesswidget.h"
#include "../../src/story/storyintelligencewidget.h"
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
    void goToEmitsExactAnnotationRange();
    void activityCardEmitsOperationSpecificUndo();
    void activityCardWithoutOperationHasNoUndoButton();
    void currentModelRevisionAllowsMutation();
    void staleModelRevisionRejectsMutation();
    void currentModelDocumentContextAllowsTools();
    void staleModelDocumentContextRejectsTools();
    void storyRailCarriesNoLocalStylesheet();
    void appStylesheetCoversAllStoryObjectNames();
    void kanagawaLotusPixelsMatchLeftSidebar();
    void lotusDarkSchemeHasNoWhiteSurfaces();
    void allBuiltInThemesKeepTextReadable();
};

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

    widget.appendActivityCard(
        QStringLiteral("Apply objective grammar fixes"),
        QStringLiteral("Applied 3 verified changes"),
        QStringLiteral("operation-123"));

    QPushButton *undo = buttonWithText(widget, QStringLiteral("Undo AI edit"));
    QVERIFY(undo);
    undo->click();

    QCOMPARE(spy.count(), 1);
    QCOMPARE(spy.takeFirst().at(0).toString(), QStringLiteral("operation-123"));
}

void StoryIntelligenceWidgetTest::activityCardWithoutOperationHasNoUndoButton()
{
    StoryIntelligenceWidget widget;
    widget.appendActivityCard(
        QStringLiteral("Prose scan completed"),
        QStringLiteral("Fresh findings are available"));

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
    QVERIFY(StoryToolHarness::modelDocumentContextCurrent(
        12, 12,
        QStringLiteral("/project/chapter.md"),
        QStringLiteral("/project/chapter.md")));
    QVERIFY(StoryToolHarness::modelDocumentContextCurrent(
        0, 0, QString(), QString()));
}

void StoryIntelligenceWidgetTest::staleModelDocumentContextRejectsTools()
{
    QVERIFY(!StoryToolHarness::modelDocumentContextCurrent(
        12, 13,
        QStringLiteral("/project/chapter.md"),
        QStringLiteral("/project/chapter.md")));
    QVERIFY(!StoryToolHarness::modelDocumentContextCurrent(
        12, 12,
        QStringLiteral("/project/chapter-one.md"),
        QStringLiteral("/project/chapter-two.md")));
    QVERIFY(!StoryToolHarness::modelDocumentContextCurrent(
        -1, 0, QString(), QString()));
}

void StoryIntelligenceWidgetTest::storyRailCarriesNoLocalStylesheet()
{
    StoryIntelligenceWidget widget;
    QVERIFY2(widget.styleSheet().isEmpty(),
             "StoryIntelligenceWidget must not carry a widget-local stylesheet; "
             "it is themed by the app-wide widgets.qss");
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

QTEST_MAIN(StoryIntelligenceWidgetTest)

#include "storyintelligencewidgettest.moc"
