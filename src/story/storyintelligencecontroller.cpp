/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "storyintelligencecontroller.h"

#include "storyintelligencewidget.h"
#include "../editor/markdowndocument.h"
#include "../editor/markdowneditor.h"
#include "../editor/textformatoverlaycontroller.h"
#include "../messageboxhelper.h"
#include "../prose/credentialstore.h"
#include "../prose/providersettingsdialog.h"
#include "../prose/writerengineclient.h"

#include <QCryptographicHash>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QInputDialog>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QLabel>
#include <QLineEdit>
#include <QPlainTextEdit>
#include <QRegularExpression>
#include <QSaveFile>
#include <QSettings>
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

QString providerDisplayName(const QString &kind)
{
    static const QHash<QString, QString> names = {
        {QStringLiteral("gemini"), QStringLiteral("Google Gemini")},
        {QStringLiteral("openai"), QStringLiteral("OpenAI")},
        {QStringLiteral("openai_compatible"), QStringLiteral("OpenAI compatible")},
        {QStringLiteral("openrouter"), QStringLiteral("OpenRouter")},
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
}

StoryIntelligenceController::StoryIntelligenceController(
    MarkdownEditor *editor,
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

    connect(m_widget, &StoryIntelligenceWidget::projectFolderRequested, this, &StoryIntelligenceController::chooseProjectFolder);
    connect(m_widget, &StoryIntelligenceWidget::modelSettingsRequested, this, &StoryIntelligenceController::openModelSettings);
    connect(m_widget, &StoryIntelligenceWidget::editSceneRequested, this, &StoryIntelligenceController::editSceneContext);
    connect(m_widget, &StoryIntelligenceWidget::addCharacterRequested, this, &StoryIntelligenceController::addCharacter);
    connect(m_widget, &StoryIntelligenceWidget::editCharactersRequested, this, &StoryIntelligenceController::editCharacters);
    connect(m_widget, &StoryIntelligenceWidget::chatRequested, this, &StoryIntelligenceController::sendChat);
    connect(m_widget, &StoryIntelligenceWidget::clearAnnotationsRequested, this, &StoryIntelligenceController::clearAnnotations);
    connect(m_widget, &StoryIntelligenceWidget::characterActivated, this, [this](const QString &id) {
        QSettings().setValue(QStringLiteral("story/activeCharacter"), id);
    });

    connect(m_engine, &WriterEngineClient::responseReceived, this, &StoryIntelligenceController::handleResponse);
    connect(m_engine, &WriterEngineClient::requestsInvalidated, this, [this](const QStringList &ids) {
        if (!m_chatRequestId.isEmpty() && ids.contains(m_chatRequestId)) {
            m_chatRequestId.clear();
            m_widget->setBusy(false);
            m_widget->setStatusMessage(tr("Engine restarted; resend the last message."));
        }
    });
    connect(m_credentials, &CredentialStore::loaded, this, &StoryIntelligenceController::handleCredentialLoaded);
    connect(m_credentials, &CredentialStore::error, this, &StoryIntelligenceController::handleCredentialError);
    connect(m_editor->document(), &QTextDocument::contentsChange, this, [this](int, int, int) {
        ++m_revision;
    });
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
    }
    m_widget->setActiveCharacter(QSettings().value(QStringLiteral("story/activeCharacter")).toString());
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
    QSettings settings;
    settings.beginGroup(QStringLiteral("prose/provider"));
    const QString provider = settings.value(QStringLiteral("provider"), QStringLiteral("openai_compatible")).toString();
    const QString model = settings.value(QStringLiteral("model"), QStringLiteral("local-model")).toString();
    const bool hasCredential = settings.value(QStringLiteral("has_credential"), false).toBool();
    settings.endGroup();
    m_widget->setProviderSummary(providerDisplayName(provider), model, hasCredential);
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
    return provider;
}

QString StoryIntelligenceController::providerCredentialId(const QJsonObject &provider) const
{
    const QUrl url(provider.value(QStringLiteral("base_url")).toString().trimmed());
    const int defaultPort = url.scheme().compare(QStringLiteral("https"), Qt::CaseInsensitive) == 0 ? 443 : 80;
    const QString origin = QStringLiteral("%1://%2:%3")
        .arg(url.scheme().toLower(), url.host().toLower())
        .arg(url.port(defaultPort));
    const QString endpointId = QString::fromLatin1(QCryptographicHash::hash(
        origin.toUtf8(), QCryptographicHash::Sha256).toHex().left(16));
    return QStringLiteral("provider/%1/%2/%3").arg(
        provider.value(QStringLiteral("provider")).toString(), endpointId,
        provider.value(QStringLiteral("model")).toString().trimmed());
}

bool StoryIntelligenceController::providerMayNeedCredential(const QJsonObject &provider) const
{
    const QString kind = provider.value(QStringLiteral("provider")).toString();
    if (kind == QStringLiteral("ollama") || kind == QStringLiteral("lmstudio") || kind == QStringLiteral("llama_cpp")) {
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

void StoryIntelligenceController::chooseProjectFolder()
{
    const QString start = m_projectRoot.isEmpty() ? QDir::homePath() : m_projectRoot;
    const QString selected = QFileDialog::getExistingDirectory(
        m_widget, tr("Open Story Project"), start,
        QFileDialog::ShowDirsOnly | QFileDialog::DontResolveSymlinks);
    if (selected.isEmpty()) {
        return;
    }
    loadProject(selected);
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
    m_projectRoot = QDir::cleanPath(canonical);
    QSettings().setValue(QStringLiteral("story/projectRoot"), m_projectRoot);
    m_widget->setProjectFolder(m_projectRoot);
    loadProjectMetadata();
    clearAnnotations();
    m_history = {};
    m_widget->clearChat();
    m_widget->setStatusMessage(tr("Project loaded"));
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

bool StoryIntelligenceController::saveProjectMetadata(QString *errorMessage) const
{
    if (m_projectRoot.isEmpty()) {
        if (errorMessage) {
            *errorMessage = tr("Open a project folder first.");
        }
        return false;
    }
    QDir root(m_projectRoot);
    if (!root.mkpath(QStringLiteral(".thothpad"))) {
        if (errorMessage) {
            *errorMessage = tr("Could not create the .thothpad project metadata directory.");
        }
        return false;
    }
    QSaveFile file(metadataPath());
    if (!file.open(QIODevice::WriteOnly)) {
        if (errorMessage) {
            *errorMessage = file.errorString();
        }
        return false;
    }
    const QByteArray json = QJsonDocument(m_metadata).toJson(QJsonDocument::Indented);
    if (file.write(json) != json.size()) {
        if (errorMessage) {
            *errorMessage = file.errorString();
        }
        file.cancelWriting();
        return false;
    }
    if (!file.commit()) {
        if (errorMessage) {
            *errorMessage = file.errorString();
        }
        return false;
    }
    return true;
}

void StoryIntelligenceController::editSceneContext()
{
    if (m_projectRoot.isEmpty()) {
        chooseProjectFolder();
        if (m_projectRoot.isEmpty()) {
            return;
        }
    }
    const QJsonObject current = m_metadata.value(QStringLiteral("scene_context")).toObject();
    QDialog dialog(m_widget);
    dialog.setWindowTitle(tr("Scene Context"));
    auto *layout = new QVBoxLayout(&dialog);
    auto *form = new QFormLayout;
    QPlainTextEdit setting;
    QPlainTextEdit goal;
    QLineEdit pov;
    QLineEdit location;
    QLineEdit time;
    QPlainTextEdit conflict;
    QPlainTextEdit notes;
    setting.setPlainText(current.value(QStringLiteral("setting")).toString());
    goal.setPlainText(current.value(QStringLiteral("goal")).toString());
    pov.setText(current.value(QStringLiteral("pov")).toString());
    location.setText(current.value(QStringLiteral("location")).toString());
    time.setText(current.value(QStringLiteral("time")).toString());
    conflict.setPlainText(current.value(QStringLiteral("conflict")).toString());
    notes.setPlainText(current.value(QStringLiteral("notes")).toString());
    for (QPlainTextEdit *edit : {&setting, &goal, &conflict, &notes}) {
        edit->setMaximumHeight(90);
    }
    form->addRow(tr("Setting"), &setting);
    form->addRow(tr("Current goal"), &goal);
    form->addRow(tr("POV"), &pov);
    form->addRow(tr("Location"), &location);
    form->addRow(tr("Time"), &time);
    form->addRow(tr("Conflict"), &conflict);
    form->addRow(tr("Notes"), &notes);
    layout->addLayout(form);
    QDialogButtonBox buttons(QDialogButtonBox::Save | QDialogButtonBox::Cancel, &dialog);
    connect(&buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    connect(&buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(&buttons);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }
    QJsonObject next;
    next.insert(QStringLiteral("setting"), setting.toPlainText().trimmed());
    next.insert(QStringLiteral("goal"), goal.toPlainText().trimmed());
    next.insert(QStringLiteral("pov"), pov.text().trimmed());
    next.insert(QStringLiteral("location"), location.text().trimmed());
    next.insert(QStringLiteral("time"), time.text().trimmed());
    next.insert(QStringLiteral("conflict"), conflict.toPlainText().trimmed());
    next.insert(QStringLiteral("notes"), notes.toPlainText().trimmed());
    m_metadata.insert(QStringLiteral("scene_context"), next);
    QString error;
    if (!saveProjectMetadata(&error)) {
        MessageBoxHelper::warning(m_widget, tr("Scene context not saved"), error);
        return;
    }
    m_widget->setSceneContext(next);
}

void StoryIntelligenceController::addCharacter()
{
    if (m_projectRoot.isEmpty()) {
        chooseProjectFolder();
        if (m_projectRoot.isEmpty()) {
            return;
        }
    }
    QDialog dialog(m_widget);
    dialog.setWindowTitle(tr("Add Character"));
    auto *layout = new QVBoxLayout(&dialog);
    auto *form = new QFormLayout;
    QLineEdit name;
    QLineEdit role;
    QPlainTextEdit summary;
    QPlainTextEdit voice;
    QPlainTextEdit knowledge;
    for (QPlainTextEdit *edit : {&summary, &voice, &knowledge}) {
        edit->setMaximumHeight(90);
    }
    form->addRow(tr("Name"), &name);
    form->addRow(tr("Role"), &role);
    form->addRow(tr("Character summary"), &summary);
    form->addRow(tr("Voice"), &voice);
    form->addRow(tr("Knowledge / secrets"), &knowledge);
    layout->addLayout(form);
    QDialogButtonBox buttons(QDialogButtonBox::Save | QDialogButtonBox::Cancel, &dialog);
    connect(&buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    connect(&buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(&buttons);
    if (dialog.exec() != QDialog::Accepted || name.text().trimmed().isEmpty()) {
        return;
    }
    QJsonObject character;
    character.insert(QStringLiteral("id"), QUuid::createUuid().toString(QUuid::WithoutBraces));
    character.insert(QStringLiteral("name"), name.text().trimmed());
    character.insert(QStringLiteral("role"), role.text().trimmed());
    character.insert(QStringLiteral("summary"), summary.toPlainText().trimmed());
    character.insert(QStringLiteral("voice"), voice.toPlainText().trimmed());
    character.insert(QStringLiteral("knowledge"), knowledge.toPlainText().trimmed());
    QJsonArray characters = m_metadata.value(QStringLiteral("characters")).toArray();
    characters.append(character);
    m_metadata.insert(QStringLiteral("characters"), characters);
    QString error;
    if (!saveProjectMetadata(&error)) {
        MessageBoxHelper::warning(m_widget, tr("Character not saved"), error);
        return;
    }
    m_widget->setCharacters(characters);
}

void StoryIntelligenceController::editCharacters()
{
    QJsonArray characters = m_metadata.value(QStringLiteral("characters")).toArray();
    if (characters.isEmpty()) {
        addCharacter();
        return;
    }
    QStringList names;
    for (const QJsonValue value : characters) {
        names << value.toObject().value(QStringLiteral("name")).toString();
    }
    bool ok = false;
    const QString selected = QInputDialog::getItem(m_widget, tr("Edit Character"), tr("Character"), names, 0, false, &ok);
    if (!ok || selected.isEmpty()) {
        return;
    }
    const int index = names.indexOf(selected);
    if (index < 0 || index >= characters.size()) {
        return;
    }
    QJsonObject character = characters.at(index).toObject();
    QDialog dialog(m_widget);
    dialog.setWindowTitle(tr("Edit %1").arg(selected));
    auto *layout = new QVBoxLayout(&dialog);
    auto *form = new QFormLayout;
    QLineEdit name(character.value(QStringLiteral("name")).toString());
    QLineEdit role(character.value(QStringLiteral("role")).toString());
    QPlainTextEdit summary(character.value(QStringLiteral("summary")).toString());
    QPlainTextEdit voice(character.value(QStringLiteral("voice")).toString());
    QPlainTextEdit knowledge(character.value(QStringLiteral("knowledge")).toString());
    for (QPlainTextEdit *edit : {&summary, &voice, &knowledge}) {
        edit->setMaximumHeight(90);
    }
    form->addRow(tr("Name"), &name);
    form->addRow(tr("Role"), &role);
    form->addRow(tr("Character summary"), &summary);
    form->addRow(tr("Voice"), &voice);
    form->addRow(tr("Knowledge / secrets"), &knowledge);
    layout->addLayout(form);
    QDialogButtonBox buttons(QDialogButtonBox::Save | QDialogButtonBox::Cancel, &dialog);
    connect(&buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    connect(&buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(&buttons);
    if (dialog.exec() != QDialog::Accepted || name.text().trimmed().isEmpty()) {
        return;
    }
    character.insert(QStringLiteral("name"), name.text().trimmed());
    character.insert(QStringLiteral("role"), role.text().trimmed());
    character.insert(QStringLiteral("summary"), summary.toPlainText().trimmed());
    character.insert(QStringLiteral("voice"), voice.toPlainText().trimmed());
    character.insert(QStringLiteral("knowledge"), knowledge.toPlainText().trimmed());
    characters[index] = character;
    m_metadata.insert(QStringLiteral("characters"), characters);
    QString error;
    if (!saveProjectMetadata(&error)) {
        MessageBoxHelper::warning(m_widget, tr("Character not saved"), error);
        return;
    }
    m_widget->setCharacters(characters);
}

QJsonObject StoryIntelligenceController::activeCharacter() const
{
    const QString id = m_widget->activeCharacterId();
    if (id.isEmpty()) {
        return {};
    }
    const QJsonArray characters = m_metadata.value(QStringLiteral("characters")).toArray();
    for (const QJsonValue value : characters) {
        const QJsonObject character = value.toObject();
        if (character.value(QStringLiteral("id")).toString() == id) {
            return character;
        }
    }
    return {};
}

void StoryIntelligenceController::appendHistory(const QString &role, const QString &content, const QString &speaker)
{
    QJsonObject message;
    message.insert(QStringLiteral("role"), role);
    message.insert(QStringLiteral("content"), content);
    if (!speaker.isEmpty()) {
        message.insert(QStringLiteral("speaker"), speaker);
    }
    m_history.append(message);
    while (m_history.size() > MaximumHistoryMessages) {
        m_history.removeFirst();
    }
}

QJsonArray StoryIntelligenceController::boundedHistory() const
{
    return m_history;
}

QString StoryIntelligenceController::currentDocumentPath() const
{
    if (auto *document = qobject_cast<MarkdownDocument *>(m_editor->document())) {
        return document->filePath();
    }
    return {};
}

void StoryIntelligenceController::sendChat(const QString &message)
{
    if (!m_chatRequestId.isEmpty() || m_pendingChat.waitingForCredential) {
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
    m_widget->appendChatMessage(QStringLiteral("user"), prompt);
    appendHistory(QStringLiteral("user"), prompt);
    m_widget->setBusy(true);

    m_pendingChat = {};
    m_pendingChat.prompt = prompt;
    m_pendingChat.provider = providerSettings();
    m_pendingChat.credentialId = providerCredentialId(m_pendingChat.provider);
    m_pendingChat.revision = m_revision;

    QSettings settings;
    settings.beginGroup(QStringLiteral("prose/provider"));
    const bool hasCredential = settings.value(QStringLiteral("has_credential"), false).toBool();
    settings.endGroup();
    if (hasCredential && m_credentials->isAvailable()) {
        m_pendingChat.waitingForCredential = true;
        m_credentials->read(m_pendingChat.credentialId);
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
    dispatchPendingChat(secret);
}

void StoryIntelligenceController::handleCredentialError(const QString &credentialId, const QString &message)
{
    if (!m_pendingChat.waitingForCredential || credentialId != m_pendingChat.credentialId) {
        return;
    }
    m_pendingChat.waitingForCredential = false;
    m_widget->setBusy(false);
    m_widget->setStatusMessage(tr("Secure API key could not be loaded."));
    if (providerMayNeedCredential(m_pendingChat.provider)) {
        MessageBoxHelper::warning(m_widget, tr("API key unavailable"), message);
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
    QJsonObject provider = m_pendingChat.provider;
    if (!apiKey.isEmpty()) {
        provider.insert(QStringLiteral("api_key"), apiKey);
    }

    QJsonArray history = boundedHistory();
    if (!history.isEmpty()) {
        const QJsonObject last = history.last().toObject();
        if (last.value(QStringLiteral("role")).toString() == QStringLiteral("user")
            && last.value(QStringLiteral("content")).toString() == m_pendingChat.prompt) {
            history.removeLast();
        }
    }

    QJsonObject storyPayload;
    storyPayload.insert(QStringLiteral("kind"), QStringLiteral("story_intelligence_v1"));
    storyPayload.insert(QStringLiteral("prompt"), m_pendingChat.prompt);
    storyPayload.insert(QStringLiteral("document"), m_editor->toPlainText());
    storyPayload.insert(QStringLiteral("document_path"), currentDocumentPath());
    storyPayload.insert(QStringLiteral("document_revision"), m_pendingChat.revision);
    storyPayload.insert(QStringLiteral("project_root"), m_projectRoot);
    storyPayload.insert(QStringLiteral("scene_context"), m_metadata.value(QStringLiteral("scene_context")).toObject());
    storyPayload.insert(QStringLiteral("characters"), m_metadata.value(QStringLiteral("characters")).toArray());
    storyPayload.insert(QStringLiteral("active_character"), activeCharacter());
    storyPayload.insert(QStringLiteral("history"), history);

    QJsonObject request;
    request.insert(QStringLiteral("text"), QString::fromUtf8(QJsonDocument(storyPayload).toJson(QJsonDocument::Compact)));
    request.insert(QStringLiteral("profile"), QSettings().value(QStringLiteral("prose/profile"), QStringLiteral("creative-default")).toString());
    request.insert(QStringLiteral("mode"), QStringLiteral("write_from_brief"));
    request.insert(QStringLiteral("passes"), 1);
    request.insert(QStringLiteral("persist"), false);
    request.insert(QStringLiteral("provider"), provider);

    m_chatRequestId = m_engine->send(QStringLiteral("rewrite"), request);
    if (m_chatRequestId.isEmpty()) {
        m_widget->setBusy(false);
        m_widget->setStatusMessage(tr("Could not start Story Intelligence request."));
    }
}

void StoryIntelligenceController::handleResponse(const QString &requestId, const QJsonObject &response)
{
    if (requestId != m_chatRequestId) {
        return;
    }
    m_chatRequestId.clear();
    m_widget->setBusy(false);

    if (!response.value(QStringLiteral("ok")).toBool()) {
        QString error = response.value(QStringLiteral("error")).toString();
        if (error.isEmpty() && response.value(QStringLiteral("error")).isObject()) {
            error = response.value(QStringLiteral("error")).toObject().value(QStringLiteral("message")).toString();
        }
        m_widget->setStatusMessage(error.isEmpty() ? tr("Story Intelligence request failed.") : error);
        return;
    }

    const QJsonObject result = response.value(QStringLiteral("result")).toObject();
    const QJsonObject story = result.value(QStringLiteral("story_intelligence")).toObject();
    QString message = story.value(QStringLiteral("message")).toString().trimmed();
    if (message.isEmpty()) {
        message = result.value(QStringLiteral("output_text")).toString().trimmed();
    }
    if (message.isEmpty()) {
        m_widget->setStatusMessage(tr("The model returned no Story Intelligence message."));
        return;
    }

    const QJsonObject persona = activeCharacter();
    const QString speaker = persona.value(QStringLiteral("name")).toString();
    m_widget->appendChatMessage(QStringLiteral("assistant"), message, speaker);
    appendHistory(QStringLiteral("assistant"), message, speaker);

    const QJsonArray annotations = story.value(QStringLiteral("annotations")).toArray();
    applyAnnotations(annotations, m_pendingChat.revision);
    m_pendingChat = {};
    m_widget->setStatusMessage(annotations.isEmpty()
        ? tr("Response complete")
        : tr("Response complete · %1 manuscript mark(s)").arg(annotations.size()));
}

void StoryIntelligenceController::applyAnnotations(const QJsonArray &annotations, int responseRevision)
{
    if (responseRevision != m_revision || annotations.isEmpty()) {
        if (responseRevision != m_revision && !annotations.isEmpty()) {
            m_widget->setStatusMessage(tr("The document changed while AI was responding; stale manuscript marks were not applied."));
        }
        return;
    }
    QHash<int, QList<QTextLayout::FormatRange>> formatsByBlock;
    QTextDocument *document = m_editor->document();
    for (const QJsonValue value : annotations) {
        const QJsonObject annotation = value.toObject();
        const int start = annotation.value(QStringLiteral("start_utf16")).toInt(-1);
        const int end = annotation.value(QStringLiteral("end_utf16")).toInt(-1);
        if (start < 0 || end <= start || end > document->characterCount()) {
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
    }
    if (!formatsByBlock.isEmpty()) {
        m_editor->textFormatOverlayController()->replaceChannelFormats(StoryOverlayChannel, formatsByBlock);
        m_annotations = annotations;
    }
}

void StoryIntelligenceController::clearAnnotations()
{
    m_editor->textFormatOverlayController()->clearChannel(StoryOverlayChannel);
    m_annotations = {};
    m_widget->setStatusMessage(tr("AI manuscript marks cleared"));
}
}
