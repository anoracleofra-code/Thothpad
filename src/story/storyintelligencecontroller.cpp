/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "storyintelligencecontroller.h"

#include "agentedittransactionmanager.h"
#include "documentactivitytracker.h"
#include "storyintelligencewidget.h"
#include "storytoolharness.h"
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
#include <QJsonValue>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QRegularExpression>
#include <QSaveFile>
#include <QSet>
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
constexpr int MaximumToolCallsPerRound = 8;
constexpr int MaximumToolRounds = 4;
constexpr int ToolPollIntervalMs = 200;
constexpr int ToolWaitTimeoutMs = 120000;
constexpr int MaximumAccumulatedToolResults = 32;

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
    } else if (category == QStringLiteral("continuity")
               || category == QStringLiteral("research")
               || category == QStringLiteral("rewrite")) {
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

    m_toolWaitTimer.setInterval(ToolPollIntervalMs);
    m_toolWaitTimer.setSingleShot(false);
    connect(&m_toolWaitTimer, &QTimer::timeout,
            this, &StoryIntelligenceController::pollPendingTool);

    connect(m_widget, &StoryIntelligenceWidget::projectFolderRequested,
            this, &StoryIntelligenceController::chooseProjectFolder);
    connect(m_widget, &StoryIntelligenceWidget::modelSettingsRequested,
            this, &StoryIntelligenceController::openModelSettings);
    connect(m_widget, &StoryIntelligenceWidget::editSceneRequested,
            this, &StoryIntelligenceController::editSceneContext);
    connect(m_widget, &StoryIntelligenceWidget::addCharacterRequested,
            this, &StoryIntelligenceController::addCharacter);
    connect(m_widget, &StoryIntelligenceWidget::editCharactersRequested,
            this, &StoryIntelligenceController::editCharacters);
    connect(m_widget, &StoryIntelligenceWidget::chatRequested,
            this, &StoryIntelligenceController::sendChat);
    connect(m_widget, &StoryIntelligenceWidget::clearAnnotationsRequested,
            this, &StoryIntelligenceController::clearAnnotations);
    connect(m_widget, &StoryIntelligenceWidget::applySuggestionRequested,
            this, &StoryIntelligenceController::applySuggestion);
    connect(m_widget, &StoryIntelligenceWidget::dismissSuggestionRequested,
            this, &StoryIntelligenceController::dismissSuggestion);
    connect(m_widget, &StoryIntelligenceWidget::characterActivated, this, [this](const QString &id) {
        QSettings().setValue(QStringLiteral("story/activeCharacter"), id);
    });

    connect(m_engine, &WriterEngineClient::responseReceived,
            this, &StoryIntelligenceController::handleResponse);
    connect(m_engine, &WriterEngineClient::requestsInvalidated, this, [this](const QStringList &ids) {
        const bool chatInvalidated = !m_chatRequestId.isEmpty() && ids.contains(m_chatRequestId);
        if (!chatInvalidated && !m_pendingChat.asyncTool.active) {
            return;
        }
        m_chatRequestId.clear();
        m_toolWaitTimer.stop();
        resetPendingChat();
        m_widget->setBusy(false);
        m_widget->setStatusMessage(tr("Engine restarted; resend the last message."));
    });
    connect(m_credentials, &CredentialStore::loaded,
            this, &StoryIntelligenceController::handleCredentialLoaded);
    connect(m_credentials, &CredentialStore::error,
            this, &StoryIntelligenceController::handleCredentialError);
    connect(m_editor->document(), &QTextDocument::contentsChange, this, [this](int, int, int) {
        ++m_revision;
    });
    if (auto *document = qobject_cast<MarkdownDocument *>(m_editor->document())) {
        connect(document, &MarkdownDocument::filePathChanged, this, [this]() {
            ++m_revision;
            const bool hadPendingTurn = !m_chatRequestId.isEmpty()
                || m_pendingChat.waitingForCredential
                || m_pendingChat.asyncTool.active
                || !m_pendingChat.prompt.isEmpty();
            if (!m_chatRequestId.isEmpty()) {
                m_engine->cancel(m_chatRequestId);
                m_chatRequestId.clear();
            }
            resetPendingChat();
            m_widget->setBusy(false);
            m_editor->textFormatOverlayController()->clearChannel(StoryOverlayChannel);
            m_annotations = {};
            m_widget->setAnnotations({});
            if (hadPendingTurn) {
                m_widget->setStatusMessage(
                    tr("Document changed; the previous Story Intelligence turn was discarded."));
            }
        });
    }
}

void StoryIntelligenceController::setToolServices(
    StoryToolHarness *harness,
    AgentEditTransactionManager *transactions,
    DocumentActivityTracker *activity)
{
    m_harness = harness;
    m_transactions = transactions;
    m_activity = activity;
    if (m_transactions) {
        m_transactions->setProjectRoot(m_projectRoot);
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
    const QJsonObject providerConfig = providerSettings();
    QSettings settings;
    settings.beginGroup(QStringLiteral("prose/provider"));
    const QString savedCredentialId = settings.value(QStringLiteral("credential_id")).toString();
    settings.endGroup();
    const QString expectedCredentialId = providerCredentialId(providerConfig);
    m_widget->setProviderSummary(
        providerDisplayName(providerConfig.value(QStringLiteral("provider")).toString()),
        providerConfig.value(QStringLiteral("model")).toString(),
        !savedCredentialId.isEmpty() && savedCredentialId == expectedCredentialId);
}

QJsonObject StoryIntelligenceController::providerSettings() const
{
    QSettings settings;
    settings.beginGroup(QStringLiteral("prose/provider"));
    QJsonObject provider;
    provider.insert(QStringLiteral("provider"),
                    settings.value(QStringLiteral("provider"), QStringLiteral("openai_compatible")).toString());
    provider.insert(QStringLiteral("base_url"),
                    settings.value(QStringLiteral("endpoint"), QStringLiteral("http://127.0.0.1:1234/v1")).toString());
    provider.insert(QStringLiteral("model"),
                    settings.value(QStringLiteral("model"), QStringLiteral("local-model")).toString());
    provider.insert(QStringLiteral("temperature"),
                    settings.value(QStringLiteral("temperature"), 0.7).toDouble());
    provider.insert(QStringLiteral("max_tokens"),
                    settings.value(QStringLiteral("max_tokens"), 4096).toInt());
    provider.insert(QStringLiteral("timeout"),
                    settings.value(QStringLiteral("timeout"), 180).toInt());
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
    if (kind == QStringLiteral("ollama")
        || kind == QStringLiteral("lmstudio")
        || kind == QStringLiteral("llama_cpp")) {
        return false;
    }
    const QUrl endpoint(provider.value(QStringLiteral("base_url")).toString());
    return !(endpoint.host() == QStringLiteral("127.0.0.1")
             || endpoint.host() == QStringLiteral("localhost")
             || endpoint.host() == QStringLiteral("::1"));
}

void StoryIntelligenceController::openModelSettings()
{
    auto *dialog = new ProviderSettingsDialog(m_credentials, m_widget);
    dialog->setAttribute(Qt::WA_DeleteOnClose);
    connect(dialog, &QDialog::accepted,
            this, &StoryIntelligenceController::refreshProviderSummary);
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
    const QString nextProjectRoot = QDir::cleanPath(canonical);
    if (nextProjectRoot != m_projectRoot) {
        if (!m_chatRequestId.isEmpty()) {
            m_engine->cancel(m_chatRequestId);
            m_chatRequestId.clear();
        }
        resetPendingChat();
        m_widget->setBusy(false);
    }
    m_projectRoot = nextProjectRoot;
    QSettings().setValue(QStringLiteral("story/projectRoot"), m_projectRoot);
    if (m_transactions) {
        m_transactions->setProjectRoot(m_projectRoot);
    }
    emit projectRootChanged(m_projectRoot);
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
                    m_metadata.insert(QStringLiteral("scene_context"),
                                      loaded.value(QStringLiteral("scene_context")));
                }
                if (loaded.value(QStringLiteral("characters")).isArray()) {
                    m_metadata.insert(QStringLiteral("characters"),
                                      loaded.value(QStringLiteral("characters")));
                }
            } else {
                m_widget->setStatusMessage(
                    tr("Project metadata is invalid; using a blank Story Intelligence state."));
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
    const QString selected = QInputDialog::getItem(
        m_widget, tr("Edit Character"), tr("Character"), names, 0, false, &ok);
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

void StoryIntelligenceController::appendHistory(
    const QString &role,
    const QString &content,
    const QString &speaker)
{
    QJsonObject message;
    message.insert(QStringLiteral("role"), role);
    message.insert(QStringLiteral("content"), content);
    if (!speaker.isEmpty()) {
        message.insert(QStringLiteral("speaker"), speaker);
    }
    m_history.append(message);
    while (m_history.size() > MaximumHistoryMessages) {
        m_history.removeAt(0);
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

QString StoryIntelligenceController::modelSafePath(const QString &path) const
{
    if (path.isEmpty()) {
        return {};
    }
    const QFileInfo file(path);
    if (!m_projectRoot.isEmpty()) {
        const QDir root(m_projectRoot);
        const QString relative = QDir::fromNativeSeparators(
            root.relativeFilePath(file.absoluteFilePath()));
        if (!relative.isEmpty()
            && relative != QStringLiteral("..")
            && !relative.startsWith(QStringLiteral("../"))
            && !QDir::isAbsolutePath(relative)) {
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
            result.insert(QStringLiteral("checkpoint_created"),
                          value.isString() && !value.toString().isEmpty());
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
                } else if (item.isString()
                           && key.endsWith(QStringLiteral("paths"))
                           && QDir::isAbsolutePath(item.toString())) {
                    safeArray.append(modelSafePath(item.toString()));
                } else {
                    safeArray.append(item);
                }
            }
            result.insert(key, safeArray);
            continue;
        }
        if (value.isString()
            && key.endsWith(QStringLiteral("path"))
            && QDir::isAbsolutePath(value.toString())) {
            result.insert(key, modelSafePath(value.toString()));
            continue;
        }
        result.insert(key, value);
    }
    return result;
}

void StoryIntelligenceController::sendChat(const QString &message)
{
    if (!m_chatRequestId.isEmpty()
        || m_pendingChat.waitingForCredential
        || m_pendingChat.asyncTool.active) {
        m_widget->setStatusMessage(
            tr("Wait for the current response before sending another message."));
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

    resetPendingChat();
    m_pendingChat.prompt = prompt;
    m_pendingChat.provider = providerSettings();
    m_pendingChat.credentialId = providerCredentialId(m_pendingChat.provider);
    m_pendingChat.revision = m_revision;

    QSettings settings;
    settings.beginGroup(QStringLiteral("prose/provider"));
    const QString savedCredentialId = settings.value(QStringLiteral("credential_id")).toString();
    settings.endGroup();
    if (!savedCredentialId.isEmpty()
        && savedCredentialId == m_pendingChat.credentialId
        && m_credentials->isAvailable()) {
        m_pendingChat.waitingForCredential = true;
        m_credentials->read(m_pendingChat.credentialId);
        return;
    }
    dispatchPendingChat();
}

void StoryIntelligenceController::handleCredentialLoaded(
    const QString &credentialId,
    const QString &secret)
{
    if (!m_pendingChat.waitingForCredential
        || credentialId != m_pendingChat.credentialId) {
        return;
    }
    m_pendingChat.waitingForCredential = false;
    m_pendingChat.apiKey = secret;
    dispatchPendingChat(secret);
}

void StoryIntelligenceController::handleCredentialError(
    const QString &credentialId,
    const QString &message)
{
    if (!m_pendingChat.waitingForCredential
        || credentialId != m_pendingChat.credentialId) {
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
    if (m_pendingChat.asyncTool.active) {
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
    // current revision/document are sampled again for every model turn.
    m_pendingChat.revision = m_revision;

    QJsonObject storyPayload;
    storyPayload.insert(QStringLiteral("kind"), QStringLiteral("story_intelligence_v1"));
    storyPayload.insert(QStringLiteral("prompt"), m_pendingChat.prompt);
    storyPayload.insert(QStringLiteral("document"), m_editor->toPlainText());
    // document_path/project_root are consumed only by the local engine's
    // retrieval layer. build_story_messages does not forward them to the model.
    storyPayload.insert(QStringLiteral("document_path"), currentDocumentPath());
    storyPayload.insert(QStringLiteral("document_revision"), m_pendingChat.revision);
    storyPayload.insert(QStringLiteral("project_root"), m_projectRoot);
    storyPayload.insert(QStringLiteral("scene_context"),
                        m_metadata.value(QStringLiteral("scene_context")).toObject());
    storyPayload.insert(QStringLiteral("characters"),
                        m_metadata.value(QStringLiteral("characters")).toArray());
    storyPayload.insert(QStringLiteral("active_character"), activeCharacter());
    storyPayload.insert(QStringLiteral("history"), history);
    storyPayload.insert(QStringLiteral("tool_round"), m_pendingChat.toolRound);
    storyPayload.insert(QStringLiteral("tool_results"), m_pendingChat.toolResults);
    if (m_harness) {
        storyPayload.insert(QStringLiteral("app_state"), m_harness->snapshot());
        storyPayload.insert(QStringLiteral("tool_manifest"), m_harness->manifest());
    }
    if (m_activity) {
        storyPayload.insert(QStringLiteral("activity_events"), m_activity->recentEvents());
    }

    QJsonObject request;
    request.insert(QStringLiteral("text"),
                   QString::fromUtf8(QJsonDocument(storyPayload).toJson(QJsonDocument::Compact)));
    request.insert(QStringLiteral("profile"),
                   QSettings().value(QStringLiteral("prose/profile"),
                                     QStringLiteral("creative-default")).toString());
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

QString StoryIntelligenceController::toolRisk(const QString &toolId) const
{
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

bool StoryIntelligenceController::authorizeTool(
    const QString &toolId,
    const QJsonObject &arguments)
{
    const QString risk = toolRisk(toolId);
    if (risk == QStringLiteral("R0")
        || risk == QStringLiteral("R1")
        || risk == QStringLiteral("R2")) {
        return true;
    }
    if (risk != QStringLiteral("R3") && risk != QStringLiteral("R4")) {
        return false;
    }

    QString summary = arguments.value(QStringLiteral("summary")).toString().trimmed();
    if (toolId == QStringLiteral("apply_objective_grammar_fixes") && m_harness) {
        const QJsonObject prose = m_harness->snapshot().value(QStringLiteral("prose")).toObject();
        const int reported = prose.value(QStringLiteral("counts")).toObject()
                                 .value(QStringLiteral("grammar_mechanics")).toInt();
        int deterministic = 0;
        for (const QJsonValue &value : prose.value(QStringLiteral("findings")).toArray()) {
            const QJsonObject finding = value.toObject();
            if (finding.value(QStringLiteral("category")).toString() == QStringLiteral("grammar_mechanics")
                && finding.value(QStringLiteral("level")).toString() == QStringLiteral("strong_flag")
                && !finding.value(QStringLiteral("replacements")).toArray().isEmpty()) {
                ++deterministic;
            }
        }
        summary = tr("Apply %1 currently verified objective grammar fix(es) from %2 grammar/mechanics finding(s)")
                      .arg(deterministic)
                      .arg(reported);
    } else if (summary.isEmpty()) {
        summary = toolDisplayName(toolId);
    }

    const bool bulk = risk == QStringLiteral("R4");
    const QString detail = bulk
        ? tr("Story Intelligence wants to make a bulk manuscript change:\n\n%1\n\n"
             "ThothPad will create a durable recovery checkpoint and group the operation into one Undo step. Allow this bulk edit?").arg(summary)
        : tr("Story Intelligence wants to edit the manuscript:\n\n%1\n\n"
             "ThothPad will create a recovery checkpoint and one Undo step. Allow this edit?").arg(summary);
    return QMessageBox::Yes == QMessageBox::question(
        m_widget,
        bulk ? tr("Allow bulk AI edit?") : tr("Allow AI edit?"),
        detail,
        QMessageBox::Yes | QMessageBox::No,
        QMessageBox::No);
}

void StoryIntelligenceController::beginPendingTool(
    const QString &callId,
    const QString &toolId,
    const QJsonObject &nativeResult)
{
    PendingAsyncTool wait;
    wait.active = true;
    wait.callId = callId;
    wait.toolId = toolId;
    wait.waitKind = nativeResult.value(QStringLiteral("wait_kind")).toString();
    wait.category = nativeResult.value(QStringLiteral("category")).toString();
    wait.baselineAnalysisId = nativeResult.value(QStringLiteral("baseline_analysis_id")).toString();
    wait.targetGeneration = static_cast<qint64>(
        nativeResult.value(QStringLiteral("target_generation")).toDouble());
    wait.revision = m_revision;
    m_pendingChat.asyncTool = wait;
    m_toolWaitTimer.start();

    if (wait.waitKind == QStringLiteral("analysis_snapshot")) {
        m_widget->setStatusMessage(tr("Scanning manuscript · waiting for fresh prose evidence…"));
    } else if (wait.waitKind == QStringLiteral("category_hydration")) {
        m_widget->setStatusMessage(
            tr("Loading %1 findings · then the co-author will continue…").arg(wait.category));
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
        const qint64 generation = static_cast<qint64>(
            state.value(QStringLiteral("analysis_generation")).toDouble());
        const QString analysisId = state.value(QStringLiteral("analysis_id")).toString();
        return generation >= wait.targetGeneration
            && !analysisId.isEmpty()
            && analysisId != wait.baselineAnalysisId;
    }
    if (wait.waitKind == QStringLiteral("category_hydration")) {
        return jsonArrayContainsString(
            state.value(QStringLiteral("hydrated_categories")).toArray(),
            wait.category);
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
        nativeResult.insert(QStringLiteral("prose"),
                            m_harness->snapshot().value(QStringLiteral("prose")));
        toolResult.insert(QStringLiteral("result"), modelSafeToolResult(nativeResult));
    } else {
        toolResult.insert(QStringLiteral("error"),
                          error.isEmpty() ? tr("The native tool did not complete.") : error);
    }

    m_pendingChat.toolResults.append(toolResult);
    while (m_pendingChat.toolResults.size() > MaximumAccumulatedToolResults) {
        m_pendingChat.toolResults.removeAt(0);
    }

    m_widget->setStatusMessage(completed
        ? tr("Native evidence ready · resuming co-author…")
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
        finishPendingTool(
            false,
            tr("The manuscript changed while the native tool was running, so its pending result was discarded as stale."));
        return;
    }
    if (pendingToolCompleted()) {
        finishPendingTool(true);
        return;
    }
    if (m_pendingChat.asyncTool.elapsedMs >= ToolWaitTimeoutMs) {
        finishPendingTool(
            false,
            tr("The native tool did not complete within %1 seconds.")
                .arg(ToolWaitTimeoutMs / 1000));
    }
}

bool StoryIntelligenceController::executeToolCalls(const QJsonArray &toolCalls)
{
    if (!m_harness || toolCalls.isEmpty()) {
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
            callId = QStringLiteral("r%1-c%2")
                         .arg(m_pendingChat.toolRound)
                         .arg(index + 1);
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

        const bool boundedEdit = risk == QStringLiteral("R3") || risk == QStringLiteral("R4");
        const bool bulkEdit = risk == QStringLiteral("R4");
        const QJsonObject nativeResult =
            m_harness->execute(toolId, arguments, boundedEdit, bulkEdit);
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
                deferred.insert(QStringLiteral("call_id"),
                                deferredCall.value(QStringLiteral("call_id")).toString());
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
            toolResult.insert(QStringLiteral("error"),
                              nativeResult.value(QStringLiteral("error")).toString(
                                  tr("Native tool execution failed.")));
        }
        results.append(toolResult);
    }

    if (toolCalls.size() > MaximumToolCallsPerRound) {
        QJsonObject truncated;
        truncated.insert(QStringLiteral("ok"), false);
        truncated.insert(QStringLiteral("error"),
                         tr("Additional tool calls were rejected because the per-round limit is %1.")
                             .arg(MaximumToolCallsPerRound));
        results.append(truncated);
    }
    while (results.size() > MaximumAccumulatedToolResults) {
        results.removeAt(0);
    }
    m_pendingChat.toolResults = results;
    return true;
}

void StoryIntelligenceController::handleResponse(
    const QString &requestId,
    const QJsonObject &response)
{
    if (requestId != m_chatRequestId) {
        return;
    }
    m_chatRequestId.clear();

    if (!response.value(QStringLiteral("ok")).toBool()) {
        QString error = response.value(QStringLiteral("error")).toString();
        if (error.isEmpty() && response.value(QStringLiteral("error")).isObject()) {
            error = response.value(QStringLiteral("error")).toObject()
                .value(QStringLiteral("message")).toString();
        }
        m_widget->setBusy(false);
        m_widget->setStatusMessage(
            error.isEmpty() ? tr("Story Intelligence request failed.") : error);
        resetPendingChat();
        return;
    }

    const QJsonObject result = response.value(QStringLiteral("result")).toObject();
    const QJsonObject story = result.value(QStringLiteral("story_intelligence")).toObject();
    const QJsonArray toolCalls = story.value(QStringLiteral("tool_calls")).toArray();

    if (!toolCalls.isEmpty() && m_harness) {
        if (m_pendingChat.toolRound >= MaximumToolRounds) {
            QJsonObject limitedStory = story;
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
        if (m_pendingChat.asyncTool.active) {
            // The same co-author turn resumes from pollPendingTool only after
            // native ThothPad reports real completion or a bounded failure.
            return;
        }
        m_widget->setStatusMessage(
            tr("Using ThothPad tools · round %1/%2")
                .arg(m_pendingChat.toolRound)
                .arg(MaximumToolRounds));
        dispatchPendingChat();
        return;
    }

    finishChatTurn(story, result);
}

void StoryIntelligenceController::finishChatTurn(
    const QJsonObject &story,
    const QJsonObject &result)
{
    QString message = story.value(QStringLiteral("message")).toString().trimmed();
    if (message.isEmpty()) {
        message = result.value(QStringLiteral("output_text")).toString().trimmed();
    }
    if (message.isEmpty()) {
        message = tr("Story Intelligence completed the turn without a text response.");
    }

    const QJsonObject persona = activeCharacter();
    const QString speaker = persona.value(QStringLiteral("name")).toString();
    m_widget->appendChatMessage(QStringLiteral("assistant"), message, speaker);
    appendHistory(QStringLiteral("assistant"), message, speaker);

    const QJsonArray annotations = story.value(QStringLiteral("annotations")).toArray();
    applyAnnotations(annotations, m_pendingChat.revision);
    m_widget->setBusy(false);
    m_widget->setStatusMessage(annotations.isEmpty()
        ? tr("Response complete")
        : tr("Response complete · %1 manuscript mark(s)").arg(annotations.size()));
    resetPendingChat();
}

void StoryIntelligenceController::applyAnnotations(
    const QJsonArray &annotations,
    int responseRevision)
{
    if (responseRevision != m_revision) {
        if (!annotations.isEmpty()) {
            m_widget->setStatusMessage(
                tr("The document changed while AI was responding; stale manuscript marks were not applied."));
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
        if (start < 0
            || end <= start
            || end > document->characterCount() - 1
            || quote.size() != end - start
            || m_editor->toPlainText().mid(start, end - start) != quote) {
            continue;
        }

        QTextCharFormat format;
        format.setBackground(annotationColor(
            annotation.value(QStringLiteral("category")).toString()));
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

    m_editor->textFormatOverlayController()->replaceChannelFormats(
        StoryOverlayChannel, formatsByBlock);
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
        QJsonObject edit;
        edit.insert(QStringLiteral("start_utf16"), annotation.value(QStringLiteral("start_utf16")));
        edit.insert(QStringLiteral("end_utf16"), annotation.value(QStringLiteral("end_utf16")));
        edit.insert(QStringLiteral("expected"), annotation.value(QStringLiteral("quote")));
        edit.insert(QStringLiteral("replacement"), replacement);
        QJsonArray edits;
        edits.append(edit);
        const QJsonObject result = m_transactions->applyVerifiedReplacements(
            edits,
            tr("Apply Story Intelligence rewrite"),
            QStringLiteral("apply_story_suggestion"));
        if (!result.value(QStringLiteral("ok")).toBool()) {
            m_widget->setStatusMessage(
                result.value(QStringLiteral("error")).toString(tr("Could not apply suggestion.")));
            return;
        }
        if (m_activity) {
            m_activity->noteSuggestionDecision(
                QStringLiteral("USER_ACCEPTED_SUGGESTION"),
                suggestionId,
                annotation.value(QStringLiteral("comment")).toString());
        }
        m_widget->appendChatMessage(
            QStringLiteral("assistant"),
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
        m_activity->noteSuggestionDecision(
            QStringLiteral("USER_REJECTED_SUGGESTION"),
            suggestionId,
            dismissed.value(QStringLiteral("comment")).toString());
    }
    m_annotations = filtered;
    refreshAnnotationPresentation();
    m_widget->appendChatMessage(
        QStringLiteral("assistant"),
        tr("You dismissed that suggestion. I'll treat that as preference evidence, not a permanent rule."),
        QStringLiteral("ThothPad"));
}

void StoryIntelligenceController::clearAnnotations()
{
    m_editor->textFormatOverlayController()->clearChannel(StoryOverlayChannel);
    m_annotations = {};
    m_widget->setAnnotations({});
    m_widget->setStatusMessage(tr("AI manuscript marks cleared"));
}

void StoryIntelligenceController::resetPendingChat()
{
    m_toolWaitTimer.stop();
    if (!m_pendingChat.apiKey.isEmpty()) {
        m_pendingChat.apiKey.fill(QChar('\0'));
    }
    m_pendingChat = {};
}
}
