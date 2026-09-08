/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "storylabdialog.h"

#include "../prose/writerengineclient.h"

#include <QAbstractItemView>
#include <QComboBox>
#include <QDialogButtonBox>
#include <QFile>
#include <QFileDialog>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QInputDialog>
#include <QJsonDocument>
#include <QJsonValue>
#include <QKeySequence>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QSaveFile>
#include <QSet>
#include <QTabWidget>
#include <QTableWidget>
#include <QTableWidgetItem>
#include <QVBoxLayout>

namespace ghostwriter
{
namespace
{
QPlainTextEdit *resultBox(QWidget *parent)
{
    auto *box = new QPlainTextEdit(parent);
    box->setReadOnly(true);
    box->setLineWrapMode(QPlainTextEdit::WidgetWidth);
    box->setPlaceholderText(QObject::tr("Run an analysis to see grounded Story Engine evidence here."));
    return box;
}

QString displayKey(QString value)
{
    value.replace(QChar('_'), QChar(' '));
    if (!value.isEmpty()) {
        value[0] = value.at(0).toUpper();
    }
    return value;
}

QString claimLabel(const QJsonObject &claim)
{
    QString value;
    const QJsonValue literal = claim.value(QStringLiteral("literal_value"));
    if (literal.isString()) {
        value = literal.toString();
    } else if (!literal.isUndefined() && !literal.isNull()) {
        value = QString::fromUtf8(QJsonDocument(QJsonArray{literal}).toJson(QJsonDocument::Compact));
        if (value.startsWith(QChar('[')) && value.endsWith(QChar(']'))) {
            value = value.mid(1, value.size() - 2);
        }
    }
    const QString predicate = displayKey(claim.value(QStringLiteral("predicate")).toString());
    return value.isEmpty() ? predicate : QStringLiteral("%1: %2").arg(predicate, value);
}
}

StoryLabDialog::StoryLabDialog(WriterEngineClient *engine, QWidget *parent)
    : QDialog(parent)
    , m_engine(engine)
    , m_positionLabel(new QLabel(this))
    , m_statusLabel(new QLabel(this))
    , m_readerAction(new QComboBox(this))
    , m_readerCharacter(new QLineEdit(this))
    , m_readerRun(new QPushButton(tr("Run"), this))
    , m_readerOutput(resultBox(this))
    , m_entityTable(new QTableWidget(this))
    , m_entityRefresh(new QPushButton(tr("Refresh entities"), this))
    , m_entityAlias(new QPushButton(tr("Add alias…"), this))
    , m_groundingEntity(new QLineEdit(this))
    , m_groundingClaims(new QPushButton(tr("Show grounded claims"), this))
    , m_groundingConflicts(new QPushButton(tr("Show conflicts"), this))
    , m_groundingOutput(resultBox(this))
    , m_councilRun(new QPushButton(tr("Run Editorial Council"), this))
    , m_councilOutput(resultBox(this))
    , m_lensCombo(new QComboBox(this))
    , m_lensNew(new QPushButton(tr("New Lens…"), this))
    , m_lensRun(new QPushButton(tr("Run Lens"), this))
    , m_lensRefresh(new QPushButton(tr("Refresh"), this))
    , m_lensOutput(resultBox(this))
    , m_experienceCurrent(new QPushButton(tr("Analyze current story unit"), this))
    , m_experienceTimeline(new QPushButton(tr("Build manuscript timeline"), this))
    , m_experienceOutput(resultBox(this))
    , m_advancedAction(new QComboBox(this))
    , m_advancedQuery(new QLineEdit(this))
    , m_advancedCharacter(new QLineEdit(this))
    , m_advancedOtherEntity(new QLineEdit(this))
    , m_advancedRun(new QPushButton(tr("Run analysis"), this))
    , m_indexRebuild(new QPushButton(tr("Rebuild index…"), this))
    , m_indexContinue(new QPushButton(tr("Continue indexing"), this))
    , m_legacyBind(new QPushButton(tr("Bind legacy workspace…"), this))
    , m_stateBackup(new QPushButton(tr("Backup Story State"), this))
    , m_stateRecover(new QPushButton(tr("Recover pending operation…"), this))
    , m_stateRestore(new QPushButton(tr("Restore Story State…"), this))
    , m_projectExport(new QPushButton(tr("Export Story metadata…"), this))
    , m_projectImport(new QPushButton(tr("Import Story metadata…"), this))
    , m_advancedOutput(resultBox(this))
    , m_proposalTable(new QTableWidget(this))
    , m_proposalRefresh(new QPushButton(tr("Refresh"), this))
    , m_proposalAccept(new QPushButton(tr("Accept selected"), this))
    , m_proposalReject(new QPushButton(tr("Reject selected"), this))
    , m_proposalOutput(resultBox(this))
    , m_writerTable(new QTableWidget(this))
    , m_writerRefresh(new QPushButton(tr("Refresh"), this))
    , m_writerConfirm(new QPushButton(tr("Confirm selected"), this))
    , m_writerIgnore(new QPushButton(tr("Ignore selected"), this))
    , m_writerOutput(resultBox(this))
{
    Q_ASSERT(m_engine);
    setWindowTitle(tr("Story Lab"));
    resize(980, 720);
    setMinimumSize(760, 560);

    auto *root = new QVBoxLayout(this);
    auto *intro = new QLabel(tr("Deep story diagnostics over ThothPad's governed Story Model. Results are observations and evidence, "
                                "not automatic canon or manuscript edits."),
                             this);
    intro->setWordWrap(true);
    root->addWidget(intro);

    m_positionLabel->setWordWrap(true);
    m_positionLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    root->addWidget(m_positionLabel);
    m_statusLabel->setWordWrap(true);
    root->addWidget(m_statusLabel);

    auto *tabs = new QTabWidget(this);
    tabs->setObjectName(QStringLiteral("storyLabTabs"));
    tabs->setAccessibleName(tr("Story Lab workspaces"));
    root->addWidget(tabs, 1);

    auto *readerPage = new QWidget(tabs);
    auto *readerLayout = new QVBoxLayout(readerPage);
    auto *readerForm = new QFormLayout;
    m_readerAction->addItem(tr("Cold Reader at this point"), QStringLiteral("cold_reader_at"));
    m_readerAction->addItem(tr("Reveal fairness"), QStringLiteral("audit_reveal_fairness"));
    m_readerAction->addItem(tr("Reader questions & expectations"), QStringLiteral("get_reader_expectations"));
    m_readerAction->addItem(tr("Dramatic irony"), QStringLiteral("get_dramatic_irony"));
    m_readerCharacter->setPlaceholderText(tr("Character name for dramatic-irony comparison"));
    readerForm->addRow(tr("Reader lens"), m_readerAction);
    readerForm->addRow(tr("Character"), m_readerCharacter);
    readerLayout->addLayout(readerForm);
    readerLayout->addWidget(m_readerRun, 0, Qt::AlignLeft);
    readerLayout->addWidget(m_readerOutput, 1);
    tabs->addTab(readerPage, tr("Reader"));

    auto *entitiesPage = new QWidget(tabs);
    auto *entitiesLayout = new QVBoxLayout(entitiesPage);
    auto *entitiesHint = new QLabel(tr("Entity identity stays independent of folder layout. Review discovered entities and add writer-confirmed aliases when "
                                       "two names refer to the same story identity."),
                                    entitiesPage);
    entitiesHint->setWordWrap(true);
    entitiesLayout->addWidget(entitiesHint);
    m_entityTable->setColumnCount(4);
    m_entityTable->setHorizontalHeaderLabels({tr("Entity"), tr("Type"), tr("Status"), tr("Aliases")});
    m_entityTable->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_entityTable->setSelectionMode(QAbstractItemView::SingleSelection);
    m_entityTable->setEditTriggers(QAbstractItemView::NoEditTriggers);
    m_entityTable->verticalHeader()->setVisible(false);
    m_entityTable->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Stretch);
    m_entityTable->horizontalHeader()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    m_entityTable->horizontalHeader()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    m_entityTable->horizontalHeader()->setSectionResizeMode(3, QHeaderView::Stretch);
    entitiesLayout->addWidget(m_entityTable, 1);
    auto *entityActions = new QHBoxLayout;
    entityActions->addWidget(m_entityRefresh);
    entityActions->addWidget(m_entityAlias);
    entityActions->addStretch(1);
    entitiesLayout->addLayout(entityActions);
    tabs->addTab(entitiesPage, tr("Entities"));

    auto *groundingPage = new QWidget(tabs);
    auto *groundingLayout = new QVBoxLayout(groundingPage);
    auto *groundingHint = new QLabel(
        tr("Inspect why ThothPad believes a fact. Claims retain authority and exact provenance; conflicts are explicit rather than silently resolved."),
        groundingPage);
    groundingHint->setWordWrap(true);
    groundingLayout->addWidget(groundingHint);
    auto *groundingRow = new QHBoxLayout;
    m_groundingEntity->setPlaceholderText(tr("Optional entity name, e.g. Mara"));
    groundingRow->addWidget(m_groundingEntity, 1);
    groundingRow->addWidget(m_groundingClaims);
    groundingRow->addWidget(m_groundingConflicts);
    groundingLayout->addLayout(groundingRow);
    groundingLayout->addWidget(m_groundingOutput, 1);
    tabs->addTab(groundingPage, tr("Grounding"));

    auto *councilPage = new QWidget(tabs);
    auto *councilLayout = new QVBoxLayout(councilPage);
    auto *councilHint = new QLabel(tr("Seven independent read-only reviewers inspect one immutable Story State snapshot. "
                                      "Their conclusions are synthesized only after every reviewer finishes."),
                                   councilPage);
    councilHint->setWordWrap(true);
    councilLayout->addWidget(councilHint);
    councilLayout->addWidget(m_councilRun, 0, Qt::AlignLeft);
    councilLayout->addWidget(m_councilOutput, 1);
    tabs->addTab(councilPage, tr("Council"));

    auto *lensPage = new QWidget(tabs);
    auto *lensLayout = new QVBoxLayout(lensPage);
    auto *lensHint = new QLabel(tr("Story Lenses are writer-defined reusable questions. Deterministic retrieval returns exact evidence "
                                   "candidates; retrieval itself never becomes a semantic conclusion."),
                                lensPage);
    lensHint->setWordWrap(true);
    lensLayout->addWidget(lensHint);
    auto *lensRow = new QHBoxLayout;
    lensRow->addWidget(m_lensCombo, 1);
    lensRow->addWidget(m_lensRefresh);
    lensRow->addWidget(m_lensNew);
    lensRow->addWidget(m_lensRun);
    lensLayout->addLayout(lensRow);
    lensLayout->addWidget(m_lensOutput, 1);
    tabs->addTab(lensPage, tr("Story Lenses"));

    auto *experiencePage = new QWidget(tabs);
    auto *experienceLayout = new QVBoxLayout(experiencePage);
    auto *experienceHint =
        new QLabel(tr("Qualitative reader-experience cues only—no fake percentages or universal claims about how readers feel."), experiencePage);
    experienceHint->setWordWrap(true);
    experienceLayout->addWidget(experienceHint);
    auto *experienceRow = new QHBoxLayout;
    experienceRow->addWidget(m_experienceCurrent);
    experienceRow->addWidget(m_experienceTimeline);
    experienceRow->addStretch(1);
    experienceLayout->addLayout(experienceRow);
    experienceLayout->addWidget(m_experienceOutput, 1);
    tabs->addTab(experiencePage, tr("Reader Experience"));

    auto *advancedPage = new QWidget(tabs);
    auto *advancedLayout = new QVBoxLayout(advancedPage);
    auto *advancedHint = new QLabel(tr("Explore normalized story state, run continuity/arc/ending audits, inspect project health, and manage the "
                                       "disposable Story Engine index. Audits are evidence-backed diagnostics, not automatic canon."),
                                    advancedPage);
    advancedHint->setWordWrap(true);
    advancedLayout->addWidget(advancedHint);
    auto *advancedForm = new QFormLayout;
    m_advancedAction->addItem(tr("Story Explorer"), QStringLiteral("explore_story"));
    m_advancedAction->addItem(tr("Scene semantics"), QStringLiteral("get_scene_semantics"));
    m_advancedAction->addItem(tr("Continuity / knowledge-access audit"), QStringLiteral("audit_continuity"));
    m_advancedAction->addItem(tr("Character arc"), QStringLiteral("get_character_arc"));
    m_advancedAction->addItem(tr("Relationship arc"), QStringLiteral("get_relationship_arc"));
    m_advancedAction->addItem(tr("Ending integrity / backpropagation"), QStringLiteral("audit_ending_integrity"));
    m_advancedAction->addItem(tr("Project health"), QStringLiteral("get_project_health"));
    m_advancedAction->addItem(tr("Index status"), QStringLiteral("get_index_status"));
    m_advancedAction->addItem(tr("10-step Wow acceptance"), QStringLiteral("run_wow_acceptance"));
    m_advancedAction->insertSeparator(m_advancedAction->count());
    m_advancedAction->addItem(tr("Migration / legacy-binding status"), QStringLiteral("get_migration_status"));
    m_advancedAction->addItem(tr("Background-indexing status"), QStringLiteral("get_indexing_status"));
    m_advancedAction->addItem(tr("Performance / query-plan report"), QStringLiteral("get_performance_report"));
    m_advancedAction->addItem(tr("Filesystem / adapter security audit"), QStringLiteral("get_security_audit"));
    m_advancedAction->addItem(tr("Remote egress preview"), QStringLiteral("get_egress_preview"));
    m_advancedAction->addItem(tr("Project-agnostic model fingerprint"), QStringLiteral("get_model_fingerprint"));
    m_advancedAction->addItem(tr("Engineering acceptance metrics"), QStringLiteral("get_acceptance_metrics"));
    m_advancedAction->addItem(tr("Retrieval capabilities"), QStringLiteral("get_retrieval_capabilities"));
    m_advancedAction->addItem(tr("Ask ThothPad Why"), QStringLiteral("explain_story_record"));
    m_advancedAction->addItem(tr("Phases 26–35 operational acceptance"), QStringLiteral("run_operational_acceptance"));
    m_advancedAction->insertSeparator(m_advancedAction->count());
    m_advancedAction->addItem(tr("Release-project validation"), QStringLiteral("get_release_validation"));
    m_advancedAction->addItem(tr("Crash / recovery status"), QStringLiteral("get_recovery_status"));
    m_advancedAction->addItem(tr("Privacy-safe local observability"), QStringLiteral("get_observability_report"));
    m_advancedAction->addItem(tr("Cancellation / resource policy"), QStringLiteral("get_resource_policy"));
    m_advancedAction->addItem(tr("Unicode / path resilience"), QStringLiteral("get_path_resilience"));
    m_advancedAction->addItem(tr("Offline / egress readiness"), QStringLiteral("get_offline_readiness"));
    m_advancedAction->addItem(tr("Upgrade / rollback compatibility"), QStringLiteral("get_compatibility_status"));
    m_advancedAction->addItem(tr("Phases 36–45 release-candidate acceptance"), QStringLiteral("run_release_candidate_acceptance"));
    m_advancedAction->insertSeparator(m_advancedAction->count());
    m_advancedAction->addItem(tr("Deterministic soak replay"), QStringLiteral("run_soak_replay"));
    m_advancedAction->addItem(tr("Privacy-safe support bundle"), QStringLiteral("get_support_bundle"));
    m_advancedAction->addItem(tr("Wire / Story API fingerprint"), QStringLiteral("get_interface_fingerprint"));
    m_advancedAction->addItem(tr("Performance budget regression"), QStringLiteral("get_performance_budget"));
    m_advancedAction->addItem(tr("Backup / relocation readiness"), QStringLiteral("get_relocation_readiness"));
    m_advancedAction->addItem(tr("Phases 46–55 release readiness"), QStringLiteral("get_release_readiness"));
    m_advancedQuery->setPlaceholderText(tr("Explorer/egress prompt, or record kind for Why"));
    m_advancedCharacter->setPlaceholderText(tr("Character/entity A, or record ID for Why"));
    m_advancedOtherEntity->setPlaceholderText(tr("Entity B for relationship arc"));
    advancedForm->addRow(tr("Analysis"), m_advancedAction);
    advancedForm->addRow(tr("Query"), m_advancedQuery);
    advancedForm->addRow(tr("Character / A"), m_advancedCharacter);
    advancedForm->addRow(tr("Entity B"), m_advancedOtherEntity);
    advancedLayout->addLayout(advancedForm);
    advancedLayout->addWidget(m_advancedRun, 0, Qt::AlignLeft);
    auto *maintenanceRow = new QHBoxLayout;
    maintenanceRow->addWidget(m_indexContinue);
    maintenanceRow->addWidget(m_indexRebuild);
    maintenanceRow->addWidget(m_legacyBind);
    maintenanceRow->addWidget(m_stateBackup);
    maintenanceRow->addWidget(m_stateRecover);
    maintenanceRow->addWidget(m_stateRestore);
    maintenanceRow->addWidget(m_projectExport);
    maintenanceRow->addWidget(m_projectImport);
    maintenanceRow->addStretch(1);
    advancedLayout->addLayout(maintenanceRow);
    advancedLayout->addWidget(m_advancedOutput, 1);
    tabs->addTab(advancedPage, tr("Advanced"));

    auto *proposalPage = new QWidget(tabs);
    auto *proposalLayout = new QVBoxLayout(proposalPage);
    auto *proposalHint = new QLabel(tr("Story proposals are durable but non-canon. Accepting one routes it through the normal writer-owned mutation boundary; "
                                       "rejecting one changes no Story State."),
                                    proposalPage);
    proposalHint->setWordWrap(true);
    proposalLayout->addWidget(proposalHint);
    m_proposalTable->setColumnCount(5);
    m_proposalTable->setHorizontalHeaderLabels({tr("Status"), tr("Kind"), tr("Target"), tr("Branch"), tr("Proposal")});
    m_proposalTable->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_proposalTable->setSelectionMode(QAbstractItemView::SingleSelection);
    m_proposalTable->setEditTriggers(QAbstractItemView::NoEditTriggers);
    m_proposalTable->verticalHeader()->setVisible(false);
    m_proposalTable->horizontalHeader()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    m_proposalTable->horizontalHeader()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    m_proposalTable->horizontalHeader()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    m_proposalTable->horizontalHeader()->setSectionResizeMode(3, QHeaderView::ResizeToContents);
    m_proposalTable->horizontalHeader()->setSectionResizeMode(4, QHeaderView::Stretch);
    proposalLayout->addWidget(m_proposalTable, 1);
    auto *proposalActions = new QHBoxLayout;
    proposalActions->addWidget(m_proposalRefresh);
    proposalActions->addWidget(m_proposalAccept);
    proposalActions->addWidget(m_proposalReject);
    proposalActions->addStretch(1);
    proposalLayout->addLayout(proposalActions);
    m_proposalOutput->setMaximumHeight(120);
    proposalLayout->addWidget(m_proposalOutput);
    tabs->addTab(proposalPage, tr("Proposals"));

    auto *writerPage = new QWidget(tabs);
    auto *writerLayout = new QVBoxLayout(writerPage);
    auto *writerHint =
        new QLabel(tr("Behavioral patterns begin as provisional hypotheses. Only you can confirm, ignore, edit, or supersede them."), writerPage);
    writerHint->setWordWrap(true);
    writerLayout->addWidget(writerHint);
    m_writerTable->setColumnCount(4);
    m_writerTable->setHorizontalHeaderLabels({tr("Status"), tr("Scope"), tr("Preference"), tr("Evidence")});
    m_writerTable->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_writerTable->setSelectionMode(QAbstractItemView::SingleSelection);
    m_writerTable->setEditTriggers(QAbstractItemView::NoEditTriggers);
    m_writerTable->verticalHeader()->setVisible(false);
    m_writerTable->horizontalHeader()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    m_writerTable->horizontalHeader()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    m_writerTable->horizontalHeader()->setSectionResizeMode(2, QHeaderView::Stretch);
    m_writerTable->horizontalHeader()->setSectionResizeMode(3, QHeaderView::ResizeToContents);
    writerLayout->addWidget(m_writerTable, 1);
    auto *writerActions = new QHBoxLayout;
    writerActions->addWidget(m_writerRefresh);
    writerActions->addWidget(m_writerConfirm);
    writerActions->addWidget(m_writerIgnore);
    writerActions->addStretch(1);
    writerLayout->addLayout(writerActions);
    m_writerOutput->setMaximumHeight(115);
    writerLayout->addWidget(m_writerOutput);
    tabs->addTab(writerPage, tr("Writer Model"));

    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Close, this);
    root->addWidget(buttons);
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);

    connect(m_readerRun, &QPushButton::clicked, this, [this]() {
        runReaderTool();
    });
    connect(m_entityRefresh, &QPushButton::clicked, this, [this]() {
        refreshEntities();
    });
    connect(m_entityAlias, &QPushButton::clicked, this, [this]() {
        addEntityAlias();
    });
    connect(m_groundingClaims, &QPushButton::clicked, this, [this]() {
        runGrounding(false);
    });
    connect(m_groundingConflicts, &QPushButton::clicked, this, [this]() {
        runGrounding(true);
    });
    connect(m_councilRun, &QPushButton::clicked, this, [this]() {
        runCouncil();
    });
    connect(m_lensRefresh, &QPushButton::clicked, this, [this]() {
        refreshLenses();
    });
    connect(m_lensNew, &QPushButton::clicked, this, [this]() {
        createLens();
    });
    connect(m_lensRun, &QPushButton::clicked, this, [this]() {
        runLens();
    });
    connect(m_experienceCurrent, &QPushButton::clicked, this, [this]() {
        runExperience(false);
    });
    connect(m_experienceTimeline, &QPushButton::clicked, this, [this]() {
        runExperience(true);
    });
    connect(m_advancedRun, &QPushButton::clicked, this, [this]() {
        runAdvancedTool();
    });
    connect(m_indexRebuild, &QPushButton::clicked, this, [this]() {
        rebuildIndex();
    });
    connect(m_indexContinue, &QPushButton::clicked, this, [this]() {
        continueIndexing();
    });
    connect(m_legacyBind, &QPushButton::clicked, this, [this]() {
        bindLegacyWorkspace();
    });
    connect(m_stateBackup, &QPushButton::clicked, this, [this]() {
        backupStoryState();
    });
    connect(m_stateRecover, &QPushButton::clicked, this, [this]() {
        recoverStoryState();
    });
    connect(m_stateRestore, &QPushButton::clicked, this, [this]() {
        restoreStoryState();
    });
    connect(m_projectExport, &QPushButton::clicked, this, [this]() {
        exportProjectMetadata();
    });
    connect(m_projectImport, &QPushButton::clicked, this, [this]() {
        importProjectMetadata();
    });
    connect(m_proposalRefresh, &QPushButton::clicked, this, [this]() {
        refreshProposals();
    });
    connect(m_proposalAccept, &QPushButton::clicked, this, [this]() {
        reviewSelectedProposal(QStringLiteral("ACCEPTED"));
    });
    connect(m_proposalReject, &QPushButton::clicked, this, [this]() {
        reviewSelectedProposal(QStringLiteral("REJECTED"));
    });
    connect(m_proposalTable, &QTableWidget::itemSelectionChanged, this, [this]() {
        const int row = m_proposalTable->currentRow();
        const bool reviewable =
            row >= 0 && m_proposalTable->item(row, 4) && m_proposalTable->item(row, 4)->data(Qt::UserRole + 1).toString() == QStringLiteral("PROPOSED");
        m_proposalAccept->setEnabled(reviewable);
        m_proposalReject->setEnabled(reviewable);
    });
    connect(m_writerRefresh, &QPushButton::clicked, this, [this]() {
        refreshWriterModel();
    });
    connect(m_writerConfirm, &QPushButton::clicked, this, [this]() {
        reviewSelectedPreference(QStringLiteral("CONFIRMED"));
    });
    connect(m_writerIgnore, &QPushButton::clicked, this, [this]() {
        reviewSelectedPreference(QStringLiteral("IGNORED"));
    });
    connect(m_writerTable, &QTableWidget::itemSelectionChanged, this, [this]() {
        const bool selected = m_writerTable->currentRow() >= 0;
        m_writerConfirm->setEnabled(selected);
        m_writerIgnore->setEnabled(selected);
    });
    connect(m_engine, &WriterEngineClient::responseReceived, this, [this](const QString &requestId, const QJsonObject &response) {
        handleResponse(requestId, response);
    });

    m_writerConfirm->setEnabled(false);
    m_writerIgnore->setEnabled(false);
    m_proposalAccept->setEnabled(false);
    m_proposalReject->setEnabled(false);
    m_entityAlias->setEnabled(false);
    connect(m_entityTable, &QTableWidget::itemSelectionChanged, this, [this]() {
        m_entityAlias->setEnabled(m_entityTable->currentRow() >= 0);
    });

    m_readerAction->setObjectName(QStringLiteral("storyLabReaderAction"));
    m_readerAction->setAccessibleName(tr("Reader analysis"));
    m_readerRun->setObjectName(QStringLiteral("storyLabReaderRun"));
    m_readerRun->setAccessibleName(tr("Run reader analysis"));
    m_readerRun->setShortcut(QKeySequence(QStringLiteral("Alt+R")));
    m_entityTable->setObjectName(QStringLiteral("storyLabEntityTable"));
    m_entityTable->setAccessibleName(tr("Story entities"));
    m_advancedAction->setObjectName(QStringLiteral("storyLabAdvancedAction"));
    m_advancedAction->setAccessibleName(tr("Advanced Story Engine analysis"));
    m_advancedRun->setObjectName(QStringLiteral("storyLabAdvancedRun"));
    m_advancedRun->setAccessibleName(tr("Run advanced Story Engine analysis"));
    m_advancedRun->setShortcut(QKeySequence(QStringLiteral("Alt+A")));
    m_advancedOutput->setObjectName(QStringLiteral("storyLabAdvancedOutput"));
    m_advancedOutput->setAccessibleName(tr("Advanced Story Engine results"));
    m_proposalTable->setObjectName(QStringLiteral("storyLabProposalTable"));
    m_proposalTable->setAccessibleName(tr("Story proposal review queue"));
    m_proposalAccept->setObjectName(QStringLiteral("storyLabProposalAccept"));
    m_proposalAccept->setAccessibleName(tr("Accept selected Story proposal"));
    m_proposalReject->setObjectName(QStringLiteral("storyLabProposalReject"));
    m_proposalReject->setAccessibleName(tr("Reject selected Story proposal"));
    m_writerTable->setObjectName(QStringLiteral("storyLabWriterModelTable"));
    m_writerTable->setAccessibleName(tr("Writer Model preferences"));
    m_stateBackup->setObjectName(QStringLiteral("storyLabStateBackup"));
    m_stateBackup->setAccessibleName(tr("Create Story State backup"));
    m_stateRecover->setObjectName(QStringLiteral("storyLabStateRecover"));
    m_stateRecover->setAccessibleName(tr("Recover interrupted Story Engine operation"));
    m_stateRestore->setObjectName(QStringLiteral("storyLabStateRestore"));
    m_stateRestore->setAccessibleName(tr("Restore a previous Story State backup"));
    m_statusLabel->setAccessibleName(tr("Story Lab status"));
    m_positionLabel->setAccessibleName(tr("Current Story position"));
    QWidget::setTabOrder(m_readerAction, m_readerCharacter);
    QWidget::setTabOrder(m_readerCharacter, m_readerRun);
    QWidget::setTabOrder(m_advancedAction, m_advancedQuery);
    QWidget::setTabOrder(m_advancedQuery, m_advancedCharacter);
    QWidget::setTabOrder(m_advancedCharacter, m_advancedOtherEntity);
    QWidget::setTabOrder(m_advancedOtherEntity, m_advancedRun);
    setPositionSensitiveEnabled(false);
    setReady();
}

StoryLabDialog::~StoryLabDialog()
{
    if (!m_requestId.isEmpty()) {
        m_engine->cancel(m_requestId);
    }
}

void StoryLabDialog::setStoryContext(const QString &projectRoot,
                                     const QString &activeStoryUnit,
                                     const QString &activeBranch,
                                     const QString &sourcePath,
                                     const QString &scopeTitle,
                                     const QString &activeCharacter)
{
    m_projectRoot = projectRoot;
    m_activeStoryUnit = activeStoryUnit;
    m_activeBranch = activeBranch.isEmpty() ? QStringLiteral("mainline") : activeBranch;
    m_sourcePath = sourcePath;
    m_scopeTitle = scopeTitle;
    m_activeCharacter = activeCharacter;
    m_readerCharacter->setText(activeCharacter);

    if (!m_activeStoryUnit.isEmpty()) {
        m_positionLabel->setText(tr("Story position: %1 · branch %2").arg(m_scopeTitle, m_activeBranch));
        setPositionSensitiveEnabled(true);
    } else {
        m_positionLabel->setText(tr("Resolving the current editor scope to a stable Story Unit…"));
        setPositionSensitiveEnabled(false);
        resolveStoryPosition();
    }
    refreshLenses();
}

void StoryLabDialog::resolveStoryPosition()
{
    if (m_projectRoot.isEmpty() || m_sourcePath.isEmpty() || !m_engine->isReady()) {
        m_positionLabel->setText(tr("Current story position is unavailable. Open an indexed manuscript and select a chapter scope."));
        return;
    }
    QJsonObject arguments{{QStringLiteral("source_path"), m_sourcePath},
                          {QStringLiteral("title"), m_scopeTitle},
                          {QStringLiteral("branch_id"), m_activeBranch}};
    QJsonObject payload{{QStringLiteral("project_root"), m_projectRoot},
                        {QStringLiteral("tool_id"), QStringLiteral("resolve_story_unit")},
                        {QStringLiteral("arguments"), arguments}};
    m_requestKind = QStringLiteral("__resolve_story_unit");
    m_requestId = m_engine->send(QStringLiteral("story_tool"), payload);
    if (m_requestId.isEmpty()) {
        m_positionLabel->setText(tr("Could not resolve the current Story Unit."));
    } else {
        setBusy(tr("Resolving story position…"));
    }
}

void StoryLabDialog::setPositionSensitiveEnabled(bool enabled)
{
    m_readerRun->setEnabled(enabled);
    m_councilRun->setEnabled(enabled);
    m_experienceCurrent->setEnabled(enabled);
    m_advancedRun->setEnabled(true);
}

void StoryLabDialog::requestTool(const QString &toolId, QJsonObject arguments)
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady()) {
        setReady(tr("Story Engine is not ready."));
        return;
    }
    if (!m_requestId.isEmpty()) {
        m_engine->cancel(m_requestId);
    }
    arguments.insert(QStringLiteral("branch_id"), m_activeBranch);
    const QSet<QString> positionTools = {
        QStringLiteral("cold_reader_at"),
        QStringLiteral("audit_reveal_fairness"),
        QStringLiteral("get_reader_expectations"),
        QStringLiteral("get_dramatic_irony"),
        QStringLiteral("run_editorial_council"),
        QStringLiteral("get_reader_experience"),
        QStringLiteral("get_scene_semantics"),
        QStringLiteral("audit_continuity"),
        QStringLiteral("audit_ending_integrity"),
        QStringLiteral("run_wow_acceptance"),
    };
    if (positionTools.contains(toolId)) {
        if (m_activeStoryUnit.isEmpty()) {
            setReady(tr("This analysis needs an unambiguous current Story Unit."));
            return;
        }
        arguments.insert(QStringLiteral("story_unit_id"), m_activeStoryUnit);
    }
    QJsonObject payload{{QStringLiteral("project_root"), m_projectRoot}, {QStringLiteral("tool_id"), toolId}, {QStringLiteral("arguments"), arguments}};
    m_requestKind = toolId;
    m_pendingMutation.clear();
    m_requestId = m_engine->send(QStringLiteral("story_tool"), payload);
    if (m_requestId.isEmpty()) {
        setReady(tr("Could not start Story Engine analysis."));
    } else {
        setBusy(tr("Story Engine is analyzing…"));
    }
}

void StoryLabDialog::requestWriterMutation(const QString &mutation, const QJsonObject &payload)
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady()) {
        setReady(tr("Story Engine is not ready."));
        return;
    }
    if (!m_requestId.isEmpty()) {
        m_engine->cancel(m_requestId);
    }
    QJsonObject request{{QStringLiteral("project_root"), m_projectRoot},
                        {QStringLiteral("mutation"), mutation},
                        {QStringLiteral("payload"), payload},
                        {QStringLiteral("writer_confirmed"), true}};
    m_requestKind = QStringLiteral("__writer_mutation");
    m_pendingMutation = mutation;
    m_requestId = m_engine->send(QStringLiteral("story_writer_mutation"), request);
    if (m_requestId.isEmpty()) {
        setReady(tr("Could not save writer-owned Story State."));
    } else {
        setBusy(tr("Saving writer-owned Story State…"));
    }
}

void StoryLabDialog::handleResponse(const QString &requestId, const QJsonObject &response)
{
    if (requestId.isEmpty() || requestId != m_requestId) {
        return;
    }
    const QString kind = m_requestKind;
    m_requestId.clear();
    m_requestKind.clear();
    if (!response.value(QStringLiteral("ok")).toBool()) {
        const QString error = response.value(QStringLiteral("error")).toObject().value(QStringLiteral("message")).toString();
        setReady(error.isEmpty() ? tr("Story Engine request failed.") : error);
        return;
    }
    const QJsonObject result = response.value(QStringLiteral("result")).toObject();
    if (kind == QStringLiteral("__resolve_story_unit")) {
        const QJsonObject resolution = result.value(QStringLiteral("resolution")).toObject();
        const QJsonObject match = resolution.value(QStringLiteral("match")).toObject();
        m_activeStoryUnit = match.value(QStringLiteral("story_unit_id")).toString();
        if (m_activeStoryUnit.isEmpty()) {
            m_positionLabel->setText(
                tr("Current Story Unit is ambiguous. Select a chapter scope or establish manuscript order before position-sensitive analysis."));
            setPositionSensitiveEnabled(false);
        } else {
            const QString title = match.value(QStringLiteral("display_title")).toString(m_scopeTitle);
            m_positionLabel->setText(tr("Story position: %1 · branch %2").arg(title, m_activeBranch));
            setPositionSensitiveEnabled(true);
        }
        setReady();
        return;
    }
    if (kind == QStringLiteral("__writer_mutation")) {
        const QString mutation = m_pendingMutation;
        m_pendingMutation.clear();
        setReady(tr("Writer-owned Story State saved."));
        if (mutation == QStringLiteral("story_lens")) {
            refreshLenses();
        } else if (mutation == QStringLiteral("writer_preference")) {
            refreshWriterModel();
        } else if (mutation == QStringLiteral("entity_alias")) {
            refreshEntities();
        }
        return;
    }
    if (kind == QStringLiteral("__index_rebuild")) {
        m_advancedOutput->setPlainText(formatJson(result));
        setReady(tr("Story Engine index rebuilt from source + durable writer state."));
        return;
    }
    if (kind == QStringLiteral("__index_batch")) {
        m_advancedOutput->setPlainText(formatJson(result));
        const bool complete = result.value(QStringLiteral("complete")).toBool();
        setReady(complete ? tr("Background indexing is complete.") : tr("Background indexing checkpoint saved; continue when convenient."));
        return;
    }
    if (kind == QStringLiteral("__legacy_bind")) {
        m_advancedOutput->setPlainText(formatJson(result));
        setReady(tr("Legacy Story Workspace bound without rewriting it."));
        return;
    }
    if (kind == QStringLiteral("__state_backup")) {
        m_advancedOutput->setPlainText(formatJson(result));
        setReady(tr("Durable Story State backup created."));
        return;
    }
    if (kind == QStringLiteral("__state_recover")) {
        m_advancedOutput->setPlainText(formatJson(result));
        setReady(result.value(QStringLiteral("recovered")).toBool() ? tr("Story Engine recovery completed.")
                                                                    : tr("No interrupted Story Engine operation required recovery."));
        return;
    }
    if (kind == QStringLiteral("__compatibility_for_restore")) {
        const QJsonObject compatibility = result.value(QStringLiteral("compatibility")).toObject();
        QStringList backups;
        for (const QJsonValue &value : compatibility.value(QStringLiteral("backups")).toArray()) {
            if (value.isString() && !value.toString().isEmpty()) {
                backups.append(value.toString());
            }
        }
        if (backups.isEmpty()) {
            setReady(tr("No Story State backups are available to restore."));
            return;
        }
        bool accepted = false;
        const QString selected = QInputDialog::getItem(this, tr("Restore Story State"), tr("Backup"), backups, backups.size() - 1, false, &accepted);
        if (!accepted || selected.isEmpty()) {
            setReady();
            return;
        }
        if (QMessageBox::warning(
                this,
                tr("Restore writer-owned Story State?"),
                tr("ThothPad will create a backup of the current durable Story State, restore %1, discard the disposable cache, and rebuild it. "
                   "Manuscript files are not changed.")
                    .arg(selected),
                QMessageBox::Yes | QMessageBox::Cancel,
                QMessageBox::Cancel)
            != QMessageBox::Yes) {
            setReady();
            return;
        }
        QJsonObject payload{{QStringLiteral("project_root"), m_projectRoot},
                            {QStringLiteral("backup_name"), selected},
                            {QStringLiteral("writer_confirmed"), true}};
        m_requestKind = QStringLiteral("__state_restore");
        m_requestId = m_engine->send(QStringLiteral("story_state_restore"), payload);
        if (m_requestId.isEmpty()) {
            setReady(tr("Could not start Story State restore."));
        } else {
            setBusy(tr("Restoring durable Story State and rebuilding the cache…"));
        }
        return;
    }
    if (kind == QStringLiteral("__state_restore")) {
        m_advancedOutput->setPlainText(formatJson(result));
        setReady(tr("Story State restored from backup."));
        return;
    }
    if (kind == QStringLiteral("__proposal_review")) {
        m_proposalOutput->setPlainText(formatJson(result));
        setReady(tr("Proposal review saved."));
        refreshProposals();
        return;
    }
    if (kind == QStringLiteral("__project_export")) {
        if (m_pendingExportPath.isEmpty()) {
            setReady(tr("Export destination was lost; metadata was not written."));
            return;
        }
        QSaveFile file(m_pendingExportPath);
        if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
            m_pendingExportPath.clear();
            setReady(tr("Could not open the selected export destination."));
            return;
        }
        const QByteArray bytes = QJsonDocument(result).toJson(QJsonDocument::Indented);
        if (file.write(bytes) != bytes.size() || !file.commit()) {
            m_pendingExportPath.clear();
            setReady(tr("Could not atomically save Story Project metadata."));
            return;
        }
        const QString savedPath = m_pendingExportPath;
        m_pendingExportPath.clear();
        m_advancedOutput->setPlainText(tr("Portable Story Project metadata saved to:\n%1\n\nNo manuscript text or credentials are included.").arg(savedPath));
        setReady(tr("Story metadata exported"));
        return;
    }
    if (kind == QStringLiteral("__project_import")) {
        m_advancedOutput->setPlainText(formatJson(result));
        setReady(tr("Story metadata imported and rebound to this project."));
        refreshEntities();
        return;
    }
    if (kind == QStringLiteral("list_entities")) {
        populateEntities(result.value(QStringLiteral("entities")).toArray());
    } else if (kind == QStringLiteral("query_claims") || kind == QStringLiteral("list_conflicts")) {
        m_groundingOutput->setPlainText(formatResult(kind, result));
    } else if (kind == QStringLiteral("list_story_proposals")) {
        populateProposals(result.value(QStringLiteral("proposals")).toArray());
        m_proposalOutput->setPlainText(formatJson(result));
    } else if (kind == QStringLiteral("list_story_lenses")) {
        populateLenses(result.value(QStringLiteral("story_lenses")).toArray());
        m_lensOutput->setPlainText(formatResult(kind, result));
    } else if (kind == QStringLiteral("get_writer_model")) {
        populateWriterModel(result);
    } else if (kind == QStringLiteral("cold_reader_at") || kind == QStringLiteral("audit_reveal_fairness") || kind == QStringLiteral("get_reader_expectations")
               || kind == QStringLiteral("get_dramatic_irony")) {
        m_readerOutput->setPlainText(formatResult(kind, result));
    } else if (kind == QStringLiteral("run_editorial_council")) {
        m_councilOutput->setPlainText(formatResult(kind, result));
    } else if (kind == QStringLiteral("run_story_lens")) {
        m_lensOutput->setPlainText(formatResult(kind, result));
    } else if (kind == QStringLiteral("get_reader_experience") || kind == QStringLiteral("get_reader_experience_timeline")) {
        m_experienceOutput->setPlainText(formatResult(kind, result));
    } else if (kind == QStringLiteral("explore_story") || kind == QStringLiteral("get_scene_semantics") || kind == QStringLiteral("audit_continuity")
               || kind == QStringLiteral("get_character_arc") || kind == QStringLiteral("get_relationship_arc")
               || kind == QStringLiteral("audit_ending_integrity") || kind == QStringLiteral("get_project_health") || kind == QStringLiteral("get_index_status")
               || kind == QStringLiteral("run_wow_acceptance") || kind == QStringLiteral("get_migration_status")
               || kind == QStringLiteral("get_indexing_status") || kind == QStringLiteral("get_performance_report")
               || kind == QStringLiteral("get_security_audit") || kind == QStringLiteral("get_egress_preview")
               || kind == QStringLiteral("get_model_fingerprint") || kind == QStringLiteral("get_acceptance_metrics")
               || kind == QStringLiteral("get_retrieval_capabilities") || kind == QStringLiteral("explain_story_record")
               || kind == QStringLiteral("run_operational_acceptance") || kind == QStringLiteral("get_release_validation")
               || kind == QStringLiteral("get_recovery_status") || kind == QStringLiteral("get_observability_report")
               || kind == QStringLiteral("get_resource_policy") || kind == QStringLiteral("get_path_resilience")
               || kind == QStringLiteral("get_offline_readiness") || kind == QStringLiteral("get_compatibility_status")
               || kind == QStringLiteral("run_release_candidate_acceptance") || kind == QStringLiteral("run_soak_replay")
               || kind == QStringLiteral("get_support_bundle") || kind == QStringLiteral("get_interface_fingerprint")
               || kind == QStringLiteral("get_performance_budget") || kind == QStringLiteral("get_relocation_readiness")
               || kind == QStringLiteral("get_release_readiness")) {
        m_advancedOutput->setPlainText(formatJson(result));
    }
    setReady();
}

void StoryLabDialog::runReaderTool()
{
    const QString tool = m_readerAction->currentData().toString();
    QJsonObject arguments;
    if (tool == QStringLiteral("get_dramatic_irony")) {
        const QString character = m_readerCharacter->text().trimmed();
        if (character.isEmpty()) {
            QMessageBox::information(this, tr("Character required"), tr("Choose a character for dramatic-irony comparison."));
            return;
        }
        arguments.insert(QStringLiteral("character"), character);
    }
    requestTool(tool, arguments);
}

void StoryLabDialog::runCouncil()
{
    requestTool(QStringLiteral("run_editorial_council"));
}

void StoryLabDialog::refreshEntities()
{
    requestTool(QStringLiteral("list_entities"), QJsonObject{{QStringLiteral("limit"), 500}});
}

void StoryLabDialog::addEntityAlias()
{
    const int row = m_entityTable->currentRow();
    if (row < 0 || !m_entityTable->item(row, 0)) {
        return;
    }
    const QString entityId = m_entityTable->item(row, 0)->data(Qt::UserRole).toString();
    const QString entityName = m_entityTable->item(row, 0)->text();
    if (entityId.isEmpty()) {
        return;
    }
    bool accepted = false;
    const QString alias =
        QInputDialog::getText(this, tr("Add writer-confirmed alias"), tr("Another name that refers to %1").arg(entityName), QLineEdit::Normal, {}, &accepted)
            .trimmed();
    if (!accepted || alias.isEmpty()) {
        return;
    }
    if (QMessageBox::question(this,
                              tr("Confirm entity alias?"),
                              tr("Treat “%1” as an alias of %2? This changes Story Model identity metadata, not manuscript text.").arg(alias, entityName),
                              QMessageBox::Yes | QMessageBox::Cancel,
                              QMessageBox::Cancel)
        != QMessageBox::Yes) {
        return;
    }
    requestWriterMutation(QStringLiteral("entity_alias"), QJsonObject{{QStringLiteral("entity_id"), entityId}, {QStringLiteral("alias"), alias}});
}

void StoryLabDialog::runGrounding(bool conflicts)
{
    if (conflicts) {
        requestTool(QStringLiteral("list_conflicts"), QJsonObject{{QStringLiteral("include_closed"), false}, {QStringLiteral("limit"), 200}});
        return;
    }
    QJsonObject arguments{{QStringLiteral("limit"), 200}};
    const QString entity = m_groundingEntity->text().trimmed();
    if (!entity.isEmpty()) {
        arguments.insert(QStringLiteral("entity"), entity);
    }
    requestTool(QStringLiteral("query_claims"), arguments);
}

void StoryLabDialog::refreshLenses()
{
    requestTool(QStringLiteral("list_story_lenses"));
}

void StoryLabDialog::createLens()
{
    bool accepted = false;
    const QString name = QInputDialog::getText(this, tr("New Story Lens"), tr("Lens name"), QLineEdit::Normal, {}, &accepted).trimmed();
    if (!accepted || name.isEmpty()) {
        return;
    }
    const QString definition = QInputDialog::getMultiLineText(this,
                                                              tr("Define Story Lens"),
                                                              tr("What recurring story behavior or pattern should ThothPad retrieve evidence for?"),
                                                              {},
                                                              &accepted)
                                   .trimmed();
    if (!accepted || definition.isEmpty()) {
        return;
    }
    if (QMessageBox::question(this,
                              tr("Save reusable Story Lens?"),
                              tr("Save “%1” as writer-owned project analysis? Lens findings remain evidence candidates, not canon.").arg(name),
                              QMessageBox::Save | QMessageBox::Cancel,
                              QMessageBox::Cancel)
        != QMessageBox::Save) {
        return;
    }
    requestWriterMutation(QStringLiteral("story_lens"), QJsonObject{{QStringLiteral("name"), name}, {QStringLiteral("definition"), definition}});
}

void StoryLabDialog::runLens()
{
    const QString lensId = m_lensCombo->currentData().toString();
    if (lensId.isEmpty()) {
        setReady(tr("Create or select a Story Lens first."));
        return;
    }
    requestTool(QStringLiteral("run_story_lens"), QJsonObject{{QStringLiteral("lens_id"), lensId}});
}

void StoryLabDialog::runExperience(bool timeline)
{
    requestTool(timeline ? QStringLiteral("get_reader_experience_timeline") : QStringLiteral("get_reader_experience"));
}

void StoryLabDialog::runAdvancedTool()
{
    const QString tool = m_advancedAction->currentData().toString();
    QJsonObject arguments;
    if (tool == QStringLiteral("explore_story")) {
        const QString query = m_advancedQuery->text().trimmed();
        if (query.isEmpty()) {
            QMessageBox::information(this, tr("Query required"), tr("Enter a Story Explorer query."));
            return;
        }
        arguments.insert(QStringLiteral("query"), query);
        arguments.insert(QStringLiteral("limit"), 75);
    } else if (tool == QStringLiteral("audit_continuity") || tool == QStringLiteral("get_character_arc") || tool == QStringLiteral("run_wow_acceptance")) {
        const QString character = m_advancedCharacter->text().trimmed();
        if (tool == QStringLiteral("get_character_arc") && character.isEmpty()) {
            QMessageBox::information(this, tr("Character required"), tr("Choose a character for the arc analysis."));
            return;
        }
        if (!character.isEmpty()) {
            arguments.insert(QStringLiteral("character"), character);
        }
    } else if (tool == QStringLiteral("get_relationship_arc")) {
        const QString entityA = m_advancedCharacter->text().trimmed();
        const QString entityB = m_advancedOtherEntity->text().trimmed();
        if (entityA.isEmpty() || entityB.isEmpty()) {
            QMessageBox::information(this, tr("Two entities required"), tr("Enter both entities for relationship-arc analysis."));
            return;
        }
        arguments.insert(QStringLiteral("entity_a"), entityA);
        arguments.insert(QStringLiteral("entity_b"), entityB);
    } else if (tool == QStringLiteral("get_egress_preview")) {
        QString prompt = m_advancedQuery->text().trimmed();
        if (prompt.isEmpty()) {
            prompt = tr("current story context");
        }
        arguments.insert(QStringLiteral("prompt"), prompt);
        arguments.insert(QStringLiteral("selected_is_remote"), true);
        arguments.insert(QStringLiteral("include_text_preview"), false);
        if (!m_activeStoryUnit.isEmpty()) {
            arguments.insert(QStringLiteral("active_story_unit"), m_activeStoryUnit);
        }
        if (!m_sourcePath.isEmpty()) {
            arguments.insert(QStringLiteral("active_source_path"), m_sourcePath);
        }
        if (!m_activeCharacter.isEmpty()) {
            arguments.insert(QStringLiteral("active_character"), m_activeCharacter);
        }
    } else if (tool == QStringLiteral("explain_story_record")) {
        const QString recordKind = m_advancedQuery->text().trimmed();
        const QString recordId = m_advancedCharacter->text().trimmed();
        if (recordKind.isEmpty() || recordId.isEmpty()) {
            QMessageBox::information(this,
                                     tr("Record required"),
                                     tr("For Ask ThothPad Why, enter the record kind in Query and the stable record ID in Character / A."));
            return;
        }
        arguments.insert(QStringLiteral("record_kind"), recordKind);
        arguments.insert(QStringLiteral("record_id"), recordId);
    } else if (tool == QStringLiteral("run_operational_acceptance")) {
        const QString prompt = m_advancedQuery->text().trimmed();
        if (!prompt.isEmpty()) {
            arguments.insert(QStringLiteral("prompt"), prompt);
        }
    } else if (tool == QStringLiteral("run_soak_replay")) {
        arguments.insert(QStringLiteral("cycles"), 3);
    } else if (tool == QStringLiteral("get_performance_budget")) {
        arguments.insert(QStringLiteral("source_lookup_100_budget_ms"), 1000.0);
    }
    requestTool(tool, arguments);
}

void StoryLabDialog::rebuildIndex()
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady()) {
        setReady(tr("Story Engine is not ready."));
        return;
    }
    if (QMessageBox::warning(this,
                             tr("Rebuild Story Engine index?"),
                             tr("ThothPad will snapshot durable writer-owned Story State, delete only the disposable Story Engine cache, "
                                "then rebuild it from the project sources. Manuscript files are not changed."),
                             QMessageBox::Yes | QMessageBox::Cancel,
                             QMessageBox::Cancel)
        != QMessageBox::Yes) {
        return;
    }
    QJsonObject payload{{QStringLiteral("project_root"), m_projectRoot}, {QStringLiteral("writer_confirmed"), true}};
    m_requestKind = QStringLiteral("__index_rebuild");
    m_requestId = m_engine->send(QStringLiteral("story_index_rebuild"), payload);
    if (m_requestId.isEmpty()) {
        setReady(tr("Could not start Story Engine index rebuild."));
    } else {
        setBusy(tr("Rebuilding Story Engine index…"));
    }
}

void StoryLabDialog::continueIndexing()
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("story_index_batch"))) {
        setReady(tr("Resumable indexing is not available in this engine build."));
        return;
    }
    QJsonObject payload{{QStringLiteral("project_root"), m_projectRoot}, {QStringLiteral("maximum_documents"), 100}, {QStringLiteral("reset"), false}};
    m_requestKind = QStringLiteral("__index_batch");
    m_requestId = m_engine->send(QStringLiteral("story_index_batch"), payload);
    if (m_requestId.isEmpty()) {
        setReady(tr("Could not start the next background-index batch."));
    } else {
        setBusy(tr("Indexing the next bounded source batch…"));
    }
}

void StoryLabDialog::bindLegacyWorkspace()
{
    if (m_projectRoot.isEmpty() || m_sourcePath.isEmpty() || !m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("story_legacy_bind"))) {
        setReady(tr("Open an indexed manuscript inside the Story Project before binding a legacy workspace."));
        return;
    }
    const QString path =
        QFileDialog::getOpenFileName(this, tr("Bind Legacy Story Workspace"), m_projectRoot, tr("Story workspace JSON (*.story.json *.json);;All files (*)"));
    if (path.isEmpty()) {
        return;
    }
    if (QMessageBox::question(this,
                              tr("Bind legacy workspace?"),
                              tr("ThothPad will read this schema-2 workspace, preserve it byte-for-byte, and add stable Story Unit links to the current "
                                 "manuscript. The legacy file will not be rewritten."),
                              QMessageBox::Yes | QMessageBox::Cancel,
                              QMessageBox::Cancel)
        != QMessageBox::Yes) {
        return;
    }
    QJsonObject payload{{QStringLiteral("project_root"), m_projectRoot},
                        {QStringLiteral("workspace_path"), path},
                        {QStringLiteral("manuscript_path"), m_sourcePath},
                        {QStringLiteral("writer_confirmed"), true}};
    m_requestKind = QStringLiteral("__legacy_bind");
    m_requestId = m_engine->send(QStringLiteral("story_legacy_bind"), payload);
    if (m_requestId.isEmpty()) {
        setReady(tr("Could not start legacy Story Workspace binding."));
    } else {
        setBusy(tr("Binding legacy Story Workspace without rewriting it…"));
    }
}

void StoryLabDialog::backupStoryState()
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("story_state_backup"))) {
        setReady(tr("Story State backup is not available in this engine build."));
        return;
    }
    m_requestKind = QStringLiteral("__state_backup");
    m_requestId = m_engine->send(QStringLiteral("story_state_backup"), QJsonObject{{QStringLiteral("project_root"), m_projectRoot}});
    if (m_requestId.isEmpty()) {
        setReady(tr("Could not create Story State backup."));
    } else {
        setBusy(tr("Creating durable Story State backup…"));
    }
}

void StoryLabDialog::recoverStoryState()
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("story_recover"))) {
        setReady(tr("Story Engine recovery is not available in this engine build."));
        return;
    }
    if (QMessageBox::warning(this,
                             tr("Recover interrupted Story Engine operation?"),
                             tr("If a recovery journal is pending, ThothPad will discard only the compiled Story Engine cache and rebuild from project sources "
                                "plus durable writer-owned state. Manuscript files are not changed."),
                             QMessageBox::Yes | QMessageBox::Cancel,
                             QMessageBox::Cancel)
        != QMessageBox::Yes) {
        return;
    }
    m_requestKind = QStringLiteral("__state_recover");
    m_requestId = m_engine->send(QStringLiteral("story_recover"),
                                 QJsonObject{{QStringLiteral("project_root"), m_projectRoot}, {QStringLiteral("writer_confirmed"), true}});
    if (m_requestId.isEmpty()) {
        setReady(tr("Could not start Story Engine recovery."));
    } else {
        setBusy(tr("Checking and recovering durable Story State…"));
    }
}

void StoryLabDialog::restoreStoryState()
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("story_state_restore"))) {
        setReady(tr("Story State restore is not available in this engine build."));
        return;
    }
    QJsonObject payload{{QStringLiteral("project_root"), m_projectRoot},
                        {QStringLiteral("tool_id"), QStringLiteral("get_compatibility_status")},
                        {QStringLiteral("arguments"), QJsonObject{}}};
    m_requestKind = QStringLiteral("__compatibility_for_restore");
    m_requestId = m_engine->send(QStringLiteral("story_tool"), payload);
    if (m_requestId.isEmpty()) {
        setReady(tr("Could not load Story State backups."));
    } else {
        setBusy(tr("Loading Story State backup list…"));
    }
}

void StoryLabDialog::exportProjectMetadata()
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady()) {
        setReady(tr("Story Engine is not ready."));
        return;
    }
    const QString path = QFileDialog::getSaveFileName(this,
                                                      tr("Export Story Project Metadata"),
                                                      QStringLiteral("thothpad-story-project.json"),
                                                      tr("JSON files (*.json);;All files (*)"));
    if (path.isEmpty()) {
        return;
    }
    m_pendingExportPath = path;
    m_requestKind = QStringLiteral("__project_export");
    m_requestId = m_engine->send(QStringLiteral("story_project_export"), QJsonObject{{QStringLiteral("project_root"), m_projectRoot}});
    if (m_requestId.isEmpty()) {
        m_pendingExportPath.clear();
        setReady(tr("Could not export Story Project metadata."));
    } else {
        setBusy(tr("Preparing portable Story Project metadata…"));
    }
}

void StoryLabDialog::importProjectMetadata()
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady()) {
        setReady(tr("Story Engine is not ready."));
        return;
    }
    const QString path = QFileDialog::getOpenFileName(this, tr("Import Story Project Metadata"), QString(), tr("JSON files (*.json);;All files (*)"));
    if (path.isEmpty()) {
        return;
    }
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        setReady(tr("Could not read the selected Story Project metadata file."));
        return;
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        setReady(tr("The selected file is not a valid Story Project metadata bundle."));
        return;
    }
    if (QMessageBox::warning(this,
                             tr("Import writer-owned Story metadata?"),
                             tr("This will merge the bundle's writer-owned Story State and project rules into this already-initialized project. "
                                "Source files are not copied or overwritten."),
                             QMessageBox::Yes | QMessageBox::Cancel,
                             QMessageBox::Cancel)
        != QMessageBox::Yes) {
        return;
    }
    QJsonObject payload{{QStringLiteral("project_root"), m_projectRoot},
                        {QStringLiteral("bundle"), document.object()},
                        {QStringLiteral("writer_confirmed"), true}};
    m_requestKind = QStringLiteral("__project_import");
    m_requestId = m_engine->send(QStringLiteral("story_project_import"), payload);
    if (m_requestId.isEmpty()) {
        setReady(tr("Could not start Story Project metadata import."));
    } else {
        setBusy(tr("Importing and rebinding writer-owned Story State…"));
    }
}

void StoryLabDialog::refreshProposals()
{
    requestTool(QStringLiteral("list_story_proposals"), QJsonObject{{QStringLiteral("limit"), 500}});
}

void StoryLabDialog::reviewSelectedProposal(const QString &decision)
{
    const int row = m_proposalTable->currentRow();
    if (row < 0 || !m_proposalTable->item(row, 4)) {
        return;
    }
    const QJsonObject proposal = QJsonObject::fromVariantMap(m_proposalTable->item(row, 4)->data(Qt::UserRole).toMap());
    if (proposal.isEmpty() || proposal.value(QStringLiteral("status")).toString() != QStringLiteral("PROPOSED")) {
        return;
    }
    const bool accepting = decision == QStringLiteral("ACCEPTED");
    const QString verb = accepting ? tr("Accept") : tr("Reject");
    const QString target = proposal.value(QStringLiteral("target_mutation")).toString();
    if (QMessageBox::question(this,
                              tr("Review Story proposal"),
                              tr("%1 this proposal?\n\nTarget Story State: %2\n\n%3").arg(verb, target, formatJson(proposal.value(QStringLiteral("payload")))),
                              QMessageBox::Yes | QMessageBox::Cancel,
                              QMessageBox::Cancel)
        != QMessageBox::Yes) {
        return;
    }
    QJsonObject payload{{QStringLiteral("project_root"), m_projectRoot},
                        {QStringLiteral("proposal_id"), proposal.value(QStringLiteral("proposal_id")).toString()},
                        {QStringLiteral("decision"), decision},
                        {QStringLiteral("writer_confirmed"), true}};
    m_requestKind = QStringLiteral("__proposal_review");
    m_requestId = m_engine->send(QStringLiteral("story_proposal_review"), payload);
    if (m_requestId.isEmpty()) {
        setReady(tr("Could not start proposal review."));
    } else {
        setBusy(accepting ? tr("Applying accepted proposal through writer-owned Story State…") : tr("Recording rejected proposal…"));
    }
}

void StoryLabDialog::refreshWriterModel()
{
    requestTool(QStringLiteral("get_writer_model"), QJsonObject{{QStringLiteral("include_ignored"), true}});
}

void StoryLabDialog::reviewSelectedPreference(const QString &status)
{
    const int row = m_writerTable->currentRow();
    if (row < 0 || !m_writerTable->item(row, 2)) {
        return;
    }
    const QJsonObject preference = QJsonObject::fromVariantMap(m_writerTable->item(row, 2)->data(Qt::UserRole).toMap());
    if (preference.isEmpty()) {
        return;
    }
    const QString verb = status == QStringLiteral("CONFIRMED") ? tr("Confirm") : tr("Ignore");
    if (QMessageBox::question(this,
                              tr("Review Writer Model preference"),
                              tr("%1 this preference?\n\n%2\n\nYour explicit review outranks future behavioral inference.")
                                  .arg(verb, preference.value(QStringLiteral("statement")).toString()),
                              QMessageBox::Yes | QMessageBox::Cancel,
                              QMessageBox::Cancel)
        != QMessageBox::Yes) {
        return;
    }
    QJsonObject payload = preference;
    payload.insert(QStringLiteral("status"), status);
    payload.remove(QStringLiteral("reviewable"));
    payload.remove(QStringLiteral("evidence_count"));
    requestWriterMutation(QStringLiteral("writer_preference"), payload);
}

void StoryLabDialog::populateLenses(const QJsonArray &lenses)
{
    const QString selected = m_lensCombo->currentData().toString();
    m_lensCombo->clear();
    for (const QJsonValue &value : lenses) {
        const QJsonObject lens = value.toObject();
        const QString id = lens.value(QStringLiteral("lens_id")).toString();
        const QString name = lens.value(QStringLiteral("name")).toString();
        if (!id.isEmpty() && !name.isEmpty()) {
            m_lensCombo->addItem(name, id);
        }
    }
    const int index = m_lensCombo->findData(selected);
    if (index >= 0) {
        m_lensCombo->setCurrentIndex(index);
    }
    m_lensRun->setEnabled(m_lensCombo->count() > 0);
}

void StoryLabDialog::populateEntities(const QJsonArray &entities)
{
    m_entityTable->setRowCount(entities.size());
    for (int row = 0; row < entities.size(); ++row) {
        const QJsonObject entity = entities.at(row).toObject();
        auto *nameItem = new QTableWidgetItem(entity.value(QStringLiteral("canonical_name")).toString());
        nameItem->setData(Qt::UserRole, entity.value(QStringLiteral("entity_id")).toString());
        m_entityTable->setItem(row, 0, nameItem);
        m_entityTable->setItem(row, 1, new QTableWidgetItem(displayKey(entity.value(QStringLiteral("entity_type")).toString())));
        m_entityTable->setItem(row, 2, new QTableWidgetItem(entity.value(QStringLiteral("status")).toString()));
        QStringList aliases;
        for (const QJsonValue &value : entity.value(QStringLiteral("aliases")).toArray()) {
            const QJsonObject alias = value.toObject();
            const QString text = alias.value(QStringLiteral("alias")).toString();
            if (!text.isEmpty() && text != entity.value(QStringLiteral("canonical_name")).toString()) {
                aliases << text + (alias.value(QStringLiteral("user_confirmed")).toInt() ? tr(" ✓") : QString());
            }
        }
        m_entityTable->setItem(row, 3, new QTableWidgetItem(aliases.join(QStringLiteral(", "))));
    }
    m_entityAlias->setEnabled(false);
}

void StoryLabDialog::populateProposals(const QJsonArray &proposals)
{
    m_proposalTable->setRowCount(proposals.size());
    for (int row = 0; row < proposals.size(); ++row) {
        const QJsonObject proposal = proposals.at(row).toObject();
        const QString status = proposal.value(QStringLiteral("status")).toString();
        m_proposalTable->setItem(row, 0, new QTableWidgetItem(status));
        m_proposalTable->setItem(row, 1, new QTableWidgetItem(proposal.value(QStringLiteral("proposal_kind")).toString()));
        m_proposalTable->setItem(row, 2, new QTableWidgetItem(proposal.value(QStringLiteral("target_mutation")).toString()));
        m_proposalTable->setItem(row, 3, new QTableWidgetItem(proposal.value(QStringLiteral("branch_id")).toString()));
        auto *summary =
            new QTableWidgetItem(QString::fromUtf8(QJsonDocument(proposal.value(QStringLiteral("payload")).toObject()).toJson(QJsonDocument::Compact)));
        summary->setData(Qt::UserRole, proposal.toVariantMap());
        summary->setData(Qt::UserRole + 1, status);
        m_proposalTable->setItem(row, 4, summary);
    }
    m_proposalAccept->setEnabled(false);
    m_proposalReject->setEnabled(false);
    if (!proposals.isEmpty()) {
        m_proposalTable->selectRow(0);
    }
}

void StoryLabDialog::populateWriterModel(const QJsonObject &model)
{
    const QJsonArray preferences = model.value(QStringLiteral("preferences")).toArray();
    m_writerTable->setRowCount(preferences.size());
    for (int row = 0; row < preferences.size(); ++row) {
        const QJsonObject preference = preferences.at(row).toObject();
        const QString scope = QStringLiteral("%1%2").arg(preference.value(QStringLiteral("scope_kind")).toString(),
                                                         preference.value(QStringLiteral("scope_id")).toString().isEmpty()
                                                             ? QString()
                                                             : QStringLiteral(": ") + preference.value(QStringLiteral("scope_id")).toString());
        m_writerTable->setItem(row, 0, new QTableWidgetItem(preference.value(QStringLiteral("status")).toString()));
        m_writerTable->setItem(row, 1, new QTableWidgetItem(scope));
        auto *statement = new QTableWidgetItem(preference.value(QStringLiteral("statement")).toString());
        statement->setData(Qt::UserRole, preference.toVariantMap());
        m_writerTable->setItem(row, 2, statement);
        m_writerTable->setItem(row, 3, new QTableWidgetItem(QString::number(preference.value(QStringLiteral("evidence_count")).toInt())));
    }
    if (!preferences.isEmpty()) {
        m_writerTable->selectRow(0);
    }
    m_writerOutput->setPlainText(tr("%1 confirmed · %2 provisional\nBehavioral inference is never canon until you review it.")
                                     .arg(model.value(QStringLiteral("confirmed_count")).toInt())
                                     .arg(model.value(QStringLiteral("provisional_count")).toInt()));
}

QString StoryLabDialog::formatResult(const QString &toolId, const QJsonObject &result) const
{
    QStringList lines;
    if (toolId == QStringLiteral("query_claims")) {
        const QJsonArray claims = result.value(QStringLiteral("claims")).toArray();
        lines << tr("GROUNDED STORY CLAIMS · %1 result(s)").arg(claims.size()) << QString();
        for (const QJsonValue &value : claims) {
            const QJsonObject claim = value.toObject();
            lines << QStringLiteral("%1 · %2").arg(claim.value(QStringLiteral("status")).toString(), claimLabel(claim));
            const QJsonArray evidence = claim.value(QStringLiteral("evidence")).toArray();
            if (evidence.isEmpty()) {
                lines << tr("  No exact evidence is currently attached.");
            }
            for (const QJsonValue &evidenceValue : evidence) {
                const QJsonObject evidenceItem = evidenceValue.toObject();
                QString source = evidenceItem.value(QStringLiteral("relative_path")).toString();
                if (source.isEmpty()) {
                    source = evidenceItem.value(QStringLiteral("source_path")).toString();
                }
                if (source.isEmpty()) {
                    source = evidenceItem.value(QStringLiteral("source_id")).toString();
                }
                lines << QStringLiteral("  ↳ %1 · [%2,%3) · %4")
                             .arg(source)
                             .arg(evidenceItem.value(QStringLiteral("start_offset")).toInt())
                             .arg(evidenceItem.value(QStringLiteral("end_offset")).toInt())
                             .arg(evidenceItem.value(QStringLiteral("stale")).toInt() ? tr("STALE") : tr("current"));
            }
            lines << QString();
        }
        return lines.join(QChar('\n'));
    }
    if (toolId == QStringLiteral("list_conflicts")) {
        const QJsonArray conflicts = result.value(QStringLiteral("conflicts")).toArray();
        lines << tr("EXPLICIT STORY CONFLICTS · %1 open conflict(s)").arg(conflicts.size())
              << tr("ThothPad reports conflicting evidence; it does not silently choose a winner.") << QString();
        for (const QJsonValue &value : conflicts) {
            const QJsonObject conflict = value.toObject();
            lines << QStringLiteral("%1 · %2 · %3")
                         .arg(conflict.value(QStringLiteral("severity")).toString(),
                              conflict.value(QStringLiteral("type")).toString(),
                              conflict.value(QStringLiteral("status")).toString());
            for (const QJsonValue &claimValue : conflict.value(QStringLiteral("claims")).toArray()) {
                const QJsonObject claim = claimValue.toObject();
                lines << QStringLiteral("  • %1 · %2").arg(claim.value(QStringLiteral("status")).toString(), claimLabel(claim));
                for (const QJsonValue &evidenceValue : claim.value(QStringLiteral("evidence")).toArray()) {
                    const QJsonObject evidence = evidenceValue.toObject();
                    QString source = evidence.value(QStringLiteral("relative_path")).toString();
                    if (source.isEmpty()) {
                        source = evidence.value(QStringLiteral("source_id")).toString();
                    }
                    lines << QStringLiteral("    ↳ %1 [%2,%3)")
                                 .arg(source)
                                 .arg(evidence.value(QStringLiteral("start_offset")).toInt())
                                 .arg(evidence.value(QStringLiteral("end_offset")).toInt());
                }
            }
            lines << QString();
        }
        return lines.join(QChar('\n'));
    }
    if (toolId == QStringLiteral("cold_reader_at")) {
        const QJsonObject data = result.value(QStringLiteral("cold_reader")).toObject();
        const QJsonObject boundary = data.value(QStringLiteral("hard_boundary")).toObject();
        lines << tr("COLD READER · hard story-position boundary")
              << tr("Source: %1 · visible through offset %2")
                     .arg(boundary.value(QStringLiteral("source_path")).toString())
                     .arg(boundary.value(QStringLiteral("visible_end")).toInt())
              << tr("Reader-state records: %1 · open reader questions: %2 · dramatic promises: %3")
                     .arg(data.value(QStringLiteral("reader_state")).toArray().size())
                     .arg(data.value(QStringLiteral("reader_questions")).toArray().size())
                     .arg(data.value(QStringLiteral("dramatic_promises")).toArray().size())
              << QString();
        for (const QJsonValue &value : data.value(QStringLiteral("reader_questions")).toArray()) {
            lines << QStringLiteral("? %1").arg(value.toObject().value(QStringLiteral("title")).toString());
        }
        for (const QJsonValue &value : data.value(QStringLiteral("dramatic_promises")).toArray()) {
            lines << QStringLiteral("→ %1").arg(value.toObject().value(QStringLiteral("title")).toString());
        }
        lines << QString() << tr("Retrieved evidence");
        for (const QJsonValue &value : data.value(QStringLiteral("evidence")).toArray()) {
            const QJsonObject evidence = value.toObject();
            lines << QStringLiteral("• %1\n  %2").arg(evidence.value(QStringLiteral("path")).toString(), evidence.value(QStringLiteral("excerpt")).toString());
        }
        return lines.join(QChar('\n'));
    }
    if (toolId == QStringLiteral("audit_reveal_fairness")) {
        const QJsonObject data = result.value(QStringLiteral("reveal_fairness")).toObject();
        lines << tr("REVEAL FAIRNESS · tracked evidence placement only")
              << tr("%1 reveal-state record(s) at this unit").arg(data.value(QStringLiteral("reveal_count")).toInt())
              << tr("Missing tracked setup is not proof that the prose is unfair.") << QString();
        for (const QJsonValue &value : data.value(QStringLiteral("findings")).toArray()) {
            const QJsonObject finding = value.toObject();
            lines << QStringLiteral("%1 · %2").arg(finding.value(QStringLiteral("status")).toString(), finding.value(QStringLiteral("observation")).toString());
            const QJsonObject claim = finding.value(QStringLiteral("claim")).toObject();
            if (!claim.isEmpty()) {
                lines << QStringLiteral("  %1").arg(claimLabel(claim));
            }
        }
        return lines.join(QChar('\n'));
    }
    if (toolId == QStringLiteral("get_reader_expectations")) {
        const QJsonObject data = result.value(QStringLiteral("reader_expectations")).toObject();
        lines << tr("READER FORWARD-MOTION STATE") << QString();
        for (const QJsonValue &value : data.value(QStringLiteral("reader_questions")).toArray()) {
            lines << QStringLiteral("? %1").arg(value.toObject().value(QStringLiteral("title")).toString());
        }
        for (const QJsonValue &value : data.value(QStringLiteral("dramatic_promises")).toArray()) {
            lines << QStringLiteral("→ %1").arg(value.toObject().value(QStringLiteral("title")).toString());
        }
        if (lines.size() == 2) {
            lines << tr("No tracked open reader question or dramatic promise is visible at this cutoff.");
        }
        return lines.join(QChar('\n'));
    }
    if (toolId == QStringLiteral("get_dramatic_irony")) {
        const QJsonObject data = result.value(QStringLiteral("dramatic_irony")).toObject();
        lines << tr("DRAMATIC IRONY · reader access versus %1").arg(data.value(QStringLiteral("character")).toString())
              << tr("Absence means untracked—not proof the character cannot know it.") << QString();
        for (const QJsonValue &value : data.value(QStringLiteral("dramatic_irony")).toArray()) {
            const QJsonObject item = value.toObject();
            lines << QStringLiteral("• %1").arg(claimLabel(item.value(QStringLiteral("claim")).toObject()))
                  << QStringLiteral("  %1").arg(item.value(QStringLiteral("observation")).toString());
        }
        return lines.join(QChar('\n'));
    }
    if (toolId == QStringLiteral("run_editorial_council")) {
        const QJsonObject council = result.value(QStringLiteral("council")).toObject();
        lines << tr("EDITORIAL COUNCIL · %1 independent read-only reviewers").arg(council.value(QStringLiteral("reviewer_count")).toInt())
              << tr("Agreement is synthesized after independent review; there is no agent chatter.") << QString();
        const QJsonArray agreement = council.value(QStringLiteral("agreement")).toArray();
        if (!agreement.isEmpty()) {
            lines << tr("Independent agreement");
            for (const QJsonValue &value : agreement) {
                const QJsonObject item = value.toObject();
                QStringList reviewers;
                for (const QJsonValue &reviewer : item.value(QStringLiteral("reviewers")).toArray()) {
                    reviewers << displayKey(reviewer.toString());
                }
                lines << tr("• %1 reviewers: %2").arg(item.value(QStringLiteral("reviewer_count")).toInt()).arg(reviewers.join(QStringLiteral(", ")));
            }
            lines << QString();
        }
        for (const QJsonValue &value : council.value(QStringLiteral("reviews")).toArray()) {
            const QJsonObject review = value.toObject();
            lines << QStringLiteral("%1 · %2 concern(s)")
                         .arg(displayKey(review.value(QStringLiteral("reviewer")).toString()))
                         .arg(review.value(QStringLiteral("concern_count")).toInt());
            for (const QJsonValue &findingValue : review.value(QStringLiteral("findings")).toArray()) {
                const QJsonObject finding = findingValue.toObject();
                lines << QStringLiteral("  %1 %2").arg(finding.value(QStringLiteral("signal")).toString() == QStringLiteral("concern") ? QStringLiteral("!")
                                                                                                                                       : QStringLiteral("·"),
                                                       finding.value(QStringLiteral("observation")).toString());
            }
            lines << QString();
        }
        return lines.join(QChar('\n'));
    }
    if (toolId == QStringLiteral("list_story_lenses")) {
        const QJsonArray lenses = result.value(QStringLiteral("story_lenses")).toArray();
        lines << tr("%1 reusable Story Lens(es). Definitions are writer-owned; findings are rebuildable.").arg(lenses.size());
        for (const QJsonValue &value : lenses) {
            const QJsonObject lens = value.toObject();
            lines << QStringLiteral("• %1 — %2").arg(lens.value(QStringLiteral("name")).toString(), lens.value(QStringLiteral("definition")).toString());
        }
        return lines.join(QChar('\n'));
    }
    if (toolId == QStringLiteral("run_story_lens")) {
        const QJsonObject data = result.value(QStringLiteral("story_lens")).toObject();
        const QJsonObject lens = data.value(QStringLiteral("lens")).toObject();
        lines << tr("STORY LENS · %1").arg(lens.value(QStringLiteral("name")).toString()) << tr("Evidence candidates only · semantic conclusion: NO")
              << QString();
        for (const QJsonValue &value : data.value(QStringLiteral("findings")).toArray()) {
            const QJsonObject finding = value.toObject();
            lines << QStringLiteral("• %1\n  %2").arg(finding.value(QStringLiteral("path")).toString(), finding.value(QStringLiteral("excerpt")).toString());
        }
        return lines.join(QChar('\n'));
    }
    if (toolId == QStringLiteral("get_reader_experience")) {
        const QJsonObject data = result.value(QStringLiteral("reader_experience")).toObject();
        lines << tr("READER EXPERIENCE · qualitative cues") << data.value(QStringLiteral("interpretation_limit")).toString() << QString();
        for (const QJsonValue &value : data.value(QStringLiteral("dimensions")).toArray()) {
            const QJsonObject dimension = value.toObject();
            lines << QStringLiteral("%1 · %2\n  %3")
                         .arg(displayKey(dimension.value(QStringLiteral("dimension")).toString()),
                              dimension.value(QStringLiteral("label")).toString(),
                              dimension.value(QStringLiteral("rationale")).toString());
        }
        return lines.join(QChar('\n'));
    }
    if (toolId == QStringLiteral("get_reader_experience_timeline")) {
        const QJsonObject data = result.value(QStringLiteral("reader_experience_timeline")).toObject();
        lines << tr("READER EXPERIENCE TIMELINE · writer-owned manuscript order") << tr("Qualitative only; no numerical scores are exposed.") << QString();
        for (const QJsonValue &value : data.value(QStringLiteral("timeline")).toArray()) {
            const QJsonObject unit = value.toObject();
            lines << QStringLiteral("%1").arg(unit.value(QStringLiteral("display_title")).toString());
            QStringList summary;
            for (const QJsonValue &dimensionValue : unit.value(QStringLiteral("dimensions")).toArray()) {
                const QJsonObject dimension = dimensionValue.toObject();
                summary << QStringLiteral("%1=%2").arg(displayKey(dimension.value(QStringLiteral("dimension")).toString()),
                                                       dimension.value(QStringLiteral("label")).toString());
            }
            lines << QStringLiteral("  %1").arg(summary.join(QStringLiteral(" · "))) << QString();
        }
        return lines.join(QChar('\n'));
    }
    return formatJson(result);
}

QString StoryLabDialog::formatJson(const QJsonValue &value) const
{
    if (value.isObject()) {
        return QString::fromUtf8(QJsonDocument(value.toObject()).toJson(QJsonDocument::Indented));
    }
    if (value.isArray()) {
        return QString::fromUtf8(QJsonDocument(value.toArray()).toJson(QJsonDocument::Indented));
    }
    return value.toVariant().toString();
}

void StoryLabDialog::setBusy(const QString &message)
{
    m_statusLabel->setText(message);
}

void StoryLabDialog::setReady(const QString &message)
{
    m_statusLabel->setText(message.isEmpty() ? tr("Ready") : message);
}
}
