// SPDX-License-Identifier: GPL-3.0-or-later
#include "writinganalysis.h"
#include <QCryptographicHash>
#include <QHash>
#include <QRegularExpression>
#include <QTextBoundaryFinder>
#include <algorithm>
#include <cmath>

namespace ghostwriter
{
namespace
{
const QRegularExpression wordPattern(QStringLiteral("[\\p{L}\\p{N}]+(?:['’\\-][\\p{L}\\p{N}]+)*"));
int words(const QString &text)
{
    int count = 0;
    auto it = wordPattern.globalMatch(text);
    while (it.hasNext()) {
        it.next();
        ++count;
    }
    return count;
}
QString bodyText(QString text)
{
    // Keep positions intact while excluding fenced code and heading markup.
    bool fenced = false;
    QChar fence;
    int fenceLength = 0;
    int offset = 0;
    const QRegularExpression fencePattern(QStringLiteral("^ {0,3}(`{3,}|~{3,})"));
    const QRegularExpression headingPattern(QStringLiteral("^ {0,3}#{1,6}\\s"));
    for (const auto &line : text.split('\n')) {
        const auto match = fencePattern.match(line);
        const bool marker = match.hasMatch();
        const bool hide = fenced || marker || headingPattern.match(line).hasMatch();
        if (marker) {
            const auto token = match.captured(1);
            if (!fenced) {
                fenced = true;
                fence = token.front();
                fenceLength = token.size();
            } else if (token.front() == fence && token.size() >= fenceLength && line.mid(match.capturedEnd()).trimmed().isEmpty())
                fenced = false;
        }
        if (hide)
            for (int i = offset; i < offset + line.size(); ++i)
                text[i] = ' ';
        offset += line.size() + 1;
    }
    const QRegularExpression code(QStringLiteral("(`+)[^`\\n]*\\1"));
    auto it = code.globalMatch(text);
    while (it.hasNext()) {
        const auto m = it.next();
        for (int i = m.capturedStart(); i < m.capturedEnd(); ++i)
            text[i] = ' ';
    }
    return text;
}
QString scopeTitle(const QJsonArray &scopes, int position, int chapterLevel, bool chapter)
{
    QJsonObject chosen;
    for (const auto &value : scopes) {
        const auto s = value.toObject();
        const int level = s.value("level").toInt();
        if (s.value("orphaned").toBool() || level == 0 || (chapter ? level != chapterLevel : level <= chapterLevel))
            continue;
        if (s.value("start").toInt() <= position && s.value("end").toInt() > position && level > chosen.value("level").toInt())
            chosen = s;
    }
    return chosen.value("title").toString();
}
}

QString WritingAnalysis::anchor(const QString &text, int start, int end)
{
    const QString context = text.mid(qMax(0, start - 64), qMin(int(text.size()), end + 64) - qMax(0, start - 64));
    return QString::fromLatin1(QCryptographicHash::hash(context.toUtf8(), QCryptographicHash::Sha256).toHex());
}

WritingAnalysis WritingAnalysis::build(const QString &text, const QJsonObject &workspace)
{
    WritingAnalysis result;
    result.text = text;
    result.scopes = workspace.value("scopes").toArray();
    const QString body = bodyText(text);
    result.body = body;
    const auto review = workspace.value("writing_review").toObject();
    const auto aliases = review.value("aliases").toObject();
    const auto assignments = review.value("assignments").toObject();
    int chapterLevel = 7;
    for (const auto &v : result.scopes) {
        const auto s = v.toObject();
        const int level = s.value("level").toInt();
        if (!s.value("orphaned").toBool() && level > 0)
            chapterLevel = qMin(chapterLevel, level);
    }
    int start = -1;
    QChar close;
    for (int i = 0; i < body.size(); ++i) {
        const QChar c = body[i];
        const QChar previous = i ? body[i - 1] : QChar();
        const QChar next = i + 1 < body.size() ? body[i + 1] : QChar();
        if (previous == '\\')
            continue;
        if ((c == '\'' || c == QChar(0x2019)) && previous.isLetterOrNumber() && next.isLetterOrNumber())
            continue;
        if (start >= 0 && (i - start > 5000 || (c == '\n' && next == '\n')))
            start = -1;
        if (start >= 0) {
            if (c != close || previous.isSpace() || next.isLetterOrNumber())
                continue;
            const int end = i + 1;
            const QString content = text.mid(start + 1, i - start - 1);
            QJsonObject line{{"start", start},
                             {"end", end},
                             {"quote", text.mid(start, end - start)},
                             {"content", content},
                             {"words", words(content)},
                             {"speaker_id", ""},
                             {"speaker", "Unknown"},
                             {"confidence", "Unknown"},
                             {"chapter", scopeTitle(result.scopes, start, chapterLevel, true)},
                             {"scene", scopeTitle(result.scopes, start, chapterLevel, false)},
                             {"anchor", anchor(text, start, end)}};
            result.dialogue.append(line);
            start = -1;
        } else if (c == '"' || c == QChar(0x201c) || c == QChar(0x2018) || c == '\'') {
            if (previous.isLetterOrNumber() || next.isNull() || next.isSpace())
                continue;
            start = i;
            close = c == QChar(0x201c) ? QChar(0x201d) : c == QChar(0x2018) ? QChar(0x2019) : c;
        }
    }
    struct Speaker {
        QString id;
        QString name;
        QRegularExpression before;
        QRegularExpression after;
        QRegularExpression actor;
    };
    QList<Speaker> speakers;
    QStringList knownNames;
    const QString verbs = QStringLiteral(
        "(?:said|asked|replied|answered|whispered|shouted|muttered|yelled|cried|called|murmured|snapped|added|continued|exclaimed|insisted|demanded|hissed|"
        "growled|told|repeated|suggested|warned|began|remarked|recalled|admitted|agreed|reassured|interrupted|protested|wondered)");
    const QString actions = QStringLiteral("(?:nodded|shook|smiled|grinned|laughed|chuckled|sighed|frowned|shrugged|spat|rode|pointed|pulled|pushed|leaned|turned|looked|glanced|stared|gazed|lifted|raised|lowered|reached|stepped|stood|sat|uncorked|stashed|placed|waved|folded|crossed|held|took|drew|squinted|snorted)");
    const QString adverbs = QStringLiteral("(?:[\\p{L}]+ly\\s+)?");
    for (const auto &v : workspace.value("agents").toArray()) {
        const auto agent = v.toObject();
        if (agent.value("kind").toString() != "character" || agent.value("archived").toBool())
            continue;
        const QString id = agent.value("id").toString(), name = agent.value("name").toString();
        QStringList names;
        if (!name.trimmed().isEmpty())
            names << name;
        for (const auto &alias : aliases.value(id).toArray())
            if (!alias.toString().trimmed().isEmpty())
                names << alias.toString().trimmed();
        if (names.isEmpty())
            continue;
        knownNames.append(names);
        for (auto &n : names)
            n = QRegularExpression::escape(n);
        const QString who = "(?:" + names.join('|') + ")";
        const QString tag = "(?:" + who + "\\s+" + adverbs + verbs + "|" + verbs + "\\s+" + adverbs + who + ")";
        speakers.append({id,
                         name,
                         QRegularExpression("\\b" + tag + "\\b[^.!?]*[.!?]?\\s*$", QRegularExpression::CaseInsensitiveOption),
                         QRegularExpression("^[\\s,;:!?.]*" + tag + "\\b", QRegularExpression::CaseInsensitiveOption),
                         QRegularExpression("(?:^|[.!?]\\s+)" + who + "\\s+" + adverbs + "(?:" + actions + "|" + verbs + ")\\b", QRegularExpression::CaseInsensitiveOption)});
    }
    const QString properName = QStringLiteral("[\\p{Lu}][\\p{L}’'\\-]*(?:[ \\t]+[\\p{Lu}][\\p{L}’'\\-]*){0,2}");
    const QRegularExpression namesInTags("(?<before>" + properName + ")\\s+" + adverbs + "(?:" + verbs + "|" + actions + ")\\b|\\b" + verbs + "\\s+" + adverbs + "(?<after>" + properName + ")");
    QString narration = body;
    for (const auto &value : result.dialogue) {
        const auto quote = value.toObject();
        for (int p = quote.value("start").toInt(); p < quote.value("end").toInt(); ++p)
            if (!narration[p].isSpace()) narration[p] = ' ';
    }
    auto tags = namesInTags.globalMatch(narration);
    const QStringList pronouns{"I", "He", "She", "They", "It", "We", "You", "The", "Then", "Someone", "Nobody", "Everyone", "Something"};
    while (tags.hasNext()) {
        const auto tag = tags.next();
        const QString name = tag.captured("before").isEmpty() ? tag.captured("after") : tag.captured("before");
        if (pronouns.contains(name, Qt::CaseInsensitive))
            continue;
        bool known = false;
        for (const auto &existing : knownNames)
            if (name.compare(existing, Qt::CaseInsensitive) == 0 || name.endsWith(" " + existing, Qt::CaseInsensitive)) {
                known = true;
                break;
            }
        if (known)
            continue;
        knownNames.append(name);
        const QString who = QRegularExpression::escape(name);
        const QString pattern = "(?:" + who + "\\s+" + adverbs + verbs + "|" + verbs + "\\s+" + adverbs + who + ")";
        speakers.append({"detected:" + name.toCaseFolded(),
                         name,
                         QRegularExpression("\\b" + pattern + "\\b[^.!?]*[.!?]?\\s*$", QRegularExpression::CaseInsensitiveOption),
                         QRegularExpression("^[\\s,;:!?.]*" + pattern + "\\b", QRegularExpression::CaseInsensitiveOption),
                         QRegularExpression("(?:^|[.!?]\\s+)" + who + "\\s+" + adverbs + "(?:" + actions + "|" + verbs + ")\\b", QRegularExpression::CaseInsensitiveOption)});
    }
    // Attribution uses only narration between quotes, never names inside spoken text.
    // A blank line ends the turn; physical ebook line wrapping does not.
    const QRegularExpression paragraphBreak(QStringLiteral("\\n[ \\t\\r]*\\n|\\n(?=[ \\t\\x{00a0}]{2,}\\S)"));
    QList<int> boundaries{0};
    auto breaks = paragraphBreak.globalMatch(body);
    while (breaks.hasNext()) boundaries << breaks.next().capturedEnd();
    boundaries << int(body.size());
    int paragraph = 0;
    for (int n = 0; n < result.dialogue.size(); ++n) {
        auto line = result.dialogue[n].toObject();
        const int begin = line.value("start").toInt(), end = line.value("end").toInt();
        while (paragraph + 1 < boundaries.size() - 1 && boundaries[paragraph + 1] <= begin) ++paragraph;
        const int pStart = boundaries[paragraph], pEnd = boundaries[paragraph + 1];
        const auto previousLine = n ? result.dialogue[n - 1].toObject() : QJsonObject();
        const int left = qMax(pStart, previousLine.value("end").toInt());
        const int right = n + 1 < result.dialogue.size() ? qMin(pEnd, result.dialogue[n + 1].toObject().value("start").toInt()) : pEnd;
        const QString before = body.mid(left, begin - left).trimmed();
        const QString after = body.mid(end, right - end).trimmed();
        QList<int> explicitMatches, actors;
        QString tag;
        for (int j = 0; j < speakers.size(); ++j) {
            const auto a = speakers[j].after.match(after), b = speakers[j].before.match(before);
            if (a.hasMatch() || b.hasMatch()) {
                explicitMatches << j;
                tag = (a.hasMatch() ? a : b).captured().trimmed();
            }
            if (speakers[j].actor.match(before).hasMatch() || speakers[j].actor.match(after).hasMatch()) actors << j;
        }
        int selected = -1;
        QString confidence;
        if (explicitMatches.size() == 1) {
            selected = explicitMatches.first();
            confidence = speakers[selected].id.startsWith("detected:") ? "Detected name in tag" : "Explicit tag";
        } else if (explicitMatches.isEmpty() && actors.size() == 1) {
            selected = actors.first();
            confidence = "Inferred from action beat";
            tag.clear();
        } else if (explicitMatches.isEmpty() && actors.isEmpty() && left > pStart && !previousLine.value("speaker_id").toString().isEmpty()) {
            // Continue the same paragraph only when narration introduces no other named actor.
            const QRegularExpression namedSubject("\\b" + properName + "\\s+[\\p{Ll}]");
            if (!namedSubject.match(before).hasMatch() || QRegularExpression("^(He|She|They|It)\\b").match(before).hasMatch()) {
                for (int j = 0; j < speakers.size(); ++j)
                    if (speakers[j].id == previousLine.value("speaker_id").toString()) selected = j;
                confidence = "Inferred from same paragraph";
            }
        }
        if (selected >= 0) {
            line.insert("speaker_id", speakers[selected].id);
            line.insert("speaker", speakers[selected].name);
            line.insert("confidence", confidence);
            if (!tag.isEmpty()) line.insert("tag", tag);
        }
        result.dialogue[n] = line;
    }
    QHash<QString, int> anchorCounts;
    for (const auto &v : result.dialogue)
        ++anchorCounts[v.toObject().value("anchor").toString()];
    for (int i = 0; i < result.dialogue.size(); ++i) {
        auto line = result.dialogue[i].toObject();
        const auto key = line.value("anchor").toString();
        line.insert("unique_anchor", anchorCounts[key] == 1);
        const QString assigned = anchorCounts[key] == 1 ? assignments.value(key).toString() : QString();
        for (const auto &s : speakers)
            if (assigned == s.id) {
                line.insert("speaker_id", s.id);
                line.insert("speaker", s.name);
                line.insert("confidence", "Confirmed by you");
            }
        if (assigned == "unknown") {
            line.insert("speaker_id", "");
            line.insert("speaker", "Unknown");
            line.insert("confidence", "Confirmed unknown");
        }
        result.dialogue[i] = line;
    }
    QTextBoundaryFinder finder(QTextBoundaryFinder::Sentence, body);
    int previous = 0;
    for (int end = finder.toNextBoundary(); end >= 0; end = finder.toNextBoundary()) {
        int begin = previous;
        previous = end;
        while (begin < end && body[begin].isSpace())
            ++begin;
        int last = end;
        while (last > begin && body[last - 1].isSpace())
            --last;
        const int count = words(body.mid(begin, last - begin));
        if (count)
            result.sentences.append(QJsonObject{{"start", begin}, {"end", last}, {"words", count}});
    }
    return result;
}

QJsonObject WritingAnalysis::report(int start, int end, const QString &speaker) const
{
    start = qBound(0, start, int(text.size()));
    end = qBound(start, end, int(text.size()));
    QString content = body.mid(start, end - start);
    int dialogueWords = 0, lines = 0, unknown = 0, tagged = 0;
    QHash<QString, int> voiceCounts;
    QJsonArray selected;
    for (const auto &v : dialogue) {
        const auto line = v.toObject();
        if (line.value("start").toInt() < start || line.value("end").toInt() > end)
            continue;
        const QString id = line.value("speaker_id").toString();
        if (!speaker.isEmpty() && (speaker == "unknown" ? !id.isEmpty() : id != speaker))
            continue;
        selected.append(line);
        dialogueWords += line.value("words").toInt();
        ++lines;
        if (id.isEmpty())
            ++unknown;
        if (!line.value("tag").toString().isEmpty())
            ++tagged;
        voiceCounts[line.value("speaker").toString()] += line.value("words").toInt();
    }
    if (!speaker.isEmpty()) {
        content.clear();
        for (const auto &v : selected)
            content += v.toObject().value("content").toString() + '\n';
    }
    int wordCount = 0, letters = 0, longWords = 0;
    QHash<QString, int> frequency, phrases;
    QStringList tokens;
    int previousWordEnd = -1;
    auto it = wordPattern.globalMatch(content);
    while (it.hasNext()) {
        const auto match = it.next();
        const QString word = match.captured().toCaseFolded();
        if (previousWordEnd >= 0 && !content.mid(previousWordEnd, match.capturedStart() - previousWordEnd).trimmed().isEmpty())
            tokens.clear();
        previousWordEnd = match.capturedEnd();
        ++wordCount;
        ++frequency[word];
        tokens << word;
        if (tokens.size() > 3)
            tokens.removeFirst();
        if (tokens.size() == 3)
            ++phrases[tokens.join(' ')];
        for (const QChar c : word)
            if (c.isLetter())
                ++letters;
        if (word.size() > 6)
            ++longWords;
    }
    QList<int> lengths;
    QJsonArray passages;
    for (const auto &v : sentences) {
        const auto s = v.toObject();
        if (s.value("start").toInt() >= start && s.value("end").toInt() <= end) {
            lengths << s.value("words").toInt();
            passages.append(s);
        }
    }
    if (!speaker.isEmpty()) {
        lengths.clear();
        for (const auto &v : selected)
            lengths << v.toObject().value("words").toInt();
    }
    double mean = 0, variance = 0;
    for (int n : lengths)
        mean += n;
    if (!lengths.isEmpty())
        mean /= lengths.size();
    for (int n : lengths)
        variance += (n - mean) * (n - mean);
    if (!lengths.isEmpty())
        variance /= lengths.size();
    const auto ranked = [](const QHash<QString, int> &counts, int minimum) {
        QList<QString> keys = counts.keys();
        std::sort(keys.begin(), keys.end(), [&counts](const auto &a, const auto &b) {
            return counts[a] == counts[b] ? a < b : counts[a] > counts[b];
        });
        QJsonArray rows;
        for (const auto &key : keys) {
            if (counts[key] < minimum || rows.size() >= 40)
                break;
            rows.append(QJsonObject{{"text", key}, {"count", counts[key]}});
        }
        return rows;
    };
    return {{"words", wordCount},
            {"sentences", lengths.size()},
            {"mean_length", mean},
            {"length_variation", std::sqrt(variance)},
            {"dialogue_words", dialogueWords},
            {"dialogue_percent", wordCount ? 100.0 * dialogueWords / wordCount : 0},
            {"dialogue_lines", lines},
            {"unknown_lines", unknown},
            {"tagged_lines", tagged},
            {"long_word_percent", wordCount ? 100.0 * longWords / wordCount : 0},
            {"coleman_liau", wordCount ? 5.88 * letters / wordCount - 29.6 * lengths.size() / wordCount - 15.8 : 0},
            {"frequency", ranked(frequency, 2)},
            {"phrases", ranked(phrases, 2)},
            {"voices", ranked(voiceCounts, 1)},
            {"dialogue", selected},
            {"passages", passages}};
}
}
