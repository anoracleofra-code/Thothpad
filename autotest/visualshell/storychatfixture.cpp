// SPDX-License-Identifier: GPL-3.0-or-later
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <cstdio>
#ifdef Q_OS_WIN
#include <fcntl.h>
#include <io.h>
#endif

// Offline protocol peer: exercise actual request framing without a provider account.
int main()
{
#ifdef Q_OS_WIN
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    char header[128];
    bool failedOnce = false;
    while (std::fgets(header, sizeof(header), stdin)) {
        const int length = QByteArray(header).mid(15).trimmed().toInt();
        if (length <= 0 || length > 4 * 1024 * 1024 || !std::fgets(header, sizeof(header), stdin))
            return 1;
        QByteArray body(length, '\0');
        if (std::fread(body.data(), 1, length, stdin) != static_cast<size_t>(length))
            return 1;
        const auto request = QJsonDocument::fromJson(body).object();
        const auto operation = request.value("operation").toString();
        if (operation == "shutdown")
            return 0;
        QJsonObject result;
        bool ok = true;
        if (operation == "initialize") {
            result = {{"protocol", QJsonObject{{"minor", 1}}}, {"operations", QJsonArray{"rewrite"}}};
        } else if (operation == "rewrite") {
            const auto payload = QJsonDocument::fromJson(request.value("text").toString().toUtf8()).object();
            if (payload.value("prompt") == "fail-once" && !failedOnce) {
                failedOnce = true;
                ok = false;
            }
            result.insert("story_intelligence", QJsonObject{{"message", QString("Reply to [%1], prior messages: %2")
                .arg(payload.value("prompt").toString()).arg(payload.value("history").toArray().size())}});
        }
        const auto response = QJsonDocument(QJsonObject{{"request_id", request.value("request_id")}, {"ok", ok},
            {"error", "Simulated provider failure"}, {"result", result}}).toJson(QJsonDocument::Compact);
        std::fprintf(stdout, "Content-Length: %d\r\n\r\n", int(response.size()));
        std::fwrite(response.constData(), 1, response.size(), stdout);
        std::fflush(stdout);
    }
    return 0;
}
