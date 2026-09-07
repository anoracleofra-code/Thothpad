/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "storyintelligencewidget.h"

#include <QApplication>
#include <QClipboard>
#include <QComboBox>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QEvent>
#include <QFrame>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QJsonValue>
#include <QKeyEvent>
#include <QLabel>
#include <QLayoutItem>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QRegularExpression>
#include <QScrollArea>
#include <QScrollBar>
#include <QShortcut>
#include <QSignalBlocker>
#include <QSizePolicy>
#include <QSplitter>
#include <QStringList>
#include <QTabBar>
#include <QTabWidget>
#include <QTimer>
#include <QToolButton>
#include <QVBoxLayout>

namespace ghostwriter
{
namespace
{
constexpr int MinimumStoryPaneWidth = 280;

QString displayValue(const QJsonObject &context, const QString &key, const QString &fallback)
{
    const QString value = context.value(key).toString().trimmed();
    return value.isEmpty() ? fallback : value;
}

QString initials(const QString &name)
{
    const QStringList words = name.split(QChar(' '), Qt::SkipEmptyParts);
    QString result;
    for (const QString &word : words) {
        if (!word.isEmpty()) {
            result += word.front().toUpper();
        }
        if (result.size() == 2) {
            break;
        }
    }
    return result.isEmpty() ? QStringLiteral("?") : result;
}

QLabel *plainLabel(const QString &text, QWidget *parent, const QString &objectName = QString())
{
    auto *label = new QLabel(text, parent);
    label->setTextFormat(Qt::PlainText);
    if (!objectName.isEmpty()) {
        label->setObjectName(objectName);
    }
    return label;
}
}

StoryIntelligenceWidget::StoryIntelligenceWidget(QWidget *parent)
    : QWidget(parent)
    , m_collapseButton(new QToolButton(this))
    , m_modelSettingsButton(new QPushButton(tr("API Key"), this))
    , m_providerLabel(new QLabel(this))
    , m_modelLabel(new QLabel(this))
    , m_keyStateLabel(new QLabel(this))
    , m_projectPathLabel(new QLabel(this))
    , m_settingLabel(new QLabel(this))
    , m_goalLabel(new QLabel(this))
    , m_contextDetailLabel(new QLabel(this))
    , m_charactersContainer(new QWidget(this))
    , m_charactersLayout(new QVBoxLayout(m_charactersContainer))
    , m_annotationsSection(new QWidget(this))
    , m_annotationsContainer(new QWidget(m_annotationsSection))
    , m_annotationsLayout(new QVBoxLayout(m_annotationsContainer))
    , m_chatContainer(new QWidget(this))
    , m_chatLayout(new QVBoxLayout(m_chatContainer))
    , m_chatScrollArea(new QScrollArea(this))
    , m_chatInput(new QPlainTextEdit(this))
    , m_sendButton(new QPushButton(tr("Send"), this))
    , m_statusLabel(new QLabel(this))
{
    setObjectName(QStringLiteral("storyIntelligenceWidget"));
    setMinimumWidth(MinimumStoryPaneWidth);
    setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Expanding);

    for (QLabel *label : {
             m_providerLabel,
             m_modelLabel,
             m_keyStateLabel,
             m_projectPathLabel,
             m_settingLabel,
             m_goalLabel,
             m_contextDetailLabel,
             m_statusLabel,
         }) {
        label->setTextFormat(Qt::PlainText);
    }

    // Theme-driven via the app-wide widgets.qss (Story Intelligence section
    // there): the rail inherits the same ChromeColors surfaces as the left
    // prose sidebar, so it follows every theme/dark-mode switch with no
    // local wiring. Do NOT add a widget-local setStyleSheet here; it would
    // shadow the app stylesheet and freeze the rail in the default system
    // palette (white cards on white on stock Windows).

    auto *root = new QVBoxLayout(this);
    root->setContentsMargins(0, 0, 0, 0);
    root->setSpacing(0);

    auto *header = new QFrame(this);
    header->setObjectName(QStringLiteral("storyIntelligenceHeaderSurface"));
    auto *headerLayout = new QHBoxLayout(header);
    headerLayout->setContentsMargins(16, 14, 16, 14);
    headerLayout->setSpacing(8);
    auto *sparkle = plainLabel(QString(), header);
    sparkle->setPixmap(QIcon(QStringLiteral(":/icons/shell-sparkles.svg")).pixmap(16, 16));
    headerLayout->addWidget(sparkle);
    auto *title = plainLabel(tr("Story Intelligence"), header, QStringLiteral("storyIntelligenceTitle"));
    headerLayout->addWidget(title);
    headerLayout->addStretch(1);
    m_collapseButton->setObjectName(QStringLiteral("storyIntelligenceCollapseButton"));
    m_collapseButton->setToolTip(tr("Hide Story Intelligence"));
    m_collapseButton->setAutoRaise(true);
    m_collapseButton->setFocusPolicy(Qt::NoFocus);
    headerLayout->addWidget(m_collapseButton);
    root->addWidget(header);

    auto *contextTabs = new QTabWidget(this);
    contextTabs->setObjectName(QStringLiteral("storyIntelligenceContextTabs"));
    contextTabs->setAccessibleName(tr("Story settings"));
    contextTabs->setAttribute(Qt::WA_StyledBackground, true);
    contextTabs->setMinimumHeight(210);
    contextTabs->tabBar()->setExpanding(true);
    contextTabs->setUsesScrollButtons(false);
    auto *bodyScroll = new QScrollArea(contextTabs);
    bodyScroll->setObjectName(QStringLiteral("storyIntelligenceScrollArea"));
    bodyScroll->setWidgetResizable(true);
    bodyScroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    bodyScroll->setFrameShape(QFrame::NoFrame);
    auto *body = new QWidget(bodyScroll);
    body->setObjectName(QStringLiteral("storyIntelligenceContent"));
    body->setMinimumWidth(0);
    body->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Preferred);
    auto *bodyLayout = new QVBoxLayout(body);
    bodyLayout->setContentsMargins(16, 16, 16, 16);
    bodyLayout->setSpacing(16);

    auto *configuration = makeCard(QStringLiteral("storyIntelligenceSetupSurface"));
    auto *configurationLayout = new QVBoxLayout(configuration);
    configurationLayout->setContentsMargins(12, 12, 12, 12);
    configurationLayout->setSpacing(6);

    auto *providerRow = new QHBoxLayout;
    auto *providerCaption = plainLabel(tr("Model"), configuration, QStringLiteral("storyIntelligenceCaption"));
    providerRow->addWidget(providerCaption);
    providerRow->addStretch(1);
    m_modelSettingsButton->setObjectName(QStringLiteral("storyIntelligenceApiKeyButton"));
    m_modelSettingsButton->setFlat(true);
    m_routingSettingsButton = new QPushButton(tr("Routing…"), configuration);
    m_routingSettingsButton->setObjectName(QStringLiteral("storyIntelligenceApiKeyButton"));
    m_routingSettingsButton->setFlat(true);
    m_routingSettingsButton->setToolTip(tr("Configure task-aware Story Intelligence model routing, quality, and privacy"));
    providerRow->addWidget(m_routingSettingsButton);
    providerRow->addWidget(m_modelSettingsButton);
    configurationLayout->addLayout(providerRow);

    m_providerLabel->setObjectName(QStringLiteral("storyIntelligenceProviderLabel"));
    m_modelLabel->setObjectName(QStringLiteral("storyIntelligenceModelLabel"));
    m_providerLabel->setWordWrap(true);
    m_modelLabel->setWordWrap(true);
    m_keyStateLabel->setWordWrap(true);
    m_keyStateLabel->setObjectName(QStringLiteral("storyIntelligenceKeyState"));
    for (QLabel *label : {m_providerLabel, m_modelLabel, m_keyStateLabel, m_projectPathLabel}) {
        label->setMinimumWidth(0);
        label->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Preferred);
    }
    m_projectPathLabel->setWordWrap(true);
    m_providerLabel->setText(tr("Provider: not configured"));
    m_modelLabel->setText(tr("Model: —"));
    m_keyStateLabel->setText(tr("Secure key not loaded"));
    configurationLayout->addWidget(m_providerLabel);
    configurationLayout->addWidget(m_modelLabel);
    configurationLayout->addWidget(m_keyStateLabel);

    bodyLayout->addWidget(configuration);
    auto *project = makeCard(QStringLiteral("storyIntelligenceProjectSurface"));
    auto *projectLayout = new QVBoxLayout(project);
    projectLayout->setContentsMargins(12, 12, 12, 12);

    auto *projectRow = new QHBoxLayout;
    auto *projectLabels = new QVBoxLayout;
    auto *projectCaption = plainLabel(tr("Project folder"), project, QStringLiteral("storyIntelligenceCaption"));
    m_projectPathLabel->setObjectName(QStringLiteral("storyIntelligenceMutedLabel"));
    m_projectPathLabel->setText(tr("No project selected"));
    m_projectPathLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    projectLabels->addWidget(projectCaption);
    projectLabels->addWidget(m_projectPathLabel);
    projectRow->addLayout(projectLabels, 1);
    auto *openProject = new QPushButton(tr("Open"), project);
    openProject->setObjectName(QStringLiteral("storyIntelligencePrimaryButton"));
    projectRow->addWidget(openProject);
    projectLayout->addLayout(projectRow);
    bodyLayout->addWidget(project);

    auto *sceneSection = new QWidget(body);
    auto *sceneLayout = new QVBoxLayout(sceneSection);
    sceneLayout->setContentsMargins(0, 0, 0, 0);
    sceneLayout->setSpacing(7);
    auto *sceneHeader = new QHBoxLayout;
    auto *sceneIcon = plainLabel(QString(), sceneSection);
    sceneIcon->setPixmap(QIcon(QStringLiteral(":/icons/shell-map.svg")).pixmap(16, 16));
    sceneHeader->addWidget(sceneIcon);
    sceneHeader->addWidget(makeSectionTitle(tr("Scene Context")));
    sceneHeader->addStretch(1);
    auto *editScene = new QToolButton(sceneSection);
    editScene->setText(tr("Edit"));
    editScene->setIcon(QIcon(QStringLiteral(":/icons/shell-edit.svg")));
    editScene->setToolButtonStyle(Qt::ToolButtonIconOnly);
    editScene->setObjectName(QStringLiteral("storyIntelligenceTextButton"));
    editScene->setAutoRaise(true);
    editScene->setAccessibleName(tr("Edit scene context"));
    editScene->setToolTip(tr("Edit scene context"));
    sceneHeader->addWidget(editScene);
    sceneLayout->addLayout(sceneHeader);
    auto *sceneCard = makeCard(QStringLiteral("storyIntelligenceSceneCard"));
    auto *sceneCardLayout = new QVBoxLayout(sceneCard);
    sceneCardLayout->setContentsMargins(12, 12, 12, 12);
    sceneCardLayout->setSpacing(5);
    auto *settingCaption = plainLabel(tr("Setting"), sceneCard, QStringLiteral("storyIntelligenceCardHeading"));
    m_settingLabel->setWordWrap(true);
    m_settingLabel->setObjectName(QStringLiteral("storyIntelligenceCardBody"));
    auto *goalCaption = plainLabel(tr("Current Goal"), sceneCard, QStringLiteral("storyIntelligenceCardHeading"));
    m_goalLabel->setWordWrap(true);
    m_goalLabel->setObjectName(QStringLiteral("storyIntelligenceCardBody"));
    m_contextDetailLabel->setWordWrap(true);
    m_contextDetailLabel->setObjectName(QStringLiteral("storyIntelligenceMutedLabel"));
    sceneCardLayout->addWidget(settingCaption);
    sceneCardLayout->addWidget(m_settingLabel);
    sceneCardLayout->addSpacing(5);
    sceneCardLayout->addWidget(goalCaption);
    sceneCardLayout->addWidget(m_goalLabel);
    sceneCardLayout->addWidget(m_contextDetailLabel);
    sceneLayout->addWidget(sceneCard);
    sceneSection->setObjectName(QStringLiteral("storyIntelligenceContextPage"));
    sceneLayout->setContentsMargins(16, 12, 16, 12);

    auto *charactersSection = new QWidget(body);
    auto *charactersOuter = new QVBoxLayout(charactersSection);
    charactersOuter->setContentsMargins(0, 0, 0, 0);
    charactersOuter->setSpacing(7);
    auto *charactersHeader = new QHBoxLayout;
    auto *charactersIcon = plainLabel(QString(), charactersSection);
    charactersIcon->setPixmap(QIcon(QStringLiteral(":/icons/shell-users.svg")).pixmap(16, 16));
    charactersHeader->addWidget(charactersIcon);
    charactersHeader->addWidget(makeSectionTitle(tr("Characters")));
    charactersHeader->addStretch(1);
    auto *editCharacters = new QToolButton(charactersSection);
    editCharacters->setText(tr("Edit"));
    editCharacters->setIcon(QIcon(QStringLiteral(":/icons/shell-edit.svg")));
    editCharacters->setToolButtonStyle(Qt::ToolButtonIconOnly);
    editCharacters->setObjectName(QStringLiteral("storyIntelligenceTextButton"));
    editCharacters->setAutoRaise(true);
    editCharacters->setAccessibleName(tr("Edit characters"));
    editCharacters->setToolTip(tr("Edit characters"));
    auto *addCharacter = new QToolButton(charactersSection);
    addCharacter->setText(QStringLiteral("+"));
    addCharacter->setObjectName(QStringLiteral("storyIntelligenceTextButton"));
    addCharacter->setAutoRaise(true);
    addCharacter->setAccessibleName(tr("Add character"));
    addCharacter->setToolTip(tr("Add character"));
    charactersHeader->addWidget(editCharacters);
    charactersHeader->addWidget(addCharacter);
    charactersOuter->addLayout(charactersHeader);
    m_charactersLayout->setContentsMargins(0, 0, 0, 0);
    m_charactersLayout->setSpacing(7);
    charactersOuter->addWidget(m_charactersContainer);
    charactersSection->setObjectName(QStringLiteral("storyIntelligenceContextPage"));
    charactersOuter->setContentsMargins(16, 12, 16, 12);
    charactersOuter->addStretch(1);

    auto *projectSection = new QWidget(body);
    projectSection->setObjectName(QStringLiteral("storyIntelligenceContextPage"));
    auto *projectOuter = new QVBoxLayout(projectSection);
    projectOuter->setContentsMargins(16, 12, 16, 12);
    projectOuter->setSpacing(8);
    projectOuter->addWidget(makeSectionTitle(tr("Project Understanding")));
    auto *projectUnderstandingCard = makeCard(QStringLiteral("storyIntelligenceSceneCard"));
    auto *projectUnderstandingLayout = new QVBoxLayout(projectUnderstandingCard);
    projectUnderstandingLayout->setContentsMargins(12, 12, 12, 12);
    projectUnderstandingLayout->setSpacing(7);
    m_projectUnderstandingLabel = plainLabel(tr("Choose a project folder to let ThothPad compile its story sources."),
                                             projectUnderstandingCard,
                                             QStringLiteral("storyProjectUnderstandingSummary"));
    m_projectUnderstandingLabel->setWordWrap(true);
    m_projectRolesLabel = plainLabel(QString(), projectUnderstandingCard, QStringLiteral("storyIntelligenceMutedLabel"));
    m_projectRolesLabel->setWordWrap(true);
    m_projectRolesLabel->setVisible(false);
    m_projectReviewButton = new QPushButton(tr("Review understanding"), projectUnderstandingCard);
    m_projectReviewButton->setObjectName(QStringLiteral("storyIntelligencePrimaryButton"));
    m_projectReviewButton->setEnabled(false);
    m_manuscriptOrderButton = new QPushButton(tr("Manuscript order…"), projectUnderstandingCard);
    m_manuscriptOrderButton->setObjectName(QStringLiteral("storyIntelligencePrimaryButton"));
    m_manuscriptOrderButton->setEnabled(false);
    m_storyLabButton = new QPushButton(tr("Open Story Lab…"), projectUnderstandingCard);
    m_storyLabButton->setObjectName(QStringLiteral("storyIntelligencePrimaryButton"));
    m_storyLabButton->setEnabled(false);
    projectUnderstandingLayout->addWidget(m_projectUnderstandingLabel);
    projectUnderstandingLayout->addWidget(m_projectRolesLabel);
    auto *projectActions = new QHBoxLayout;
    projectActions->addWidget(m_projectReviewButton);
    projectActions->addWidget(m_manuscriptOrderButton);
    projectActions->addWidget(m_storyLabButton);
    projectActions->addStretch(1);
    projectUnderstandingLayout->addLayout(projectActions);
    projectOuter->addWidget(projectUnderstandingCard);

    auto *branchCard = makeCard(QStringLiteral("storyIntelligenceSceneCard"));
    auto *branchLayout = new QVBoxLayout(branchCard);
    branchLayout->setContentsMargins(12, 12, 12, 12);
    branchLayout->setSpacing(7);
    branchLayout->addWidget(plainLabel(tr("Story branch"), branchCard, QStringLiteral("storyIntelligenceCardHeading")));
    m_branchCombo = new QComboBox(branchCard);
    m_branchCombo->setObjectName(QStringLiteral("storyBranchCombo"));
    m_branchCombo->addItem(tr("Mainline"), QStringLiteral("mainline"));
    m_branchCombo->setToolTip(tr("Choose the story continuity Story Intelligence may reason inside"));
    branchLayout->addWidget(m_branchCombo);
    auto *branchActions = new QHBoxLayout;
    m_branchCreateButton = new QPushButton(tr("New alternate…"), branchCard);
    m_branchCreateButton->setObjectName(QStringLiteral("storyIntelligencePrimaryButton"));
    m_branchCreateButton->setEnabled(false);
    m_branchDetailsButton = new QPushButton(tr("Details…"), branchCard);
    m_branchDetailsButton->setObjectName(QStringLiteral("storyIntelligencePrimaryButton"));
    m_branchDetailsButton->setEnabled(false);
    branchActions->addWidget(m_branchCreateButton);
    branchActions->addWidget(m_branchDetailsButton);
    branchActions->addStretch(1);
    branchLayout->addLayout(branchActions);
    auto *branchHelp = plainLabel(tr("Alternate branches are isolated overlays. Branch-only facts never become Mainline unless you explicitly merge them."),
                                  branchCard,
                                  QStringLiteral("storyIntelligenceMutedLabel"));
    branchHelp->setWordWrap(true);
    branchLayout->addWidget(branchHelp);
    projectOuter->addWidget(branchCard);

    auto *projectHelp = plainLabel(tr("Folder names are hints, never authority. Corrections here become writer-owned project rules."),
                                   projectSection,
                                   QStringLiteral("storyIntelligenceMutedLabel"));
    projectHelp->setWordWrap(true);
    projectOuter->addWidget(projectHelp);
    projectOuter->addStretch(1);

    auto *contextSection = new QWidget(body);
    contextSection->setObjectName(QStringLiteral("storyIntelligenceContextPage"));
    auto *contextOuter = new QVBoxLayout(contextSection);
    contextOuter->setContentsMargins(16, 12, 16, 12);
    contextOuter->setSpacing(8);
    contextOuter->addWidget(makeSectionTitle(tr("What the AI sees")));
    auto *modeCard = makeCard(QStringLiteral("storyIntelligenceSceneCard"));
    auto *modeLayout = new QVBoxLayout(modeCard);
    modeLayout->setContentsMargins(12, 12, 12, 12);
    modeLayout->setSpacing(6);
    modeLayout->addWidget(plainLabel(tr("Epistemic perspective"), modeCard, QStringLiteral("storyIntelligenceCardHeading")));
    m_epistemicCombo = new QComboBox(modeCard);
    m_epistemicCombo->setObjectName(QStringLiteral("storyEpistemicModeCombo"));
    m_epistemicCombo->addItem(tr("Author omniscient"), QStringLiteral("author_omniscient"));
    m_epistemicCombo->addItem(tr("Current POV"), QStringLiteral("current_pov"));
    m_epistemicCombo->addItem(tr("Active character"), QStringLiteral("character"));
    m_epistemicCombo->addItem(tr("Reader at this point"), QStringLiteral("reader"));
    m_epistemicCombo->addItem(tr("Cold reader"), QStringLiteral("cold_reader"));
    m_epistemicCombo->addItem(tr("Manuscript only"), QStringLiteral("manuscript_only"));
    m_epistemicCombo->addItem(tr("World/reference only"), QStringLiteral("world_reference_only"));
    modeLayout->addWidget(m_epistemicCombo);
    contextOuter->addWidget(modeCard);
    auto *contextCard = makeCard(QStringLiteral("storyIntelligenceSceneCard"));
    auto *contextCardLayout = new QVBoxLayout(contextCard);
    contextCardLayout->setContentsMargins(12, 12, 12, 12);
    contextCardLayout->setSpacing(6);
    m_contextInspectorLabel = plainLabel(tr("Send a Story Intelligence message to inspect the compiled context used for that response."),
                                         contextCard,
                                         QStringLiteral("storyContextInspectorSummary"));
    m_contextInspectorLabel->setWordWrap(true);
    m_contextSourcesLabel = plainLabel(QString(), contextCard, QStringLiteral("storyIntelligenceMutedLabel"));
    m_contextSourcesLabel->setWordWrap(true);
    m_contextSourcesLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    m_contextSourcesLabel->setVisible(false);
    contextCardLayout->addWidget(m_contextInspectorLabel);
    contextCardLayout->addWidget(m_contextSourcesLabel);
    contextOuter->addWidget(contextCard);
    contextOuter->addStretch(1);

    auto *annotationsOuter = new QVBoxLayout(m_annotationsSection);
    annotationsOuter->setContentsMargins(0, 0, 0, 0);
    annotationsOuter->setSpacing(7);
    annotationsOuter->addWidget(makeSectionTitle(tr("⌄  Manuscript Marks")));
    m_annotationsLayout->setContentsMargins(0, 0, 0, 0);
    m_annotationsLayout->setSpacing(7);
    annotationsOuter->addWidget(m_annotationsContainer);
    m_annotationsSection->setVisible(false);
    sceneLayout->addWidget(m_annotationsSection);
    sceneLayout->addStretch(1);

    bodyLayout->addStretch(1);
    bodyScroll->setWidget(body);
    contextTabs->addTab(bodyScroll, tr("Model"));
    const auto addContextTab = [contextTabs](QWidget *page, const QString &title) {
        page->setAttribute(Qt::WA_StyledBackground, true);
        auto *scroll = new QScrollArea(contextTabs);
        scroll->setObjectName(QStringLiteral("storyIntelligenceContextScroll"));
        scroll->setWidgetResizable(true);
        scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        scroll->setFrameShape(QFrame::NoFrame);
        scroll->setWidget(page);
        contextTabs->addTab(scroll, title);
    };
    addContextTab(sceneSection, tr("Scene Context"));
    addContextTab(charactersSection, tr("Characters"));
    addContextTab(projectSection, tr("Project"));
    addContextTab(contextSection, tr("AI Context"));
    auto *chatPanel = new QWidget(this);
    chatPanel->setObjectName(QStringLiteral("storyIntelligenceChatPanel"));
    chatPanel->setAttribute(Qt::WA_StyledBackground, true);
    auto *chatPanelLayout = new QVBoxLayout(chatPanel);
    chatPanelLayout->setContentsMargins(16, 10, 16, 8);
    chatPanelLayout->setSpacing(10);
    auto *chatHeadingRow = new QHBoxLayout;
    auto *chatIcon = plainLabel(QString(), body);
    chatIcon->setPixmap(QIcon(QStringLiteral(":/icons/shell-chat.svg")).pixmap(16, 16));
    chatHeadingRow->addWidget(chatIcon);
    chatHeadingRow->addWidget(makeSectionTitle(tr("Co-Writer Chat")));
    chatHeadingRow->addStretch(1);
    auto *clearMarks = new QToolButton(body);
    clearMarks->setText(tr("Clear marks"));
    clearMarks->setObjectName(QStringLiteral("storyIntelligenceTextButton"));
    clearMarks->setAutoRaise(true);
    clearMarks->setToolTip(tr("Clear AI annotations without changing manuscript text"));
    chatHeadingRow->addWidget(clearMarks);
    chatPanelLayout->addLayout(chatHeadingRow);

    auto *sessionRow = new QHBoxLayout;
    m_sessionCombo = new QComboBox(body);
    m_sessionCombo->setObjectName(QStringLiteral("storySessionCombo"));
    m_sessionCombo->setAccessibleName(tr("Saved conversations"));
    m_sessionCombo->setToolTip(tr("Reopen a conversation with its own chapter, agent and history"));
    m_sessionCombo->setSizeAdjustPolicy(QComboBox::AdjustToMinimumContentsLengthWithIcon);
    m_sessionCombo->setMinimumContentsLength(8);
    sessionRow->addWidget(m_sessionCombo, 1);
    m_newSessionButton = new QToolButton(body);
    m_newSessionButton->setObjectName(QStringLiteral("storyNewSessionButton"));
    m_newSessionButton->setText(tr("New"));
    m_newSessionButton->setAccessibleName(tr("New conversation"));
    m_newSessionButton->setToolTip(tr("Start another conversation with the current agent and scope"));
    sessionRow->addWidget(m_newSessionButton);
    m_deleteSessionButton = new QToolButton(body);
    m_deleteSessionButton->setObjectName(QStringLiteral("storyDeleteSessionButton"));
    m_deleteSessionButton->setIcon(QIcon(QStringLiteral(":/icons/shell-trash.svg")));
    m_deleteSessionButton->setAccessibleName(tr("Delete conversation"));
    m_deleteSessionButton->setToolTip(tr("Delete this saved conversation"));
    m_deleteSessionButton->setEnabled(false);
    sessionRow->addWidget(m_deleteSessionButton);
    sessionRow->setSpacing(8);
    chatPanelLayout->addLayout(sessionRow);
    connect(m_sessionCombo, &QComboBox::activated, this, [this](int index) {
        const auto id = m_sessionCombo->itemData(index).toString();
        if (!id.isEmpty())
            emit sessionSelected(id);
    });

    m_chatScrollArea->setObjectName(QStringLiteral("storyIntelligenceChatScroll"));
    m_chatScrollArea->setWidgetResizable(true);
    m_chatScrollArea->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    m_chatScrollArea->setFrameShape(QFrame::NoFrame);
    m_chatContainer->setObjectName(QStringLiteral("storyIntelligenceChatContent"));
    m_chatLayout->setContentsMargins(0, 0, 4, 4);
    m_chatLayout->setSpacing(12);
    m_chatLayout->setSizeConstraint(QLayout::SetMinAndMaxSize);
    m_chatLayout->addStretch(1);
    m_chatScrollArea->setWidget(m_chatContainer);
    m_chatScrollArea->setMinimumHeight(100);
    chatPanelLayout->addWidget(m_chatScrollArea, 1);

    auto *sections = new QSplitter(Qt::Vertical, this);
    sections->setObjectName(QStringLiteral("storyIntelligenceSections"));
    sections->setHandleWidth(6);
    sections->addWidget(contextTabs);
    sections->addWidget(chatPanel);
    sections->setCollapsible(0, false);
    sections->setCollapsible(1, false);
    sections->setStretchFactor(0, 0);
    sections->setStretchFactor(1, 1);
    sections->setSizes({300, 500});
    sections->handle(1)->setToolTip(tr("Drag to resize context and conversation"));
    root->addWidget(sections, 1);

    auto *composer = new QFrame(this);
    composer->setObjectName(QStringLiteral("storyIntelligenceComposer"));
    auto *composerLayout = new QVBoxLayout(composer);
    composerLayout->setContentsMargins(12, 12, 12, 12);
    composerLayout->setSpacing(6);
    auto *workspaceRow = new QHBoxLayout;
    m_scopeCombo = new QComboBox(composer);
    m_scopeCombo->setObjectName(QStringLiteral("storyScopeCombo"));
    m_scopeCombo->addItem(tr("Manuscript"), QStringLiteral("manuscript"));
    m_scopeCombo->addItem(tr("Current chapter"), QStringLiteral("chapter"));
    m_scopeCombo->setSizeAdjustPolicy(QComboBox::AdjustToMinimumContentsLengthWithIcon);
    m_scopeCombo->setMinimumContentsLength(8);
    m_scopeCombo->setToolTip(tr("Context and cast follow the heading containing your cursor. Manuscript settings are inherited."));
    workspaceRow->addWidget(m_scopeCombo, 1);
    m_workspaceButton = new QToolButton(composer);
    m_workspaceButton->setText(tr("Workspace…"));
    m_workspaceButton->setObjectName(QStringLiteral("storyWorkspaceButton"));
    m_workspaceButton->setToolTip(tr("Edit agents, import Buzz souls, manage scoped memories and saved conversations"));
    composerLayout->addLayout(workspaceRow);
    m_contextLabel = plainLabel(QString(), composer, QStringLiteral("storyContextHeadingLabel"));
    m_contextLabel->setWordWrap(true);
    composerLayout->addWidget(m_contextLabel);
    m_scopeLabel = plainLabel(QString(), composer, QStringLiteral("storyIntelligenceMutedLabel"));
    m_scopeLabel->setWordWrap(true);
    auto *agentRow = new QHBoxLayout;
    agentRow->addWidget(m_scopeLabel, 1);
    agentRow->addWidget(m_workspaceButton);
    composerLayout->addLayout(agentRow);
    connect(m_workspaceButton, &QToolButton::clicked, this, &StoryIntelligenceWidget::workspaceRequested);
    connect(m_newSessionButton, &QToolButton::clicked, this, &StoryIntelligenceWidget::newSessionRequested);
    connect(m_deleteSessionButton, &QToolButton::clicked, this, [this]() {
        const QString id = m_sessionCombo->currentData().toString();
        if (!id.isEmpty())
            emit deleteSessionRequested(id);
    });
    connect(m_scopeCombo, &QComboBox::currentIndexChanged, this, [this](int) {
        emit scopeModeChanged(m_scopeCombo->currentData().toString());
    });
    m_chatInput->setObjectName(QStringLiteral("storyIntelligenceChatInput"));
    m_chatInput->setTabChangesFocus(true);
    m_chatInput->setAccessibleName(tr("Message to Story Intelligence"));
    m_chatInput->setToolTip(tr("Enter to send; Shift+Enter for a new line"));
    m_chatInput->setFixedHeight(64);
    m_chatInput->installEventFilter(this);
    auto *composerField = new QWidget(composer);
    auto *composerFieldLayout = new QGridLayout(composerField);
    composerFieldLayout->setContentsMargins(0, 0, 0, 0);
    composerFieldLayout->addWidget(m_chatInput, 0, 0);
    m_sendButton->setObjectName(QStringLiteral("storyIntelligencePrimaryButton"));
    m_sendButton->setProperty("storySend", true);
    m_sendButton->setText(QString());
    m_sendButton->setIcon(QIcon(QStringLiteral(":/icons/shell-send.svg")));
    m_sendButton->setAccessibleName(tr("Send message"));
    m_sendButton->setToolTip(tr("Send message (Enter)"));
    composerFieldLayout->addWidget(m_sendButton, 0, 0, Qt::AlignRight | Qt::AlignBottom);
    composerLayout->addWidget(composerField);
    m_statusLabel->setObjectName(QStringLiteral("storyIntelligenceStatus"));
    m_statusLabel->setText(tr("Ready"));
    m_statusLabel->setWordWrap(true);
    m_statusLabel->hide();
    composerLayout->addWidget(m_statusLabel);
    root->addWidget(composer);

    setSceneContext({});
    setCharacters({});
    setAnnotations({});

    connect(m_collapseButton, &QToolButton::clicked, this, &StoryIntelligenceWidget::collapseRequested);
    connect(m_modelSettingsButton, &QPushButton::clicked, this, &StoryIntelligenceWidget::modelSettingsRequested);
    connect(m_routingSettingsButton, &QPushButton::clicked, this, &StoryIntelligenceWidget::routingSettingsRequested);
    connect(openProject, &QPushButton::clicked, this, &StoryIntelligenceWidget::projectFolderRequested);
    connect(m_projectReviewButton, &QPushButton::clicked, this, &StoryIntelligenceWidget::projectUnderstandingReviewRequested);
    connect(m_manuscriptOrderButton, &QPushButton::clicked, this, &StoryIntelligenceWidget::manuscriptOrderRequested);
    connect(m_storyLabButton, &QPushButton::clicked, this, &StoryIntelligenceWidget::storyLabRequested);
    connect(m_branchCombo, &QComboBox::currentIndexChanged, this, [this](int index) {
        emit branchChanged(m_branchCombo->itemData(index).toString());
    });
    connect(m_branchCreateButton, &QPushButton::clicked, this, &StoryIntelligenceWidget::createBranchRequested);
    connect(m_branchDetailsButton, &QPushButton::clicked, this, &StoryIntelligenceWidget::branchDetailsRequested);
    connect(m_epistemicCombo, &QComboBox::currentIndexChanged, this, [this](int index) {
        emit epistemicModeChanged(m_epistemicCombo->itemData(index).toString());
    });
    connect(editScene, &QToolButton::clicked, this, &StoryIntelligenceWidget::editSceneRequested);
    connect(addCharacter, &QToolButton::clicked, this, &StoryIntelligenceWidget::addCharacterRequested);
    connect(editCharacters, &QToolButton::clicked, this, &StoryIntelligenceWidget::editCharactersRequested);
    connect(clearMarks, &QToolButton::clicked, this, &StoryIntelligenceWidget::clearAnnotationsRequested);
    connect(m_sendButton, &QPushButton::clicked, this, &StoryIntelligenceWidget::submitChat);

    auto *sendShortcut = new QShortcut(QKeySequence(QStringLiteral("Ctrl+Return")), m_chatInput);
    sendShortcut->setContext(Qt::WidgetWithChildrenShortcut);
    connect(sendShortcut, &QShortcut::activated, this, &StoryIntelligenceWidget::submitChat);
}

bool StoryIntelligenceWidget::eventFilter(QObject *watched, QEvent *event)
{
    if (watched == m_chatInput && event->type() == QEvent::KeyPress) {
        const auto *key = static_cast<QKeyEvent *>(event);
        if ((key->key() == Qt::Key_Return || key->key() == Qt::Key_Enter) && !(key->modifiers() & (Qt::ShiftModifier | Qt::AltModifier | Qt::MetaModifier))) {
            submitChat();
            return true;
        }
    }
    return QWidget::eventFilter(watched, event);
}

void StoryIntelligenceWidget::setCollapseIcon(const QIcon &icon)
{
    m_collapseButton->setIcon(icon);
}

QFrame *StoryIntelligenceWidget::makeCard(const QString &objectName)
{
    auto *frame = new QFrame(this);
    frame->setObjectName(objectName);
    return frame;
}

QLabel *StoryIntelligenceWidget::makeSectionTitle(const QString &text)
{
    return plainLabel(text, this, QStringLiteral("storyIntelligenceSectionTitle"));
}

void StoryIntelligenceWidget::setProviderSummary(const QString &provider, const QString &model, bool credentialConfigured, const QString &authKind)
{
    m_providerLabel->setText(tr("Provider: %1").arg(provider.isEmpty() ? tr("not configured") : provider));
    m_modelLabel->setText(tr("Model: %1").arg(model.isEmpty() ? QStringLiteral("—") : model));
    m_keyStateLabel->setText(credentialConfigured ? tr("🔒 Secure key configured") : tr("No stored key · local/keyless providers still work"));
    if (authKind == QStringLiteral("codex")) {
        m_keyStateLabel->setText(tr("ChatGPT sign-in is managed by Codex · connect in Model Settings"));
    } else if (authKind == QStringLiteral("gemini_oauth")) {
        m_keyStateLabel->setText(credentialConfigured ? tr("🔒 Secure Google sign-in configured") : tr("Connect Google in Model Settings"));
    } else if (authKind == QStringLiteral("openrouter") && !credentialConfigured) {
        m_keyStateLabel->setText(tr("OpenRouter key required, including :free models · connect in Model Settings"));
    } else if (authKind == QStringLiteral("opencode_go") && !credentialConfigured) {
        m_keyStateLabel->setText(tr("OpenCode key and Go subscription required · connect in Model Settings"));
    } else if (authKind == QStringLiteral("opencode_zen") && !credentialConfigured) {
        m_keyStateLabel->setText(tr("OpenCode key required · connect in Model Settings"));
    }
}

void StoryIntelligenceWidget::setProjectFolder(const QString &path)
{
    if (path.isEmpty()) {
        m_projectPathLabel->setText(tr("No project selected"));
        m_projectPathLabel->setToolTip(QString());
        if (m_projectUnderstandingLabel) {
            m_projectUnderstandingLabel->setText(tr("Choose a project folder to let ThothPad compile its story sources."));
        }
        if (m_projectRolesLabel) {
            m_projectRolesLabel->clear();
            m_projectRolesLabel->setVisible(false);
        }
        if (m_projectReviewButton) {
            m_projectReviewButton->setEnabled(false);
        }
        if (m_manuscriptOrderButton) {
            m_manuscriptOrderButton->setEnabled(false);
        }
        if (m_branchCreateButton) {
            m_branchCreateButton->setEnabled(false);
        }
        if (m_branchDetailsButton) {
            m_branchDetailsButton->setEnabled(false);
        }
        setBranches({}, QStringLiteral("mainline"));
        return;
    }
    const QString normalized = QDir::toNativeSeparators(path);
    m_projectPathLabel->setText(normalized.length() > 34 ? QStringLiteral("…") + normalized.right(33) : normalized);
    m_projectPathLabel->setToolTip(normalized);
}

void StoryIntelligenceWidget::setProjectUnderstanding(const QJsonObject &understanding)
{
    if (!m_projectUnderstandingLabel || !m_projectRolesLabel || !m_projectReviewButton || !m_manuscriptOrderButton || !m_storyLabButton) {
        return;
    }
    if (understanding.isEmpty()) {
        m_projectUnderstandingLabel->setText(tr("Project understanding is not available yet."));
        m_projectRolesLabel->clear();
        m_projectRolesLabel->setVisible(false);
        m_projectReviewButton->setEnabled(false);
        m_manuscriptOrderButton->setEnabled(false);
        m_storyLabButton->setEnabled(false);
        return;
    }

    const int sources = understanding.value(QStringLiteral("source_count")).toInt();
    const int entities = understanding.value(QStringLiteral("entity_count")).toInt();
    const int conflicts = understanding.value(QStringLiteral("open_conflict_count")).toInt();
    m_projectUnderstandingLabel->setText(tr("%1 sources · %2 entities · %3 open conflicts").arg(sources).arg(entities).arg(conflicts));

    const QJsonObject roles = understanding.value(QStringLiteral("role_counts")).toObject();
    QStringList lines;
    for (auto iterator = roles.constBegin(); iterator != roles.constEnd(); ++iterator) {
        QString label = iterator.key();
        label.replace(QChar('_'), QChar(' '));
        if (!label.isEmpty()) {
            label[0] = label.at(0).toUpper();
        }
        lines << tr("%1: %2").arg(label).arg(iterator.value().toInt());
    }
    m_projectRolesLabel->setText(lines.join(QStringLiteral(" · ")));
    m_projectRolesLabel->setVisible(!lines.isEmpty());
    m_projectReviewButton->setEnabled(sources > 0);
    m_manuscriptOrderButton->setEnabled(roles.value(QStringLiteral("manuscript")).toInt() > 0);
    m_storyLabButton->setEnabled(sources > 0);
    if (m_branchCreateButton) {
        m_branchCreateButton->setEnabled(sources > 0);
    }
}

void StoryIntelligenceWidget::setBranches(const QJsonArray &branches, const QString &activeBranch)
{
    if (!m_branchCombo || !m_branchDetailsButton) {
        return;
    }
    const QSignalBlocker blocker(m_branchCombo);
    m_branchCombo->clear();
    m_branchCombo->addItem(tr("Mainline"), QStringLiteral("mainline"));
    for (const QJsonValue &value : branches) {
        const QJsonObject branch = value.toObject();
        const QString id = branch.value(QStringLiteral("branch_id")).toString();
        if (id.isEmpty() || id == QStringLiteral("mainline")) {
            continue;
        }
        const QString status = branch.value(QStringLiteral("status")).toString();
        const bool stale = branch.value(QStringLiteral("freshness")).toObject().value(QStringLiteral("stale")).toBool();
        if (status == QStringLiteral("DISCARDED")) {
            continue;
        }
        QString label = id;
        if (stale || status == QStringLiteral("STALE_NEEDS_REBASE")) {
            label += tr(" · stale");
        } else if (status == QStringLiteral("MERGED")) {
            label += tr(" · merged");
        } else if (status == QStringLiteral("DISCARDED")) {
            label += tr(" · discarded");
        }
        m_branchCombo->addItem(label, id);
    }
    const QString selected = activeBranch.isEmpty() ? QStringLiteral("mainline") : activeBranch;
    int index = m_branchCombo->findData(selected);
    if (index < 0) {
        index = 0;
    }
    m_branchCombo->setCurrentIndex(index);
    m_branchDetailsButton->setEnabled(m_branchCombo->currentData().toString() != QStringLiteral("mainline"));
}

void StoryIntelligenceWidget::setContextInspector(const QJsonObject &inspector)
{
    if (!m_contextInspectorLabel || !m_contextSourcesLabel) {
        return;
    }
    if (inspector.isEmpty()) {
        m_contextInspectorLabel->setText(tr("Send a Story Intelligence message to inspect the compiled context used for that response."));
        m_contextSourcesLabel->clear();
        m_contextSourcesLabel->setVisible(false);
        return;
    }

    QString mode = inspector.value(QStringLiteral("mode")).toString();
    mode.replace(QChar('_'), QChar(' '));
    const QJsonObject budget = inspector.value(QStringLiteral("budget")).toObject();
    const int used = budget.value(QStringLiteral("used_chars")).toInt();
    const int maximum = budget.value(QStringLiteral("maximum_chars")).toInt();
    const QJsonArray included = inspector.value(QStringLiteral("included")).toArray();
    const QJsonArray excluded = inspector.value(QStringLiteral("excluded")).toArray();
    QString summary = tr("%1 · %2 / %3 characters · %4 sources included · %5 excluded")
                          .arg(mode.isEmpty() ? tr("Context") : mode)
                          .arg(used)
                          .arg(maximum)
                          .arg(included.size())
                          .arg(excluded.size());
    const QString activeBranch =
        inspector.value(QStringLiteral("active_branch")).toString(inspector.value(QStringLiteral("branch_id")).toString(QStringLiteral("mainline")));
    const int branchOverlays = inspector.value(QStringLiteral("branch_overlay_count")).toInt();
    if (activeBranch != QStringLiteral("mainline")) {
        summary += tr("\nBranch: %1 · %2 branch-only change(s)").arg(activeBranch).arg(branchOverlays);
    }
    const QJsonObject boundary = inspector.value(QStringLiteral("epistemic_boundary")).toObject();
    if (boundary.value(QStringLiteral("position_bounded")).toBool()) {
        const QString order = boundary.value(QStringLiteral("cross_file_order")).toString();
        if (order == QStringLiteral("writer_owned")) {
            summary += tr("\nBounded at active story position · writer-owned manuscript order");
        } else if (order == QStringLiteral("unresolved")) {
            summary += tr("\nBounded at active story position · other manuscript files withheld until Manuscript Order is set");
        } else {
            summary += tr("\nBounded at active story position");
        }
    }
    if (inspector.value(QStringLiteral("current_document_masked")).toBool()) {
        summary += tr(" · later current-document text withheld");
    }
    m_contextInspectorLabel->setText(summary);

    QStringList sourceLines;
    for (const QJsonValue &value : included) {
        const QJsonObject record = value.toObject();
        const QString path = record.value(QStringLiteral("path")).toString();
        if (path.isEmpty()) {
            continue;
        }
        const QString authority = record.value(QStringLiteral("authority")).toString();
        const QString reason = record.value(QStringLiteral("reason")).toString();
        sourceLines << QStringLiteral("✓ %1\n  %2 · %3").arg(path, authority, reason);
        if (sourceLines.size() >= 12) {
            break;
        }
    }
    if (included.size() > sourceLines.size()) {
        sourceLines << tr("…and %1 more included sources").arg(included.size() - sourceLines.size());
    }
    int excludedShown = 0;
    for (const QJsonValue &value : excluded) {
        const QJsonObject record = value.toObject();
        const QString path = record.value(QStringLiteral("path")).toString();
        const QString reason = record.value(QStringLiteral("reason")).toString();
        if (path.isEmpty() || reason.isEmpty()) {
            continue;
        }
        sourceLines << QStringLiteral("× %1\n  %2").arg(path, reason);
        if (++excludedShown >= 4) {
            break;
        }
    }
    m_contextSourcesLabel->setText(sourceLines.join(QChar('\n')));
    m_contextSourcesLabel->setVisible(!sourceLines.isEmpty());
}

void StoryIntelligenceWidget::setSceneContext(const QJsonObject &context)
{
    m_settingLabel->setText(displayValue(context, QStringLiteral("setting"), tr("No setting recorded yet.")));
    m_goalLabel->setText(displayValue(context, QStringLiteral("goal"), tr("No current goal recorded yet.")));
    QStringList details;
    const QString pov = context.value(QStringLiteral("pov")).toString().trimmed();
    const QString location = context.value(QStringLiteral("location")).toString().trimmed();
    const QString time = context.value(QStringLiteral("time")).toString().trimmed();
    const QString conflict = context.value(QStringLiteral("conflict")).toString().trimmed();
    const QString notes = context.value(QStringLiteral("notes")).toString().trimmed();
    if (!pov.isEmpty()) {
        details << tr("POV: %1").arg(pov);
    }
    if (!location.isEmpty()) {
        details << tr("Location: %1").arg(location);
    }
    if (!time.isEmpty()) {
        details << tr("Time: %1").arg(time);
    }
    if (!conflict.isEmpty()) {
        details << tr("Conflict: %1").arg(conflict);
    }
    if (!notes.isEmpty()) {
        details << tr("Notes: %1").arg(notes);
    }
    m_contextDetailLabel->setText(details.join(QChar('\n')));
    m_contextDetailLabel->setVisible(!details.isEmpty());
}

QString StoryIntelligenceWidget::characterId(const QJsonObject &character) const
{
    QString id = character.value(QStringLiteral("id")).toString().trimmed();
    if (id.isEmpty()) {
        id = character.value(QStringLiteral("name")).toString().trimmed().toCaseFolded();
        id.replace(QRegularExpression(QStringLiteral("[^a-z0-9]+")), QStringLiteral("-"));
    }
    return id;
}

void StoryIntelligenceWidget::setCharacters(const QJsonArray &characters)
{
    if (m_characters == characters && m_charactersLayout->count() > 0)
        return;
    m_characters = characters;
    rebuildCharacters();
}

void StoryIntelligenceWidget::setActiveCharacter(const QString &characterId)
{
    if (m_activeCharacterId == characterId)
        return;
    m_activeCharacterId = characterId;
    rebuildCharacters();
}

QString StoryIntelligenceWidget::activeCharacterId() const
{
    return m_activeCharacterId;
}

void StoryIntelligenceWidget::rebuildCharacters()
{
    while (QLayoutItem *item = m_charactersLayout->takeAt(0)) {
        if (QWidget *widget = item->widget()) {
            widget->deleteLater();
        }
        delete item;
    }

    if (m_characters.isEmpty()) {
        auto *empty = plainLabel(tr("No characters yet. Add one to ground voice and knowledge checks."),
                                 m_charactersContainer,
                                 QStringLiteral("storyIntelligenceMutedLabel"));
        empty->setWordWrap(true);
        m_charactersLayout->addWidget(empty);
        return;
    }

    for (const QJsonValue value : m_characters) {
        if (!value.isObject()) {
            continue;
        }
        const QJsonObject character = value.toObject();
        const QString name = character.value(QStringLiteral("name")).toString().trimmed();
        if (name.isEmpty()) {
            continue;
        }
        const QString id = characterId(character);
        const QString role = character.value(QStringLiteral("role")).toString().trimmed();
        auto *button = new QPushButton(m_charactersContainer);
        button->setObjectName(QStringLiteral("storyIntelligenceCharacterCard"));
        button->setCheckable(true);
        button->setChecked(!id.isEmpty() && id == m_activeCharacterId);
        const QString subtitle = role.isEmpty() ? tr("Character simulation") : role;
        button->setAccessibleName(name + QStringLiteral(" — ") + subtitle);
        auto *cardLayout = new QHBoxLayout(button);
        cardLayout->setContentsMargins(10, 8, 10, 8);
        cardLayout->setSpacing(10);
        auto *avatar = plainLabel(initials(name), button, QStringLiteral("storyIntelligenceAvatar"));
        avatar->setFixedSize(28, 28);
        avatar->setAlignment(Qt::AlignCenter);
        avatar->setAttribute(Qt::WA_TransparentForMouseEvents);
        cardLayout->addWidget(avatar);
        auto *captions = new QVBoxLayout;
        captions->setSpacing(2);
        for (auto *label : {plainLabel(name, button), plainLabel(subtitle, button, QStringLiteral("storyIntelligenceMutedLabel"))}) {
            label->setWordWrap(true);
            label->setMinimumWidth(0);
            label->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Preferred);
            label->setAttribute(Qt::WA_TransparentForMouseEvents);
            captions->addWidget(label);
        }
        cardLayout->addLayout(captions, 1);
        button->setToolTip(tr("Use %1 as the active simulated character for new chat messages. Click again to return to the co-writer.").arg(name));
        button->setProperty("characterId", id);
        connect(button, &QPushButton::clicked, this, [this, id](bool checked) {
            m_activeCharacterId = checked ? id : QString();
            rebuildCharacters();
            emit characterActivated(m_activeCharacterId);
        });
        m_charactersLayout->addWidget(button);
    }
}

void StoryIntelligenceWidget::setAnnotations(const QJsonArray &annotations)
{
    if (m_annotations == annotations && m_annotationsLayout->count() > 0)
        return;
    m_annotations = annotations;
    rebuildAnnotations();
}

void StoryIntelligenceWidget::rebuildAnnotations()
{
    while (QLayoutItem *item = m_annotationsLayout->takeAt(0)) {
        if (QWidget *widget = item->widget()) {
            widget->deleteLater();
        }
        delete item;
    }
    m_annotationsSection->setVisible(!m_annotations.isEmpty());
    for (const QJsonValue value : m_annotations) {
        if (!value.isObject()) {
            continue;
        }
        const QJsonObject annotation = value.toObject();
        const QString id = annotation.value(QStringLiteral("id")).toString();
        const QString category = annotation.value(QStringLiteral("category")).toString();
        const QString comment = annotation.value(QStringLiteral("comment")).toString();
        const QString quote = annotation.value(QStringLiteral("quote")).toString();
        const QString replacement = annotation.value(QStringLiteral("replacement")).toString();
        const int start = annotation.value(QStringLiteral("start_utf16")).toInt(-1);
        const int end = annotation.value(QStringLiteral("end_utf16")).toInt(-1);
        if (id.isEmpty() || quote.isEmpty() || start < 0 || end <= start) {
            continue;
        }
        auto *card = new QFrame(m_annotationsContainer);
        card->setObjectName(QStringLiteral("storyIntelligenceSuggestionCard"));
        auto *layout = new QVBoxLayout(card);
        layout->setContentsMargins(9, 8, 9, 8);
        layout->setSpacing(5);
        auto *heading = plainLabel(category.isEmpty() ? tr("Observation") : category.toUpper(), card, QStringLiteral("storyIntelligenceCardHeading"));
        auto *commentLabel = plainLabel(comment, card);
        commentLabel->setWordWrap(true);
        auto *quoteLabel = plainLabel(tr("“%1”").arg(quote), card, QStringLiteral("storyIntelligenceSuggestionQuote"));
        quoteLabel->setWordWrap(true);
        layout->addWidget(heading);
        if (!comment.isEmpty()) {
            layout->addWidget(commentLabel);
        }
        layout->addWidget(quoteLabel);
        if (!replacement.isEmpty()) {
            auto *replacementLabel = plainLabel(replacement, card, QStringLiteral("storyIntelligenceSuggestionReplacement"));
            replacementLabel->setWordWrap(true);
            replacementLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
            layout->addWidget(replacementLabel);
        }
        auto *actions = new QHBoxLayout;
        auto *goTo = new QPushButton(tr("Go to"), card);
        goTo->setObjectName(QStringLiteral("storyIntelligencePrimaryButton"));
        goTo->setToolTip(tr("Select this marked passage in the manuscript"));
        goTo->setEnabled(!annotation.value(QStringLiteral("stale")).toBool());
        if (annotation.value(QStringLiteral("stale")).toBool())
            heading->setText(tr("STALE · manuscript changed"));
        actions->addWidget(goTo);
        connect(goTo, &QPushButton::clicked, this, [this, start, end, quote]() {
            emit annotationNavigationRequested(start, end, quote);
        });
        actions->addStretch(1);
        auto *dismiss = new QPushButton(tr("Dismiss"), card);
        dismiss->setObjectName(QStringLiteral("storyIntelligencePrimaryButton"));
        actions->addWidget(dismiss);
        connect(dismiss, &QPushButton::clicked, this, [this, id]() {
            emit dismissSuggestionRequested(id);
        });
        if (!replacement.isEmpty()) {
            auto *apply = new QPushButton(tr("Apply"), card);
            apply->setEnabled(!annotation.value(QStringLiteral("stale")).toBool());
            apply->setObjectName(QStringLiteral("storyIntelligenceApplyButton"));
            actions->addWidget(apply);
            connect(apply, &QPushButton::clicked, this, [this, id]() {
                emit applySuggestionRequested(id);
            });
        }
        layout->addLayout(actions);
        m_annotationsLayout->addWidget(card);
    }
}

void StoryIntelligenceWidget::scrollChatToBottom()
{
    QTimer::singleShot(0, m_chatScrollArea, [this]() {
        QScrollBar *bar = m_chatScrollArea->verticalScrollBar();
        bar->setValue(bar->maximum());
    });
}

void StoryIntelligenceWidget::appendChatMessage(const QString &role,
                                                const QString &text,
                                                const QString &speaker,
                                                const QJsonArray &references,
                                                const QString &messageId)
{
    if (text.trimmed().isEmpty()) {
        return;
    }
    auto *bubble = new QFrame(m_chatContainer);
    bubble->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Minimum);
    const bool user = role == QStringLiteral("user");
    bubble->setObjectName(user ? QStringLiteral("storyIntelligenceUserBubble") : QStringLiteral("storyIntelligenceAssistantBubble"));
    auto *layout = new QVBoxLayout(bubble);
    layout->setContentsMargins(12, 10, 12, 10);
    layout->setSpacing(8);
    auto *speakerLabel = plainLabel(role == QStringLiteral("error") ? tr("Connection / model error")
                                        : user                      ? tr("You")
                                                                    : (speaker.isEmpty() ? tr("AI") : tr("%1 · simulation").arg(speaker)),
                                    bubble,
                                    QStringLiteral("storyIntelligenceBubbleSpeaker"));
    auto *message = plainLabel(text, bubble, QStringLiteral("storyIntelligenceBubbleText"));
    message->setWordWrap(true);
    message->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Minimum);
    message->setTextInteractionFlags(Qt::TextSelectableByMouse);
    layout->addWidget(speakerLabel);
    layout->addWidget(message);
    auto *actions = new QHBoxLayout;
    actions->setSpacing(4);
    const auto addAction = [&](const QString &action, const QString &label, const QIcon &icon, bool mutating) {
        auto *button = new QToolButton(bubble);
        button->setObjectName(QStringLiteral("storyChatActionButton"));
        button->setProperty("messageId", messageId);
        button->setProperty("chatAction", action);
        button->setProperty("chatMutation", mutating);
        button->setAccessibleName(label);
        button->setToolTip(label);
        button->setIcon(icon);
        button->setAutoRaise(true);
        button->setEnabled(!mutating || !m_busy);
        actions->addWidget(button);
        connect(button, &QToolButton::clicked, this, [this, bubble, messageId, action, text]() {
            if (action == QStringLiteral("copy"))
                QApplication::clipboard()->setText(text);
            else if (messageId.isEmpty() && action == QStringLiteral("delete"))
                bubble->deleteLater();
            else
                emit messageActionRequested(messageId, action);
        });
    };
    addAction(QStringLiteral("copy"), tr("Copy message"), QIcon(QStringLiteral(":/icons/shell-copy.svg")), false);
    if (!messageId.isEmpty()) {
        addAction(QStringLiteral("edit"),
                  user ? tr("Edit and resend in a new conversation branch") : tr("Edit response in a new conversation branch"),
                  QIcon(QStringLiteral(":/icons/shell-edit.svg")),
                  true);
        addAction(QStringLiteral("retry"),
                  user ? tr("Resend from here in a new conversation branch") : tr("Regenerate response in a new conversation branch"),
                  QIcon(QStringLiteral(":/icons/shell-retry.svg")),
                  true);
    } else if (role == QStringLiteral("error")) {
        addAction(QStringLiteral("retry"), tr("Retry the last prompt"), QIcon(QStringLiteral(":/icons/shell-retry.svg")), true);
    }
    addAction(QStringLiteral("delete"), tr("Delete message"), QIcon(QStringLiteral(":/icons/shell-trash.svg")), true);
    actions->addStretch(1);
    layout->addLayout(actions);
    for (const auto &value : references) {
        const auto reference = value.toObject();
        const QString quote = reference.value(QStringLiteral("quote")).toString();
        if (quote.isEmpty())
            continue;
        auto *link = new QPushButton(tr("↗ %1").arg(quote.simplified().left(55)), bubble);
        link->setObjectName(QStringLiteral("storyQuoteLink"));
        link->setToolTip(quote);
        connect(link, &QPushButton::clicked, this, [this, reference, quote]() {
            emit annotationNavigationRequested(reference.value(QStringLiteral("start_utf16")).toInt(-1),
                                               reference.value(QStringLiteral("end_utf16")).toInt(-1),
                                               quote);
        });
        layout->addWidget(link);
    }
    if (role == QStringLiteral("user") || role == QStringLiteral("assistant")) {
        auto *remember = new QToolButton(bubble);
        remember->setText(tr("Memory…"));
        remember->setAccessibleName(tr("Save message as memory"));
        remember->setObjectName(QStringLiteral("storyRememberButton"));
        remember->setProperty("chatMutation", true);
        remember->setEnabled(!m_busy);
        remember->setToolTip(tr("Review the text and choose whether to approve it as a scoped memory"));
        connect(remember, &QToolButton::clicked, this, [this, text]() {
            emit rememberRequested(text);
        });
        actions->addWidget(remember);
    }
    m_chatLayout->insertWidget(qMax(0, m_chatLayout->count() - 1), bubble);
    scrollChatToBottom();
}

void StoryIntelligenceWidget::showChatError(const QString &message)
{
    appendChatMessage(QStringLiteral("error"),
                      message.trimmed().isEmpty() ? tr("Story Intelligence request failed. Check Model Settings and try again.") : message.left(2000));
    setBusy(false);
    setStatusMessage(tr("Response failed · check Model Settings"));
}

void StoryIntelligenceWidget::appendActivityCard(const QString &title, const QString &detail, const QString &operationId)
{
    if (title.trimmed().isEmpty() && detail.trimmed().isEmpty()) {
        return;
    }
    auto *card = new QFrame(m_chatContainer);
    card->setObjectName(QStringLiteral("storyIntelligenceActivityCard"));
    auto *layout = new QVBoxLayout(card);
    layout->setContentsMargins(9, 8, 9, 8);
    layout->setSpacing(4);

    auto *caption = plainLabel(tr("THOTHPAD ACTION"), card, QStringLiteral("storyIntelligenceActivityCaption"));
    layout->addWidget(caption);
    if (!title.trimmed().isEmpty()) {
        auto *titleLabel = plainLabel(title, card, QStringLiteral("storyIntelligenceActivityTitle"));
        titleLabel->setWordWrap(true);
        layout->addWidget(titleLabel);
    }
    if (!detail.trimmed().isEmpty()) {
        auto *detailLabel = plainLabel(detail, card, QStringLiteral("storyIntelligenceActivityDetail"));
        detailLabel->setWordWrap(true);
        detailLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
        layout->addWidget(detailLabel);
    }
    if (!operationId.isEmpty()) {
        auto *actions = new QHBoxLayout;
        actions->addStretch(1);
        auto *undo = new QPushButton(tr("Undo AI edit"), card);
        undo->setObjectName(QStringLiteral("storyIntelligenceUndoButton"));
        undo->setToolTip(tr("Undo only if this exact AI transaction is still the current manuscript state"));
        actions->addWidget(undo);
        connect(undo, &QPushButton::clicked, this, [this, operationId]() {
            emit undoAgentTransactionRequested(operationId);
        });
        layout->addLayout(actions);
    }

    m_chatLayout->insertWidget(qMax(0, m_chatLayout->count() - 1), card);
    scrollChatToBottom();
}

void StoryIntelligenceWidget::clearChat()
{
    while (m_chatLayout->count() > 1) {
        QLayoutItem *item = m_chatLayout->takeAt(0);
        if (item && item->widget()) {
            item->widget()->deleteLater();
        }
        delete item;
    }
}

void StoryIntelligenceWidget::setBusy(bool busy)
{
    m_busy = busy;
    for (auto *button : m_chatContainer->findChildren<QToolButton *>())
        if (button->property("chatMutation").toBool())
            button->setEnabled(!busy);
    m_scopeCombo->setEnabled(!busy);
    m_sessionCombo->setEnabled(!busy);
    m_workspaceButton->setEnabled(!busy);
    m_newSessionButton->setEnabled(!busy);
    m_deleteSessionButton->setEnabled(!busy && !m_sessionCombo->currentData().toString().isEmpty());
    m_charactersContainer->setEnabled(!busy);
    m_chatInput->setEnabled(!busy);
    m_sendButton->setEnabled(!busy);
    m_statusLabel->setText(busy ? tr("Thinking…") : tr("Ready"));
    m_statusLabel->setVisible(busy);
}

void StoryIntelligenceWidget::setWorkspaceContext(const QString &mode,
                                                  const QString &title,
                                                  const QString &agentName,
                                                  const QString &sessionTitle,
                                                  const QString &scopeKind)
{
    const QSignalBlocker blocker(m_scopeCombo);
    QString displayTitle = title;
    displayTitle.remove(QStringLiteral("**"));
    displayTitle.remove(QStringLiteral("__"));
    m_scopeCombo->setItemText(1, tr("Current chapter"));
    const auto kind = scopeKind.isEmpty() ? mode : scopeKind;
    QString heading = kind == QStringLiteral("scene") ? tr("Scene: %1").arg(displayTitle)
        : kind == QStringLiteral("chapter")           ? tr("Chapter: %1").arg(displayTitle)
                                                      : tr("Whole manuscript");
    if (mode != QStringLiteral("manuscript") && kind == QStringLiteral("manuscript"))
        heading = tr("No chapter heading here. Using manuscript context.");
    m_contextLabel->setText(heading);
    m_scopeCombo->setCurrentIndex(mode == QStringLiteral("manuscript") ? 0 : 1);
    m_scopeCombo->setToolTip(tr("%1\nContext and cast follow the heading containing your cursor. Manuscript settings are inherited.").arg(displayTitle));
    m_scopeLabel->setText(agentName);
    m_scopeLabel->setToolTip(tr("Conversation: %1\nWorkspace contains saved agents, memories and sessions.").arg(sessionTitle));
}

void StoryIntelligenceWidget::setSessions(const QJsonArray &sessions, const QString &activeSessionId)
{
    if (m_sessions == sessions && m_sessionCombo->count() > 0 && m_sessionCombo->currentData().toString() == activeSessionId)
        return;
    m_sessions = sessions;
    const QSignalBlocker blocker(m_sessionCombo);
    m_sessionCombo->clear();
    if (activeSessionId.isEmpty())
        m_sessionCombo->addItem(tr("New conversation"), QString());
    for (const auto &value : sessions) {
        const auto session = value.toObject();
        const QString label = session.value("label").toString();
        m_sessionCombo->addItem(label, session.value("id").toString());
        m_sessionCombo->setItemData(m_sessionCombo->count() - 1, label, Qt::ToolTipRole);
    }
    m_sessionCombo->setCurrentIndex(qMax(0, m_sessionCombo->findData(activeSessionId)));
    m_deleteSessionButton->setEnabled(!m_busy && !activeSessionId.isEmpty());
}

void StoryIntelligenceWidget::appendProposal(const QString &kind, const QJsonObject &proposal)
{
    auto *button = new QPushButton(kind == QStringLiteral("memory")      ? tr("Review proposed memory…")
                                       : kind == QStringLiteral("scene") ? tr("Review proposed scene context…")
                                                                         : tr("Review proposed character…"),
                                   m_chatContainer);
    button->setObjectName(QStringLiteral("storyProposalButton"));
    connect(button, &QPushButton::clicked, this, [this, kind, proposal]() {
        emit proposalReviewRequested(kind, proposal);
    });
    m_chatLayout->insertWidget(qMax(0, m_chatLayout->count() - 1), button);
}

void StoryIntelligenceWidget::setStatusMessage(const QString &message)
{
    const QString status = message.trimmed();
    m_statusLabel->setText(status.isEmpty() ? tr("Ready") : status);
    m_statusLabel->setVisible(!status.isEmpty());
}

void StoryIntelligenceWidget::submitChat()
{
    const QString message = m_chatInput->toPlainText().trimmed();
    if (!m_chatInput->isEnabled() || message.isEmpty()) {
        return;
    }
    m_chatInput->clear();
    emit chatRequested(message);
}
}
