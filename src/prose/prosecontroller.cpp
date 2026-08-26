/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#include "prosecontroller.h"
#include "../editor/markdowndocument.h"
#include "../editor/markdowneditor.h"
#include "../editor/textformatoverlaycontroller.h"
#include "../messageboxhelper.h"
#include "credentialstore.h"
#include "grammarsettingsdialog.h"
#include "performancepolicy.h"
#include "performancesettingsdialog.h"
#include "profileeditordialog.h"
#include "proseoverlayformats.h"
#include "providersettingsdialog.h"
#include "rewritereviewdialog.h"
#include "writerengineclient.h"
#include <QCryptographicHash>
#include <QDir>
#include <QDirIterator>
#include <QElapsedTimer>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFutureWatcher>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMessageBox>
#include <QRegularExpression>
#include <QSaveFile>
#include <QSettings>
#include <QShortcut>
#include <QTextBlock>
#include <QTextCursor>
#include <QTextDocument>
#include <QTextLayout>
#include <QUrl>
#include <QUuid>
#include <QtConcurrentRun>
#include <Sonnet/GuessLanguage>
#include <algorithm>
#include <limits>
#include <utility>
#ifdef THOTHPAD_INSTRUMENTATION
#include "proseinstrumentation.h"
#endif
namespace ghostwriter
{
namespace
{
const QString ProseChannel = QStringLiteral("prose");
constexpr int LiveMaximumCharacters = 8000;
constexpr qsizetype FolderMaximumJsonBytes = 12 * 1024 * 1024;
struct PreparedDocumentSnapshot {
    QString text;
    QString textHash;
    QString language;
    QJsonArray exclusions;
};
QString detectedLanguage(const QString &text)
{
    const QString language = Sonnet::GuessLanguage().identify(text.left(8000));
    return language.isEmpty() ? QStringLiteral("und") : language;
}
QList<QJsonObject> exclusionRangesForSettings(const QString &text, int baseOffset, const QJsonObject &markdownSettings)
{
    QList<QJsonObject> ranges;
    const auto enabled = [&markdownSettings](const QString &name) {
        const QJsonValue value = markdownSettings.value(name);
        return !value.isBool() || value.toBool();
    };
    const auto appendMatches = [&ranges, &text, baseOffset](const QRegularExpression &expression, const QString &kind, int captured = 0) {
        auto iterator = expression.globalMatch(text);
        while (iterator.hasNext()) {
            const auto match = iterator.next();
            QJsonObject range;
            range.insert(QStringLiteral("start_utf16"), baseOffset + match.capturedStart(captured));
            range.insert(QStringLiteral("end_utf16"), baseOffset + match.capturedEnd(captured));
            range.insert(QStringLiteral("kind"), kind);
            ranges.append(range);
        }
    };
    if (enabled(QStringLiteral("fenced_code"))) {
        static const QRegularExpression fencedCodePattern(QStringLiteral(R"((?ms)^(?:```|~~~).*?^(?:```|~~~)[ \t]*$)"));
        appendMatches(fencedCodePattern, QStringLiteral("fenced_code"));
    }
    if (enabled(QStringLiteral("inline_code"))) {
        static const QRegularExpression inlineCodePattern(QStringLiteral(R"(`+[^`\r\n]+`+)"));
        appendMatches(inlineCodePattern, QStringLiteral("inline_code"));
    }
    if (enabled(QStringLiteral("link_destinations"))) {
        static const QRegularExpression linkDestinationPattern(QStringLiteral(R"(\]\(([^)\s]+)(?:\s+[^)]*)?\))"));
        appendMatches(linkDestinationPattern, QStringLiteral("link_destination"), 1);
    }
    if (enabled(QStringLiteral("urls"))) {
        static const QRegularExpression urlPattern(QStringLiteral(R"(https?://[^\s)>]+)"), QRegularExpression::CaseInsensitiveOption);
        appendMatches(urlPattern, QStringLiteral("url"));
    }
    if (enabled(QStringLiteral("html_markup"))) {
        static const QRegularExpression htmlPattern(QStringLiteral(R"(<[^>\r\n]+>)"));
        appendMatches(htmlPattern, QStringLiteral("html"));
    }
    if (enabled(QStringLiteral("markdown_delimiters"))) {
        static const QRegularExpression blockDelimiterPattern(QStringLiteral(R"((?m)^[ \t]{0,3}(?:#{1,6}[ \t]+|>[ \t]?|(?:[-+*]|\d+[.)])[ \t]+))"));
        appendMatches(blockDelimiterPattern, QStringLiteral("markdown_delimiter"));
        static const QRegularExpression emphasisDelimiterPattern(QStringLiteral(R"((?<!\\)(?:\*\*|__|~~|\*|_))"));
        appendMatches(emphasisDelimiterPattern, QStringLiteral("markdown_delimiter"));
    }
    if (baseOffset == 0 && enabled(QStringLiteral("yaml_front_matter"))) {
        static const QRegularExpression frontMatterPattern(QStringLiteral(R"((?ms)\A---[ \t]*\r?\n.*?^---[ \t]*$)"));
        appendMatches(frontMatterPattern, QStringLiteral("front_matter"));
    }
    return ranges;
}
}
ProseController::ProseController(MarkdownEditor *editor, ProseAwarenessWidget *widget, QObject *parent)
    : QObject(parent)
    , m_editor(editor)
    , m_widget(widget)
    , m_engine(new WriterEngineClient(this))
    , m_credentials(new CredentialStore(this))
    , m_documentId(QUuid::createUuid().toString(QUuid::WithoutBraces))
{
    const PerformancePolicy performance = PerformancePolicy::load();
    m_liveTimer.setSingleShot(true);
    m_liveTimer.setInterval(250);
    m_idleTimer.setSingleShot(true);
    m_idleTimer.setInterval(performance.analysisDelayMs);
    m_overlayBudgetMs = performance.overlayBudgetMs;
    m_profileSaveTimer.setSingleShot(true);
    m_profileSaveTimer.setInterval(300);
    m_documentSyncTimer.setSingleShot(true);
    m_documentSyncTimer.setInterval(25);
    // Coalesces rapid keystrokes into one patch_document frame so the engine
    // sees batches instead of a per-keystroke IPC train.
    m_patchFlushTimer.setSingleShot(true);
    m_patchFlushTimer.setInterval(25);
    m_overlayApplyTimer.setSingleShot(true);
    m_colors = {
        {QStringLiteral("general_rules"), QColor("#F87171")},
        {QStringLiteral("possible_adverbs"), QColor("#FACC15")},
        {QStringLiteral("possible_adjectives"), QColor("#C084FC")},
        {QStringLiteral("possible_verbs"), QColor("#34D399")},
        {QStringLiteral("filter_words"), QColor("#FB923C")},
        {QStringLiteral("cliches"), QColor("#60A5FA")},
        {QStringLiteral("formulaic_patterns"), QColor("#F472B6")},
        {QStringLiteral("repetition_rhythm"), QColor("#22D3EE")},
        {QStringLiteral("body_cinematic"), QColor("#B66E7D")},
        {QStringLiteral("abstraction_agency"), QColor("#6096A4")},
        {QStringLiteral("metaphor_texture"), QColor("#739764")},
        {QStringLiteral("grammar_mechanics"), QColor("#B8685F")},
        {QStringLiteral("repetition"), QColor("#A76D87")},
    };
    m_enabledCategories = {
        QStringLiteral("general_rules"),
        QStringLiteral("possible_adverbs"),
        QStringLiteral("possible_adjectives"),
        QStringLiteral("possible_verbs"),
        QStringLiteral("filter_words"),
        QStringLiteral("cliches"),
        QStringLiteral("formulaic_patterns"),
        QStringLiteral("repetition_rhythm"),
        QStringLiteral("body_cinematic"),
        QStringLiteral("grammar_mechanics"),
        QStringLiteral("repetition"),
    };
    m_editor->setAccessibleName(tr("Document editor"));
    m_editor->setAccessibleDescription(tr("Editable Markdown document with optional spelling, grammar, and prose observations."));
    loadLensSettings();
    loadLockedFacts();
    QSettings presentation;
    m_widget->setMode(
        static_cast<ProseAwarenessWidget::Mode>(presentation.value(QStringLiteral("prose/mode"), static_cast<int>(ProseAwarenessWidget::Mode::Live)).toInt()));
    connect(m_editor->document(), &QTextDocument::contentsChange, this, &ProseController::documentChanged);
    if (auto *document = qobject_cast<MarkdownDocument *>(m_editor->document())) {
        connect(document, &MarkdownDocument::filePathChanged, this, [this]() {
            disposeSynchronizedDocument(m_documentId);
            if (!m_analysisId.isEmpty()) {
                QJsonObject payload;
                payload.insert(QStringLiteral("analysis_id"), m_analysisId);
                m_engine->send(QStringLiteral("dispose_analysis"), payload);
            }
            ++m_revision;
            m_documentId = QUuid::createUuid().toString(QUuid::WithoutBraces);
            m_documentSynchronized = false;
            m_documentExclusionsStale = false;
            m_documentPatchRequestId.clear();
            m_documentPatches.clear();
            m_lastReport = {};
            m_analysisId.clear();
            m_snapshotCategoryCounts.clear();
            resetSnapshotLoading();
            m_dismissedIds.clear();
            m_pendingRevision = {};
            m_pendingGrammarReview = {};
            loadLensSettings();
            clearObservations();
            m_documentSyncTimer.start();
            if (m_widget->mode() == ProseAwarenessWidget::Mode::Live) {
                m_liveTimer.start();
                m_idleTimer.start();
            }
        });
    }
    connect(&m_liveTimer, &QTimer::timeout, this, &ProseController::runLiveAnalysis);
    connect(&m_idleTimer, &QTimer::timeout, this, &ProseController::runIdleAnalysis);
    connect(&m_profileSaveTimer, &QTimer::timeout, this, [this]() {
        const QString profileName = m_widget->profile().isEmpty() ? QStringLiteral("creative-default") : m_widget->profile();
        if (!m_engine->isReady() || m_activeProfile.value(QStringLiteral("name")).toString() != profileName) {
            return;
        }
        m_activeProfile.insert(QStringLiteral("lenses"), m_baseLenses);
        QJsonObject payload;
        payload.insert(QStringLiteral("name"), profileName);
        payload.insert(QStringLiteral("profile"), m_activeProfile);
        const QString requestId = m_engine->send(QStringLiteral("save_profile"), payload);
        if (!requestId.isEmpty()) {
            m_requests.insert(requestId, {QStringLiteral("save_profile_presentation"), m_revision, 0, 0});
            m_lensPresentationDirty = false;
        }
    });
    connect(&m_documentSyncTimer, &QTimer::timeout, this, &ProseController::synchronizeDocument);
    connect(&m_patchFlushTimer, &QTimer::timeout, this, &ProseController::flushPendingDocumentPatches);
    connect(&m_overlayApplyTimer, &QTimer::timeout, this, &ProseController::processOverlayBatch);
    connect(m_engine, &WriterEngineClient::readyChanged, m_widget, &ProseAwarenessWidget::setEngineReady);
    connect(m_engine, &WriterEngineClient::readyChanged, this, [this](bool ready) {
        if (ready) {
            const QString requestId = m_engine->send(QStringLiteral("list_profiles"));
            if (!requestId.isEmpty()) {
                m_requests.insert(requestId, {QStringLiteral("list_profiles"), m_revision, 0, 0});
            }
            synchronizeDocument();
            if (m_widget->mode() == ProseAwarenessWidget::Mode::Live) {
                m_liveTimer.start();
                m_idleTimer.start();
            }
        }
    });
    connect(m_engine, &WriterEngineClient::responseReceived, this, &ProseController::handleResponse);
    connect(m_engine, &WriterEngineClient::requestsInvalidated, this, [this](const QStringList &) {
        m_requests.clear();
        m_liveRequestId.clear();
        m_backgroundRequestId.clear();
        m_findingRequestId.clear();
        m_overlayRequestId.clear();
        m_documentPatchRequestId.clear();
        m_documentPatches.clear();
        m_documentSynchronized = false;
        m_documentExclusionsStale = false;
        resetSnapshotLoading();
        m_analysisId.clear();
        m_snapshotCategoryCounts.clear();
        m_lastReport = {};
        clearObservations();
        m_widget->setCategoryCounts({});
        m_pendingRevision = {};
        m_pendingGrammarReview = {};
    });
    connect(m_engine, &WriterEngineClient::engineError, m_widget, &ProseAwarenessWidget::setEngineMessage);
    connect(m_widget, &ProseAwarenessWidget::modeChanged, this, &ProseController::setMode);
    connect(m_widget, &ProseAwarenessWidget::reviewRequested, this, [this](const QString &scope) {
        if (scope == QStringLiteral("document")) {
            reviewDocument();
        } else if (scope == QStringLiteral("selection")) {
            reviewSelection();
        } else if (scope == QStringLiteral("folder")) {
            reviewFolder();
        }
    });
    connect(m_widget, &ProseAwarenessWidget::exportMarkdownRequested, this, &ProseController::exportMarkdownReport);
    connect(m_widget, &ProseAwarenessWidget::exportJsonRequested, this, &ProseController::exportJsonReport);
    connect(m_widget, &ProseAwarenessWidget::profileChanged, this, [this]() {
        m_profileSaveTimer.stop();
        m_activeProfile = {};
        m_lensPresentationDirty = false;
        QSettings().setValue(QStringLiteral("prose/profile"), m_widget->profile());
        loadLensSettings();
        loadLockedFacts();
        requestProfilePresentation();
    });
    connect(m_widget, &ProseAwarenessWidget::categoryEnabledChanged, this, [this](const QString &category, bool enabled) {
        if (enabled) {
            m_enabledCategories.insert(category);
        } else {
            m_enabledCategories.remove(category);
        }
        QSettings().setValue(profileSettingsPrefix() + QStringLiteral("/lenses/%1/enabled").arg(category), enabled);
        QJsonObject lens = m_baseLenses.value(category).toObject();
        lens.insert(QStringLiteral("enabled"), enabled);
        m_baseLenses.insert(category, lens);
        applyHighlights();
        const bool analyzerMembershipChanged =
            category == QStringLiteral("possible_adverbs") || category == QStringLiteral("possible_adjectives") || category == QStringLiteral("possible_verbs");
        if (analyzerMembershipChanged && m_widget->mode() != ProseAwarenessWidget::Mode::Off && !m_editor->document()->isEmpty()) {
            requestAnalysis(true, true);
        } else if (enabled && !m_analysisId.isEmpty()) {
            prioritizeSnapshotCategory(category);
        }
        scheduleLensProfileSave();
    });
    connect(m_widget, &ProseAwarenessWidget::categoryColorChanged, this, [this](const QString &category, const QColor &color) {
        m_colors.insert(category, color);
        QSettings().setValue(profileSettingsPrefix() + QStringLiteral("/lenses/%1/color").arg(category), color.name());
        loadLensSettings();
        scheduleLensProfileSave();
    });
    connect(m_widget, &ProseAwarenessWidget::categoryDecorationChanged, this, [this](const QString &category, const QString &decoration) {
        m_decorations.insert(category, decoration);
        QSettings().setValue(profileSettingsPrefix() + QStringLiteral("/lenses/%1/decoration").arg(category), decoration);
        loadLensSettings();
        scheduleLensProfileSave();
    });
    connect(m_widget, &ProseAwarenessWidget::categoryModeChanged, this, [this](const QString &category, const QString &mode) {
        m_categoryModes.insert(category, mode);
        QSettings().setValue(profileSettingsPrefix() + QStringLiteral("/lenses/%1/mode").arg(category), mode);
        m_widget->setCategoryMode(category, mode);
        loadLensSettings();
        scheduleLensProfileSave();
    });
    connect(m_widget, &ProseAwarenessWidget::categorySelected, this, [this](const QString &category) {
        if (!m_analysisId.isEmpty()) {
            prioritizeSnapshotCategory(category);
        }
    });
    connect(m_widget, &ProseAwarenessWidget::moreFindingsRequested, this, [this](const QString &category) {
        if (m_findingRequestId.isEmpty() && m_snapshotFindingPages.active && category == m_snapshotFindingPages.category
            && !m_snapshotFindingPages.expectedCursor.isEmpty()) {
            queryFindings(category, m_snapshotFindingPages.expectedCursor);
        }
    });
    connect(m_widget, &ProseAwarenessWidget::findingActivated, this, &ProseController::navigateTo);
    connect(m_widget, &ProseAwarenessWidget::findingPreviewRequested, this, &ProseController::previewFinding);
    connect(m_widget, &ProseAwarenessWidget::dismissRequested, this, [this](const ProseDiagnostic &diagnostic) {
        m_dismissedIds.insert(diagnostic.id);
        recordOverlaySuppressions({diagnostic});
        applyHighlights();
    });
    connect(m_widget, &ProseAwarenessWidget::ignoreRequested, this, &ProseController::ignoreOccurrence);
    connect(m_widget, &ProseAwarenessWidget::allowPhraseRequested, this, &ProseController::allowPhrase);
    connect(m_widget, &ProseAwarenessWidget::disableRuleRequested, this, [this](const ProseDiagnostic &diagnostic) {
        if (diagnostic.ruleId.isEmpty()) {
            return;
        }
        QStringList rules = QSettings().value(profileSettingsPrefix() + QStringLiteral("/disabledRules")).toStringList();
        if (!rules.contains(diagnostic.ruleId)) {
            rules.append(diagnostic.ruleId);
            QSettings().setValue(profileSettingsPrefix() + QStringLiteral("/disabledRules"), rules);
            m_disabledRules.insert(diagnostic.ruleId);
        }
        m_overlaySuppressions.disabledRules = m_disabledRules;
        applyHighlights();
    });
    connect(m_widget, &ProseAwarenessWidget::modelSettingsRequested, this, &ProseController::openModelSettings);
    connect(m_widget, &ProseAwarenessWidget::grammarSettingsRequested, this, &ProseController::openGrammarSettings);
    connect(m_widget, &ProseAwarenessWidget::performanceSettingsRequested, this, &ProseController::openPerformanceSettings);
    connect(m_widget, &ProseAwarenessWidget::deleteRequested, this, &ProseController::deleteDiagnostic);
    connect(m_widget, &ProseAwarenessWidget::deleteAllRequested, this, &ProseController::deleteCategory);
    connect(m_widget, &ProseAwarenessWidget::undoRequested, m_editor, &MarkdownEditor::undo);
    connect(m_editor->document(), &QTextDocument::undoAvailable, m_widget, &ProseAwarenessWidget::setUndoAvailable);
    m_widget->setUndoAvailable(m_editor->document()->isUndoAvailable());
    connect(m_widget, &ProseAwarenessWidget::editProfileRequested, this, [this]() {
        QJsonObject payload;
        payload.insert(QStringLiteral("name"), m_widget->profile());
        const QString requestId = m_engine->send(QStringLiteral("get_profile"), payload);
        if (!requestId.isEmpty()) {
            m_requests.insert(requestId, {QStringLiteral("get_profile"), m_revision, 0, 0});
        }
    });
    connect(m_widget, &ProseAwarenessWidget::importProfileRequested, this, &ProseController::importProfile);
    connect(m_widget, &ProseAwarenessWidget::exportProfileRequested, this, &ProseController::exportProfile);
    connect(m_widget, &ProseAwarenessWidget::revisionRequested, this, &ProseController::requestRevision);
    connect(m_widget, &ProseAwarenessWidget::lockedFactsChanged, this, [this](const QStringList &facts) {
        QSettings().setValue(profileSettingsPrefix() + QStringLiteral("/lockedFacts"), facts);
    });
    connect(m_widget, &ProseAwarenessWidget::rewriteSelectionRequested, this, [this]() {
        const QTextCursor cursor = m_editor->textCursor();
        if (!cursor.hasSelection()) {
            MessageBoxHelper::information(m_widget, tr("No selection"), tr("Select a passage before requesting a rewrite."));
            return;
        }
        ProseDiagnostic diagnostic;
        diagnostic.id = QUuid::createUuid().toString(QUuid::WithoutBraces);
        diagnostic.ruleId = QStringLiteral("selection.rewrite");
        diagnostic.start = cursor.selectionStart();
        diagnostic.end = cursor.selectionEnd();
        diagnostic.excerpt = cursor.selectedText();
        requestRevision(diagnostic);
    });
    connect(m_widget, &ProseAwarenessWidget::explainRequested, this, [this](const ProseDiagnostic &diagnostic) {
        MessageBoxHelper::information(m_widget, tr("Observation"), diagnostic.explanation + QStringLiteral("\n\n") + diagnostic.suggestion);
    });
    connect(m_credentials, &CredentialStore::loaded, this, [this](const QString &credentialId, const QString &secret) {
        if (!m_pendingRevision.sourceText.isEmpty() && credentialId == m_pendingRevision.credentialId) {
            const PendingRevision pending = m_pendingRevision;
            m_pendingRevision = {};
            sendRevision(pending, secret);
        } else if (!m_pendingGrammarReview.scope.isEmpty() && credentialId == m_pendingGrammarReview.credentialId) {
            const PendingGrammarReview pending = m_pendingGrammarReview;
            m_pendingGrammarReview = {};
            sendGrammarReview(pending, secret);
        }
    });
    connect(m_credentials, &CredentialStore::error, this, [this](const QString &credentialId, const QString &message) {
        if (!m_pendingRevision.sourceText.isEmpty() && credentialId == m_pendingRevision.credentialId) {
            m_pendingRevision = {};
            MessageBoxHelper::warning(m_widget, tr("Model unavailable"), message);
        } else if (!m_pendingGrammarReview.scope.isEmpty() && credentialId == m_pendingGrammarReview.credentialId) {
            m_pendingGrammarReview = {};
            MessageBoxHelper::warning(m_widget, tr("Grammar provider unavailable"), message);
            reportReviewNotStarted(tr("Grammar provider unavailable."));
        }
    });
    auto *nextShortcut = new QShortcut(QKeySequence(Qt::Key_F8), m_editor);
    auto *previousShortcut = new QShortcut(QKeySequence(Qt::SHIFT | Qt::Key_F8), m_editor);
    connect(nextShortcut, &QShortcut::activated, this, &ProseController::navigateNext);
    connect(previousShortcut, &QShortcut::activated, this, &ProseController::navigatePrevious);
}
void ProseController::start()
{
    m_engine->start();
}
void ProseController::documentChanged(int position, int removed, int added)
{
    const int baseRevision = m_revision;
    ++m_revision;
    patchDocument(position, removed, added, baseRevision);
    m_changedPosition = position;
    m_lastReport = {};
    // TextFormatOverlayController evicted every block touching this edit;
    // reapply the adjusted cached snapshot formats immediately so painted
    // highlights survive until the next snapshot deltas them.
    adjustCachedOverlayFormats(position, removed, added);
    const bool hadSnapshot = !m_analysisId.isEmpty();
    if (!m_backgroundRequestId.isEmpty()) {
        m_engine->cancel(m_backgroundRequestId);
        m_requests.remove(m_backgroundRequestId);
        m_backgroundRequestId.clear();
    }
    if (hadSnapshot) {
        QJsonObject payload;
        payload.insert(QStringLiteral("analysis_id"), m_analysisId);
        m_engine->send(QStringLiteral("dispose_analysis"), payload);
        m_analysisId.clear();
        m_snapshotCategoryCounts.clear();
        resetSnapshotLoading();
    }
    if (!m_pendingRevision.sourceText.isEmpty()) {
        m_pendingRevision = {};
    }
    if (!m_pendingGrammarReview.scope.isEmpty()) {
        m_pendingGrammarReview = {};
    }
    if (m_widget->mode() != ProseAwarenessWidget::Mode::Live) {
        clearObservations();
        return;
    }
    if (hadSnapshot) {
        m_diagnostics.clear();
        m_widget->setDiagnostics({});
        m_widget->setCategoryCounts({});
    } else {
        m_diagnostics = adjustedDiagnosticsAfterEdit(m_diagnostics, position, removed, added);
    }
    m_liveTimer.start();
    m_idleTimer.start();
}
void ProseController::synchronizeDocument()
{
    if (!m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("open_document"))) {
        return;
    }
    if (!m_documentPatchRequestId.isEmpty() || !m_documentPatches.isEmpty()) {
        flushPendingDocumentPatches();
        if (!m_documentPatchRequestId.isEmpty() || !m_documentPatches.isEmpty()) {
            m_documentSyncTimer.start();
            return;
        }
    }
    const int revision = m_revision;
    const QString documentId = m_documentId;
    const quint64 sequence = ++m_documentSyncSequence;
    QJsonObject markdownSettings = m_activeProfile.value(QStringLiteral("markdown_exclusions")).toObject();
    const QJsonObject folderSettings = currentFolderProfileOverrides().value(QStringLiteral("markdown_exclusions")).toObject();
    for (auto item = folderSettings.constBegin(); item != folderSettings.constEnd(); ++item) {
        markdownSettings.insert(item.key(), item.value());
    }
    QFuture<QString> textFuture = m_editor->textSnapshot();
    auto *watcher = new QFutureWatcher<PreparedDocumentSnapshot>(this);
    connect(watcher, &QFutureWatcher<PreparedDocumentSnapshot>::finished, this, [this, watcher, revision, documentId, sequence]() {
        PreparedDocumentSnapshot snapshot = watcher->result();
        watcher->deleteLater();
        if (sequence != m_documentSyncSequence || revision != m_revision || documentId != m_documentId || !m_engine->isReady()) {
            return;
        }
        // Sonnet is not thread-safe: language detection must stay on the
        // GUI thread alongside the spellchecker's Speller usage.
        snapshot.language = detectedLanguage(snapshot.text);
        QJsonObject payload;
        payload.insert(QStringLiteral("document_id"), documentId);
        payload.insert(QStringLiteral("document_revision"), revision);
        payload.insert(QStringLiteral("text"), snapshot.text);
        payload.insert(QStringLiteral("language"), snapshot.language);
        payload.insert(QStringLiteral("hash"), snapshot.textHash);
        payload.insert(QStringLiteral("exclusion_ranges"), snapshot.exclusions);
        const QString requestId = m_engine->send(QStringLiteral("open_document"), payload);
        if (!requestId.isEmpty()) {
#ifdef THOTHPAD_INSTRUMENTATION
            ProseInstrumentation::instance()->recordResyncEvent(snapshot.text.toUtf8().size());
#endif
            m_documentSynchronized = false;
            m_requests.insert(requestId, {QStringLiteral("open_document"), revision, 0, static_cast<int>(snapshot.text.size()), QString(), snapshot.textHash});
        }
    });
    watcher->setFuture(QtConcurrent::run([textFuture, markdownSettings]() mutable {
        textFuture.waitForFinished();
        PreparedDocumentSnapshot snapshot;
        snapshot.text = textFuture.result();
        snapshot.textHash = QString::fromLatin1(QCryptographicHash::hash(snapshot.text.toUtf8(), QCryptographicHash::Sha256).toHex());
        for (const QJsonObject &range : exclusionRangesForSettings(snapshot.text, 0, markdownSettings)) {
            snapshot.exclusions.append(range);
        }
        return snapshot;
    }));
}
void ProseController::patchDocument(int position, int removed, int added, int baseRevision)
{
    if (!m_documentSynchronized || !m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("patch_document"))) {
        m_documentSyncTimer.start();
        return;
    }
    QTextCursor cursor(m_editor->document());
    cursor.setPosition(position);
    cursor.setPosition(position + added, QTextCursor::KeepAnchor);
    QString replacement = cursor.selectedText();
    replacement.replace(QChar::ParagraphSeparator, QLatin1Char('\n'));
    replacement.replace(QChar::LineSeparator, QLatin1Char('\n'));
    m_documentPatches.append({position, removed, replacement, baseRevision, m_revision});
    m_patchFlushTimer.start();
}
void ProseController::flushPendingDocumentPatches()
{
    if (!m_documentPatchRequestId.isEmpty() || m_documentPatches.isEmpty() || !m_documentSynchronized || !m_engine->isReady()) {
        return;
    }
    // All change offsets in one frame must address the same base state (the
    // engine resolves them against its current buffer before applying), so
    // rebase each queued edit into the first edit's coordinate system.
    QJsonArray changes;
    int cumulativeDelta = 0;
    for (const PendingDocumentPatch &patch : m_documentPatches) {
        const int rebasedStart = patch.position + cumulativeDelta;
        if (rebasedStart < 0) {
            m_documentSynchronized = false;
            m_documentPatches.clear();
            m_documentSyncTimer.start();
            return;
        }
        QJsonObject change;
        change.insert(QStringLiteral("start_utf16"), rebasedStart);
        change.insert(QStringLiteral("end_utf16"), rebasedStart + patch.removed);
        change.insert(QStringLiteral("replacement"), patch.replacement);
        changes.append(change);
        cumulativeDelta += patch.replacement.size() - patch.removed;
    }
    const PendingDocumentPatch &first = m_documentPatches.constFirst();
    const PendingDocumentPatch &last = m_documentPatches.constLast();
    QJsonObject payload;
    payload.insert(QStringLiteral("document_id"), m_documentId);
    payload.insert(QStringLiteral("document_revision"), last.revision);
    payload.insert(QStringLiteral("base_revision"), first.baseRevision);
    payload.insert(QStringLiteral("changes"), changes);
#ifdef THOTHPAD_INSTRUMENTATION
    qint64 frameBytes = 0;
    for (const PendingDocumentPatch &patch : m_documentPatches) {
        frameBytes += patch.replacement.toUtf8().size();
    }
    ProseInstrumentation::instance()->recordPatchFrame(static_cast<int>(frameBytes));
#endif
    const QString requestId = m_engine->send(QStringLiteral("patch_document"), payload);
    if (!requestId.isEmpty()) {
        m_documentPatchRequestId = requestId;
        m_requests.insert(
            requestId,
            {QStringLiteral("patch_document"), last.revision, first.position, last.position + static_cast<int>(last.replacement.size()), last.replacement});
    } else {
        m_documentSynchronized = false;
        m_documentPatches.clear();
        m_documentSyncTimer.start();
    }
}
void ProseController::disposeSynchronizedDocument(const QString &documentId)
{
    if (!m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("dispose_document")) || documentId.isEmpty()) {
        return;
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("document_id"), documentId);
    m_engine->send(QStringLiteral("dispose_document"), payload);
}
void ProseController::runLiveAnalysis()
{
    requestAnalysis(false, false);
}
void ProseController::runIdleAnalysis()
{
    // The report runs out of process after typing settles. A subsequent edit
    // cancels it immediately; no full-document work occurs in the keypress path.
    if (m_backgroundRequestId.isEmpty() && m_analysisId.isEmpty()) {
        requestAnalysis(true, true);
    }
}
void ProseController::reviewDocument()
{
    beginGrammarReview(QStringLiteral("document"));
}
void ProseController::reviewSelection()
{
    const QTextCursor cursor = m_editor->textCursor();
    if (!cursor.hasSelection()) {
        MessageBoxHelper::information(m_widget, tr("No selection"), tr("Select a passage before reviewing the current selection."));
        reportReviewNotStarted(tr("No selection to review."));
        return;
    }
    beginGrammarReview(QStringLiteral("selection"), cursor.selectionStart(), cursor.selectionEnd());
}
void ProseController::requestSelectionAnalysis(int start, int end, const QJsonObject &grammar, bool grammarConsent)
{
    if (!m_engine->isReady() || end <= start) {
        reportReviewNotStarted(tr("Selection review could not be started."));
        return;
    }
    QTextCursor rangeCursor(m_editor->document());
    rangeCursor.setPosition(start);
    rangeCursor.setPosition(end, QTextCursor::KeepAnchor);
    QString text = rangeCursor.selectedText();
    text.replace(QChar::ParagraphSeparator, QLatin1Char('\n'));
    text.replace(QChar::LineSeparator, QLatin1Char('\n'));
    const QString textHash = QString::fromLatin1(QCryptographicHash::hash(text.toUtf8(), QCryptographicHash::Sha256).toHex());
    QJsonObject payload;
    payload.insert(QStringLiteral("document_id"), m_documentId);
    payload.insert(QStringLiteral("document_revision"), m_revision);
    payload.insert(QStringLiteral("text"), text);
    payload.insert(QStringLiteral("language"), detectedLanguage(text));
    payload.insert(QStringLiteral("profile"), m_widget->profile().isEmpty() ? QStringLiteral("creative-default") : m_widget->profile());
    const QJsonObject overrides = analysisOverrides(currentFolderProfileOverrides());
    if (!overrides.isEmpty()) {
        payload.insert(QStringLiteral("overrides"), overrides);
    }
    payload.insert(QStringLiteral("base_offset_utf16"), start);
    payload.insert(QStringLiteral("confirm_adverbs"), true);
    payload.insert(QStringLiteral("persist"), false);
    payload.insert(QStringLiteral("text_hash"), textHash);
    payload.insert(QStringLiteral("grammar"), grammar);
    payload.insert(QStringLiteral("grammar_consent"), grammarConsent);
    QJsonArray exclusions;
    for (const QJsonObject &range : exclusionRanges(text, start)) {
        exclusions.append(range);
    }
    payload.insert(QStringLiteral("exclusion_ranges"), exclusions);
    const QString requestId = m_engine->send(QStringLiteral("analyze_document"), payload);
    if (!requestId.isEmpty()) {
        m_requests.insert(requestId, {QStringLiteral("analyze_selection"), m_revision, start, end, text, textHash, ++m_analysisSequence});
    } else {
        reportReviewNotStarted(tr("ThothPad Engine could not start the selection review."));
    }
}
void ProseController::reviewFolder()
{
    beginGrammarReview(QStringLiteral("folder"));
}
void ProseController::sendFolderAnalysis(const QJsonObject &grammar, bool grammarConsent)
{
    if (!m_engine->isReady()) {
        reportReviewNotStarted(tr("Folder review could not be started."));
        return;
    }
    auto *document = qobject_cast<MarkdownDocument *>(m_editor->document());
    if (!document || document->filePath().isEmpty()) {
        MessageBoxHelper::information(m_widget, tr("Save the document"), tr("Save the current document before reviewing its folder."));
        reportReviewNotStarted(tr("Save the document before reviewing its folder."));
        return;
    }
    const QDir root = QFileInfo(document->filePath()).absoluteDir();
    QJsonArray documents;
    qsizetype totalBytes = 0;
    bool limitReached = false;
    QDirIterator iterator(root.absolutePath(),
                          {QStringLiteral("*.md"), QStringLiteral("*.markdown"), QStringLiteral("*.txt")},
                          QDir::Files,
                          QDirIterator::Subdirectories);
    while (iterator.hasNext() && documents.size() < 500) {
        const QString path = iterator.next();
        QFile file(path);
        if (!file.open(QIODevice::ReadOnly) || file.size() > 2'000'000) {
            continue;
        }
        const QByteArray contents = file.readAll();
        QJsonObject item;
        item.insert(QStringLiteral("name"), root.relativeFilePath(path));
        item.insert(QStringLiteral("text"), QString::fromUtf8(contents));
        const qsizetype itemBytes = QJsonDocument(item).toJson(QJsonDocument::Compact).size() + 1;
        if (totalBytes + itemBytes > FolderMaximumJsonBytes) {
            limitReached = true;
            break;
        }
        documents.append(item);
        totalBytes += itemBytes;
    }
    if (documents.isEmpty()) {
        MessageBoxHelper::information(m_widget, tr("No manuscripts found"), tr("This folder does not contain readable Markdown or text files."));
        reportReviewNotStarted(tr("No manuscripts found in the current folder."));
        return;
    }
    if (limitReached) {
        MessageBoxHelper::information(m_widget,
                                      tr("Folder scope limited"),
                                      tr("The report includes the first %1 readable files within the 12 MiB serialized folder limit.").arg(documents.size()));
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("document_id"), m_documentId);
    payload.insert(QStringLiteral("document_revision"), m_revision);
    payload.insert(QStringLiteral("documents"), documents);
    payload.insert(QStringLiteral("profile"), m_widget->profile().isEmpty() ? QStringLiteral("creative-default") : m_widget->profile());
    const QJsonObject overrides = analysisOverrides(folderProfileOverrides(root));
    if (!overrides.isEmpty()) {
        payload.insert(QStringLiteral("overrides"), overrides);
    }
    payload.insert(QStringLiteral("persist"), false);
    payload.insert(QStringLiteral("grammar"), grammar);
    payload.insert(QStringLiteral("grammar_consent"), grammarConsent);
    const QString requestId = m_engine->send(QStringLiteral("analyze_manuscript"), payload);
    if (!requestId.isEmpty()) {
        m_requests.insert(requestId, {QStringLiteral("analyze_manuscript"), m_revision, 0, 0, QString()});
    } else {
        reportReviewNotStarted(tr("ThothPad Engine could not start the folder review."));
    }
}
QJsonObject ProseController::automaticGrammarSettings() const
{
    QSettings settings;
    settings.beginGroup(QStringLiteral("prose/grammar"));
    QJsonObject grammar;
    grammar.insert(QStringLiteral("provider"), QStringLiteral("harper"));
    grammar.insert(QStringLiteral("dialect"), settings.value(QStringLiteral("language"), QStringLiteral("en-US")).toString());
    grammar.insert(QStringLiteral("include_spelling"), settings.value(QStringLiteral("include_spelling"), false).toBool());
    grammar.insert(QStringLiteral("max_findings"), 100000);
    grammar.insert(QStringLiteral("timeout"), 10);
    settings.endGroup();
    return grammar;
}
void ProseController::beginGrammarReview(const QString &scope, int selectionStart, int selectionEnd)
{
    if (!m_engine->isReady()) {
        reportReviewNotStarted(tr("Review could not be started because the ThothPad Engine is unavailable."));
        return;
    }
    const QJsonObject grammar = GrammarSettingsDialog::configuredSettings();
    const QString provider = grammar.value(QStringLiteral("provider")).toString();
    PendingGrammarReview pending;
    pending.scope = scope;
    pending.grammar = grammar;
    pending.selectionStart = selectionStart;
    pending.selectionEnd = selectionEnd;
    pending.revision = m_revision;
    if (provider == QStringLiteral("harper")) {
        sendGrammarReview(pending, QString());
        return;
    }
    const QString endpoint = grammar.value(QStringLiteral("url")).toString();
    const int characterCount = scope == QStringLiteral("selection") ? selectionEnd - selectionStart
        : scope == QStringLiteral("document")                       ? m_editor->document()->characterCount() - 1
                                                                    : -1;
    const QString scopeDescription = scope == QStringLiteral("folder") ? tr("All readable Markdown and text files in the current document folder")
        : scope == QStringLiteral("selection")                         ? tr("Current selection (%1 characters)").arg(characterCount)
                                                                       : tr("Current document (%1 characters)").arg(characterCount);
    const auto answer = MessageBoxHelper::question(
        m_widget,
        tr("Run external grammar review"),
        tr("Provider: %1\nEndpoint: %2\nScope sent: %3\n\nThe selected prose scope will be transmitted for grammar and copyediting analysis. Continue?")
            .arg(provider, endpoint, scopeDescription));
    if (answer != QMessageBox::Yes) {
        reportReviewNotStarted(tr("Scan cancelled."));
        return;
    }
    const QUrl url(endpoint);
    const QString host = url.host().toLower();
    const bool loopback = host == QStringLiteral("localhost") || host == QStringLiteral("127.0.0.1") || host == QStringLiteral("::1");
    if (provider == QStringLiteral("languagetool") && loopback) {
        sendGrammarReview(pending, QString());
        return;
    }
    if (provider == QStringLiteral("languagetool") && grammar.value(QStringLiteral("username")).toString().isEmpty()) {
        MessageBoxHelper::warning(m_widget,
                                  tr("LanguageTool account required"),
                                  tr("Enter the account name and API key for the remote LanguageTool endpoint in Grammar Settings."));
        reportReviewNotStarted(tr("Scan cancelled."));
        return;
    }
    if (!m_credentials->isAvailable()) {
        MessageBoxHelper::warning(m_widget,
                                  tr("Secure storage unavailable"),
                                  tr("This build cannot retrieve a cloud grammar API key securely. Harper and a local LanguageTool server remain available."));
        reportReviewNotStarted(tr("Scan cancelled."));
        return;
    }
    pending.credentialId = GrammarSettingsDialog::credentialId(grammar);
    m_pendingGrammarReview = pending;
    m_credentials->read(pending.credentialId);
}
void ProseController::sendGrammarReview(const PendingGrammarReview &pending, const QString &apiKey)
{
    if (pending.revision != m_revision) {
        reportReviewNotStarted(tr("Scan cancelled."));
        return;
    }
    QJsonObject grammar = pending.grammar;
    if (!apiKey.isEmpty()) {
        grammar.insert(QStringLiteral("api_key"), apiKey);
    }
    const bool consent = grammar.value(QStringLiteral("provider")).toString() != QStringLiteral("harper");
    if (pending.scope == QStringLiteral("document")) {
        requestAnalysis(true, true, true, grammar, consent);
    } else if (pending.scope == QStringLiteral("selection")) {
        requestSelectionAnalysis(pending.selectionStart, pending.selectionEnd, grammar, consent);
    } else if (pending.scope == QStringLiteral("folder")) {
        sendFolderAnalysis(grammar, consent);
    }
}
void ProseController::retryQueuedAnalysis()
{
    if (!m_analysisRetryPending || !m_documentSynchronized || !m_engine->isReady() || m_widget->mode() == ProseAwarenessWidget::Mode::Off
        || m_editor->document()->isEmpty()) {
        return;
    }
    const bool explicitReport = m_analysisRetryExplicit;
    const QJsonObject grammar = m_analysisRetryGrammar;
    const bool grammarConsent = m_analysisRetryGrammarConsent;
    m_analysisRetryPending = false;
    m_analysisRetryExplicit = false;
    m_analysisRetryGrammar = {};
    m_analysisRetryGrammarConsent = false;
    requestAnalysis(true, true, explicitReport, grammar, grammarConsent);
}
void ProseController::requestAnalysis(bool fullDocument, bool confirmAdverbs, bool explicitReport, const QJsonObject &grammar, bool grammarConsent)
{
    if (!m_engine->isReady()) {
        if (explicitReport) {
            reportReviewNotStarted(tr("Scan cancelled."));
        }
        return;
    }
    if (!m_documentPatchRequestId.isEmpty() || !m_documentPatches.isEmpty()) {
        // Give a queued-but-unflushed batch a chance to go out immediately;
        // postpone only while a frame is genuinely in flight.
        flushPendingDocumentPatches();
        if (!m_documentPatchRequestId.isEmpty() || !m_documentPatches.isEmpty()) {
            m_analysisRetryPending = true;
            m_analysisRetryExplicit = m_analysisRetryExplicit || explicitReport;
            if (explicitReport) {
                m_analysisRetryGrammar = grammar;
                m_analysisRetryGrammarConsent = grammarConsent;
                reportReviewNotStarted(tr("Scan postponed while edits are pending."));
            }
            return;
        }
    }
    QTextDocument *document = m_editor->document();
    const bool mirroredDocument = m_documentSynchronized && m_engine->supportsOperation(QStringLiteral("open_document"));
    int start = 0;
    int end = document->characterCount() - 1;
    if (!fullDocument) {
        QTextBlock block = document->findBlock(m_changedPosition);
        if (block.previous().isValid()) {
            block = block.previous();
        }
        start = block.position();
        QTextBlock last = block.next().isValid() ? block.next() : block;
        if (last.next().isValid()) {
            last = last.next();
        }
        end = qMin(last.position() + last.length() - 1, start + LiveMaximumCharacters);
    }
    QString text;
    if (!fullDocument || !mirroredDocument) {
        QTextCursor rangeCursor(document);
        rangeCursor.setPosition(start);
        rangeCursor.setPosition(end, QTextCursor::KeepAnchor);
        text = rangeCursor.selectedText();
        text.replace(QChar::ParagraphSeparator, QLatin1Char('\n'));
        text.replace(QChar::LineSeparator, QLatin1Char('\n'));
    }
    if (text.isEmpty() && !mirroredDocument) {
        clearObservations();
        if (explicitReport) {
            reportReviewNotStarted(tr("Nothing to scan yet."));
        }
        return;
    }
    if (!fullDocument && !m_liveRequestId.isEmpty()) {
        m_engine->cancel(m_liveRequestId);
        m_requests.remove(m_liveRequestId);
    }
#ifdef THOTHPAD_INSTRUMENTATION
    QElapsedTimer guiWorkTimer;
    guiWorkTimer.start();
#endif
    // Sonnet is not thread-safe: language detection must stay on the GUI
    // thread alongside the spellchecker's Speller usage.
    const QString language = mirroredDocument ? QString() : detectedLanguage(text);
    const int revision = m_revision;
    const QString documentId = m_documentId;
    const quint64 prepGeneration = static_cast<quint64>(++m_analysisPrepGeneration);
    QJsonObject markdownSettings = m_activeProfile.value(QStringLiteral("markdown_exclusions")).toObject();
    const QJsonObject folderSettings = currentFolderProfileOverrides().value(QStringLiteral("markdown_exclusions")).toObject();
    for (auto item = folderSettings.constBegin(); item != folderSettings.constEnd(); ++item) {
        markdownSettings.insert(item.key(), item.value());
    }
    const QJsonObject overrides = analysisOverrides(currentFolderProfileOverrides());
    const QString selectedProfile = m_widget->profile().isEmpty() ? QStringLiteral("creative-default") : m_widget->profile();
    const QJsonObject resolvedGrammar = grammar.isEmpty() ? automaticGrammarSettings() : grammar;
#ifdef THOTHPAD_INSTRUMENTATION
    ProseInstrumentation::instance()->recordGuiThreadWork(guiWorkTimer.elapsed());
#endif
    // SHA-256 over potentially large text plus the global exclusion regex
    // scan belong off the GUI thread; results are applied only if this
    // request is still the newest one by then.
    auto *prepWatcher = new QFutureWatcher<AnalysisPrep>(this);
    connect(prepWatcher,
            &QFutureWatcher<AnalysisPrep>::finished,
            this,
            [this,
             prepWatcher,
             prepGeneration,
             documentId,
             revision,
             start,
             end,
             text,
             language,
             mirroredDocument,
             fullDocument,
             confirmAdverbs,
             explicitReport,
             grammarConsent,
             overrides,
             selectedProfile,
             resolvedGrammar]() {
                prepWatcher->deleteLater();
                if (prepGeneration != static_cast<quint64>(m_analysisPrepGeneration) || revision != m_revision || documentId != m_documentId
                    || !m_engine->isReady()) {
                    return;
                }
                const AnalysisPrep prep = prepWatcher->result();
                // Discard stale prep when edits landed, the document was replaced, or
                // synchronization state flipped while the background work ran.
                if (prepGeneration != static_cast<quint64>(m_analysisPrepGeneration) || revision != m_revision || documentId != m_documentId
                    || !m_engine->isReady() || m_documentSynchronized != mirroredDocument) {
                    return;
                }
                QJsonObject payload;
                payload.insert(QStringLiteral("document_id"), documentId);
                payload.insert(QStringLiteral("document_revision"), revision);
                if (!mirroredDocument) {
                    payload.insert(QStringLiteral("text"), text);
                    payload.insert(QStringLiteral("language"), language);
                } else if (!fullDocument) {
                    payload.insert(QStringLiteral("start_utf16"), start);
                    payload.insert(QStringLiteral("end_utf16"), end);
                }
                payload.insert(QStringLiteral("profile"), selectedProfile);
                if (!overrides.isEmpty()) {
                    payload.insert(QStringLiteral("overrides"), overrides);
                }
                payload.insert(QStringLiteral("base_offset_utf16"), start);
                payload.insert(QStringLiteral("confirm_adverbs"), confirmAdverbs);
                payload.insert(QStringLiteral("persist"), false);
                if (!prep.textHash.isEmpty()) {
                    payload.insert(QStringLiteral("text_hash"), prep.textHash);
                }
                payload.insert(QStringLiteral("grammar"), resolvedGrammar);
                payload.insert(QStringLiteral("grammar_consent"), grammarConsent);
                if (fullDocument) {
                    payload.insert(QStringLiteral("initial_page_size"), 0);
                }
                if (!prep.exclusions.isEmpty()) {
                    payload.insert(QStringLiteral("exclusion_ranges"), prep.exclusions);
                }
                const QString operation = fullDocument ? QStringLiteral("analyze_document") : QStringLiteral("analyze_region");
                const QString requestId = m_engine->send(operation, payload);
                if (requestId.isEmpty()) {
                    if (explicitReport) {
                        reportReviewNotStarted(tr("ThothPad Engine could not complete the request."));
                    }
                    return;
                }
                const QString contextOperation =
                    fullDocument ? (explicitReport ? QStringLiteral("analyze_document") : QStringLiteral("analyze_idle")) : QStringLiteral("analyze_region");
                m_requests.insert(requestId, {contextOperation, revision, start, end, text, prep.textHash, ++m_analysisSequence});
                if (!fullDocument) {
                    m_liveRequestId = requestId;
                } else if (!explicitReport) {
                    if (!m_backgroundRequestId.isEmpty()) {
                        m_engine->cancel(m_backgroundRequestId);
                        m_requests.remove(m_backgroundRequestId);
                    }
                    m_backgroundRequestId = requestId;
                    m_widget->setEngineMessage(tr("Reviewing document..."));
                }
            });
    prepWatcher->setFuture(QtConcurrent::run([text, start, markdownSettings]() mutable {
        AnalysisPrep prep;
        if (!text.isEmpty()) {
            prep.textHash = QString::fromLatin1(QCryptographicHash::hash(text.toUtf8(), QCryptographicHash::Sha256).toHex());
            QJsonArray exclusions;
            for (const QJsonObject &range : exclusionRangesForSettings(text, start, markdownSettings)) {
                exclusions.append(range);
            }
            prep.exclusions = exclusions;
        }
        return prep;
    }));
}
void ProseController::handleResponse(const QString &requestId, const QJsonObject &response)
{
    const RequestContext context = m_requests.take(requestId);
    if (requestId == m_liveRequestId) {
        m_liveRequestId.clear();
    }
    if (requestId == m_backgroundRequestId) {
        m_backgroundRequestId.clear();
    }
    if (context.operation.isEmpty()) {
        return;
    }
    if (!response.value(QStringLiteral("ok")).toBool()) {
        if (context.operation == QStringLiteral("query_overlay_spans") && requestId == m_overlayRequestId) {
            m_overlayRequestInFlight = false;
            m_overlayRequestId.clear();
        }
        if (context.operation == QStringLiteral("query_findings") && requestId == m_findingRequestId) {
            m_findingRequestId.clear();
            m_snapshotFindingPages.reset();
        }
        const QJsonObject error = response.value(QStringLiteral("error")).toObject();
        if (context.operation == QStringLiteral("open_document") || context.operation == QStringLiteral("patch_document")
            || error.value(QStringLiteral("code")).toString() == QStringLiteral("resync_required")) {
            m_documentSynchronized = false;
            m_documentExclusionsStale = false;
            m_documentPatchRequestId.clear();
            m_documentPatches.clear();
            m_documentSyncTimer.start();
        }
        const QString message = error.value(QStringLiteral("message")).toString();
        if (message.contains(QStringLiteral("analysis_id"), Qt::CaseInsensitive) && message.contains(QStringLiteral("invalid"), Qt::CaseInsensitive)) {
            m_analysisId.clear();
            m_snapshotFindingPages.reset();
            if (m_widget->mode() != ProseAwarenessWidget::Mode::Off && !m_editor->document()->isEmpty()) {
                requestAnalysis(true, true);
                return;
            }
        }
        m_widget->setEngineMessage(message.isEmpty() ? tr("ThothPad Engine could not complete the request.") : message);
        return;
    }
    const QJsonObject result = response.value(QStringLiteral("result")).toObject();
    if (context.operation == QStringLiteral("open_document")) {
        if (context.revision == m_revision && result.value(QStringLiteral("document_revision")).toInt(-1) == m_revision) {
            m_documentSynchronized = true;
            m_documentExclusionsStale = false;
            m_documentPatchRequestId.clear();
            m_documentPatches.clear();
            retryQueuedAnalysis();
        } else {
            m_documentSynchronized = false;
            m_documentSyncTimer.start();
        }
        return;
    }
    if (context.operation == QStringLiteral("patch_document")) {
        m_documentPatchRequestId.clear();
        if (m_documentPatches.isEmpty() || result.value(QStringLiteral("document_revision")).toInt(-1) != context.revision) {
            m_documentSynchronized = false;
            m_documentPatches.clear();
            m_documentSyncTimer.start();
            return;
        }
        m_documentExclusionsStale = m_documentExclusionsStale || result.value(QStringLiteral("exclusions_stale")).toBool();
        // The flushed frame carried the whole coalesced batch; the engine
        // applied it atomically, so every queued edit is now acknowledged.
        m_documentPatches.clear();
        if (m_documentExclusionsStale) {
            m_documentSynchronized = false;
            m_documentSyncTimer.start();
        } else {
            retryQueuedAnalysis();
            if (m_widget->mode() == ProseAwarenessWidget::Mode::Live) {
                m_liveTimer.start();
                m_idleTimer.start();
            }
        }
        return;
    }
    if (context.operation == QStringLiteral("list_profiles")) {
        QStringList profiles;
        for (const QJsonValue &value : result.value(QStringLiteral("profiles")).toArray()) {
            const QString name = value.toObject().value(QStringLiteral("name")).toString();
            if (!name.isEmpty()) {
                profiles.append(name);
            }
        }
        const QString selectedProfile = QSettings().value(QStringLiteral("prose/profile"), QStringLiteral("creative-default")).toString();
        m_widget->setProfiles(profiles, profiles.contains(selectedProfile) ? selectedProfile : QStringLiteral("creative-default"));
        requestProfilePresentation();
        return;
    }
    if (context.operation == QStringLiteral("get_profile")) {
        const QJsonObject profile = result.value(QStringLiteral("profile")).toObject();
        if (profile.isEmpty()) {
            return;
        }
        QJsonObject editableProfile = profile;
        if (editableProfile.value(QStringLiteral("lenses")).toObject().isEmpty()) {
            editableProfile.insert(QStringLiteral("lenses"), lensProfileSettings());
        }
        ProfileEditorDialog dialog(editableProfile, m_widget);
        if (dialog.exec() == QDialog::Accepted) {
            const QJsonObject savedProfile = dialog.profile();
            if (savedProfile.value(QStringLiteral("name")).toString() == m_widget->profile()) {
                m_activeProfile = savedProfile;
            }
            applyLensProfileSettings(profile.value(QStringLiteral("name")).toString(), savedProfile.value(QStringLiteral("lenses")).toObject());
            QJsonObject payload;
            payload.insert(QStringLiteral("name"), profile.value(QStringLiteral("name")));
            payload.insert(QStringLiteral("profile"), savedProfile);
            const QString saveId = m_engine->send(QStringLiteral("save_profile"), payload);
            if (!saveId.isEmpty()) {
                m_requests.insert(saveId, {QStringLiteral("save_profile"), m_revision, 0, 0});
            }
        }
        return;
    }
    if (context.operation == QStringLiteral("load_profile_presentation")) {
        const QString currentProfile = m_widget->profile().isEmpty() ? QStringLiteral("creative-default") : m_widget->profile();
        if (context.sourceText == currentProfile) {
            m_activeProfile = result.value(QStringLiteral("profile")).toObject();
            if (m_lensPresentationDirty) {
                m_activeProfile.insert(QStringLiteral("lenses"), m_baseLenses);
                m_profileSaveTimer.start();
            } else {
                applyLensProfileSettings(context.sourceText, m_activeProfile.value(QStringLiteral("lenses")).toObject());
            }
            if (m_widget->mode() == ProseAwarenessWidget::Mode::Live) {
                requestAnalysis(true, true);
            }
        }
        return;
    }
    if (context.operation == QStringLiteral("prepare_rewrite")) {
        if (requestId != m_pendingRevision.profileRequestId || context.revision != m_revision) {
            return;
        }
        confirmPendingRevision(result.value(QStringLiteral("profile")).toObject());
        return;
    }
    if (context.operation == QStringLiteral("save_profile")) {
        const QString listId = m_engine->send(QStringLiteral("list_profiles"));
        if (!listId.isEmpty()) {
            m_requests.insert(listId, {QStringLiteral("list_profiles"), m_revision, 0, 0});
        }
        requestAnalysis(true, true);
        return;
    }
    if (context.operation == QStringLiteral("save_profile_presentation")) {
        return;
    }
    if (context.operation == QStringLiteral("import_profile")) {
        const QString imported = result.value(QStringLiteral("name")).toString();
        if (!imported.isEmpty()) {
            QSettings().setValue(QStringLiteral("prose/profile"), imported);
            const QJsonDocument importedDocument = QJsonDocument::fromJson(context.sourceText.toUtf8());
            applyLensProfileSettings(imported, importedDocument.object().value(QStringLiteral("lenses")).toObject());
        }
        const QString listId = m_engine->send(QStringLiteral("list_profiles"));
        if (!listId.isEmpty()) {
            m_requests.insert(listId, {QStringLiteral("list_profiles"), m_revision, 0, 0});
        }
        return;
    }
    if (context.operation == QStringLiteral("export_profile")) {
        QJsonDocument exported = QJsonDocument::fromJson(result.value(QStringLiteral("json")).toString().toUtf8());
        QJsonObject exportedProfile = exported.object();
        exportedProfile.insert(QStringLiteral("lenses"), lensProfileSettings());
        const QByteArray serialized = QJsonDocument(exportedProfile).toJson(QJsonDocument::Indented);
        QSaveFile file(context.sourceText);
        if (!file.open(QIODevice::WriteOnly) || file.write(serialized) != serialized.size() || !file.commit()) {
            MessageBoxHelper::warning(m_widget, tr("Export failed"), file.errorString());
        }
        return;
    }
    if (context.revision != m_revision || response.value(QStringLiteral("document_revision")).toInt(-1) != m_revision) {
        return;
    }
    if (context.operation == QStringLiteral("analyze_region") && !m_analysisId.isEmpty()) {
        return;
    }
    if (context.operation == QStringLiteral("analyze_region") && context.sequence > 0 && context.sequence < m_lastAppliedSequence) {
        return;
    }
    if (context.operation == QStringLiteral("query_overlay_spans")) {
        if (requestId != m_overlayRequestId || context.sequence != m_overlayHydrationGeneration) {
            return;
        }
        m_overlayRequestInFlight = false;
        m_overlayRequestId.clear();
        if (result.value(QStringLiteral("analysis_id")).toString() != m_analysisId) {
            return;
        }
        applyOverlayPage(result);
        m_overlayCursor = result.value(QStringLiteral("next_cursor")).toString();
        m_overlayHasMore = result.value(QStringLiteral("has_more")).toBool() && !m_overlayCursor.isEmpty();
        return;
    }
    if (context.operation == QStringLiteral("query_findings")) {
        if (requestId != m_findingRequestId || !m_snapshotFindingPages.accepts(context.sourceText, context.revision, context.textHash, result)) {
            return;
        }
        m_findingRequestId.clear();
        if (context.textHash.isEmpty()) {
            m_diagnostics.removeIf([&context](const ProseDiagnostic &diagnostic) {
                return diagnostic.category == context.sourceText;
            });
        }
        QList<ProseDiagnostic> page;
        page.reserve(result.value(QStringLiteral("diagnostics")).toArray().size());
        for (const QJsonValue &value : result.value(QStringLiteral("diagnostics")).toArray()) {
            ProseDiagnostic diagnostic = ProseDiagnostic::fromJson(value.toObject());
            diagnostic.category = lensCategory(diagnostic.analyzer);
            page.append(diagnostic);
        }
        const auto positionLess = [](const ProseDiagnostic &left, const ProseDiagnostic &right) {
            return left.start == right.start ? left.end < right.end : left.start < right.start;
        };
        std::sort(page.begin(), page.end(), positionLess);
        if (!m_diagnostics.isEmpty() && !page.isEmpty() && positionLess(page.first(), m_diagnostics.last())) {
            m_diagnostics.append(page);
            std::sort(m_diagnostics.begin(), m_diagnostics.end(), positionLess);
        } else {
            m_diagnostics.append(page);
        }
        m_displayLane = m_snapshotDisplayLane;
        const bool hasMore = m_snapshotFindingPages.advance(result);
        if (m_widget->selectedCategory() == context.sourceText) {
            refreshSelectedFindings(hasMore, !context.textHash.isEmpty());
        }
        m_widget->setCategoryCounts(m_snapshotCategoryCounts);
        if (context.sequence > 0) {
            m_lastAppliedSequence = qMax(m_lastAppliedSequence, context.sequence);
        }
        if (!hasMore) {
            m_snapshotLoadedCategories.insert(context.sourceText);
            const QSet<QString> previousSuppressions = m_overlaySuppressions.exactSpans;
            recordOverlaySuppressions(m_diagnostics);
            if (previousSuppressions != m_overlaySuppressions.exactSpans) {
                restartOverlayHydration();
            }
            m_widget->setEngineMessage(tr("Ready"));
        } else {
            m_widget->setEngineMessage(
                tr("Loading %1 of %2...").arg(m_snapshotFindingPages.loadedCount).arg(m_snapshotCategoryCounts.value(context.sourceText)));
            const QString analysisId = m_snapshotFindingPages.analysisId;
            const QString category = m_snapshotFindingPages.category;
            const QString cursor = m_snapshotFindingPages.expectedCursor;
            const int revision = m_snapshotFindingPages.revision;
            QTimer::singleShot(0, this, [this, analysisId, category, cursor, revision]() {
                if (m_findingRequestId.isEmpty() && m_snapshotFindingPages.active && m_snapshotFindingPages.analysisId == analysisId
                    && m_snapshotFindingPages.category == category && m_snapshotFindingPages.expectedCursor == cursor
                    && m_snapshotFindingPages.revision == revision) {
                    queryFindings(category, cursor);
                }
            });
        }
        return;
    }
    if (!context.textHash.isEmpty() && result.value(QStringLiteral("text_hash")).toString() != context.textHash) {
        return;
    }
    if (context.operation == QStringLiteral("rewrite")) {
        const QJsonArray errors = result.value(QStringLiteral("llm_errors")).toArray();
        if (!errors.isEmpty()) {
            MessageBoxHelper::warning(m_widget, tr("Model request failed"), errors.first().toString());
            return;
        }
        const QString proposed = result.value(QStringLiteral("output_text")).toString();
        if (proposed.isEmpty()) {
            return;
        }
        RewriteReviewDialog dialog(context.sourceText,
                                   proposed,
                                   result.value(QStringLiteral("score_before")).toDouble(),
                                   result.value(QStringLiteral("score_after")).toDouble(),
                                   m_widget);
        if (dialog.exec() == QDialog::Accepted && context.revision == m_revision) {
            QTextCursor cursor(m_editor->document());
            cursor.setPosition(context.rangeStart);
            cursor.setPosition(context.rangeEnd, QTextCursor::KeepAnchor);
            cursor.beginEditBlock();
            cursor.insertText(dialog.acceptedText());
            cursor.endEditBlock();
            m_editor->setTextCursor(cursor);
        }
        return;
    }
    if (context.sequence > 0) {
        m_lastAppliedSequence = qMax(m_lastAppliedSequence, context.sequence);
    }
    if (context.operation == QStringLiteral("analyze_manuscript")) {
        m_lastReport = result;
        clearObservations();
        MessageBoxHelper::information(m_widget,
                                      tr("Folder review complete"),
                                      tr("Reviewed %1 documents. Export the report as Markdown or JSON to inspect manuscript-wide patterns.")
                                          .arg(result.value(QStringLiteral("document_count")).toInt()));
        m_widget->setEngineMessage(tr("Ready"));
        return;
    }
    QList<ProseDiagnostic> diagnostics;
    for (const QJsonValue &value : result.value(QStringLiteral("diagnostics")).toArray()) {
        ProseDiagnostic diagnostic = ProseDiagnostic::fromJson(value.toObject());
        diagnostic.category = lensCategory(diagnostic.analyzer);
        diagnostics.append(diagnostic);
    }
    if (context.operation == QStringLiteral("analyze_document") || context.operation == QStringLiteral("analyze_selection")) {
        m_lastReport = result;
    }
    const QJsonObject dialogue = result.value(QStringLiteral("dialogue")).toObject();
    if (!dialogue.isEmpty()) {
        emit dialogueStatsChanged(dialogue.value(QStringLiteral("span_count")).toInt(), dialogue.value(QStringLiteral("dialogue_word_ratio")).toDouble() * 100);
    }
    if (context.operation != QStringLiteral("analyze_region") && !result.value(QStringLiteral("analysis_id")).toString().isEmpty()) {
        if (!m_liveRequestId.isEmpty()) {
            m_engine->cancel(m_liveRequestId);
            m_requests.remove(m_liveRequestId);
            m_liveRequestId.clear();
        }
        m_analysisId = result.value(QStringLiteral("analysis_id")).toString();
        m_snapshotDisplayLane = context.operation == QStringLiteral("analyze_idle") ? QStringLiteral("idle") : QStringLiteral("report");
        m_snapshotCategoryCounts = categoryCounts(result.value(QStringLiteral("counts_by_analyzer")).toObject());
        m_diagnostics.clear();
        m_widget->setDiagnostics({});
        m_widget->setCategoryCounts(m_snapshotCategoryCounts);
        beginSnapshotLoading();
        // Delta hydration: keep the applied overlays painted and diff the
        // incoming snapshot against them instead of wiping and rebuilding.
        restartOverlayHydration();
        return;
    }
    m_displayLane = context.operation == QStringLiteral("analyze_region") ? QStringLiteral("live")
        : context.operation == QStringLiteral("analyze_idle")             ? QStringLiteral("idle")
                                                                          : QStringLiteral("report");
    acceptDiagnostics(diagnostics, context);
    if (context.operation == QStringLiteral("analyze_selection")) {
        m_widget->setEngineMessage(tr("Ready"));
    }
}
void ProseController::acceptDiagnostics(const QList<ProseDiagnostic> &diagnostics, const RequestContext &context)
{
    if (context.operation == QStringLiteral("analyze_document") || context.operation == QStringLiteral("analyze_idle")) {
        m_diagnostics = diagnostics;
    } else {
        m_diagnostics.removeIf([&context](const ProseDiagnostic &item) {
            return item.start < context.rangeEnd && item.end > context.rangeStart;
        });
        m_diagnostics.append(diagnostics);
        std::sort(m_diagnostics.begin(), m_diagnostics.end(), [](const ProseDiagnostic &left, const ProseDiagnostic &right) {
            return left.start < right.start;
        });
    }
    applyHighlights();
}
void ProseController::queryFindings(const QString &category, const QString &cursor)
{
    if (!m_engine->isReady() || m_analysisId.isEmpty() || category.isEmpty() || !m_findingRequestId.isEmpty() || !m_snapshotFindingPages.active
        || m_snapshotFindingPages.analysisId != m_analysisId || m_snapshotFindingPages.category != category || m_snapshotFindingPages.revision != m_revision
        || m_snapshotFindingPages.expectedCursor != cursor) {
        return;
    }
    const QStringList analyzers = analyzersForCategory(category);
    if (analyzers.isEmpty()) {
        return;
    }
    QJsonArray analyzerValues;
    for (const QString &analyzer : analyzers) {
        analyzerValues.append(analyzer);
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("document_id"), m_documentId);
    payload.insert(QStringLiteral("document_revision"), m_revision);
    payload.insert(QStringLiteral("analysis_id"), m_analysisId);
    payload.insert(QStringLiteral("analyzers"), analyzerValues);
    payload.insert(QStringLiteral("limit"), 250);
    if (!cursor.isEmpty()) {
        payload.insert(QStringLiteral("cursor"), cursor);
    }
    const QString requestId = m_engine->send(QStringLiteral("query_findings"), payload);
    if (requestId.isEmpty()) {
        return;
    }
    m_findingRequestId = requestId;
    m_requests.insert(requestId, {QStringLiteral("query_findings"), m_revision, 0, 0, category, cursor, ++m_analysisSequence});
}
void ProseController::queryOverlaySpans(const QString &cursor)
{
    if (!m_engine->isReady() || m_analysisId.isEmpty() || m_overlayRequestInFlight || !m_engine->supportsOperation(QStringLiteral("query_overlay_spans"))) {
        return;
    }
    QSet<QString> analyzerNames;
    for (const QString &category : std::as_const(m_enabledCategories)) {
        for (const QString &analyzer : analyzersForCategory(category)) {
            analyzerNames.insert(analyzer);
        }
    }
    QJsonArray categories;
    for (const QString &analyzer : std::as_const(analyzerNames)) {
        categories.append(analyzer);
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("document_id"), m_documentId);
    payload.insert(QStringLiteral("document_revision"), m_revision);
    payload.insert(QStringLiteral("analysis_id"), m_analysisId);
    payload.insert(QStringLiteral("categories"), categories);
    payload.insert(QStringLiteral("limit"), 4096);
    if (!cursor.isEmpty()) {
        payload.insert(QStringLiteral("cursor"), cursor);
    }
    const QString requestId = m_engine->send(QStringLiteral("query_overlay_spans"), payload);
    if (!requestId.isEmpty()) {
        m_overlayRequestInFlight = true;
        m_overlayRequestId = requestId;
        m_requests.insert(requestId, {QStringLiteral("query_overlay_spans"), m_revision, 0, 0, m_analysisId, cursor, m_overlayHydrationGeneration});
    }
}
void ProseController::restartOverlayHydration()
{
#ifdef THOTHPAD_INSTRUMENTATION
    ProseInstrumentation::instance()->beginHydrationCycle();
#endif
    recordOverlaySuppressions(m_diagnostics);
    ++m_overlayHydrationGeneration;
    if (!m_overlayRequestId.isEmpty()) {
        m_engine->cancel(m_overlayRequestId);
        m_requests.remove(m_overlayRequestId);
    }
    m_overlayRequestId.clear();
    m_overlayRequestInFlight = false;
    m_overlayApplyTimer.stop();
    m_overlayPages.clear();
    m_currentOverlayPage = {};
    m_currentOverlaySpanOrder.clear();
    m_currentOverlayIndex = 0;
    m_overlayCursor.clear();
    m_overlayHasMore = false;
    // Delta hydration: keep the currently applied overlays painted while the
    // incoming snapshot is accumulated fresh and diffed against what is on
    // screen block by block. Only blocks whose incoming span-set differs are
    // repainted.
    m_overlayBaselineFormats = std::move(m_snapshotOverlayFormats);
    m_snapshotOverlayFormats.clear();
    m_pendingOverlayUpdates.clear();
    m_overlayDiffPending = true;
    m_overlayViewportAnchor = overlayViewportAnchor();
    if (m_engine->supportsOperation(QStringLiteral("query_overlay_spans"))) {
        queryOverlaySpans();
    }
}
int ProseController::overlayViewportAnchor() const
{
    const QTextCursor viewportTopLeft = m_editor->cursorForPosition(QPoint(0, 0));
    if (viewportTopLeft.block().isValid()) {
        return viewportTopLeft.block().position();
    }
    return m_editor->textCursor().position();
}
QVector<int> ProseController::viewportOrderedSpans(const QJsonObject &page) const
{
    const QJsonArray spans = page.value(QStringLiteral("spans")).toArray();
    QVector<QPair<int, int>> orderedByDistance;
    orderedByDistance.reserve(spans.size());
    for (int index = 0; index < spans.size(); ++index) {
        const QJsonArray span = spans.at(index).toArray();
        const int start = span.size() == 3 ? span.at(0).toInt(-1) : -1;
        const QTextBlock block = start >= 0 ? m_editor->document()->findBlock(start) : QTextBlock();
        const int distance = block.isValid() ? qAbs(block.position() - m_overlayViewportAnchor) : std::numeric_limits<int>::max();
        orderedByDistance.append({distance, index});
    }
    std::stable_sort(orderedByDistance.begin(), orderedByDistance.end(), [](const QPair<int, int> &left, const QPair<int, int> &right) {
        return left.first < right.first;
    });
    QVector<int> spanOrder;
    spanOrder.reserve(orderedByDistance.size());
    for (const auto &entry : orderedByDistance) {
        spanOrder.append(entry.second);
    }
    return spanOrder;
}
void ProseController::diffOverlaySnapshot()
{
    m_overlayDiffPending = false;
    QVector<int> updatedPositions;
    int preservedBlocks = 0;
    for (auto iterator = m_overlayBaselineFormats.constBegin(); iterator != m_overlayBaselineFormats.constEnd(); ++iterator) {
        const auto incoming = m_snapshotOverlayFormats.constFind(iterator.key());
        if (incoming != m_snapshotOverlayFormats.constEnd() && overlayFormatListsEqual(iterator.value(), incoming.value())) {
            ++preservedBlocks;
        } else {
            updatedPositions.append(iterator.key());
        }
    }
    // Blocks that appeared during this hydration were streamed immediately;
    // remaining updates clear or repaint blocks the new snapshot changed.
    const int anchor = m_overlayViewportAnchor;
    std::stable_sort(updatedPositions.begin(), updatedPositions.end(), [anchor](int left, int right) {
        return qAbs(left - anchor) < qAbs(right - anchor);
    });
    m_pendingOverlayUpdates += updatedPositions;
    m_overlayBaselineFormats.clear();
#ifdef THOTHPAD_INSTRUMENTATION
    ProseInstrumentation::instance()->recordBlocksPreserved(preservedBlocks);
#endif
}
void ProseController::adjustCachedOverlayFormats(int position, int removed, int added)
{
    if (m_snapshotOverlayFormats.isEmpty() && m_overlayBaselineFormats.isEmpty()) {
        return;
    }
    m_snapshotOverlayFormats = adjustedOverlayFormatsAfterEdit(m_snapshotOverlayFormats, position, removed, added);
    // While a delta hydration is running, the painted overlays live in the
    // baseline until the diff swaps them in; keep it aligned with the edit.
    const bool hydrating = !m_overlayBaselineFormats.isEmpty();
    if (hydrating) {
        m_overlayBaselineFormats = adjustedOverlayFormatsAfterEdit(m_overlayBaselineFormats, position, removed, added);
    }
    // Mirror the TextFormatOverlayController::onContentsChange eviction zone
    // and push surviving adjusted ranges back so painted highlights do not
    // blink out until the next snapshot refresh reapplies them.
    QTextDocument *document = m_editor->document();
    const QTextBlock endBlock = document->findBlock(position + qMax(1, added));
    QSet<int> evictedPositions;
    for (QTextBlock block = document->findBlock(position); block.isValid(); block = block.next()) {
        evictedPositions.insert(block.position());
        if (!endBlock.isValid() || block == endBlock) {
            break;
        }
    }
    const QHash<int, QList<QTextLayout::FormatRange>> &paintedState = hydrating ? m_overlayBaselineFormats : m_snapshotOverlayFormats;
    QHash<int, QList<QTextLayout::FormatRange>> reapplied;
    for (const int blockPosition : std::as_const(evictedPositions)) {
        const auto survivors = paintedState.constFind(blockPosition);
        if (survivors == paintedState.constEnd()) {
            continue;
        }
        const QTextBlock block = document->findBlock(blockPosition);
        if (!block.isValid() || block.position() != blockPosition) {
            continue;
        }
        reapplied.insert(blockPosition, survivors.value());
    }
    if (!reapplied.isEmpty()) {
        m_editor->textFormatOverlayController()->updateChannelFormats(ProseChannel, reapplied);
    }
}
void ProseController::recordOverlaySuppressions(const QList<ProseDiagnostic> &diagnostics)
{
    m_overlaySuppressions.disabledRules = m_disabledRules;
    for (const ProseDiagnostic &diagnostic : diagnostics) {
        if (diagnostic.end <= diagnostic.start || diagnostic.ruleId.isEmpty()) {
            continue;
        }
        const bool ignored = !m_ignoredOccurrences.isEmpty() && isPersistentlyIgnored(diagnostic);
        const bool allowed = !m_allowedPhrases.isEmpty() && isAllowedPhrase(diagnostic);
        if (m_dismissedIds.contains(diagnostic.id) || ignored || allowed) {
            m_overlaySuppressions.exactSpans.insert(
                ReportOverlaySuppressionState::spanKey(diagnostic.category, diagnostic.ruleId, diagnostic.start, diagnostic.end));
        }
    }
}
void ProseController::applyOverlayPage(const QJsonObject &result)
{
    m_overlayPages.enqueue(result);
    if (!m_overlayApplyTimer.isActive()) {
        m_overlayApplyTimer.start(0);
    }
}
void ProseController::processOverlayBatch()
{
#ifdef THOTHPAD_INSTRUMENTATION
    ProseInstrumentation::instance()->recordHydrationTick();
    int appliedSpans = 0;
#endif
    QHash<int, QList<QTextLayout::FormatRange>> changedBlocks;
    QElapsedTimer budget;
    budget.start();
    int processedSpans = 0;
    while (budget.elapsed() < m_overlayBudgetMs && changedBlocks.size() < 4 && processedSpans < 128) {
        if (m_currentOverlayPage.isEmpty()) {
            if (m_overlayPages.isEmpty()) {
                break;
            }
            m_currentOverlayPage = m_overlayPages.dequeue();
            m_currentOverlaySpanOrder = viewportOrderedSpans(m_currentOverlayPage);
            m_currentOverlayIndex = 0;
        }
        const QJsonArray spans = m_currentOverlayPage.value(QStringLiteral("spans")).toArray();
        if (m_currentOverlayIndex >= m_currentOverlaySpanOrder.size()) {
            m_currentOverlayPage = {};
            m_currentOverlaySpanOrder.clear();
            m_currentOverlayIndex = 0;
            continue;
        }
        const QJsonArray categoryNames = m_currentOverlayPage.value(QStringLiteral("categories")).toArray();
        const QJsonArray rules = m_currentOverlayPage.value(QStringLiteral("rules")).toArray();
        ++processedSpans;
        const QJsonArray span = spans.at(m_currentOverlaySpanOrder.at(m_currentOverlayIndex++)).toArray();
        if (span.size() != 3) {
            continue;
        }
        const int start = span.at(0).toInt(-1);
        const int end = span.at(1).toInt(-1);
        const int ruleIndex = span.at(2).toInt(-1);
        if (start < 0 || end <= start || ruleIndex < 0 || ruleIndex >= rules.size()) {
            continue;
        }
        const QJsonObject rule = rules.at(ruleIndex).toObject();
        const int categoryIndex = rule.value(QStringLiteral("category")).toInt(-1);
        if (categoryIndex < 0 || categoryIndex >= categoryNames.size()) {
            continue;
        }
        const QString category = lensCategory(categoryNames.at(categoryIndex).toString());
        if (!m_enabledCategories.contains(category)) {
            continue;
        }
        const QString ruleId = rule.value(QStringLiteral("rule_id")).toString();
        if (m_overlaySuppressions.suppresses(category, ruleId, start, end)) {
            continue;
        }
        QTextBlock block = m_editor->document()->findBlock(start);
        while (block.isValid() && block.position() < end) {
            const int rangeStart = qMax(start, block.position());
            const int rangeEnd = qMin(end, block.position() + block.length() - 1);
            if (rangeEnd > rangeStart) {
                QTextLayout::FormatRange formatRange;
                formatRange.start = rangeStart - block.position();
                formatRange.length = rangeEnd - rangeStart;
                const QString decoration = m_decorations.value(category, QStringLiteral("background"));
                if (decoration != QStringLiteral("underline")) {
                    QColor background = categoryColor(category);
                    background.setAlpha(72);
                    formatRange.format.setBackground(background);
                }
                formatRange.format.setToolTip(QStringLiteral("%1\n%2").arg(m_widget->categoryFindingLabel(category), m_widget->categoryDescription(category)));
                int priority = m_categoryPriorities.value(category, 100);
                const QString level = rule.value(QStringLiteral("level")).toString();
                if (level == QStringLiteral("context_flag")) {
                    priority += 100;
                } else if (level == QStringLiteral("strong_flag")) {
                    priority += 200;
                } else if (level == QStringLiteral("hard_fail")) {
                    priority += 300;
                }
                TextFormatOverlayController::setPriority(formatRange.format, priority);
                if (decoration != QStringLiteral("background") || level == QStringLiteral("hard_fail")) {
                    formatRange.format.setUnderlineColor(categoryColor(category));
                    formatRange.format.setUnderlineStyle(QTextCharFormat::SingleUnderline);
                }
                QList<QTextLayout::FormatRange> &blockRanges = m_snapshotOverlayFormats[block.position()];
                blockRanges.append(formatRange);
#ifdef THOTHPAD_INSTRUMENTATION
                ++appliedSpans;
#endif
                // Blocks without an existing overlay paint immediately so a
                // first hydration streams in; refreshed blocks wait for the
                // end-of-hydration diff so unchanged blocks are never touched.
                if (!m_overlayBaselineFormats.contains(block.position())) {
                    changedBlocks.insert(block.position(), blockRanges);
                }
            }
            block = block.next();
        }
    }
    const bool pagesDrained = m_currentOverlayPage.isEmpty() && m_overlayPages.isEmpty();
    if (pagesDrained && !m_overlayHasMore && !m_overlayRequestInFlight && m_overlayDiffPending) {
        diffOverlaySnapshot();
    }
    while (budget.elapsed() < m_overlayBudgetMs && changedBlocks.size() < 4 && !m_pendingOverlayUpdates.isEmpty()) {
        const int blockPosition = m_pendingOverlayUpdates.takeFirst();
        changedBlocks.insert(blockPosition, m_snapshotOverlayFormats.value(blockPosition));
    }
    if (!changedBlocks.isEmpty()) {
#ifdef THOTHPAD_INSTRUMENTATION
        ProseInstrumentation::instance()->recordSpansApplied(appliedSpans);
#endif
        m_editor->textFormatOverlayController()->updateChannelFormats(ProseChannel, changedBlocks);
    }
    if (!m_currentOverlayPage.isEmpty() || !m_overlayPages.isEmpty()) {
        m_overlayApplyTimer.start(0);
    } else if (m_overlayHasMore && !m_overlayCursor.isEmpty()) {
        queryOverlaySpans(m_overlayCursor);
    } else if (!m_pendingOverlayUpdates.isEmpty()) {
        m_overlayApplyTimer.start(0);
    }
}
void ProseController::beginSnapshotLoading()
{
    resetSnapshotLoading();
    const QString selected = m_widget->selectedCategory();
    refreshSelectedFindings();
    if (!selected.isEmpty() && m_snapshotCategoryCounts.value(selected) > 0) {
        m_snapshotFindingPages.begin(m_analysisId, selected, m_revision);
        queryFindings(selected);
    } else {
        m_widget->setEngineMessage(tr("Ready"));
    }
}
void ProseController::loadNextSnapshotCategory()
{
    if (m_analysisId.isEmpty() || m_snapshotFindingPages.active || !m_findingRequestId.isEmpty()) {
        return;
    }
    while (!m_snapshotCategoryQueue.isEmpty()) {
        const QString category = m_snapshotCategoryQueue.takeFirst();
        if (m_snapshotLoadedCategories.contains(category) || m_snapshotCategoryCounts.value(category) <= 0) {
            continue;
        }
        m_snapshotFindingPages.begin(m_analysisId, category, m_revision);
        queryFindings(category);
        return;
    }
    refreshSelectedFindings();
    m_widget->setEngineMessage(tr("Ready"));
}
void ProseController::prioritizeSnapshotCategory(const QString &category)
{
    if (m_analysisId.isEmpty() || category.isEmpty()) {
        return;
    }
    m_diagnostics.clear();
    m_snapshotLoadedCategories.clear();
    if (!m_findingRequestId.isEmpty()) {
        m_engine->cancel(m_findingRequestId);
        m_requests.remove(m_findingRequestId);
        m_findingRequestId.clear();
    }
    m_snapshotFindingPages.reset();
    refreshSelectedFindings();
    if (m_snapshotCategoryCounts.value(category) > 0) {
        m_snapshotFindingPages.begin(m_analysisId, category, m_revision);
        queryFindings(category);
    }
}
void ProseController::resetSnapshotLoading()
{
    m_overlayApplyTimer.stop();
    m_overlayPages.clear();
    m_currentOverlayPage = {};
    m_currentOverlayIndex = 0;
    m_snapshotCategoryQueue.clear();
    m_snapshotLoadedCategories.clear();
    if (!m_findingRequestId.isEmpty()) {
        m_engine->cancel(m_findingRequestId);
        m_requests.remove(m_findingRequestId);
    }
    m_findingRequestId.clear();
    m_snapshotFindingPages.reset();
    if (!m_overlayRequestId.isEmpty()) {
        m_engine->cancel(m_overlayRequestId);
        m_requests.remove(m_overlayRequestId);
    }
    m_overlayRequestId.clear();
    m_overlayCursor.clear();
    m_overlayRequestInFlight = false;
    m_overlayHasMore = false;
    m_overlayBaselineFormats.clear();
    m_pendingOverlayUpdates.clear();
    m_overlayDiffPending = false;
}
void ProseController::refreshSelectedFindings(bool hasMore, bool appendPage)
{
    QList<ProseDiagnostic> visible;
    for (const ProseDiagnostic &diagnostic : std::as_const(m_diagnostics)) {
        if (diagnosticVisible(diagnostic)) {
            visible.append(diagnostic);
        }
    }
    const QString selected = m_widget->selectedCategory();
    m_widget->setDiagnostics(visible, hasMore, m_snapshotCategoryCounts.value(selected), appendPage);
    m_widget->setCategoryCounts(m_snapshotCategoryCounts);
}
QStringList ProseController::analyzersForCategory(const QString &category) const
{
    static const QHash<QString, QStringList> analyzers = {
        {QStringLiteral("general_rules"), {QStringLiteral("profile_patterns")}},
        {QStringLiteral("possible_adverbs"), {QStringLiteral("possible_adverbs")}},
        {QStringLiteral("possible_adjectives"), {QStringLiteral("possible_adjectives")}},
        {QStringLiteral("possible_verbs"), {QStringLiteral("possible_verbs")}},
        {QStringLiteral("filter_words"), {QStringLiteral("filter_words")}},
        {QStringLiteral("cliches"), {QStringLiteral("cliches"), QStringLiteral("rules_library")}},
        {QStringLiteral("formulaic_patterns"),
         {QStringLiteral("slop_score"), QStringLiteral("binary_contrast"), QStringLiteral("negative_listing"), QStringLiteral("triad_cadence")}},
        {QStringLiteral("repetition_rhythm"), {QStringLiteral("rhythm"), QStringLiteral("stylometry"), QStringLiteral("calibration")}},
        {QStringLiteral("body_cinematic"), {QStringLiteral("body_cliches"), QStringLiteral("cinematic_fog")}},
        {QStringLiteral("abstraction_agency"), {QStringLiteral("false_agency"), QStringLiteral("vague_abstracts")}},
        {QStringLiteral("metaphor_texture"), {QStringLiteral("metaphor_density"), QStringLiteral("concrete_anchor")}},
        {QStringLiteral("repetition"), {QStringLiteral("repetition")}},
        {QStringLiteral("grammar_mechanics"), {QStringLiteral("grammar_mechanics")}},
    };
    return analyzers.value(category);
}
QHash<QString, int> ProseController::categoryCounts(const QJsonObject &countsByAnalyzer) const
{
    QHash<QString, int> counts;
    for (auto iterator = countsByAnalyzer.constBegin(); iterator != countsByAnalyzer.constEnd(); ++iterator) {
        counts[lensCategory(iterator.key())] += iterator.value().toInt();
    }
    return counts;
}
void ProseController::applyHighlights(bool updateWidget)
{
    if (!m_analysisId.isEmpty() && m_engine->supportsOperation(QStringLiteral("query_overlay_spans"))) {
        restartOverlayHydration();
        if (updateWidget) {
            refreshSelectedFindings(m_snapshotFindingPages.active);
        }
        return;
    }
    auto *overlays = m_editor->textFormatOverlayController();
    QHash<int, QList<QTextLayout::FormatRange>> blockFormats;
    QList<ProseDiagnostic> visibleDiagnostics;
    for (const ProseDiagnostic &diagnostic : std::as_const(m_diagnostics)) {
        if (!diagnosticVisible(diagnostic)) {
            continue;
        }
        visibleDiagnostics.append(diagnostic);
        // Paint the primary span plus any related occurrences (e.g., the
        // earlier half of a repetition pair) with identical formatting.
        const auto paintSpan = [this, &diagnostic, &blockFormats](int spanStart, int spanEnd) {
            QTextBlock block = m_editor->document()->findBlock(spanStart);
            while (block.isValid() && block.position() < spanEnd) {
                const int rangeStart = qMax(spanStart, block.position());
                const int rangeEnd = qMin(spanEnd, block.position() + block.length() - 1);
                if (rangeEnd > rangeStart) {
                    QTextLayout::FormatRange formatRange;
                    formatRange.start = rangeStart - block.position();
                    formatRange.length = rangeEnd - rangeStart;
                    const QString decoration = m_decorations.value(diagnostic.category, QStringLiteral("background"));
                    if (decoration != QStringLiteral("underline")) {
                        QColor background = categoryColor(diagnostic.category);
                        background.setAlpha(72);
                        formatRange.format.setBackground(background);
                    }
                    formatRange.format.setToolTip(
                        QStringLiteral("%1\n%2").arg(m_widget->categoryFindingLabel(diagnostic.category), m_widget->categoryDescription(diagnostic.category)));
                    int priority = m_categoryPriorities.value(diagnostic.category, 100);
                    if (diagnostic.level == QStringLiteral("context_flag")) {
                        priority += 100;
                    } else if (diagnostic.level == QStringLiteral("strong_flag")) {
                        priority += 200;
                    } else if (diagnostic.level == QStringLiteral("hard_fail")) {
                        priority += 300;
                    }
                    TextFormatOverlayController::setPriority(formatRange.format, priority);
                    if (decoration != QStringLiteral("background") || diagnostic.level == QStringLiteral("hard_fail")) {
                        formatRange.format.setUnderlineColor(categoryColor(diagnostic.category));
                        formatRange.format.setUnderlineStyle(QTextCharFormat::SingleUnderline);
                    }
                    blockFormats[block.position()].append(formatRange);
                }
                block = block.next();
            }
        };
        paintSpan(diagnostic.start, diagnostic.end);
        for (const auto &extra : diagnostic.extraSpans) {
            paintSpan(extra.first, extra.second);
        }
    }
    overlays->replaceChannelFormats(ProseChannel, blockFormats);
    if (updateWidget) {
        m_widget->setDiagnostics(visibleDiagnostics);
        QHash<QString, int> counts;
        for (const ProseDiagnostic &diagnostic : std::as_const(visibleDiagnostics)) {
            counts[diagnostic.category] += 1;
        }
        m_widget->setCategoryCounts(counts);
    }
}
void ProseController::clearObservations()
{
    m_diagnostics.clear();
    m_snapshotOverlayFormats.clear();
    m_overlayBaselineFormats.clear();
    m_pendingOverlayUpdates.clear();
    m_overlayDiffPending = false;
    m_overlaySuppressions.exactSpans.clear();
    m_overlayApplyTimer.stop();
    m_overlayPages.clear();
    m_currentOverlayPage = {};
    m_currentOverlayIndex = 0;
#ifdef THOTHPAD_INSTRUMENTATION
    ProseInstrumentation::instance()->recordClearChannel(ProseChannel);
#endif
    m_editor->textFormatOverlayController()->clearChannel(ProseChannel);
    m_widget->setDiagnostics({});
    m_widget->setCategoryCounts({});
}
// Terminal status for a review that ends before any engine request is sent.
// Without it the Scan button keeps its "Scanning..." busy state because no
// engine response ever arrives to clear it; the widget clears busy on any
// non-ellipsis status message.
void ProseController::reportReviewNotStarted(const QString &message)
{
    m_widget->setEngineMessage(message);
}
void ProseController::openModelSettings()
{
    ProviderSettingsDialog dialog(m_credentials, m_widget);
    dialog.exec();
}
void ProseController::openGrammarSettings()
{
    GrammarSettingsDialog dialog(m_credentials, m_widget);
    if (dialog.exec() == QDialog::Accepted && m_widget->mode() == ProseAwarenessWidget::Mode::Live) {
        requestAnalysis(true, true);
    }
}
void ProseController::openPerformanceSettings()
{
    PerformanceSettingsDialog dialog(m_widget);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }
    const PerformancePolicy policy = PerformancePolicy::load();
    m_idleTimer.setInterval(policy.analysisDelayMs);
    m_overlayBudgetMs = policy.overlayBudgetMs;
    m_liveTimer.stop();
    m_idleTimer.stop();
    clearObservations();
    m_engine->stop();
    m_engine->start();
}
void ProseController::deleteDiagnostic(const ProseDiagnostic &diagnostic)
{
    if (!diagnostic.ruleId.isEmpty()) {
        deleteDiagnostics({diagnostic});
    }
}
void ProseController::deleteCategory(const QString &category, const QString &label)
{
    if ((m_snapshotFindingPages.active || !m_findingRequestId.isEmpty()) && m_snapshotFindingPages.category == category) {
        m_widget->setEngineMessage(tr("Finish loading this lens before deleting all."));
        return;
    }
    QList<ProseDiagnostic> matches;
    for (const ProseDiagnostic &diagnostic : std::as_const(m_diagnostics)) {
        if (diagnostic.category == category && diagnosticVisible(diagnostic)) {
            matches.append(diagnostic);
        }
    }
    if (matches.isEmpty()) {
        return;
    }
    const auto answer =
        MessageBoxHelper::question(m_widget,
                                   tr("Delete all in lens?"),
                                   tr("Delete all %1 %2 observations from the manuscript?\n\nThis is one undoable edit.").arg(matches.size()).arg(label),
                                   QMessageBox::Yes | QMessageBox::No,
                                   QMessageBox::No);
    if (answer == QMessageBox::Yes) {
        deleteDiagnostics(matches);
    }
}
void ProseController::deleteDiagnostics(const QList<ProseDiagnostic> &diagnostics)
{
    const QString text = m_editor->document()->toPlainText();
    QList<QPair<int, int>> ranges;
    for (const ProseDiagnostic &diagnostic : diagnostics) {
        if (diagnostic.start < 0 || diagnostic.end <= diagnostic.start || diagnostic.end > text.size()) {
            continue;
        }
        const QString current = text.mid(diagnostic.start, diagnostic.end - diagnostic.start);
        if (current.simplified() != diagnostic.excerpt.simplified()) {
            continue;
        }
        int start = diagnostic.start;
        int end = diagnostic.end;
        if (end < text.size() && text.at(end).isSpace() && text.at(end) != QLatin1Char('\n') && text.at(end) != QLatin1Char('\r')) {
            ++end;
        } else if (start > 0 && text.at(start - 1).isSpace() && text.at(start - 1) != QLatin1Char('\n') && text.at(start - 1) != QLatin1Char('\r')) {
            --start;
        }
        ranges.append({start, end});
    }
    if (ranges.isEmpty()) {
        MessageBoxHelper::information(m_widget,
                                      tr("Observation is out of date"),
                                      tr("The highlighted text changed. Review the document again before deleting it."));
        return;
    }
    std::sort(ranges.begin(), ranges.end(), [](const auto &left, const auto &right) {
        return left.first < right.first || (left.first == right.first && left.second < right.second);
    });
    QList<QPair<int, int>> merged;
    for (const auto &range : std::as_const(ranges)) {
        if (!merged.isEmpty() && range.first <= merged.last().second) {
            merged.last().second = qMax(merged.last().second, range.second);
        } else {
            merged.append(range);
        }
    }
    QTextCursor cursor(m_editor->document());
    cursor.beginEditBlock();
    for (auto iterator = merged.crbegin(); iterator != merged.crend(); ++iterator) {
        cursor.setPosition(iterator->first);
        cursor.setPosition(iterator->second, QTextCursor::KeepAnchor);
        cursor.removeSelectedText();
    }
    cursor.endEditBlock();
    cursor.setPosition(merged.first().first);
    m_editor->setTextCursor(cursor);
}
void ProseController::importProfile()
{
    const QString path = QFileDialog::getOpenFileName(m_widget, tr("Import Prose Profile"), QString(), tr("JSON files (*.json)"));
    if (path.isEmpty()) {
        return;
    }
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly) || file.size() > 262144) {
        MessageBoxHelper::warning(m_widget, tr("Import failed"), tr("The profile could not be read or exceeds 256 KiB."));
        return;
    }
    QJsonParseError error;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &error);
    if (error.error != QJsonParseError::NoError || !document.isObject()) {
        MessageBoxHelper::warning(m_widget, tr("Import failed"), tr("The selected file is not a valid JSON profile."));
        return;
    }
    QJsonObject profile = document.object();
    QString name = profile.value(QStringLiteral("name")).toString().trimmed();
    if (name.isEmpty()) {
        name = QFileInfo(path).completeBaseName();
        profile.insert(QStringLiteral("name"), name);
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("name"), name);
    payload.insert(QStringLiteral("profile"), profile);
    const QString requestId = m_engine->send(QStringLiteral("import_profile"), payload);
    if (!requestId.isEmpty()) {
        m_requests.insert(requestId,
                          {QStringLiteral("import_profile"), m_revision, 0, 0, QString::fromUtf8(QJsonDocument(profile).toJson(QJsonDocument::Compact))});
    }
}
void ProseController::exportProfile()
{
    const QString name = m_widget->profile();
    if (name.isEmpty()) {
        return;
    }
    const QString path = QFileDialog::getSaveFileName(m_widget, tr("Export Prose Profile"), name + QStringLiteral(".json"), tr("JSON files (*.json)"));
    if (path.isEmpty()) {
        return;
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("name"), name);
    const QString requestId = m_engine->send(QStringLiteral("export_profile"), payload);
    if (!requestId.isEmpty()) {
        m_requests.insert(requestId, {QStringLiteral("export_profile"), m_revision, 0, 0, path});
    }
}
QString ProseController::normalizedPhrase(const QString &text) const
{
    QString normalized = text.simplified().toCaseFolded();
    normalized.remove(QRegularExpression(QStringLiteral(R"(^[\p{P}\p{S}]+|[\p{P}\p{S}]+$)")));
    return normalized;
}
QString ProseController::profileSettingsPrefix() const
{
    return profileSettingsPrefix(m_widget->profile());
}
QString ProseController::profileSettingsPrefix(const QString &profileName) const
{
    QString profile = profileName.trimmed();
    profile.replace(QRegularExpression(QStringLiteral("[^A-Za-z0-9_.-]")), QStringLiteral("_"));
    if (profile.isEmpty()) {
        profile = QStringLiteral("creative-default");
    }
    return QStringLiteral("prose/profiles/%1").arg(profile);
}
void ProseController::requestProfilePresentation()
{
    const QString name = m_widget->profile().isEmpty() ? QStringLiteral("creative-default") : m_widget->profile();
    if (!m_engine->isReady()) {
        return;
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("name"), name);
    const QString requestId = m_engine->send(QStringLiteral("get_profile"), payload);
    if (!requestId.isEmpty()) {
        m_requests.insert(requestId, {QStringLiteral("load_profile_presentation"), m_revision, 0, 0, name});
    }
}
void ProseController::scheduleLensProfileSave()
{
    m_lensPresentationDirty = true;
    m_profileSaveTimer.start();
}
QJsonObject ProseController::lensProfileSettings() const
{
    if (!m_baseLenses.isEmpty()) {
        return m_baseLenses;
    }
    QJsonObject lenses;
    for (auto iterator = m_colors.cbegin(); iterator != m_colors.cend(); ++iterator) {
        QJsonObject lens;
        const bool enabled = m_enabledCategories.contains(iterator.key());
        lens.insert(QStringLiteral("enabled"), enabled);
        lens.insert(QStringLiteral("color"), iterator.value().name());
        lens.insert(QStringLiteral("mode"), m_categoryModes.value(iterator.key(), QStringLiteral("live")));
        lens.insert(QStringLiteral("priority"), m_categoryPriorities.value(iterator.key(), 100));
        lens.insert(QStringLiteral("decoration"), m_decorations.value(iterator.key(), QStringLiteral("background")));
        lenses.insert(iterator.key(), lens);
    }
    return lenses;
}
void ProseController::applyLensProfileSettings(const QString &profile, const QJsonObject &lenses)
{
    if (lenses.isEmpty()) {
        return;
    }
    QSettings settings;
    const QString prefix = profileSettingsPrefix(profile) + QStringLiteral("/lenses/");
    for (auto iterator = lenses.constBegin(); iterator != lenses.constEnd(); ++iterator) {
        if (!iterator.value().isObject() || !m_colors.contains(iterator.key())) {
            continue;
        }
        const QJsonObject lens = iterator.value().toObject();
        if (lens.value(QStringLiteral("enabled")).isBool()) {
            settings.setValue(prefix + iterator.key() + QStringLiteral("/enabled"), lens.value(QStringLiteral("enabled")).toBool());
        }
        const QString mode = lens.value(QStringLiteral("mode")).toString();
        if (mode == QStringLiteral("live") || mode == QStringLiteral("idle") || mode == QStringLiteral("report")) {
            settings.setValue(prefix + iterator.key() + QStringLiteral("/mode"), mode);
        }
        const QColor color(lens.value(QStringLiteral("color")).toString());
        if (color.isValid()) {
            settings.setValue(prefix + iterator.key() + QStringLiteral("/color"), color.name());
        }
        const QString decoration = lens.value(QStringLiteral("decoration")).toString();
        if (decoration == QStringLiteral("background") || decoration == QStringLiteral("underline") || decoration == QStringLiteral("background_underline")) {
            settings.setValue(prefix + iterator.key() + QStringLiteral("/decoration"), decoration);
        }
        if (lens.value(QStringLiteral("priority")).isDouble()) {
            settings.setValue(prefix + iterator.key() + QStringLiteral("/priority"), qBound(0, lens.value(QStringLiteral("priority")).toInt(), 1000));
        }
    }
    if (profile == m_widget->profile() || (profile == QStringLiteral("creative-default") && m_widget->profile().isEmpty())) {
        loadLensSettings();
    }
}
void ProseController::loadLensSettings()
{
    static const QHash<QString, QColor> defaults = {
        {QStringLiteral("general_rules"), QColor("#F87171")},
        {QStringLiteral("possible_adverbs"), QColor("#FACC15")},
        {QStringLiteral("possible_adjectives"), QColor("#C084FC")},
        {QStringLiteral("possible_verbs"), QColor("#34D399")},
        {QStringLiteral("filter_words"), QColor("#FB923C")},
        {QStringLiteral("cliches"), QColor("#60A5FA")},
        {QStringLiteral("formulaic_patterns"), QColor("#F472B6")},
        {QStringLiteral("repetition_rhythm"), QColor("#22D3EE")},
        {QStringLiteral("body_cinematic"), QColor("#B66E7D")},
        {QStringLiteral("abstraction_agency"), QColor("#6096A4")},
        {QStringLiteral("metaphor_texture"), QColor("#739764")},
        {QStringLiteral("repetition"), QColor("#A76D87")},
        {QStringLiteral("grammar_mechanics"), QColor("#B8685F")},
    };
    static const QSet<QString> liveDefaults = {
        QStringLiteral("general_rules"),
        QStringLiteral("possible_adverbs"),
        QStringLiteral("possible_adjectives"),
        QStringLiteral("possible_verbs"),
        QStringLiteral("filter_words"),
        QStringLiteral("cliches"),
        QStringLiteral("formulaic_patterns"),
        QStringLiteral("body_cinematic"),
        QStringLiteral("repetition"),
        QStringLiteral("grammar_mechanics"),
    };
    static const QSet<QString> idleDefaults = {
        QStringLiteral("repetition_rhythm"),
        QStringLiteral("abstraction_agency"),
    };
    const QString prefix = profileSettingsPrefix() + QStringLiteral("/lenses/");
    QSettings settings;
    const bool migrateInventoryDecoration = settings.value(QStringLiteral("prose/lensPresentationVersion"), 1).toInt() < 2;
    const QStringList disabledRules = settings.value(profileSettingsPrefix() + QStringLiteral("/disabledRules")).toStringList();
    m_disabledRules = QSet<QString>(disabledRules.cbegin(), disabledRules.cend());
    const QStringList allowedPhrases = settings.value(profileSettingsPrefix() + QStringLiteral("/allowedPhrases")).toStringList();
    m_allowedPhrases = QSet<QString>(allowedPhrases.cbegin(), allowedPhrases.cend());
    const QStringList ignoredOccurrences = settings.value(profileSettingsPrefix() + QStringLiteral("/ignoredOccurrences")).toStringList();
    m_ignoredOccurrences = QSet<QString>(ignoredOccurrences.cbegin(), ignoredOccurrences.cend());
    m_enabledCategories.clear();
    m_decorations.clear();
    m_categoryModes.clear();
    m_categoryPriorities.clear();
    for (auto iterator = m_colors.begin(); iterator != m_colors.end(); ++iterator) {
        const QString category = iterator.key();
        const bool enabledByDefault = true;
        const bool enabled = settings.value(prefix + category + QStringLiteral("/enabled"), enabledByDefault).toBool();
        const QColor stored(settings.value(prefix + category + QStringLiteral("/color"), defaults.value(category, iterator.value()).name()).toString());
        iterator.value() = stored.isValid() ? stored : defaults.value(category, iterator.value());
        if (enabled) {
            m_enabledCategories.insert(category);
        }
        const bool inventoryLens =
            category == QStringLiteral("possible_adverbs") || category == QStringLiteral("possible_adjectives") || category == QStringLiteral("possible_verbs");
        QString decoration =
            settings
                .value(prefix + category + QStringLiteral("/decoration"),
                       category == QStringLiteral("grammar_mechanics") || inventoryLens ? QStringLiteral("underline") : QStringLiteral("background"))
                .toString();
        if (migrateInventoryDecoration && inventoryLens) {
            decoration = QStringLiteral("underline");
            settings.setValue(prefix + category + QStringLiteral("/decoration"), decoration);
        }
        m_decorations.insert(category,
                             decoration == QStringLiteral("underline") || decoration == QStringLiteral("background_underline") ? decoration
                                                                                                                               : QStringLiteral("background"));
        m_categoryPriorities.insert(category, qBound(0, settings.value(prefix + category + QStringLiteral("/priority"), 100).toInt(), 1000));
        const QString defaultMode = liveDefaults.contains(category) ? QStringLiteral("live")
            : idleDefaults.contains(category)                       ? QStringLiteral("idle")
                                                                    : QStringLiteral("report");
        const QString mode = settings.value(prefix + category + QStringLiteral("/mode"), defaultMode).toString();
        m_categoryModes.insert(category,
                               mode == QStringLiteral("live") || mode == QStringLiteral("idle") || mode == QStringLiteral("report") ? mode : defaultMode);
        m_widget->setCategoryState(category, enabled, iterator.value());
        m_widget->setCategoryMode(category, m_categoryModes.value(category));
    }
    settings.setValue(QStringLiteral("prose/lensPresentationVersion"), 2);
    m_baseLenses = {};
    for (auto iterator = m_colors.cbegin(); iterator != m_colors.cend(); ++iterator) {
        QJsonObject lens;
        lens.insert(QStringLiteral("enabled"), m_enabledCategories.contains(iterator.key()));
        lens.insert(QStringLiteral("color"), iterator.value().name());
        lens.insert(QStringLiteral("mode"), m_categoryModes.value(iterator.key()));
        lens.insert(QStringLiteral("priority"), m_categoryPriorities.value(iterator.key(), 100));
        lens.insert(QStringLiteral("decoration"), m_decorations.value(iterator.key(), QStringLiteral("background")));
        m_baseLenses.insert(iterator.key(), lens);
    }
    const QJsonObject folderLenses = currentFolderProfileOverrides().value(QStringLiteral("lenses")).toObject();
    for (auto iterator = folderLenses.constBegin(); iterator != folderLenses.constEnd(); ++iterator) {
        if (!m_colors.contains(iterator.key()) || !iterator.value().isObject()) {
            continue;
        }
        const QJsonObject lens = iterator.value().toObject();
        if (lens.value(QStringLiteral("enabled")).isBool()) {
            if (lens.value(QStringLiteral("enabled")).toBool()) {
                m_enabledCategories.insert(iterator.key());
            } else {
                m_enabledCategories.remove(iterator.key());
            }
        }
        const QColor color(lens.value(QStringLiteral("color")).toString());
        if (color.isValid()) {
            m_colors.insert(iterator.key(), color);
        }
        const QString mode = lens.value(QStringLiteral("mode")).toString();
        if (mode == QStringLiteral("live") || mode == QStringLiteral("idle") || mode == QStringLiteral("report")) {
            m_categoryModes.insert(iterator.key(), mode);
        }
        const QString decoration = lens.value(QStringLiteral("decoration")).toString();
        if (decoration == QStringLiteral("background") || decoration == QStringLiteral("underline") || decoration == QStringLiteral("background_underline")) {
            m_decorations.insert(iterator.key(), decoration);
        }
        if (lens.value(QStringLiteral("priority")).isDouble()) {
            m_categoryPriorities.insert(iterator.key(), qBound(0, lens.value(QStringLiteral("priority")).toInt(), 1000));
        }
        m_widget->setCategoryState(iterator.key(), m_enabledCategories.contains(iterator.key()), m_colors.value(iterator.key()));
        m_widget->setCategoryMode(iterator.key(), m_categoryModes.value(iterator.key()));
    }
    applyHighlights();
}
void ProseController::loadLockedFacts()
{
    m_widget->setLockedFacts(QSettings().value(profileSettingsPrefix() + QStringLiteral("/lockedFacts")).toStringList());
}
QString ProseController::occurrenceIgnoreKey(const ProseDiagnostic &diagnostic) const
{
    const int contextStart = qMax(0, diagnostic.start - 48);
    const int contextEnd = qMin(m_editor->document()->characterCount() - 1, diagnostic.end + 48);
    QTextCursor cursor(m_editor->document());
    cursor.setPosition(contextStart);
    cursor.setPosition(contextEnd, QTextCursor::KeepAnchor);
    QString context = cursor.selectedText();
    context.replace(QChar::ParagraphSeparator, QLatin1Char('\n'));
    const QString material = diagnostic.ruleId + QLatin1Char('\n') + normalizedPhrase(diagnostic.excerpt) + QLatin1Char('\n') + context.simplified();
    return QString::fromLatin1(QCryptographicHash::hash(material.toUtf8(), QCryptographicHash::Sha256).toHex());
}
bool ProseController::isPersistentlyIgnored(const ProseDiagnostic &diagnostic) const
{
    if (diagnostic.excerpt.isEmpty()) {
        return false;
    }
    return m_ignoredOccurrences.contains(occurrenceIgnoreKey(diagnostic));
}
bool ProseController::isAllowedPhrase(const ProseDiagnostic &diagnostic) const
{
    const QString phrase = normalizedPhrase(diagnostic.excerpt);
    if (phrase.isEmpty()) {
        return false;
    }
    return m_allowedPhrases.contains(phrase);
}
void ProseController::ignoreOccurrence(const ProseDiagnostic &diagnostic)
{
    if (diagnostic.ruleId.isEmpty() || diagnostic.excerpt.isEmpty()) {
        return;
    }
    QStringList ignored = QSettings().value(profileSettingsPrefix() + QStringLiteral("/ignoredOccurrences")).toStringList();
    const QString key = occurrenceIgnoreKey(diagnostic);
    if (!ignored.contains(key)) {
        ignored.append(key);
        QSettings().setValue(profileSettingsPrefix() + QStringLiteral("/ignoredOccurrences"), ignored);
        m_ignoredOccurrences.insert(key);
    }
    recordOverlaySuppressions({diagnostic});
    applyHighlights();
}
void ProseController::allowPhrase(const ProseDiagnostic &diagnostic)
{
    const QString phrase = normalizedPhrase(diagnostic.excerpt);
    if (phrase.isEmpty()) {
        return;
    }
    QStringList allowed = QSettings().value(profileSettingsPrefix() + QStringLiteral("/allowedPhrases")).toStringList();
    if (!allowed.contains(phrase)) {
        allowed.append(phrase);
        QSettings().setValue(profileSettingsPrefix() + QStringLiteral("/allowedPhrases"), allowed);
        m_allowedPhrases.insert(phrase);
    }
    recordOverlaySuppressions(m_diagnostics);
    applyHighlights();
}
void ProseController::exportJsonReport()
{
    if (m_lastReport.isEmpty()) {
        MessageBoxHelper::information(m_widget, tr("No report"), tr("Review the document before exporting a report."));
        return;
    }
    const QString path = QFileDialog::getSaveFileName(m_widget, tr("Export Prose Report"), QStringLiteral("prose-report.json"), tr("JSON files (*.json)"));
    if (path.isEmpty()) {
        return;
    }
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly) || file.write(QJsonDocument(m_lastReport).toJson(QJsonDocument::Indented)) < 0 || !file.commit()) {
        MessageBoxHelper::warning(m_widget, tr("Export failed"), file.errorString());
    }
}
void ProseController::exportMarkdownReport()
{
    if (m_lastReport.isEmpty()) {
        MessageBoxHelper::information(m_widget, tr("No report"), tr("Review the document before exporting a report."));
        return;
    }
    const QString path = QFileDialog::getSaveFileName(m_widget, tr("Export Prose Report"), QStringLiteral("prose-report.md"), tr("Markdown files (*.md)"));
    if (path.isEmpty()) {
        return;
    }
    QString markdown = QStringLiteral("# Prose Awareness Report\n\n");
    markdown += tr("Profile: %1\n\n").arg(m_lastReport.value(QStringLiteral("profile")).toString());
    if (m_lastReport.value(QStringLiteral("mode")).toString() == QStringLiteral("manuscript")) {
        const QJsonObject stats = m_lastReport.value(QStringLiteral("manuscript_stats")).toObject();
        markdown += tr("Score: %1\n\n").arg(m_lastReport.value(QStringLiteral("score_before")).toDouble());
        markdown += tr("Documents: %1  \nWords: %2\n\n")
                        .arg(m_lastReport.value(QStringLiteral("document_count")).toInt())
                        .arg(stats.value(QStringLiteral("word_count")).toInt());
        const QJsonObject repetition = m_lastReport.value(QStringLiteral("repetition")).toObject();
        markdown += QStringLiteral("## Repeated Words\n\n");
        for (const QJsonValue &value : repetition.value(QStringLiteral("repeated_words")).toArray()) {
            const QJsonObject item = value.toObject();
            markdown += tr("- `%1`: %2 uses across %3 files\n")
                            .arg(item.value(QStringLiteral("lemma")).toString())
                            .arg(item.value(QStringLiteral("count")).toInt())
                            .arg(item.value(QStringLiteral("affected_files")).toInt());
        }
        markdown += QStringLiteral("\n## Repeated Phrases\n\n");
        for (const QJsonValue &value : repetition.value(QStringLiteral("repeated_phrases")).toArray()) {
            const QJsonObject item = value.toObject();
            markdown += tr("- `%1`: %2 uses across %3 files\n")
                            .arg(item.value(QStringLiteral("phrase")).toString())
                            .arg(item.value(QStringLiteral("count")).toInt())
                            .arg(item.value(QStringLiteral("affected_files")).toInt());
        }
        const auto appendPatternCounts = [&markdown](const QString &heading, const QJsonArray &items, const QString &textKey) {
            markdown += QStringLiteral("\n## %1\n\n").arg(heading);
            for (const QJsonValue &value : items) {
                const QJsonObject item = value.toObject();
                markdown += QStringLiteral("- `%1`: %2 uses").arg(item.value(textKey).toString()).arg(item.value(QStringLiteral("count")).toInt());
                if (item.contains(QStringLiteral("affected_files"))) {
                    markdown += tr(" across %1 files").arg(item.value(QStringLiteral("affected_files")).toInt());
                }
                markdown += QLatin1Char('\n');
            }
        };
        appendPatternCounts(tr("Repeated Sentence Openings"),
                            repetition.value(QStringLiteral("repeated_sentence_openings")).toArray(),
                            QStringLiteral("opening"));
        appendPatternCounts(tr("Repeated Paragraph Endings"),
                            repetition.value(QStringLiteral("repeated_paragraph_endings")).toArray(),
                            QStringLiteral("ending"));
        appendPatternCounts(tr("Repeated Image Families"), repetition.value(QStringLiteral("image_families")).toArray(), QStringLiteral("family"));
        markdown += QStringLiteral("\n## Pattern Hotspots\n\n");
        for (const QJsonValue &value : m_lastReport.value(QStringLiteral("pattern_hotspots")).toArray()) {
            const QJsonObject item = value.toObject();
            markdown += tr("- `%1:%2`: %3 matches across %4 files\n")
                            .arg(item.value(QStringLiteral("analyzer")).toString())
                            .arg(item.value(QStringLiteral("type")).toString())
                            .arg(item.value(QStringLiteral("total_matches")).toInt())
                            .arg(item.value(QStringLiteral("affected_files")).toInt());
        }
        markdown += QStringLiteral("\n## Chapters\n\n");
        for (const QJsonValue &value : m_lastReport.value(QStringLiteral("chapters")).toArray()) {
            const QJsonObject chapter = value.toObject();
            markdown += tr("- **%1**: score %2, %3 words, %4 observations\n")
                            .arg(chapter.value(QStringLiteral("name")).toString())
                            .arg(chapter.value(QStringLiteral("score")).toDouble())
                            .arg(chapter.value(QStringLiteral("word_count")).toInt())
                            .arg(chapter.value(QStringLiteral("flags")).toArray().size());
        }
        const QJsonObject timeline = m_lastReport.value(QStringLiteral("quality_timeline")).toObject();
        const QJsonArray timelineRuns = timeline.value(QStringLiteral("runs")).toArray();
        if (!timelineRuns.isEmpty()) {
            markdown += QStringLiteral("\n## Quality Timeline\n\n");
            markdown += QStringLiteral("| Date | Score | Words | Documents |\n|---|---|---|---|\n");
            for (const QJsonValue &value : timelineRuns) {
                const QJsonObject run = value.toObject();
                markdown += QStringLiteral("| %1 | %2 | %3 | %4 |\n")
                                .arg(run.value(QStringLiteral("created_at")).toString())
                                .arg(run.value(QStringLiteral("score")).toDouble())
                                .arg(run.value(QStringLiteral("word_count")).toInt())
                                .arg(run.value(QStringLiteral("document_count")).toInt());
            }
        }
        const QJsonObject comparison = m_lastReport.value(QStringLiteral("genre_comparison")).toObject();
        if (!comparison.isEmpty()) {
            const QJsonObject baselines = comparison.value(QStringLiteral("baselines")).toObject();
            const QJsonObject current = comparison.value(QStringLiteral("current_densities")).toObject();
            markdown += tr("\n## Genre Comparison (%1)\n\n").arg(comparison.value(QStringLiteral("calibration")).toString());
            markdown += QStringLiteral("| Lens | You / 1k words | Genre baseline |\n|---|---|---|\n");
            for (const QString &analyzer : baselines.keys()) {
                markdown += QStringLiteral("| %1 | %2 | %3 |\n")
                                .arg(analyzer)
                                .arg(current.value(analyzer).toDouble(), 0, 'f', 2)
                                .arg(baselines.value(analyzer).toDouble(), 0, 'f', 2);
            }
        }
    } else {
        markdown += tr("Score: %1\n\n").arg(m_lastReport.value(QStringLiteral("score")).toDouble());
        markdown += QStringLiteral("## Observations\n\n");
        for (const QJsonValue &value : m_lastReport.value(QStringLiteral("diagnostics")).toArray()) {
            const ProseDiagnostic diagnostic = ProseDiagnostic::fromJson(value.toObject());
            markdown += QStringLiteral("- **%1**: `%2` - %3\n").arg(diagnostic.ruleId, diagnostic.excerpt, diagnostic.suggestion);
        }
    }
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly) || file.write(markdown.toUtf8()) < 0 || !file.commit()) {
        MessageBoxHelper::warning(m_widget, tr("Export failed"), file.errorString());
    }
}
QJsonObject ProseController::providerSettings() const
{
    QSettings settings;
    settings.beginGroup(QStringLiteral("prose/provider"));
    QJsonObject provider;
    provider.insert(QStringLiteral("provider"), settings.value(QStringLiteral("provider"), QStringLiteral("openai_compatible")).toString());
    provider.insert(QStringLiteral("base_url"), settings.value(QStringLiteral("endpoint"), QStringLiteral("http://127.0.0.1:1234/v1")).toString());
    provider.insert(QStringLiteral("model"), settings.value(QStringLiteral("model"), QStringLiteral("local-model")).toString());
    provider.insert(QStringLiteral("temperature"), settings.value(QStringLiteral("temperature"), 0.7).toDouble());
    provider.insert(QStringLiteral("max_tokens"), settings.value(QStringLiteral("max_tokens"), 4096).toInt());
    provider.insert(QStringLiteral("timeout"), settings.value(QStringLiteral("timeout"), 180).toInt());
    settings.endGroup();
    return provider;
}
QString ProseController::providerCredentialId(const QJsonObject &provider) const
{
    const QUrl url(provider.value(QStringLiteral("base_url")).toString());
    const QString origin = QStringLiteral("%1://%2:%3")
                               .arg(url.scheme().toLower(), url.host().toLower())
                               .arg(url.port(url.scheme().compare(QStringLiteral("https"), Qt::CaseInsensitive) == 0 ? 443 : 80));
    const QString endpointId = QString::fromLatin1(QCryptographicHash::hash(origin.toUtf8(), QCryptographicHash::Sha256).toHex().left(16));
    return QStringLiteral("provider/%1/%2/%3")
        .arg(provider.value(QStringLiteral("provider")).toString(), endpointId, provider.value(QStringLiteral("model")).toString());
}
void ProseController::requestRevision(const ProseDiagnostic &diagnostic)
{
    if (!m_engine->isReady() || diagnostic.end <= diagnostic.start) {
        return;
    }
    const QJsonObject provider = providerSettings();
    const QString scope = m_editor->document()->toPlainText().mid(diagnostic.start, diagnostic.end - diagnostic.start);
    if (scope.isEmpty()) {
        return;
    }
    PendingRevision pending;
    pending.diagnostic = diagnostic;
    pending.provider = provider;
    pending.profileName = m_widget->profile().isEmpty() ? QStringLiteral("creative-default") : m_widget->profile();
    pending.folderOverrides = currentFolderProfileOverrides();
    pending.credentialId = providerCredentialId(provider);
    pending.passes = qBound(1, QSettings().value(QStringLiteral("prose/provider/passes"), 1).toInt(), 5);
    pending.preserve = QJsonArray::fromStringList(m_widget->lockedFacts());
    pending.sourceText = scope;
    pending.sourceHash = QString::fromLatin1(QCryptographicHash::hash(scope.toUtf8(), QCryptographicHash::Sha256).toHex());
    pending.revision = m_revision;
    QJsonObject payload;
    payload.insert(QStringLiteral("name"), pending.profileName);
    const QString requestId = m_engine->send(QStringLiteral("get_profile"), payload);
    if (requestId.isEmpty()) {
        return;
    }
    pending.profileRequestId = requestId;
    m_pendingRevision = pending;
    m_requests.insert(requestId, {QStringLiteral("prepare_rewrite"), m_revision, 0, 0});
}
void ProseController::confirmPendingRevision(const QJsonObject &profile)
{
    if (m_pendingRevision.sourceText.isEmpty() || m_pendingRevision.revision != m_revision) {
        m_pendingRevision = {};
        return;
    }
    m_pendingRevision.profile = profile;
    const QString endpoint = m_pendingRevision.provider.value(QStringLiteral("base_url")).toString();
    const QString profileScope = QString::fromUtf8(QJsonDocument(profile).toJson(QJsonDocument::Indented));
    const QString overrideScope = m_pendingRevision.folderOverrides.isEmpty()
        ? tr("None")
        : QString::fromUtf8(QJsonDocument(m_pendingRevision.folderOverrides).toJson(QJsonDocument::Indented));
    const QString lockedScope =
        m_pendingRevision.preserve.isEmpty() ? tr("None") : QString::fromUtf8(QJsonDocument(m_pendingRevision.preserve).toJson(QJsonDocument::Indented));
    const auto answer =
        MessageBoxHelper::question(m_widget,
                                   tr("Send text to model"),
                                   tr("Provider: %1\nEndpoint: %2\nModel: %3\nPasses: %4\n\nSelected prose:\n%5\n\nProfile instructions and voice "
                                      "data:\n%6\n\nFolder overrides:\n%7\n\nLocked facts:\n%8")
                                       .arg(m_pendingRevision.provider.value(QStringLiteral("provider")).toString(),
                                            endpoint,
                                            m_pendingRevision.provider.value(QStringLiteral("model")).toString(),
                                            QString::number(m_pendingRevision.passes),
                                            m_pendingRevision.sourceText,
                                            profileScope,
                                            overrideScope,
                                            lockedScope));
    if (answer != QMessageBox::Yes) {
        m_pendingRevision = {};
        return;
    }
    const QUrl url(endpoint);
    const QString host = url.host().toLower();
    if (host == QStringLiteral("localhost") || host == QStringLiteral("127.0.0.1") || host == QStringLiteral("::1")) {
        const PendingRevision pending = m_pendingRevision;
        m_pendingRevision = {};
        sendRevision(pending, QString());
        return;
    }
    if (!m_credentials->isAvailable()) {
        m_pendingRevision = {};
        MessageBoxHelper::warning(m_widget, tr("Secure storage unavailable"), tr("This build cannot retrieve a cloud-provider key securely."));
        return;
    }
    m_credentials->read(m_pendingRevision.credentialId);
}
void ProseController::sendRevision(const PendingRevision &pending, const QString &apiKey)
{
    if (pending.revision != m_revision) {
        return;
    }
    const QString currentSource = m_editor->document()->toPlainText().mid(pending.diagnostic.start, pending.diagnostic.end - pending.diagnostic.start);
    const QString currentHash = QString::fromLatin1(QCryptographicHash::hash(currentSource.toUtf8(), QCryptographicHash::Sha256).toHex());
    if (currentSource != pending.sourceText || currentHash != pending.sourceHash) {
        return;
    }
    QJsonObject provider = pending.provider;
    if (!apiKey.isEmpty()) {
        provider.insert(QStringLiteral("api_key"), apiKey);
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("document_id"), m_documentId);
    payload.insert(QStringLiteral("document_revision"), m_revision);
    payload.insert(QStringLiteral("text"), pending.sourceText);
    payload.insert(QStringLiteral("profile"), pending.profileName);
    payload.insert(QStringLiteral("profile_snapshot"), pending.profile);
    if (!pending.folderOverrides.isEmpty()) {
        payload.insert(QStringLiteral("overrides"), pending.folderOverrides);
    }
    payload.insert(QStringLiteral("mode"), QStringLiteral("line_edit"));
    payload.insert(QStringLiteral("passes"), pending.passes);
    if (!pending.preserve.isEmpty()) {
        payload.insert(QStringLiteral("preserve"), pending.preserve);
    }
    payload.insert(QStringLiteral("provider"), provider);
    payload.insert(QStringLiteral("consent"), true);
    payload.insert(QStringLiteral("persist"), false);
    const QString requestId = m_engine->send(QStringLiteral("rewrite"), payload);
    if (!requestId.isEmpty()) {
        m_requests.insert(requestId, {QStringLiteral("rewrite"), m_revision, pending.diagnostic.start, pending.diagnostic.end, pending.sourceText});
    }
}
void ProseController::setMode(ProseAwarenessWidget::Mode mode)
{
    QSettings().setValue(QStringLiteral("prose/mode"), static_cast<int>(mode));
    if (mode == ProseAwarenessWidget::Mode::Off) {
        m_liveTimer.stop();
        m_idleTimer.stop();
        if (!m_liveRequestId.isEmpty()) {
            m_engine->cancel(m_liveRequestId);
        }
        clearObservations();
    } else if (mode == ProseAwarenessWidget::Mode::Live) {
        requestAnalysis(true, true);
    } else {
#ifdef THOTHPAD_INSTRUMENTATION
        ProseInstrumentation::instance()->recordClearChannel(ProseChannel);
#endif
        m_editor->textFormatOverlayController()->clearChannel(ProseChannel);
    }
}
void ProseController::previewFinding(const ProseDiagnostic &diagnostic)
{
    if (diagnostic.end <= diagnostic.start) {
        return;
    }
    QTextCursor cursor(m_editor->document());
    cursor.setPosition(diagnostic.start);
    cursor.setPosition(diagnostic.end, QTextCursor::KeepAnchor);
    m_editor->setTextCursor(cursor);
    m_editor->centerCursor();
    m_widget->selectDiagnostic(diagnostic);
}
void ProseController::navigateTo(const ProseDiagnostic &diagnostic)
{
    previewFinding(diagnostic);
    m_editor->setFocus();
}
void ProseController::navigateNext()
{
    const int position = m_editor->textCursor().selectionEnd();
    for (const ProseDiagnostic &diagnostic : std::as_const(m_diagnostics)) {
        if (diagnostic.start > position && diagnosticVisible(diagnostic)) {
            navigateTo(diagnostic);
            return;
        }
    }
    for (const ProseDiagnostic &diagnostic : std::as_const(m_diagnostics)) {
        if (diagnosticVisible(diagnostic)) {
            navigateTo(diagnostic);
            return;
        }
    }
}
void ProseController::navigatePrevious()
{
    const int position = m_editor->textCursor().selectionStart();
    for (auto iterator = m_diagnostics.crbegin(); iterator != m_diagnostics.crend(); ++iterator) {
        if (iterator->end < position && diagnosticVisible(*iterator)) {
            navigateTo(*iterator);
            return;
        }
    }
    for (auto iterator = m_diagnostics.crbegin(); iterator != m_diagnostics.crend(); ++iterator) {
        if (diagnosticVisible(*iterator)) {
            navigateTo(*iterator);
            return;
        }
    }
}
QList<QJsonObject> ProseController::exclusionRanges(const QString &text, int baseOffset) const
{
    QJsonObject markdownSettings = m_activeProfile.value(QStringLiteral("markdown_exclusions")).toObject();
    const QJsonObject folderSettings = currentFolderProfileOverrides().value(QStringLiteral("markdown_exclusions")).toObject();
    for (auto item = folderSettings.constBegin(); item != folderSettings.constEnd(); ++item) {
        markdownSettings.insert(item.key(), item.value());
    }
    return exclusionRangesForSettings(text, baseOffset, markdownSettings);
}
QJsonObject ProseController::currentFolderProfileOverrides() const
{
    const auto *document = qobject_cast<MarkdownDocument *>(m_editor->document());
    if (!document || document->filePath().isEmpty()) {
        return {};
    }
    return folderProfileOverrides(QFileInfo(document->filePath()).absoluteDir());
}
QJsonObject ProseController::analysisOverrides(const QJsonObject &baseOverrides) const
{
    QJsonObject overrides = baseOverrides;
    for (const QString &category : {
             QStringLiteral("possible_adverbs"),
             QStringLiteral("possible_adjectives"),
             QStringLiteral("possible_verbs"),
         }) {
        QJsonObject settings = overrides.value(category).toObject();
        settings.insert(QStringLiteral("enabled"), m_enabledCategories.contains(category));
        overrides.insert(category, settings);
    }
    return overrides;
}
QJsonObject ProseController::folderProfileOverrides(const QDir &directory) const
{
    QString profilePath = directory.filePath(QStringLiteral(".thothpad/prose-profile.json"));
    if (!QFileInfo::exists(profilePath)) {
        profilePath = directory.filePath(QStringLiteral(".writer-suite/prose-profile.json"));
    }
    QFile file(profilePath);
    if (!file.exists() || !file.open(QIODevice::ReadOnly) || file.size() > 262144) {
        return {};
    }
    QJsonParseError error;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &error);
    if (error.error != QJsonParseError::NoError || !document.isObject()) {
        return {};
    }
    static const QSet<QString> allowedKeys = {
        QStringLiteral("register_target"),
        QStringLiteral("reader_profile"),
        QStringLiteral("scene_anchor"),
        QStringLiteral("texture_constraint"),
        QStringLiteral("anti_pattern"),
        QStringLiteral("preserve"),
        QStringLiteral("prefer"),
        QStringLiteral("avoid"),
        QStringLiteral("hard_bans"),
        QStringLiteral("soft_flags"),
        QStringLiteral("analyzer_weights"),
        QStringLiteral("thresholds"),
        QStringLiteral("dialogue_exclusions"),
        QStringLiteral("markdown_exclusions"),
        QStringLiteral("possible_adverbs"),
        QStringLiteral("possible_adjectives"),
        QStringLiteral("possible_verbs"),
        QStringLiteral("filter_words"),
        QStringLiteral("cliches"),
        QStringLiteral("cliche_categories"),
        QStringLiteral("lenses"),
        QStringLiteral("voice_stats"),
        QStringLiteral("voice_fingerprint"),
        QStringLiteral("calibration_profile"),
    };
    QJsonObject overrides;
    const QJsonObject source = document.object();
    for (auto item = source.constBegin(); item != source.constEnd(); ++item) {
        if (allowedKeys.contains(item.key())) {
            overrides.insert(item.key(), item.value());
        }
    }
    return overrides;
}
QString ProseController::lensCategory(const QString &analyzer) const
{
    if (analyzer == QStringLiteral("grammar_mechanics"))
        return QStringLiteral("grammar_mechanics");
    if (analyzer == QStringLiteral("profile_patterns"))
        return QStringLiteral("general_rules");
    if (analyzer == QStringLiteral("possible_adverbs"))
        return QStringLiteral("possible_adverbs");
    if (analyzer == QStringLiteral("possible_adjectives"))
        return QStringLiteral("possible_adjectives");
    if (analyzer == QStringLiteral("possible_verbs"))
        return QStringLiteral("possible_verbs");
    if (analyzer == QStringLiteral("filter_words"))
        return QStringLiteral("filter_words");
    if (analyzer == QStringLiteral("cliches") || analyzer == QStringLiteral("rules_library"))
        return QStringLiteral("cliches");
    if (analyzer == QStringLiteral("body_cliches") || analyzer == QStringLiteral("cinematic_fog"))
        return QStringLiteral("body_cinematic");
    if (analyzer == QStringLiteral("false_agency") || analyzer == QStringLiteral("vague_abstracts"))
        return QStringLiteral("abstraction_agency");
    if (analyzer == QStringLiteral("metaphor_density") || analyzer == QStringLiteral("concrete_anchor"))
        return QStringLiteral("metaphor_texture");
    if (analyzer == QStringLiteral("rhythm") || analyzer == QStringLiteral("stylometry") || analyzer == QStringLiteral("calibration"))
        return QStringLiteral("repetition_rhythm");
    if (analyzer == QStringLiteral("repetition"))
        return QStringLiteral("repetition");
    return QStringLiteral("formulaic_patterns");
}
QColor ProseController::categoryColor(const QString &category) const
{
    return m_colors.value(category, QColor("#60A5FA"));
}
bool ProseController::categoryEnabled(const QString &category) const
{
    return m_enabledCategories.contains(category);
}
bool ProseController::diagnosticVisible(const ProseDiagnostic &diagnostic) const
{
    const QString mode = m_categoryModes.value(diagnostic.category, QStringLiteral("live"));
    const bool laneVisible = mode == QStringLiteral("live") || (mode == QStringLiteral("idle") && m_displayLane != QStringLiteral("live"))
        || (mode == QStringLiteral("report") && m_displayLane == QStringLiteral("report"));
    return categoryEnabled(diagnostic.category) && laneVisible && !m_disabledRules.contains(diagnostic.ruleId) && !m_dismissedIds.contains(diagnostic.id)
        && !isPersistentlyIgnored(diagnostic) && !isAllowedPhrase(diagnostic);
}
}
