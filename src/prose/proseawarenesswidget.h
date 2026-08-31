/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef PROSE_AWARENESS_WIDGET_H
#define PROSE_AWARENESS_WIDGET_H

#include <QColor>
#include <QHash>
#include <QIcon>
#include <QList>
#include <QStringList>
#include <QWidget>

#include "prosediagnostic.h"

class QComboBox;
class QLabel;
class QListView;
class QPlainTextEdit;
class QPushButton;
class QToolButton;
class QTreeWidget;
class QTreeWidgetItem;

namespace ghostwriter
{
class ProseFindingListModel;

class ProseAwarenessWidget : public QWidget
{
    Q_OBJECT

public:
    enum class Mode {
        Off,
        Live,
        ReportOnly
    };
    Q_ENUM(Mode)

    explicit ProseAwarenessWidget(QWidget *parent = nullptr);

    Mode mode() const;
    QString profile() const;
    QString scope() const;
    QString selectedCategory() const;
    QStringList lockedFacts() const;
    QString categoryLabel(const QString &category) const;
    QString categoryFindingLabel(const QString &category) const;
    QString categoryDescription(const QString &category) const;

    /**
     * Read-only state adapters used by Story Intelligence's native tool
     * harness. These return copies so the agent path cannot mutate Prose
     * Awareness presentation or diagnostic state behind the widget's back.
     */
    QHash<QString, int> categoryCountsSnapshot() const { return m_categoryCounts; }
    QList<ProseDiagnostic> diagnosticsSnapshot() const { return m_diagnostics; }
    bool engineReadySnapshot() const { return m_engineReady; }

    void setMode(Mode mode);
    void setProfiles(const QStringList &profiles, const QString &selected);
    void setCategoryState(const QString &category, bool enabled, const QColor &color);
    void setCategoryMode(const QString &category, const QString &mode);
    void setDiagnostics(const QList<ProseDiagnostic> &diagnostics, bool hasMore = false, int selectedCategoryTotal = -1, bool appendPage = false);
    void setCategoryCounts(const QHash<QString, int> &counts);
    void selectDiagnostic(const ProseDiagnostic &diagnostic);
    void setEngineReady(bool ready);
    void setEngineMessage(const QString &message);
    void setLockedFacts(const QStringList &facts);
    void setUndoAvailable(bool available);
    void setCollapseIcon(const QIcon &icon);

signals:
    void modeChanged(Mode mode);
    void profileChanged(const QString &profile);
    void reviewRequested(const QString &scope);
    void exportMarkdownRequested();
    void exportJsonRequested();
    void categoryEnabledChanged(const QString &category, bool enabled);
    void categoryColorChanged(const QString &category, const QColor &color);
    void categoryDecorationChanged(const QString &category, const QString &decoration);
    void categoryModeChanged(const QString &category, const QString &mode);
    void categorySelected(const QString &category);
    void moreFindingsRequested(const QString &category);
    void findingActivated(const ProseDiagnostic &diagnostic);
    void findingPreviewRequested(const ProseDiagnostic &diagnostic);
    void dismissRequested(const ProseDiagnostic &diagnostic);
    void ignoreRequested(const ProseDiagnostic &diagnostic);
    void allowPhraseRequested(const ProseDiagnostic &diagnostic);
    void disableRuleRequested(const ProseDiagnostic &diagnostic);
    void explainRequested(const ProseDiagnostic &diagnostic);
    void revisionRequested(const ProseDiagnostic &diagnostic);
    void deleteRequested(const ProseDiagnostic &diagnostic);
    void deleteAllRequested(const QString &category, const QString &label);
    void undoRequested();
    void rewriteSelectionRequested();
    void lockedFactsChanged(const QStringList &facts);
    void modelSettingsRequested();
    void grammarSettingsRequested();
    void performanceSettingsRequested();
    void editProfileRequested();
    void importProfileRequested();
    void exportProfileRequested();
    void collapseRequested();

private:
    void addCategory(const QString &id, const QString &label, const QString &description, const QColor &color, bool live, QTreeWidgetItem *parent = nullptr);
    void applyCategoryColor(QTreeWidgetItem *item, const QColor &color);
    void chooseCategoryColor(QTreeWidgetItem *item);
    void updateCategoryToolTip(QTreeWidgetItem *item);
    void selectFirstPopulatedCategory();
    int categoryCount(const QString &category) const;
    void updateScanState();
    void rebuildFindings();
    void activateFinding(const QModelIndex &index);
    void activateAdjacentFinding(int direction);
    ProseDiagnostic selectedDiagnostic() const;
    void updateDetails();

    QComboBox *m_modeCombo;
    QComboBox *m_profileCombo;
    QComboBox *m_scopeCombo;
    QPushButton *m_scanButton;
    QPushButton *m_exportMarkdownButton;
    QPushButton *m_exportJsonButton;
    QLabel *m_statusLabel;
    QPlainTextEdit *m_lockedFacts;
    QTreeWidget *m_categoryTree;
    QListView *m_findingView;
    ProseFindingListModel *m_findingModel;
    QLabel *m_findingsHint;
    QLabel *m_explanationLabel;
    QLabel *m_suggestionLabel;
    QPushButton *m_dismissButton;
    QPushButton *m_ignoreButton;
    QPushButton *m_allowButton;
    QPushButton *m_disableRuleButton;
    QPushButton *m_explainButton;
    QPushButton *m_reviseButton;
    QToolButton *m_deleteButton;
    QToolButton *m_backButton;
    QToolButton *m_nextButton;
    QToolButton *m_undoButton;
    QPushButton *m_rewriteSelectionButton;
    QPushButton *m_modelSettingsButton;
    QPushButton *m_grammarSettingsButton;
    QPushButton *m_profileEditButton;
    QPushButton *m_profileImportButton;
    QPushButton *m_profileExportButton;
    QToolButton *m_toolsButton;
    QToolButton *m_collapseButton;
    QToolButton *m_findingActionsButton;
    QWidget *m_findingActionsSurface;
    QList<ProseDiagnostic> m_diagnostics;
    QHash<QString, QColor> m_categoryColors;
    QHash<QString, int> m_categoryCounts;
    int m_visibleFindingCount{250};
    bool m_hasMoreFindings{false};
    int m_pendingNextRow{-1};
    int m_selectedCategoryTotal{-1};
    bool m_engineReady{false};
    bool m_scanRunning{false};
    bool m_reviewRunning{false};
};
}

#endif
