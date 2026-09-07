/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef STORY_RESPONSE_H
#define STORY_RESPONSE_H

#include <QCoreApplication>
#include <QJsonArray>
#include <QJsonObject>

namespace ghostwriter
{
inline bool storyProviderRequiresSavedCredential(const QString &kind)
{
    return kind == QStringLiteral("openrouter") || kind == QStringLiteral("opencode_zen")
        || kind == QStringLiteral("opencode_go") || kind == QStringLiteral("openai")
        || kind == QStringLiteral("anthropic") || kind == QStringLiteral("gemini")
        || kind == QStringLiteral("gemini_oauth");
}

inline QString storyResponseError(const QJsonObject &response)
{
    const auto fallback = []() { return QCoreApplication::translate("StoryIntelligenceController", "Story Intelligence request failed. Check Model Settings and try again."); };
    if (!response.value(QStringLiteral("ok")).toBool()) {
        const auto error = response.value(QStringLiteral("error"));
        const QString message = error.isObject() ? error.toObject().value(QStringLiteral("message")).toString() : error.toString();
        return message.trimmed().isEmpty() ? fallback() : message;
    }
    const auto result = response.value(QStringLiteral("result")).toObject();
    const auto errors = result.value(QStringLiteral("llm_errors")).toArray();
    if (!errors.isEmpty()) {
        const QString message = errors.first().toString();
        return message.trimmed().isEmpty() ? fallback() : message;
    }
    const auto story = result.value(QStringLiteral("story_intelligence")).toObject();
    if (story.value(QStringLiteral("message")).toString().trimmed().isEmpty()
        && result.value(QStringLiteral("output_text")).toString().trimmed().isEmpty()
        && story.value(QStringLiteral("tool_calls")).toArray().isEmpty()) {
        return QCoreApplication::translate("StoryIntelligenceController", "The provider returned no text. Check the model and generation settings, then retry.");
    }
    return {};
}
}
#endif
