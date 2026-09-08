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
#include <QInputDialog>
#include <QJsonDocument>
#include <QMessageBox>
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
QString sessionBranchId(const QJsonObject &session)
{
    const QString branch = session.value(QStringLiteral("branch_id")).toString().trimmed();
    return branch.isEmpty() ? QStringLiteral("mainline") : branch;
}
QString rememberedSessionKey(const QString &branchId, const QString &scopeId)
{
    return QStringLiteral("%1::%2").arg(branchId.isEmpty() ? QStringLiteral("mainline") : branchId, scopeId);
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
        if (it.value().toString() == detectedId)
            it.value() = character.value("id");
    review.insert("assignments", assignments);
    m_workspace.data.insert("writing_review", review);
    if (!saveWorkspace()) {
        m_workspace.data = before;
        return false;
    }
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
    if (m_scopeMode != "manuscript")
        m_scopeMode = "chapter";
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
        const auto rememberedSessions = m_workspace.data.value("last_sessions").toObject();
        QString remembered = rememberedSessions.value(rememberedSessionKey(m_activeBranch, m_scopeId)).toString();
        if (remembered.isEmpty() && m_activeBranch == QStringLiteral("mainline"))
            remembered = rememberedSessions.value(m_scopeId).toString();
        for (const auto &v : m_workspace.data.value("sessions").toArray()) {
            const auto s = v.toObject();
            if (s.value("scope_id").toString() != m_scopeId || sessionBranchId(s) != m_activeBranch)
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
        if (session.value("id").toString() == m_sessionId && (!sessionAvailable(session) || session.value("agent_id") != activeAgent().value("id"))) {
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
    m_widget->setWorkspaceContext(m_scopeMode,
                                  m_workspace.scope(m_scopeId).value("title").toString(),
                                  activeAgent().value("name").toString(),
                                  title,
                                  m_workspace.scopeKind(m_scopeId));
    QJsonArray choices;
    const auto sessions = m_workspace.data.value("sessions").toArray();
    for (int i = sessions.size() - 1; i >= 0; --i) {
        const auto session = sessions[i].toObject();
        if (!sessionAvailable(session))
            continue;
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
                              {"branch_id", m_activeBranch},
                              {"character_id", m_widget->activeCharacterId()},
                              {"agent_id", activeAgent().value("id")},
                              {"created", QDateTime::currentDateTimeUtc().toString(Qt::ISODate)},
                              {"title", m_history.first().toObject().value("content").toString().simplified().left(80)}};
    }
    session.insert(QStringLiteral("branch_id"), m_activeBranch);
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
    remembered.insert(rememberedSessionKey(m_activeBranch, m_scopeId), m_sessionId);
    if (m_activeBranch == QStringLiteral("mainline"))
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
                                        message.value("references").toArray(),
                                        message.value("id").toString());
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
        remembered.insert(rememberedSessionKey(m_activeBranch, m_scopeId), m_sessionId);
        if (m_activeBranch == QStringLiteral("mainline"))
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
    sessions.append(QJsonObject{{"id", m_sessionId},
                                {"scope_id", m_scopeId},
                                {"scope_mode", m_scopeMode},
                                {"branch_id", m_activeBranch},
                                {"character_id", m_widget->activeCharacterId()},
                                {"agent_id", activeAgent().value("id")},
                                {"created", QDateTime::currentDateTimeUtc().toString(Qt::ISODate)},
                                {"title", tr("New conversation %1").arg(sessions.size() + 1)},
                                {"messages", QJsonArray()}});
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
    if (QMessageBox::question(m_widget,
                              tr("Delete conversation?"),
                              tr("Delete “%1” and its saved chat history? Its un-applied manuscript marks will also be removed. Applied manuscript edits and "
                                 "approved memories are kept.")
                                  .arg(target.value("title").toString()),
                              QMessageBox::Yes | QMessageBox::Cancel,
                              QMessageBox::Cancel)
        != QMessageBox::Yes)
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
    if (sessionBranchId(session) != m_activeBranch)
        return false;
    const auto scopeId = session.value("scope_id").toString();
    const auto scope = m_workspace.scope(scopeId);
    const auto characterId = session.value("character_id").toString();
    const auto settings = m_workspace.effectiveSettings(scopeId);
    const auto agentId = session.value("agent_id").toString();
    if (session.isEmpty() || scope.isEmpty() || scope.value("orphaned").toBool())
        return false;
    const auto agent = m_workspace.agent(agentId);
    if (!characterId.isEmpty())
        return agentId == characterId && !agent.isEmpty() && !agent.value("archived").toBool() && settings.value("cast").toArray().contains(characterId);
    const auto writer = m_workspace.agent(settings.value("co_writer").toString());
    const auto writerId = writer.isEmpty() || writer.value("archived").toBool() ? QStringLiteral("default-co-writer") : writer.value("id").toString();
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
        m_widget->setStatusMessage(
            tr("This conversation's heading or agent is no longer assigned. Restore its assignment in Workspace; its saved history is still there."));
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
        if (QMessageBox::question(m_widget,
                                  tr("Delete message?"),
                                  tr("Delete this message from the saved conversation and remove its manuscript marks? Applied manuscript edits and approved "
                                     "memories are not undone."),
                                  QMessageBox::Yes | QMessageBox::No,
                                  QMessageBox::No)
            != QMessageBox::Yes)
            return;
    } else if (action == "edit") {
        const bool user = m_history[index].toObject().value("role") == "user";
        bool accepted = false;
        replacement = QInputDialog::getMultiLineText(m_widget,
                                                     user ? tr("Edit and resend") : tr("Edit response"),
                                                     user ? tr("A new conversation branch will send this edited prompt. The original conversation is kept.")
                                                          : tr("Save an edited response in a new branch. Later messages remain in the original conversation."),
                                                     m_history[index].toObject().value("content").toString(),
                                                     &accepted);
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
    if (index < 0 || index >= m_history.size() || !m_pendingChat.prompt.isEmpty() || !m_chatRequestId.isEmpty() || m_workspaceDocument != currentDocumentPath()
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
        branch.insert("branch_id", m_activeBranch);
        branch.insert("forked_from", previousSession);
        branch.insert("created", QDateTime::currentDateTimeUtc().toString(Qt::ISODate));
        branch.insert("title",
                      tr("%1 (%2 %3)")
                          .arg(branch.value("title").toString().left(60), action == "retry" ? tr("retry") : tr("edited"))
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
    for (const auto &value : storyEngineReadManifest())
        result.append(value);
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

QJsonArray StoryIntelligenceController::storyEngineReadManifest() const
{
    if (m_projectRoot.isEmpty() || !m_engine->isReady() || !m_engine->supportsOperation(QStringLiteral("story_tool")))
        return {};

    QJsonArray result{QJsonObject{{"id", "query_project_story_context"},
                                  {"risk", "R0"},
                                  {"description", "Compile fresh project evidence and typed story state through the active epistemic boundary."},
                                  {"arguments", "prompt: string, maximum_chars?: integer"}}};

    // Raw Story Engine state is intentionally an author-only capability. In
    // Character/Reader/Cold Reader/etc. modes, exposing these queries would let
    // the model route around the Context Compiler and recover hidden knowledge.
    if (m_epistemicMode != QStringLiteral("author_omniscient"))
        return result;

    result.append(QJsonObject{{"id", "resolve_entity"},
                              {"risk", "R0"},
                              {"description", "Resolve a project entity by exact name or alias."},
                              {"arguments", "name: string"}});
    result.append(QJsonObject{{"id", "get_entity"},
                              {"risk", "R0"},
                              {"description", "Read one normalized entity with grounded claims and mentions."},
                              {"arguments", "entity_id: string"}});
    result.append(QJsonObject{{"id", "find_story_evidence"},
                              {"risk", "R0"},
                              {"description", "Search bounded project evidence."},
                              {"arguments", "query: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "query_claims"},
                              {"risk", "R0"},
                              {"description", "Read bounded provenance-backed story claims."},
                              {"arguments", "entity?: string, predicate?: string, limit?: integer"}});
    result.append(
        QJsonObject{{"id", "get_story_unit"}, {"risk", "R0"}, {"description", "Read one stable project story unit."}, {"arguments", "story_unit_id: string"}});
    result.append(QJsonObject{{"id", "list_story_units"},
                              {"risk", "R0"},
                              {"description", "List bounded stable project story units."},
                              {"arguments", "source_id?: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "get_character_knowledge"},
                              {"risk", "R0"},
                              {"description", "Read tracked character knowledge and belief state."},
                              {"arguments", "character: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "get_character_beliefs"},
                              {"risk", "R0"},
                              {"description", "Read tracked beliefs, suspicions and disbelief state."},
                              {"arguments", "character: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "get_reader_state"},
                              {"risk", "R0"},
                              {"description", "Read tracked reader information state."},
                              {"arguments", "through_story_unit?: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "query_timeline"}, {"risk", "R0"}, {"description", "Read normalized timeline events."}, {"arguments", "limit?: integer"}});
    result.append(QJsonObject{{"id", "get_world_state"},
                              {"risk", "R0"},
                              {"description", "Read typed world state for an entity."},
                              {"arguments", "entity: string, state_type?: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "where_is_entity"},
                              {"risk", "R0"},
                              {"description", "Read tracked location state for an entity."},
                              {"arguments", "entity: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "who_has_object"},
                              {"risk", "R0"},
                              {"description", "Read tracked possession state for an object."},
                              {"arguments", "object: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "list_threads"},
                              {"risk", "R0"},
                              {"description", "Read bounded narrative threads with provenance."},
                              {"arguments", "limit?: integer"}});
    result.append(QJsonObject{{"id", "list_reader_questions"},
                              {"risk", "R0"},
                              {"description", "Read bounded tracked reader questions with provenance."},
                              {"arguments", "limit?: integer"}});
    result.append(QJsonObject{{"id", "list_dramatic_promises"},
                              {"risk", "R0"},
                              {"description", "Read bounded dramatic promises with provenance."},
                              {"arguments", "limit?: integer"}});
    result.append(QJsonObject{{"id", "trace_causality"},
                              {"risk", "R0"},
                              {"description", "Trace bounded causal dependencies around one normalized story record."},
                              {"arguments", "record_kind: string, record_id: string, direction?: upstream|downstream|both, maximum_depth?: integer"}});
    result.append(QJsonObject{{"id", "get_decision_history"},
                              {"risk", "R0"},
                              {"description", "Read bounded consequential character decisions."},
                              {"arguments", "character?: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "get_opposition_state"},
                              {"risk", "R0"},
                              {"description", "Read bounded opposition attached to story objectives."},
                              {"arguments", "objective_id?: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "get_scene_contract"},
                              {"risk", "R0"},
                              {"description", "Read the reviewed scene contract for one stable story unit."},
                              {"arguments", "story_unit_id: string"}});
    result.append(QJsonObject{{"id", "get_author_decisions"},
                              {"risk", "R0"},
                              {"description", "Read writer-owned structural decisions and rationale."},
                              {"arguments", "story_unit_id?: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "audit_scene"},
                              {"risk", "R0"},
                              {"description", "Compose a deterministic scene audit from tracked narrative state."},
                              {"arguments", "story_unit_id: string"}});
    result.append(QJsonObject{{"id", "audit_chapter"},
                              {"risk", "R0"},
                              {"description", "Compose a deterministic chapter audit from tracked narrative state."},
                              {"arguments", "story_unit_id: string"}});
    result.append(QJsonObject{{"id", "list_branches"},
                              {"risk", "R0"},
                              {"description", "List alternate branches and stale-base state."},
                              {"arguments", "limit?: integer"}});
    result.append(QJsonObject{{"id", "compare_branch"},
                              {"risk", "R0"},
                              {"description", "Read one branch diff, merge history, and freshness."},
                              {"arguments", "branch_id: string"}});
    result.append(QJsonObject{{"id", "get_retcon_impact"},
                              {"risk", "R0"},
                              {"description", "Trace registered downstream dependencies for a potential retcon."},
                              {"arguments", "source_kind: string, source_id: string, maximum_nodes?: integer"}});
    result.append(QJsonObject{{"id", "cold_reader_at"},
                              {"risk", "R0"},
                              {"description", "Read only tracked reader-visible evidence at a selected story cutoff."},
                              {"arguments", "story_unit_id: string, prompt?: string, maximum_chars?: integer"}});
    result.append(QJsonObject{{"id", "audit_reveal_fairness"},
                              {"risk", "R0"},
                              {"description", "Audit tracked setup evidence before a reveal without declaring prose fair or unfair."},
                              {"arguments", "story_unit_id: string, claim_id?: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "get_reader_expectations"},
                              {"risk", "R0"},
                              {"description", "Read tracked open reader questions and dramatic promises at a story cutoff."},
                              {"arguments", "story_unit_id: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "get_dramatic_irony"},
                              {"risk", "R0"},
                              {"description", "Compare tracked reader access with one character's tracked knowledge."},
                              {"arguments", "story_unit_id: string, character: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "get_writer_model"},
                              {"risk", "R0"},
                              {"description", "Read confirmed and provisional writer preferences with behavioral evidence."},
                              {"arguments", "scope_kind?: string, scope_id?: string, include_ignored?: boolean, limit?: integer"}});
    result.append(QJsonObject{{"id", "explain_writer_preference"},
                              {"risk", "R0"},
                              {"description", "Explain one Writer Model preference from its bounded evidence."},
                              {"arguments", "preference_id: string"}});
    result.append(QJsonObject{{"id", "run_editorial_council"},
                              {"risk", "R0"},
                              {"description", "Run seven independent read-only reviewers over one immutable Story State snapshot."},
                              {"arguments", "story_unit_id: string"}});
    result.append(QJsonObject{{"id", "list_story_lenses"},
                              {"risk", "R0"},
                              {"description", "List writer-defined reusable Story Lenses."},
                              {"arguments", "include_archived?: boolean"}});
    result.append(QJsonObject{{"id", "get_story_lens"},
                              {"risk", "R0"},
                              {"description", "Read one Story Lens and its current exact-source evidence findings."},
                              {"arguments", "lens_id: string"}});
    result.append(QJsonObject{{"id", "run_story_lens"},
                              {"risk", "R0"},
                              {"description", "Run deterministic evidence retrieval for one Story Lens."},
                              {"arguments", "lens_id: string, maximum_findings?: integer"}});
    result.append(QJsonObject{{"id", "get_reader_experience"},
                              {"risk", "R0"},
                              {"description", "Read qualitative reader-experience cues for one story unit."},
                              {"arguments", "story_unit_id: string"}});
    result.append(QJsonObject{{"id", "get_reader_experience_timeline"},
                              {"risk", "R0"},
                              {"description", "Build a qualitative reader-experience timeline in writer-owned manuscript order."},
                              {"arguments", "source_id?: string, maximum_units?: integer"}});
    result.append(QJsonObject{{"id", "explore_story"},
                              {"risk", "R0"},
                              {"description", "Explore bounded cross-state Story Model nodes, evidence, and dependency edges."},
                              {"arguments", "query: string, limit?: integer"}});
    result.append(QJsonObject{{"id", "get_scene_semantics"},
                              {"risk", "R0"},
                              {"description", "Read exact scene presence plus explicitly tracked scene/world state."},
                              {"arguments", "story_unit_id: string"}});
    result.append(QJsonObject{{"id", "audit_continuity"},
                              {"risk", "R0"},
                              {"description", "Audit tracked continuity conflicts and knowledge-access review candidates."},
                              {"arguments", "story_unit_id: string, character?: string"}});
    result.append(QJsonObject{{"id", "get_character_arc"},
                              {"risk", "R0"},
                              {"description", "Read tracked decisions, knowledge changes, and relationship changes for one character."},
                              {"arguments", "character: string"}});
    result.append(QJsonObject{{"id", "get_relationship_arc"},
                              {"risk", "R0"},
                              {"description", "Read explicit relationship-state transitions between two entities."},
                              {"arguments", "entity_a: string, entity_b: string"}});
    result.append(QJsonObject{{"id", "audit_ending_integrity"},
                              {"risk", "R0"},
                              {"description", "Audit tracked open obligations and causal prerequisites at a selected ending."},
                              {"arguments", "story_unit_id: string"}});
    result.append(QJsonObject{{"id", "get_project_health"},
                              {"risk", "R0"},
                              {"description", "Read engineering and coverage metrics without producing a story-quality score."}});
    result.append(QJsonObject{{"id", "get_index_status"},
                              {"risk", "R0"},
                              {"description", "Read Story Engine index integrity, FTS, stale evidence, and foreign-key status."}});
    result.append(QJsonObject{{"id", "run_wow_acceptance"},
                              {"risk", "R0"},
                              {"description", "Run the ten-step Story Engine acceptance harness against the active project."},
                              {"arguments", "story_unit_id?: string, character?: string"}});
    result.append(QJsonObject{{"id", "get_migration_status"},
                              {"risk", "R0"},
                              {"description", "Read additive legacy Story Workspace bindings and stable Story Unit links."}});
    result.append(QJsonObject{{"id", "get_indexing_status"},
                              {"risk", "R0"},
                              {"description", "Read resumable background-index checkpoint progress without starting indexing."}});
    result.append(
        QJsonObject{{"id", "get_performance_report"}, {"risk", "R0"}, {"description", "Inspect normalized Story Model query plans and local query health."}});
    result.append(
        QJsonObject{{"id", "get_security_audit"}, {"risk", "R0"}, {"description", "Inspect fail-closed filesystem and adapter security boundaries."}});
    result.append(
        QJsonObject{{"id", "get_model_fingerprint"}, {"risk", "R0"}, {"description", "Read a path/ID-independent normalized Story Model fingerprint."}});
    result.append(
        QJsonObject{{"id", "get_acceptance_metrics"}, {"risk", "R0"}, {"description", "Read engineering acceptance metrics without a story-quality score."}});
    result.append(QJsonObject{{"id", "get_retrieval_capabilities"},
                              {"risk", "R0"},
                              {"description", "Inspect lexical/default and optional semantic retrieval guarantees."}});
    result.append(QJsonObject{{"id", "explain_story_record"},
                              {"risk", "R0"},
                              {"description", "Explain why one tracked Story Model record exists using provenance and dependencies."},
                              {"arguments", "record_kind: string, record_id: string"}});
    result.append(QJsonObject{{"id", "run_operational_acceptance"},
                              {"risk", "R0"},
                              {"description", "Run the read-only ten-step operational acceptance harness for Story Engine phases 26–35."},
                              {"arguments", "prompt?: string"}});
    result.append(QJsonObject{{"id", "get_release_validation"},
                              {"risk", "R0"},
                              {"description", "Read release-grade hard-gate validation for the normalized Story Project."}});
    result.append(
        QJsonObject{{"id", "get_recovery_status"}, {"risk", "R0"}, {"description", "Inspect crash-recovery journal state without modifying Story State."}});
    result.append(QJsonObject{{"id", "get_observability_report"},
                              {"risk", "R0"},
                              {"description", "Inspect local content-free Story Engine operational counters and timings."}});
    result.append(
        QJsonObject{{"id", "get_resource_policy"}, {"risk", "R0"}, {"description", "Inspect bounded record/character/time/cancellation resource policy."}});
    result.append(
        QJsonObject{{"id", "get_path_resilience"}, {"risk", "R0"}, {"description", "Inspect Unicode and cross-platform project-relative path portability."}});
    result.append(QJsonObject{{"id", "get_offline_readiness"},
                              {"risk", "R0"},
                              {"description", "Inspect deterministic offline guarantees and fail-closed privacy readiness."}});
    result.append(QJsonObject{{"id", "get_compatibility_status"},
                              {"risk", "R0"},
                              {"description", "Inspect schema compatibility and local Story State rollback availability."}});
    result.append(QJsonObject{{"id", "run_release_candidate_acceptance"},
                              {"risk", "R0"},
                              {"description", "Run the read-only engine release-candidate harness for Story Engine phases 36–45."}});
    return result;
}

bool StoryIntelligenceController::isStoryEngineReadTool(const QString &toolId) const
{
    for (const auto &value : storyEngineReadManifest())
        if (value.toObject().value(QStringLiteral("id")).toString() == toolId)
            return true;
    return false;
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
