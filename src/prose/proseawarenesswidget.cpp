/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "proseawarenesswidget.h"

#include <algorithm>
#include <limits>
#include <utility>

#include <QAbstractListModel>
#include <QAction>
#include <QApplication>
#include <QColorDialog>
#include <QComboBox>
#include <QEvent>
#include <QFormLayout>
#include <QFrame>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QIcon>
#include <QItemSelectionModel>
#include <QLabel>
#include <QListView>
#include <QMenu>
#include <QPainter>
#include <QPalette>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QResizeEvent>
#include <QScrollArea>
#include <QSet>
#include <QShortcut>
#include <QSignalBlocker>
#include <QStyle>
#include <QStyledItemDelegate>
#include <QToolButton>
#include <QTreeWidget>
#include <QVBoxLayout>

namespace ghostwriter
{
namespace
{
constexpr int DiagnosticIndexRole = Qt::UserRole;
constexpr int CategoryIdRole = Qt::UserRole + 1;
constexpr int CategoryDescriptionRole = Qt::UserRole + 2;
constexpr int CategoryModeRole = Qt::UserRole + 3;
constexpr int LoadMoreIndex = -2;
constexpr int FindingPageSize = 250;

class LensCountDelegate final : public QStyledItemDelegate
{
public:
    using QStyledItemDelegate::QStyledItemDelegate;

    void paint(QPainter *painter, const QStyleOptionViewItem &option, const QModelIndex &index) const override
    {
        bool isCount = false;
        const QString text = index.data(Qt::DisplayRole).toString();
        text.toInt(&isCount);
        if (index.column() != 1 || !isCount) {
            QStyledItemDelegate::paint(painter, option, index);
            return;
        }

        QStyleOptionViewItem rowOption(option);
        initStyleOption(&rowOption, index);
        rowOption.text.clear();
        const QStyle *style = rowOption.widget ? rowOption.widget->style() : QApplication::style();
        style->drawControl(QStyle::CE_ItemViewItem, &rowOption, painter, rowOption.widget);

        const QFontMetrics metrics(option.font);
        const int badgeWidth = qMax(22, metrics.horizontalAdvance(text) + 10);
        const QRect badgeRect(option.rect.right() - badgeWidth - 5, option.rect.center().y() - 10, badgeWidth, 20);
        QColor background = option.palette.color(QPalette::Window);
        const QColor foreground = option.palette.color(QPalette::Text);
        const int blend = option.state.testFlag(QStyle::State_Selected) ? 16 : 9;
        background.setRgb((background.red() * (100 - blend) + foreground.red() * blend) / 100,
                          (background.green() * (100 - blend) + foreground.green() * blend) / 100,
                          (background.blue() * (100 - blend) + foreground.blue() * blend) / 100);

        painter->save();
        painter->setRenderHint(QPainter::Antialiasing);
        painter->setPen(Qt::NoPen);
        painter->setBrush(background);
        painter->drawRoundedRect(badgeRect, 4, 4);
        painter->setPen(foreground);
        painter->drawText(badgeRect, Qt::AlignCenter, text);
        painter->restore();
    }
};

class SidebarScrollArea final : public QScrollArea
{
public:
    using QScrollArea::QScrollArea;

protected:
    void resizeEvent(QResizeEvent *event) override
    {
        QScrollArea::resizeEvent(event);
        if (widget()) {
            widget()->setFixedWidth(viewport()->width());
        }
    }
};

QString levelLabel(const QString &level)
{
    if (level == QStringLiteral("hard_fail")) {
        return ProseAwarenessWidget::tr("General rule");
    }
    if (level == QStringLiteral("strong_flag")) {
        return ProseAwarenessWidget::tr("Strong signal");
    }
    if (level == QStringLiteral("context_flag")) {
        return ProseAwarenessWidget::tr("Context signal");
    }
    return ProseAwarenessWidget::tr("Style preference");
}

void appendCategoryItems(QTreeWidgetItem *parent, QList<QTreeWidgetItem *> &items)
{
    for (int index = 0; index < parent->childCount(); ++index) {
        QTreeWidgetItem *item = parent->child(index);
        if (item->data(0, CategoryIdRole).isValid()) {
            items.append(item);
        }
        appendCategoryItems(item, items);
    }
}

QList<QTreeWidgetItem *> categoryItems(QTreeWidget *tree)
{
    QList<QTreeWidgetItem *> items;
    for (int index = 0; index < tree->topLevelItemCount(); ++index) {
        QTreeWidgetItem *item = tree->topLevelItem(index);
        if (item->data(0, CategoryIdRole).isValid()) {
            items.append(item);
        }
        appendCategoryItems(item, items);
    }
    return items;
}
}

class ProseFindingListModel final : public QAbstractListModel
{
public:
    explicit ProseFindingListModel(QObject *parent = nullptr)
        : QAbstractListModel(parent)
    {
    }

    void setDiagnostics(const QList<ProseDiagnostic> &diagnostics, const QString &category, int visibleLimit, bool hasMore, int knownTotal)
    {
        beginResetModel();
        m_diagnostics = diagnostics;
        m_categoryIndexes.clear();
        for (int index = 0; index < m_diagnostics.size(); ++index) {
            m_categoryIndexes[m_diagnostics.at(index).category].append(index);
        }
        for (QVector<int> &indexes : m_categoryIndexes) {
            if (std::is_sorted(indexes.cbegin(), indexes.cend(), [this](int left, int right) {
                    const ProseDiagnostic &leftDiagnostic = m_diagnostics.at(left);
                    const ProseDiagnostic &rightDiagnostic = m_diagnostics.at(right);
                    return leftDiagnostic.start == rightDiagnostic.start ? leftDiagnostic.end < rightDiagnostic.end
                                                                         : leftDiagnostic.start < rightDiagnostic.start;
                })) {
                continue;
            }
            std::stable_sort(indexes.begin(), indexes.end(), [this](int left, int right) {
                const ProseDiagnostic &leftDiagnostic = m_diagnostics.at(left);
                const ProseDiagnostic &rightDiagnostic = m_diagnostics.at(right);
                return leftDiagnostic.start == rightDiagnostic.start ? leftDiagnostic.end < rightDiagnostic.end : leftDiagnostic.start < rightDiagnostic.start;
            });
        }
        applyCategory(category, visibleLimit, hasMore, knownTotal);
        endResetModel();
    }

    void setCategory(const QString &category, int visibleLimit, bool hasMore, int knownTotal)
    {
        beginResetModel();
        applyCategory(category, visibleLimit, hasMore, knownTotal);
        endResetModel();
    }

    int rowCount(const QModelIndex &parent = QModelIndex()) const override
    {
        if (parent.isValid()) {
            return 0;
        }
        return m_visibleCount + (m_hasLoadMore ? 1 : 0);
    }

    QVariant data(const QModelIndex &index, int role) const override
    {
        if (!index.isValid() || index.row() < 0 || index.row() >= rowCount()) {
            return {};
        }
        if (isLoadMore(index)) {
            if (role == Qt::DisplayRole) {
                const int remaining = std::max(0, m_knownTotal - m_visibleCount);
                return tr("Show %1 more (%2 remaining)").arg(std::min(FindingPageSize, remaining)).arg(remaining);
            }
            if (role == Qt::AccessibleTextRole) {
                const int remaining = std::max(0, m_knownTotal - m_visibleCount);
                return tr("Show %1 more suggestions, %2 remaining.").arg(std::min(FindingPageSize, remaining)).arg(remaining);
            }
            if (role == Qt::ToolTipRole) {
                return tr("Load the next page of suggestions in this lens.");
            }
            return role == DiagnosticIndexRole ? QVariant(LoadMoreIndex) : QVariant();
        }
        const int diagnosticIndex = indexes().at(index.row());
        const ProseDiagnostic &diagnostic = m_diagnostics.at(diagnosticIndex);
        if (role == Qt::DisplayRole) {
            return diagnostic.excerpt;
        }
        if (role == Qt::AccessibleTextRole) {
            return tr("%1: %2. %3").arg(levelLabel(diagnostic.level), diagnostic.excerpt, diagnostic.explanation);
        }
        if (role == Qt::AccessibleDescriptionRole) {
            return diagnostic.explanation;
        }
        if (role == Qt::ToolTipRole) {
            return QStringLiteral("%1\n%2").arg(levelLabel(diagnostic.level), diagnostic.explanation);
        }
        return role == DiagnosticIndexRole ? QVariant(diagnosticIndex) : QVariant();
    }

    Qt::ItemFlags flags(const QModelIndex &index) const override
    {
        if (!index.isValid()) {
            return Qt::NoItemFlags;
        }
        if (isLoadMore(index)) {
            return QAbstractListModel::flags(index);
        }
        return QAbstractListModel::flags(index);
    }

    bool isLoadMore(const QModelIndex &index) const
    {
        if (!m_hasLoadMore || !index.isValid()) {
            return false;
        }
        return index.row() == m_visibleCount;
    }

    int loadedCount() const
    {
        return indexes().size();
    }

    bool isEmptyState() const
    {
        return indexes().isEmpty() && !m_hasLoadMore;
    }

    int rowForDiagnosticIndex(int diagnosticIndex) const
    {
        const QVector<int> &current = indexes();
        const auto searchEnd = current.cbegin() + std::min(m_visibleCount, static_cast<int>(current.size()));
        const auto found = std::find(current.cbegin(), searchEnd, diagnosticIndex);
        return found == searchEnd ? -1 : static_cast<int>(std::distance(current.cbegin(), found));
    }

private:
    const QVector<int> &indexes() const
    {
        static const QVector<int> empty;
        return m_indexes ? *m_indexes : empty;
    }

    void applyCategory(const QString &category, int visibleLimit, bool hasMore, int knownTotal)
    {
        const auto found = m_categoryIndexes.constFind(category);
        m_indexes = found == m_categoryIndexes.cend() ? nullptr : &found.value();
        m_visibleCount = std::min(visibleLimit, static_cast<int>(indexes().size()));
        m_knownTotal = knownTotal >= 0 ? knownTotal : indexes().size();
        m_hasLoadMore = m_visibleCount < indexes().size() || hasMore;
    }

    QList<ProseDiagnostic> m_diagnostics;
    QHash<QString, QVector<int>> m_categoryIndexes;
    const QVector<int> *m_indexes{nullptr};
    int m_visibleCount{0};
    int m_knownTotal{0};
    bool m_hasLoadMore{false};
};

ProseAwarenessWidget::ProseAwarenessWidget(QWidget *parent)
    : QWidget(parent)
    , m_modeCombo(new QComboBox(this))
    , m_profileCombo(new QComboBox(this))
    , m_scopeCombo(new QComboBox(this))
    , m_scanButton(new QPushButton(tr("Scan document"), this))
    , m_exportMarkdownButton(new QPushButton(tr("Export Markdown"), this))
    , m_exportJsonButton(new QPushButton(tr("Export JSON"), this))
    , m_statusLabel(new QLabel(tr("Engine unavailable"), this))
    , m_lockedFacts(new QPlainTextEdit(this))
    , m_categoryTree(new QTreeWidget(this))
    , m_findingView(new QListView(this))
    , m_findingModel(new ProseFindingListModel(this))
    , m_explanationLabel(new QLabel(this))
    , m_suggestionLabel(new QLabel(this))
    , m_dismissButton(new QPushButton(tr("Dismiss"), this))
    , m_ignoreButton(new QPushButton(tr("Ignore"), this))
    , m_allowButton(new QPushButton(tr("Allow phrase"), this))
    , m_disableRuleButton(new QPushButton(tr("Disable rule"), this))
    , m_explainButton(new QPushButton(tr("Explain"), this))
    , m_reviseButton(new QPushButton(tr("Suggest revision"), this))
    , m_deleteButton(new QToolButton(this))
    , m_backButton(new QToolButton(this))
    , m_nextButton(new QToolButton(this))
    , m_undoButton(new QToolButton(this))
    , m_rewriteSelectionButton(new QPushButton(tr("Rewrite selection"), this))
    , m_modelSettingsButton(new QPushButton(tr("Model settings"), this))
    , m_grammarSettingsButton(new QPushButton(tr("Grammar settings"), this))
    , m_profileEditButton(new QPushButton(tr("Edit profile"), this))
    , m_profileImportButton(new QPushButton(tr("Import"), this))
    , m_profileExportButton(new QPushButton(tr("Export"), this))
    , m_toolsButton(new QToolButton(this))
    , m_collapseButton(new QToolButton(this))
    , m_findingActionsButton(new QToolButton(this))
{
    setObjectName(QStringLiteral("proseAwarenessWidget"));
    setMinimumWidth(320);
    setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Preferred);

    m_modeCombo->addItem(tr("Off"), static_cast<int>(Mode::Off));
    m_modeCombo->addItem(tr("Live"), static_cast<int>(Mode::Live));
    m_modeCombo->addItem(tr("Report only"), static_cast<int>(Mode::ReportOnly));
    m_modeCombo->setCurrentIndex(1);
    m_scopeCombo->addItem(tr("Current selection"), QStringLiteral("selection"));
    m_scopeCombo->addItem(tr("Document"), QStringLiteral("document"));
    m_scopeCombo->addItem(tr("Folder"), QStringLiteral("folder"));
    m_scopeCombo->setCurrentIndex(1);
    for (QComboBox *combo : {m_modeCombo, m_profileCombo, m_scopeCombo}) {
        combo->setSizeAdjustPolicy(QComboBox::AdjustToMinimumContentsLengthWithIcon);
        combo->setMinimumContentsLength(8);
        combo->setFixedWidth(160);
    }
    m_modeCombo->setObjectName(QStringLiteral("proseAwarenessModeCombo"));
    m_profileCombo->setObjectName(QStringLiteral("proseAwarenessProfileCombo"));
    m_scopeCombo->setObjectName(QStringLiteral("proseAwarenessScopeCombo"));
    m_scopeCombo->setAccessibleName(tr("Review scope"));
    m_scopeCombo->setToolTip(tr("Choose the scope to review"));
    m_lockedFacts->setMaximumHeight(96);
    m_lockedFacts->setMaximumBlockCount(200);
    m_lockedFacts->setPlaceholderText(tr("Names, terminology, chronology, dialogue, and plot beats"));
    m_lockedFacts->setAccessibleName(tr("Locked facts, one per line"));

    m_statusLabel->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
    m_statusLabel->setMinimumWidth(0);
    m_statusLabel->setMaximumWidth(100);
    m_statusLabel->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Preferred);
    m_statusLabel->setObjectName(QStringLiteral("proseAwarenessStatusLabel"));
    auto *header = new QHBoxLayout;

    auto *controls = new QFormLayout;
    controls->setFieldGrowthPolicy(QFormLayout::AllNonFixedFieldsGrow);
    controls->setHorizontalSpacing(10);
    controls->setVerticalSpacing(8);
    controls->setLabelAlignment(Qt::AlignLeft | Qt::AlignVCenter);
    const auto addControlRow = [controls, this](const QString &labelText, QWidget *field) {
        auto *label = new QLabel(labelText, this);
        label->setMinimumWidth(78);
        controls->addRow(label, field);
    };
    addControlRow(tr("Mode"), m_modeCombo);
    addControlRow(tr("Profile"), m_profileCombo);

    m_scanButton->setObjectName(QStringLiteral("scanButton"));
    m_scanButton->setMinimumHeight(34);
    m_scanButton->setMinimumWidth(140);
    m_scanButton->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
    m_scanButton->setAccessibleName(tr("Scan document"));
    auto *scanShortcut = new QShortcut(QKeySequence(QStringLiteral("Ctrl+Shift+S")), this);
    scanShortcut->setContext(Qt::WidgetWithChildrenShortcut);
    connect(scanShortcut, &QShortcut::activated, m_scanButton, &QPushButton::click);
    m_rewriteSelectionButton->setMinimumHeight(32);
    m_rewriteSelectionButton->setObjectName(QStringLiteral("rewriteSelectionButton"));
    m_toolsButton->setText(tr("Tools"));
    m_toolsButton->setObjectName(QStringLiteral("proseAwarenessToolsButton"));
    m_toolsButton->setToolButtonStyle(Qt::ToolButtonTextBesideIcon);
    m_toolsButton->setFixedHeight(28);
    m_toolsButton->setPopupMode(QToolButton::InstantPopup);
    m_toolsButton->setAccessibleName(tr("Prose tools and settings"));
    header->addWidget(m_toolsButton);
    header->addStretch();
    m_collapseButton->setObjectName(QStringLiteral("proseAwarenessCollapseButton"));
    m_collapseButton->setText(QStringLiteral("‹"));
    m_collapseButton->setToolButtonStyle(Qt::ToolButtonTextOnly);
    m_collapseButton->setAccessibleName(tr("Collapse sidebar"));
    m_collapseButton->setToolTip(tr("Collapse sidebar"));
    m_collapseButton->setFixedSize(28, 28);
    header->addWidget(m_collapseButton);
    m_statusLabel->hide();
    auto *toolsMenu = new QMenu(m_toolsButton);
    QAction *editProfileAction = toolsMenu->addAction(tr("Edit profile"));
    QAction *importProfileAction = toolsMenu->addAction(tr("Import profile..."));
    QAction *exportProfileAction = toolsMenu->addAction(tr("Export profile..."));
    QAction *rewriteSelectionAction = toolsMenu->addAction(tr("Rewrite selection..."));
    toolsMenu->addSeparator();
    QAction *exportMarkdownAction = toolsMenu->addAction(tr("Export report as Markdown..."));
    QAction *exportJsonAction = toolsMenu->addAction(tr("Export report as JSON..."));
    toolsMenu->addSeparator();
    QAction *grammarSettingsAction = toolsMenu->addAction(tr("Grammar settings..."));
    QAction *modelSettingsAction = toolsMenu->addAction(tr("Model settings..."));
    QAction *performanceSettingsAction = toolsMenu->addAction(tr("Performance settings..."));
    m_toolsButton->setMenu(toolsMenu);
    const QList<QAction *> engineActions = {editProfileAction, importProfileAction, exportProfileAction, rewriteSelectionAction};
    connect(toolsMenu, &QMenu::aboutToShow, this, [this, engineActions]() {
        for (QAction *action : engineActions) {
            action->setEnabled(m_engineReady);
        }
    });

    auto *reviewRow = new QHBoxLayout;
    reviewRow->setSpacing(6);
    reviewRow->addWidget(m_scopeCombo, 1);
    auto *scopeLabel = new QLabel(tr("Scope"), this);
    scopeLabel->setMinimumWidth(78);
    controls->addRow(scopeLabel, reviewRow);

    m_rewriteSelectionButton->setMinimumWidth(0);
    m_rewriteSelectionButton->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Fixed);

    m_lockedFacts->hide();

    m_categoryTree->setColumnCount(2);
    m_categoryTree->header()->hide();
    m_categoryTree->header()->setStretchLastSection(false);
    m_categoryTree->header()->setSectionResizeMode(0, QHeaderView::Stretch);
    m_categoryTree->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    m_categoryTree->setRootIsDecorated(false);
    m_categoryTree->setUniformRowHeights(true);
    m_categoryTree->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    m_categoryTree->setMinimumWidth(0);
    m_categoryTree->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Preferred);
    m_categoryTree->setItemDelegateForColumn(1, new LensCountDelegate(m_categoryTree));
    // Keep the navigator useful at the narrow sidebar width without letting
    // it push the selected-observation actions below the viewport.
    // Match the redesign's lens card.  The additional engine lenses
    // remain available through the card's own scrollbar instead of pushing the
    // observations and actions below the fold.
    // Ten compact primary rows fit without leaving a dead band below Grammar.
    // Repetition and Grammar stay visible next to the other primaries: their
    // findings render by default, so invisible toggles read as bugs.
    // Advanced lenses remain available through the Tools toggle and tree scroll.
    m_categoryTree->setFixedHeight(358);
    m_categoryTree->setObjectName(QStringLiteral("proseAwarenessCategoryTree"));
    m_categoryTree->setAccessibleName(tr("Prose lenses"));
    m_categoryTree->setAccessibleDescription(tr("Check lenses to enable them. Open the context menu for color, timing, and decoration choices."));
    m_categoryTree->setContextMenuPolicy(Qt::CustomContextMenu);

    addCategory(QStringLiteral("general_rules"),
                tr("General rules"),
                tr("Words and phrases the active profile marks as general rules."),
                QColor("#F87171"),
                true);
    addCategory(QStringLiteral("possible_adverbs"), tr("Adverbs"), tr("Words that may be functioning as adverbs."), QColor("#FACC15"), true);
    addCategory(QStringLiteral("possible_adjectives"), tr("Adjectives"), tr("Words functioning as adjectives in this sentence."), QColor("#C084FC"), true);
    addCategory(QStringLiteral("possible_verbs"), tr("Verbs"), tr("Words functioning as verbs in this sentence."), QColor("#34D399"), true);
    addCategory(QStringLiteral("filter_words"), tr("Filter/filler"), tr("Filtering verbs and removable filler words."), QColor("#FB923C"), true);
    addCategory(QStringLiteral("cliches"), tr("Cliches"), tr("Stock phrases and familiar expressions."), QColor("#60A5FA"), true);
    addCategory(QStringLiteral("formulaic_patterns"), tr("Formulaic prose"), tr("Repeated rhetorical patterns and AI-like cadences."), QColor("#F472B6"), true);
    addCategory(QStringLiteral("repetition_rhythm"), tr("Echoes"), tr("Manuscript-wide word and phrase echoes."), QColor("#22D3EE"), true);
    addCategory(QStringLiteral("repetition"), tr("Repetition"), tr("Words repeated close together."), QColor("#A76D87"), true);
    addCategory(QStringLiteral("grammar_mechanics"), tr("Grammar"), tr("Punctuation, agreement, spelling, and copyediting."), QColor("#B8685F"), true);
    auto *advancedGroup = new QTreeWidgetItem(m_categoryTree);
    advancedGroup->setText(0, tr("More lenses"));
    advancedGroup->setFlags(Qt::ItemIsEnabled);
    advancedGroup->setExpanded(false);
    addCategory(QStringLiteral("body_cinematic"),
                tr("Body/cinematic"),
                tr("Stock body language and cinematic atmosphere."),
                QColor("#B66E7D"),
                true,
                advancedGroup);
    addCategory(QStringLiteral("abstraction_agency"),
                tr("Abstraction"),
                tr("Vague abstractions and inanimate false agency."),
                QColor("#6096A4"),
                false,
                advancedGroup);
    addCategory(QStringLiteral("metaphor_texture"), tr("Metaphors"), tr("Dense or clustered metaphors and similes."), QColor("#739764"), false, advancedGroup);
    advancedGroup->setHidden(true);
    toolsMenu->addSeparator();
    QAction *showAdvancedLensesAction = toolsMenu->addAction(tr("Show advanced lenses"));
    showAdvancedLensesAction->setCheckable(true);
    connect(showAdvancedLensesAction, &QAction::toggled, this, [advancedGroup](bool visible) {
        advancedGroup->setHidden(!visible);
    });
    m_categoryTree->setCurrentItem(m_categoryTree->topLevelItem(0));

    m_findingView->setModel(m_findingModel);
    m_findingView->setUniformItemSizes(true);
    m_findingView->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    m_findingView->setTextElideMode(Qt::ElideRight);
    m_findingView->setMinimumWidth(0);
    m_findingView->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Preferred);
    // Keep the six-row observation viewport used by the redesign.  The outer
    // sidebar scroll area handles shorter windows and any expanded detail card.
    m_findingView->setFixedHeight(252);
    m_findingView->setObjectName(QStringLiteral("proseAwarenessFindingView"));
    m_findingView->setAccessibleName(tr("Prose observations"));

    m_explanationLabel->setWordWrap(true);
    m_suggestionLabel->setWordWrap(true);
    m_explanationLabel->setTextInteractionFlags(Qt::TextSelectableByMouse | Qt::TextSelectableByKeyboard);
    m_suggestionLabel->setTextInteractionFlags(Qt::TextSelectableByMouse | Qt::TextSelectableByKeyboard);
    m_explanationLabel->setFocusPolicy(Qt::StrongFocus);
    m_suggestionLabel->setFocusPolicy(Qt::StrongFocus);
    m_findingView->setAccessibleDescription(tr("Use F8 for the next observation and Shift+F8 for the previous observation."));

    m_deleteButton->setText(tr("Delete"));
    m_deleteButton->setIcon(QIcon::fromTheme(QStringLiteral("edit-delete")));
    m_deleteButton->setToolButtonStyle(Qt::ToolButtonTextOnly);
    m_deleteButton->setAccessibleName(tr("Delete selected occurrence"));
    m_deleteButton->setToolTip(tr("Delete the selected occurrence from the manuscript"));
    m_backButton->setText(QStringLiteral("←"));
    m_backButton->setToolButtonStyle(Qt::ToolButtonTextOnly);
    m_backButton->setAccessibleName(tr("Previous observation"));
    m_backButton->setToolTip(tr("Back to the previous observation"));
    m_nextButton->setText(QStringLiteral("→"));
    m_nextButton->setToolButtonStyle(Qt::ToolButtonTextOnly);
    m_nextButton->setAccessibleName(tr("Next observation"));
    m_nextButton->setToolTip(tr("Select and reveal the next observation in this lens"));
    const QIcon undoIcon = QIcon::fromTheme(QStringLiteral("edit-undo"));
    m_undoButton->setIcon(undoIcon);
    if (undoIcon.isNull()) {
        m_undoButton->setText(tr("Undo"));
        m_undoButton->setToolButtonStyle(Qt::ToolButtonTextOnly);
    } else {
        m_undoButton->setToolButtonStyle(Qt::ToolButtonIconOnly);
    }
    m_undoButton->setAccessibleName(tr("Undo manuscript edit"));
    m_undoButton->setToolTip(tr("Undo the last manuscript edit"));
    m_undoButton->setEnabled(false);
    m_findingActionsButton->setText(QStringLiteral("..."));
    m_findingActionsButton->setToolTip(tr("More actions"));
    m_findingActionsButton->setPopupMode(QToolButton::InstantPopup);
    m_findingActionsButton->setAccessibleName(tr("More finding actions"));
    auto *findingMenu = new QMenu(m_findingActionsButton);
    QAction *explainAction = findingMenu->addAction(tr("Explain in context"));
    QAction *reviseAction = findingMenu->addAction(tr("Suggest revision"));
    findingMenu->addSeparator();
    QAction *deleteAllAction = findingMenu->addAction(tr("Delete all in this lens..."));
    findingMenu->addSeparator();
    QAction *dismissAction = findingMenu->addAction(tr("Dismiss for this session"));
    QAction *ignoreAction = findingMenu->addAction(tr("Ignore this occurrence"));
    QAction *allowAction = findingMenu->addAction(tr("Allow this phrase"));
    QAction *disableRuleAction = findingMenu->addAction(tr("Disable this rule"));
    m_findingActionsButton->setMenu(findingMenu);
    m_deleteButton->setObjectName(QStringLiteral("deleteActionButton"));
    m_deleteButton->setMinimumHeight(28);
    m_backButton->setObjectName(QStringLiteral("backObservationButton"));
    m_backButton->setMinimumHeight(28);
    m_backButton->setFixedWidth(36);
    m_nextButton->setObjectName(QStringLiteral("nextObservationButton"));
    m_nextButton->setMinimumHeight(28);
    m_nextButton->setFixedWidth(36);
    m_undoButton->setObjectName(QStringLiteral("undoActionButton"));
    m_undoButton->setMinimumHeight(28);
    m_findingActionsButton->setObjectName(QStringLiteral("findingActionsButton"));
    m_findingActionsButton->setMinimumHeight(28);

    auto *findingActions = new QHBoxLayout;
    findingActions->setSpacing(6);
    findingActions->addWidget(m_deleteButton, 1);
    findingActions->addWidget(m_backButton, 1);
    findingActions->addWidget(m_nextButton, 1);
    findingActions->addWidget(m_undoButton);
    findingActions->addWidget(m_findingActionsButton);

    for (QPushButton *advancedButton : {m_exportMarkdownButton,
                                        m_exportJsonButton,
                                        m_dismissButton,
                                        m_ignoreButton,
                                        m_allowButton,
                                        m_disableRuleButton,
                                        m_explainButton,
                                        m_reviseButton,
                                        m_modelSettingsButton,
                                        m_grammarSettingsButton,
                                        m_profileEditButton,
                                        m_profileImportButton,
                                        m_profileExportButton}) {
        advancedButton->hide();
    }

    auto *lensesTitle = new QLabel(tr("Lenses"), this);
    lensesTitle->setObjectName(QStringLiteral("proseAwarenessSectionLabel"));
    auto *findingsTitle = new QLabel(tr("Observations"), this);
    findingsTitle->setObjectName(QStringLiteral("proseAwarenessSectionLabel"));

    auto *content = new QWidget;
    content->setObjectName(QStringLiteral("proseAwarenessContent"));
    content->setMinimumWidth(0);
    content->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Preferred);
    auto *layout = new QVBoxLayout(content);
    layout->setSizeConstraint(QLayout::SetNoConstraint);
    layout->setContentsMargins(16, 16, 16, 16);
    layout->setSpacing(24);
    auto *setupSurface = new QFrame(content);
    setupSurface->setObjectName(QStringLiteral("proseAwarenessSetupSurface"));
    setupSurface->setFrameShape(QFrame::NoFrame);
    // Keep the compact reference card compact.  Without a fixed vertical
    // policy, the scroll area's spare height is assigned to this surface and
    // leaves a large empty gap between the controls and Scan document.
    setupSurface->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Fixed);
    auto *setupLayout = new QVBoxLayout(setupSurface);
    setupLayout->setContentsMargins(12, 12, 12, 12);
    setupLayout->setSpacing(10);
    setupLayout->addLayout(controls);
    setupLayout->addWidget(m_scanButton);
    layout->addWidget(setupSurface);

    auto *lensesSurface = new QFrame(content);
    lensesSurface->setObjectName(QStringLiteral("proseAwarenessLensesSurface"));
    lensesSurface->setFrameShape(QFrame::NoFrame);
    lensesSurface->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Fixed);
    auto *lensesLayout = new QVBoxLayout(lensesSurface);
    lensesLayout->setContentsMargins(0, 0, 0, 0);
    lensesLayout->setSpacing(7);
    lensesLayout->addWidget(lensesTitle);
    auto *lensesHint = new QLabel(tr("Choose a lens to inspect its observations."), lensesSurface);
    lensesHint->setObjectName(QStringLiteral("proseAwarenessHelperLabel"));
    lensesHint->setWordWrap(true);
    lensesLayout->addWidget(lensesHint);
    lensesLayout->addWidget(m_categoryTree);
    layout->addWidget(lensesSurface);
    auto *findingsSurface = new QFrame(content);
    findingsSurface->setObjectName(QStringLiteral("proseAwarenessFindingsSurface"));
    findingsSurface->setFrameShape(QFrame::NoFrame);
    auto *findingsLayout = new QVBoxLayout(findingsSurface);
    findingsLayout->setContentsMargins(0, 0, 0, 0);
    findingsLayout->setSpacing(7);
    findingsLayout->addWidget(findingsTitle);
    m_findingsHint = new QLabel(tr("Choose a lens to see its observations."), findingsSurface);
    m_findingsHint->setObjectName(QStringLiteral("proseAwarenessFindingsHint"));
    m_findingsHint->setWordWrap(true);
    findingsLayout->addWidget(m_findingsHint);
    findingsLayout->addWidget(m_findingView);

    auto *detailsSurface = new QFrame(findingsSurface);
    detailsSurface->setObjectName(QStringLiteral("proseAwarenessDetailsSurface"));
    detailsSurface->setFrameShape(QFrame::NoFrame);
    auto *detailsLayout = new QVBoxLayout(detailsSurface);
    detailsLayout->setContentsMargins(12, 10, 12, 10);
    detailsLayout->setSpacing(4);
    auto *explanationTitle = new QLabel(tr("Observation"), detailsSurface);
    explanationTitle->setObjectName(QStringLiteral("proseAwarenessDetailLabel"));
    detailsLayout->addWidget(explanationTitle);
    m_explanationLabel->setObjectName(QStringLiteral("proseAwarenessExplanationLabel"));
    detailsLayout->addWidget(m_explanationLabel);
    auto *suggestionTitle = new QLabel(tr("Suggested revision"), detailsSurface);
    suggestionTitle->setObjectName(QStringLiteral("proseAwarenessDetailLabel"));
    detailsLayout->addWidget(suggestionTitle);
    m_suggestionLabel->setObjectName(QStringLiteral("proseAwarenessSuggestionLabel"));
    detailsLayout->addWidget(m_suggestionLabel);
    // The reference card keeps the review controls attached to the
    // observation they act on.  Keeping the same parent also prevents a
    // detached action strip from reading like a second unrelated section.
    m_findingActionsSurface = new QFrame(detailsSurface);
    m_findingActionsSurface->setObjectName(QStringLiteral("proseAwarenessFindingActions"));
    auto *findingActionsLayout = new QVBoxLayout(m_findingActionsSurface);
    findingActionsLayout->setContentsMargins(0, 8, 0, 0);
    findingActionsLayout->addLayout(findingActions);
    detailsLayout->addWidget(m_findingActionsSurface);
    detailsSurface->setVisible(false);
    findingsLayout->addWidget(detailsSurface);
    layout->addWidget(findingsSurface);

    auto *scrollArea = new SidebarScrollArea(this);
    scrollArea->setObjectName(QStringLiteral("proseAwarenessScrollArea"));
    scrollArea->setWidgetResizable(true);
    scrollArea->setFrameShape(QFrame::NoFrame);
    scrollArea->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    scrollArea->setWidget(content);
    auto *outerLayout = new QVBoxLayout(this);
    outerLayout->setContentsMargins(0, 0, 0, 0);
    outerLayout->setSpacing(0);
    auto *headerSurface = new QFrame(this);
    headerSurface->setObjectName(QStringLiteral("proseAwarenessHeaderSurface"));
    auto *headerSurfaceLayout = new QVBoxLayout(headerSurface);
    // The redesign uses a deliberately quiet, full-height tool header.  Keep
    // its content aligned with the 16 px sidebar inset and the 56 px activity
    // rail rather than allowing the native style to compress it.
    headerSurfaceLayout->setContentsMargins(16, 14, 16, 14);
    headerSurfaceLayout->addLayout(header);
    outerLayout->addWidget(headerSurface);
    outerLayout->addWidget(scrollArea, 1);
    connect(editProfileAction, &QAction::triggered, m_profileEditButton, &QPushButton::click);
    connect(importProfileAction, &QAction::triggered, m_profileImportButton, &QPushButton::click);
    connect(exportProfileAction, &QAction::triggered, m_profileExportButton, &QPushButton::click);
    connect(rewriteSelectionAction, &QAction::triggered, m_rewriteSelectionButton, &QPushButton::click);
    connect(exportMarkdownAction, &QAction::triggered, m_exportMarkdownButton, &QPushButton::click);
    connect(exportJsonAction, &QAction::triggered, m_exportJsonButton, &QPushButton::click);
    connect(grammarSettingsAction, &QAction::triggered, m_grammarSettingsButton, &QPushButton::click);
    connect(modelSettingsAction, &QAction::triggered, m_modelSettingsButton, &QPushButton::click);
    connect(performanceSettingsAction, &QAction::triggered, this, &ProseAwarenessWidget::performanceSettingsRequested);
    connect(explainAction, &QAction::triggered, m_explainButton, &QPushButton::click);
    connect(reviseAction, &QAction::triggered, m_reviseButton, &QPushButton::click);
    connect(dismissAction, &QAction::triggered, m_dismissButton, &QPushButton::click);
    connect(ignoreAction, &QAction::triggered, m_ignoreButton, &QPushButton::click);
    connect(allowAction, &QAction::triggered, m_allowButton, &QPushButton::click);
    connect(disableRuleAction, &QAction::triggered, m_disableRuleButton, &QPushButton::click);
    connect(deleteAllAction, &QAction::triggered, this, [this]() {
        emit deleteAllRequested(selectedCategory(), categoryLabel(selectedCategory()));
    });
    connect(findingMenu,
            &QMenu::aboutToShow,
            this,
            [this, explainAction, reviseAction, deleteAllAction, dismissAction, ignoreAction, allowAction, disableRuleAction]() {
                explainAction->setEnabled(m_explainButton->isEnabled());
                reviseAction->setEnabled(m_reviseButton->isEnabled());
                deleteAllAction->setEnabled(std::any_of(m_diagnostics.cbegin(), m_diagnostics.cend(), [this](const ProseDiagnostic &diagnostic) {
                    return diagnostic.category == selectedCategory();
                }));
                dismissAction->setEnabled(m_dismissButton->isEnabled());
                ignoreAction->setEnabled(m_ignoreButton->isEnabled());
                allowAction->setEnabled(m_allowButton->isEnabled());
                disableRuleAction->setEnabled(m_disableRuleButton->isEnabled());
            });

    connect(m_scanButton, &QPushButton::clicked, this, [this]() {
        if (!m_engineReady || m_scanRunning) {
            return;
        }
        setDiagnostics({});
        m_reviewRunning = scope() != QStringLiteral("document");
        m_scanRunning = true;
        updateScanState();
        emit reviewRequested(scope());
    });
    connect(m_modeCombo, &QComboBox::currentIndexChanged, this, [this]() {
        emit modeChanged(mode());
    });
    connect(m_profileCombo, &QComboBox::currentTextChanged, this, &ProseAwarenessWidget::profileChanged);
    connect(m_scopeCombo, &QComboBox::currentIndexChanged, this, [this]() {
        updateScanState();
    });
    connect(m_exportMarkdownButton, &QPushButton::clicked, this, &ProseAwarenessWidget::exportMarkdownRequested);
    connect(m_exportJsonButton, &QPushButton::clicked, this, &ProseAwarenessWidget::exportJsonRequested);
    connect(m_categoryTree, &QTreeWidget::itemChanged, this, [this](QTreeWidgetItem *item, int column) {
        if (column == 0 && item->data(0, CategoryIdRole).isValid()) {
            const QString category = item->data(0, CategoryIdRole).toString();
            const bool enabled = item->checkState(0) == Qt::Checked;
            item->setText(1, QString::number(enabled ? m_categoryCounts.value(category, 0) : 0));
            emit categoryEnabledChanged(category, enabled);
        }
    });
    connect(m_categoryTree, &QTreeWidget::currentItemChanged, this, [this]() {
        m_pendingNextRow = -1;
        m_visibleFindingCount = FindingPageSize;
        m_hasMoreFindings = false;
        m_selectedCategoryTotal = -1;
        rebuildFindings();
        emit categorySelected(selectedCategory());
    });
    connect(m_categoryTree, &QTreeWidget::itemDoubleClicked, this, [this](QTreeWidgetItem *item, int) {
        if (item->data(0, CategoryIdRole).isValid()) {
            chooseCategoryColor(item);
        }
    });
    connect(m_categoryTree, &QWidget::customContextMenuRequested, this, [this](const QPoint &position) {
        QTreeWidgetItem *selected = m_categoryTree->itemAt(position);
        QPoint menuPosition = position;
        if (!selected) {
            selected = m_categoryTree->currentItem();
            if (!selected) {
                return;
            }
            menuPosition = m_categoryTree->visualItemRect(selected).center();
        }
        const QString category = selected->data(0, CategoryIdRole).toString();
        if (category.isEmpty()) {
            return;
        }
        QMenu menu(this);
        QAction *isolate = menu.addAction(tr("Show only this lens"));
        QAction *showAll = menu.addAction(tr("Show all lenses"));
        QAction *chooseColor = menu.addAction(tr("Choose lens color"));
        menu.addSeparator();
        QAction *liveMode = menu.addAction(tr("Run live"));
        QAction *idleMode = menu.addAction(tr("Run after pause and in reports"));
        QAction *reportMode = menu.addAction(tr("Run in reports only"));
        menu.addSeparator();
        QAction *background = menu.addAction(tr("Background decoration"));
        QAction *underline = menu.addAction(tr("Underline decoration"));
        QAction *combined = menu.addAction(tr("Background and underline"));
        QAction *chosen = menu.exec(m_categoryTree->viewport()->mapToGlobal(menuPosition));
        if (chosen == isolate || chosen == showAll) {
            for (QTreeWidgetItem *item : categoryItems(m_categoryTree)) {
                item->setCheckState(0, chosen == showAll || item == selected ? Qt::Checked : Qt::Unchecked);
            }
        } else if (chosen == chooseColor) {
            chooseCategoryColor(selected);
        } else if (chosen == background) {
            emit categoryDecorationChanged(category, QStringLiteral("background"));
        } else if (chosen == underline) {
            emit categoryDecorationChanged(category, QStringLiteral("underline"));
        } else if (chosen == combined) {
            emit categoryDecorationChanged(category, QStringLiteral("background_underline"));
        } else if (chosen == liveMode) {
            emit categoryModeChanged(category, QStringLiteral("live"));
        } else if (chosen == idleMode) {
            emit categoryModeChanged(category, QStringLiteral("idle"));
        } else if (chosen == reportMode) {
            emit categoryModeChanged(category, QStringLiteral("report"));
        }
    });
    connect(m_findingView->selectionModel(), &QItemSelectionModel::selectionChanged, this, [this]() {
        updateDetails();
    });
    connect(m_findingView, &QListView::activated, this, [this](const QModelIndex &index) {
        if (m_findingModel->isLoadMore(index)) {
            const QString category = selectedCategory();
            const int loaded = m_findingModel->loadedCount();
            if (m_visibleFindingCount < loaded) {
                m_visibleFindingCount += FindingPageSize;
                rebuildFindings();
            } else if (m_hasMoreFindings) {
                emit moreFindingsRequested(category);
            }
            return;
        }
        const ProseDiagnostic diagnostic = selectedDiagnostic();
        if (!diagnostic.ruleId.isEmpty()) {
            emit findingActivated(diagnostic);
        }
    });
    connect(m_dismissButton, &QPushButton::clicked, this, [this]() {
        emit dismissRequested(selectedDiagnostic());
    });
    connect(m_ignoreButton, &QPushButton::clicked, this, [this]() {
        emit ignoreRequested(selectedDiagnostic());
    });
    connect(m_allowButton, &QPushButton::clicked, this, [this]() {
        emit allowPhraseRequested(selectedDiagnostic());
    });
    connect(m_disableRuleButton, &QPushButton::clicked, this, [this]() {
        emit disableRuleRequested(selectedDiagnostic());
    });
    connect(m_explainButton, &QPushButton::clicked, this, [this]() {
        emit explainRequested(selectedDiagnostic());
    });
    connect(m_reviseButton, &QPushButton::clicked, this, [this]() {
        emit revisionRequested(selectedDiagnostic());
    });
    connect(m_deleteButton, &QToolButton::clicked, this, [this]() {
        emit deleteRequested(selectedDiagnostic());
    });
    connect(m_backButton, &QToolButton::clicked, this, [this]() {
        activateAdjacentFinding(-1);
    });
    connect(m_nextButton, &QToolButton::clicked, this, [this]() {
        activateAdjacentFinding(1);
    });
    connect(m_undoButton, &QToolButton::clicked, this, &ProseAwarenessWidget::undoRequested);
    connect(m_rewriteSelectionButton, &QPushButton::clicked, this, &ProseAwarenessWidget::rewriteSelectionRequested);
    connect(m_modelSettingsButton, &QPushButton::clicked, this, &ProseAwarenessWidget::modelSettingsRequested);
    connect(m_grammarSettingsButton, &QPushButton::clicked, this, &ProseAwarenessWidget::grammarSettingsRequested);
    connect(m_profileEditButton, &QPushButton::clicked, this, &ProseAwarenessWidget::editProfileRequested);
    connect(m_profileImportButton, &QPushButton::clicked, this, &ProseAwarenessWidget::importProfileRequested);
    connect(m_profileExportButton, &QPushButton::clicked, this, &ProseAwarenessWidget::exportProfileRequested);
    connect(m_collapseButton, &QToolButton::clicked, this, &ProseAwarenessWidget::collapseRequested);
    connect(m_lockedFacts, &QPlainTextEdit::textChanged, this, [this]() {
        emit lockedFactsChanged(lockedFacts());
    });

    setEngineReady(false);
    updateDetails();
}

void ProseAwarenessWidget::setCollapseIcon(const QIcon &icon)
{
    if (icon.isNull()) {
        m_collapseButton->setIcon(QIcon());
        m_collapseButton->setText(QStringLiteral("‹"));
        m_collapseButton->setToolButtonStyle(Qt::ToolButtonTextOnly);
    } else {
        m_collapseButton->setText(QString());
        m_collapseButton->setIcon(icon);
        m_collapseButton->setToolButtonStyle(Qt::ToolButtonIconOnly);
    }
}

void ProseAwarenessWidget::addCategory(const QString &id,
                                       const QString &label,
                                       const QString &description,
                                       const QColor &color,
                                       bool live,
                                       QTreeWidgetItem *parent)
{
    auto *item = parent ? new QTreeWidgetItem(parent) : new QTreeWidgetItem(m_categoryTree);
    item->setText(0, label);
    item->setText(1, QStringLiteral("0"));
    item->setData(0, CategoryIdRole, id);
    item->setData(0, CategoryDescriptionRole, description);
    item->setData(0, CategoryModeRole, live ? QStringLiteral("live") : QStringLiteral("report"));
    item->setFlags(item->flags() | Qt::ItemIsUserCheckable);
    item->setCheckState(0, live ? Qt::Checked : Qt::Unchecked);
    applyCategoryColor(item, color);
    m_categoryColors.insert(id, color);
    updateCategoryToolTip(item);
}

void ProseAwarenessWidget::applyCategoryColor(QTreeWidgetItem *item, const QColor &color)
{
    QPixmap swatch(14, 14);
    swatch.fill(Qt::transparent);
    QPainter painter(&swatch);
    painter.setRenderHint(QPainter::Antialiasing);
    painter.setPen(Qt::NoPen);
    painter.setBrush(color);
    painter.drawRoundedRect(QRectF(1, 1, 12, 12), 3, 3);
    item->setIcon(0, QIcon());
    item->setIcon(1, QIcon(swatch));
    item->setBackground(0, QBrush());
    item->setForeground(0, QBrush());
}

void ProseAwarenessWidget::chooseCategoryColor(QTreeWidgetItem *item)
{
    if (!item) {
        return;
    }
    const QString category = item->data(0, CategoryIdRole).toString();
    const QColor selected = QColorDialog::getColor(m_categoryColors.value(category), this, tr("Choose lens color"));
    if (!selected.isValid()) {
        return;
    }
    m_categoryColors.insert(category, selected);
    {
        const QSignalBlocker blocker(m_categoryTree);
        applyCategoryColor(item, selected);
    }
    emit categoryColorChanged(category, selected);
}

void ProseAwarenessWidget::updateCategoryToolTip(QTreeWidgetItem *item)
{
    const QString mode = item->data(0, CategoryModeRole).toString();
    const QString modeLabel = mode == QStringLiteral("live") ? tr("Runs live")
        : mode == QStringLiteral("idle")                     ? tr("Runs after a pause")
                                                             : tr("Runs in reports");
    item->setToolTip(0, tr("%1\n%2").arg(item->data(0, CategoryDescriptionRole).toString(), modeLabel));
}

ProseAwarenessWidget::Mode ProseAwarenessWidget::mode() const
{
    return static_cast<Mode>(m_modeCombo->currentData().toInt());
}

void ProseAwarenessWidget::setMode(Mode mode)
{
    const int index = m_modeCombo->findData(static_cast<int>(mode));
    if (index < 0) {
        return;
    }
    m_modeCombo->blockSignals(true);
    m_modeCombo->setCurrentIndex(index);
    m_modeCombo->blockSignals(false);
}

QString ProseAwarenessWidget::profile() const
{
    return m_profileCombo->currentText();
}

QString ProseAwarenessWidget::scope() const
{
    return m_scopeCombo->currentData().toString();
}

QStringList ProseAwarenessWidget::lockedFacts() const
{
    QStringList facts;
    QSet<QString> seen;
    for (const QString &line : m_lockedFacts->toPlainText().split(QLatin1Char('\n'))) {
        const QString fact = line.trimmed();
        if (!fact.isEmpty() && !seen.contains(fact.toCaseFolded())) {
            facts.append(fact);
            seen.insert(fact.toCaseFolded());
        }
    }
    return facts;
}

QString ProseAwarenessWidget::categoryLabel(const QString &category) const
{
    for (QTreeWidgetItem *item : categoryItems(m_categoryTree)) {
        if (item->data(0, CategoryIdRole).toString() == category) {
            return item->text(0);
        }
    }
    return category;
}

QString ProseAwarenessWidget::categoryFindingLabel(const QString &category) const
{
    static const QHash<QString, QString> labels = {
        {QStringLiteral("general_rules"), tr("General rule")},
        {QStringLiteral("possible_adverbs"), tr("Adverb")},
        {QStringLiteral("possible_adjectives"), tr("Adjective")},
        {QStringLiteral("possible_verbs"), tr("Verb")},
        {QStringLiteral("filter_words"), tr("Filter or filler word")},
        {QStringLiteral("cliches"), tr("Cliche")},
        {QStringLiteral("formulaic_patterns"), tr("Formulaic pattern")},
        {QStringLiteral("repetition_rhythm"), tr("Echo")},
        {QStringLiteral("abstraction_agency"), tr("Abstraction or false agency")},
        {QStringLiteral("metaphor_texture"), tr("Metaphor or texture")},
        {QStringLiteral("grammar_mechanics"), tr("Grammar or mechanics")},
        {QStringLiteral("repetition"), tr("Repetition")},
    };
    return labels.value(category, categoryLabel(category));
}

QString ProseAwarenessWidget::categoryDescription(const QString &category) const
{
    for (QTreeWidgetItem *item : categoryItems(m_categoryTree)) {
        if (item->data(0, CategoryIdRole).toString() == category) {
            return item->data(0, CategoryDescriptionRole).toString();
        }
    }
    return QString();
}

void ProseAwarenessWidget::setLockedFacts(const QStringList &facts)
{
    m_lockedFacts->blockSignals(true);
    m_lockedFacts->setPlainText(facts.join(QLatin1Char('\n')));
    m_lockedFacts->blockSignals(false);
}

void ProseAwarenessWidget::setProfiles(const QStringList &profiles, const QString &selected)
{
    m_profileCombo->blockSignals(true);
    m_profileCombo->clear();
    m_profileCombo->addItems(profiles);
    const int selectedIndex = m_profileCombo->findText(selected);
    if (selectedIndex >= 0) {
        m_profileCombo->setCurrentIndex(selectedIndex);
    }
    m_profileCombo->blockSignals(false);
}

void ProseAwarenessWidget::setCategoryState(const QString &category, bool enabled, const QColor &color)
{
    for (QTreeWidgetItem *item : categoryItems(m_categoryTree)) {
        if (item->data(0, CategoryIdRole).toString() != category) {
            continue;
        }
        const QSignalBlocker blocker(m_categoryTree);
        item->setCheckState(0, enabled ? Qt::Checked : Qt::Unchecked);
        applyCategoryColor(item, color);
        m_categoryColors.insert(category, color);
        updateCategoryToolTip(item);
        return;
    }
}

void ProseAwarenessWidget::setCategoryMode(const QString &category, const QString &mode)
{
    for (QTreeWidgetItem *item : categoryItems(m_categoryTree)) {
        if (item->data(0, CategoryIdRole).toString() != category) {
            continue;
        }
        const QSignalBlocker blocker(m_categoryTree);
        item->setData(0, CategoryModeRole, mode);
        updateCategoryToolTip(item);
        return;
    }
}

void ProseAwarenessWidget::setDiagnostics(const QList<ProseDiagnostic> &diagnostics, bool hasMore, int selectedCategoryTotal, bool appendPage)
{
    m_diagnostics = diagnostics;
    if (!appendPage) {
        m_pendingNextRow = -1;
    }
    if (appendPage) {
        m_visibleFindingCount = m_visibleFindingCount == std::numeric_limits<int>::max()
            ? m_diagnostics.size()
            : std::min(std::numeric_limits<int>::max() - FindingPageSize, m_visibleFindingCount) + FindingPageSize;
    } else {
        m_visibleFindingCount = FindingPageSize;
    }
    m_hasMoreFindings = hasMore;
    m_selectedCategoryTotal = selectedCategoryTotal;
    m_findingModel->setDiagnostics(diagnostics, selectedCategory(), m_visibleFindingCount, m_hasMoreFindings, m_selectedCategoryTotal);
    if (m_pendingNextRow >= 0) {
        const int targetRow = m_pendingNextRow;
        m_pendingNextRow = -1;
        const QModelIndex target = m_findingModel->index(targetRow, 0);
        if (target.isValid() && !m_findingModel->isLoadMore(target)) {
            activateFinding(target);
        } else if (!m_hasMoreFindings && m_findingModel->rowCount() > 0) {
            activateFinding(m_findingModel->index(0, 0));
        }
    } else if (!m_findingView->currentIndex().isValid() && m_findingModel->rowCount() > 0) {
        m_findingView->setCurrentIndex(m_findingModel->index(0, 0));
    }
    updateDetails();
}

void ProseAwarenessWidget::setCategoryCounts(const QHash<QString, int> &counts)
{
    const bool firstPopulation = m_categoryCounts.isEmpty() && !counts.isEmpty();
    m_categoryCounts = counts;
    for (QTreeWidgetItem *item : categoryItems(m_categoryTree)) {
        const QString category = item->data(0, CategoryIdRole).toString();
        const bool enabled = item->checkState(0) == Qt::Checked;
        item->setText(1, QString::number(enabled ? counts.value(category) : 0));
    }
    if (firstPopulation) {
        selectFirstPopulatedCategory();
    }
    updateDetails();
}

void ProseAwarenessWidget::selectFirstPopulatedCategory()
{
    if (categoryCount(selectedCategory()) > 0) {
        return;
    }
    for (QTreeWidgetItem *item : categoryItems(m_categoryTree)) {
        if (categoryCount(item->data(0, CategoryIdRole).toString()) > 0) {
            m_categoryTree->setCurrentItem(item);
            return;
        }
    }
}

int ProseAwarenessWidget::categoryCount(const QString &category) const
{
    return m_categoryCounts.value(category, -1);
}

QString ProseAwarenessWidget::selectedCategory() const
{
    QTreeWidgetItem *item = m_categoryTree->currentItem();
    return item ? item->data(0, CategoryIdRole).toString() : QString();
}

void ProseAwarenessWidget::rebuildFindings()
{
    m_findingModel->setCategory(selectedCategory(), m_visibleFindingCount, m_hasMoreFindings, m_selectedCategoryTotal);
    if (!m_findingView->currentIndex().isValid() && m_findingModel->rowCount() > 0) {
        m_findingView->setCurrentIndex(m_findingModel->index(0, 0));
    }
    updateDetails();
}

void ProseAwarenessWidget::activateFinding(const QModelIndex &index)
{
    if (!index.isValid() || m_findingModel->isLoadMore(index)) {
        return;
    }
    m_findingView->setCurrentIndex(index);
    m_findingView->scrollTo(index);
    const ProseDiagnostic diagnostic = selectedDiagnostic();
    if (!diagnostic.ruleId.isEmpty()) {
        emit findingPreviewRequested(diagnostic);
    }
}

void ProseAwarenessWidget::activateAdjacentFinding(int direction)
{
    const int rowCount = m_findingModel->rowCount();
    if (rowCount <= 0) {
        return;
    }

    const QModelIndex current = m_findingView->currentIndex();
    int targetRow = current.isValid() && !m_findingModel->isLoadMore(current) ? current.row() + direction : 0;
    if (targetRow < 0) {
        targetRow = rowCount - 1;
        if (m_findingModel->isLoadMore(m_findingModel->index(targetRow, 0))) {
            --targetRow;
        }
    } else if (targetRow >= rowCount) {
        targetRow = 0;
    }

    QModelIndex target = m_findingModel->index(targetRow, 0);
    if (!m_findingModel->isLoadMore(target)) {
        activateFinding(target);
        return;
    }

    const int loaded = m_findingModel->loadedCount();
    if (m_visibleFindingCount < loaded) {
        m_visibleFindingCount += FindingPageSize;
        rebuildFindings();
        activateFinding(m_findingModel->index(targetRow, 0));
    } else if (m_hasMoreFindings) {
        m_pendingNextRow = targetRow;
        emit moreFindingsRequested(selectedCategory());
    } else {
        activateFinding(m_findingModel->index(0, 0));
    }
}

void ProseAwarenessWidget::selectDiagnostic(const ProseDiagnostic &diagnostic)
{
    for (QTreeWidgetItem *item : categoryItems(m_categoryTree)) {
        if (item->data(0, CategoryIdRole).toString() == diagnostic.category) {
            if (item->parent() && !item->parent()->isExpanded()) {
                item->parent()->setExpanded(true);
            }
            m_categoryTree->setCurrentItem(item);
            break;
        }
    }
    for (int index = 0; index < m_diagnostics.size(); ++index) {
        const ProseDiagnostic &candidate = m_diagnostics.at(index);
        if ((!diagnostic.id.isEmpty() && candidate.id == diagnostic.id)
            || (candidate.ruleId == diagnostic.ruleId && candidate.start == diagnostic.start && candidate.end == diagnostic.end)) {
            int row = m_findingModel->rowForDiagnosticIndex(index);
            if (row < 0) {
                m_visibleFindingCount = std::numeric_limits<int>::max();
                rebuildFindings();
                row = m_findingModel->rowForDiagnosticIndex(index);
            }
            if (row < 0) {
                return;
            }
            const QModelIndex modelIndex = m_findingModel->index(row, 0);
            m_findingView->setCurrentIndex(modelIndex);
            m_findingView->scrollTo(modelIndex);
            updateDetails();
            return;
        }
    }
}

void ProseAwarenessWidget::setEngineReady(bool ready)
{
    m_engineReady = ready;
    if (!ready) {
        m_scanRunning = false;
        m_reviewRunning = false;
    }
    m_statusLabel->setText(ready ? tr("Ready") : tr("Unavailable"));
    m_profileEditButton->setEnabled(ready);
    m_profileImportButton->setEnabled(ready);
    m_profileExportButton->setEnabled(ready);
    m_rewriteSelectionButton->setEnabled(ready);
    updateScanState();
    updateDetails();
    if (!ready) {
        for (QPushButton *button : {m_dismissButton, m_ignoreButton, m_allowButton, m_disableRuleButton, m_explainButton, m_reviseButton}) {
            button->setEnabled(false);
        }
        m_deleteButton->setEnabled(false);
        m_backButton->setEnabled(false);
        m_nextButton->setEnabled(false);
        m_findingActionsButton->setEnabled(false);
    }
}

void ProseAwarenessWidget::setEngineMessage(const QString &message)
{
    const QString compact = message.size() > 36 ? message.left(33).trimmed() + QStringLiteral("...") : message;
    m_statusLabel->setText(compact);
    m_statusLabel->setToolTip(message);
    m_statusLabel->setAccessibleDescription(message);
    // Progress feedback from the review machinery ends with an ellipsis; any other
    // status ("Ready", errors) means no scan is in flight anymore.
    if (!message.endsWith(QStringLiteral("..."))) {
        m_scanRunning = false;
        m_reviewRunning = false;
    }
    updateScanState();
    updateDetails();
}

void ProseAwarenessWidget::setUndoAvailable(bool available)
{
    m_undoButton->setEnabled(available);
    updateDetails();
}

void ProseAwarenessWidget::updateScanState()
{
    m_scanButton->setEnabled(m_engineReady && !m_scanRunning);
    m_scopeCombo->setEnabled(m_engineReady && !m_scanRunning);
    m_findingView->setVisible(m_engineReady && !m_scanRunning);
    if (QWidget *detailsSurface = m_explanationLabel->parentWidget()) {
        detailsSurface->setVisible(m_engineReady && !m_scanRunning && !selectedDiagnostic().ruleId.isEmpty());
    }
    if (!m_engineReady) {
        m_scanButton->setText(tr("Engine starting..."));
        m_scanButton->setToolTip(tr("Unavailable while the ThothPad Engine is not ready."));
        m_findingsHint->setText(tr("Waiting for the ThothPad Engine."));
    } else if (m_scanRunning) {
        if (m_reviewRunning) {
            m_statusLabel->setText(tr("Reviewing..."));
            m_statusLabel->setToolTip(tr("Reviewing the selected scope..."));
        }
        m_scanButton->setText(m_reviewRunning ? tr("Reviewing...") : tr("Scanning..."));
        m_scanButton->setToolTip(m_reviewRunning ? tr("Reviewing the selected scope...") : tr("Scanning..."));
        m_findingsHint->setText(m_reviewRunning ? tr("Reviewing the selected scope. Findings will appear here when the pass completes.")
                                                : tr("Scanning the document. Findings will appear here when the pass completes."));
    } else {
        const QString action = scope() == QStringLiteral("selection") ? tr("Analyze selection")
            : scope() == QStringLiteral("folder")                     ? tr("Scan folder")
                                                                      : tr("Scan document");
        m_scanButton->setText(action);
        m_scanButton->setToolTip(scope() == QStringLiteral("document") ? tr("Run a full-document scan with contextual analysis. Shortcut: Ctrl+Shift+S")
                                                                       : tr("Analyze the selected review scope. Shortcut: Ctrl+Shift+S"));
        m_findingsHint->setText(tr("Choose a lens to see its observations."));
    }
}

ProseDiagnostic ProseAwarenessWidget::selectedDiagnostic() const
{
    const QModelIndex current = m_findingView->currentIndex();
    if (!current.isValid()) {
        return {};
    }
    const int index = current.data(DiagnosticIndexRole).toInt();
    if (index < 0 || index >= m_diagnostics.size()) {
        return {};
    }
    return m_diagnostics.at(index);
}

void ProseAwarenessWidget::updateDetails()
{
    const ProseDiagnostic diagnostic = selectedDiagnostic();
    const bool selected = !m_scanRunning && !diagnostic.ruleId.isEmpty();
    if (auto *detailsSurface = findChild<QFrame *>(QStringLiteral("proseAwarenessDetailsSurface"))) {
        detailsSurface->setVisible(m_engineReady && selected);
    }
    if (m_findingActionsSurface) {
        m_findingActionsSurface->setVisible(m_engineReady && !m_scanRunning && selected);
    }
    if (m_engineReady && !m_scanRunning) {
        const QString lens = categoryLabel(selectedCategory());
        const int count = categoryCount(selectedCategory());
        const QTreeWidgetItem *lensItem = m_categoryTree->currentItem();
        const bool lensEnabled = lensItem && lensItem->checkState(0) == Qt::Checked;
        if (!lensEnabled) {
            m_findingsHint->setText(tr("%1 is off. Turn it on to analyze and highlight this lens.").arg(lens));
        } else if (count > 0 && m_findingModel->isEmptyState()) {
            m_findingsHint->setText(tr("%1 — %2 observation%3. Loading details…").arg(lens).arg(count).arg(count == 1 ? QString() : QStringLiteral("s")));
        } else if (m_findingModel->isEmptyState()) {
            m_findingsHint->setText(tr("No observations in %1.").arg(lens));
        } else {
            m_findingsHint->setText(tr("%1 — %2 observation%3. Select one to review it.")
                                        .arg(lens)
                                        .arg(count > -1 ? count : m_findingModel->loadedCount())
                                        .arg((count > -1 ? count : m_findingModel->loadedCount()) == 1 ? QString() : QStringLiteral("s")));
        }
    }
    const QString provenance = selected ? tr("%1 | Confidence %2% | Source: %3")
                                              .arg(levelLabel(diagnostic.level))
                                              .arg(qRound(diagnostic.confidence * 100.0))
                                              .arg(diagnostic.source.isEmpty() ? tr("unspecified") : diagnostic.source)
                                        : QString();
    m_explanationLabel->setText(selected ? diagnostic.explanation + QStringLiteral("\n") + provenance : tr("Select an observation to review it."));
    m_suggestionLabel->setText(selected ? diagnostic.suggestion : QString());
    m_dismissButton->setEnabled(selected);
    m_ignoreButton->setEnabled(selected);
    m_allowButton->setEnabled(selected);
    m_disableRuleButton->setEnabled(selected);
    m_explainButton->setEnabled(selected);
    m_reviseButton->setEnabled(selected);
    m_deleteButton->setEnabled(selected);
    m_backButton->setEnabled(selected);
    m_nextButton->setEnabled(selected);
    m_findingActionsButton->setEnabled(m_engineReady && selected && !m_scanRunning);
}
}
