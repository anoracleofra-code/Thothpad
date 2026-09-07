// SPDX-License-Identifier: GPL-3.0-or-later
#include "storyworkspace.h"
#include <QCryptographicHash>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QSaveFile>
#include <QSet>
#include <QUuid>
#include <QtEndian>

namespace ghostwriter
{
namespace
{
constexpr qint64 MaximumWorkspaceBytes = 32 * 1024 * 1024;
QByteArray hash(const QByteArray &bytes)
{
    return QCryptographicHash::hash(bytes, QCryptographicHash::Sha256);
}
QJsonObject merge(QJsonObject base, const QJsonObject &next)
{
    for (auto i = next.begin(); i != next.end(); ++i)
        base.insert(i.key(), i.value());
    return base;
}
bool validRecordIds(const QJsonArray &records, bool requireManuscript)
{
    QSet<QString> ids;
    bool root = false;
    for (const auto &value : records) {
        const auto record = value.toObject();
        const auto id = record.value("id").toString();
        if (id.isEmpty() || ids.contains(id)) return false;
        ids.insert(id);
        if (id == "manuscript") root = record.value("level").toInt(-1) == 0;
    }
    return !requireManuscript || root;
}
}

QString StoryWorkspace::newId()
{
    return QUuid::createUuid().toString(QUuid::WithoutBraces);
}

QJsonObject StoryWorkspace::agentTemplate(const QString &kind)
{
    QString name = QStringLiteral("Character");
    QString instructions =
        QStringLiteral("Stay in the character's documented voice and knowledge. Treat improvisation as a possibility, not established canon.");
    if (kind == QStringLiteral("co_writer")) {
        name = QStringLiteral("Co-Writer");
        instructions = QStringLiteral(
            "Collaborate with the author on story, structure and revision. Preserve their voice. Ground suggestions in exact manuscript passages; explain "
            "choices and distinguish canon from invention.");
    } else if (kind == QStringLiteral("continuity")) {
        name = QStringLiteral("Continuity Editor");
        instructions = QStringLiteral(
            "Inspect chronology, character knowledge and established facts. Cite exact conflicting passages and propose the smallest correction. Flag "
            "uncertainty.");
    } else if (kind == QStringLiteral("scene_architect")) {
        name = QStringLiteral("Scene Architect");
        instructions = QStringLiteral(
            "Help shape setting, conflict, stakes, goals and scene turns. Offer alternatives that fit the active chapter and established canon.");
    } else if (kind == QStringLiteral("custom")) {
        name = QStringLiteral("Custom Agent");
        instructions.clear();
    }
    return {{"id", newId()},
            {"kind", kind},
            {"name", name},
            {"instructions", instructions},
            {"tools", "suggest"},
            {"memory_policy", "ask"},
            {"archived", false}};
}

QJsonObject StoryWorkspace::defaults()
{
    auto writer = agentTemplate(QStringLiteral("co_writer"));
    QJsonObject root{{"id", "manuscript"},
                     {"title", "Whole manuscript"},
                     {"level", 0},
                     {"context", QJsonObject()},
                     {"settings", QJsonObject{{"co_writer", writer.value("id")}}}};
    return {{"version", 2},
            {"id", newId()},
            {"agents", QJsonArray{writer}},
            {"scopes", QJsonArray{root}},
            {"memories", QJsonArray()},
            {"sessions", QJsonArray()},
            {"markers", QJsonArray()}};
}

bool StoryWorkspace::open(const QString &path, const QJsonObject &legacy, QString *error)
{
    m_path = path;
    data = defaults();
    m_diskHash.clear();
    m_writable = true;
    QFile file(path);
    if (file.exists()) {
        if (!file.open(QIODevice::ReadOnly) || file.size() > MaximumWorkspaceBytes) {
            m_writable = false;
            if (error)
                *error = QStringLiteral("Cannot read this story workspace, or it exceeds 32 MB. Existing data has been preserved.");
            return false;
        }
        const auto bytes = file.readAll();
        m_diskHash = hash(bytes);
        QJsonParseError parse;
        const auto doc = QJsonDocument::fromJson(bytes, &parse);
        const auto object = doc.object();
        if (parse.error != QJsonParseError::NoError || !doc.isObject() || object.value("version").toInt() != 2 || !object.value("agents").isArray()
            || !object.value("scopes").isArray() || !object.value("sessions").isArray() || !object.value("memories").isArray()
            || !validRecordIds(object.value("scopes").toArray(),true) || !validRecordIds(object.value("agents").toArray(),false)) {
            m_writable = false;
            if (error)
                *error = QStringLiteral("Unsupported or damaged story workspace. It will not be overwritten.");
            return false;
        }
        data = object;
        return true;
    }
    if (!legacy.isEmpty()) {
        auto root = scope(QStringLiteral("manuscript"));
        root.insert("context", legacy.value("scene_context").toObject());
        QJsonArray cast;
        for (const auto &value : legacy.value("characters").toArray()) {
            auto record = cleanAgent(value.toObject());
            if (!record.value("name").toString().isEmpty()) {
                putAgent(record);
                cast.append(record.value("id"));
            }
        }
        auto settings = root.value("settings").toObject();
        settings.insert("cast", cast);
        root.insert("settings", settings);
        setScope(root);
    }
    return true;
}

bool StoryWorkspace::save(QString *error)
{
    auto fail = [&](const QString &message) {
        if (error)
            *error = message;
        return false;
    };
    if (!m_writable || m_path.isEmpty())
        return fail(QStringLiteral("Story workspace is read-only."));
    QFile existing(m_path);
    if (existing.exists()) {
        if (!existing.open(QIODevice::ReadOnly) || existing.size() > MaximumWorkspaceBytes || hash(existing.readAll()) != m_diskHash)
            return fail(QStringLiteral("The story workspace changed outside this window. Reopen the manuscript before saving story changes."));
    } else if (!m_diskHash.isEmpty())
        return fail(QStringLiteral("The story workspace was moved or deleted. Reopen the manuscript before saving story changes."));
    existing.close();
    const auto bytes = QJsonDocument(data).toJson(QJsonDocument::Indented);
    if (bytes.size() > MaximumWorkspaceBytes)
        return fail(QStringLiteral("Story workspace exceeds 32 MB. Export or remove older sessions before saving."));
    if (!QDir().mkpath(QFileInfo(m_path).absolutePath()))
        return fail(QStringLiteral("Cannot create the story workspace directory."));
    QSaveFile file(m_path);
    if (!file.open(QIODevice::WriteOnly) || file.write(bytes) != bytes.size() || !file.commit())
        return fail(file.errorString());
    m_diskHash = hash(bytes);
    return true;
}

QJsonObject StoryWorkspace::cleanAgent(const QJsonObject &source)
{
    QJsonObject result = agentTemplate(source.value("kind").toString(QStringLiteral("character")));
    for (const auto *key : {"id", "name", "kind", "role", "summary", "voice", "knowledge", "instructions", "sources", "goals", "boundaries", "avatar"}) {
        if (source.value(key).isString())
            result.insert(key, source.value(key).toString().left(QString::fromLatin1(key) == "avatar" ? 2800000 : 48000));
    }
    if (result.value("id").toString().isEmpty())
        result.insert("id", newId());
    result.insert("name", source.value("name").toString().trimmed().left(200));
    result.insert("archived", source.value("archived").toBool());
    // Permissions always start at the native default on import.
    return result;
}

QJsonObject StoryWorkspace::importAgent(const QByteArray &input, const QString &suffix, QString *error)
{
    auto fail = [&](const QString &message) {
        if (error)
            *error = message;
        return QJsonObject();
    };
    if (input.size() > 16 * 1024 * 1024)
        return fail(QStringLiteral("Agent files must be smaller than 16 MB."));
    QByteArray bytes = input;
    if (suffix == QStringLiteral("md")) {
        auto result = agentTemplate(QStringLiteral("character"));
        result.insert("name", "Imported soul");
        result.insert("instructions", QString::fromUtf8(bytes).left(48000));
        return result;
    }
    if (suffix == QStringLiteral("png")) {
        if (!bytes.startsWith("\x89PNG\r\n\x1a\n"))
            return fail(QStringLiteral("Invalid PNG agent card."));
        QByteArray payload;
        for (qsizetype offset = 8; offset + 12 <= bytes.size();) {
            const quint32 size = qFromBigEndian<quint32>(reinterpret_cast<const uchar *>(bytes.constData() + offset));
            if (size > static_cast<quint32>(bytes.size() - offset - 12))
                return fail(QStringLiteral("Truncated PNG agent card."));
            if (bytes.mid(offset + 4, 4) == "tEXt") {
                auto chunk = bytes.mid(offset + 8, size);
                const QByteArray prefix("buzz_agent_snapshot\0", 20);
                if (chunk.startsWith(prefix))
                    payload = QByteArray::fromBase64(chunk.mid(prefix.size()), QByteArray::AbortOnBase64DecodingErrors);
            }
            offset += size + 12;
        }
        if (payload.isEmpty())
            return fail(QStringLiteral("This image does not contain an unlocked Buzz agent snapshot. Export an unlocked .agent.json from Buzz."));
        bytes = payload;
    }
    QJsonParseError parse;
    const auto doc = QJsonDocument::fromJson(bytes, &parse);
    if (parse.error != QJsonParseError::NoError || !doc.isObject())
        return fail(QStringLiteral("Invalid agent JSON."));
    const auto source = doc.object();
    if (source.value("format").toString() != QStringLiteral("buzz-agent-snapshot") || source.value("version").toInt() != 1)
        return fail(QStringLiteral("Expected an unlocked buzz-agent-snapshot version 1."));
    const auto definition = source.value("definition").toObject();
    const auto profile = source.value("profile").toObject();
    auto record = cleanAgent(QJsonObject{{"name", profile.value("displayName").toString(definition.value("name").toString())},
                                         {"instructions", definition.value("systemPrompt")},
                                         {"summary", profile.value("about")},
                                         {"avatar", profile.value("avatarDataUrl")}});
    if (record.value("name").toString().isEmpty())
        return fail(QStringLiteral("The agent has no name."));
    const auto avatar = record.value("avatar").toString();
    if (!avatar.startsWith(QStringLiteral("data:image/")) || avatar.size() > 2800000)
        record.remove("avatar");
    // Memory is previewed separately and never activated simply by importing a profile.
    QJsonArray memories;
    for (const auto &value : source.value("memory").toObject().value("entries").toArray()) {
        const auto entry = value.toObject();
        if (memories.size() >= 200)
            break;
        if (entry.value("body").isString())
            memories.append(QJsonObject{{"title", entry.value("slug").toString().left(200)}, {"body", entry.value("body").toString().left(48000)}});
    }
    record.insert("imported_memories", memories);
    return record;
}

QByteArray StoryWorkspace::exportAgent(const QJsonObject &agent, const QJsonArray &memories, const QString &level)
{
    QJsonArray entries;
    // Structured character fields remain readable in Buzz's portable system prompt.
    QString prompt = agent.value("instructions").toString();
    for (const auto *key : {"role", "summary", "voice", "knowledge", "goals", "boundaries", "sources"}) {
        const auto text = agent.value(key).toString();
        if (!text.isEmpty())
            prompt += QStringLiteral("\n\n## %1\n%2").arg(QString::fromLatin1(key), text);
    }
    if (level != QStringLiteral("none"))
        for (const auto &value : memories) {
            const auto record = value.toObject();
            if (record.value("agent_id") != agent.value("id"))
                continue;
            if (record.value("state").toString() != QStringLiteral("approved"))
                continue;
            if (level == QStringLiteral("core") && record.value("kind").toString() != QStringLiteral("core"))
                continue;
            entries.append(QJsonObject{{"slug", record.value("title")}, {"body", record.value("body")}});
        }
    return QJsonDocument(
               QJsonObject{
                   {"format", "buzz-agent-snapshot"},
                   {"version", 1},
                   {"definition", QJsonObject{{"name", agent.value("name")}, {"systemPrompt", prompt}}},
                   {"profile", QJsonObject{{"displayName", agent.value("name")}, {"about", agent.value("summary")}, {"avatarDataUrl", agent.value("avatar")}}},
                   {"memory", QJsonObject{{"level", level}, {"entries", entries}}}})
        .toJson(QJsonDocument::Indented);
}

QJsonObject StoryWorkspace::scope(const QString &id) const
{
    for (const auto &value : data.value("scopes").toArray())
        if (value.toObject().value("id").toString() == id)
            return value.toObject();
    return {};
}
void StoryWorkspace::setScope(const QJsonObject &record)
{
    auto records = data.value("scopes").toArray();
    for (int i = 0; i < records.size(); ++i)
        if (records[i].toObject().value("id") == record.value("id")) {
            records[i] = record;
            data.insert("scopes", records);
            return;
        }
    records.append(record);
    data.insert("scopes", records);
}
QJsonObject StoryWorkspace::agent(const QString &id) const
{
    for (const auto &value : data.value("agents").toArray())
        if (value.toObject().value("id").toString() == id)
            return value.toObject();
    return {};
}
void StoryWorkspace::putAgent(const QJsonObject &record)
{
    auto records = data.value("agents").toArray();
    for (int i = 0; i < records.size(); ++i)
        if (records[i].toObject().value("id") == record.value("id")) {
            records[i] = record;
            data.insert("agents", records);
            return;
        }
    records.append(record);
    data.insert("agents", records);
}

void StoryWorkspace::reconcileHeadings(const QJsonArray &headings)
{
    const auto old = data.value("scopes").toArray();
    QSet<QString> used;
    QStringList parents{QStringLiteral("manuscript")};
    QList<int> levels{0};
    for (const auto &value : headings) {
        const auto heading = value.toObject();
        const int level = heading.value("level").toInt();
        while (levels.size() > 1 && levels.last() >= level) {
            levels.removeLast();
            parents.removeLast();
        }
        QJsonObject match;
        // Prefer a unique unchanged opening passage: this survives renames and
        // reordering, including chapters with the same heading text.
        const QString fingerprint = heading.value("fingerprint").toString();
        if (!fingerprint.isEmpty()) {
            int oldMatches = 0, newMatches = 0;
            for (const auto &v : headings)
                if (v.toObject().value("fingerprint").toString() == fingerprint)
                    ++newMatches;
            for (const auto &candidate : old) {
                const auto record = candidate.toObject();
                if (record.value("fingerprint").toString() == fingerprint && !used.contains(record.value("id").toString())) {
                    match = record;
                    ++oldMatches;
                }
            }
            if (oldMatches != 1 || newMatches != 1)
                match = {};
        }
        if (match.isEmpty()) {
            const QString headingPath = heading.value("heading_path").toString();
            const QString base = headingPath.left(headingPath.lastIndexOf('#'));
            int duplicates = 0;
            for (const auto &v : headings) {
                const QString path = v.toObject().value("heading_path").toString();
                if (path.left(path.lastIndexOf('#')) == base)
                    ++duplicates;
            }
            for (const auto &candidate : old) {
                const auto record = candidate.toObject();
                if (record.value("id").toString() == "manuscript" || used.contains(record.value("id").toString()))
                    continue;
                if (record.value("heading_path").toString() == headingPath && (duplicates == 1 || record.value("fingerprint").toString() == fingerprint)) {
                    match = record;
                    break;
                }
            }
        }
        if (match.isEmpty())
            match = QJsonObject{{"id", newId()}, {"context", QJsonObject()}, {"settings", QJsonObject()}};
        const QString id = match.value("id").toString();
        match = merge(match, heading);
        match.insert("parent", parents.last());
        match.insert("orphaned", false);
        setScope(match);
        used.insert(id);
        parents.append(id);
        levels.append(level);
    }
    auto records = data.value("scopes").toArray();
    for (int i = 0; i < records.size(); ++i) {
        auto record = records[i].toObject();
        if (record.value("id").toString() != QStringLiteral("manuscript") && !used.contains(record.value("id").toString()))
            record.insert("orphaned", true);
        records[i] = record;
    }
    data.insert("scopes", records);
}

QStringList StoryWorkspace::ancestry(const QString &scopeId) const
{
    QStringList result;
    QString id = scopeId;
    while (!id.isEmpty() && !result.contains(id) && result.size() < 8) {
        const auto record = scope(id);
        if (record.isEmpty() || record.value("orphaned").toBool())
            break;
        result.prepend(id);
        id = record.value("parent").toString();
    }
    if (!result.contains(QStringLiteral("manuscript")))
        result.prepend(QStringLiteral("manuscript"));
    return result;
}
QString StoryWorkspace::scopeKind(const QString &scopeId) const
{
    const auto record = scope(scopeId);
    if (scopeId == QStringLiteral("manuscript"))
        return QStringLiteral("manuscript");
    if (record.isEmpty() || record.value("orphaned").toBool())
        return {};
    int chapterLevel = 7;
    for (const auto &value : data.value("scopes").toArray()) {
        const auto heading = value.toObject();
        if (!heading.value("orphaned").toBool() && heading.value("level").toInt() > 0)
            chapterLevel = qMin(chapterLevel, heading.value("level").toInt());
    }
    return record.value("level").toInt() == chapterLevel ? QStringLiteral("chapter") : QStringLiteral("scene");
}

QString StoryWorkspace::scopeAt(int position, const QString &mode) const
{
    QString result = QStringLiteral("manuscript");
    int best = -1;
    int chapterLevel = 7;
    for (const auto &value : data.value("scopes").toArray()) {
        auto r = value.toObject();
        if (!r.value("orphaned").toBool() && r.value("level").toInt() > 0)
            chapterLevel = qMin(chapterLevel, r.value("level").toInt());
    }
    if (mode == QStringLiteral("manuscript"))
        return result;
    for (const auto &value : data.value("scopes").toArray()) {
        const auto r = value.toObject();
        if (r.value("orphaned").toBool() || r.value("level").toInt() < 1)
            continue;
        if (mode == QStringLiteral("chapter") && r.value("level").toInt() != chapterLevel)
            continue;
        const int start = r.value("start").toInt();
        if (start <= position && position < r.value("end").toInt() && start >= best) {
            best = start;
            result = r.value("id").toString();
        }
    }
    return result;
}
QJsonObject StoryWorkspace::effectiveContext(const QString &id) const
{
    QJsonObject result;
    for (const auto &parent : ancestry(id))
        result = merge(result, scope(parent).value("context").toObject());
    return result;
}
QJsonObject StoryWorkspace::effectiveSettings(const QString &id) const
{
    QJsonObject result;
    for (const auto &parent : ancestry(id))
        result = merge(result, scope(parent).value("settings").toObject());
    return result;
}
QJsonArray StoryWorkspace::scopedMemories(const QString &id, const QStringList &agentIds) const
{
    QJsonArray result;
    const auto scopes = ancestry(id);
    int budget = 24000;
    for (const auto &value : data.value("memories").toArray()) {
        const auto record = value.toObject();
        if (record.value("state").toString() != QStringLiteral("approved") || !scopes.contains(record.value("scope_id").toString()))
            continue;
        const auto owner = record.value("agent_id").toString();
        if (record.value("kind").toString() == QStringLiteral("private") && owner.isEmpty())
            continue;
        if (!owner.isEmpty() && !agentIds.contains(owner))
            continue;
        const int size = record.value("body").toString().size();
        if (size > budget)
            continue;
        budget -= size;
        result.append(record);
    }
    return result;
}
}
