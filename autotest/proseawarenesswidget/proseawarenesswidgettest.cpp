/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QAbstractButton>
#include <QAbstractItemModel>
#include <QComboBox>
#include <QElapsedTimer>
#include <QFrame>
#include <QLabel>
#include <QListView>
#include <QMenu>
#include <QPixmap>
#include <QPushButton>
#include <QScrollArea>
#include <QScrollBar>
#include <QSignalSpy>
#include <QTest>
#include <QToolButton>
#include <QTreeWidget>

#include <algorithm>
#include <functional>

#include "../../src/prose/proseawarenesswidget.h"
#include "../../src/prose/prosecontroller.h"

using namespace ghostwriter;
namespace
{
// The combo boxes own internal QListView popups; match the suggestions list explicitly.
QListView *findFindingView(const ProseAwarenessWidget &widget)
{
    for (QListView *view : widget.findChildren<QListView *>()) {
        if (view->accessibleName() == QStringLiteral("Prose observations")) {
            return view;
        }
    }
    return nullptr;
}
}

class ProseAwarenessWidgetTest : public QObject
{
    Q_OBJECT

private slots:
    void programmaticLensUpdatesDoNotEmitUserChanges();
    void continuityNotesStayOutOfTheSidebar();
    void lensColorsUseCompactSwatches();
    void advancedControlsStayInMenus();
    void narrowLayoutKeepsControlsInsideViewport();
    void shortLayoutScrollsVertically();
    void partOfSpeechLensesUseConciseLabels();
    void selectedLensShowsOnlyOrderedOccurrences();
    void largeLensLoadsSuggestionsInPages();
    void snapshotCountsAreIndependentOfLoadedRows();
    void disabledLensImmediatelyDisplaysZeroCount();
    void firstReportedLensOpensItsLoadedDetails();
    void populatedLensSelectsFirstFinding();
    void emptyLensUsesOneTruthfulStatus();
    void snapshotProtocolPagesContinueUntilComplete();
    void reportOverlaySuppressionsKeepUnrelatedSpans();
    void selectingDiagnosticSynchronizesFindingDetails();
    void observationActionsExcludeApply();
    void nextObservationAdvancesWithinSelectedLens();
    void nextObservationContinuesAfterLoadingAnotherPage();
    void destructiveActionsStayTogetherAndEmitScope();
    void findingsUseVirtualizedModelAtScale();
    void performanceSettingsActionEmitsSignal();
    void repetitionLensRegisteredWithDefaults();
    void repetitionLensToggleRoundTrip();
    void syntheticRepetitionFindingFlowsToSuggestionsModel();
    void scanButtonTriggersDocumentReviewRequest();
    void scanButtonDisabledWhenEngineNotReady();
    void scanButtonAnchorsReviewSetupAndShortcutWorks();
};

void ProseAwarenessWidgetTest::partOfSpeechLensesUseConciseLabels()
{
    ProseAwarenessWidget widget;
    QCOMPARE(widget.categoryFindingLabel(QStringLiteral("possible_adverbs")), QStringLiteral("Adverb"));
    QCOMPARE(widget.categoryFindingLabel(QStringLiteral("possible_adjectives")), QStringLiteral("Adjective"));
    QCOMPARE(widget.categoryFindingLabel(QStringLiteral("possible_verbs")), QStringLiteral("Verb"));

    QTreeWidget *lensTree = nullptr;
    for (QTreeWidget *tree : widget.findChildren<QTreeWidget *>()) {
        if (tree->accessibleName() == QStringLiteral("Prose lenses")) {
            lensTree = tree;
            break;
        }
    }
    QVERIFY(lensTree);
    QStringList labels;
    QHash<QString, Qt::CheckState> states;
    for (int index = 0; index < lensTree->topLevelItemCount(); ++index) {
        QTreeWidgetItem *item = lensTree->topLevelItem(index);
        labels.append(item->text(0));
        states.insert(item->text(0), item->checkState(0));
    }
    QVERIFY(labels.contains(QStringLiteral("Adverbs")));
    QVERIFY(labels.contains(QStringLiteral("Adjectives")));
    QVERIFY(labels.contains(QStringLiteral("Verbs")));
    QCOMPARE(states.value(QStringLiteral("Adjectives")), Qt::Checked);
    QCOMPARE(states.value(QStringLiteral("Verbs")), Qt::Checked);
}

void ProseAwarenessWidgetTest::continuityNotesStayOutOfTheSidebar()
{
    ProseAwarenessWidget widget;
    QVERIFY(!widget.findChild<QWidget *>(QStringLiteral("proseAwarenessFactsSurface")));
    QVERIFY(!widget.findChild<QToolButton *>(QStringLiteral("proseAwarenessLockedFactsToggle")));
    widget.setLockedFacts({QStringLiteral("Lazan keeps the brass monocle")});
    QCOMPARE(widget.lockedFacts(), QStringList({QStringLiteral("Lazan keeps the brass monocle")}));
}

void ProseAwarenessWidgetTest::shortLayoutScrollsVertically()
{
    ProseAwarenessWidget widget;
    widget.resize(320, 600);
    widget.show();
    QTest::qWait(50);

    auto *scrollArea = widget.findChild<QScrollArea *>(QStringLiteral("proseAwarenessScrollArea"));
    QVERIFY(scrollArea);
    QVERIFY(scrollArea->verticalScrollBar()->maximum() > 0);
    QCOMPARE(scrollArea->horizontalScrollBar()->maximum(), 0);
}

void ProseAwarenessWidgetTest::programmaticLensUpdatesDoNotEmitUserChanges()
{
    ProseAwarenessWidget widget;
    QSignalSpy enabledSpy(&widget, &ProseAwarenessWidget::categoryEnabledChanged);

    widget.setCategoryState(QStringLiteral("possible_adverbs"), false, QColor("#123456"));
    widget.setCategoryMode(QStringLiteral("possible_adverbs"), QStringLiteral("report"));

    QCOMPARE(enabledSpy.count(), 0);

    QTreeWidget *lensTree = nullptr;
    for (QTreeWidget *tree : widget.findChildren<QTreeWidget *>()) {
        if (tree->accessibleName() == QStringLiteral("Prose lenses")) {
            lensTree = tree;
            break;
        }
    }
    QVERIFY(lensTree);
    QVERIFY(lensTree->topLevelItemCount() > 1);
    lensTree->topLevelItem(1)->setCheckState(0, Qt::Checked);
    QCOMPARE(enabledSpy.count(), 1);
}

void ProseAwarenessWidgetTest::lensColorsUseCompactSwatches()
{
    ProseAwarenessWidget widget;
    QTreeWidget *lensTree = nullptr;
    for (QTreeWidget *tree : widget.findChildren<QTreeWidget *>()) {
        if (tree->accessibleName() == QStringLiteral("Prose lenses")) {
            lensTree = tree;
            break;
        }
    }
    QVERIFY(lensTree);
    const QList<QColor> colors = {QColor("#FBBF24"), QColor("#102030")};
    for (const QColor &color : colors) {
        widget.setCategoryState(QStringLiteral("possible_adverbs"), true, color);
        QTreeWidgetItem *item = lensTree->topLevelItem(1);
        QVERIFY(item->icon(0).isNull());
        QVERIFY(!item->icon(1).isNull());
        QCOMPARE(item->background(0).style(), Qt::NoBrush);
        QCOMPARE(item->foreground(0).style(), Qt::NoBrush);
    }
    QVERIFY(lensTree->isHeaderHidden());
}

void ProseAwarenessWidgetTest::advancedControlsStayInMenus()
{
    ProseAwarenessWidget widget;
    QToolButton *toolsButton = nullptr;
    for (QToolButton *button : widget.findChildren<QToolButton *>()) {
        if (button->accessibleName() == QStringLiteral("Prose tools and settings")) {
            toolsButton = button;
            break;
        }
    }
    QVERIFY(toolsButton);
    QVERIFY(toolsButton->menu());
    QVERIFY(toolsButton->menu()->actions().size() >= 8);

    for (QPushButton *button : widget.findChildren<QPushButton *>()) {
        if (button->text() == QStringLiteral("Model settings") || button->text() == QStringLiteral("Grammar settings")
            || button->text() == QStringLiteral("Import") || button->text() == QStringLiteral("Export")) {
            QVERIFY(button->isHidden());
        }
    }

    QListView *findingView = findFindingView(widget);
    QVERIFY(findingView);
    QCOMPARE(findingView->accessibleName(), QStringLiteral("Prose observations"));
    QVERIFY(findingView->uniformItemSizes());

    const QString capturePath = qEnvironmentVariable("THOTHPAD_CAPTURE_UI");
    if (!capturePath.isEmpty()) {
        ProseDiagnostic diagnostic;
        diagnostic.id = QStringLiteral("capture-1");
        diagnostic.ruleId = QStringLiteral("filter.perception.saw");
        diagnostic.category = QStringLiteral("filter_words");
        diagnostic.level = QStringLiteral("context_flag");
        diagnostic.excerpt = QStringLiteral("She saw the door open.");
        diagnostic.explanation = QStringLiteral("A perception filter may place distance between the reader and the action.");
        diagnostic.suggestion = QStringLiteral("Consider stating the observed action directly when the act of seeing is not important.");
        diagnostic.source = QStringLiteral("deterministic");
        diagnostic.confidence = 0.91;
        widget.setProfiles({QStringLiteral("creative-default")}, QStringLiteral("creative-default"));
        widget.setDiagnostics({diagnostic});
        widget.selectDiagnostic(diagnostic);
        widget.setEngineReady(true);
        widget.resize(320, 900);
        widget.show();
        QTest::qWait(50);
        QVERIFY(widget.grab().save(capturePath));
    }
}

void ProseAwarenessWidgetTest::narrowLayoutKeepsControlsInsideViewport()
{
    ProseAwarenessWidget widget;
    widget.setProfiles({QStringLiteral("creative-default")}, QStringLiteral("creative-default"));
    for (int width : {320, 340}) {
        widget.resize(width, 900);
        widget.show();
        QTest::qWait(25);

        QCOMPARE(widget.width(), width);
        QList<QWidget *> controls;
        for (QAbstractButton *button : widget.findChildren<QAbstractButton *>()) {
            if (button->isVisible()) {
                controls.append(button);
            }
        }
        for (QComboBox *combo : widget.findChildren<QComboBox *>()) {
            if (combo->isVisible()) {
                controls.append(combo);
            }
        }
        for (QTreeWidget *tree : widget.findChildren<QTreeWidget *>()) {
            if (tree->isVisible()) {
                controls.append(tree);
            }
        }
        for (QListView *view : widget.findChildren<QListView *>()) {
            if (view->isVisible()) {
                controls.append(view);
            }
        }

        QTreeWidget *lensTree = nullptr;
        for (QTreeWidget *tree : widget.findChildren<QTreeWidget *>()) {
            if (tree->accessibleName() == QStringLiteral("Prose lenses")) {
                lensTree = tree;
                break;
            }
        }
        QVERIFY(lensTree);
        QVERIFY(lensTree->columnWidth(0) > lensTree->viewport()->width() * 3 / 4);

        for (QWidget *control : controls) {
            const QRect geometry(control->mapTo(&widget, QPoint(0, 0)), control->size());
            QVERIFY2(widget.rect().contains(geometry),
                     qPrintable(QStringLiteral("%1 extends outside the %2 px sidebar: %3,%4 %5x%6")
                                    .arg(control->metaObject()->className())
                                    .arg(width)
                                    .arg(geometry.x())
                                    .arg(geometry.y())
                                    .arg(geometry.width())
                                    .arg(geometry.height())));
        }
    }
}

void ProseAwarenessWidgetTest::selectedLensShowsOnlyOrderedOccurrences()
{
    ProseAwarenessWidget widget;
    ProseDiagnostic laterFilter;
    laterFilter.id = QStringLiteral("filter-later");
    laterFilter.ruleId = QStringLiteral("filter.perception.watched");
    laterFilter.category = QStringLiteral("filter_words");
    laterFilter.excerpt = QStringLiteral("watched");
    laterFilter.start = 80;
    laterFilter.end = 87;
    ProseDiagnostic adverb;
    adverb.id = QStringLiteral("adverb");
    adverb.ruleId = QStringLiteral("adverb.possibly");
    adverb.category = QStringLiteral("possible_adverbs");
    adverb.excerpt = QStringLiteral("possibly");
    adverb.start = 20;
    adverb.end = 28;
    ProseDiagnostic earlierFilter = laterFilter;
    earlierFilter.id = QStringLiteral("filter-earlier");
    earlierFilter.excerpt = QStringLiteral("saw");
    earlierFilter.start = 10;
    earlierFilter.end = 13;
    widget.setDiagnostics({laterFilter, adverb, earlierFilter});

    QTreeWidget *lensTree = nullptr;
    QListView *findingView = findFindingView(widget);
    for (QTreeWidget *tree : widget.findChildren<QTreeWidget *>()) {
        if (tree->accessibleName() == QStringLiteral("Prose lenses")) {
            lensTree = tree;
        }
    }
    QVERIFY(lensTree);
    QVERIFY(findingView);

    QTreeWidgetItem *filterLens = nullptr;
    QTreeWidgetItem *adverbLens = nullptr;
    for (int index = 0; index < lensTree->topLevelItemCount(); ++index) {
        QTreeWidgetItem *item = lensTree->topLevelItem(index);
        if (!item->data(0, Qt::UserRole + 1).isValid()) {
            continue;
        }
        QVERIFY(item->text(0).size() <= 15);
        QVERIFY(item->toolTip(0).contains(QLatin1Char('\n')));
        if (item->text(0) == QStringLiteral("Filter/filler")) {
            filterLens = item;
        } else if (item->text(0) == QStringLiteral("Adverbs")) {
            adverbLens = item;
        }
    }
    QVERIFY(filterLens);
    QVERIFY(adverbLens);

    lensTree->setCurrentItem(filterLens);
    QCOMPARE(findingView->model()->rowCount(), 2);
    QCOMPARE(findingView->model()->index(0, 0).data().toString(), QStringLiteral("saw"));
    QCOMPARE(findingView->model()->index(1, 0).data().toString(), QStringLiteral("watched"));

    lensTree->setCurrentItem(adverbLens);
    QCOMPARE(findingView->model()->rowCount(), 1);
    QCOMPARE(findingView->model()->index(0, 0).data().toString(), QStringLiteral("possibly"));
}

void ProseAwarenessWidgetTest::largeLensLoadsSuggestionsInPages()
{
    ProseAwarenessWidget widget;
    QList<ProseDiagnostic> diagnostics;
    for (int index = 0; index < 600; ++index) {
        ProseDiagnostic diagnostic;
        diagnostic.id = QString::number(index);
        diagnostic.ruleId = QStringLiteral("possible_adverbs.adverb");
        diagnostic.category = QStringLiteral("possible_adverbs");
        diagnostic.excerpt = QStringLiteral("quietly-%1").arg(index);
        diagnostic.start = index * 10;
        diagnostic.end = diagnostic.start + 7;
        diagnostics.append(diagnostic);
    }
    widget.setDiagnostics(diagnostics);

    QTreeWidget *lensTree = nullptr;
    QListView *findingView = findFindingView(widget);
    for (QTreeWidget *tree : widget.findChildren<QTreeWidget *>()) {
        if (tree->accessibleName() == QStringLiteral("Prose lenses")) {
            lensTree = tree;
        }
    }
    QVERIFY(lensTree);
    QVERIFY(findingView);
    for (int index = 0; index < lensTree->topLevelItemCount(); ++index) {
        if (lensTree->topLevelItem(index)->text(0) == QStringLiteral("Adverbs")) {
            lensTree->setCurrentItem(lensTree->topLevelItem(index));
            break;
        }
    }

    QCOMPARE(findingView->model()->rowCount(), 251);
    const QModelIndex loadMore = findingView->model()->index(250, 0);
    QVERIFY(loadMore.data().toString().contains(QStringLiteral("350 remaining")));
    QMetaObject::invokeMethod(findingView, "activated", Qt::DirectConnection, Q_ARG(QModelIndex, loadMore));
    QCOMPARE(findingView->model()->rowCount(), 501);
}

void ProseAwarenessWidgetTest::snapshotCountsAreIndependentOfLoadedRows()
{
    ProseAwarenessWidget widget;
    ProseDiagnostic diagnostic;
    diagnostic.id = QStringLiteral("one-loaded-row");
    diagnostic.ruleId = QStringLiteral("possible_adverbs.adverb");
    diagnostic.category = QStringLiteral("possible_adverbs");
    diagnostic.excerpt = QStringLiteral("quietly");
    widget.setDiagnostics({diagnostic}, true, 1203);
    widget.setCategoryCounts({{QStringLiteral("possible_adverbs"), 1203}});

    QTreeWidget *lensTree = nullptr;
    for (QTreeWidget *tree : widget.findChildren<QTreeWidget *>()) {
        if (tree->accessibleName() == QStringLiteral("Prose lenses")) {
            lensTree = tree;
            break;
        }
    }
    QVERIFY(lensTree);
    for (int index = 0; index < lensTree->topLevelItemCount(); ++index) {
        QTreeWidgetItem *item = lensTree->topLevelItem(index);
        if (item->text(0) == QStringLiteral("Adverbs")) {
            QCOMPARE(item->text(1), QStringLiteral("1203"));
            return;
        }
    }
    QFAIL("Adverb lens was not found");
}

void ProseAwarenessWidgetTest::disabledLensImmediatelyDisplaysZeroCount()
{
    ProseAwarenessWidget widget;
    widget.setCategoryCounts({{QStringLiteral("possible_adverbs"), 163}});

    QTreeWidgetItem *adverbs = nullptr;
    for (QTreeWidget *tree : widget.findChildren<QTreeWidget *>()) {
        if (tree->accessibleName() != QStringLiteral("Prose lenses")) {
            continue;
        }
        for (int index = 0; index < tree->topLevelItemCount(); ++index) {
            QTreeWidgetItem *item = tree->topLevelItem(index);
            if (item->data(0, Qt::UserRole + 1).toString() == QStringLiteral("possible_adverbs")) {
                adverbs = item;
                break;
            }
        }
    }
    QVERIFY(adverbs);
    QCOMPARE(adverbs->text(1), QStringLiteral("163"));

    adverbs->setCheckState(0, Qt::Unchecked);
    QCOMPARE(adverbs->text(1), QStringLiteral("0"));

    // A response already in flight must not resurrect a disabled lens count.
    widget.setCategoryCounts({{QStringLiteral("possible_adverbs"), 164}});
    QCOMPARE(adverbs->text(1), QStringLiteral("0"));

    // Re-enabling exposes the latest calculated total again.
    adverbs->setCheckState(0, Qt::Checked);
    QCOMPARE(adverbs->text(1), QStringLiteral("164"));
}

void ProseAwarenessWidgetTest::snapshotProtocolPagesContinueUntilComplete()
{
    SnapshotFindingPageState pages;
    pages.begin(QStringLiteral("analysis-1"), QStringLiteral("possible_adverbs"), 42);

    auto responsePage = [](const QString &analysisId, int count, bool hasMore, const QString &nextCursor) {
        QJsonArray diagnostics;
        for (int index = 0; index < count; ++index) {
            diagnostics.append(QJsonObject{{QStringLiteral("id"), index}});
        }
        return QJsonObject{
            {QStringLiteral("analysis_id"), analysisId},
            {QStringLiteral("diagnostics"), diagnostics},
            {QStringLiteral("has_more"), hasMore},
            {QStringLiteral("next_cursor"), nextCursor},
        };
    };

    const QJsonObject first = responsePage(QStringLiteral("analysis-1"), 250, true, QStringLiteral("cursor-250"));
    QVERIFY(pages.accepts(QStringLiteral("possible_adverbs"), 42, QString(), first));
    QVERIFY(pages.advance(first));
    QCOMPARE(pages.loadedCount, 250);
    QCOMPARE(pages.expectedCursor, QStringLiteral("cursor-250"));

    const QJsonObject second = responsePage(QStringLiteral("analysis-1"), 250, true, QStringLiteral("cursor-500"));
    QVERIFY(!pages.accepts(QStringLiteral("possible_adverbs"), 42, QString(), second));
    QVERIFY(!pages.accepts(QStringLiteral("possible_verbs"), 42, QStringLiteral("cursor-250"), second));
    QVERIFY(!pages.accepts(QStringLiteral("possible_adverbs"), 41, QStringLiteral("cursor-250"), second));
    QVERIFY(pages.accepts(QStringLiteral("possible_adverbs"), 42, QStringLiteral("cursor-250"), second));
    QVERIFY(pages.advance(second));
    QCOMPARE(pages.loadedCount, 500);

    const QJsonObject staleAnalysis = responsePage(QStringLiteral("analysis-old"), 100, false, QString());
    QVERIFY(!pages.accepts(QStringLiteral("possible_adverbs"), 42, QStringLiteral("cursor-500"), staleAnalysis));

    const QJsonObject finalPage = responsePage(QStringLiteral("analysis-1"), 100, false, QString());
    QVERIFY(pages.accepts(QStringLiteral("possible_adverbs"), 42, QStringLiteral("cursor-500"), finalPage));
    QVERIFY(!pages.advance(finalPage));
    QCOMPARE(pages.loadedCount, 600);
    QVERIFY(!pages.active);
    QVERIFY(pages.expectedCursor.isEmpty());
}

void ProseAwarenessWidgetTest::reportOverlaySuppressionsKeepUnrelatedSpans()
{
    ReportOverlaySuppressionState suppressions;
    suppressions.exactSpans.insert(ReportOverlaySuppressionState::spanKey(QStringLiteral("filter_words"), QStringLiteral("filter.perception.saw"), 10, 13));
    suppressions.disabledRules.insert(QStringLiteral("cliche.stock_phrase"));

    QVERIFY(suppressions.suppresses(QStringLiteral("filter_words"), QStringLiteral("filter.perception.saw"), 10, 13));
    QVERIFY(!suppressions.suppresses(QStringLiteral("filter_words"), QStringLiteral("filter.perception.saw"), 30, 33));
    QVERIFY(!suppressions.suppresses(QStringLiteral("possible_adverbs"), QStringLiteral("possible_adverbs.adverb"), 10, 13));
    QVERIFY(suppressions.suppresses(QStringLiteral("cliches"), QStringLiteral("cliche.stock_phrase"), 50, 65));
}

void ProseAwarenessWidgetTest::selectingDiagnosticSynchronizesFindingDetails()
{
    ProseAwarenessWidget widget;
    ProseDiagnostic diagnostic;
    diagnostic.id = QStringLiteral("finding-1");
    diagnostic.ruleId = QStringLiteral("filter.perception.saw");
    diagnostic.category = QStringLiteral("filter_words");
    diagnostic.level = QStringLiteral("context_flag");
    diagnostic.excerpt = QStringLiteral("saw");
    diagnostic.explanation = QStringLiteral("A perception filter.");
    diagnostic.suggestion = QStringLiteral("State the observed action directly.");
    diagnostic.source = QStringLiteral("deterministic");
    diagnostic.start = 5;
    diagnostic.end = 8;

    widget.setDiagnostics({diagnostic});
    widget.selectDiagnostic(diagnostic);

    QListView *findingView = findFindingView(widget);
    QVERIFY(findingView);
    QVERIFY(findingView->currentIndex().isValid());
    const QList<QLabel *> labels = widget.findChildren<QLabel *>();
    QVERIFY(std::any_of(labels.cbegin(), labels.cend(), [](const QLabel *label) {
        return label->text().contains(QStringLiteral("Source: deterministic"));
    }));
    QVERIFY(std::any_of(labels.cbegin(), labels.cend(), [](const QLabel *label) {
        return label->text().contains(QStringLiteral("Source: deterministic")) && label->focusPolicy() == Qt::StrongFocus;
    }));
}

void ProseAwarenessWidgetTest::observationActionsExcludeApply()
{
    ProseAwarenessWidget widget;
    ProseDiagnostic diagnostic;
    diagnostic.id = QStringLiteral("grammar-1");
    diagnostic.ruleId = QStringLiteral("grammar.harper.article");
    diagnostic.category = QStringLiteral("grammar_mechanics");
    diagnostic.excerpt = QStringLiteral("an");
    diagnostic.replacements = {QStringLiteral("a")};
    widget.setDiagnostics({diagnostic});
    widget.selectDiagnostic(diagnostic);

    for (QPushButton *button : widget.findChildren<QPushButton *>()) {
        QVERIFY(button->text() != QStringLiteral("Apply"));
    }
    QVERIFY(!widget.findChild<QWidget *>(QStringLiteral("applySuggestionButton")));
}

void ProseAwarenessWidgetTest::nextObservationAdvancesWithinSelectedLens()
{
    ProseAwarenessWidget widget;
    widget.setEngineReady(true);
    QList<ProseDiagnostic> diagnostics;
    for (int index = 0; index < 251; ++index) {
        ProseDiagnostic diagnostic;
        diagnostic.id = QString::number(index);
        diagnostic.ruleId = QStringLiteral("possible_verbs.verb");
        diagnostic.category = QStringLiteral("possible_verbs");
        diagnostic.excerpt = QStringLiteral("verb-%1").arg(index);
        diagnostic.start = index * 10;
        diagnostic.end = diagnostic.start + 4;
        diagnostics.append(diagnostic);
    }
    widget.setDiagnostics(diagnostics);
    widget.selectDiagnostic(diagnostics.first());

    QListView *findingView = findFindingView(widget);
    QToolButton *backButton = widget.findChild<QToolButton *>(QStringLiteral("backObservationButton"));
    QToolButton *nextButton = widget.findChild<QToolButton *>(QStringLiteral("nextObservationButton"));
    QVERIFY(findingView);
    QVERIFY(backButton);
    QVERIFY(nextButton);
    QCOMPARE(backButton->text(), QStringLiteral("←"));
    QCOMPARE(backButton->accessibleName(), QStringLiteral("Previous observation"));
    QCOMPARE(nextButton->text(), QStringLiteral("→"));
    QCOMPARE(nextButton->accessibleName(), QStringLiteral("Next observation"));

    QSignalSpy previewSpy(&widget, &ProseAwarenessWidget::findingPreviewRequested);
    QSignalSpy activatedSpy(&widget, &ProseAwarenessWidget::findingActivated);
    QTest::mouseClick(nextButton, Qt::LeftButton);
    QCOMPARE(findingView->currentIndex().row(), 1);
    QCOMPARE(findingView->currentIndex().data().toString(), QStringLiteral("verb-1"));
    QCOMPARE(previewSpy.count(), 1);
    QCOMPARE(activatedSpy.count(), 0);
    QCOMPARE(qvariant_cast<ProseDiagnostic>(previewSpy.takeFirst().at(0)).id, QStringLiteral("1"));

    QTest::mouseClick(backButton, Qt::LeftButton);
    QCOMPARE(findingView->currentIndex().row(), 0);
    QCOMPARE(findingView->currentIndex().data().toString(), QStringLiteral("verb-0"));
    QCOMPARE(previewSpy.count(), 1);
    QCOMPARE(qvariant_cast<ProseDiagnostic>(previewSpy.takeFirst().at(0)).id, QStringLiteral("0"));

    widget.selectDiagnostic(diagnostics.at(249));
    QTest::mouseClick(nextButton, Qt::LeftButton);
    QCOMPARE(findingView->currentIndex().row(), 250);
    QCOMPARE(findingView->currentIndex().data().toString(), QStringLiteral("verb-250"));

    QTest::mouseClick(nextButton, Qt::LeftButton);
    QCOMPARE(findingView->currentIndex().row(), 0);
    QCOMPARE(findingView->currentIndex().data().toString(), QStringLiteral("verb-0"));

    QTest::mouseClick(backButton, Qt::LeftButton);
    QCOMPARE(findingView->currentIndex().row(), 250);
    QCOMPARE(findingView->currentIndex().data().toString(), QStringLiteral("verb-250"));
}

void ProseAwarenessWidgetTest::nextObservationContinuesAfterLoadingAnotherPage()
{
    ProseAwarenessWidget widget;
    widget.setEngineReady(true);
    for (QTreeWidget *tree : widget.findChildren<QTreeWidget *>()) {
        if (tree->accessibleName() != QStringLiteral("Prose lenses")) {
            continue;
        }
        for (int index = 0; index < tree->topLevelItemCount(); ++index) {
            if (tree->topLevelItem(index)->text(0) == QStringLiteral("Verbs")) {
                tree->setCurrentItem(tree->topLevelItem(index));
                break;
            }
        }
    }
    QList<ProseDiagnostic> diagnostics;
    for (int index = 0; index < 250; ++index) {
        ProseDiagnostic diagnostic;
        diagnostic.id = QString::number(index);
        diagnostic.ruleId = QStringLiteral("possible_verbs.verb");
        diagnostic.category = QStringLiteral("possible_verbs");
        diagnostic.excerpt = QStringLiteral("verb-%1").arg(index);
        diagnostic.start = index * 10;
        diagnostic.end = diagnostic.start + 4;
        diagnostics.append(diagnostic);
    }
    widget.setDiagnostics(diagnostics, true, 251);
    widget.selectDiagnostic(diagnostics.last());

    QToolButton *nextButton = widget.findChild<QToolButton *>(QStringLiteral("nextObservationButton"));
    QVERIFY(nextButton);
    QSignalSpy moreSpy(&widget, &ProseAwarenessWidget::moreFindingsRequested);
    QSignalSpy previewSpy(&widget, &ProseAwarenessWidget::findingPreviewRequested);
    QSignalSpy activatedSpy(&widget, &ProseAwarenessWidget::findingActivated);
    QTest::mouseClick(nextButton, Qt::LeftButton);
    QCOMPARE(moreSpy.count(), 1);
    QCOMPARE(previewSpy.count(), 0);
    QCOMPARE(activatedSpy.count(), 0);

    ProseDiagnostic finalDiagnostic;
    finalDiagnostic.id = QStringLiteral("250");
    finalDiagnostic.ruleId = QStringLiteral("possible_verbs.verb");
    finalDiagnostic.category = QStringLiteral("possible_verbs");
    finalDiagnostic.excerpt = QStringLiteral("verb-250");
    finalDiagnostic.start = 2500;
    finalDiagnostic.end = 2504;
    diagnostics.append(finalDiagnostic);
    widget.setDiagnostics(diagnostics, false, 251, true);

    QListView *findingView = findFindingView(widget);
    QVERIFY(findingView);
    QCOMPARE(findingView->currentIndex().row(), 250);
    QCOMPARE(findingView->currentIndex().data().toString(), QStringLiteral("verb-250"));
    QCOMPARE(previewSpy.count(), 1);
    QCOMPARE(activatedSpy.count(), 0);
    QCOMPARE(qvariant_cast<ProseDiagnostic>(previewSpy.takeFirst().at(0)).id, QStringLiteral("250"));
}

void ProseAwarenessWidgetTest::destructiveActionsStayTogetherAndEmitScope()
{
    ProseAwarenessWidget widget;
    ProseDiagnostic diagnostic;
    diagnostic.id = QStringLiteral("filter-1");
    diagnostic.ruleId = QStringLiteral("filter.hedge.just");
    diagnostic.category = QStringLiteral("filter_words");
    diagnostic.excerpt = QStringLiteral("just");
    diagnostic.start = 5;
    diagnostic.end = 9;
    widget.setDiagnostics({diagnostic});
    widget.selectDiagnostic(diagnostic);
    widget.setUndoAvailable(true);
    widget.resize(320, 900);
    widget.show();

    QToolButton *deleteButton = nullptr;
    QToolButton *backButton = nullptr;
    QToolButton *nextButton = nullptr;
    QToolButton *undoButton = nullptr;
    QToolButton *moreButton = nullptr;
    for (QToolButton *button : widget.findChildren<QToolButton *>()) {
        if (button->accessibleName() == QStringLiteral("Delete selected occurrence")) {
            deleteButton = button;
        } else if (button->accessibleName() == QStringLiteral("Previous observation")) {
            backButton = button;
        } else if (button->accessibleName() == QStringLiteral("Next observation")) {
            nextButton = button;
        } else if (button->accessibleName() == QStringLiteral("Undo manuscript edit")) {
            undoButton = button;
        } else if (button->accessibleName() == QStringLiteral("More finding actions")) {
            moreButton = button;
        }
    }
    QVERIFY(deleteButton);
    QVERIFY(backButton);
    QVERIFY(nextButton);
    QVERIFY(undoButton);
    QVERIFY(moreButton);
    QCOMPARE(deleteButton->geometry().center().y(), undoButton->geometry().center().y());
    QCOMPARE(deleteButton->geometry().center().y(), backButton->geometry().center().y());
    QCOMPARE(deleteButton->geometry().center().y(), nextButton->geometry().center().y());
    QCOMPARE(deleteButton->geometry().center().y(), moreButton->geometry().center().y());

    QSignalSpy deleteSpy(&widget, &ProseAwarenessWidget::deleteRequested);
    QSignalSpy undoSpy(&widget, &ProseAwarenessWidget::undoRequested);
    QSignalSpy deleteAllSpy(&widget, &ProseAwarenessWidget::deleteAllRequested);
    auto *scrollArea = widget.findChild<QScrollArea *>(QStringLiteral("proseAwarenessScrollArea"));
    QVERIFY(scrollArea);
    scrollArea->ensureWidgetVisible(deleteButton);
    QTest::qWait(25);
    QTest::mouseClick(deleteButton, Qt::LeftButton);
    QTest::mouseClick(undoButton, Qt::LeftButton);
    QCOMPARE(deleteSpy.count(), 1);
    QCOMPARE(undoSpy.count(), 1);

    QAction *deleteAllAction = nullptr;
    for (QAction *action : moreButton->menu()->actions()) {
        if (action->text() == QStringLiteral("Delete all in this lens...")) {
            deleteAllAction = action;
            break;
        }
    }
    QVERIFY(deleteAllAction);
    deleteAllAction->trigger();
    QCOMPARE(deleteAllSpy.count(), 1);
    QCOMPARE(deleteAllSpy.at(0).at(0).toString(), QStringLiteral("filter_words"));
    QCOMPARE(deleteAllSpy.at(0).at(1).toString(), QStringLiteral("Filter/filler"));
}

void ProseAwarenessWidgetTest::findingsUseVirtualizedModelAtScale()
{
    constexpr int FindingCount = 200000;
    ProseAwarenessWidget widget;
    QTreeWidget *lensTree = nullptr;
    for (QTreeWidget *tree : widget.findChildren<QTreeWidget *>()) {
        if (tree->accessibleName() == QStringLiteral("Prose lenses")) {
            lensTree = tree;
            break;
        }
    }
    QVERIFY(lensTree);
    for (int index = 0; index < lensTree->topLevelItemCount(); ++index) {
        if (lensTree->topLevelItem(index)->text(0) == QStringLiteral("Adverbs")) {
            lensTree->setCurrentItem(lensTree->topLevelItem(index));
            break;
        }
    }

    QList<ProseDiagnostic> diagnostics;
    diagnostics.reserve(FindingCount);
    for (int index = 0; index < FindingCount; ++index) {
        ProseDiagnostic diagnostic;
        diagnostic.id = QString::number(index);
        diagnostic.ruleId = QStringLiteral("possible_adverbs.adverb");
        diagnostic.category = QStringLiteral("possible_adverbs");
        diagnostic.excerpt = QStringLiteral("quietly");
        diagnostic.start = index * 2;
        diagnostic.end = diagnostic.start + 1;
        diagnostics.append(diagnostic);
    }

    QElapsedTimer timer;
    timer.start();
    widget.setDiagnostics(diagnostics, false, FindingCount);
    const qint64 elapsed = timer.elapsed();

    QListView *findingView = findFindingView(widget);
    QVERIFY(findingView);
    QCOMPARE(findingView->model()->rowCount(), 251);
    QCOMPARE(widget.findChildren<QTreeWidget *>().size(), 1);
    QVERIFY2(findingView->findChildren<QWidget *>(QString(), Qt::FindDirectChildrenOnly).size() < 12, "The virtualized list created per-finding widgets");
    qInfo().noquote() << QStringLiteral("virtual_findings rows=200000 visible_model_rows=%1 reset_ms=%2").arg(findingView->model()->rowCount()).arg(elapsed);
    QVERIFY2(elapsed < 1000, qPrintable(QStringLiteral("Virtual model reset took %1 ms").arg(elapsed)));
}

void ProseAwarenessWidgetTest::performanceSettingsActionEmitsSignal()
{
    ProseAwarenessWidget widget;
    QSignalSpy spy(&widget, &ProseAwarenessWidget::performanceSettingsRequested);
    QAction *performanceAction = nullptr;
    for (QAction *action : widget.findChildren<QAction *>()) {
        if (action->text() == QStringLiteral("Performance settings...")) {
            performanceAction = action;
            break;
        }
    }
    QVERIFY(performanceAction);
    performanceAction->trigger();

    QCOMPARE(spy.count(), 1);
}
namespace
{
QTreeWidget *findLensTree(const ProseAwarenessWidget &widget)
{
    for (QTreeWidget *tree : widget.findChildren<QTreeWidget *>()) {
        if (tree->accessibleName() == QStringLiteral("Prose lenses")) {
            return tree;
        }
    }
    return nullptr;
}

QTreeWidgetItem *findLensItem(const ProseAwarenessWidget &widget, const QString &categoryId)
{
    QTreeWidget *lensTree = findLensTree(widget);
    if (!lensTree) {
        return nullptr;
    }
    std::function<QTreeWidgetItem *(QTreeWidgetItem *)> findChild = [&](QTreeWidgetItem *parent) -> QTreeWidgetItem * {
        for (int index = 0; index < parent->childCount(); ++index) {
            QTreeWidgetItem *item = parent->child(index);
            if (item->data(0, Qt::UserRole + 1).toString() == categoryId) {
                return item;
            }
            if (QTreeWidgetItem *nested = findChild(item)) {
                return nested;
            }
        }
        return nullptr;
    };
    for (int index = 0; index < lensTree->topLevelItemCount(); ++index) {
        QTreeWidgetItem *item = lensTree->topLevelItem(index);
        if (item->data(0, Qt::UserRole + 1).toString() == categoryId) {
            return item;
        }
        if (QTreeWidgetItem *nested = findChild(item)) {
            return nested;
        }
    }
    return nullptr;
}
}

void ProseAwarenessWidgetTest::firstReportedLensOpensItsLoadedDetails()
{
    ProseAwarenessWidget widget;
    widget.setEngineReady(true);
    widget.setCategoryCounts({{QStringLiteral("possible_adverbs"), 160}, {QStringLiteral("filter_words"), 33}});

    QCOMPARE(widget.selectedCategory(), QStringLiteral("possible_adverbs"));
    QTreeWidgetItem *adverbs = findLensItem(widget, QStringLiteral("possible_adverbs"));
    QVERIFY(adverbs);
    QCOMPARE(adverbs->text(1), QStringLiteral("160"));

    QLabel *hint = widget.findChild<QLabel *>(QStringLiteral("proseAwarenessFindingsHint"));
    QVERIFY(hint);
    QCOMPARE(hint->text(), QStringLiteral("Adverbs — 160 observations. Loading details…"));
}

void ProseAwarenessWidgetTest::populatedLensSelectsFirstFinding()
{
    ProseAwarenessWidget widget;
    widget.setEngineReady(true);
    ProseDiagnostic diagnostic;
    diagnostic.id = QStringLiteral("first-finding");
    diagnostic.ruleId = QStringLiteral("possible_adverbs.adverb");
    diagnostic.category = QStringLiteral("possible_adverbs");
    diagnostic.excerpt = QStringLiteral("there");
    diagnostic.explanation = QStringLiteral("Review this adverb.");
    widget.setDiagnostics({diagnostic});
    widget.setCategoryCounts({{QStringLiteral("possible_adverbs"), 1}});

    QListView *findings = findFindingView(widget);
    QVERIFY(findings);
    QCOMPARE(findings->currentIndex().data().toString(), QStringLiteral("there"));
    QLabel *hint = widget.findChild<QLabel *>(QStringLiteral("proseAwarenessFindingsHint"));
    QVERIFY(hint);
    QCOMPARE(hint->text(), QStringLiteral("Adverbs — 1 observation. Select one to review it."));
}

void ProseAwarenessWidgetTest::emptyLensUsesOneTruthfulStatus()
{
    ProseAwarenessWidget widget;
    widget.setEngineReady(true);

    QListView *findings = findFindingView(widget);
    QVERIFY(findings);
    QCOMPARE(findings->model()->rowCount(), 0);
    QLabel *hint = widget.findChild<QLabel *>(QStringLiteral("proseAwarenessFindingsHint"));
    QVERIFY(hint);
    QCOMPARE(hint->text(), QStringLiteral("No observations in General rules."));
    QFrame *actions = widget.findChild<QFrame *>(QStringLiteral("proseAwarenessFindingActions"));
    QVERIFY(actions);
    QVERIFY(actions->isHidden());
}

void ProseAwarenessWidgetTest::repetitionLensRegisteredWithDefaults()
{
    ProseAwarenessWidget widget;
    QTreeWidgetItem *item = findLensItem(widget, QStringLiteral("repetition"));
    QVERIFY(item);
    QCOMPARE(widget.categoryLabel(QStringLiteral("repetition")), QStringLiteral("Repetition"));
    QCOMPARE(widget.categoryFindingLabel(QStringLiteral("repetition")), QStringLiteral("Repetition"));
    QCOMPARE(widget.categoryLabel(QStringLiteral("repetition_rhythm")), QStringLiteral("Echoes"));
    QCOMPARE(widget.categoryFindingLabel(QStringLiteral("repetition_rhythm")), QStringLiteral("Echo"));
    QCOMPARE(widget.categoryDescription(QStringLiteral("repetition_rhythm")), QStringLiteral("Manuscript-wide word and phrase echoes."));
    QCOMPARE(item->checkState(0), Qt::Checked);
    QVERIFY(item->toolTip(0).contains(QStringLiteral("Words repeated close together.")));
    QVERIFY(item->toolTip(0).contains(QStringLiteral("Runs live")));
    const QColor swatch = item->icon(1).pixmap(14, 14).toImage().pixelColor(7, 7);
    QCOMPARE(swatch.name(), QStringLiteral("#a76d87"));
}

void ProseAwarenessWidgetTest::repetitionLensToggleRoundTrip()
{
    ProseAwarenessWidget widget;
    QSignalSpy enabledSpy(&widget, &ProseAwarenessWidget::categoryEnabledChanged);

    widget.setCategoryState(QStringLiteral("repetition"), false, QColor("#F472B6"));
    QCOMPARE(enabledSpy.count(), 0);
    QCOMPARE(findLensItem(widget, QStringLiteral("repetition"))->checkState(0), Qt::Unchecked);

    widget.setCategoryState(QStringLiteral("repetition"), true, QColor("#F472B6"));
    QCOMPARE(enabledSpy.count(), 0);
    QCOMPARE(findLensItem(widget, QStringLiteral("repetition"))->checkState(0), Qt::Checked);

    QTreeWidgetItem *item = findLensItem(widget, QStringLiteral("repetition"));
    item->setCheckState(0, Qt::Unchecked);
    QCOMPARE(enabledSpy.count(), 1);
    QCOMPARE(enabledSpy.at(0).at(0).toString(), QStringLiteral("repetition"));
    QCOMPARE(enabledSpy.at(0).at(1).toBool(), false);
}

void ProseAwarenessWidgetTest::syntheticRepetitionFindingFlowsToSuggestionsModel()
{
    ProseAwarenessWidget widget;
    ProseDiagnostic repetition;
    repetition.id = QStringLiteral("repetition-1");
    repetition.ruleId = QStringLiteral("repetition.proximal_word");
    repetition.category = QStringLiteral("repetition");
    repetition.level = QStringLiteral("strong_flag");
    repetition.excerpt = QStringLiteral("dark");
    repetition.explanation = QStringLiteral("The word repeats within a short window.");
    repetition.suggestion = QStringLiteral("Vary the wording.");
    repetition.source = QStringLiteral("deterministic");
    repetition.confidence = 0.9;
    repetition.start = 40;
    repetition.end = 44;
    ProseDiagnostic filler;
    filler.id = QStringLiteral("filter-1");
    filler.ruleId = QStringLiteral("filter.perception.saw");
    filler.category = QStringLiteral("filter_words");
    filler.excerpt = QStringLiteral("saw");
    filler.start = 5;
    filler.end = 8;
    widget.setDiagnostics({filler, repetition});
    widget.setCategoryCounts({{QStringLiteral("filter_words"), 1}, {QStringLiteral("repetition"), 1}});

    QTreeWidgetItem *item = findLensItem(widget, QStringLiteral("repetition"));
    QVERIFY(item);
    QCOMPARE(item->text(1), QStringLiteral("1"));
    QTreeWidget *lensTree = findLensTree(widget);
    QVERIFY(lensTree);
    lensTree->setCurrentItem(item);

    QListView *findingView = findFindingView(widget);
    QVERIFY(findingView);
    QCOMPARE(findingView->model()->rowCount(), 1);
    QCOMPARE(findingView->model()->index(0, 0).data().toString(), QStringLiteral("dark"));

    widget.selectDiagnostic(repetition);
    QVERIFY(findingView->currentIndex().isValid());
    // Overlay tooltips are composed from these labels in ProseController.
    QCOMPARE(widget.categoryFindingLabel(repetition.category), QStringLiteral("Repetition"));
    QVERIFY(!widget.categoryDescription(repetition.category).isEmpty());
}

void ProseAwarenessWidgetTest::scanButtonTriggersDocumentReviewRequest()
{
    ProseAwarenessWidget widget;
    // Locate by objectName: the label is state-dependent ("Scan" /
    // "Scanning..." / "Engine starting...") since the visibility fix.
    QPushButton *scanButton = widget.findChild<QPushButton *>(QStringLiteral("scanButton"));
    QVERIFY(scanButton);

    QSignalSpy spy(&widget, &ProseAwarenessWidget::reviewRequested);
    QTest::mouseClick(scanButton, Qt::LeftButton);
    QCOMPARE(spy.count(), 0);

    widget.setEngineReady(true);
    QVERIFY(scanButton->isEnabled());
    QVERIFY(scanButton->toolTip().contains(QStringLiteral("full-document scan")));
    QVERIFY(scanButton->toolTip().contains(QStringLiteral("Ctrl+Shift+S")));

    QTest::mouseClick(scanButton, Qt::LeftButton);
    QCOMPARE(spy.count(), 1);
    QCOMPARE(spy.at(0).at(0).toString(), QStringLiteral("document"));

    // The button stays busy until the review machinery reports a finished status.
    QVERIFY(!scanButton->isEnabled());
    QCOMPARE(scanButton->toolTip(), QStringLiteral("Scanning..."));
    widget.setEngineMessage(QStringLiteral("Loading 250 of 1203..."));
    QVERIFY(!scanButton->isEnabled());
    widget.setEngineMessage(QStringLiteral("Ready"));
    QVERIFY(scanButton->isEnabled());
}

void ProseAwarenessWidgetTest::scanButtonDisabledWhenEngineNotReady()
{
    ProseAwarenessWidget widget;
    QPushButton *scanButton = widget.findChild<QPushButton *>(QStringLiteral("scanButton"));
    QVERIFY(scanButton);
    QCOMPARE(scanButton->isEnabled(), false);

    widget.setEngineReady(true);
    QCOMPARE(scanButton->isEnabled(), true);
    widget.setEngineReady(false);
    QCOMPARE(scanButton->isEnabled(), false);
    QVERIFY(scanButton->toolTip().contains(QStringLiteral("not ready")));
}

void ProseAwarenessWidgetTest::scanButtonAnchorsReviewSetupAndShortcutWorks()
{
    ProseAwarenessWidget widget;
    widget.setEngineReady(true);
    widget.resize(320, 900);
    widget.show();
    QTest::qWait(25);

    QPushButton *scanButton = widget.findChild<QPushButton *>(QStringLiteral("scanButton"));
    QVERIFY(scanButton);
    QTreeWidget *lensTree = findLensTree(widget);
    QVERIFY(lensTree);
    QListView *findingView = findFindingView(widget);
    QVERIFY(findingView);

    const int treeTop = lensTree->mapTo(&widget, QPoint(0, 0)).y();
    const int scanTop = scanButton->mapTo(&widget, QPoint(0, 0)).y();
    const int findingsTop = findingView->mapTo(&widget, QPoint(0, 0)).y();
    QVERIFY2(scanTop < treeTop, "The document command must remain in the aligned review setup block");
    QVERIFY2(findingsTop > treeTop, "The observations list must follow the lens navigator");
    QVERIFY(scanButton->isVisible());
    if (qEnvironmentVariableIsSet("THOTHPAD_WIDGET_DUMP")) {
        // Visual-regression aid: dump the widget as rendered in this state.
        widget.grab().save(qEnvironmentVariable("THOTHPAD_WIDGET_DUMP"));
    }

    QSignalSpy spy(&widget, &ProseAwarenessWidget::reviewRequested);
    QTest::keyClick(&widget, Qt::Key_S, Qt::ControlModifier | Qt::ShiftModifier);
    QCOMPARE(spy.count(), 1);
    QCOMPARE(spy.at(0).at(0).toString(), QStringLiteral("document"));
    QVERIFY(!scanButton->isEnabled());
}

QTEST_MAIN(ProseAwarenessWidgetTest)
#include "proseawarenesswidgettest.moc"
