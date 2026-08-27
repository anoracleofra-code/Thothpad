/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "storyintelligencewidget.h"

#include <QAbstractTextDocumentLayout>
#include <QFrame>
#include <QHBoxLayout>
#include <QJsonValue>
#include <QLabel>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QScrollArea>
#include <QScrollBar>
#include <QSizePolicy>
#include <QToolButton>
#include <QVBoxLayout>

namespace ghostwriter
{
namespace
{
constexpr int StoryPaneWidth = 320;

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
    , m_chatContainer(new QWidget(this))
    , m_chatLayout(new QVBoxLayout(m_chatContainer))
    , m_chatScrollArea(new QScrollArea(this))
    , m_chatInput(new QPlainTextEdit(this))
    , m_sendButton(new QPushButton(tr("Send"), this))
    , m_statusLabel(new QLabel(this))
{
    setObjectName(QStringLiteral("storyIntelligenceWidget"));
    setMinimumWidth(StoryPaneWidth);
    setMaximumWidth(StoryPaneWidth);
    setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Expanding);

    auto *root = new QVBoxLayout(this);
    root->setContentsMargins(0, 0, 0, 0);
    root->setSpacing(0);

    auto *header = new QFrame(this);
    header->setObjectName(QStringLiteral("storyIntelligenceHeaderSurface"));
    auto *headerLayout = new QHBoxLayout(header);
    headerLayout->setContentsMargins(14, 10, 10, 10);
    headerLayout->setSpacing(8);
    auto *title = new QLabel(tr("✦  Story Intelligence"), header);
    title->setObjectName(QStringLiteral("storyIntelligenceTitle"));
    headerLayout->addWidget(title);
    headerLayout->addStretch(1);
    m_collapseButton->setObjectName(QStringLiteral("storyIntelligenceCollapseButton"));
    m_collapseButton->setToolTip(tr("Hide Story Intelligence"));
    m_collapseButton->setAutoRaise(true);
    m_collapseButton->setFocusPolicy(Qt::NoFocus);
    headerLayout->addWidget(m_collapseButton);
    root->addWidget(header);

    auto *bodyScroll = new QScrollArea(this);
    bodyScroll->setObjectName(QStringLiteral("storyIntelligenceScrollArea"));
    bodyScroll->setWidgetResizable(true);
    bodyScroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    bodyScroll->setFrameShape(QFrame::NoFrame);
    auto *body = new QWidget(bodyScroll);
    body->setObjectName(QStringLiteral("storyIntelligenceContent"));
    auto *bodyLayout = new QVBoxLayout(body);
    bodyLayout->setContentsMargins(14, 14, 14, 14);
    bodyLayout->setSpacing(18);

    auto *configuration = makeCard(QStringLiteral("storyIntelligenceSetupSurface"));
    auto *configurationLayout = new QVBoxLayout(configuration);
    configurationLayout->setContentsMargins(10, 10, 10, 10);
    configurationLayout->setSpacing(8);

    auto *providerRow = new QHBoxLayout;
    auto *providerCaption = new QLabel(tr("Model"), configuration);
    providerCaption->setObjectName(QStringLiteral("storyIntelligenceCaption"));
    providerRow->addWidget(providerCaption);
    providerRow->addStretch(1);
    m_modelSettingsButton->setObjectName(QStringLiteral("storyIntelligenceApiKeyButton"));
    m_modelSettingsButton->setFlat(true);
    providerRow->addWidget(m_modelSettingsButton);
    configurationLayout->addLayout(providerRow);

    m_providerLabel->setObjectName(QStringLiteral("storyIntelligenceProviderLabel"));
    m_modelLabel->setObjectName(QStringLiteral("storyIntelligenceModelLabel"));
    m_keyStateLabel->setObjectName(QStringLiteral("storyIntelligenceKeyState"));
    m_providerLabel->setText(tr("Provider: not configured"));
    m_modelLabel->setText(tr("Model: —"));
    m_keyStateLabel->setText(tr("Secure key not loaded"));
    configurationLayout->addWidget(m_providerLabel);
    configurationLayout->addWidget(m_modelLabel);
    configurationLayout->addWidget(m_keyStateLabel);

    auto *projectDivider = new QFrame(configuration);
    projectDivider->setFrameShape(QFrame::HLine);
    projectDivider->setObjectName(QStringLiteral("storyIntelligenceDivider"));
    configurationLayout->addWidget(projectDivider);

    auto *projectRow = new QHBoxLayout;
    auto *projectLabels = new QVBoxLayout;
    auto *projectCaption = new QLabel(tr("Project folder"), configuration);
    projectCaption->setObjectName(QStringLiteral("storyIntelligenceCaption"));
    m_projectPathLabel->setObjectName(QStringLiteral("storyIntelligenceMutedLabel"));
    m_projectPathLabel->setText(tr("No project selected"));
    m_projectPathLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    projectLabels->addWidget(projectCaption);
    projectLabels->addWidget(m_projectPathLabel);
    projectRow->addLayout(projectLabels, 1);
    auto *openProject = new QPushButton(tr("Open"), configuration);
    openProject->setObjectName(QStringLiteral("storyIntelligencePrimaryButton"));
    projectRow->addWidget(openProject);
    configurationLayout->addLayout(projectRow);
    bodyLayout->addWidget(configuration);

    auto *sceneSection = new QWidget(body);
    auto *sceneLayout = new QVBoxLayout(sceneSection);
    sceneLayout->setContentsMargins(0, 0, 0, 0);
    sceneLayout->setSpacing(7);
    auto *sceneHeader = new QHBoxLayout;
    sceneHeader->addWidget(makeSectionTitle(tr("⌄  Scene Context")));
    sceneHeader->addStretch(1);
    auto *editScene = new QToolButton(sceneSection);
    editScene->setText(tr("Edit"));
    editScene->setObjectName(QStringLiteral("storyIntelligenceTextButton"));
    editScene->setAutoRaise(true);
    sceneHeader->addWidget(editScene);
    sceneLayout->addLayout(sceneHeader);
    auto *sceneCard = makeCard(QStringLiteral("storyIntelligenceSceneCard"));
    auto *sceneCardLayout = new QVBoxLayout(sceneCard);
    sceneCardLayout->setContentsMargins(10, 10, 10, 10);
    sceneCardLayout->setSpacing(5);
    auto *settingCaption = new QLabel(tr("Setting"), sceneCard);
    settingCaption->setObjectName(QStringLiteral("storyIntelligenceCardHeading"));
    m_settingLabel->setWordWrap(true);
    m_settingLabel->setObjectName(QStringLiteral("storyIntelligenceCardBody"));
    auto *goalCaption = new QLabel(tr("Current Goal"), sceneCard);
    goalCaption->setObjectName(QStringLiteral("storyIntelligenceCardHeading"));
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
    bodyLayout->addWidget(sceneSection);

    auto *charactersSection = new QWidget(body);
    auto *charactersOuter = new QVBoxLayout(charactersSection);
    charactersOuter->setContentsMargins(0, 0, 0, 0);
    charactersOuter->setSpacing(7);
    auto *charactersHeader = new QHBoxLayout;
    charactersHeader->addWidget(makeSectionTitle(tr("⌄  Characters")));
    charactersHeader->addStretch(1);
    auto *editCharacters = new QToolButton(charactersSection);
    editCharacters->setText(tr("Edit"));
    editCharacters->setObjectName(QStringLiteral("storyIntelligenceTextButton"));
    editCharacters->setAutoRaise(true);
    auto *addCharacter = new QToolButton(charactersSection);
    addCharacter->setText(QStringLiteral("+"));
    addCharacter->setObjectName(QStringLiteral("storyIntelligenceTextButton"));
    addCharacter->setAutoRaise(true);
    charactersHeader->addWidget(editCharacters);
    charactersHeader->addWidget(addCharacter);
    charactersOuter->addLayout(charactersHeader);
    m_charactersLayout->setContentsMargins(0, 0, 0, 0);
    m_charactersLayout->setSpacing(7);
    charactersOuter->addWidget(m_charactersContainer);
    bodyLayout->addWidget(charactersSection);

    auto *chatHeadingRow = new QHBoxLayout;
    chatHeadingRow->addWidget(makeSectionTitle(tr("▱  Co-Writer Chat")));
    chatHeadingRow->addStretch(1);
    auto *clearMarks = new QToolButton(body);
    clearMarks->setText(tr("Clear marks"));
    clearMarks->setObjectName(QStringLiteral("storyIntelligenceTextButton"));
    clearMarks->setAutoRaise(true);
    clearMarks->setToolTip(tr("Clear AI annotations without changing manuscript text"));
    chatHeadingRow->addWidget(clearMarks);
    bodyLayout->addLayout(chatHeadingRow);

    m_chatScrollArea->setObjectName(QStringLiteral("storyIntelligenceChatScroll"));
    m_chatScrollArea->setWidgetResizable(true);
    m_chatScrollArea->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    m_chatScrollArea->setFrameShape(QFrame::NoFrame);
    m_chatContainer->setObjectName(QStringLiteral("storyIntelligenceChatContent"));
    m_chatLayout->setContentsMargins(0, 0, 0, 0);
    m_chatLayout->setSpacing(8);
    m_chatLayout->addStretch(1);
    m_chatScrollArea->setWidget(m_chatContainer);
    m_chatScrollArea->setMinimumHeight(170);
    bodyLayout->addWidget(m_chatScrollArea, 1);

    bodyLayout->addStretch(1);
    bodyScroll->setWidget(body);
    root->addWidget(bodyScroll, 1);

    auto *composer = new QFrame(this);
    composer->setObjectName(QStringLiteral("storyIntelligenceComposer"));
    auto *composerLayout = new QVBoxLayout(composer);
    composerLayout->setContentsMargins(10, 9, 10, 9);
    composerLayout->setSpacing(6);
    m_chatInput->setObjectName(QStringLiteral("storyIntelligenceChatInput"));
    m_chatInput->setPlaceholderText(tr("Ask AI to brainstorm, rewrite, analyze, or annotate…"));
    m_chatInput->setTabChangesFocus(true);
    m_chatInput->setMaximumBlockCount(32);
    m_chatInput->setFixedHeight(58);
    composerLayout->addWidget(m_chatInput);
    auto *composerActions = new QHBoxLayout;
    m_statusLabel->setObjectName(QStringLiteral("storyIntelligenceStatus"));
    m_statusLabel->setText(tr("Ready"));
    composerActions->addWidget(m_statusLabel, 1);
    m_sendButton->setObjectName(QStringLiteral("storyIntelligencePrimaryButton"));
    composerActions->addWidget(m_sendButton);
    composerLayout->addLayout(composerActions);
    root->addWidget(composer);

    setSceneContext({});
    setCharacters({});

    connect(m_collapseButton, &QToolButton::clicked, this, &StoryIntelligenceWidget::collapseRequested);
    connect(m_modelSettingsButton, &QPushButton::clicked, this, &StoryIntelligenceWidget::modelSettingsRequested);
    connect(openProject, &QPushButton::clicked, this, &StoryIntelligenceWidget::projectFolderRequested);
    connect(editScene, &QToolButton::clicked, this, &StoryIntelligenceWidget::editSceneRequested);
    connect(addCharacter, &QToolButton::clicked, this, &StoryIntelligenceWidget::addCharacterRequested);
    connect(editCharacters, &QToolButton::clicked, this, &StoryIntelligenceWidget::editCharactersRequested);
    connect(clearMarks, &QToolButton::clicked, this, &StoryIntelligenceWidget::clearAnnotationsRequested);
    connect(m_sendButton, &QPushButton::clicked, this, &StoryIntelligenceWidget::submitChat);
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
    auto *label = new QLabel(text, this);
    label->setObjectName(QStringLiteral("storyIntelligenceSectionTitle"));
    return label;
}

void StoryIntelligenceWidget::setProviderSummary(const QString &provider, const QString &model, bool credentialConfigured)
{
    m_providerLabel->setText(tr("Provider: %1").arg(provider.isEmpty() ? tr("not configured") : provider));
    m_modelLabel->setText(tr("Model: %1").arg(model.isEmpty() ? QStringLiteral("—") : model));
    m_keyStateLabel->setText(credentialConfigured ? tr("🔒 Secure key configured") : tr("No stored key · local/keyless providers still work"));
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
    m_contextDetailLabel->setText(details.join(QStringLiteral(" · ")));
    m_contextDetailLabel->setVisible(!details.isEmpty());
}

QString StoryIntelligenceWidget::characterId(const QJsonObject &character) const
{
    QString id = character.value(QStringLiteral("id")).toString().trimmed();
    if (id.isEmpty()) {
        id = character.value(QStringLiteral("name")).toString().trimmed().toCaseFolded();
        id.replace(QRegularExpression(QStringLiteral("[^a-z0-9]+")), QStringLiteral("-"));
        id = id.trimmed();
    }
    return id;
}

void StoryIntelligenceWidget::setCharacters(const QJsonArray &characters)
{
    m_characters = characters;
    rebuildCharacters();
}

void StoryIntelligenceWidget::setActiveCharacter(const QString &characterId)
{
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
        auto *empty = new QLabel(tr("No characters yet. Add one to ground voice and knowledge checks."), m_charactersContainer);
        empty->setWordWrap(true);
        empty->setObjectName(QStringLiteral("storyIntelligenceMutedLabel"));
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
        const QString subtitle = role.isEmpty() ? tr("Character agent") : role;
        button->setText(QStringLiteral("%1   %2\n      %3").arg(initials(name), name, subtitle));
        button->setToolTip(tr("Use %1 as the active character simulation for new chat messages. Click again to return to the co-writer.").arg(name));
        button->setProperty("characterId", id);
        connect(button, &QPushButton::clicked, this, [this, id](bool checked) {
            const QString next = checked ? id : QString();
            m_activeCharacterId = next;
            rebuildCharacters();
            emit characterActivated(next);
        });
        m_charactersLayout->addWidget(button);
    }
}

void StoryIntelligenceWidget::appendChatMessage(const QString &role, const QString &text, const QString &speaker)
{
    if (text.trimmed().isEmpty()) {
        return;
    }
    auto *bubble = new QFrame(m_chatContainer);
    const bool user = role == QStringLiteral("user");
    bubble->setObjectName(user ? QStringLiteral("storyIntelligenceUserBubble") : QStringLiteral("storyIntelligenceAssistantBubble"));
    auto *layout = new QVBoxLayout(bubble);
    layout->setContentsMargins(9, 8, 9, 8);
    layout->setSpacing(4);
    auto *speakerLabel = new QLabel(user ? tr("You") : (speaker.isEmpty() ? tr("AI") : speaker), bubble);
    speakerLabel->setObjectName(QStringLiteral("storyIntelligenceBubbleSpeaker"));
    auto *message = new QLabel(text, bubble);
    message->setObjectName(QStringLiteral("storyIntelligenceBubbleText"));
    message->setWordWrap(true);
    message->setTextInteractionFlags(Qt::TextSelectableByMouse | Qt::LinksAccessibleByMouse);
    layout->addWidget(speakerLabel);
    layout->addWidget(message);
    m_chatLayout->insertWidget(qMax(0, m_chatLayout->count() - 1), bubble);
    QMetaObject::invokeMethod(m_chatScrollArea->verticalScrollBar(), "setValue", Qt::QueuedConnection,
                              Q_ARG(int, m_chatScrollArea->verticalScrollBar()->maximum()));
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
    m_chatInput->setEnabled(!busy);
    m_sendButton->setEnabled(!busy);
    m_statusLabel->setText(busy ? tr("Thinking…") : tr("Ready"));
}

void StoryIntelligenceWidget::setStatusMessage(const QString &message)
{
    m_statusLabel->setText(message.isEmpty() ? tr("Ready") : message);
}

void StoryIntelligenceWidget::submitChat()
{
    const QString message = m_chatInput->toPlainText().trimmed();
    if (message.isEmpty()) {
        return;
    }
    m_chatInput->clear();
    emit chatRequested(message);
}
}
