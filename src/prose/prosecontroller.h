/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef PROSE_CONTROLLER_H
#define PROSE_CONTROLLER_H

#include <QColor>
#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QObject>
#include <QQueue>
#include <QSet>
#include <QStringList>
#include <QTextLayout>
#include <QTimer>
#include <QVector>

#include "proseawarenesswidget.h"
#include "prosediagnostic.h"

class QDir;

namespace ghostwriter
{
class MarkdownEditor;
class CredentialStore;
class WriterEngineClient;

struct SnapshotFindingPageState {
    QString analysisId;
    QString category;
    QString expectedCursor;
    int revision = -1;
    int loadedCount = 0;
    bool active = false;

    void begin(const QString &newAnalysisId, const QString &newCategory, int newRevision)
    {
        analysisId = newAnalysisId;
        category = newCategory;
        expectedCursor.clear();
        revision = newRevision;
        loadedCount = 0;
        active = !analysisId.isEmpty() && !category.isEmpty();
    }

    void reset()
    {
        analysisId.clear();
        category.clear();
        expectedCursor.clear();
        revision = -1;
        loadedCount = 0;
        active = false;
    }

    bool accepts(const QString &requestCategory, int requestRevision, const QString &requestCursor, const QJsonObject &result) const
    {
        return active && requestRevision == revision && requestCategory == category && requestCursor == expectedCursor
            && result.value(QStringLiteral("analysis_id")).toString() == analysisId;
    }

    bool advance(const QJsonObject &result)
    {
        loadedCount += result.value(QStringLiteral("diagnostics")).toArray().size();
        const QString nextCursor = result.value(QStringLiteral("next_cursor")).toString();
        const bool hasMore = result.value(QStringLiteral("has_more")).toBool() && !nextCursor.isEmpty();
        expectedCursor = hasMore ? nextCursor : QString();
        active = hasMore;
        return hasMore;
    }
};

struct ReportOverlaySuppressionState {
    QSet<QString> exactSpans;
    QSet<QString> disabledRules;

    static QString spanKey(const QString &category, const QString &ruleId, int start, int end)
    {
        return category + QChar(0x1f) + ruleId + QChar(0x1f) + QString::number(start) + QChar(0x1f) + QString::number(end);
    }

    bool suppresses(const QString &category, const QString &ruleId, int start, int end) const
    {
        return disabledRules.contains(ruleId) || exactSpans.contains(spanKey(category, ruleId, start, end));
    }
};

struct AgentProseSnapshotContract {
    static bool snapshotAvailable(const QString &analysisId)
    {
        return !analysisId.isEmpty();
    }

    static bool categoryHydrated(bool snapshotAvailable, bool categoryKnown, bool categoryLoaded)
    {
        return snapshotAvailable && categoryKnown && categoryLoaded;
    }

    static bool categoryCanHydrate(bool snapshotAvailable, bool categoryKnown)
    {
        return snapshotAvailable && categoryKnown;
    }
};

class ProseController : public QObject
{
    Q_OBJECT

public:
    ProseController(MarkdownEditor *editor, ProseAwarenessWidget *widget, QObject *parent = nullptr);

    void start();
    void reviewDocument();
    void reviewSelection();
    void reviewFolder();

    /**
     * Story Intelligence uses this local-only review path instead of the
     * interactive Grammar Settings path. It always uses the bundled/local
     * automatic grammar configuration, never silently escalating an agent
     * tool call into a cloud grammar request.
     */
    void reviewDocumentForAgent()
    {
        requestAnalysis(true, true, true, automaticGrammarSettings(), false);
    }

    /**
     * Read-only progress adapters for Story Intelligence. A model never sees
     * these members directly; the native harness turns them into bounded tool
     * completion facts. A new full-document snapshot gets a fresh analysis ID.
     */
    quint64 analysisGenerationSnapshot() const { return m_analysisPrepGeneration; }
    QString analysisIdSnapshot() const { return m_analysisId; }
    int revisionSnapshotForAgent() const { return m_revision; }
    bool hasAnalysisSnapshotForAgent() const { return AgentProseSnapshotContract::snapshotAvailable(m_analysisId); }

    bool categoryKnownForAgent(const QString &category) const
    {
        return !analyzersForCategory(category).isEmpty();
    }

    bool categoryHydratedForAgent(const QString &category) const
    {
        return AgentProseSnapshotContract::categoryHydrated(
            hasAnalysisSnapshotForAgent(),
            categoryKnownForAgent(category),
            m_snapshotLoadedCategories.contains(category));
    }

    bool hydrateCategoryForAgent(const QString &category)
    {
        if (!AgentProseSnapshotContract::categoryCanHydrate(
                hasAnalysisSnapshotForAgent(), categoryKnownForAgent(category))) {
            return false;
        }
        if (!m_snapshotLoadedCategories.contains(category)) {
            prioritizeSnapshotCategory(category);
        }
        return true;
    }

signals:
    /**
     * Emitted whenever an analysis envelope carries the dialogue balance
     * block: span count and whole-percent dialogue word ratio.
     */
    void dialogueStatsChanged(int spanCount, int ratioPercent);

private slots:
    void documentChanged(int position, int removed, int added);
    void runLiveAnalysis();
    void runIdleAnalysis();
    void handleResponse(const QString &requestId, const QJsonObject &response);
    void setMode(ProseAwarenessWidget::Mode mode);
    void previewFinding(const ProseDiagnostic &diagnostic);
    void navigateTo(const ProseDiagnostic &diagnostic);
    void navigateNext();
    void navigatePrevious();

private:
    struct RequestContext {
        QString operation;
        int revision = 0;
        int rangeStart = 0;
        int rangeEnd = 0;
        QString sourceText;
        QString textHash;
        int sequence = 0;
    };

    struct PendingRevision {
        ProseDiagnostic diagnostic;
        QJsonObject provider;
        QJsonObject profile;
        QJsonObject folderOverrides;
        QString profileName;
        QString credentialId;
        QString sourceText;
        QString sourceHash;
        QString profileRequestId;
        QJsonArray preserve;
        int passes = 1;
        int revision = -1;
    };

    struct PendingGrammarReview {
        QString scope;
        QJsonObject grammar;
        QString credentialId;
        int selectionStart = 0;
        int selectionEnd = 0;
        int revision = -1;
    };

    struct PendingDocumentPatch {
        int position = 0;
        int removed = 0;
        QString replacement;
        int baseRevision = 0;
        int revision = 0;
    };

    struct AnalysisPrep {
        QString textHash;
        QJsonArray exclusions;
    };

    void requestAnalysis(bool fullDocument, bool confirmAdverbs, bool explicitReport = false, const QJsonObject &grammar = {}, bool grammarConsent = false);
    void retryQueuedAnalysis();
    void synchronizeDocument();
    void patchDocument(int position, int removed, int added, int baseRevision);
    void flushPendingDocumentPatches();
    void disposeSynchronizedDocument(const QString &documentId);
    void requestSelectionAnalysis(int start, int end, const QJsonObject &grammar, bool grammarConsent);
    void sendFolderAnalysis(const QJsonObject &grammar, bool grammarConsent);
    void beginGrammarReview(const QString &scope, int selectionStart = 0, int selectionEnd = 0);
    void sendGrammarReview(const PendingGrammarReview &pending, const QString &apiKey);
    QJsonObject automaticGrammarSettings() const;
    void acceptDiagnostics(const QList<ProseDiagnostic> &diagnostics, const RequestContext &context);
    void applyHighlights(bool updateWidget = true);
    void queryFindings(const QString &category, const QString &cursor = QString());
    void queryOverlaySpans(const QString &cursor = QString());
    void restartOverlayHydration();
    int overlayViewportAnchor() const;
    QVector<int> viewportOrderedSpans(const QJsonObject &page) const;
    void diffOverlaySnapshot();
    void adjustCachedOverlayFormats(int position, int removed, int added);
    void recordOverlaySuppressions(const QList<ProseDiagnostic> &diagnostics);
    void applyOverlayPage(const QJsonObject &result);
    void processOverlayBatch();
    void beginSnapshotLoading();
    void loadNextSnapshotCategory();
    void prioritizeSnapshotCategory(const QString &category);
    void resetSnapshotLoading();
    void refreshSelectedFindings(bool hasMore = false, bool appendPage = false);
    QStringList analyzersForCategory(const QString &category) const;
    QHash<QString, int> categoryCounts(const QJsonObject &countsByAnalyzer) const;
    void clearObservations();
    void reportReviewNotStarted(const QString &message);
    void openModelSettings();
    void openGrammarSettings();
    void openPerformanceSettings();
    void importProfile();
    void exportProfile();
    void exportMarkdownReport();
    void exportJsonReport();
    void requestRevision(const ProseDiagnostic &diagnostic);
    void deleteDiagnostic(const ProseDiagnostic &diagnostic);
    void deleteCategory(const QString &category, const QString &label);
    void deleteDiagnostics(const QList<ProseDiagnostic> &diagnostics);
    void confirmPendingRevision(const QJsonObject &profile);
    void sendRevision(const PendingRevision &pending, const QString &apiKey);
    void ignoreOccurrence(const ProseDiagnostic &diagnostic);
    void allowPhrase(const ProseDiagnostic &diagnostic);
    QString normalizedPhrase(const QString &text) const;
    QString occurrenceIgnoreKey(const ProseDiagnostic &diagnostic) const;
    QString profileSettingsPrefix() const;
    QString profileSettingsPrefix(const QString &profile) const;
    void loadLensSettings();
    void loadLockedFacts();
    void requestProfilePresentation();
    void scheduleLensProfileSave();
    QJsonObject lensProfileSettings() const;
    void applyLensProfileSettings(const QString &profile, const QJsonObject &lenses);
    bool isPersistentlyIgnored(const ProseDiagnostic &diagnostic) const;
    bool isAllowedPhrase(const ProseDiagnostic &diagnostic) const;
    QJsonObject providerSettings() const;
    QString providerCredentialId(const QJsonObject &provider) const;
    QList<QJsonObject> exclusionRanges(const QString &text, int baseOffset) const;
    QJsonObject analysisOverrides(const QJsonObject &baseOverrides) const;
    QJsonObject folderProfileOverrides(const QDir &directory) const;
    QJsonObject currentFolderProfileOverrides() const;
    QString lensCategory(const QString &analyzer) const;
    QColor categoryColor(const QString &category) const;
    bool categoryEnabled(const QString &category) const;
    bool diagnosticVisible(const ProseDiagnostic &diagnostic) const;

    MarkdownEditor *m_editor;
    ProseAwarenessWidget *m_widget;
    WriterEngineClient *m_engine;
    CredentialStore *m_credentials;
    QTimer m_liveTimer;
    QTimer m_idleTimer;
    QTimer m_profileSaveTimer;
    QTimer m_documentSyncTimer;
    QTimer m_overlayApplyTimer;
    QTimer m_patchFlushTimer;
    QString m_documentId;
    QString m_liveRequestId;
    QString m_backgroundRequestId;
    QString m_analysisId;
    QString m_overlayCursor;
    QString m_findingRequestId;
    QString m_overlayRequestId;
    QString m_documentPatchRequestId;
    int m_revision = 0;
    int m_analysisSequence = 0;
    quint64 m_analysisPrepGeneration = 0;
    int m_lastAppliedSequence = 0;
    int m_changedPosition = 0;
    QList<ProseDiagnostic> m_diagnostics;
    QHash<QString, RequestContext> m_requests;
    QHash<QString, QColor> m_colors;
    QHash<QString, QString> m_decorations;
    QHash<QString, QString> m_categoryModes;
    QHash<QString, int> m_categoryPriorities;
    QSet<QString> m_enabledCategories;
    QSet<QString> m_dismissedIds;
    QSet<QString> m_disabledRules;
    QSet<QString> m_allowedPhrases;
    QSet<QString> m_ignoredOccurrences;
    PendingRevision m_pendingRevision;
    PendingGrammarReview m_pendingGrammarReview;
    QJsonObject m_activeProfile;
    QJsonObject m_baseLenses;
    QJsonObject m_lastReport;
    QHash<QString, int> m_snapshotCategoryCounts;
    QHash<int, QList<QTextLayout::FormatRange>> m_snapshotOverlayFormats;
    QQueue<QJsonObject> m_overlayPages;
    QJsonObject m_currentOverlayPage;
    QVector<int> m_currentOverlaySpanOrder;
    int m_currentOverlayIndex = 0;
    int m_overlayBudgetMs = 4;
    bool m_overlayRequestInFlight = false;
    bool m_overlayHasMore = false;
    int m_overlayHydrationGeneration = 0;
    QHash<int, QList<QTextLayout::FormatRange>> m_overlayBaselineFormats;
    QVector<int> m_pendingOverlayUpdates;
    bool m_overlayDiffPending = false;
    int m_overlayViewportAnchor = 0;
    ReportOverlaySuppressionState m_overlaySuppressions;
    QStringList m_snapshotCategoryQueue;
    QSet<QString> m_snapshotLoadedCategories;
    SnapshotFindingPageState m_snapshotFindingPages;
    QString m_snapshotDisplayLane = QStringLiteral("report");
    QString m_displayLane = QStringLiteral("live");
    bool m_lensPresentationDirty = false;
    bool m_documentSynchronized = false;
    bool m_documentExclusionsStale = false;
    bool m_analysisRetryPending = false;
    bool m_analysisRetryExplicit = false;
    QJsonObject m_analysisRetryGrammar;
    bool m_analysisRetryGrammarConsent = false;
    quint64 m_documentSyncSequence = 0;
    QList<PendingDocumentPatch> m_documentPatches;
};
}

#endif
