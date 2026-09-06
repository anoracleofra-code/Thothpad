/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "storyintelligencewidget.h"

#include <QDir>
#include <QApplication>
#include <QClipboard>
#include <QComboBox>
#include <QDialog>
#include <QDialogButtonBox>
#include <QSignalBlocker>
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
#include <QSizePolicy>
#include <QSplitter>
#include <QTabWidget>
#include <QTabBar>
#include <QStringList>
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
    connect(openProject, &QPushButton::clicked, this, &StoryIntelligenceWidget::projectFolderRequested);
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
        if ((key->key() == Qt::Key_Return || key->key() == Qt::Key_Enter)
            && !(key->modifiers() & (Qt::ShiftModifier | Qt::AltModifier | Qt::MetaModifier))) {
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
        return;
    }
    const QString normalized = QDir::toNativeSeparators(path);
    m_projectPathLabel->setText(normalized.length() > 34 ? QStringLiteral("…") + normalized.right(33) : normalized);
    m_projectPathLabel->setToolTip(normalized);
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
    if (m_characters == characters && m_charactersLayout->count() > 0) return;
    m_characters = characters;
    rebuildCharacters();
}

void StoryIntelligenceWidget::setActiveCharacter(const QString &characterId)
{
    if (m_activeCharacterId == characterId) return;
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
        auto *empty = plainLabel(tr("No characters yet. Add one to ground voice and knowledge checks."), m_charactersContainer, QStringLiteral("storyIntelligenceMutedLabel"));
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
    if (m_annotations == annotations && m_annotationsLayout->count() > 0) return;
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

void StoryIntelligenceWidget::appendChatMessage(const QString &role, const QString &text, const QString &speaker, const QJsonArray &references, const QString &messageId)
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
    auto *speakerLabel = plainLabel(role == QStringLiteral("error") ? tr("Connection / model error") : user ? tr("You") : (speaker.isEmpty() ? tr("AI") : tr("%1 · simulation").arg(speaker)), bubble, QStringLiteral("storyIntelligenceBubbleSpeaker"));
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
        addAction(QStringLiteral("edit"), user ? tr("Edit and resend in a new conversation branch") : tr("Edit response in a new conversation branch"), QIcon(QStringLiteral(":/icons/shell-edit.svg")), true);
        addAction(QStringLiteral("retry"), user ? tr("Resend from here in a new conversation branch") : tr("Regenerate response in a new conversation branch"), QIcon(QStringLiteral(":/icons/shell-retry.svg")), true);
    } else if (role == QStringLiteral("error")) {
        addAction(QStringLiteral("retry"), tr("Retry the last prompt"), QIcon(QStringLiteral(":/icons/shell-retry.svg")), true);
    }
    addAction(QStringLiteral("delete"), tr("Delete message"), QIcon(QStringLiteral(":/icons/shell-trash.svg")), true);
    actions->addStretch(1);
    layout->addLayout(actions);
    for (const auto &value : references) {
        const auto reference = value.toObject();
        const QString quote = reference.value(QStringLiteral("quote")).toString();
        if (quote.isEmpty()) continue;
        auto *link = new QPushButton(tr("↗ %1").arg(quote.simplified().left(55)), bubble);
        link->setObjectName(QStringLiteral("storyQuoteLink"));
        link->setToolTip(quote);
        connect(link, &QPushButton::clicked, this, [this, reference, quote]() {
            emit annotationNavigationRequested(reference.value(QStringLiteral("start_utf16")).toInt(-1),
                reference.value(QStringLiteral("end_utf16")).toInt(-1), quote);
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
        connect(remember, &QToolButton::clicked, this, [this, text]() { emit rememberRequested(text); });
        actions->addWidget(remember);
    }
    m_chatLayout->insertWidget(qMax(0, m_chatLayout->count() - 1), bubble);
    scrollChatToBottom();
}

void StoryIntelligenceWidget::showChatError(const QString &message)
{
    appendChatMessage(QStringLiteral("error"), message.trimmed().isEmpty()
        ? tr("Story Intelligence request failed. Check Model Settings and try again.") : message.left(2000));
    setBusy(false);
    setStatusMessage(tr("Response failed · check Model Settings"));
}

void StoryIntelligenceWidget::appendActivityCard(
    const QString &title,
    const QString &detail,
    const QString &operationId)
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

void StoryIntelligenceWidget::setWorkspaceContext(const QString &mode, const QString &title, const QString &agentName, const QString &sessionTitle, const QString &scopeKind)
{
    const QSignalBlocker blocker(m_scopeCombo);
    QString displayTitle = title;
    displayTitle.remove(QStringLiteral("**"));
    displayTitle.remove(QStringLiteral("__"));
    m_scopeCombo->setItemText(1, tr("Current chapter"));
    const auto kind = scopeKind.isEmpty() ? mode : scopeKind;
    QString heading = kind == QStringLiteral("scene") ? tr("Scene: %1").arg(displayTitle)
        : kind == QStringLiteral("chapter") ? tr("Chapter: %1").arg(displayTitle) : tr("Whole manuscript");
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
    if (m_sessions == sessions && m_sessionCombo->count() > 0
        && m_sessionCombo->currentData().toString() == activeSessionId)
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
    auto *button = new QPushButton(kind == QStringLiteral("memory") ? tr("Review proposed memory…")
        : kind == QStringLiteral("scene") ? tr("Review proposed scene context…") : tr("Review proposed character…"), m_chatContainer);
    button->setObjectName(QStringLiteral("storyProposalButton"));
    connect(button, &QPushButton::clicked, this, [this, kind, proposal]() {
        emit proposalReviewRequested(kind, proposal);
    });
    m_chatLayout->insertWidget(qMax(0, m_chatLayout->count()-1), button);
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
