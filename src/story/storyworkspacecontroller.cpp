// SPDX-License-Identifier: GPL-3.0-or-later
#include "../editor/markdowndocument.h"
#include "../editor/markdowneditor.h"
#include "../editor/textformatoverlaycontroller.h"
#include "../markdown/markdownnode.h"
#include "../prose/writerengineclient.h"
#include "storyintelligencecontroller.h"
#include "storyintelligencewidget.h"
#include "storytoolharness.h"
#include "storyworkspacedialog.h"
#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QMessageBox>
#include <QInputDialog>
#include <QSettings>
#include <QStandardPaths>
#include <QTextBlock>
#include <cmath>

namespace ghostwriter
{
namespace
{
QString documentDigest(const QString &text)
{
    return QString::fromLatin1(QCryptographicHash::hash(text.toUtf8(), QCryptographicHash::Sha256).toHex());
}
QJsonObject modelAgent(QJsonObject agent)
{
    QJsonObject safe;
    for (const auto *key : {"id", "name", "kind", "role", "summary", "instructions", "voice", "knowledge", "goals", "boundaries", "sources", "memory_policy"})
        if (agent.contains(key))
            safe.insert(key, agent.value(key));
    return safe;
}
}

StoryIntelligenceController::~StoryIntelligenceController()
{
    // Every user operation already saved; retain reconciled headings on shutdown.
    if (m_workspace.writable()) {
        QString error;
        m_workspace.save(&error);
    }
}

bool StoryIntelligenceController::saveWorkspace()
{
    if (m_loadingWorkspace || !m_started)
        return false;
    QString error;
    if (m_workspace.save(&error))
        return true;
    m_widget->setStatusMessage(tr("Story changes are not saved: %1").arg(error));
    return false;
}

QJsonObject StoryIntelligenceController::reviewWorkspace()
{
    openWorkspaceForDocument();
    m_editor->ensureDocumentParsed();
    reconcileWorkspaceHeadings();
    return m_workspace.data;
}

bool StoryIntelligenceController::saveWritingReview(const QString &workspaceId, const QJsonObject &review)
{
    openWorkspaceForDocument();
    if (workspaceId != m_workspace.data.value("id").toString())
        return false;
    const auto before = m_workspace.data;
    m_workspace.data.insert("writing_review", review);
    if (saveWorkspace())
        return true;
    m_workspace.data = before;
    return false;
}

bool StoryIntelligenceController::saveDetectedCharacter(const QString &workspaceId, const QString &name, const QString &detectedId)
{
    openWorkspaceForDocument();
    if (workspaceId != m_workspace.data.value("id").toString() || name.trimmed().isEmpty())
        return false;
    const auto before = m_workspace.data;
    auto character = StoryWorkspace::agentTemplate("character");
    character.insert("name", name.trimmed().left(200));
    m_workspace.putAgent(character);
    auto review = m_workspace.data.value("writing_review").toObject();
    auto assignments = review.value("assignments").toObject();
    for (auto it = assignments.begin(); it != assignments.end(); ++it)
        if (it.value().toString() == detectedId) it.value() = character.value("id");
    review.insert("assignments", assignments);
    m_workspace.data.insert("writing_review", review);
    if (!saveWorkspace()) { m_workspace.data = before; return false; }
    refreshWorkspace();
    return true;
}

void StoryIntelligenceController::openWorkspaceForDocument()
{
    const QString document = currentDocumentPath();
    if (!m_workspace.path().isEmpty() && m_workspaceDocument == document && !m_documentCleared)
        return;
    m_loadingWorkspace = true;
    if (!m_chatRequestId.isEmpty())
        m_engine->cancel(m_chatRequestId);
    m_chatRequestId.clear();
    resetPendingChat();
    m_widget->setBusy(false);
    const bool saveAs = !m_workspace.path().isEmpty() && !m_documentCleared && m_workspaceDocument != document;
    const auto previous = m_workspace.data;
    QString path;
    if (document.isEmpty()) {
        QSettings settings;
        QString draft = settings.value("story/draftWorkspace").toString();
        if (draft.isEmpty() || m_documentCleared) {
            draft = StoryWorkspace::newId();
            settings.setValue("story/draftWorkspace", draft);
        }
        path = QDir(QStandardPaths::writableLocation(QStandardPaths::AppDataLocation)).filePath("story-workspaces/draft-" + draft + ".json");
    } else {
        const QFileInfo file(document);
        path = QDir(file.absolutePath()).filePath(".thothpad/" + file.fileName() + ".story.json");
    }
    QString error;
    const bool existed = QFileInfo::exists(path);
    const bool firstWorkspace = m_workspace.path().isEmpty();
    const bool opened = m_workspace.open(path, firstWorkspace ? m_metadata : QJsonObject(), &error);
    if (saveAs && !existed && opened)
        m_workspace.data = previous;
    m_workspaceDocument = document;
    m_documentCleared = false;
    m_scopeMode = m_workspace.data.value("scope_mode").toString("manuscript");
    if (m_scopeMode != "manuscript") m_scopeMode = "chapter";
    m_scopeId = "manuscript";
    m_sessionId = m_workspace.data.value("active_session").toString();
    m_loadingWorkspace = false;
    reconcileWorkspaceHeadings();
    m_scopeId = m_workspace.scopeAt(m_editor->textCursor().position(), m_scopeMode);
    for (const auto &v : m_workspace.data.value("sessions").toArray()) {
        const auto session = v.toObject();
        if (session.value("id").toString() == m_sessionId && session.value("scope_id").toString() != m_scopeId)
            m_sessionId.clear();
    }
    if (m_sessionId.isEmpty()) {
        const auto remembered = m_workspace.data.value("last_sessions").toObject().value(m_scopeId).toString();
        for (const auto &v : m_workspace.data.value("sessions").toArray()) {
            const auto session = v.toObject();
            if (session.value("id").toString() == remembered && session.value("scope_id").toString() == m_scopeId)
                m_sessionId = remembered;
        }
    }
    restoreSession();
    refreshWorkspace();
    restoreMarkers();
    if (!opened)
        m_widget->setStatusMessage(error);
}

void StoryIntelligenceController::reconcileWorkspaceHeadings()
{
    auto *document = qobject_cast<MarkdownDocument *>(m_editor->document());
    if (!document || !document->markdownAST() || !m_editor->isDocumentParsed() || m_workspace.path().isEmpty())
        return;
    const auto nodes = document->markdownAST()->headings();
    QJsonArray headings;
    QStringList parents;
    QList<int> levels;
    QHash<QString, int> occurrences;
    const QString text = m_editor->toPlainText();
    for (int i = 0; i < nodes.size(); ++i) {
        const auto *node = nodes[i];
        const int level = node->headingLevel();
        while (!levels.isEmpty() && levels.last() >= level) {
            levels.removeLast();
            parents.removeLast();
        }
        const QString title = node->text().trimmed();
        const QString base = parents.join('/') + "/" + title;
        const QString path = base + "#" + QString::number(++occurrences[base]);
        const auto block = document->findBlockByNumber(node->startLine() - 1);
        if (!block.isValid())
            continue;
        const int start = block.position();
        int end = text.size() + 1;
        for (int j = i + 1; j < nodes.size(); ++j)
            if (nodes[j]->headingLevel() <= level) {
                end = document->findBlockByNumber(nodes[j]->startLine() - 1).position();
                break;
            }
        const auto after = document->findBlockByNumber(node->endLine());
        const int nextHeading = i + 1 < nodes.size() ? document->findBlockByNumber(nodes[i + 1]->startLine() - 1).position() : text.size();
        const QString opening = after.isValid() ? text.mid(after.position(), qMax(0, nextHeading - after.position())).trimmed().left(256) : QString();
        headings.append(QJsonObject{{"title", title},
                                    {"level", level},
                                    {"heading_path", path},
                                    {"fingerprint", opening.isEmpty() ? QString() : documentDigest(opening)},
                                    {"start", start},
                                    {"end", end}});
        parents.append(title);
        levels.append(level);
    }
    const auto before = m_workspace.data.value("scopes");
    m_workspace.reconcileHeadings(headings);
    if (before != m_workspace.data.value("scopes"))
        saveWorkspace();
}

QJsonObject StoryIntelligenceController::activeAgent() const
{
    auto agent = activeCharacter();
    if (agent.isEmpty())
        agent = m_workspace.agent(m_workspace.effectiveSettings(m_scopeId).value("co_writer").toString());
    if (!agent.isEmpty() && !agent.value("archived").toBool())
        return agent;
    auto fallback = StoryWorkspace::agentTemplate(QStringLiteral("co_writer"));
    fallback.insert("id", "default-co-writer"); // Stable identity in the pending context hash.
    return fallback;
}

QJsonObject StoryIntelligenceController::workspaceContext() const
{
    QJsonObject result;
    const auto agent = activeAgent();
    result.insert("co_writer", modelAgent(agent));
    auto scope = m_workspace.scope(m_scopeId);
    QJsonObject scopeInfo;
    for (const auto *key : {"id", "title", "start", "end", "level"})
        if (scope.contains(key))
            scopeInfo.insert(key, scope.value(key));
    result.insert("scope", scopeInfo);
    result.insert("scene_context", m_workspace.effectiveContext(m_scopeId));
    QJsonArray cast;
    for (const auto &id : m_workspace.effectiveSettings(m_scopeId).value("cast").toArray()) {
        auto record = m_workspace.agent(id.toString());
        if (record.isEmpty() || record.value("archived").toBool())
            continue;
        // Cast records describe public identities. Private knowledge belongs to the speaking agent only.
        record.remove("knowledge");
        record.remove("instructions");
        cast.append(modelAgent(record));
    }
    result.insert("characters", cast);
    QJsonArray memories;
    for (const auto &v : m_workspace.scopedMemories(m_scopeId, {agent.value("id").toString()})) {
        const auto memory = v.toObject();
        if (memory.value("kind").toString() == "session" && memory.value("session_id").toString() != m_sessionId)
            continue;
        memories.append(memory);
    }
    result.insert("memories", memories);
    return result;
}

void StoryIntelligenceController::refreshWorkspace()
{
    if (m_loadingWorkspace || m_workspace.path().isEmpty() || !m_pendingChat.prompt.isEmpty())
        return;
    const auto nextScope = m_workspace.scopeAt(m_editor->textCursor().position(), m_scopeMode);
    const bool changed = nextScope != m_scopeId;
    m_scopeId = nextScope;
    auto settings = m_workspace.effectiveSettings(m_scopeId);
    QJsonArray characters;
    for (const auto &id : settings.value("cast").toArray()) {
        auto record = m_workspace.agent(id.toString());
        if (!record.isEmpty() && !record.value("archived").toBool())
            characters.append(record);
    }
    const auto activeId = m_widget->activeCharacterId();
    m_widget->setCharacters(characters);
    bool activeInCast = false;
    for (const auto &v : characters)
        if (v.toObject().value("id").toString() == activeId)
            activeInCast = true;
    if (!changed)
        m_widget->setActiveCharacter(activeInCast ? activeId : QString());
    if (changed) {
        m_widget->setActiveCharacter(QString());
        // Return to the conversation the author chose, not merely the newest one.
        m_sessionId.clear();
        const auto remembered = m_workspace.data.value("last_sessions").toObject().value(m_scopeId).toString();
        for (const auto &v : m_workspace.data.value("sessions").toArray()) {
            const auto s = v.toObject();
            if (s.value("scope_id").toString() != m_scopeId)
                continue;
            if (s.value("id").toString() == remembered) {
                m_sessionId = s.value("id").toString();
                break;
            }
            if (s.value("character_id").toString().isEmpty())
                m_sessionId = s.value("id").toString();
        }
        restoreSession();
    }
    m_metadata.insert("scene_context", m_workspace.effectiveContext(m_scopeId));
    m_metadata.insert("characters", characters);
    m_widget->setSceneContext(m_metadata.value("scene_context").toObject());
    // A different agent never inherits private conversation history implicitly.
    for (const auto &v : m_workspace.data.value("sessions").toArray()) {
        const auto session = v.toObject();
        if (session.value("id").toString() == m_sessionId
            && (!sessionAvailable(session) || session.value("agent_id") != activeAgent().value("id"))) {
            m_sessionId.clear();
            m_history = {};
            m_widget->clearChat();
            m_workspace.data.remove("active_session");
            break;
        }
    }
    QString title = tr("New conversation");
    for (const auto &v : m_workspace.data.value("sessions").toArray())
        if (v.toObject().value("id").toString() == m_sessionId)
            title = v.toObject().value("title").toString();
    m_widget->setWorkspaceContext(m_scopeMode, m_workspace.scope(m_scopeId).value("title").toString(), activeAgent().value("name").toString(), title, m_workspace.scopeKind(m_scopeId));
    QJsonArray choices;
    const auto sessions = m_workspace.data.value("sessions").toArray();
    for (int i = sessions.size() - 1; i >= 0; --i) {
        const auto session = sessions[i].toObject();
        auto scopeTitle = m_workspace.scope(session.value("scope_id").toString()).value("title").toString();
        scopeTitle.remove(QStringLiteral("**"));
        scopeTitle.remove(QStringLiteral("__"));
        const auto agent = m_workspace.agent(session.value("agent_id").toString());
        const auto name = agent.value("name").toString(tr("Co-Writer"));
        choices.append(QJsonObject{{"id", session.value("id")},
            {"label", tr("%1 · %2 · %3").arg(session.value("title").toString(tr("New conversation")), scopeTitle, name)}});
    }
    m_widget->setSessions(choices, m_sessionId);
    refreshProviderSummary();
}

void StoryIntelligenceController::storeSession()
{
    if (m_loadingWorkspace || (m_history.isEmpty() && m_sessionId.isEmpty()))
        return;
    auto sessions = m_workspace.data.value("sessions").toArray();
    int found = -1;
    for (int i = 0; i < sessions.size(); ++i)
        if (sessions[i].toObject().value("id").toString() == m_sessionId) {
            found = i;
            break;
        }
    QJsonObject session;
    if (found >= 0)
        session = sessions[found].toObject();
    else {
        if (m_history.isEmpty())
            return;
        m_sessionId = StoryWorkspace::newId();
        session = QJsonObject{{"id", m_sessionId},
                              {"scope_id", m_scopeId},
                              {"scope_mode", m_scopeMode},
                              {"character_id", m_widget->activeCharacterId()},
                              {"agent_id", activeAgent().value("id")},
                              {"created", QDateTime::currentDateTimeUtc().toString(Qt::ISODate)},
                              {"title", m_history.first().toObject().value("content").toString().simplified().left(80)}};
    }
    if (session.value("messages").toArray().isEmpty() && !m_history.isEmpty() && !session.contains("forked_from"))
        session.insert("title", m_history.first().toObject().value("content").toString().simplified().left(80));
    session.insert("messages", m_history);
    if (found >= 0)
        sessions[found] = session;
    else
        sessions.append(session);
    m_workspace.data.insert("sessions", sessions);
    m_workspace.data.insert("active_session", m_sessionId);
    auto remembered = m_workspace.data.value("last_sessions").toObject();
    remembered.insert(m_scopeId, m_sessionId);
    m_workspace.data.insert("last_sessions", remembered);
}

void StoryIntelligenceController::restoreSession()
{
    m_history = {};
    m_widget->clearChat();
    for (const auto &v : m_workspace.data.value("sessions").toArray()) {
        const auto session = v.toObject();
        if (session.value("id").toString() != m_sessionId)
            continue;
        if (!sessionAvailable(session)) {
            m_sessionId.clear();
            m_widget->setActiveCharacter(QString());
            break;
        }
        m_history = session.value("messages").toArray();
        m_widget->setActiveCharacter(session.value("character_id").toString());
        for (int i = 0; i < m_history.size(); ++i) {
            auto message = m_history[i].toObject();
            if (message.value("id").toString().isEmpty()) {
                message.insert("id", StoryWorkspace::newId());
                m_history[i] = message;
            }
            m_widget->appendChatMessage(message.value("role").toString(),
                                        message.value("content").toString(),
                                        message.value("speaker").toString(),
                                        message.value("references").toArray(), message.value("id").toString());
            for (const auto &v : message.value("proposals").toArray()) {
                const auto p = v.toObject();
                m_widget->appendProposal(p.value("kind").toString(), p.value("record").toObject());
            }
        }
    }
    m_workspace.data.insert("active_session", m_sessionId);
    storeSession();
    if (!m_sessionId.isEmpty()) {
        auto remembered = m_workspace.data.value("last_sessions").toObject();
        remembered.insert(m_scopeId, m_sessionId);
        m_workspace.data.insert("last_sessions", remembered);
    }
}

void StoryIntelligenceController::newSession()
{
    if (!m_pendingChat.prompt.isEmpty())
        return;
    storeSession();
    m_sessionId = StoryWorkspace::newId();
    m_history = {};
    auto sessions = m_workspace.data.value("sessions").toArray();
    sessions.append(QJsonObject{{"id", m_sessionId}, {"scope_id", m_scopeId},
        {"scope_mode", m_scopeMode}, {"character_id", m_widget->activeCharacterId()},
        {"agent_id", activeAgent().value("id")},
        {"created", QDateTime::currentDateTimeUtc().toString(Qt::ISODate)},
        {"title", tr("New conversation %1").arg(sessions.size() + 1)}, {"messages", QJsonArray()}});
    m_workspace.data.insert("sessions", sessions);
    restoreSession();
    refreshWorkspace();
    saveWorkspace();
}

void StoryIntelligenceController::deleteSession(const QString &sessionId)
{
    if (sessionId.isEmpty() || !m_pendingChat.prompt.isEmpty() || !m_chatRequestId.isEmpty())
        return;
    QJsonObject target;
    for (const auto &value : m_workspace.data.value("sessions").toArray()) {
        const auto session = value.toObject();
        if (session.value("id").toString() == sessionId)
            target = session;
    }
    if (target.isEmpty())
        return;
    if (QMessageBox::question(m_widget, tr("Delete conversation?"),
            tr("Delete “%1” and its saved chat history? Its un-applied manuscript marks will also be removed. Applied manuscript edits and approved memories are kept.")
                .arg(target.value("title").toString()),
            QMessageBox::Yes | QMessageBox::Cancel, QMessageBox::Cancel) != QMessageBox::Yes)
        return;
    const auto previousData = m_workspace.data;
    const auto previousHistory = m_history;
    const auto previousSession = m_sessionId;
    QJsonArray sessions;
    for (const auto &value : previousData.value("sessions").toArray())
        if (value.toObject().value("id").toString() != sessionId)
            sessions.append(value);
    QJsonArray markers;
    for (const auto &value : previousData.value("markers").toArray())
        if (value.toObject().value("session_id").toString() != sessionId)
            markers.append(value);
    m_workspace.data.insert("sessions", sessions);
    m_workspace.data.insert("markers", markers);
    auto remembered = previousData.value("last_sessions").toObject();
    for (auto it = remembered.begin(); it != remembered.end();) {
        if (it.value().toString() == sessionId)
            it = remembered.erase(it);
        else
            ++it;
    }
    m_workspace.data.insert("last_sessions", remembered);
    if (m_sessionId == sessionId) {
        m_sessionId.clear();
        m_history = {};
        for (int i = sessions.size() - 1; i >= 0; --i) {
            const auto candidate = sessions[i].toObject();
            if (candidate.value("scope_id").toString() == m_scopeId && sessionAvailable(candidate)) {
                m_sessionId = candidate.value("id").toString();
                break;
            }
        }
        m_workspace.data.insert("active_session", m_sessionId);
    }
    if (!saveWorkspace()) {
        m_workspace.data = previousData;
        m_history = previousHistory;
        m_sessionId = previousSession;
        return;
    }
    restoreSession();
    restoreMarkers();
    refreshWorkspace();
    if (m_sessionId.isEmpty())
        newSession();
    else
        m_widget->setStatusMessage(tr("Conversation deleted"));
}

bool StoryIntelligenceController::sessionAvailable(const QJsonObject &session) const
{
    const auto scopeId = session.value("scope_id").toString();
    const auto scope = m_workspace.scope(scopeId);
    const auto characterId = session.value("character_id").toString();
    const auto settings = m_workspace.effectiveSettings(scopeId);
    const auto agentId = session.value("agent_id").toString();
    if (session.isEmpty() || scope.isEmpty() || scope.value("orphaned").toBool())
        return false;
    const auto agent = m_workspace.agent(agentId);
    if (!characterId.isEmpty())
        return agentId == characterId && !agent.isEmpty() && !agent.value("archived").toBool()
            && settings.value("cast").toArray().contains(characterId);
    const auto writer = m_workspace.agent(settings.value("co_writer").toString());
    const auto writerId = writer.isEmpty() || writer.value("archived").toBool()
        ? QStringLiteral("default-co-writer") : writer.value("id").toString();
    return agentId == writerId;
}

void StoryIntelligenceController::selectSession(const QString &sessionId)
{
    if (!m_pendingChat.prompt.isEmpty() || sessionId == m_sessionId)
        return;
    QJsonObject session;
    for (const auto &value : m_workspace.data.value("sessions").toArray())
        if (value.toObject().value("id").toString() == sessionId)
            session = value.toObject();
    if (!sessionAvailable(session)) {
        refreshWorkspace();
        m_widget->setStatusMessage(tr("This conversation's heading or agent is no longer assigned. Restore its assignment in Workspace; its saved history is still there."));
        return;
    }
    const auto scopeId = session.value("scope_id").toString();
    const auto scope = m_workspace.scope(scopeId);
    if (m_workspace.scopeKind(scopeId) == "scene") {
        refreshWorkspace();
        m_widget->setStatusMessage(tr("This older scene conversation is preserved in Workspace. Start a chapter conversation to use chapter context."));
        return;
    }
    m_loadingWorkspace = true;
    if (scopeId != QStringLiteral("manuscript")) {
        auto cursor = m_editor->textCursor();
        cursor.setPosition(qBound(0, scope.value("start").toInt(), m_editor->toPlainText().size()));
        m_editor->setTextCursor(cursor);
        m_editor->ensureCursorVisible();
    }
    m_scopeId = scopeId;
    m_scopeMode = scopeId == QStringLiteral("manuscript") ? QStringLiteral("manuscript") : QStringLiteral("chapter");
    m_workspace.data.insert("scope_mode", m_scopeMode);
    m_sessionId.clear();
    m_history = {};
    m_widget->setActiveCharacter(QString());
    m_loadingWorkspace = false;
    refreshWorkspace();
    m_sessionId = sessionId;
    restoreSession();
    refreshWorkspace();
    saveWorkspace();
}

void StoryIntelligenceController::handleMessageAction(const QString &messageId, const QString &action)
{
    if (!m_pendingChat.prompt.isEmpty() || !m_chatRequestId.isEmpty())
        return;
    int index = -1;
    for (int i = 0; i < m_history.size(); ++i) {
        const auto message = m_history[i].toObject();
        if ((!messageId.isEmpty() && message.value("id").toString() == messageId)
            || (messageId.isEmpty() && action == "retry" && message.value("role") == "user"))
            index = i;
    }
    if (index < 0)
        return;
    const auto sessionId = m_sessionId;
    QString replacement;
    if (action == "delete") {
        if (QMessageBox::question(m_widget, tr("Delete message?"),
                tr("Delete this message from the saved conversation and remove its manuscript marks? Applied manuscript edits and approved memories are not undone."),
                QMessageBox::Yes | QMessageBox::No, QMessageBox::No) != QMessageBox::Yes)
            return;
    } else if (action == "edit") {
        const bool user = m_history[index].toObject().value("role") == "user";
        bool accepted = false;
        replacement = QInputDialog::getMultiLineText(m_widget, user ? tr("Edit and resend") : tr("Edit response"),
            user ? tr("A new conversation branch will send this edited prompt. The original conversation is kept.")
                 : tr("Save an edited response in a new branch. Later messages remain in the original conversation."),
            m_history[index].toObject().value("content").toString(), &accepted);
        if (!accepted || replacement.trimmed().isEmpty())
            return;
    }
    // A modal editor can process navigation events; never mutate another conversation.
    if (m_sessionId != sessionId)
        return;
    reviseChatHistory(index, action, replacement);
}

bool StoryIntelligenceController::reviseChatHistory(int index, const QString &action, const QString &replacement)
{
    if (index < 0 || index >= m_history.size() || !m_pendingChat.prompt.isEmpty()
        || !m_chatRequestId.isEmpty() || m_workspaceDocument != currentDocumentPath()
        || (action != "edit" && action != "retry" && action != "delete"))
        return false;
    const auto originalMessage = m_history[index].toObject();
    const bool user = originalMessage.value("role") == "user";
    const bool generate = action == "retry" || (action == "edit" && user);
    if (action == "edit" && replacement.trimmed().isEmpty())
        return false;
    if (generate && !m_engine->isReady()) {
        m_widget->setStatusMessage(tr("The engine is not ready. Your original conversation is unchanged; try again when it is connected."));
        return false;
    }
    int promptIndex = index;
    if (generate) {
        while (promptIndex >= 0 && m_history[promptIndex].toObject().value("role") != "user")
            --promptIndex;
        if (promptIndex < 0)
            return false;
    }
    const auto before = m_workspace.data;
    const auto previousHistory = m_history;
    const auto previousSession = m_sessionId;
    QString prompt;
    if (action == "delete") {
        m_history.removeAt(index);
        QJsonArray markers;
        for (const auto &value : m_workspace.data.value("markers").toArray()) {
            const auto mark = value.toObject();
            bool owned = mark.value("message_id") == originalMessage.value("id") && originalMessage.contains("id");
            if (!mark.contains("message_id"))
                for (const auto &reference : originalMessage.value("references").toArray())
                    owned = owned || (reference.toObject().contains("id") && reference.toObject().value("id") == mark.value("id"));
            if (!owned || mark.value("session_id").toString() != m_sessionId)
                markers.append(mark);
        }
        m_workspace.data.insert("markers", markers);
    } else {
        QJsonObject branch;
        for (const auto &value : before.value("sessions").toArray())
            if (value.toObject().value("id").toString() == m_sessionId)
                branch = value.toObject();
        if (branch.isEmpty() || !sessionAvailable(branch))
            return false;
        m_history = {};
        const int cutoff = generate ? promptIndex : index;
        for (int i = 0; i < cutoff; ++i)
            m_history.append(previousHistory[i]);
        if (generate) {
            prompt = action == "edit" ? replacement.trimmed() : previousHistory[promptIndex].toObject().value("content").toString();
        } else {
            auto edited = originalMessage;
            edited.insert("id", StoryWorkspace::newId());
            edited.insert("content", replacement.trimmed());
            edited.insert("edited", true);
            edited.remove("references");
            edited.remove("proposals");
            m_history.append(edited);
        }
        m_sessionId = StoryWorkspace::newId();
        branch.insert("id", m_sessionId);
        branch.insert("forked_from", previousSession);
        branch.insert("created", QDateTime::currentDateTimeUtc().toString(Qt::ISODate));
        branch.insert("title", tr("%1 (%2 %3)").arg(branch.value("title").toString().left(60), action == "retry" ? tr("retry") : tr("edited"))
            .arg(before.value("sessions").toArray().size() + 1));
        branch.insert("messages", m_history);
        auto sessions = before.value("sessions").toArray();
        sessions.append(branch);
        m_workspace.data.insert("sessions", sessions);
    }
    storeSession();
    if (!saveWorkspace()) {
        m_workspace.data = before;
        m_history = previousHistory;
        m_sessionId = previousSession;
        return false;
    }
    restoreSession();
    restoreMarkers();
    refreshWorkspace();
    if (generate)
        sendChat(prompt);
    else
        m_widget->setStatusMessage(action == "delete" ? tr("Message deleted") : tr("Edited response saved in a new conversation branch"));
    return true;
}

void StoryIntelligenceController::editWorkspace(int tab)
{
    if (!m_pendingChat.prompt.isEmpty()) {
        m_widget->setStatusMessage(tr("Finish the current response before editing the workspace."));
        return;
    }
    reconcileWorkspaceHeadings();
    auto data = m_workspace.data;
    if (!editStoryWorkspace(data, m_scopeId, tab, m_widget))
        return;
    const auto before = m_workspace.data;
    m_workspace.data = data;
    if (!saveWorkspace()) {
        m_workspace.data = before;
        return;
    }
    const auto nextSession = data.value("active_session").toString();
    if (nextSession != m_sessionId) {
        if (nextSession.isEmpty()) {
            m_sessionId.clear();
            restoreSession();
        } else {
            selectSession(nextSession);
        }
    }
    refreshWorkspace();
    restoreMarkers();
}

void StoryIntelligenceController::rememberMessage(const QString &text)
{
    QJsonObject proposal{{"title", text.simplified().left(80)}, {"body", text.left(48000)}, {"kind", "preference"}};
    reviewProposal(QStringLiteral("memory"), proposal);
}

void StoryIntelligenceController::reviewProposal(const QString &kind, const QJsonObject &proposal)
{
    if (!m_pendingChat.prompt.isEmpty())
        return;
    const auto before = m_workspace.data;
    const auto proposalId = proposal.value("_proposal_id").toString();
    if (!proposalId.isEmpty() && m_workspace.data.value("reviewed_proposals").toArray().contains(proposalId)) {
        m_widget->setStatusMessage(tr("This proposal was already reviewed and saved. Edit its record in Workspace."));
        return;
    }
    const QString proposalScope = proposal.value("_scope_id").toString(m_scopeId);
    if (m_workspace.scope(proposalScope).isEmpty() || m_workspace.scope(proposalScope).value("orphaned").toBool()) {
        m_widget->setStatusMessage(tr("Relink this proposal's removed heading in Workspace before reviewing it."));
        return;
    }
    if (kind == "scene") {
        auto context = m_workspace.effectiveContext(proposalScope);
        for (auto i = proposal.begin(); i != proposal.end(); ++i)
            if (i.value().isString() && !i.key().startsWith('_'))
                context.insert(i.key(), i.value());
        if (!editStoryRecord(context, "scene", m_widget))
            return;
        auto scope = m_workspace.scope(proposalScope);
        scope.insert("context", context);
        m_workspace.setScope(scope);
    } else if (kind == "character") {
        auto record = StoryWorkspace::cleanAgent(proposal);
        record.insert("id", StoryWorkspace::newId());
        if (!editStoryRecord(record, "agent", m_widget))
            return;
        m_workspace.putAgent(record);
        auto scope = m_workspace.scope(proposalScope);
        auto settings = scope.value("settings").toObject();
        auto cast = m_workspace.effectiveSettings(proposalScope).value("cast").toArray();
        cast.append(record.value("id"));
        settings.insert("cast", cast);
        scope.insert("settings", settings);
        m_workspace.setScope(scope);
    } else if (kind == "memory") {
        QJsonObject record;
        for (const auto *key : {"title", "body", "kind", "source"})
            if (proposal.value(key).isString())
                record.insert(key, proposal.value(key).toString().left(48000));
        record.insert("state", "proposed");
        record.insert("id", StoryWorkspace::newId());
        record.insert("scope_id", proposalScope);
        record.insert("agent_id", proposal.value("_agent_id").toString(activeAgent().value("id").toString()));
        record.insert("session_id", m_sessionId);
        if (!editStoryRecord(record, "memory", m_widget))
            return;
        auto memories = m_workspace.data.value("memories").toArray();
        memories.append(record);
        m_workspace.data.insert("memories", memories);
    } else
        return;
    if (!proposalId.isEmpty()) {
        auto reviewed = m_workspace.data.value("reviewed_proposals").toArray();
        reviewed.append(proposalId);
        m_workspace.data.insert("reviewed_proposals", reviewed);
    }
    if (!saveWorkspace())
        m_workspace.data = before;
    refreshWorkspace();
}

QJsonArray StoryIntelligenceController::allowedManifest() const
{
    QJsonArray result{
        QJsonObject{{"id", "get_story_context"}, {"risk", "R0"}, {"description", "Read the current scene, speaking agent, cast and approved scoped memories."}},
        QJsonObject{{"id", "list_story_scopes"}, {"risk", "R0"}, {"description", "List current manuscript headings and their exact UTF-16 boundaries."}},
        QJsonObject{{"id", "read_story_scope"},
                    {"risk", "R0"},
                    {"description", "Read a chapter or scene, up to 8000 UTF-16 units per page."},
                    {"arguments", "scope_id: string, offset?: nonnegative integer"}},
        QJsonObject{{"id", "search_manuscript"},
                    {"risk", "R0"},
                    {"description", "Find up to 20 exact occurrences of a phrase in the current manuscript."},
                    {"arguments", "query: string"}}};
    if (!m_harness)
        return result;
    const bool edits = activeAgent().value("tools").toString() == "edit";
    for (const auto &value : m_harness->manifest()) {
        const auto tool = value.toObject();
        const QString id = tool.value("id").toString();
        const auto risk = toolRisk(id);
        if (!edits && (risk == "R3" || risk == "R4"))
            continue;
        result.append(tool);
    }
    return result;
}

QJsonObject StoryIntelligenceController::workspaceTool(const QString &toolId, const QJsonObject &arguments) const
{
    if (toolId == "get_story_context")
        return QJsonObject{{"ok", true}, {"context", workspaceContext()}};
    if (toolId == "list_story_scopes") {
        if (!m_editor->isDocumentParsed())
            return QJsonObject{{"ok", false}, {"error", "Heading analysis is refreshing. Retry after it completes."}};
        QJsonArray scopes;
        for (const auto &v : m_workspace.data.value("scopes").toArray()) {
            const auto record = v.toObject();
            if (record.value("orphaned").toBool())
                continue;
            QJsonObject safe;
            for (const auto *key : {"id", "title", "level", "start", "end"})
                if (record.contains(key))
                    safe.insert(key, record.value(key));
            scopes.append(safe);
        }
        return QJsonObject{{"ok", true}, {"scopes", scopes}};
    }
    if (toolId == "read_story_scope") {
        if (!m_editor->isDocumentParsed())
            return QJsonObject{{"ok", false}, {"error", "Heading analysis is refreshing. Retry after it completes."}};
        const auto scope = m_workspace.scope(arguments.value("scope_id").toString());
        if (scope.isEmpty() || scope.value("orphaned").toBool())
            return QJsonObject{{"ok", false}, {"error", "Unknown or orphaned scope."}};
        const auto value = arguments.value("offset");
        const double offset = value.isUndefined() ? 0 : value.toDouble(-1);
        const QString text = m_editor->toPlainText();
        if (!std::isfinite(offset) || offset < 0 || offset > text.size() || offset != static_cast<int>(offset))
            return QJsonObject{{"ok", false}, {"error", "Invalid page offset."}};
        const int start = scope.value("start").toInt() + static_cast<int>(offset);
        const int end = qMin(text.size(), scope.value("end").toInt(text.size()));
        if (start < 0 || end < 0 || start > end)
            return QJsonObject{{"ok", false}, {"error", "Offset is outside this scope."}};
        const int count = qMin(8000, end - start);
        return QJsonObject{{"ok", true},
                           {"start_utf16", start},
                           {"end_utf16", start + count},
                           {"text", text.mid(start, count)},
                           {"has_more", start + count < end}};
    }
    if (toolId == "search_manuscript") {
        const auto query = arguments.value("query").toString();
        if (query.isEmpty() || query.size() > 1000)
            return QJsonObject{{"ok", false}, {"error", "Query must contain 1–1000 characters."}};
        const QString text = m_editor->toPlainText();
        QJsonArray matches;
        int position = 0;
        while (matches.size() < 20 && (position = text.indexOf(query, position)) >= 0) {
            matches.append(QJsonObject{{"start_utf16", position}, {"end_utf16", position + query.size()}, {"quote", query}});
            position += query.size();
        }
        return QJsonObject{{"ok", true}, {"matches", matches}};
    }
    return {};
}

void StoryIntelligenceController::restoreMarkers()
{
    const QString digest = documentDigest(m_editor->toPlainText());
    QJsonArray valid, stale;
    for (const auto &v : m_workspace.data.value("markers").toArray()) {
        auto mark = v.toObject();
        if (mark.value("document_hash").toString() == digest) {
            mark.insert("document_revision", m_revision);
            mark.remove("stale");
            valid.append(mark);
        } else {
            mark.insert("stale", true);
            stale.append(mark);
        }
    }
    applyAnnotations(valid, m_revision);
    for (const auto &v : stale)
        m_annotations.append(v);
    m_widget->setAnnotations(m_annotations);
}
}
