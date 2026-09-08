/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "storyintelligencecontroller.h"

#include "../editor/markdowndocument.h"
#include "../editor/markdowneditor.h"
#include "../editor/textformatoverlaycontroller.h"
#include "../messageboxhelper.h"
#include "../prose/credentialstore.h"
#include "../prose/providersettingsdialog.h"
#include "../prose/writerengineclient.h"
#include "agentedittransactionmanager.h"
#include "documentactivitytracker.h"
#include "storyintelligencewidget.h"
#include "storylabdialog.h"
#include "storyresponse.h"
#include "storytoolharness.h"
#include "storyworkspacedialog.h"

#include <QCheckBox>
#include <QComboBox>
#include <QCryptographicHash>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QInputDialog>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QJsonValue>
#include <QLabel>
#include <QLineEdit>
#include <QListWidget>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QRegularExpression>
#include <QSaveFile>
#include <QSet>
#include <QSettings>
#include <QTableWidget>
#include <QTextBlock>
#include <QTextCharFormat>
#include <QTextDocument>
#include <QTextLayout>
#include <QUrl>
#include <QUuid>
#include <QVBoxLayout>

namespace ghostwriter
{
namespace
{
const QString StoryOverlayChannel = QStringLiteral("story-intelligence");
constexpr int MaximumHistoryMessages = 16;
constexpr int MaximumChatInputCharacters = 20000;
constexpr int MaximumToolCallsPerRound = 8;
constexpr int MaximumToolRounds = 4;
constexpr int ToolPollIntervalMs = 200;
constexpr int ToolWaitTimeoutMs = 120000;
constexpr int MaximumAccumulatedToolResults = 32;

QString providerDisplayName(const QString &kind)
{
    static const QHash<QString, QString> names = {
        {QStringLiteral("gemini"), QStringLiteral("Google Gemini")},
        {QStringLiteral("gemini_oauth"), QStringLiteral("Google Gemini (OAuth)")},
        {QStringLiteral("codex"), QStringLiteral("OpenAI (Codex / ChatGPT)")},
        {QStringLiteral("openai"), QStringLiteral("OpenAI")},
        {QStringLiteral("openai_compatible"), QStringLiteral("OpenAI compatible")},
        {QStringLiteral("openrouter"), QStringLiteral("OpenRouter")},
        {QStringLiteral("opencode_zen"), QStringLiteral("OpenCode Zen")},
        {QStringLiteral("opencode_go"), QStringLiteral("OpenCode Go")},
        {QStringLiteral("anthropic"), QStringLiteral("Anthropic")},
        {QStringLiteral("ollama"), QStringLiteral("Ollama")},
        {QStringLiteral("lmstudio"), QStringLiteral("LM Studio")},
        {QStringLiteral("llama_cpp"), QStringLiteral("llama.cpp")},
    };
    return names.value(kind, kind);
}

QColor annotationColor(const QString &category)
{
    QColor color;
    if (category == QStringLiteral("voice")) {
        color = QColor(QStringLiteral("#F2C4C4"));
    } else if (category == QStringLiteral("continuity") || category == QStringLiteral("research") || category == QStringLiteral("rewrite")) {
        color = QColor(QStringLiteral("#C6E2E9"));
    } else if (category == QStringLiteral("idea")) {
        color = QColor(QStringLiteral("#CDE8D2"));
    } else {
        color = QColor(QStringLiteral("#F9E08E"));
    }
    color.setAlpha(150);
    return color;
}

QString annotationTooltip(const QJsonObject &annotation)
{
    QStringList lines;
    const QString category = annotation.value(QStringLiteral("category")).toString().trimmed();
    const QString comment = annotation.value(QStringLiteral("comment")).toString().trimmed();
    const QString replacement = annotation.value(QStringLiteral("replacement")).toString().trimmed();
    if (!category.isEmpty()) {
        lines << QObject::tr("Story Intelligence · %1").arg(category);
    }
    if (!comment.isEmpty()) {
        lines << comment;
    }
    if (!replacement.isEmpty()) {
        lines << QObject::tr("Suggested rewrite: %1").arg(replacement);
    }
    return lines.join(QChar('\n'));
}

QString toolDisplayName(const QString &toolId)
{
    QString display = toolId;
    display.replace(QChar('_'), QChar(' '));
    return display;
}

bool jsonArrayContainsString(const QJsonArray &values, const QString &needle)
{
    for (const QJsonValue &value : values) {
        if (value.toString() == needle) {
            return true;
        }
    }
    return false;
}

bool chooseProjectSourceOverride(QWidget *parent, const QJsonObject &page, QString &relativePath, QJsonArray &roles, QString &authority, QString &pattern)
{
    const QJsonArray sources = page.value(QStringLiteral("sources")).toArray();
    if (sources.isEmpty()) {
        return false;
    }
    QDialog dialog(parent);
    dialog.setWindowTitle(QObject::tr("Review Project Understanding"));
    dialog.resize(780, 520);
    auto *layout = new QVBoxLayout(&dialog);
    auto *hint = new QLabel(QObject::tr("Select a source, then optionally override its role or authority. "
                                        "ThothPad never reorganizes the source file."),
                            &dialog);
    hint->setWordWrap(true);
    layout->addWidget(hint);
    auto *table = new QTableWidget(sources.size(), 3, &dialog);
    table->setHorizontalHeaderLabels({QObject::tr("Source"), QObject::tr("Detected roles"), QObject::tr("Authority")});
    table->setSelectionBehavior(QAbstractItemView::SelectRows);
    table->setSelectionMode(QAbstractItemView::SingleSelection);
    table->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table->verticalHeader()->setVisible(false);
    table->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Stretch);
    table->horizontalHeader()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    table->horizontalHeader()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    for (int row = 0; row < sources.size(); ++row) {
        const QJsonObject source = sources.at(row).toObject();
        const QJsonArray detectedRoles = source.value(QStringLiteral("roles")).toArray();
        QStringList roleNames;
        for (const QJsonValue &value : detectedRoles) {
            roleNames << value.toObject().value(QStringLiteral("role")).toString();
        }
        auto *pathItem = new QTableWidgetItem(source.value(QStringLiteral("path")).toString());
        pathItem->setData(Qt::UserRole, source);
        table->setItem(row, 0, pathItem);
        table->setItem(row, 1, new QTableWidgetItem(roleNames.join(QStringLiteral(", "))));
        table->setItem(row, 2, new QTableWidgetItem(source.value(QStringLiteral("authority")).toString()));
    }
    table->selectRow(0);
    layout->addWidget(table, 1);

    auto *form = new QFormLayout;
    auto *roleCombo = new QComboBox(&dialog);
    roleCombo->addItem(QObject::tr("Keep detected roles"), QString());
    const QStringList roleValues = {QStringLiteral("manuscript"),
                                    QStringLiteral("outline"),
                                    QStringLiteral("character_reference"),
                                    QStringLiteral("world_reference"),
                                    QStringLiteral("plot_reference"),
                                    QStringLiteral("timeline_reference"),
                                    QStringLiteral("research"),
                                    QStringLiteral("author_notes"),
                                    QStringLiteral("style_reference"),
                                    QStringLiteral("alternate"),
                                    QStringLiteral("archive"),
                                    QStringLiteral("unknown")};
    for (const QString &value : roleValues) {
        QString label = value;
        label.replace(QChar('_'), QChar(' '));
        roleCombo->addItem(label, value);
    }
    auto *authorityCombo = new QComboBox(&dialog);
    authorityCombo->addItem(QObject::tr("Keep detected authority"), QString());
    const QStringList authorityValues = {QStringLiteral("AUTHOR_LOCKED"),
                                         QStringLiteral("CONFIRMED_CANON"),
                                         QStringLiteral("MANUSCRIPT_OBSERVED"),
                                         QStringLiteral("AUTHOR_INTENT"),
                                         QStringLiteral("COMPILED_CANON"),
                                         QStringLiteral("PROVISIONAL"),
                                         QStringLiteral("OPEN"),
                                         QStringLiteral("CONTESTED"),
                                         QStringLiteral("SUPERSEDED"),
                                         QStringLiteral("ARCHIVED")};
    for (const QString &value : authorityValues) {
        authorityCombo->addItem(value, value);
    }
    form->addRow(QObject::tr("Role override"), roleCombo);
    form->addRow(QObject::tr("Authority override"), authorityCombo);
    auto *learnRule = new QCheckBox(QObject::tr("Learn this correction for other files in the same folder"), &dialog);
    learnRule->setToolTip(
        QObject::tr("Creates a writer-confirmed project rule. Folder names remain hints only; they never become Story Engine semantics by themselves."));
    form->addRow(QString(), learnRule);
    layout->addLayout(form);
    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Cancel | QDialogButtonBox::Save, &dialog);
    QObject::connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    QObject::connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(buttons);
    if (dialog.exec() != QDialog::Accepted || table->currentRow() < 0) {
        return false;
    }
    relativePath = table->item(table->currentRow(), 0)->text();
    const QString role = roleCombo->currentData().toString();
    if (!role.isEmpty()) {
        roles = QJsonArray{role};
    }
    authority = authorityCombo->currentData().toString();
    if (learnRule->isChecked()) {
        QString normalized = relativePath;
        normalized.replace(QChar('\\'), QChar('/'));
        const int slash = normalized.lastIndexOf(QChar('/'));
        pattern = slash >= 0 ? normalized.left(slash + 1) + QStringLiteral("*") : QStringLiteral("*");
    }
    return !relativePath.isEmpty() && (!roles.isEmpty() || !authority.isEmpty());
}

bool chooseManuscriptOrder(QWidget *parent, const QJsonObject &page, QJsonArray &paths)
{
    const QJsonArray sources = page.value(QStringLiteral("sources")).toArray();
    if (sources.isEmpty()) {
        return false;
    }

    QDialog dialog(parent);
    dialog.setWindowTitle(QObject::tr("Manuscript Order"));
    dialog.resize(620, 480);
    auto *layout = new QVBoxLayout(&dialog);
    auto *hint = new QLabel(QObject::tr("Set the reading order for manuscript files. ThothPad uses this writer-owned order for Reader, Cold Reader, and "
                                        "character knowledge boundaries; it never guesses from folder names."),
                            &dialog);
    hint->setWordWrap(true);
    layout->addWidget(hint);

    auto *list = new QListWidget(&dialog);
    list->setSelectionMode(QAbstractItemView::SingleSelection);
    QSet<QString> available;
    QStringList detected;
    for (const QJsonValue &value : sources) {
        const QString path = value.toObject().value(QStringLiteral("path")).toString();
        if (path.isEmpty()) {
            continue;
        }
        available.insert(path);
        detected.append(path);
    }
    QSet<QString> added;
    for (const QJsonValue &value : page.value(QStringLiteral("active_manuscripts")).toArray()) {
        const QString path = value.toString();
        if (!path.isEmpty() && available.contains(path) && !added.contains(path)) {
            list->addItem(path);
            added.insert(path);
        }
    }
    for (const QString &path : detected) {
        if (!added.contains(path)) {
            list->addItem(path);
            added.insert(path);
        }
    }
    if (list->count() > 0) {
        list->setCurrentRow(0);
    }
    layout->addWidget(list, 1);

    auto *moveRow = new QHBoxLayout;
    auto *up = new QPushButton(QObject::tr("Move up"), &dialog);
    auto *down = new QPushButton(QObject::tr("Move down"), &dialog);
    moveRow->addWidget(up);
    moveRow->addWidget(down);
    moveRow->addStretch(1);
    layout->addLayout(moveRow);
    QObject::connect(up, &QPushButton::clicked, &dialog, [list]() {
        const int row = list->currentRow();
        if (row <= 0) {
            return;
        }
        QListWidgetItem *item = list->takeItem(row);
        list->insertItem(row - 1, item);
        list->setCurrentRow(row - 1);
    });
    QObject::connect(down, &QPushButton::clicked, &dialog, [list]() {
        const int row = list->currentRow();
        if (row < 0 || row >= list->count() - 1) {
            return;
        }
        QListWidgetItem *item = list->takeItem(row);
        list->insertItem(row + 1, item);
        list->setCurrentRow(row + 1);
    });

    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Cancel | QDialogButtonBox::Save, &dialog);
    QObject::connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    QObject::connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(buttons);
    if (dialog.exec() != QDialog::Accepted) {
        return false;
    }
    for (int row = 0; row < list->count(); ++row) {
        paths.append(list->item(row)->text());
    }
    return !paths.isEmpty();
}

enum class BranchReviewAction {
    Close,
    Rebase,
    Merge,
};

struct BranchReviewDecision {
    BranchReviewAction action = BranchReviewAction::Close;
    QJsonArray overlayIds;
};

bool branchOverlayCanMerge(const QString &kind)
{
    static const QSet<QString> supported = {
        QStringLiteral("claim"),
        QStringLiteral("world_state"),
        QStringLiteral("thread"),
        QStringLiteral("promise_item"),
        QStringLiteral("scene_contract"),
    };
    return supported.contains(kind);
}

BranchReviewDecision reviewStoryBranch(QWidget *parent, const QJsonObject &comparison, bool canRebase, bool canMerge)
{
    BranchReviewDecision decision;
    const QJsonObject branch = comparison.value(QStringLiteral("branch")).toObject();
    const QJsonObject freshness = comparison.value(QStringLiteral("freshness")).toObject();
    const QJsonArray overlays = comparison.value(QStringLiteral("overlays")).toArray();
    const bool stale = freshness.value(QStringLiteral("stale")).toBool();

    QDialog dialog(parent);
    dialog.setWindowTitle(QObject::tr("Review Story Branch"));
    dialog.resize(780, 520);
    auto *layout = new QVBoxLayout(&dialog);
    auto *summary = new QLabel(
        QObject::tr("%1 · parent %2 · %3 change(s) · %4")
            .arg(branch.value(QStringLiteral("branch_id")).toString(), branch.value(QStringLiteral("parent_branch")).toString(QStringLiteral("mainline")))
            .arg(overlays.size())
            .arg(stale ? QObject::tr("stale") : QObject::tr("current")),
        &dialog);
    summary->setWordWrap(true);
    layout->addWidget(summary);
    auto *help = new QLabel(
        stale ? QObject::tr("The parent Story State changed after this branch forked. Review and rebase before merging; branch overlays remain isolated.")
              : QObject::tr(
                    "Select only the changes you want to promote into Mainline. Unsupported or already merged records remain visible but cannot be selected."),
        &dialog);
    help->setWordWrap(true);
    layout->addWidget(help);

    auto *table = new QTableWidget(overlays.size(), 5, &dialog);
    table->setHorizontalHeaderLabels({QObject::tr("Merge"), QObject::tr("Operation"), QObject::tr("Kind"), QObject::tr("Record"), QObject::tr("State")});
    table->setSelectionBehavior(QAbstractItemView::SelectRows);
    table->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table->verticalHeader()->setVisible(false);
    table->horizontalHeader()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    table->horizontalHeader()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    table->horizontalHeader()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    table->horizontalHeader()->setSectionResizeMode(3, QHeaderView::Stretch);
    table->horizontalHeader()->setSectionResizeMode(4, QHeaderView::ResizeToContents);
    for (int row = 0; row < overlays.size(); ++row) {
        const QJsonObject overlay = overlays.at(row).toObject();
        const QString kind = overlay.value(QStringLiteral("record_kind")).toString();
        const bool merged = overlay.value(QStringLiteral("merged")).toBool();
        const bool supported = branchOverlayCanMerge(kind);
        auto *check = new QTableWidgetItem;
        check->setData(Qt::UserRole, overlay.value(QStringLiteral("overlay_id")).toString());
        if (!stale && !merged && supported) {
            check->setFlags(Qt::ItemIsEnabled | Qt::ItemIsUserCheckable | Qt::ItemIsSelectable);
            check->setCheckState(Qt::Unchecked);
        } else {
            check->setFlags(Qt::ItemIsSelectable);
        }
        const QString payloadText = QString::fromUtf8(QJsonDocument(overlay.value(QStringLiteral("payload")).toObject()).toJson(QJsonDocument::Compact));
        check->setToolTip(payloadText);
        table->setItem(row, 0, check);
        table->setItem(row, 1, new QTableWidgetItem(overlay.value(QStringLiteral("operation")).toString()));
        table->setItem(row, 2, new QTableWidgetItem(kind));
        table->setItem(row, 3, new QTableWidgetItem(overlay.value(QStringLiteral("record_id")).toString()));
        table->setItem(row, 4, new QTableWidgetItem(merged ? QObject::tr("merged") : supported ? QObject::tr("ready") : QObject::tr("inspect only")));
    }
    layout->addWidget(table, 1);

    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Close, &dialog);
    QPushButton *rebase = nullptr;
    QPushButton *merge = nullptr;
    if (stale && canRebase) {
        rebase = buttons->addButton(QObject::tr("Review and rebase…"), QDialogButtonBox::ActionRole);
    }
    if (!stale && canMerge) {
        merge = buttons->addButton(QObject::tr("Merge selected…"), QDialogButtonBox::AcceptRole);
        merge->setEnabled(false);
        const auto updateMergeEnabled = [table, merge]() {
            bool selected = false;
            for (int row = 0; row < table->rowCount(); ++row) {
                const auto *item = table->item(row, 0);
                if (item && item->checkState() == Qt::Checked) {
                    selected = true;
                    break;
                }
            }
            merge->setEnabled(selected);
        };
        QObject::connect(table, &QTableWidget::itemChanged, &dialog, [updateMergeEnabled](QTableWidgetItem *) {
            updateMergeEnabled();
        });
        QObject::connect(merge, &QPushButton::clicked, &dialog, [&decision, table, &dialog]() {
            for (int row = 0; row < table->rowCount(); ++row) {
                const auto *item = table->item(row, 0);
                if (item && item->checkState() == Qt::Checked) {
                    decision.overlayIds.append(item->data(Qt::UserRole).toString());
                }
            }
            if (!decision.overlayIds.isEmpty()) {
                decision.action = BranchReviewAction::Merge;
                dialog.accept();
            }
        });
    }
    if (rebase) {
        QObject::connect(rebase, &QPushButton::clicked, &dialog, [&decision, &dialog]() {
            decision.action = BranchReviewAction::Rebase;
            dialog.accept();
        });
    }
    QObject::connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(buttons);
    dialog.exec();
    return decision;
}
}

StoryIntelligenceController::StoryIntelligenceController(MarkdownEditor *editor,
                                                         StoryIntelligenceWidget *widget,
                                                         WriterEngineClient *engine,
                                                         CredentialStore *credentials,
                                                         QObject *parent)
    : QObject(parent)
    , m_editor(editor)
    , m_widget(widget)
    , m_engine(engine)
    , m_credentials(credentials)
{
    Q_ASSERT(m_editor);
    Q_ASSERT(m_widget);
    Q_ASSERT(m_engine);
    Q_ASSERT(m_credentials);

    m_toolWaitTimer.setInterval(ToolPollIntervalMs);
    m_toolWaitTimer.setSingleShot(false);
    connect(&m_toolWaitTimer, &QTimer::timeout, this, &StoryIntelligenceController::pollPendingTool);

    connect(m_widget, &StoryIntelligenceWidget::projectFolderRequested, this, &StoryIntelligenceController::chooseProjectFolder);
    connect(m_widget, &StoryIntelligenceWidget::projectUnderstandingReviewRequested, this, &StoryIntelligenceController::reviewProjectUnderstanding);
    connect(m_widget, &StoryIntelligenceWidget::manuscriptOrderRequested, this, &StoryIntelligenceController::editManuscriptOrder);
    connect(m_widget, &StoryIntelligenceWidget::storyLabRequested, this, &StoryIntelligenceController::openStoryLab);
    connect(m_widget, &StoryIntelligenceWidget::routingSettingsRequested, this, &StoryIntelligenceController::openStoryRoutingSettings);
    connect(m_widget, &StoryIntelligenceWidget::branchChanged, this, &StoryIntelligenceController::switchStoryBranch);
    connect(m_widget, &StoryIntelligenceWidget::createBranchRequested, this, &StoryIntelligenceController::createStoryBranch);
    connect(m_widget, &StoryIntelligenceWidget::branchDetailsRequested, this, &StoryIntelligenceController::showStoryBranchDetails);
    connect(m_widget, &StoryIntelligenceWidget::epistemicModeChanged, this, [this](const QString &mode) {
        m_epistemicMode = mode.isEmpty() ? QStringLiteral("author_omniscient") : mode;
    });
    connect(m_widget, &StoryIntelligenceWidget::modelSettingsRequested, this, &StoryIntelligenceController::openModelSettings);
    connect(m_widget, &StoryIntelligenceWidget::editSceneRequested, this, &StoryIntelligenceController::editSceneContext);
    connect(m_widget, &StoryIntelligenceWidget::addCharacterRequested, this, &StoryIntelligenceController::addCharacter);
    connect(m_widget, &StoryIntelligenceWidget::editCharactersRequested, this, &StoryIntelligenceController::editCharacters);
    connect(m_widget, &StoryIntelligenceWidget::chatRequested, this, &StoryIntelligenceController::sendChat);
    connect(m_widget, &StoryIntelligenceWidget::clearAnnotationsRequested, this, &StoryIntelligenceController::clearAnnotations);
    connect(m_widget, &StoryIntelligenceWidget::applySuggestionRequested, this, &StoryIntelligenceController::applySuggestion);
    connect(m_widget, &StoryIntelligenceWidget::dismissSuggestionRequested, this, &StoryIntelligenceController::dismissSuggestion);
    connect(m_widget, &StoryIntelligenceWidget::characterActivated, this, [this](const QString &id) {
        Q_UNUSED(id);
        newSession();
    });
    connect(m_widget, &StoryIntelligenceWidget::workspaceRequested, this, [this]() {
        editWorkspace();
    });
    connect(m_widget, &StoryIntelligenceWidget::newSessionRequested, this, &StoryIntelligenceController::newSession);
    connect(m_widget, &StoryIntelligenceWidget::deleteSessionRequested, this, &StoryIntelligenceController::deleteSession);
    connect(m_widget, &StoryIntelligenceWidget::sessionSelected, this, &StoryIntelligenceController::selectSession);
    connect(m_widget, &StoryIntelligenceWidget::messageActionRequested, this, &StoryIntelligenceController::handleMessageAction);
    connect(m_widget, &StoryIntelligenceWidget::rememberRequested, this, &StoryIntelligenceController::rememberMessage);
    connect(m_widget, &StoryIntelligenceWidget::proposalReviewRequested, this, &StoryIntelligenceController::reviewProposal);
    connect(m_widget, &StoryIntelligenceWidget::scopeModeChanged, this, [this](const QString &mode) {
        m_scopeMode = mode == "manuscript" ? "manuscript" : "chapter";
        m_workspace.data.insert("scope_mode", m_scopeMode);
        refreshWorkspace();
        saveWorkspace();
    });
    m_workspaceTimer.setSingleShot(true);
    m_workspaceTimer.setInterval(600);
    connect(&m_workspaceTimer, &QTimer::timeout, this, [this]() {
        if (!m_started)
            return;
        openWorkspaceForDocument();
        reconcileWorkspaceHeadings();
        refreshWorkspace();
    });
    connect(m_editor, &MarkdownEditor::markdownASTUpdated, this, [this](quint64) {
        if (m_started)
            m_workspaceTimer.start();
    });
    connect(m_editor, &MarkdownEditor::cursorPositionChanged, this, [this]() {
        if (m_started && !m_loadingWorkspace)
            refreshWorkspace();
    });
    if (auto *document = qobject_cast<MarkdownDocument *>(m_editor->document())) {
        connect(document, &MarkdownDocument::filePathChanged, this, [this]() {
            m_workspaceTimer.start(0);
        });
        connect(document, &MarkdownDocument::cleared, this, [this]() {
            m_documentCleared = true;
            m_workspaceTimer.start(0);
        });
    }

    connect(m_engine, &WriterEngineClient::responseReceived, this, &StoryIntelligenceController::handleResponse);
    connect(m_engine, &WriterEngineClient::readyChanged, this, [this](bool ready) {
        if (ready && !m_projectRoot.isEmpty()) {
            refreshProjectUnderstanding();
        }
    });
    connect(m_engine, &WriterEngineClient::requestsInvalidated, this, [this](const QStringList &ids) {
        const bool chatInvalidated = !m_chatRequestId.isEmpty() && ids.contains(m_chatRequestId);
        const bool understandingInvalidated = !m_projectUnderstandingRequestId.isEmpty() && ids.contains(m_projectUnderstandingRequestId);
        const bool sourcesInvalidated = !m_projectSourcesRequestId.isEmpty() && ids.contains(m_projectSourcesRequestId);
        const bool overrideInvalidated = !m_projectOverrideRequestId.isEmpty() && ids.contains(m_projectOverrideRequestId);
        const bool manuscriptSourcesInvalidated = !m_manuscriptSourcesRequestId.isEmpty() && ids.contains(m_manuscriptSourcesRequestId);
        const bool manuscriptOrderInvalidated = !m_manuscriptOrderRequestId.isEmpty() && ids.contains(m_manuscriptOrderRequestId);
        const bool branchListInvalidated = !m_branchListRequestId.isEmpty() && ids.contains(m_branchListRequestId);
        const bool branchCreateInvalidated = !m_branchCreateRequestId.isEmpty() && ids.contains(m_branchCreateRequestId);
        const bool branchDetailInvalidated = !m_branchDetailRequestId.isEmpty() && ids.contains(m_branchDetailRequestId);
        const bool branchRebaseInvalidated = !m_branchRebaseRequestId.isEmpty() && ids.contains(m_branchRebaseRequestId);
        const bool branchPrepareMergeInvalidated = !m_branchPrepareMergeRequestId.isEmpty() && ids.contains(m_branchPrepareMergeRequestId);
        const bool branchApplyMergeInvalidated = !m_branchApplyMergeRequestId.isEmpty() && ids.contains(m_branchApplyMergeRequestId);
        const bool storyToolInvalidated =
            m_pendingChat.storyEngineTool.active && !m_pendingChat.storyEngineTool.requestId.isEmpty() && ids.contains(m_pendingChat.storyEngineTool.requestId);

        if (understandingInvalidated)
            m_projectUnderstandingRequestId.clear();
        if (sourcesInvalidated)
            m_projectSourcesRequestId.clear();
        if (overrideInvalidated)
            m_projectOverrideRequestId.clear();
        if (manuscriptSourcesInvalidated)
            m_manuscriptSourcesRequestId.clear();
        if (manuscriptOrderInvalidated)
            m_manuscriptOrderRequestId.clear();
        if (branchListInvalidated)
            m_branchListRequestId.clear();
        if (branchCreateInvalidated)
            m_branchCreateRequestId.clear();
        if (branchDetailInvalidated)
            m_branchDetailRequestId.clear();
        if (branchRebaseInvalidated)
            m_branchRebaseRequestId.clear();
        if (branchPrepareMergeInvalidated)
            m_branchPrepareMergeRequestId.clear();
        if (branchApplyMergeInvalidated)
            m_branchApplyMergeRequestId.clear();
        if (branchPrepareMergeInvalidated || branchApplyMergeInvalidated) {
            m_pendingBranchMergeBranch.clear();
            m_pendingBranchMergeOverlayIds = {};
        }

        if (storyToolInvalidated)
            m_pendingChat.storyEngineTool = {};

        if (chatInvalidated || m_pendingChat.asyncTool.active || storyToolInvalidated) {
            m_chatRequestId.clear();
            m_toolWaitTimer.stop();
            resetPendingChat();
            m_widget->setBusy(false);
            m_widget->setStatusMessage(tr("Engine restarted; resend the last message."));
        } else if (understandingInvalidated || sourcesInvalidated || overrideInvalidated || manuscriptSourcesInvalidated || manuscriptOrderInvalidated
                   || branchListInvalidated || branchCreateInvalidated || branchDetailInvalidated || branchRebaseInvalidated || branchPrepareMergeInvalidated
                   || branchApplyMergeInvalidated) {
            // readyChanged will rebuild Project Understanding after the sidecar
            // comes back. Clearing these IDs here is essential: otherwise a
            // canceled source-review request permanently blocks Retry.
            m_widget->setStatusMessage(tr("Engine restarted; project understanding will refresh."));
        }
    });
    connect(m_credentials, &CredentialStore::loaded, this, &StoryIntelligenceController::handleCredentialLoaded);
    connect(m_credentials, &CredentialStore::error, this, &StoryIntelligenceController::handleCredentialError);
    connect(m_editor->document(), &QTextDocument::contentsChange, this, [this](int, int, int) {
        ++m_revision;
        QTimer::singleShot(0, this, [this]() {
            if (m_started && !m_loadingWorkspace && currentDocumentPath() == m_workspaceDocument)
                restoreMarkers();
        });
    });
}

void StoryIntelligenceController::setToolServices(StoryToolHarness *harness, AgentEditTransactionManager *transactions, DocumentActivityTracker *activity)
{
    m_harness = harness;
    m_transactions = transactions;
    m_activity = activity;
    if (m_transactions) {
        m_transactions->setProjectRoot(m_projectRoot);
    }
    if (m_activity) {
        connect(m_activity, &DocumentActivityTracker::activityEvent, this, [this](const QJsonObject &event) {
            if (m_projectRoot.isEmpty() || !m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("story_writer_model_observe"))) {
                return;
            }
            QJsonObject payload{{QStringLiteral("project_root"), m_projectRoot},
                                {QStringLiteral("events"), QJsonArray{event}},
                                {QStringLiteral("scope_kind"), QStringLiteral("project")},
                                {QStringLiteral("scope_id"), QString()}};
            // Local sidecar observation only. The result is intentionally not
            // surfaced as a chat event; hypotheses remain provisional until the
            // writer reviews them in Story Lab.
            m_engine->send(QStringLiteral("story_writer_model_observe"), payload);
        });
    }
}

void StoryIntelligenceController::start()
{
    refreshProviderSummary();
    const QString savedProject = QSettings().value(QStringLiteral("story/projectRoot")).toString();
    if (!savedProject.isEmpty() && QDir(savedProject).exists()) {
        loadProject(savedProject);
    } else {
        m_widget->setProjectFolder(QString());
        m_metadata = defaultMetadata();
        m_widget->setSceneContext(m_metadata.value(QStringLiteral("scene_context")).toObject());
        m_widget->setCharacters(m_metadata.value(QStringLiteral("characters")).toArray());
        emit projectRootChanged(QString());
    }
    m_started = true;
    openWorkspaceForDocument();
}

QString StoryIntelligenceController::projectRoot() const
{
    return m_projectRoot;
}

QJsonObject StoryIntelligenceController::defaultMetadata() const
{
    QJsonObject result;
    result.insert(QStringLiteral("version"), 1);
    result.insert(QStringLiteral("scene_context"), QJsonObject());
    result.insert(QStringLiteral("characters"), QJsonArray());
    return result;
}

void StoryIntelligenceController::refreshProviderSummary()
{
    const QJsonObject providerConfig = providerSettings();
    QSettings settings;
    settings.beginGroup(QStringLiteral("prose/provider"));
    const QString savedCredentialId = settings.value(QStringLiteral("credential_id")).toString();
    settings.endGroup();
    const QString expectedCredentialId = providerCredentialId(providerConfig);
    m_widget->setProviderSummary(providerDisplayName(providerConfig.value(QStringLiteral("provider")).toString()),
                                 providerConfig.value(QStringLiteral("model")).toString(),
                                 !savedCredentialId.isEmpty() && savedCredentialId == expectedCredentialId,
                                 providerConfig.value(QStringLiteral("provider")).toString());
}

QJsonObject StoryIntelligenceController::providerSettings() const
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
    provider.insert(QStringLiteral("_desktop_no_environment"), true);
    settings.endGroup();
    const auto agent = activeAgent();
    if (!agent.value("model").toString().isEmpty()) {
        const auto kind = agent.value("provider").toString();
        if (!kind.isEmpty() && kind != provider.value("provider").toString()) {
            const auto preset = QJsonObject::fromVariantMap(settings.value(QStringLiteral("prose/providerPresets/") + kind).toMap());
            provider = QJsonObject{{"provider", kind},
                                   {"temperature", preset.value("temperature").toDouble(0.7)},
                                   {"max_tokens", preset.value("max_tokens").toInt(4096)},
                                   {"timeout", preset.value("timeout").toInt(180)},
                                   {"_desktop_no_environment", true}};
        }
        provider.insert("model", agent.value("model"));
        if (!kind.isEmpty()) {
            provider.insert("provider", kind);
            provider.insert("base_url", agent.value("base_url"));
        }
    }
    return provider;
}

QString StoryIntelligenceController::providerCredentialId(const QJsonObject &provider) const
{
    const QString kind = provider.value(QStringLiteral("provider")).toString();
    if (kind == QStringLiteral("opencode_zen") || kind == QStringLiteral("opencode_go")) {
        QSettings settings;
        settings.beginGroup(QStringLiteral("prose/provider"));
        const QString storedKind = settings.value(QStringLiteral("provider")).toString();
        const QString storedId = settings.value(QStringLiteral("credential_id")).toString();
        settings.endGroup();
        if (storedKind == kind && !storedId.isEmpty())
            return storedId;
    }
    return CredentialStore::providerCredentialId(provider.value(QStringLiteral("provider")).toString(),
                                                 QUrl(provider.value(QStringLiteral("base_url")).toString().trimmed()),
                                                 provider.value(QStringLiteral("model")).toString().trimmed());
}

bool StoryIntelligenceController::providerMayNeedCredential(const QJsonObject &provider) const
{
    const QString kind = provider.value(QStringLiteral("provider")).toString();
    if (kind == QStringLiteral("codex") || kind == QStringLiteral("ollama") || kind == QStringLiteral("lmstudio") || kind == QStringLiteral("llama_cpp")) {
        return false;
    }
    const QUrl endpoint(provider.value(QStringLiteral("base_url")).toString());
    return !(endpoint.host() == QStringLiteral("127.0.0.1") || endpoint.host() == QStringLiteral("localhost") || endpoint.host() == QStringLiteral("::1"));
}

void StoryIntelligenceController::openModelSettings()
{
    auto *dialog = new ProviderSettingsDialog(m_credentials, m_widget);
    dialog->setAttribute(Qt::WA_DeleteOnClose);
    connect(dialog, &QDialog::accepted, this, &StoryIntelligenceController::refreshProviderSummary);
    dialog->open();
}

QJsonArray StoryIntelligenceController::storyRoutingCandidates() const
{
    QSettings settings;
    const QByteArray stored = settings.value(QStringLiteral("story/routingCandidates")).toByteArray();
    if (!stored.isEmpty()) {
        QJsonParseError error;
        const QJsonDocument document = QJsonDocument::fromJson(stored, &error);
        if (error.error == QJsonParseError::NoError && document.isArray() && !document.array().isEmpty()) {
            return document.array();
        }
    }

    QJsonArray candidates;
    QSet<QString> identities;
    const auto appendCandidate = [&candidates, &identities](QJsonObject candidate) {
        candidate.remove(QStringLiteral("api_key"));
        candidate.remove(QStringLiteral("credential_id"));
        candidate.remove(QStringLiteral("_desktop_no_environment"));
        const QString provider = candidate.value(QStringLiteral("provider")).toString().trimmed();
        const QString model = candidate.value(QStringLiteral("model")).toString().trimmed();
        const QString endpoint = candidate.value(QStringLiteral("base_url")).toString().trimmed();
        if (provider.isEmpty() || model.isEmpty() || endpoint.isEmpty()) {
            return;
        }
        const QString identity = QStringLiteral("%1\n%2\n%3").arg(provider, model, endpoint);
        if (identities.contains(identity)) {
            return;
        }
        identities.insert(identity);
        candidate.insert(QStringLiteral("roles"), candidate.value(QStringLiteral("roles")).toArray());
        candidate.insert(QStringLiteral("quality"), candidate.value(QStringLiteral("quality")).toString(QStringLiteral("balanced")));
        candidate.insert(QStringLiteral("priority"), candidates.size());
        candidates.append(candidate);
    };

    appendCandidate(providerSettings());
    const QStringList providers = {
        QStringLiteral("gemini"),
        QStringLiteral("gemini_oauth"),
        QStringLiteral("codex"),
        QStringLiteral("openai"),
        QStringLiteral("openai_compatible"),
        QStringLiteral("openrouter"),
        QStringLiteral("opencode_zen"),
        QStringLiteral("opencode_go"),
        QStringLiteral("anthropic"),
        QStringLiteral("ollama"),
        QStringLiteral("lmstudio"),
        QStringLiteral("llama_cpp"),
    };
    for (const QString &provider : providers) {
        QJsonObject preset = QJsonObject::fromVariantMap(settings.value(QStringLiteral("prose/providerPresets/") + provider).toMap());
        if (preset.isEmpty()) {
            continue;
        }
        preset.insert(QStringLiteral("provider"), provider);
        appendCandidate(preset);
    }
    return candidates;
}

void StoryIntelligenceController::openStoryRoutingSettings()
{
    QDialog dialog(m_widget);
    dialog.setWindowTitle(tr("Story Intelligence Routing"));
    dialog.resize(860, 520);
    auto *layout = new QVBoxLayout(&dialog);
    auto *hint = new QLabel(tr("Automatic routing classifies the story task, then selects among models you have configured. "
                               "Provider names are interchangeable candidates; Story Engine behavior does not depend on any vendor."),
                            &dialog);
    hint->setWordWrap(true);
    layout->addWidget(hint);

    QSettings settings;
    auto *enabled = new QCheckBox(tr("Route Story Intelligence automatically"), &dialog);
    enabled->setChecked(settings.value(QStringLiteral("story/routingEnabled"), false).toBool());
    layout->addWidget(enabled);

    auto *form = new QFormLayout;
    auto *quality = new QComboBox(&dialog);
    quality->addItem(tr("Fast"), QStringLiteral("fast"));
    quality->addItem(tr("Balanced"), QStringLiteral("balanced"));
    quality->addItem(tr("Quality"), QStringLiteral("quality"));
    quality->setCurrentIndex(qMax(0, quality->findData(settings.value(QStringLiteral("story/routingQuality"), QStringLiteral("balanced")).toString())));
    auto *privacy = new QComboBox(&dialog);
    privacy->addItem(tr("Local only"), QStringLiteral("local_only"));
    privacy->addItem(tr("Prefer local"), QStringLiteral("prefer_local"));
    privacy->addItem(tr("Allow remote"), QStringLiteral("allow_remote"));
    privacy->setCurrentIndex(qMax(0, privacy->findData(settings.value(QStringLiteral("story/routingPrivacy"), QStringLiteral("prefer_local")).toString())));
    form->addRow(tr("Quality preset"), quality);
    form->addRow(tr("Privacy preset"), privacy);
    layout->addLayout(form);

    const QJsonArray candidates = storyRoutingCandidates();
    auto *table = new QTableWidget(candidates.size(), 5, &dialog);
    table->setHorizontalHeaderLabels({tr("Use"), tr("Provider"), tr("Model"), tr("Task roles"), tr("Candidate profile")});
    table->verticalHeader()->setVisible(false);
    table->horizontalHeader()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    table->horizontalHeader()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    table->horizontalHeader()->setSectionResizeMode(2, QHeaderView::Stretch);
    table->horizontalHeader()->setSectionResizeMode(3, QHeaderView::Stretch);
    table->horizontalHeader()->setSectionResizeMode(4, QHeaderView::ResizeToContents);
    for (int row = 0; row < candidates.size(); ++row) {
        const QJsonObject candidate = candidates.at(row).toObject();
        auto *use = new QTableWidgetItem;
        use->setFlags(Qt::ItemIsEnabled | Qt::ItemIsUserCheckable | Qt::ItemIsSelectable);
        use->setCheckState(Qt::Checked);
        table->setItem(row, 0, use);
        auto *provider = new QTableWidgetItem(candidate.value(QStringLiteral("provider")).toString());
        provider->setFlags(provider->flags() & ~Qt::ItemIsEditable);
        provider->setData(Qt::UserRole, candidate.value(QStringLiteral("base_url")).toString());
        table->setItem(row, 1, provider);
        auto *model = new QTableWidgetItem(candidate.value(QStringLiteral("model")).toString());
        model->setFlags(model->flags() & ~Qt::ItemIsEditable);
        table->setItem(row, 2, model);
        QStringList roles;
        for (const QJsonValue &role : candidate.value(QStringLiteral("roles")).toArray()) {
            roles << role.toString();
        }
        table->setItem(row, 3, new QTableWidgetItem(roles.join(QStringLiteral(", "))));
        table->setItem(row, 4, new QTableWidgetItem(candidate.value(QStringLiteral("quality")).toString(QStringLiteral("balanced"))));
    }
    layout->addWidget(table, 1);
    auto *rolesHint = new QLabel(tr("Optional task roles: continuity, structural, creative, line_edit, cold_reader, council, lens, "
                                    "reader_experience, extraction, chat. Separate roles with commas. Candidate profile: fast, balanced, or quality."),
                                 &dialog);
    rolesHint->setWordWrap(true);
    layout->addWidget(rolesHint);

    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Save | QDialogButtonBox::Cancel, &dialog);
    connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(buttons);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }

    QJsonArray saved;
    for (int row = 0; row < table->rowCount(); ++row) {
        if (!table->item(row, 0) || table->item(row, 0)->checkState() != Qt::Checked) {
            continue;
        }
        const QString provider = table->item(row, 1)->text().trimmed();
        const QString model = table->item(row, 2)->text().trimmed();
        const QString endpoint = table->item(row, 1)->data(Qt::UserRole).toString().trimmed();
        if (provider.isEmpty() || model.isEmpty() || endpoint.isEmpty()) {
            continue;
        }
        QJsonArray roles;
        const QStringList roleParts = table->item(row, 3)->text().split(QChar(','), Qt::SkipEmptyParts);
        for (QString role : roleParts) {
            role = role.trimmed().toCaseFolded();
            if (!role.isEmpty()) {
                roles.append(role);
            }
        }
        QString candidateQuality = table->item(row, 4)->text().trimmed().toCaseFolded();
        if (candidateQuality != QStringLiteral("fast") && candidateQuality != QStringLiteral("balanced") && candidateQuality != QStringLiteral("quality")) {
            candidateQuality = QStringLiteral("balanced");
        }
        saved.append(QJsonObject{{QStringLiteral("provider"), provider},
                                 {QStringLiteral("model"), model},
                                 {QStringLiteral("base_url"), endpoint},
                                 {QStringLiteral("roles"), roles},
                                 {QStringLiteral("quality"), candidateQuality},
                                 {QStringLiteral("priority"), saved.size()}});
    }
    settings.setValue(QStringLiteral("story/routingEnabled"), enabled->isChecked());
    settings.setValue(QStringLiteral("story/routingQuality"), quality->currentData().toString());
    settings.setValue(QStringLiteral("story/routingPrivacy"), privacy->currentData().toString());
    settings.setValue(QStringLiteral("story/routingCandidates"), QJsonDocument(saved).toJson(QJsonDocument::Compact));
    m_widget->setStatusMessage(enabled->isChecked() ? tr("Task-aware Story Intelligence routing enabled")
                                                    : tr("Story Intelligence routing set to manual provider mode"));
}

void StoryIntelligenceController::openStoryLab()
{
    if (m_projectRoot.isEmpty()) {
        MessageBoxHelper::information(m_widget, tr("Story Project required"), tr("Choose a Story Project folder first."));
        return;
    }
    if (!m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("story_tool"))) {
        MessageBoxHelper::information(m_widget, tr("Story Engine unavailable"), tr("Story Lab is waiting for the ThothPad Story Engine."));
        return;
    }
    openWorkspaceForDocument();
    reconcileWorkspaceHeadings();
    refreshWorkspace();
    const QJsonObject context = workspaceContext();
    const QJsonObject scope = context.value(QStringLiteral("scope")).toObject();
    QString sourcePath;
    const QString documentPath = currentDocumentPath();
    if (!documentPath.isEmpty()) {
        sourcePath = QDir(m_projectRoot).relativeFilePath(documentPath).replace(QChar('\\'), QChar('/'));
        if (sourcePath.startsWith(QStringLiteral("../")) || sourcePath == QStringLiteral("..")) {
            sourcePath.clear();
        }
    }
    StoryLabDialog dialog(m_engine, m_widget);
    dialog.setStoryContext(m_projectRoot,
                           m_lastActiveStoryUnit,
                           m_activeBranch,
                           sourcePath,
                           scope.value(QStringLiteral("title")).toString(),
                           activeCharacter().value(QStringLiteral("name")).toString());
    dialog.exec();
}

void StoryIntelligenceController::chooseProjectFolder()
{
    const QString start = m_projectRoot.isEmpty() ? QDir::homePath() : m_projectRoot;
    const QString selected =
        QFileDialog::getExistingDirectory(m_widget, tr("Open Story Project"), start, QFileDialog::ShowDirsOnly | QFileDialog::DontResolveSymlinks);
    if (selected.isEmpty()) {
        return;
    }
    loadProject(selected);
}

void StoryIntelligenceController::refreshProjectUnderstanding()
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("story_project_understanding"))) {
        return;
    }
    if (!m_projectUnderstandingRequestId.isEmpty()) {
        m_engine->cancel(m_projectUnderstandingRequestId);
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("project_root"), m_projectRoot);
    m_projectUnderstandingRequestId = m_engine->send(QStringLiteral("story_project_understanding"), payload);
    if (!m_projectUnderstandingRequestId.isEmpty()) {
        m_widget->setStatusMessage(tr("Understanding project…"));
    }
}

void StoryIntelligenceController::refreshBranches()
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("story_tool"))) {
        m_branches = {};
        m_widget->setBranches({}, QStringLiteral("mainline"));
        return;
    }
    if (!m_branchListRequestId.isEmpty()) {
        m_engine->cancel(m_branchListRequestId);
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("project_root"), m_projectRoot);
    payload.insert(QStringLiteral("tool_id"), QStringLiteral("list_branches"));
    payload.insert(QStringLiteral("arguments"), QJsonObject{{QStringLiteral("limit"), 200}});
    m_branchListRequestId = m_engine->send(QStringLiteral("story_tool"), payload);
}

void StoryIntelligenceController::createStoryBranch()
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("story_branch_create"))) {
        m_widget->setStatusMessage(tr("Alternate branches are not available in this engine build."));
        return;
    }
    if (!m_branchCreateRequestId.isEmpty() || !m_pendingChat.prompt.isEmpty() || !m_chatRequestId.isEmpty()) {
        m_widget->setStatusMessage(tr("Finish the current Story Intelligence operation before creating a branch."));
        return;
    }
    bool accepted = false;
    const QString assumption = QInputDialog::getMultiLineText(m_widget,
                                                              tr("New alternate branch"),
                                                              tr("What changes in this alternate? This is a branch assumption, not Mainline canon."),
                                                              QString(),
                                                              &accepted);
    if (!accepted || assumption.trimmed().isEmpty()) {
        return;
    }
    QString detail = tr("Create an isolated alternate from %1?").arg(m_activeBranch == QStringLiteral("mainline") ? tr("Mainline") : m_activeBranch);
    if (!m_lastActiveStoryUnit.isEmpty()) {
        detail += tr("\n\nThothPad will fork at the last resolved stable story position.");
    } else {
        detail += tr("\n\nNo stable story position is resolved yet, so this will be a project-level fork.");
    }
    if (QMessageBox::question(m_widget, tr("Create alternate branch?"), detail, QMessageBox::Yes | QMessageBox::Cancel, QMessageBox::Cancel)
        != QMessageBox::Yes) {
        return;
    }
    QJsonObject payload{{QStringLiteral("project_root"), m_projectRoot},
                        {QStringLiteral("parent_branch"), m_activeBranch},
                        {QStringLiteral("assumptions"), QJsonArray{assumption.trimmed().left(2000)}},
                        {QStringLiteral("writer_confirmed"), true}};
    if (!m_lastActiveStoryUnit.isEmpty()) {
        payload.insert(QStringLiteral("fork_story_unit"), m_lastActiveStoryUnit);
    }
    m_branchCreateRequestId = m_engine->send(QStringLiteral("story_branch_create"), payload);
    if (!m_branchCreateRequestId.isEmpty()) {
        m_widget->setStatusMessage(tr("Creating isolated alternate branch…"));
    }
}

void StoryIntelligenceController::showStoryBranchDetails()
{
    if (m_activeBranch == QStringLiteral("mainline")) {
        m_widget->setStatusMessage(tr("Mainline has no overlay diff."));
        return;
    }
    if (m_projectRoot.isEmpty() || !m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("story_tool")) || !m_branchDetailRequestId.isEmpty()) {
        return;
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("project_root"), m_projectRoot);
    payload.insert(QStringLiteral("tool_id"), QStringLiteral("compare_branch"));
    payload.insert(QStringLiteral("arguments"), QJsonObject{{QStringLiteral("branch_id"), m_activeBranch}});
    m_branchDetailRequestId = m_engine->send(QStringLiteral("story_tool"), payload);
    if (!m_branchDetailRequestId.isEmpty()) {
        m_widget->setStatusMessage(tr("Loading branch diff…"));
    }
}

void StoryIntelligenceController::switchStoryBranch(const QString &branchId)
{
    const QString next = branchId.trimmed().isEmpty() ? QStringLiteral("mainline") : branchId.trimmed();
    if (next == m_activeBranch) {
        return;
    }
    if (next != QStringLiteral("mainline")) {
        bool found = false;
        for (const QJsonValue &value : m_branches) {
            const QJsonObject branch = value.toObject();
            if (branch.value(QStringLiteral("branch_id")).toString() == next
                && branch.value(QStringLiteral("status")).toString() != QStringLiteral("DISCARDED")) {
                found = true;
                break;
            }
        }
        if (!found) {
            m_widget->setBranches(m_branches, m_activeBranch);
            m_widget->setStatusMessage(tr("That branch is unavailable."));
            return;
        }
    }

    if (!m_chatRequestId.isEmpty()) {
        m_engine->cancel(m_chatRequestId);
        m_chatRequestId.clear();
    }
    if (m_pendingChat.storyEngineTool.active && !m_pendingChat.storyEngineTool.requestId.isEmpty()) {
        m_engine->cancel(m_pendingChat.storyEngineTool.requestId);
    }
    for (QString *requestId : {&m_branchPrepareMergeRequestId, &m_branchApplyMergeRequestId}) {
        if (!requestId->isEmpty()) {
            m_engine->cancel(*requestId);
            requestId->clear();
        }
    }
    m_pendingBranchMergeBranch.clear();
    m_pendingBranchMergeOverlayIds = {};
    resetPendingChat();
    m_widget->setBusy(false);
    storeSession();
    saveWorkspace();

    m_activeBranch = next;
    m_sessionId.clear();
    m_history = {};
    m_widget->clearChat();
    for (int index = m_workspace.data.value(QStringLiteral("sessions")).toArray().size() - 1; index >= 0; --index) {
        const QJsonObject candidate = m_workspace.data.value(QStringLiteral("sessions")).toArray().at(index).toObject();
        if (candidate.value(QStringLiteral("scope_id")).toString() == m_scopeId && sessionAvailable(candidate)) {
            m_sessionId = candidate.value(QStringLiteral("id")).toString();
            break;
        }
    }
    if (m_sessionId.isEmpty()) {
        newSession();
    } else {
        restoreSession();
        refreshWorkspace();
        saveWorkspace();
    }
    m_widget->setContextInspector(QJsonObject());
    m_widget->setBranches(m_branches, m_activeBranch);
    m_widget->setStatusMessage(next == QStringLiteral("mainline") ? tr("Story branch: Mainline") : tr("Story branch: %1").arg(next));
}

void StoryIntelligenceController::reviewProjectUnderstanding()
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("story_project_sources"))) {
        m_widget->setStatusMessage(tr("Project understanding is not available in this engine build."));
        return;
    }
    if (!m_projectSourcesRequestId.isEmpty()) {
        return;
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("project_root"), m_projectRoot);
    payload.insert(QStringLiteral("offset"), 0);
    payload.insert(QStringLiteral("limit"), 250);
    m_projectSourcesRequestId = m_engine->send(QStringLiteral("story_project_sources"), payload);
    if (!m_projectSourcesRequestId.isEmpty()) {
        m_widget->setStatusMessage(tr("Loading project sources…"));
    }
}

void StoryIntelligenceController::editManuscriptOrder()
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("story_project_sources"))
        || !m_engine->supportsOperation(QStringLiteral("story_set_manuscript_order"))) {
        m_widget->setStatusMessage(tr("Manuscript ordering is not available in this engine build."));
        return;
    }
    if (!m_manuscriptSourcesRequestId.isEmpty() || !m_manuscriptOrderRequestId.isEmpty()) {
        return;
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("project_root"), m_projectRoot);
    payload.insert(QStringLiteral("offset"), 0);
    payload.insert(QStringLiteral("limit"), 500);
    payload.insert(QStringLiteral("role"), QStringLiteral("manuscript"));
    m_manuscriptSourcesRequestId = m_engine->send(QStringLiteral("story_project_sources"), payload);
    if (!m_manuscriptSourcesRequestId.isEmpty()) {
        m_widget->setStatusMessage(tr("Loading manuscript order…"));
    }
}

void StoryIntelligenceController::loadProject(const QString &root)
{
    QFileInfo info(root);
    QString canonical = info.canonicalFilePath();
    if (canonical.isEmpty()) {
        canonical = info.absoluteFilePath();
    }
    if (!QDir(canonical).exists()) {
        m_widget->setStatusMessage(tr("Project folder is unavailable."));
        return;
    }
    const QString nextProjectRoot = QDir::cleanPath(canonical);
    if (nextProjectRoot != m_projectRoot) {
        if (!m_chatRequestId.isEmpty()) {
            m_engine->cancel(m_chatRequestId);
            m_chatRequestId.clear();
        }
        for (QString *requestId : {
                 &m_projectUnderstandingRequestId,
                 &m_projectSourcesRequestId,
                 &m_projectOverrideRequestId,
                 &m_manuscriptSourcesRequestId,
                 &m_manuscriptOrderRequestId,
                 &m_branchListRequestId,
                 &m_branchCreateRequestId,
                 &m_branchDetailRequestId,
                 &m_branchRebaseRequestId,
                 &m_branchPrepareMergeRequestId,
                 &m_branchApplyMergeRequestId,
             }) {
            if (!requestId->isEmpty()) {
                m_engine->cancel(*requestId);
                requestId->clear();
            }
        }
        if (m_pendingChat.storyEngineTool.active && !m_pendingChat.storyEngineTool.requestId.isEmpty()) {
            m_engine->cancel(m_pendingChat.storyEngineTool.requestId);
            m_pendingChat.storyEngineTool = {};
        }
        resetPendingChat();
        m_pendingBranchMergeBranch.clear();
        m_pendingBranchMergeOverlayIds = {};
        m_widget->setBusy(false);
    }
    m_projectRoot = nextProjectRoot;
    m_activeBranch = QStringLiteral("mainline");
    m_lastActiveStoryUnit.clear();
    m_branches = {};
    QSettings().setValue(QStringLiteral("story/projectRoot"), m_projectRoot);
    if (m_transactions) {
        m_transactions->setProjectRoot(m_projectRoot);
    }
    emit projectRootChanged(m_projectRoot);
    m_widget->setProjectFolder(m_projectRoot);
    if (!m_started)
        loadProjectMetadata();
    m_widget->setProjectUnderstanding(QJsonObject());
    m_widget->setBranches({}, m_activeBranch);
    m_widget->setContextInspector(QJsonObject());
    if (m_engine->isReady()) {
        refreshProjectUnderstanding();
    } else {
        m_widget->setStatusMessage(tr("Project loaded · waiting for Story Engine"));
    }
}

QString StoryIntelligenceController::metadataPath() const
{
    if (m_projectRoot.isEmpty()) {
        return {};
    }
    return QDir(m_projectRoot).filePath(QStringLiteral(".thothpad/story-intelligence.json"));
}

void StoryIntelligenceController::loadProjectMetadata()
{
    m_metadata = defaultMetadata();
    const QString path = metadataPath();
    QFile file(path);
    if (file.exists()) {
        if (!file.open(QIODevice::ReadOnly)) {
            m_widget->setStatusMessage(tr("Could not read Story Intelligence metadata."));
        } else {
            QJsonParseError parseError;
            const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
            if (parseError.error == QJsonParseError::NoError && document.isObject()) {
                const QJsonObject loaded = document.object();
                if (loaded.value(QStringLiteral("scene_context")).isObject()) {
                    m_metadata.insert(QStringLiteral("scene_context"), loaded.value(QStringLiteral("scene_context")));
                }
                if (loaded.value(QStringLiteral("characters")).isArray()) {
                    m_metadata.insert(QStringLiteral("characters"), loaded.value(QStringLiteral("characters")));
                }
            } else {
                m_widget->setStatusMessage(tr("Project metadata is invalid; using a blank Story Intelligence state."));
            }
        }
    }
    m_widget->setSceneContext(m_metadata.value(QStringLiteral("scene_context")).toObject());
    m_widget->setCharacters(m_metadata.value(QStringLiteral("characters")).toArray());
}

void StoryIntelligenceController::editSceneContext()
{
    auto context = m_workspace.effectiveContext(m_scopeId);
    if (!editStoryRecord(context, QStringLiteral("scene"), m_widget))
        return;
    const auto before = m_workspace.data;
    auto scope = m_workspace.scope(m_scopeId);
    scope.insert("context", context);
    m_workspace.setScope(scope);
    if (!saveWorkspace())
        m_workspace.data = before;
    refreshWorkspace();
}

void StoryIntelligenceController::addCharacter()
{
    auto character = StoryWorkspace::agentTemplate(QStringLiteral("character"));
    if (!editStoryRecord(character, QStringLiteral("agent"), m_widget))
        return;
    const auto before = m_workspace.data;
    m_workspace.putAgent(character);
    auto scope = m_workspace.scope(m_scopeId);
    auto settings = scope.value("settings").toObject();
    auto cast = m_workspace.effectiveSettings(m_scopeId).value("cast").toArray();
    cast.append(character.value("id"));
    settings.insert("cast", cast);
    scope.insert("settings", settings);
    m_workspace.setScope(scope);
    if (!saveWorkspace())
        m_workspace.data = before;
    refreshWorkspace();
}

void StoryIntelligenceController::editCharacters()
{
    auto character = m_workspace.agent(m_widget->activeCharacterId());
    if (character.isEmpty()) {
        editWorkspace(0);
        return;
    }
    if (!editStoryRecord(character, QStringLiteral("agent"), m_widget))
        return;
    const auto before = m_workspace.data;
    m_workspace.putAgent(character);
    if (!saveWorkspace())
        m_workspace.data = before;
    refreshWorkspace();
}

QJsonObject StoryIntelligenceController::activeCharacter() const
{
    const auto record = m_workspace.agent(m_widget->activeCharacterId());
    return record.value("archived").toBool() ? QJsonObject() : record;
}

QString StoryIntelligenceController::currentStoryContextHash() const
{
    auto context = workspaceContext();
    context.insert("project_root", m_projectRoot);
    context.insert("session_id", m_sessionId);
    context.insert("epistemic_mode", m_epistemicMode);
    context.insert("active_branch", m_activeBranch);
    return QString::fromLatin1(QCryptographicHash::hash(QJsonDocument(context).toJson(QJsonDocument::Compact), QCryptographicHash::Sha256).toHex());
}

void StoryIntelligenceController::appendHistory(const QString &role, const QString &content, const QString &speaker)
{
    QJsonObject message;
    message.insert(QStringLiteral("id"), StoryWorkspace::newId());
    message.insert(QStringLiteral("role"), role);
    message.insert(QStringLiteral("content"), content);
    if (!speaker.isEmpty()) {
        message.insert(QStringLiteral("speaker"), speaker);
    }
    m_history.append(message);
    storeSession();
    saveWorkspace();
}

QJsonArray StoryIntelligenceController::boundedHistory() const
{
    QJsonArray result;
    for (int i = qMax(0, m_history.size() - MaximumHistoryMessages); i < m_history.size(); ++i)
        result.append(m_history[i]);
    return result;
}

QString StoryIntelligenceController::currentDocumentPath() const
{
    if (auto *document = qobject_cast<MarkdownDocument *>(m_editor->document())) {
        return document->filePath();
    }
    return {};
}

QString StoryIntelligenceController::modelSafePath(const QString &path) const
{
    if (path.isEmpty()) {
        return {};
    }
    const QFileInfo file(path);
    if (!m_projectRoot.isEmpty()) {
        const QDir root(m_projectRoot);
        const QString relative = QDir::fromNativeSeparators(root.relativeFilePath(file.absoluteFilePath()));
        if (!relative.isEmpty() && relative != QStringLiteral("..") && !relative.startsWith(QStringLiteral("../")) && !QDir::isAbsolutePath(relative)) {
            return relative;
        }
    }
    return file.fileName();
}

QJsonObject StoryIntelligenceController::modelSafeToolResult(const QJsonObject &source) const
{
    const QSet<QString> omittedKeys = {
        QStringLiteral("analysis_id"),
        QStringLiteral("baseline_analysis_id"),
        QStringLiteral("project_root"),
        QStringLiteral("target_generation"),
    };

    QJsonObject result;
    for (auto iterator = source.constBegin(); iterator != source.constEnd(); ++iterator) {
        const QString key = iterator.key();
        const QJsonValue value = iterator.value();
        if (key == QStringLiteral("checkpoint_path")) {
            result.insert(QStringLiteral("checkpoint_created"), value.isString() && !value.toString().isEmpty());
            continue;
        }
        if (omittedKeys.contains(key)) {
            continue;
        }
        if (value.isObject()) {
            result.insert(key, modelSafeToolResult(value.toObject()));
            continue;
        }
        if (value.isArray()) {
            QJsonArray safeArray;
            for (const QJsonValue &item : value.toArray()) {
                if (item.isObject()) {
                    safeArray.append(modelSafeToolResult(item.toObject()));
                } else if (item.isString() && key.endsWith(QStringLiteral("paths")) && QDir::isAbsolutePath(item.toString())) {
                    safeArray.append(modelSafePath(item.toString()));
                } else {
                    safeArray.append(item);
                }
            }
            result.insert(key, safeArray);
            continue;
        }
        if (value.isString() && key.endsWith(QStringLiteral("path")) && QDir::isAbsolutePath(value.toString())) {
            result.insert(key, modelSafePath(value.toString()));
            continue;
        }
        result.insert(key, value);
    }
    return result;
}

void StoryIntelligenceController::sendChat(const QString &message)
{
    if (!m_chatRequestId.isEmpty() || m_pendingChat.waitingForCredential || m_pendingChat.asyncTool.active) {
        m_widget->setStatusMessage(tr("Wait for the current response before sending another message."));
        return;
    }
    if (!m_engine->isReady()) {
        m_widget->setStatusMessage(tr("Story Intelligence is waiting for ThothPad Engine."));
        return;
    }
    const QString prompt = message.left(MaximumChatInputCharacters).trimmed();
    if (prompt.isEmpty()) {
        return;
    }

    openWorkspaceForDocument();
    reconcileWorkspaceHeadings();
    refreshWorkspace();
    appendHistory(QStringLiteral("user"), prompt);
    m_widget->appendChatMessage(QStringLiteral("user"), prompt, {}, {}, m_history.last().toObject().value("id").toString());
    m_widget->setBusy(true);

    resetPendingChat();
    m_pendingChat.prompt = prompt;
    m_pendingChat.revision = m_revision;
    QSettings routing;
    const bool routingEnabled = routing.value(QStringLiteral("story/routingEnabled"), false).toBool();
    if (routingEnabled && m_engine->supportsOperation(QStringLiteral("story_model_route"))) {
        QJsonObject payload{{QStringLiteral("prompt"), prompt},
                            {QStringLiteral("candidates"), storyRoutingCandidates()},
                            {QStringLiteral("fallback"), providerSettings()},
                            {QStringLiteral("quality"), routing.value(QStringLiteral("story/routingQuality"), QStringLiteral("balanced")).toString()},
                            {QStringLiteral("privacy"), routing.value(QStringLiteral("story/routingPrivacy"), QStringLiteral("prefer_local")).toString()}};
        m_storyRouteRequestId = m_engine->send(QStringLiteral("story_model_route"), payload);
        if (!m_storyRouteRequestId.isEmpty()) {
            m_widget->setStatusMessage(tr("Choosing the best configured model for this story task…"));
            return;
        }
    }
    m_pendingChat.provider = providerSettings();
    continuePendingChatAfterProviderSelection();
}

void StoryIntelligenceController::continuePendingChatAfterProviderSelection()
{
    if (m_pendingChat.prompt.isEmpty() || m_pendingChat.provider.isEmpty()) {
        m_widget->setBusy(false);
        resetPendingChat();
        return;
    }
    const auto agent = activeAgent();
    QSettings settings;
    settings.beginGroup(QStringLiteral("prose/provider"));
    const QString currentProvider = settings.value(QStringLiteral("provider")).toString();
    const QString currentCredentialId = settings.value(QStringLiteral("credential_id")).toString();
    settings.endGroup();
    const QString kind = m_pendingChat.provider.value(QStringLiteral("provider")).toString();
    const QJsonObject preset = QJsonObject::fromVariantMap(settings.value(QStringLiteral("prose/providerPresets/") + kind).toMap());
    const QString presetCredentialId = preset.value(QStringLiteral("credential_id")).toString();
    const QString expectedCredentialId = providerCredentialId(m_pendingChat.provider);
    if (kind == currentProvider && !currentCredentialId.isEmpty()) {
        m_pendingChat.credentialId = currentCredentialId;
    } else if (!presetCredentialId.isEmpty()) {
        m_pendingChat.credentialId = presetCredentialId;
    } else {
        m_pendingChat.credentialId = expectedCredentialId;
    }
    const bool credentialKnown =
        (!m_pendingChat.credentialId.isEmpty() && (m_pendingChat.credentialId == currentCredentialId || m_pendingChat.credentialId == presetCredentialId))
        || !agent.value(QStringLiteral("model")).toString().isEmpty();
    if (credentialKnown && m_credentials->isAvailable()) {
        m_pendingChat.waitingForCredential = true;
        m_credentials->read(m_pendingChat.credentialId);
        return;
    }
    if (storyProviderRequiresSavedCredential(kind)) {
        m_widget->showChatError(
            tr("The routed provider/model has no matching saved credential. Open Model Settings, configure that model, then retry or change Routing "
               "privacy/candidates."));
        resetPendingChat();
        return;
    }
    dispatchPendingChat();
}

void StoryIntelligenceController::handleCredentialLoaded(const QString &credentialId, const QString &secret)
{
    if (!m_pendingChat.waitingForCredential || credentialId != m_pendingChat.credentialId) {
        return;
    }
    m_pendingChat.waitingForCredential = false;
    m_pendingChat.apiKey = secret;
    dispatchPendingChat(secret);
}

void StoryIntelligenceController::handleCredentialError(const QString &credentialId, const QString &message)
{
    if (!m_pendingChat.waitingForCredential || credentialId != m_pendingChat.credentialId) {
        return;
    }
    m_pendingChat.waitingForCredential = false;
    if (providerMayNeedCredential(m_pendingChat.provider)) {
        m_widget->setBusy(false);
        m_widget->setStatusMessage(tr("Secure API key could not be loaded."));
        MessageBoxHelper::warning(m_widget, tr("API key unavailable"), message);
        resetPendingChat();
    } else {
        dispatchPendingChat();
    }
}

void StoryIntelligenceController::dispatchPendingChat(const QString &apiKey)
{
    if (m_pendingChat.prompt.isEmpty()) {
        m_widget->setBusy(false);
        return;
    }
    if (m_pendingChat.asyncTool.active || m_pendingChat.storyEngineTool.active) {
        return;
    }
    if (!apiKey.isEmpty()) {
        m_pendingChat.apiKey = apiKey;
    }

    QJsonObject provider = m_pendingChat.provider;
    if (!m_pendingChat.apiKey.isEmpty()) {
        provider.insert(QStringLiteral("api_key"), m_pendingChat.apiKey);
    }

    QJsonArray history = boundedHistory();
    if (!history.isEmpty()) {
        const QJsonObject last = history.last().toObject();
        if (last.value(QStringLiteral("role")).toString() == QStringLiteral("user")
            && last.value(QStringLiteral("content")).toString() == m_pendingChat.prompt) {
            history.removeLast();
        }
    }

    // Text-changing tools may have completed in a previous tool round, so the
    // current revision/document/context are sampled again for every model turn.
    const auto context = workspaceContext();
    const QJsonObject persona = activeCharacter().isEmpty() ? QJsonObject() : context.value("co_writer").toObject();
    m_pendingChat.revision = m_revision;
    m_pendingChat.documentPath = currentDocumentPath();
    m_pendingChat.storyContextHash = currentStoryContextHash();
    m_pendingChat.speaker = activeAgent().value(QStringLiteral("name")).toString();

    QJsonObject storyPayload;
    storyPayload.insert(QStringLiteral("kind"), QStringLiteral("story_intelligence_v1"));
    storyPayload.insert(QStringLiteral("prompt"), m_pendingChat.prompt);
    storyPayload.insert(QStringLiteral("document"), m_editor->toPlainText());
    // document_path/project_root are consumed only by the local engine's
    // retrieval layer. build_story_messages does not forward them to the model.
    storyPayload.insert(QStringLiteral("document_path"), m_pendingChat.documentPath);
    storyPayload.insert(QStringLiteral("document_revision"), m_pendingChat.revision);
    storyPayload.insert(QStringLiteral("project_root"), m_projectRoot);
    storyPayload.insert(QStringLiteral("scene_context"), m_metadata.value(QStringLiteral("scene_context")).toObject());
    storyPayload.insert(QStringLiteral("characters"), m_metadata.value(QStringLiteral("characters")).toArray());
    storyPayload.insert(QStringLiteral("active_character"), persona);
    storyPayload.insert("co_writer", context.value("co_writer"));
    storyPayload.insert("scope", context.value("scope"));
    storyPayload.insert("memories", context.value("memories"));
    storyPayload.insert("characters", context.value("characters"));
    storyPayload.insert(QStringLiteral("history"), history);
    storyPayload.insert(QStringLiteral("tool_round"), m_pendingChat.toolRound);
    storyPayload.insert(QStringLiteral("tool_results"), m_pendingChat.toolResults);
    storyPayload.insert(QStringLiteral("epistemic_mode"), m_epistemicMode);
    storyPayload.insert(QStringLiteral("active_branch"), m_activeBranch);
    if (!m_pendingChat.modelRouting.isEmpty()) {
        storyPayload.insert(QStringLiteral("model_routing"), m_pendingChat.modelRouting);
    }
    if (m_harness) {
        storyPayload.insert(QStringLiteral("app_state"), m_harness->snapshot());
    }
    storyPayload.insert(QStringLiteral("tool_manifest"), allowedManifest());
    if (m_activity) {
        storyPayload.insert(QStringLiteral("activity_events"), m_activity->recentEvents());
    }

    QJsonObject request;
    request.insert(QStringLiteral("text"), QString::fromUtf8(QJsonDocument(storyPayload).toJson(QJsonDocument::Compact)));
    request.insert(QStringLiteral("profile"), QSettings().value(QStringLiteral("prose/profile"), QStringLiteral("creative-default")).toString());
    request.insert(QStringLiteral("mode"), QStringLiteral("write_from_brief"));
    request.insert(QStringLiteral("passes"), 1);
    request.insert(QStringLiteral("persist"), false);
    request.insert(QStringLiteral("provider"), provider);
    // A chat turn is an explicit user-requested model operation. This consent
    // applies only to this request; it does not enable background remote use.
    request.insert(QStringLiteral("consent"), providerMayNeedCredential(provider));

    m_chatRequestId = m_engine->send(QStringLiteral("rewrite"), request);
    if (m_chatRequestId.isEmpty()) {
        m_widget->setBusy(false);
        m_widget->setStatusMessage(tr("Could not start Story Intelligence request."));
        resetPendingChat();
    }
}

QString StoryIntelligenceController::backendStoryEngineToolId(const QString &toolId) const
{
    return toolId == QStringLiteral("query_project_story_context") ? QStringLiteral("get_story_context") : toolId;
}

QJsonObject StoryIntelligenceController::boundedStoryEngineArguments(const QString &toolId, const QJsonObject &arguments) const
{
    auto boundedLimit = [](const QJsonValue &value, int fallback, int maximum) {
        if (!value.isDouble())
            return fallback;
        const double number = value.toDouble();
        const int integer = static_cast<int>(number);
        if (number != static_cast<double>(integer))
            return fallback;
        return qBound(1, integer, maximum);
    };
    auto boundedString = [](const QJsonValue &value, int maximum) {
        return value.isString() ? value.toString().trimmed().left(maximum) : QString();
    };

    QJsonObject safe;
    if (toolId == QStringLiteral("query_project_story_context")) {
        QString prompt = boundedString(arguments.value(QStringLiteral("prompt")), MaximumChatInputCharacters);
        if (prompt.isEmpty())
            prompt = m_pendingChat.prompt.left(MaximumChatInputCharacters);
        safe.insert(QStringLiteral("prompt"), prompt);
        safe.insert(QStringLiteral("mode"), m_epistemicMode);
        safe.insert(QStringLiteral("branch_id"), m_activeBranch);
        safe.insert(QStringLiteral("maximum_chars"), boundedLimit(arguments.value(QStringLiteral("maximum_chars")), 40000, 40000));
        if (!m_pendingChat.activeStoryUnit.isEmpty())
            safe.insert(QStringLiteral("active_story_unit"), m_pendingChat.activeStoryUnit);

        QString character = activeCharacter().value(QStringLiteral("name")).toString().trimmed();
        if (character.isEmpty() && m_epistemicMode == QStringLiteral("current_pov"))
            character = m_metadata.value(QStringLiteral("scene_context")).toObject().value(QStringLiteral("pov")).toString().trimmed();
        if (!character.isEmpty())
            safe.insert(QStringLiteral("active_character"), character.left(240));
        return safe;
    }

    if (toolId == QStringLiteral("resolve_entity")) {
        safe.insert(QStringLiteral("name"), boundedString(arguments.value(QStringLiteral("name")), 240));
    } else if (toolId == QStringLiteral("get_entity")) {
        safe.insert(QStringLiteral("entity_id"), boundedString(arguments.value(QStringLiteral("entity_id")), 240));
    } else if (toolId == QStringLiteral("find_story_evidence")) {
        safe.insert(QStringLiteral("query"), boundedString(arguments.value(QStringLiteral("query")), 1000));
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 16, 50));
    } else if (toolId == QStringLiteral("query_claims")) {
        const QString entity = boundedString(arguments.value(QStringLiteral("entity")), 240);
        const QString predicate = boundedString(arguments.value(QStringLiteral("predicate")), 120);
        if (!entity.isEmpty())
            safe.insert(QStringLiteral("entity"), entity);
        if (!predicate.isEmpty())
            safe.insert(QStringLiteral("predicate"), predicate);
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 100));
    } else if (toolId == QStringLiteral("get_story_unit")) {
        safe.insert(QStringLiteral("story_unit_id"), boundedString(arguments.value(QStringLiteral("story_unit_id")), 240));
    } else if (toolId == QStringLiteral("list_story_units")) {
        const QString sourceId = boundedString(arguments.value(QStringLiteral("source_id")), 240);
        if (!sourceId.isEmpty())
            safe.insert(QStringLiteral("source_id"), sourceId);
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 100));
    } else if (toolId == QStringLiteral("get_character_knowledge") || toolId == QStringLiteral("get_character_beliefs")) {
        safe.insert(QStringLiteral("character"), boundedString(arguments.value(QStringLiteral("character")), 240));
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 100));
    } else if (toolId == QStringLiteral("get_reader_state")) {
        const QString through = boundedString(arguments.value(QStringLiteral("through_story_unit")), 240);
        if (!through.isEmpty())
            safe.insert(QStringLiteral("through_story_unit"), through);
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 100));
    } else if (toolId == QStringLiteral("query_timeline")) {
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 100));
    } else if (toolId == QStringLiteral("get_world_state")) {
        safe.insert(QStringLiteral("entity"), boundedString(arguments.value(QStringLiteral("entity")), 240));
        const QString stateType = boundedString(arguments.value(QStringLiteral("state_type")), 120);
        if (!stateType.isEmpty())
            safe.insert(QStringLiteral("state_type"), stateType);
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 100));
    } else if (toolId == QStringLiteral("where_is_entity")) {
        safe.insert(QStringLiteral("entity"), boundedString(arguments.value(QStringLiteral("entity")), 240));
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 100));
    } else if (toolId == QStringLiteral("who_has_object")) {
        safe.insert(QStringLiteral("object"), boundedString(arguments.value(QStringLiteral("object")), 240));
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 100));
    } else if (toolId == QStringLiteral("list_threads") || toolId == QStringLiteral("list_reader_questions")
               || toolId == QStringLiteral("list_dramatic_promises")) {
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 100));
    } else if (toolId == QStringLiteral("trace_causality")) {
        safe.insert(QStringLiteral("record_kind"), boundedString(arguments.value(QStringLiteral("record_kind")), 120));
        safe.insert(QStringLiteral("record_id"), boundedString(arguments.value(QStringLiteral("record_id")), 240));
        const QString direction = boundedString(arguments.value(QStringLiteral("direction")), 20);
        safe.insert(QStringLiteral("direction"),
                    direction == QStringLiteral("upstream") || direction == QStringLiteral("downstream") || direction == QStringLiteral("both")
                        ? direction
                        : QStringLiteral("both"));
        safe.insert(QStringLiteral("maximum_depth"), boundedLimit(arguments.value(QStringLiteral("maximum_depth")), 8, 32));
    } else if (toolId == QStringLiteral("get_decision_history")) {
        const QString character = boundedString(arguments.value(QStringLiteral("character")), 240);
        if (!character.isEmpty())
            safe.insert(QStringLiteral("character"), character);
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 100));
    } else if (toolId == QStringLiteral("get_opposition_state")) {
        const QString objective = boundedString(arguments.value(QStringLiteral("objective_id")), 240);
        if (!objective.isEmpty())
            safe.insert(QStringLiteral("objective_id"), objective);
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 100));
    } else if (toolId == QStringLiteral("get_scene_contract")) {
        safe.insert(QStringLiteral("story_unit_id"), boundedString(arguments.value(QStringLiteral("story_unit_id")), 240));
    } else if (toolId == QStringLiteral("get_author_decisions")) {
        const QString storyUnit = boundedString(arguments.value(QStringLiteral("story_unit_id")), 240);
        if (!storyUnit.isEmpty())
            safe.insert(QStringLiteral("story_unit_id"), storyUnit);
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 100));
    } else if (toolId == QStringLiteral("audit_scene") || toolId == QStringLiteral("audit_chapter")) {
        safe.insert(QStringLiteral("story_unit_id"), boundedString(arguments.value(QStringLiteral("story_unit_id")), 240));
    } else if (toolId == QStringLiteral("list_branches")) {
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 100));
    } else if (toolId == QStringLiteral("compare_branch")) {
        safe.insert(QStringLiteral("branch_id"), boundedString(arguments.value(QStringLiteral("branch_id")), 240));
    } else if (toolId == QStringLiteral("get_retcon_impact")) {
        safe.insert(QStringLiteral("source_kind"), boundedString(arguments.value(QStringLiteral("source_kind")), 120));
        safe.insert(QStringLiteral("source_id"), boundedString(arguments.value(QStringLiteral("source_id")), 240));
        safe.insert(QStringLiteral("maximum_nodes"), boundedLimit(arguments.value(QStringLiteral("maximum_nodes")), 5000, 5000));
    } else if (toolId == QStringLiteral("cold_reader_at")) {
        safe.insert(QStringLiteral("story_unit_id"), boundedString(arguments.value(QStringLiteral("story_unit_id")), 240));
        safe.insert(QStringLiteral("prompt"), boundedString(arguments.value(QStringLiteral("prompt")), MaximumChatInputCharacters));
        safe.insert(QStringLiteral("maximum_chars"), boundedLimit(arguments.value(QStringLiteral("maximum_chars")), 40000, 100000));
    } else if (toolId == QStringLiteral("audit_reveal_fairness") || toolId == QStringLiteral("get_reader_expectations")) {
        safe.insert(QStringLiteral("story_unit_id"), boundedString(arguments.value(QStringLiteral("story_unit_id")), 240));
        const QString claimId = boundedString(arguments.value(QStringLiteral("claim_id")), 240);
        if (toolId == QStringLiteral("audit_reveal_fairness") && !claimId.isEmpty())
            safe.insert(QStringLiteral("claim_id"), claimId);
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 500));
    } else if (toolId == QStringLiteral("get_dramatic_irony")) {
        safe.insert(QStringLiteral("story_unit_id"), boundedString(arguments.value(QStringLiteral("story_unit_id")), 240));
        safe.insert(QStringLiteral("character"), boundedString(arguments.value(QStringLiteral("character")), 240));
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 500));
    } else if (toolId == QStringLiteral("get_writer_model")) {
        const QString scopeKind = boundedString(arguments.value(QStringLiteral("scope_kind")), 80);
        const QString scopeId = boundedString(arguments.value(QStringLiteral("scope_id")), 240);
        if (!scopeKind.isEmpty())
            safe.insert(QStringLiteral("scope_kind"), scopeKind);
        if (!scopeId.isEmpty())
            safe.insert(QStringLiteral("scope_id"), scopeId);
        safe.insert(QStringLiteral("include_ignored"), arguments.value(QStringLiteral("include_ignored")).toBool(false));
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 100, 500));
    } else if (toolId == QStringLiteral("explain_writer_preference")) {
        safe.insert(QStringLiteral("preference_id"), boundedString(arguments.value(QStringLiteral("preference_id")), 240));
    } else if (toolId == QStringLiteral("run_editorial_council")) {
        safe.insert(QStringLiteral("story_unit_id"), boundedString(arguments.value(QStringLiteral("story_unit_id")), 240));
    } else if (toolId == QStringLiteral("list_story_lenses")) {
        safe.insert(QStringLiteral("include_archived"), arguments.value(QStringLiteral("include_archived")).toBool(false));
    } else if (toolId == QStringLiteral("get_story_lens")) {
        safe.insert(QStringLiteral("lens_id"), boundedString(arguments.value(QStringLiteral("lens_id")), 240));
    } else if (toolId == QStringLiteral("run_story_lens")) {
        safe.insert(QStringLiteral("lens_id"), boundedString(arguments.value(QStringLiteral("lens_id")), 240));
        safe.insert(QStringLiteral("maximum_findings"), boundedLimit(arguments.value(QStringLiteral("maximum_findings")), 100, 500));
    } else if (toolId == QStringLiteral("get_reader_experience")) {
        safe.insert(QStringLiteral("story_unit_id"), boundedString(arguments.value(QStringLiteral("story_unit_id")), 240));
    } else if (toolId == QStringLiteral("get_reader_experience_timeline")) {
        const QString sourceId = boundedString(arguments.value(QStringLiteral("source_id")), 240);
        if (!sourceId.isEmpty())
            safe.insert(QStringLiteral("source_id"), sourceId);
        safe.insert(QStringLiteral("maximum_units"), boundedLimit(arguments.value(QStringLiteral("maximum_units")), 200, 500));
    } else if (toolId == QStringLiteral("explore_story")) {
        safe.insert(QStringLiteral("query"), boundedString(arguments.value(QStringLiteral("query")), 1000));
        safe.insert(QStringLiteral("limit"), boundedLimit(arguments.value(QStringLiteral("limit")), 50, 100));
    } else if (toolId == QStringLiteral("get_scene_semantics") || toolId == QStringLiteral("audit_ending_integrity")) {
        safe.insert(QStringLiteral("story_unit_id"), boundedString(arguments.value(QStringLiteral("story_unit_id")), 240));
    } else if (toolId == QStringLiteral("audit_continuity")) {
        safe.insert(QStringLiteral("story_unit_id"), boundedString(arguments.value(QStringLiteral("story_unit_id")), 240));
        const QString character = boundedString(arguments.value(QStringLiteral("character")), 240);
        if (!character.isEmpty())
            safe.insert(QStringLiteral("character"), character);
    } else if (toolId == QStringLiteral("get_character_arc")) {
        safe.insert(QStringLiteral("character"), boundedString(arguments.value(QStringLiteral("character")), 240));
    } else if (toolId == QStringLiteral("get_relationship_arc")) {
        safe.insert(QStringLiteral("entity_a"), boundedString(arguments.value(QStringLiteral("entity_a")), 240));
        safe.insert(QStringLiteral("entity_b"), boundedString(arguments.value(QStringLiteral("entity_b")), 240));
    } else if (toolId == QStringLiteral("run_wow_acceptance")) {
        const QString storyUnit = boundedString(arguments.value(QStringLiteral("story_unit_id")), 240);
        const QString character = boundedString(arguments.value(QStringLiteral("character")), 240);
        if (!storyUnit.isEmpty())
            safe.insert(QStringLiteral("story_unit_id"), storyUnit);
        if (!character.isEmpty())
            safe.insert(QStringLiteral("character"), character);
    } else if (toolId == QStringLiteral("get_project_health") || toolId == QStringLiteral("get_index_status")) {
        // These diagnostics take no model-controlled arguments beyond the
        // branch inserted below.
    }
    if (m_activeBranch != QStringLiteral("mainline") && toolId != QStringLiteral("compare_branch") && toolId != QStringLiteral("list_branches")
        && toolId != QStringLiteral("query_project_story_context")) {
        safe.insert(QStringLiteral("branch_id"), m_activeBranch);
    }
    return safe;
}

QString StoryIntelligenceController::toolRisk(const QString &toolId) const
{
    if (toolId == "get_story_context" || toolId == "list_story_scopes" || toolId == "read_story_scope" || toolId == "search_manuscript")
        return QStringLiteral("R0");
    if (isStoryEngineReadTool(toolId))
        return QStringLiteral("R0");
    if (!m_harness) {
        return {};
    }
    const QJsonArray manifest = m_harness->manifest();
    for (const QJsonValue &value : manifest) {
        const QJsonObject entry = value.toObject();
        if (entry.value(QStringLiteral("id")).toString() == toolId) {
            return entry.value(QStringLiteral("risk")).toString();
        }
    }
    return {};
}

bool StoryIntelligenceController::authorizeTool(const QString &toolId, const QJsonObject &arguments)
{
    const QString risk = toolRisk(toolId);
    if ((risk == "R3" || risk == "R4") && activeAgent().value("tools").toString() != "edit")
        return false;
    if (risk == QStringLiteral("R0") || risk == QStringLiteral("R1") || risk == QStringLiteral("R2")) {
        return true;
    }
    if (risk != QStringLiteral("R3") && risk != QStringLiteral("R4")) {
        return false;
    }

    QString summary = arguments.value(QStringLiteral("summary")).toString().trimmed();
    if (toolId == QStringLiteral("apply_objective_grammar_fixes") && m_harness) {
        const QJsonObject prose = m_harness->proseSnapshot();
        const int reported = prose.value(QStringLiteral("counts")).toObject().value(QStringLiteral("grammar_mechanics")).toInt();
        int deterministic = 0;
        for (const QJsonValue &value : prose.value(QStringLiteral("findings")).toArray()) {
            const QJsonObject finding = value.toObject();
            if (finding.value(QStringLiteral("category")).toString() == QStringLiteral("grammar_mechanics")
                && finding.value(QStringLiteral("level")).toString() == QStringLiteral("strong_flag")
                && !finding.value(QStringLiteral("replacements")).toArray().isEmpty()) {
                ++deterministic;
            }
        }
        summary = tr("Apply %1 currently verified objective grammar fix(es) from %2 grammar/mechanics finding(s)").arg(deterministic).arg(reported);
    } else if (summary.isEmpty()) {
        summary = toolDisplayName(toolId);
    }

    const bool bulk = risk == QStringLiteral("R4");
    const QString detail = bulk ? tr("Story Intelligence wants to make a bulk manuscript change:\n\n%1\n\n"
                                     "ThothPad will create a durable recovery checkpoint and group the operation into one Undo step. Allow this bulk edit?")
                                      .arg(summary)
                                : tr("Story Intelligence wants to edit the manuscript:\n\n%1\n\n"
                                     "ThothPad will create a recovery checkpoint and one Undo step. Allow this edit?")
                                      .arg(summary);
    return QMessageBox::Yes
        == QMessageBox::question(m_widget,
                                 bulk ? tr("Allow bulk AI edit?") : tr("Allow AI edit?"),
                                 detail,
                                 QMessageBox::Yes | QMessageBox::No,
                                 QMessageBox::No);
}

void StoryIntelligenceController::beginPendingTool(const QString &callId, const QString &toolId, const QJsonObject &nativeResult)
{
    PendingAsyncTool wait;
    wait.active = true;
    wait.callId = callId;
    wait.toolId = toolId;
    wait.waitKind = nativeResult.value(QStringLiteral("wait_kind")).toString();
    wait.category = nativeResult.value(QStringLiteral("category")).toString();
    wait.baselineAnalysisId = nativeResult.value(QStringLiteral("baseline_analysis_id")).toString();
    wait.targetGeneration = static_cast<qint64>(nativeResult.value(QStringLiteral("target_generation")).toDouble());
    wait.revision = m_revision;
    m_pendingChat.asyncTool = wait;
    m_toolWaitTimer.start();

    if (wait.waitKind == QStringLiteral("analysis_snapshot")) {
        m_widget->setStatusMessage(tr("Scanning manuscript · waiting for fresh prose evidence…"));
    } else if (wait.waitKind == QStringLiteral("category_hydration")) {
        m_widget->setStatusMessage(tr("Loading %1 findings · then the co-author will continue…").arg(wait.category));
    } else {
        m_widget->setStatusMessage(tr("Waiting for ThothPad tool completion…"));
    }
}

bool StoryIntelligenceController::pendingToolCompleted() const
{
    if (!m_harness || !m_pendingChat.asyncTool.active) {
        return false;
    }
    const PendingAsyncTool &wait = m_pendingChat.asyncTool;
    const QJsonObject state = m_harness->completionState();
    if (wait.waitKind == QStringLiteral("analysis_snapshot")) {
        const qint64 generation = static_cast<qint64>(state.value(QStringLiteral("analysis_generation")).toDouble());
        const QString analysisId = state.value(QStringLiteral("analysis_id")).toString();
        return generation >= wait.targetGeneration && !analysisId.isEmpty() && analysisId != wait.baselineAnalysisId;
    }
    if (wait.waitKind == QStringLiteral("category_hydration")) {
        return jsonArrayContainsString(state.value(QStringLiteral("hydrated_categories")).toArray(), wait.category);
    }
    return false;
}

void StoryIntelligenceController::finishPendingTool(bool completed, const QString &error)
{
    if (!m_pendingChat.asyncTool.active) {
        return;
    }
    m_toolWaitTimer.stop();
    const PendingAsyncTool wait = m_pendingChat.asyncTool;
    m_pendingChat.asyncTool = {};

    QJsonObject toolResult;
    toolResult.insert(QStringLiteral("call_id"), wait.callId);
    toolResult.insert(QStringLiteral("tool"), wait.toolId);
    toolResult.insert(QStringLiteral("tool_id"), wait.toolId);
    toolResult.insert(QStringLiteral("ok"), completed);

    if (completed && m_harness) {
        QJsonObject nativeResult;
        nativeResult.insert(QStringLiteral("ok"), true);
        nativeResult.insert(QStringLiteral("completed"), true);
        nativeResult.insert(QStringLiteral("pending"), false);
        if (wait.waitKind == QStringLiteral("analysis_snapshot")) {
            nativeResult.insert(QStringLiteral("scope"), QStringLiteral("document"));
        } else if (wait.waitKind == QStringLiteral("category_hydration")) {
            nativeResult.insert(QStringLiteral("category"), wait.category);
        }
        nativeResult.insert(QStringLiteral("prose"), m_harness->proseSnapshot());
        toolResult.insert(QStringLiteral("result"), modelSafeToolResult(nativeResult));
    } else {
        toolResult.insert(QStringLiteral("error"), error.isEmpty() ? tr("The native tool did not complete.") : error);
    }

    m_pendingChat.toolResults.append(toolResult);
    while (m_pendingChat.toolResults.size() > MaximumAccumulatedToolResults) {
        m_pendingChat.toolResults.removeAt(0);
    }

    m_widget->setStatusMessage(completed ? tr("Native evidence ready · resuming co-author…")
                                         : tr("Native tool could not complete · asking co-author to adapt…"));
    dispatchPendingChat();
}

void StoryIntelligenceController::pollPendingTool()
{
    if (!m_pendingChat.asyncTool.active) {
        m_toolWaitTimer.stop();
        return;
    }
    m_pendingChat.asyncTool.elapsedMs += ToolPollIntervalMs;

    if (m_pendingChat.asyncTool.revision != m_revision) {
        finishPendingTool(false, tr("The manuscript changed while the native tool was running, so its pending result was discarded as stale."));
        return;
    }
    if (pendingToolCompleted()) {
        finishPendingTool(true);
        return;
    }
    if (m_pendingChat.asyncTool.elapsedMs >= ToolWaitTimeoutMs) {
        finishPendingTool(false, tr("The native tool did not complete within %1 seconds.").arg(ToolWaitTimeoutMs / 1000));
    }
}

bool StoryIntelligenceController::executeToolCalls(const QJsonArray &toolCalls)
{
    if (toolCalls.isEmpty()) {
        return false;
    }

    QJsonArray results = m_pendingChat.toolResults;
    const int count = qMin(toolCalls.size(), MaximumToolCallsPerRound);
    for (int index = 0; index < count; ++index) {
        const QJsonValue value = toolCalls.at(index);
        QJsonObject toolResult;
        toolResult.insert(QStringLiteral("call_index"), index);
        if (!value.isObject()) {
            toolResult.insert(QStringLiteral("ok"), false);
            toolResult.insert(QStringLiteral("error"), tr("Tool call was not an object."));
            results.append(toolResult);
            continue;
        }

        const QJsonObject call = value.toObject();
        QString toolId = call.value(QStringLiteral("tool")).toString().trimmed();
        if (toolId.isEmpty()) {
            toolId = call.value(QStringLiteral("id")).toString().trimmed();
        }
        QString callId = call.value(QStringLiteral("call_id")).toString().trimmed();
        if (callId.isEmpty()) {
            callId = QStringLiteral("r%1-c%2").arg(m_pendingChat.toolRound).arg(index + 1);
        }
        const QJsonObject arguments = call.value(QStringLiteral("arguments")).toObject();
        toolResult.insert(QStringLiteral("call_id"), callId);
        toolResult.insert(QStringLiteral("tool"), toolId);
        toolResult.insert(QStringLiteral("tool_id"), toolId);

        const QString risk = toolRisk(toolId);
        if (risk.isEmpty()) {
            toolResult.insert(QStringLiteral("ok"), false);
            toolResult.insert(QStringLiteral("error"), tr("Tool is not exposed by this ThothPad build."));
            results.append(toolResult);
            continue;
        }
        if (!authorizeTool(toolId, arguments)) {
            toolResult.insert(QStringLiteral("ok"), false);
            toolResult.insert(QStringLiteral("error"), tr("User did not authorize this tool operation."));
            toolResult.insert(QStringLiteral("denied_by_user"), true);
            results.append(toolResult);
            continue;
        }

        if (isStoryEngineReadTool(toolId)) {
            QJsonObject payload;
            payload.insert(QStringLiteral("project_root"), m_projectRoot);
            payload.insert(QStringLiteral("tool_id"), backendStoryEngineToolId(toolId));
            payload.insert(QStringLiteral("arguments"), boundedStoryEngineArguments(toolId, arguments));
            const QString requestId = m_engine->send(QStringLiteral("story_tool"), payload);
            if (requestId.isEmpty()) {
                toolResult.insert(QStringLiteral("ok"), false);
                toolResult.insert(QStringLiteral("error"), tr("Could not start the Story Engine query."));
                results.append(toolResult);
                continue;
            }

            m_pendingChat.toolResults = results;
            PendingStoryEngineTool pending;
            pending.active = true;
            pending.requestId = requestId;
            pending.callId = callId;
            pending.toolId = toolId;
            pending.projectRoot = m_projectRoot;
            pending.documentPath = currentDocumentPath();
            pending.storyContextHash = currentStoryContextHash();
            pending.revision = m_revision;
            m_pendingChat.storyEngineTool = pending;

            for (int deferredIndex = index + 1; deferredIndex < count; ++deferredIndex) {
                const QJsonObject deferredCall = toolCalls.at(deferredIndex).toObject();
                QString deferredTool = deferredCall.value(QStringLiteral("tool")).toString();
                if (deferredTool.isEmpty())
                    deferredTool = deferredCall.value(QStringLiteral("id")).toString();
                QJsonObject deferred;
                deferred.insert(QStringLiteral("call_id"), deferredCall.value(QStringLiteral("call_id")).toString());
                deferred.insert(QStringLiteral("tool"), deferredTool);
                deferred.insert(QStringLiteral("ok"), false);
                deferred.insert(QStringLiteral("deferred"), true);
                deferred.insert(QStringLiteral("error"),
                                tr("Deferred because a Story Engine query must finish first. Request this tool again if it is still needed."));
                m_pendingChat.toolResults.append(deferred);
            }
            while (m_pendingChat.toolResults.size() > MaximumAccumulatedToolResults)
                m_pendingChat.toolResults.removeAt(0);
            m_widget->setStatusMessage(tr("Querying Story Engine · preserving the active epistemic boundary…"));
            return true;
        }

        const bool boundedEdit = risk == QStringLiteral("R3") || risk == QStringLiteral("R4");
        const bool bulkEdit = risk == QStringLiteral("R4");
        QJsonObject nativeResult = workspaceTool(toolId, arguments);
        if (nativeResult.isEmpty() && m_harness)
            nativeResult = m_harness->execute(toolId, arguments, boundedEdit, bulkEdit);
        if (nativeResult.isEmpty())
            nativeResult = QJsonObject{{QStringLiteral("ok"), false}, {QStringLiteral("error"), tr("Native tool service is unavailable.")}};
        const bool nativeOk = nativeResult.value(QStringLiteral("ok")).toBool();

        if (nativeOk && nativeResult.value(QStringLiteral("pending")).toBool()) {
            m_pendingChat.toolResults = results;
            beginPendingTool(callId, toolId, nativeResult);
            for (int deferredIndex = index + 1; deferredIndex < count; ++deferredIndex) {
                const QJsonObject deferredCall = toolCalls.at(deferredIndex).toObject();
                QString deferredTool = deferredCall.value(QStringLiteral("tool")).toString();
                if (deferredTool.isEmpty()) {
                    deferredTool = deferredCall.value(QStringLiteral("id")).toString();
                }
                QJsonObject deferred;
                deferred.insert(QStringLiteral("call_id"), deferredCall.value(QStringLiteral("call_id")).toString());
                deferred.insert(QStringLiteral("tool"), deferredTool);
                deferred.insert(QStringLiteral("ok"), false);
                deferred.insert(QStringLiteral("deferred"), true);
                deferred.insert(QStringLiteral("error"),
                                tr("Deferred because an asynchronous native tool must finish first. Request this tool again if it is still needed."));
                m_pendingChat.toolResults.append(deferred);
            }
            while (m_pendingChat.toolResults.size() > MaximumAccumulatedToolResults) {
                m_pendingChat.toolResults.removeAt(0);
            }
            return true;
        }

        toolResult.insert(QStringLiteral("ok"), nativeOk);
        toolResult.insert(QStringLiteral("result"), modelSafeToolResult(nativeResult));
        if (!nativeOk) {
            toolResult.insert(QStringLiteral("error"), nativeResult.value(QStringLiteral("error")).toString(tr("Native tool execution failed.")));
        }
        results.append(toolResult);
    }

    if (toolCalls.size() > MaximumToolCallsPerRound) {
        QJsonObject truncated;
        truncated.insert(QStringLiteral("ok"), false);
        truncated.insert(QStringLiteral("error"), tr("Additional tool calls were rejected because the per-round limit is %1.").arg(MaximumToolCallsPerRound));
        results.append(truncated);
    }
    while (results.size() > MaximumAccumulatedToolResults) {
        results.removeAt(0);
    }
    m_pendingChat.toolResults = results;
    return true;
}

void StoryIntelligenceController::handleResponse(const QString &requestId, const QJsonObject &response)
{
    if (m_pendingChat.storyEngineTool.active && requestId == m_pendingChat.storyEngineTool.requestId) {
        const PendingStoryEngineTool pending = m_pendingChat.storyEngineTool;
        m_pendingChat.storyEngineTool = {};

        QJsonObject toolResult;
        toolResult.insert(QStringLiteral("call_id"), pending.callId);
        toolResult.insert(QStringLiteral("tool"), pending.toolId);
        toolResult.insert(QStringLiteral("tool_id"), pending.toolId);

        const bool staleProject = pending.projectRoot != m_projectRoot;
        const bool staleDocument = !StoryToolHarness::modelDocumentContextCurrent(pending.revision, m_revision, pending.documentPath, currentDocumentPath());
        const bool staleStory = pending.storyContextHash != currentStoryContextHash();
        if (staleProject || staleDocument || staleStory) {
            toolResult.insert(QStringLiteral("ok"), false);
            toolResult.insert(QStringLiteral("stale"), true);
            toolResult.insert(
                QStringLiteral("error"),
                tr("The project, manuscript, or epistemic context changed while the Story Engine query was running, so its result was discarded."));
        } else if (!response.value(QStringLiteral("ok")).toBool()) {
            toolResult.insert(QStringLiteral("ok"), false);
            toolResult.insert(QStringLiteral("error"),
                              response.value(QStringLiteral("error")).toObject().value(QStringLiteral("message")).toString(tr("Story Engine query failed.")));
        } else {
            toolResult.insert(QStringLiteral("ok"), true);
            toolResult.insert(QStringLiteral("result"), modelSafeToolResult(response.value(QStringLiteral("result")).toObject()));
        }
        m_pendingChat.toolResults.append(toolResult);
        while (m_pendingChat.toolResults.size() > MaximumAccumulatedToolResults)
            m_pendingChat.toolResults.removeAt(0);
        m_widget->setStatusMessage(toolResult.value(QStringLiteral("ok")).toBool() ? tr("Story Engine evidence ready · resuming co-author…")
                                                                                   : tr("Story Engine query could not be used · asking co-author to adapt…"));
        dispatchPendingChat();
        return;
    }

    if (requestId == m_storyRouteRequestId) {
        m_storyRouteRequestId.clear();
        if (!response.value(QStringLiteral("ok")).toBool()) {
            const QString error = response.value(QStringLiteral("error")).toObject().value(QStringLiteral("message")).toString();
            m_widget->showChatError(error.isEmpty() ? tr("Story Intelligence could not select an eligible model.") : error);
            resetPendingChat();
            return;
        }
        const QJsonObject routing = response.value(QStringLiteral("result")).toObject();
        QJsonObject selected = routing.value(QStringLiteral("selected")).toObject();
        if (selected.value(QStringLiteral("provider")).toString().isEmpty() || selected.value(QStringLiteral("model")).toString().isEmpty()
            || selected.value(QStringLiteral("base_url")).toString().isEmpty()) {
            m_widget->showChatError(tr("Story Intelligence routing returned an incomplete provider selection."));
            resetPendingChat();
            return;
        }
        selected.insert(QStringLiteral("_desktop_no_environment"), true);
        m_pendingChat.provider = selected;
        m_pendingChat.modelRouting = QJsonObject{
            {QStringLiteral("task"), routing.value(QStringLiteral("task"))},
            {QStringLiteral("quality"), routing.value(QStringLiteral("quality"))},
            {QStringLiteral("privacy"), routing.value(QStringLiteral("privacy"))},
            {QStringLiteral("selected_is_remote"), routing.value(QStringLiteral("selected_is_remote"))},
            {QStringLiteral("provider_agnostic"), true},
            {QStringLiteral("reason"), routing.value(QStringLiteral("reason"))},
        };
        m_widget->setStatusMessage(tr("Routed %1 task to %2 · %3")
                                       .arg(routing.value(QStringLiteral("task")).toString(),
                                            providerDisplayName(selected.value(QStringLiteral("provider")).toString()),
                                            selected.value(QStringLiteral("model")).toString()));
        continuePendingChatAfterProviderSelection();
        return;
    }

    if (requestId == m_projectUnderstandingRequestId) {
        m_projectUnderstandingRequestId.clear();
        if (!response.value(QStringLiteral("ok")).toBool()) {
            m_widget->setProjectUnderstanding(QJsonObject());
            m_widget->setStatusMessage(tr("Project understanding could not be compiled."));
            return;
        }
        m_widget->setProjectUnderstanding(response.value(QStringLiteral("result")).toObject());
        m_widget->setStatusMessage(tr("Project understood"));
        refreshBranches();
        return;
    }
    if (requestId == m_branchListRequestId) {
        m_branchListRequestId.clear();
        if (!response.value(QStringLiteral("ok")).toBool()) {
            m_branches = {};
            m_activeBranch = QStringLiteral("mainline");
            m_widget->setBranches({}, m_activeBranch);
            m_widget->setStatusMessage(tr("Story branches could not be loaded; using Mainline."));
            return;
        }
        m_branches = response.value(QStringLiteral("result")).toObject().value(QStringLiteral("branches")).toArray();
        bool activeStillExists = m_activeBranch == QStringLiteral("mainline");
        for (const QJsonValue &value : m_branches) {
            const QJsonObject branch = value.toObject();
            if (branch.value(QStringLiteral("branch_id")).toString() == m_activeBranch
                && branch.value(QStringLiteral("status")).toString() != QStringLiteral("DISCARDED")) {
                activeStillExists = true;
                break;
            }
        }
        if (!activeStillExists) {
            m_activeBranch = QStringLiteral("mainline");
            m_sessionId.clear();
            m_history = {};
            m_widget->clearChat();
            refreshWorkspace();
        }
        m_widget->setBranches(m_branches, m_activeBranch);
        return;
    }
    if (requestId == m_branchCreateRequestId) {
        m_branchCreateRequestId.clear();
        if (!response.value(QStringLiteral("ok")).toBool()) {
            m_widget->setStatusMessage(tr("Alternate branch could not be created."));
            return;
        }
        const QString created =
            response.value(QStringLiteral("result")).toObject().value(QStringLiteral("branch")).toObject().value(QStringLiteral("branch_id")).toString();
        refreshBranches();
        if (!created.isEmpty()) {
            // The refreshed branch list is the authority used to validate a
            // switch. Temporarily include the just-created record so the user
            // can enter it immediately without a race against refreshBranches.
            QJsonObject branch = response.value(QStringLiteral("result")).toObject().value(QStringLiteral("branch")).toObject();
            QJsonObject freshness = response.value(QStringLiteral("result")).toObject().value(QStringLiteral("freshness")).toObject();
            if (!freshness.isEmpty()) {
                branch.insert(QStringLiteral("freshness"), freshness);
            }
            m_branches.append(branch);
            switchStoryBranch(created);
        }
        return;
    }
    if (requestId == m_branchDetailRequestId) {
        m_branchDetailRequestId.clear();
        if (!response.value(QStringLiteral("ok")).toBool()) {
            m_widget->setStatusMessage(tr("Branch details could not be loaded."));
            return;
        }
        const QJsonObject envelope = response.value(QStringLiteral("result")).toObject();
        QJsonObject comparison = envelope.value(QStringLiteral("comparison")).toObject();
        if (comparison.isEmpty()) {
            comparison = envelope; // compatibility with early 1.x sidecars
        }
        const QJsonObject branch = comparison.value(QStringLiteral("branch")).toObject();
        const QString branchId = branch.value(QStringLiteral("branch_id")).toString();
        if (branchId.isEmpty()) {
            m_widget->setStatusMessage(tr("Branch details were incomplete."));
            return;
        }
        const BranchReviewDecision decision = reviewStoryBranch(m_widget,
                                                                comparison,
                                                                m_engine->supportsOperation(QStringLiteral("story_branch_rebase")),
                                                                m_engine->supportsOperation(QStringLiteral("story_branch_apply_merge")));
        if (decision.action == BranchReviewAction::Rebase) {
            if (QMessageBox::question(
                    m_widget,
                    tr("Rebase alternate branch?"),
                    tr("Rebase %1 onto its parent’s current Story State? This changes only the branch base revision; branch overlays remain isolated.")
                        .arg(branchId),
                    QMessageBox::Yes | QMessageBox::Cancel,
                    QMessageBox::Cancel)
                == QMessageBox::Yes) {
                QJsonObject payload{{QStringLiteral("project_root"), m_projectRoot},
                                    {QStringLiteral("branch_id"), branchId},
                                    {QStringLiteral("writer_confirmed"), true}};
                m_branchRebaseRequestId = m_engine->send(QStringLiteral("story_branch_rebase"), payload);
                if (!m_branchRebaseRequestId.isEmpty()) {
                    m_widget->setStatusMessage(tr("Rebasing alternate branch…"));
                }
            }
        } else if (decision.action == BranchReviewAction::Merge && !decision.overlayIds.isEmpty()) {
            m_pendingBranchMergeBranch = branchId;
            m_pendingBranchMergeOverlayIds = decision.overlayIds;
            QJsonObject payload{{QStringLiteral("project_root"), m_projectRoot},
                                {QStringLiteral("branch_id"), branchId},
                                {QStringLiteral("overlay_ids"), decision.overlayIds}};
            m_branchPrepareMergeRequestId = m_engine->send(QStringLiteral("story_branch_prepare_merge"), payload);
            if (m_branchPrepareMergeRequestId.isEmpty()) {
                m_pendingBranchMergeBranch.clear();
                m_pendingBranchMergeOverlayIds = {};
                m_widget->setStatusMessage(tr("Could not prepare the branch merge."));
            } else {
                m_widget->setStatusMessage(tr("Verifying selected branch changes…"));
            }
        }
        return;
    }
    if (requestId == m_branchRebaseRequestId) {
        m_branchRebaseRequestId.clear();
        if (!response.value(QStringLiteral("ok")).toBool()) {
            m_widget->setStatusMessage(tr("Branch rebase was not applied."));
            return;
        }
        m_widget->setStatusMessage(tr("Alternate branch rebased"));
        refreshBranches();
        return;
    }
    if (requestId == m_branchPrepareMergeRequestId) {
        m_branchPrepareMergeRequestId.clear();
        if (!response.value(QStringLiteral("ok")).toBool()) {
            const QString error = response.value(QStringLiteral("error")).toObject().value(QStringLiteral("message")).toString();
            m_pendingBranchMergeBranch.clear();
            m_pendingBranchMergeOverlayIds = {};
            m_widget->setStatusMessage(error.isEmpty() ? tr("Branch merge preparation failed.") : error);
            refreshBranches();
            return;
        }
        const QJsonObject prepared = response.value(QStringLiteral("result")).toObject();
        const QString expected = prepared.value(QStringLiteral("expected_parent_revision")).toString();
        const QJsonArray operations = prepared.value(QStringLiteral("operations")).toArray();
        if (m_pendingBranchMergeBranch.isEmpty() || m_pendingBranchMergeOverlayIds.isEmpty() || expected.isEmpty()
            || operations.size() != m_pendingBranchMergeOverlayIds.size()) {
            m_pendingBranchMergeBranch.clear();
            m_pendingBranchMergeOverlayIds = {};
            m_widget->setStatusMessage(tr("Branch merge preparation became stale; review it again."));
            return;
        }
        QStringList summary;
        for (const QJsonValue &value : operations) {
            const QJsonObject operation = value.toObject();
            summary << tr("• %1 %2 · %3")
                           .arg(operation.value(QStringLiteral("operation")).toString(),
                                operation.value(QStringLiteral("record_kind")).toString(),
                                operation.value(QStringLiteral("record_id")).toString());
            const QJsonObject impact = operation.value(QStringLiteral("retcon_impact")).toObject();
            const QJsonObject counts = impact.value(QStringLiteral("counts")).toObject();
            if (!counts.isEmpty()) {
                QStringList countLabels;
                for (auto iterator = counts.constBegin(); iterator != counts.constEnd(); ++iterator) {
                    countLabels << tr("%1 %2").arg(iterator.value().toInt()).arg(toolDisplayName(iterator.key()));
                }
                summary << tr("  Retcon impact: %1").arg(countLabels.join(QStringLiteral(", ")));
                int shown = 0;
                const QJsonArray affected = impact.value(QStringLiteral("affected")).toArray();
                for (const QJsonValue &affectedValue : affected) {
                    const QString label = affectedValue.toObject().value(QStringLiteral("label")).toString();
                    if (!label.isEmpty()) {
                        summary << tr("    → %1").arg(label);
                        if (++shown >= 3) {
                            break;
                        }
                    }
                }
                if (affected.size() > shown) {
                    summary << tr("    …and %1 more tracked dependent record(s)").arg(affected.size() - shown);
                }
            }
        }
        const QString prompt = tr("Promote these %1 reviewed branch change(s) into Mainline Story State?\n\n%2\n\n"
                                  "This changes structured canon/state only; it does not rewrite manuscript text. The operation is atomic.")
                                   .arg(operations.size())
                                   .arg(summary.join(QChar('\n')));
        if (QMessageBox::question(m_widget, tr("Merge selected branch changes?"), prompt, QMessageBox::Yes | QMessageBox::Cancel, QMessageBox::Cancel)
            != QMessageBox::Yes) {
            m_pendingBranchMergeBranch.clear();
            m_pendingBranchMergeOverlayIds = {};
            m_widget->setStatusMessage(tr("Branch merge canceled"));
            return;
        }
        QJsonObject payload{{QStringLiteral("project_root"), m_projectRoot},
                            {QStringLiteral("branch_id"), m_pendingBranchMergeBranch},
                            {QStringLiteral("overlay_ids"), m_pendingBranchMergeOverlayIds},
                            {QStringLiteral("expected_parent_revision"), expected},
                            {QStringLiteral("writer_confirmed"), true}};
        m_branchApplyMergeRequestId = m_engine->send(QStringLiteral("story_branch_apply_merge"), payload);
        if (m_branchApplyMergeRequestId.isEmpty()) {
            m_pendingBranchMergeBranch.clear();
            m_pendingBranchMergeOverlayIds = {};
            m_widget->setStatusMessage(tr("Could not start the branch merge."));
        } else {
            m_widget->setStatusMessage(tr("Applying selected branch changes atomically…"));
        }
        return;
    }
    if (requestId == m_branchApplyMergeRequestId) {
        m_branchApplyMergeRequestId.clear();
        const QString mergedBranch = m_pendingBranchMergeBranch;
        const int requestedCount = m_pendingBranchMergeOverlayIds.size();
        m_pendingBranchMergeBranch.clear();
        m_pendingBranchMergeOverlayIds = {};
        if (!response.value(QStringLiteral("ok")).toBool()) {
            const QString error = response.value(QStringLiteral("error")).toObject().value(QStringLiteral("message")).toString();
            m_widget->setStatusMessage(error.isEmpty() ? tr("Branch merge was not applied.") : error);
            refreshBranches();
            return;
        }
        const QJsonObject result = response.value(QStringLiteral("result")).toObject();
        const QString status = result.value(QStringLiteral("branch_status")).toString();
        m_widget->setStatusMessage(tr("Merged %1 branch change(s) into Mainline").arg(requestedCount));
        refreshBranches();
        if (status == QStringLiteral("MERGED") && m_activeBranch == mergedBranch) {
            switchStoryBranch(QStringLiteral("mainline"));
        }
        return;
    }
    if (requestId == m_manuscriptSourcesRequestId) {
        m_manuscriptSourcesRequestId.clear();
        if (!response.value(QStringLiteral("ok")).toBool()) {
            m_widget->setStatusMessage(tr("Manuscript sources could not be loaded."));
            return;
        }
        QJsonArray paths;
        if (!chooseManuscriptOrder(m_widget, response.value(QStringLiteral("result")).toObject(), paths)) {
            m_widget->setStatusMessage(tr("Manuscript order unchanged"));
            return;
        }
        QJsonObject payload;
        payload.insert(QStringLiteral("project_root"), m_projectRoot);
        payload.insert(QStringLiteral("paths"), paths);
        m_manuscriptOrderRequestId = m_engine->send(QStringLiteral("story_set_manuscript_order"), payload);
        if (!m_manuscriptOrderRequestId.isEmpty()) {
            m_widget->setStatusMessage(tr("Saving writer-owned manuscript order…"));
        }
        return;
    }
    if (requestId == m_manuscriptOrderRequestId) {
        m_manuscriptOrderRequestId.clear();
        if (!response.value(QStringLiteral("ok")).toBool()) {
            m_widget->setStatusMessage(tr("Manuscript order could not be saved."));
            return;
        }
        m_widget->setStatusMessage(tr("Manuscript order saved"));
        refreshProjectUnderstanding();
        return;
    }
    if (requestId == m_projectSourcesRequestId) {
        m_projectSourcesRequestId.clear();
        if (!response.value(QStringLiteral("ok")).toBool()) {
            m_widget->setStatusMessage(tr("Project sources could not be loaded."));
            return;
        }
        QString relativePath;
        QJsonArray roles;
        QString authority;
        QString pattern;
        if (!chooseProjectSourceOverride(m_widget, response.value(QStringLiteral("result")).toObject(), relativePath, roles, authority, pattern)) {
            m_widget->setStatusMessage(tr("Project understanding unchanged"));
            return;
        }
        QJsonObject payload;
        payload.insert(QStringLiteral("project_root"), m_projectRoot);
        payload.insert(QStringLiteral("path"), relativePath);
        if (!roles.isEmpty()) {
            payload.insert(QStringLiteral("roles"), roles);
        }
        if (!authority.isEmpty()) {
            payload.insert(QStringLiteral("authority"), authority);
        }
        if (!pattern.isEmpty()) {
            payload.insert(QStringLiteral("pattern"), pattern);
        }
        m_projectOverrideRequestId = m_engine->send(QStringLiteral("story_set_source_override"), payload);
        if (!m_projectOverrideRequestId.isEmpty()) {
            m_widget->setStatusMessage(tr("Applying writer-owned project correction…"));
        }
        return;
    }
    if (requestId == m_projectOverrideRequestId) {
        m_projectOverrideRequestId.clear();
        if (!response.value(QStringLiteral("ok")).toBool()) {
            m_widget->setStatusMessage(tr("Project correction could not be saved."));
            return;
        }
        m_widget->setStatusMessage(tr("Project correction saved"));
        refreshProjectUnderstanding();
        return;
    }
    if (requestId != m_chatRequestId) {
        return;
    }
    m_chatRequestId.clear();

    const QString error = storyResponseError(response);
    if (!error.isEmpty()) {
        failChatTurn(error);
        return;
    }

    const QJsonObject result = response.value(QStringLiteral("result")).toObject();
    const QJsonObject story = result.value(QStringLiteral("story_intelligence")).toObject();
    m_pendingChat.activeStoryUnit = result.value(QStringLiteral("story_context_inspector")).toObject().value(QStringLiteral("active_story_unit")).toString();
    if (!m_pendingChat.activeStoryUnit.isEmpty()) {
        m_lastActiveStoryUnit = m_pendingChat.activeStoryUnit;
    }
    const QJsonArray toolCalls = story.value(QStringLiteral("tool_calls")).toArray();
    const bool staleDocumentContext =
        !StoryToolHarness::modelDocumentContextCurrent(m_pendingChat.revision, m_revision, m_pendingChat.documentPath, currentDocumentPath());
    const bool staleStoryContext = m_pendingChat.storyContextHash != currentStoryContextHash();

    if (!toolCalls.isEmpty() && (staleDocumentContext || staleStoryContext)) {
        QJsonObject staleStory = story;
        staleStory.remove(QStringLiteral("tool_calls"));
        staleStory.remove(QStringLiteral("annotations"));
        staleStory.insert(QStringLiteral("message"),
                          tr("The manuscript or Story Intelligence context changed while AI was responding, so no requested ThothPad tools were run. Resend "
                             "the message if you still want those actions."));
        finishChatTurn(staleStory, result);
        return;
    }

    QJsonObject guardedStory = story;
    if (staleStoryContext) {
        guardedStory.remove(QStringLiteral("annotations"));
        guardedStory.remove(QStringLiteral("scene_context_proposal"));
        guardedStory.remove(QStringLiteral("character_proposals"));
        guardedStory.remove(QStringLiteral("memory_proposals"));
    }

    if (!toolCalls.isEmpty()) {
        if (m_pendingChat.toolRound >= MaximumToolRounds) {
            QJsonObject limitedStory = guardedStory;
            QString message = limitedStory.value(QStringLiteral("message")).toString().trimmed();
            if (message.isEmpty()) {
                message = tr("I reached ThothPad's tool-round safety limit before completing every requested operation.");
            }
            limitedStory.insert(QStringLiteral("message"), message);
            finishChatTurn(limitedStory, result);
            return;
        }

        executeToolCalls(toolCalls);
        ++m_pendingChat.toolRound;
        if (m_pendingChat.asyncTool.active || m_pendingChat.storyEngineTool.active) {
            // The same co-author turn resumes from pollPendingTool only after
            // native ThothPad reports real completion or a bounded failure.
            return;
        }
        m_widget->setStatusMessage(tr("Using ThothPad tools · round %1/%2").arg(m_pendingChat.toolRound).arg(MaximumToolRounds));
        dispatchPendingChat();
        return;
    }

    finishChatTurn(guardedStory, result);
}

void StoryIntelligenceController::finishChatTurn(const QJsonObject &story, const QJsonObject &result)
{
    m_widget->setContextInspector(result.value(QStringLiteral("story_context_inspector")).toObject());
    QString message = story.value(QStringLiteral("message")).toString().trimmed();
    if (message.isEmpty()) {
        message = result.value(QStringLiteral("output_text")).toString().trimmed();
    }
    if (message.isEmpty()) {
        failChatTurn(tr("The provider returned no text. Check the model and generation settings, then retry."));
        return;
    }

    const QString speaker = m_pendingChat.speaker;
    const auto references = m_pendingChat.revision == m_revision ? story.value("annotations").toArray() : QJsonArray();
    appendHistory(QStringLiteral("assistant"), message, speaker);
    m_widget->appendChatMessage(QStringLiteral("assistant"), message, speaker, references, m_history.last().toObject().value("id").toString());
    auto saved = m_history.last().toObject();
    saved.insert("references", references);
    QJsonArray proposals;
    auto offer = [&](const QString &kind, QJsonObject proposal) {
        if (proposal.isEmpty())
            return;
        proposal.insert("_scope_id", m_scopeId);
        proposal.insert("_agent_id", activeAgent().value("id"));
        proposal.insert("_proposal_id", StoryWorkspace::newId());
        proposals.append(QJsonObject{{"kind", kind}, {"record", proposal}});
        m_widget->appendProposal(kind, proposal);
    };
    if (m_pendingChat.revision == m_revision && m_pendingChat.storyContextHash == currentStoryContextHash()) {
        offer("scene", story.value("scene_context_proposal").toObject());
        for (const auto &v : story.value("character_proposals").toArray())
            offer("character", v.toObject());
        if (activeAgent().value("memory_policy").toString() != "off")
            for (const auto &v : story.value("memory_proposals").toArray())
                offer("memory", v.toObject());
    }
    saved.insert("proposals", proposals);
    m_history[m_history.size() - 1] = saved;
    storeSession();

    const QJsonArray annotations = story.value(QStringLiteral("annotations")).toArray();
    applyAnnotations(annotations, m_pendingChat.revision);
    if (m_pendingChat.revision == m_revision) {
        auto markers = m_workspace.data.value("markers").toArray();
        const QString digest = QString::fromLatin1(QCryptographicHash::hash(m_editor->toPlainText().toUtf8(), QCryptographicHash::Sha256).toHex());
        for (const auto &v : m_annotations) {
            auto mark = v.toObject();
            mark.insert("document_hash", digest);
            mark.insert("scope_id", m_scopeId);
            mark.insert("session_id", m_sessionId);
            mark.insert("message_id", saved.value("id"));
            bool exists = false;
            for (const auto &old : markers)
                if (old.toObject().value("id") == mark.value("id")) {
                    exists = true;
                    break;
                }
            if (!exists)
                markers.append(mark);
        }
        m_workspace.data.insert("markers", markers);
    }
    m_widget->setBusy(false);
    const bool savedWorkspace = saveWorkspace();
    restoreMarkers();
    if (savedWorkspace)
        m_widget->setStatusMessage(annotations.isEmpty() ? tr("Response complete") : tr("Response complete · %1 manuscript mark(s)").arg(annotations.size()));
    resetPendingChat();
    refreshWorkspace();
}

void StoryIntelligenceController::failChatTurn(const QString &message)
{
    m_widget->showChatError(message);
    resetPendingChat();
}

void StoryIntelligenceController::applyAnnotations(const QJsonArray &annotations, int responseRevision)
{
    if (responseRevision != m_revision) {
        if (!annotations.isEmpty()) {
            m_widget->setStatusMessage(tr("The document changed while AI was responding; stale manuscript marks were not applied."));
        }
        return;
    }

    QHash<int, QList<QTextLayout::FormatRange>> formatsByBlock;
    QJsonArray accepted;
    QTextDocument *document = m_editor->document();
    for (const QJsonValue value : annotations) {
        if (!value.isObject()) {
            continue;
        }
        const QJsonObject annotation = value.toObject();
        const int start = annotation.value(QStringLiteral("start_utf16")).toInt(-1);
        const int end = annotation.value(QStringLiteral("end_utf16")).toInt(-1);
        const QString quote = annotation.value(QStringLiteral("quote")).toString();
        if (start < 0 || end <= start || end > document->characterCount() - 1 || quote.size() != end - start
            || m_editor->toPlainText().mid(start, end - start) != quote) {
            continue;
        }

        QTextCharFormat format;
        format.setBackground(annotationColor(annotation.value(QStringLiteral("category")).toString()));
        format.setToolTip(annotationTooltip(annotation));
        TextFormatOverlayController::setPriority(format, 60);

        QTextBlock block = document->findBlock(start);
        while (block.isValid() && block.position() < end) {
            const int blockStart = block.position();
            const int blockTextEnd = blockStart + block.text().size();
            const int rangeStart = qMax(start, blockStart);
            const int rangeEnd = qMin(end, blockTextEnd);
            if (rangeEnd > rangeStart) {
                QTextLayout::FormatRange range;
                range.start = rangeStart - blockStart;
                range.length = rangeEnd - rangeStart;
                range.format = format;
                formatsByBlock[blockStart].append(range);
            }
            block = block.next();
        }
        accepted.append(annotation);
    }

    m_editor->textFormatOverlayController()->replaceChannelFormats(StoryOverlayChannel, formatsByBlock);
    m_annotations = accepted;
    m_widget->setAnnotations(m_annotations);
}

void StoryIntelligenceController::refreshAnnotationPresentation()
{
    if (m_annotations.isEmpty()) {
        m_editor->textFormatOverlayController()->clearChannel(StoryOverlayChannel);
        m_widget->setAnnotations({});
        return;
    }
    applyAnnotations(m_annotations, m_revision);
}

void StoryIntelligenceController::applySuggestion(const QString &suggestionId)
{
    if (!m_transactions) {
        m_widget->setStatusMessage(tr("Safe edit transactions are unavailable in this build."));
        return;
    }
    for (const QJsonValue &value : m_annotations) {
        const QJsonObject annotation = value.toObject();
        if (annotation.value(QStringLiteral("id")).toString() != suggestionId) {
            continue;
        }
        const QString replacement = annotation.value(QStringLiteral("replacement")).toString();
        if (replacement.isEmpty()) {
            return;
        }
        if (annotation.value(QStringLiteral("document_revision")).toInt(-1) != m_revision) {
            m_widget->setStatusMessage(tr("That suggestion is stale because the manuscript changed."));
            return;
        }
        if (annotation.value("stale").toBool())
            return;
        QDialog review(m_widget);
        review.setWindowTitle(tr("Review rewrite"));
        review.resize(720, 520);
        auto *layout = new QVBoxLayout(&review);
        layout->addWidget(new QLabel(tr("Original passage"), &review));
        auto *original = new QPlainTextEdit(annotation.value("quote").toString(), &review);
        original->setReadOnly(true);
        layout->addWidget(original);
        layout->addWidget(new QLabel(tr("Proposed replacement"), &review));
        auto *proposed = new QPlainTextEdit(replacement, &review);
        proposed->setReadOnly(true);
        layout->addWidget(proposed);
        auto *buttons = new QDialogButtonBox(QDialogButtonBox::Apply | QDialogButtonBox::Cancel, &review);
        layout->addWidget(buttons);
        connect(buttons->button(QDialogButtonBox::Apply), &QPushButton::clicked, &review, &QDialog::accept);
        connect(buttons, &QDialogButtonBox::rejected, &review, &QDialog::reject);
        if (review.exec() != QDialog::Accepted)
            return;
        if (annotation.value("document_revision").toInt(-1) != m_revision)
            return;
        QJsonObject edit;
        edit.insert(QStringLiteral("start_utf16"), annotation.value(QStringLiteral("start_utf16")));
        edit.insert(QStringLiteral("end_utf16"), annotation.value(QStringLiteral("end_utf16")));
        edit.insert(QStringLiteral("expected"), annotation.value(QStringLiteral("quote")));
        edit.insert(QStringLiteral("replacement"), replacement);
        QJsonArray edits;
        edits.append(edit);
        const QJsonObject result =
            m_transactions->applyVerifiedReplacements(edits, tr("Apply Story Intelligence rewrite"), QStringLiteral("apply_story_suggestion"));
        if (!result.value(QStringLiteral("ok")).toBool()) {
            m_widget->setStatusMessage(result.value(QStringLiteral("error")).toString(tr("Could not apply suggestion.")));
            return;
        }
        if (m_activity) {
            m_activity->noteSuggestionDecision(QStringLiteral("USER_ACCEPTED_SUGGESTION"),
                                               suggestionId,
                                               annotation.value(QStringLiteral("comment")).toString());
        }
        m_widget->appendChatMessage(QStringLiteral("assistant"),
                                    tr("✓ You applied a Story Intelligence suggestion. The edit is checkpointed and undoable."),
                                    QStringLiteral("ThothPad"));
        // Any edit can shift the remaining offsets. Drop all marks rather than
        // risk presenting stale locations; the next turn can regenerate them.
        clearAnnotations();
        return;
    }
}

void StoryIntelligenceController::dismissSuggestion(const QString &suggestionId)
{
    QJsonArray filtered;
    QJsonObject dismissed;
    for (const QJsonValue &value : m_annotations) {
        const QJsonObject annotation = value.toObject();
        if (annotation.value(QStringLiteral("id")).toString() == suggestionId) {
            dismissed = annotation;
            continue;
        }
        filtered.append(annotation);
    }
    if (dismissed.isEmpty()) {
        return;
    }
    if (m_activity) {
        m_activity->noteSuggestionDecision(QStringLiteral("USER_REJECTED_SUGGESTION"), suggestionId, dismissed.value(QStringLiteral("comment")).toString());
    }
    m_annotations = filtered;
    auto markers = m_workspace.data.value("markers").toArray();
    for (int i = markers.size() - 1; i >= 0; --i)
        if (markers[i].toObject().value("id").toString() == suggestionId)
            markers.removeAt(i);
    m_workspace.data.insert("markers", markers);
    saveWorkspace();
    refreshAnnotationPresentation();
    m_widget->appendChatMessage(QStringLiteral("assistant"),
                                tr("You dismissed that suggestion. I'll treat that as preference evidence, not a permanent rule."),
                                QStringLiteral("ThothPad"));
}

void StoryIntelligenceController::clearAnnotations()
{
    m_editor->textFormatOverlayController()->clearChannel(StoryOverlayChannel);
    m_annotations = {};
    m_workspace.data.insert("markers", QJsonArray());
    if (m_started && !m_loadingWorkspace)
        saveWorkspace();
    m_widget->setAnnotations({});
    m_widget->setStatusMessage(tr("AI manuscript marks cleared"));
}

void StoryIntelligenceController::resetPendingChat()
{
    m_toolWaitTimer.stop();
    if (!m_storyRouteRequestId.isEmpty()) {
        m_engine->cancel(m_storyRouteRequestId);
        m_storyRouteRequestId.clear();
    }
    if (!m_pendingChat.apiKey.isEmpty()) {
        m_pendingChat.apiKey.fill(QChar('\0'));
    }
    m_pendingChat = {};
}
}
