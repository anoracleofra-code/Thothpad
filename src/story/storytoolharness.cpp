/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "storytoolharness.h"

#include "agentedittransactionmanager.h"
#include "../documentmanager.h"
#include "../editor/markdowneditor.h"
#include "../prose/proseawarenesswidget.h"
#include "../prose/prosecontroller.h"
#include "../settings/appsettings.h"

#include <algorithm>
#include <limits>

#include <QAction>
#include <QDockWidget>
#include <QJsonValue>
#include <QMainWindow>
#include <QTextBlock>
#include <QTextCursor>
#include <QTextDocument>

#include <KActionCollection>

namespace ghostwriter
{
namespace
{
QJsonObject tool(
    const QString &id,
    const QString &risk,
    const QString &description,
    const QString &arguments = QString())
{
    QJsonObject result;
    result.insert(QStringLiteral("id"), id);
    result.insert(QStringLiteral("risk"), risk);
    result.insert(QStringLiteral("description"), description);
    if (!arguments.isEmpty()) {
        result.insert(QStringLiteral("arguments"), arguments);
    }
    return result;
}

bool strictInteger(const QJsonValue &value, int *result)
{
    if (!value.isDouble()) {
        return false;
    }
    const double number = value.toDouble();
    if (number < static_cast<double>(std::numeric_limits<int>::min())
        || number > static_cast<double>(std::numeric_limits<int>::max())) {
        return false;
    }
    const int integer = static_cast<int>(number);
    if (number != static_cast<double>(integer)) {
        return false;
    }
    if (result) {
        *result = integer;
    }
    return true;
}

QString modeName(ProseAwarenessWidget::Mode mode)
{
    switch (mode) {
    case ProseAwarenessWidget::Mode::Off:
        return QStringLiteral("off");
    case ProseAwarenessWidget::Mode::Live:
        return QStringLiteral("live");
    case ProseAwarenessWidget::Mode::ReportOnly:
        return QStringLiteral("report_only");
    }
    return QStringLiteral("unknown");
}
}

StoryToolHarness::StoryToolHarness(
    QMainWindow *window,
    MarkdownEditor *editor,
    DocumentManager *documentManager,
    ProseController *proseController,
    ProseAwarenessWidget *proseWidget,
    AgentEditTransactionManager *transactions,
    QObject *parent)
    : QObject(parent)
    , m_window(window)
    , m_editor(editor)
    , m_documentManager(documentManager)
    , m_proseController(proseController)
    , m_proseWidget(proseWidget)
    , m_transactions(transactions)
    , m_actions(window ? window->findChild<KActionCollection *>() : nullptr)
{
    Q_ASSERT(m_window);
    Q_ASSERT(m_editor);
    Q_ASSERT(m_documentManager);
    Q_ASSERT(m_proseController);
    Q_ASSERT(m_proseWidget);
    Q_ASSERT(m_transactions);
}

QJsonObject StoryToolHarness::success(const QJsonObject &payload)
{
    QJsonObject result = payload;
    result.insert(QStringLiteral("ok"), true);
    return result;
}

QJsonObject StoryToolHarness::failure(const QString &message, const QString &code)
{
    QJsonObject result;
    result.insert(QStringLiteral("ok"), false);
    result.insert(QStringLiteral("code"), code);
    result.insert(QStringLiteral("error"), message);
    return result;
}

QAction *StoryToolHarness::action(const QString &stableId) const
{
    return m_actions ? m_actions->action(stableId) : nullptr;
}

QJsonArray StoryToolHarness::manifest() const
{
    QJsonArray tools;
    tools.append(tool(QStringLiteral("get_app_state"), QStringLiteral("R0"),
                      tr("Read current theme and panel visibility.")));
    tools.append(tool(QStringLiteral("get_editor_state"), QStringLiteral("R0"),
                      tr("Read cursor, selection, document revision and save/undo state.")));
    tools.append(tool(QStringLiteral("get_prose_summary"), QStringLiteral("R0"),
                      tr("Read Prose Intelligence lens counts and current loaded findings."),
                      QStringLiteral("limit?: integer")));
    tools.append(tool(QStringLiteral("get_prose_findings"), QStringLiteral("R0"),
                      tr("Read currently hydrated Prose Intelligence findings."),
                      QStringLiteral("limit?: integer")));
    tools.append(tool(QStringLiteral("set_theme"), QStringLiteral("R1"),
                      tr("Switch the current theme between light and dark mode."),
                      QStringLiteral("theme: light|dark")));
    tools.append(tool(QStringLiteral("set_panel_visible"), QStringLiteral("R1"),
                      tr("Show or hide the left tools pane, Story Intelligence, or preview."),
                      QStringLiteral("panel: left|story|preview, visible: boolean")));
    tools.append(tool(QStringLiteral("focus_editor"), QStringLiteral("R1"),
                      tr("Move keyboard focus back to the manuscript editor.")));
    tools.append(tool(QStringLiteral("go_to_range"), QStringLiteral("R1"),
                      tr("Move the cursor to a verified UTF-16 manuscript range."),
                      QStringLiteral("start_utf16: integer, end_utf16: integer")));
    tools.append(tool(QStringLiteral("select_range"), QStringLiteral("R1"),
                      tr("Select a verified UTF-16 manuscript range."),
                      QStringLiteral("start_utf16: integer, end_utf16: integer")));
    tools.append(tool(QStringLiteral("run_prose_scan"), QStringLiteral("R2"),
                      tr("Run Prose Intelligence on the document, selection, or project folder."),
                      QStringLiteral("scope: document|selection|folder")));
    tools.append(tool(QStringLiteral("go_to_prose_finding"), QStringLiteral("R1"),
                      tr("Navigate to a currently hydrated prose finding by stable finding ID."),
                      QStringLiteral("finding_id: string")));
    tools.append(tool(QStringLiteral("indent_selection"), QStringLiteral("R3"),
                      tr("Indent the current line or selection inside a checkpointed undo transaction.")));
    tools.append(tool(QStringLiteral("unindent_selection"), QStringLiteral("R3"),
                      tr("Unindent the current line or selection inside a checkpointed undo transaction.")));
    tools.append(tool(QStringLiteral("indent_document"), QStringLiteral("R4"),
                      tr("Indent the whole document inside one checkpointed undo transaction.")));
    tools.append(tool(QStringLiteral("unindent_document"), QStringLiteral("R4"),
                      tr("Unindent the whole document inside one checkpointed undo transaction.")));
    tools.append(tool(QStringLiteral("replace_verified_range"), QStringLiteral("R3"),
                      tr("Replace one exact current manuscript span after verifying its expected text."),
                      QStringLiteral("start_utf16, end_utf16, expected, replacement, summary?")));
    tools.append(tool(QStringLiteral("apply_verified_replacements"), QStringLiteral("R4"),
                      tr("Apply multiple exact verified replacements as one checkpointed undo transaction."),
                      QStringLiteral("replacements: array, summary?: string")));
    tools.append(tool(QStringLiteral("apply_objective_grammar_fixes"), QStringLiteral("R4"),
                      tr("Apply all fully loaded strong grammar/mechanics findings that have deterministic replacements as one checkpointed Undo step.")));
    return tools;
}

QJsonObject StoryToolHarness::appState() const
{
    QJsonObject state;
    state.insert(QStringLiteral("dark_mode"), AppSettings::instance()->darkModeEnabled());

    if (QAction *left = action(QStringLiteral("view_sidebar"))) {
        state.insert(QStringLiteral("left_panel_visible"), left->isChecked());
    }
    if (QAction *preview = action(QStringLiteral("view_preview"))) {
        state.insert(QStringLiteral("preview_visible"), preview->isChecked());
        state.insert(QStringLiteral("preview_available"), preview->isEnabled());
    }
    if (QDockWidget *story = m_window->findChild<QDockWidget *>(QStringLiteral("storyIntelligenceDock"))) {
        state.insert(QStringLiteral("story_panel_visible"), story->isVisible());
    }
    state.insert(QStringLiteral("window_fullscreen"), m_window->isFullScreen());
    return state;
}

QJsonObject StoryToolHarness::editorState() const
{
    const QTextCursor cursor = m_editor->textCursor();
    const int selectionStart = std::min(cursor.position(), cursor.anchor());
    const int selectionEnd = std::max(cursor.position(), cursor.anchor());

    QJsonObject state;
    state.insert(QStringLiteral("document_path"), m_documentManager->document()->filePath());
    state.insert(QStringLiteral("document_revision"), m_editor->document()->revision());
    state.insert(QStringLiteral("modified"), m_editor->document()->isModified());
    state.insert(QStringLiteral("read_only"), m_editor->isReadOnly());
    state.insert(QStringLiteral("character_count"), std::max(0, m_editor->document()->characterCount() - 1));
    state.insert(QStringLiteral("cursor_utf16"), cursor.position());
    state.insert(QStringLiteral("line"), cursor.block().isValid() ? cursor.block().blockNumber() + 1 : 1);
    state.insert(QStringLiteral("selection_start_utf16"), selectionStart);
    state.insert(QStringLiteral("selection_end_utf16"), selectionEnd);
    state.insert(QStringLiteral("selection_text"), cursor.hasSelection() ? cursor.selectedText().left(1200) : QString());
    state.insert(QStringLiteral("undo_available"), m_editor->document()->availableUndoSteps() > 0);
    state.insert(QStringLiteral("redo_available"), m_editor->document()->availableRedoSteps() > 0);
    return state;
}

QJsonObject StoryToolHarness::proseState(int findingLimit) const
{
    const int limit = std::max(0, std::min(findingLimit, 200));
    QJsonObject state;
    state.insert(QStringLiteral("engine_ready"), m_proseWidget->engineReadySnapshot());
    state.insert(QStringLiteral("mode"), modeName(m_proseWidget->mode()));
    state.insert(QStringLiteral("profile"), m_proseWidget->profile());
    state.insert(QStringLiteral("scope"), m_proseWidget->scope());
    state.insert(QStringLiteral("selected_category"), m_proseWidget->selectedCategory());

    QJsonObject counts;
    const QHash<QString, int> categoryCounts = m_proseWidget->categoryCountsSnapshot();
    for (auto iterator = categoryCounts.cbegin(); iterator != categoryCounts.cend(); ++iterator) {
        counts.insert(iterator.key(), iterator.value());
    }
    state.insert(QStringLiteral("counts"), counts);

    QJsonObject hydratedCounts;
    QJsonArray findings;
    const QList<ProseDiagnostic> diagnostics = m_proseWidget->diagnosticsSnapshot();
    for (const ProseDiagnostic &diagnostic : diagnostics) {
        hydratedCounts.insert(
            diagnostic.category,
            hydratedCounts.value(diagnostic.category).toInt() + 1);
    }
    for (int index = 0; index < diagnostics.size() && index < limit; ++index) {
        const ProseDiagnostic &diagnostic = diagnostics.at(index);
        QJsonObject finding;
        finding.insert(QStringLiteral("id"), diagnostic.id);
        finding.insert(QStringLiteral("rule_id"), diagnostic.ruleId);
        finding.insert(QStringLiteral("analyzer"), diagnostic.analyzer);
        finding.insert(QStringLiteral("category"), diagnostic.category);
        finding.insert(QStringLiteral("level"), diagnostic.level);
        finding.insert(QStringLiteral("excerpt"), diagnostic.excerpt.left(300));
        finding.insert(QStringLiteral("explanation"), diagnostic.explanation.left(600));
        finding.insert(QStringLiteral("suggestion"), diagnostic.suggestion.left(600));
        finding.insert(QStringLiteral("start_utf16"), diagnostic.start);
        finding.insert(QStringLiteral("end_utf16"), diagnostic.end);
        finding.insert(QStringLiteral("revision"), diagnostic.revision);
        finding.insert(QStringLiteral("confidence"), diagnostic.confidence);
        QJsonArray replacements;
        for (const QString &replacement : diagnostic.replacements) {
            replacements.append(replacement.left(500));
        }
        finding.insert(QStringLiteral("replacements"), replacements);
        findings.append(finding);
    }
    state.insert(QStringLiteral("findings"), findings);
    state.insert(QStringLiteral("findings_hydrated"), diagnostics.size());
    state.insert(QStringLiteral("hydrated_counts"), hydratedCounts);
    return state;
}

QJsonObject StoryToolHarness::snapshot() const
{
    QJsonObject result;
    result.insert(QStringLiteral("app"), appState());
    result.insert(QStringLiteral("editor"), editorState());
    result.insert(QStringLiteral("prose"), proseState());
    return result;
}

QJsonObject StoryToolHarness::setCheckedAction(const QString &stableId, bool desired)
{
    QAction *nativeAction = action(stableId);
    if (!nativeAction) {
        return failure(tr("The requested native action is unavailable."), QStringLiteral("unavailable"));
    }
    if (!nativeAction->isCheckable()) {
        return failure(tr("The requested native action is not toggleable."));
    }
    if (!nativeAction->isEnabled()) {
        return failure(tr("The requested native action is disabled in this build."), QStringLiteral("disabled"));
    }
    if (nativeAction->isChecked() != desired) {
        nativeAction->trigger();
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("checked"), nativeAction->isChecked());
    return success(payload);
}

QJsonObject StoryToolHarness::setPanelVisible(const QString &panel, bool visible)
{
    if (panel == QStringLiteral("left")) {
        return setCheckedAction(QStringLiteral("view_sidebar"), visible);
    }
    if (panel == QStringLiteral("preview")) {
        return setCheckedAction(QStringLiteral("view_preview"), visible);
    }
    if (panel == QStringLiteral("story")) {
        QDockWidget *dock = m_window->findChild<QDockWidget *>(QStringLiteral("storyIntelligenceDock"));
        if (!dock) {
            return failure(tr("Story Intelligence pane is unavailable."), QStringLiteral("unavailable"));
        }
        dock->setVisible(visible);
        QJsonObject payload;
        payload.insert(QStringLiteral("visible"), dock->isVisible());
        return success(payload);
    }
    return failure(tr("Unknown panel. Use left, story, or preview."), QStringLiteral("invalid_arguments"));
}

QJsonObject StoryToolHarness::navigateRange(int start, int end, bool select)
{
    const int documentLength = std::max(0, m_editor->document()->characterCount() - 1);
    if (start < 0 || end < start || end > documentLength) {
        return failure(tr("The requested range is outside the current document."), QStringLiteral("stale_range"));
    }
    QTextCursor cursor(m_editor->document());
    cursor.setPosition(start);
    if (select && end > start) {
        cursor.setPosition(end, QTextCursor::KeepAnchor);
    }
    m_editor->setTextCursor(cursor);
    m_editor->ensureCursorVisible();
    m_editor->setFocus();
    QJsonObject payload;
    payload.insert(QStringLiteral("start_utf16"), start);
    payload.insert(QStringLiteral("end_utf16"), end);
    payload.insert(QStringLiteral("selected"), select && end > start);
    return success(payload);
}

QJsonObject StoryToolHarness::findProseFinding(const QString &findingId, bool navigate) const
{
    if (findingId.isEmpty()) {
        return failure(tr("finding_id is required."), QStringLiteral("invalid_arguments"));
    }
    const QList<ProseDiagnostic> diagnostics = m_proseWidget->diagnosticsSnapshot();
    for (const ProseDiagnostic &diagnostic : diagnostics) {
        if (diagnostic.id != findingId) {
            continue;
        }
        if (navigate) {
            QTextCursor cursor(m_editor->document());
            const int maximum = std::max(0, m_editor->document()->characterCount() - 1);
            const int start = std::max(0, std::min(diagnostic.start, maximum));
            const int end = std::max(start, std::min(diagnostic.end, maximum));
            cursor.setPosition(start);
            cursor.setPosition(end, QTextCursor::KeepAnchor);
            m_editor->setTextCursor(cursor);
            m_editor->ensureCursorVisible();
            m_editor->setFocus();
        }
        QJsonObject payload;
        payload.insert(QStringLiteral("id"), diagnostic.id);
        payload.insert(QStringLiteral("category"), diagnostic.category);
        payload.insert(QStringLiteral("excerpt"), diagnostic.excerpt);
        payload.insert(QStringLiteral("explanation"), diagnostic.explanation);
        payload.insert(QStringLiteral("suggestion"), diagnostic.suggestion);
        payload.insert(QStringLiteral("start_utf16"), diagnostic.start);
        payload.insert(QStringLiteral("end_utf16"), diagnostic.end);
        return success(payload);
    }
    return failure(tr("That prose finding is not currently hydrated. Run/select the relevant lens first."),
                   QStringLiteral("not_found"));
}

QJsonObject StoryToolHarness::applyObjectiveGrammarFixes(bool allowBulkEdits)
{
    if (!allowBulkEdits) {
        return failure(tr("Objective grammar correction requires explicit bulk-edit authorization."),
                       QStringLiteral("authorization_required"));
    }

    const QHash<QString, int> counts = m_proseWidget->categoryCountsSnapshot();
    const int reportedTotal = counts.value(QStringLiteral("grammar_mechanics"));
    QList<ProseDiagnostic> grammar;
    for (const ProseDiagnostic &diagnostic : m_proseWidget->diagnosticsSnapshot()) {
        if (diagnostic.category == QStringLiteral("grammar_mechanics")
            || diagnostic.analyzer == QStringLiteral("grammar_mechanics")) {
            grammar.append(diagnostic);
        }
    }

    if (reportedTotal > grammar.size()) {
        QJsonObject result = failure(
            tr("Grammar findings are not fully hydrated yet. Open/select the Grammar & Mechanics lens after a document scan, then retry."),
            QStringLiteral("grammar_findings_not_fully_loaded"));
        result.insert(QStringLiteral("reported_total"), reportedTotal);
        result.insert(QStringLiteral("hydrated"), grammar.size());
        return result;
    }
    if (grammar.isEmpty()) {
        QJsonObject result = success();
        result.insert(QStringLiteral("reported_total"), reportedTotal);
        result.insert(QStringLiteral("hydrated"), 0);
        result.insert(QStringLiteral("applied"), 0);
        result.insert(QStringLiteral("message"), tr("No loaded grammar/mechanics fixes are available."));
        return result;
    }

    std::sort(grammar.begin(), grammar.end(), [](const ProseDiagnostic &left, const ProseDiagnostic &right) {
        if (left.start == right.start) {
            return left.end < right.end;
        }
        return left.start < right.start;
    });

    const QString currentText = m_editor->toPlainText();
    QJsonArray replacements;
    int skippedNonObjective = 0;
    int skippedNoReplacement = 0;
    int skippedStale = 0;
    int skippedOverlap = 0;
    int previousEnd = -1;

    for (const ProseDiagnostic &diagnostic : std::as_const(grammar)) {
        if (diagnostic.level != QStringLiteral("strong_flag")) {
            ++skippedNonObjective;
            continue;
        }
        if (diagnostic.replacements.isEmpty()) {
            ++skippedNoReplacement;
            continue;
        }
        if (diagnostic.start < 0 || diagnostic.end <= diagnostic.start
            || diagnostic.end > currentText.size()) {
            ++skippedStale;
            continue;
        }
        if (diagnostic.start < previousEnd) {
            ++skippedOverlap;
            continue;
        }

        const QString expected = currentText.mid(diagnostic.start, diagnostic.end - diagnostic.start);
        const QString replacement = diagnostic.replacements.first();
        if (replacement == expected) {
            ++skippedNoReplacement;
            continue;
        }
        QJsonObject item;
        item.insert(QStringLiteral("start_utf16"), diagnostic.start);
        item.insert(QStringLiteral("end_utf16"), diagnostic.end);
        item.insert(QStringLiteral("expected"), expected);
        item.insert(QStringLiteral("replacement"), replacement);
        replacements.append(item);
        previousEnd = diagnostic.end;
    }

    if (replacements.isEmpty()) {
        QJsonObject result = success();
        result.insert(QStringLiteral("reported_total"), reportedTotal);
        result.insert(QStringLiteral("hydrated"), grammar.size());
        result.insert(QStringLiteral("objective_candidates"), 0);
        result.insert(QStringLiteral("applied"), 0);
        result.insert(QStringLiteral("skipped_non_objective"), skippedNonObjective);
        result.insert(QStringLiteral("skipped_no_replacement"), skippedNoReplacement);
        result.insert(QStringLiteral("skipped_stale"), skippedStale);
        result.insert(QStringLiteral("skipped_overlap"), skippedOverlap);
        return result;
    }

    QJsonObject result = m_transactions->applyVerifiedReplacements(
        replacements,
        tr("Apply %1 objective grammar/mechanics fixes").arg(replacements.size()),
        QStringLiteral("apply_objective_grammar_fixes"));
    result.insert(QStringLiteral("reported_total"), reportedTotal);
    result.insert(QStringLiteral("hydrated"), grammar.size());
    result.insert(QStringLiteral("objective_candidates"), replacements.size());
    result.insert(QStringLiteral("applied"),
                  result.value(QStringLiteral("ok")).toBool() ? replacements.size() : 0);
    result.insert(QStringLiteral("skipped_non_objective"), skippedNonObjective);
    result.insert(QStringLiteral("skipped_no_replacement"), skippedNoReplacement);
    result.insert(QStringLiteral("skipped_stale"), skippedStale);
    result.insert(QStringLiteral("skipped_overlap"), skippedOverlap);
    return result;
}

QJsonObject StoryToolHarness::runIndent(
    bool wholeDocument,
    bool unindent,
    bool allowBulkEdits,
    bool allowBoundedEdits)
{
    if (wholeDocument && !allowBulkEdits) {
        return failure(tr("Whole-document formatting requires explicit bulk-edit authorization."),
                       QStringLiteral("authorization_required"));
    }
    if (!wholeDocument && !allowBoundedEdits) {
        return failure(tr("Manuscript formatting requires edit authorization."),
                       QStringLiteral("authorization_required"));
    }

    const QString toolId = wholeDocument
        ? (unindent ? QStringLiteral("unindent_document") : QStringLiteral("indent_document"))
        : (unindent ? QStringLiteral("unindent_selection") : QStringLiteral("indent_selection"));
    const QString summary = wholeDocument
        ? (unindent ? tr("Unindent the entire document") : tr("Indent the entire document"))
        : (unindent ? tr("Unindent the current selection") : tr("Indent the current selection"));

    return m_transactions->runEditorCommand(summary, toolId, [this, wholeDocument, unindent]() {
        if (wholeDocument) {
            QTextCursor selection(m_editor->document());
            selection.select(QTextCursor::Document);
            m_editor->setTextCursor(selection);
        }
        if (unindent) {
            m_editor->unindentText();
        } else {
            m_editor->indentText();
        }
    });
}

QJsonObject StoryToolHarness::execute(
    const QString &toolId,
    const QJsonObject &arguments,
    bool allowBoundedEdits,
    bool allowBulkEdits)
{
    QJsonObject result;

    if (toolId == QStringLiteral("get_app_state")) {
        result = success(appState());
    } else if (toolId == QStringLiteral("get_editor_state")) {
        result = success(editorState());
    } else if (toolId == QStringLiteral("get_prose_summary")
               || toolId == QStringLiteral("get_prose_findings")) {
        int limit = 60;
        if (arguments.contains(QStringLiteral("limit"))) {
            if (!strictInteger(arguments.value(QStringLiteral("limit")), &limit)) {
                result = failure(tr("limit must be an integer."), QStringLiteral("invalid_arguments"));
            } else {
                result = success(proseState(limit));
            }
        } else {
            result = success(proseState(limit));
        }
    } else if (toolId == QStringLiteral("set_theme")) {
        const QString theme = arguments.value(QStringLiteral("theme")).toString().trimmed().toLower();
        if (theme != QStringLiteral("light") && theme != QStringLiteral("dark")) {
            result = failure(tr("theme must be light or dark."), QStringLiteral("invalid_arguments"));
        } else {
            result = setCheckedAction(QStringLiteral("view_dark_mode"), theme == QStringLiteral("dark"));
        }
    } else if (toolId == QStringLiteral("set_panel_visible")) {
        if (!arguments.value(QStringLiteral("visible")).isBool()) {
            result = failure(tr("visible must be a boolean."), QStringLiteral("invalid_arguments"));
        } else {
            result = setPanelVisible(
                arguments.value(QStringLiteral("panel")).toString().trimmed().toLower(),
                arguments.value(QStringLiteral("visible")).toBool());
        }
    } else if (toolId == QStringLiteral("focus_editor")) {
        m_editor->setFocus();
        result = success();
    } else if (toolId == QStringLiteral("go_to_range") || toolId == QStringLiteral("select_range")) {
        int start = -1;
        int end = -1;
        if (!strictInteger(arguments.value(QStringLiteral("start_utf16")), &start)
            || !strictInteger(arguments.value(QStringLiteral("end_utf16")), &end)) {
            result = failure(tr("Range offsets must be integers."), QStringLiteral("invalid_arguments"));
        } else {
            result = navigateRange(start, end, toolId == QStringLiteral("select_range"));
        }
    } else if (toolId == QStringLiteral("run_prose_scan")) {
        const QString scope = arguments.value(QStringLiteral("scope")).toString(QStringLiteral("document")).trimmed().toLower();
        if (scope == QStringLiteral("document")) {
            m_proseController->reviewDocument();
            result = success(QJsonObject{{QStringLiteral("scope"), scope}, {QStringLiteral("started"), true}, {QStringLiteral("pending"), true}});
        } else if (scope == QStringLiteral("selection")) {
            m_proseController->reviewSelection();
            result = success(QJsonObject{{QStringLiteral("scope"), scope}, {QStringLiteral("started"), true}, {QStringLiteral("pending"), true}});
        } else if (scope == QStringLiteral("folder")) {
            m_proseController->reviewFolder();
            result = success(QJsonObject{{QStringLiteral("scope"), scope}, {QStringLiteral("started"), true}, {QStringLiteral("pending"), true}});
        } else {
            result = failure(tr("scope must be document, selection, or folder."), QStringLiteral("invalid_arguments"));
        }
    } else if (toolId == QStringLiteral("go_to_prose_finding")) {
        result = findProseFinding(arguments.value(QStringLiteral("finding_id")).toString(), true);
    } else if (toolId == QStringLiteral("indent_selection")) {
        result = runIndent(false, false, allowBulkEdits, allowBoundedEdits);
    } else if (toolId == QStringLiteral("unindent_selection")) {
        result = runIndent(false, true, allowBulkEdits, allowBoundedEdits);
    } else if (toolId == QStringLiteral("indent_document")) {
        result = runIndent(true, false, allowBulkEdits, allowBoundedEdits);
    } else if (toolId == QStringLiteral("unindent_document")) {
        result = runIndent(true, true, allowBulkEdits, allowBoundedEdits);
    } else if (toolId == QStringLiteral("replace_verified_range")) {
        if (!allowBoundedEdits) {
            result = failure(tr("Text replacement requires edit authorization."),
                             QStringLiteral("authorization_required"));
        } else {
            QJsonArray replacements;
            replacements.append(arguments);
            result = m_transactions->applyVerifiedReplacements(
                replacements,
                arguments.value(QStringLiteral("summary")).toString(tr("Apply AI replacement")),
                toolId);
        }
    } else if (toolId == QStringLiteral("apply_verified_replacements")) {
        if (!allowBulkEdits) {
            result = failure(tr("Multiple replacements require explicit bulk-edit authorization."),
                             QStringLiteral("authorization_required"));
        } else if (!arguments.value(QStringLiteral("replacements")).isArray()) {
            result = failure(tr("replacements must be an array."), QStringLiteral("invalid_arguments"));
        } else {
            result = m_transactions->applyVerifiedReplacements(
                arguments.value(QStringLiteral("replacements")).toArray(),
                arguments.value(QStringLiteral("summary")).toString(tr("Apply AI edit batch")),
                toolId);
        }
    } else if (toolId == QStringLiteral("apply_objective_grammar_fixes")) {
        result = applyObjectiveGrammarFixes(allowBulkEdits);
    } else {
        result = failure(tr("Unknown or unexposed Story Intelligence tool."), QStringLiteral("unknown_tool"));
    }

    result.insert(QStringLiteral("tool_id"), toolId);
    emit toolExecuted(toolId, result);
    return result;
}
}
