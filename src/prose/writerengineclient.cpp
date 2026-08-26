/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "writerengineclient.h"

#include <QCoreApplication>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QMetaObject>
#include <QPointer>
#include <QProcessEnvironment>
#include <QStandardPaths>
#include <QTimer>

#include "performancepolicy.h"

#ifdef Q_OS_WIN
#include <qt_windows.h>
#endif

namespace ghostwriter
{
namespace
{
constexpr int ProtocolMajor = 1;
constexpr int ProtocolMinor = 2;
constexpr qsizetype MaxRequestFrameBytes = 64 * 1024 * 1024;
constexpr qsizetype MaxResponseFrameBytes = 64 * 1024 * 1024;
constexpr qsizetype MaxHeaderBytes = 16 * 1024;
constexpr qsizetype MaxResyncWindow = 256 * 1024;

QThreadPool &responseParsePool()
{
    // Process-lifetime pool: response tasks use QPointer guards and must not
    // make a closing editor wait for a large JSON frame to finish parsing.
    static QThreadPool *pool = [] {
        auto *value = new QThreadPool;
        value->setMaxThreadCount(1);
        value->setExpiryTimeout(-1);
        return value;
    }();
    return *pool;
}

QProcessEnvironment sidecarEnvironment()
{
    const QProcessEnvironment system = QProcessEnvironment::systemEnvironment();
    QProcessEnvironment result;
    const QStringList allowed = {
        QStringLiteral("SystemRoot"),
        QStringLiteral("WINDIR"),
        QStringLiteral("TEMP"),
        QStringLiteral("TMP"),
        QStringLiteral("USERPROFILE"),
        QStringLiteral("HOME"),
        QStringLiteral("APPDATA"),
        QStringLiteral("LOCALAPPDATA"),
        QStringLiteral("XDG_DATA_HOME"),
        QStringLiteral("XDG_CONFIG_HOME"),
        QStringLiteral("XDG_CACHE_HOME"),
        QStringLiteral("LANG"),
        QStringLiteral("LC_ALL"),
        QStringLiteral("THOTHPAD_DATA_DIR"),
        QStringLiteral("THOTHPAD_SIDECAR_TRACE"),
    };
    for (const QString &name : allowed) {
        if (system.contains(name)) {
            result.insert(name, system.value(name));
        }
    }
    // Keep the sidecar environment scrubbed while still allowing system
    // helpers and development Python launchers to resolve platform runtimes.
#ifdef Q_OS_WIN
    result.insert(QStringLiteral("PATH"), QStringLiteral("C:\\Windows\\System32;C:\\Windows;C:\\Windows\\System32\\Wbem"));
#else
    if (system.contains(QStringLiteral("PATH"))) {
        result.insert(QStringLiteral("PATH"), system.value(QStringLiteral("PATH")));
    }
#endif
    return result;
}
}

WriterEngineClient::WriterEngineClient(QObject *parent)
    : QObject(parent)
{
#ifdef Q_OS_WIN
    m_process.setCreateProcessArgumentsModifier([](QProcess::CreateProcessArguments *arguments) {
        arguments->flags |= CREATE_NO_WINDOW;
    });
#endif
    m_deadlineTimer.setInterval(1000);
    connect(&m_deadlineTimer, &QTimer::timeout, this, [this]() {
        const qint64 now = QDateTime::currentMSecsSinceEpoch();
        for (auto iterator = m_deadlines.cbegin(); iterator != m_deadlines.cend(); ++iterator) {
            if (iterator.value() <= now) {
                const QString requestId = iterator.key();
                if (m_timedOutRequests.contains(requestId)) {
                    continue;
                }
                m_timedOutRequests.insert(requestId);
                const QString operation = m_pendingOperations.value(requestId);
                // A timed-out request is cancelled, not engine-fatal: the
                // stream resyncs around lost frames, so killing the sidecar
                // would only restart the analysis loop it is mid-way through.
                QFile engineLog(QCoreApplication::applicationDirPath() + QStringLiteral("/thothpad-engine.log"));
                if (engineLog.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
                    engineLog.write(QDateTime::currentDateTime().toString(Qt::ISODate).toUtf8());
                    engineLog.write(QString(QStringLiteral(" [timeout] %1 (request cancelled; engine kept)\n")).arg(operation).toUtf8());
                    engineLog.close();
                }
                emit engineError(tr("ThothPad Engine timed out while running %1.").arg(operation));
                QJsonObject cancelRequest;
                cancelRequest.insert(QStringLiteral("protocol_major"), ProtocolMajor);
                cancelRequest.insert(QStringLiteral("protocol_minor"), ProtocolMinor);
                cancelRequest.insert(QStringLiteral("request_id"), QUuid::createUuid().toString(QUuid::WithoutBraces));
                cancelRequest.insert(QStringLiteral("operation"), QStringLiteral("cancel"));
                cancelRequest.insert(QStringLiteral("target_request_id"), requestId);
                cancelRequest.insert(QStringLiteral("persist"), false);
                writeMessage(cancelRequest);
                return;
            }
        }
    });
    m_deadlineTimer.start();
    connect(&m_process, &QProcess::readyReadStandardOutput,
        this, &WriterEngineClient::readStandardOutput);
    connect(&m_process, &QProcess::readyReadStandardError,
        this, &WriterEngineClient::readStandardError);
    connect(&m_process, &QProcess::started,
        this, &WriterEngineClient::processStarted);
    connect(&m_process, &QProcess::finished,
        this, &WriterEngineClient::processFinished);
    connect(&m_process, &QProcess::errorOccurred,
        this, &WriterEngineClient::processError);
}

WriterEngineClient::~WriterEngineClient()
{
    stop();
}

bool WriterEngineClient::isReady() const
{
    return m_ready;
}

bool WriterEngineClient::supportsOperation(const QString &operation) const
{
    return m_operations.contains(operation);
}

int WriterEngineClient::negotiatedProtocolMinor() const
{
    return m_negotiatedProtocolMinor;
}

QString WriterEngineClient::enginePath() const
{
    return m_enginePath;
}

void WriterEngineClient::setEnginePath(const QString &path)
{
    m_enginePath = path;
}

QString WriterEngineClient::resolveEngineProgram(
    QStringList &arguments,
    QString &workingDirectory) const
{
    if (!m_enginePath.isEmpty()) {
        return m_enginePath;
    }

    const QString configured = qEnvironmentVariable("THOTHPAD_ENGINE");
    if (!configured.isEmpty()) {
        return configured;
    }

    const QString appDirectory = QCoreApplication::applicationDirPath();
#ifdef Q_OS_WIN
    const QString bundled = QDir(appDirectory).filePath(
        QStringLiteral("writer-engine/writer-engine.exe"));
#else
    const QString bundled = QDir(appDirectory).filePath(
        QStringLiteral("writer-engine/writer-engine"));
#endif
    if (QFileInfo::exists(bundled)) {
        return bundled;
    }

    const QStringList developmentCandidates = {
        QDir(appDirectory).absoluteFilePath(QStringLiteral("../writer-engine")),
        QDir(appDirectory).absoluteFilePath(QStringLiteral("../../writer-engine")),
        QDir(appDirectory).absoluteFilePath(QStringLiteral("../../../writer-engine")),
    };
    for (const QString &developmentEngine : developmentCandidates) {
        if (!QFileInfo::exists(QDir(developmentEngine).filePath(
                QStringLiteral("backend/sidecar.py")))) {
            continue;
        }
        workingDirectory = developmentEngine;
        arguments << QStringLiteral("-m") << QStringLiteral("backend.sidecar");
#ifdef Q_OS_WIN
        const QString venvPython = QDir(developmentEngine).filePath(QStringLiteral(".venv/Scripts/python.exe"));
#else
        const QString venvPython = QDir(developmentEngine).filePath(QStringLiteral(".venv/bin/python"));
#endif
        if (QFileInfo::exists(venvPython)) {
            return venvPython;
        }
#ifdef Q_OS_WIN
        return QStandardPaths::findExecutable(QStringLiteral("python.exe"));
#else
        return QStandardPaths::findExecutable(QStringLiteral("python3"));
#endif
    }
    return {};
}

void WriterEngineClient::start()
{
    if (m_process.state() != QProcess::NotRunning) {
        return;
    }

    QStringList arguments;
    QString workingDirectory;
    const QString program = resolveEngineProgram(arguments, workingDirectory);
    if (program.isEmpty()) {
        emit engineError(tr("ThothPad Engine could not be found."));
        return;
    }

    m_stopping = false;
    m_aborting = false;
    ++m_processGeneration;
    // A restart reuses this QProcess object: drain any bytes the previous
    // engine left in the pipes/Qt buffers, or its tail frames bleed into the
    // new session's stream and desynchronize the frame parser.
    m_buffer.clear();
    m_bufferOffset = 0;
    m_process.readAllStandardOutput();
    m_process.readAllStandardError();
    if (!workingDirectory.isEmpty()) {
        m_process.setWorkingDirectory(workingDirectory);
    }
    m_process.setProgram(program);
    m_process.setArguments(arguments);
    m_process.setProcessEnvironment(sidecarEnvironment());
    m_process.setProcessChannelMode(QProcess::SeparateChannels);
    m_process.start(QIODevice::ReadWrite);
}

void WriterEngineClient::stop()
{
    if (m_process.state() == QProcess::NotRunning) {
        return;
    }
    m_stopping = true;
    send(QStringLiteral("shutdown"));
    m_process.closeWriteChannel();
    if (!m_process.waitForFinished(100)) {
        forceTerminateProcessTree();
        m_process.waitForFinished(25);
    }
    setReady(false);
}

QString WriterEngineClient::send(const QString &operation, const QJsonObject &payload)
{
    if (m_process.state() != QProcess::Running) {
        return {};
    }

    const QString requestId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    QJsonObject message = payload;
    message.insert(QStringLiteral("protocol_major"), ProtocolMajor);
    message.insert(QStringLiteral("protocol_minor"), ProtocolMinor);
    message.insert(QStringLiteral("request_id"), requestId);
    message.insert(QStringLiteral("operation"), operation);
    if (!message.contains(QStringLiteral("persist"))) {
        message.insert(QStringLiteral("persist"), false);
    }
    if (!writeMessage(message)) {
        return {};
    }
    m_pendingOperations.insert(requestId, operation);
    int timeoutSeconds = 30;
    if (operation == QStringLiteral("initialize")) {
        timeoutSeconds = 60;
    } else if (operation == QStringLiteral("open_document") || operation == QStringLiteral("analyze_region")) {
        // First hit may wait on the worker's cold import of the analyzer
        // stack (antivirus real-time scanning makes this minutes).
        timeoutSeconds = 180;
    } else if (operation == QStringLiteral("analyze_document") || operation == QStringLiteral("analyze_manuscript")) {
        timeoutSeconds = 300;
    } else if (operation == QStringLiteral("rewrite")) {
        const QJsonObject provider = payload.value(QStringLiteral("provider")).toObject();
        const int providerTimeout = qBound(5,
            provider.value(QStringLiteral("timeout")).toInt(180), 600);
        const int passes = qBound(1, payload.value(QStringLiteral("passes")).toInt(1), 5);
        timeoutSeconds = providerTimeout * passes + 30;
    }
    m_deadlines.insert(requestId,
        QDateTime::currentMSecsSinceEpoch() + static_cast<qint64>(timeoutSeconds) * 1000);
    return requestId;
}

void WriterEngineClient::cancel(const QString &requestId)
{
    if (requestId.isEmpty()) {
        return;
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("target_request_id"), requestId);
    send(QStringLiteral("cancel"), payload);
}

bool WriterEngineClient::writeMessage(const QJsonObject &message)
{
    const QByteArray body = QJsonDocument(message).toJson(QJsonDocument::Compact);
    if (body.size() > MaxRequestFrameBytes) {
        emit engineError(tr("ThothPad Engine request exceeds the 64 MiB limit."));
        return false;
    }
    const QByteArray header = "Content-Length: " + QByteArray::number(body.size()) + "\r\n\r\n";
    const QByteArray frame = header + body;
    return m_process.write(frame) == frame.size();
}

void WriterEngineClient::readStandardOutput()
{
    m_buffer.append(m_process.readAllStandardOutput());
    parseMessages();
}

void WriterEngineClient::compactBuffer()
{
    // Amortized compaction: shift the dead prefix at most once per megabyte
    // consumed instead of once per frame.
    if (m_bufferOffset > 1024 * 1024) {
        m_buffer.remove(0, m_bufferOffset);
        m_bufferOffset = 0;
    }
}

void WriterEngineClient::parseMessages()
{
    // Consume frames through a read offset: memmoving the whole multi-megabyte
    // buffer once per frame (multi-page finding/overlay streams) caused
    // measurable GUI-thread stutter. The buffer compacts once per drain.
    while (true) {
        const qsizetype headerEnd = m_buffer.indexOf("\r\n\r\n", m_bufferOffset);
        if (headerEnd < 0) {
            if ((m_buffer.size() - m_bufferOffset) > MaxHeaderBytes) {
                // Desynchronized stream: try to resync to the next frame
                // header instead of tearing the engine down.
                const QByteArray headerStart = QByteArrayLiteral("Content-Length:");
                const int resync = m_buffer.indexOf(headerStart, m_bufferOffset + 1);
                if (resync > 0) {
                    QFile engineLog(QCoreApplication::applicationDirPath() + QStringLiteral("/thothpad-engine.log"));
                    if (engineLog.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
                        engineLog.write(QDateTime::currentDateTime().toString(Qt::ISODate).toUtf8());
                        engineLog.write(QString(QStringLiteral(" [stream] resync(oversized): skipped %1 bytes at %2\n"))
                                            .arg(resync - m_bufferOffset)
                                            .arg(m_bufferOffset)
                                            .toUtf8());
                        engineLog.close();
                    }
                    m_bufferOffset = resync;
                    continue;
                }
                const QByteArray preview = m_buffer.mid(m_bufferOffset, 96);
                if ((m_buffer.size() - m_bufferOffset) < MaxResponseFrameBytes) {
                    // Mid-body desync and the next frame header has not
                    // arrived yet: keep the bytes and wait — the parser
                    // resyncs when the next "Content-Length:" lands.
                    QFile engineLog(QCoreApplication::applicationDirPath() + QStringLiteral("/thothpad-engine.log"));
                    if (engineLog.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
                        engineLog.write(QDateTime::currentDateTime().toString(Qt::ISODate).toUtf8());
                        engineLog.write(QString(QStringLiteral(" [stream] desync-wait pending=%1 preview=%2\n"))
                                            .arg(m_buffer.size() - m_bufferOffset)
                                            .arg(QString::fromLatin1(preview.toHex()))
                                            .toUtf8());
                        engineLog.close();
                    }
                    compactBuffer();
                    return;
                }
                QFile engineLog(QCoreApplication::applicationDirPath() + QStringLiteral("/thothpad-engine.log"));
                if (engineLog.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
                    engineLog.write(QDateTime::currentDateTime().toString(Qt::ISODate).toUtf8());
                    engineLog.write(QString(QStringLiteral(" [stream] oversized-header pending=%1 preview=%2\n"))
                                        .arg(m_buffer.size() - m_bufferOffset)
                                        .arg(QString::fromLatin1(preview.toHex()))
                                        .toUtf8());
                    engineLog.close();
                }
                m_buffer.clear();
                m_bufferOffset = 0;
                abortEngine(tr("ThothPad Engine returned an oversized frame header."));
            }
            compactBuffer();
            return;
        }

        if ((headerEnd - m_bufferOffset) > MaxHeaderBytes) {
            // A header span this large means we are mid-desync inside a
            // body: resync to the next frame header instead of dying.
            const QByteArray headerStart = QByteArrayLiteral("Content-Length:");
            const int resync = m_buffer.indexOf(headerStart, m_bufferOffset + 1);
            if (resync > 0) {
                QFile engineLog(QCoreApplication::applicationDirPath() + QStringLiteral("/thothpad-engine.log"));
                if (engineLog.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
                    engineLog.write(QDateTime::currentDateTime().toString(Qt::ISODate).toUtf8());
                    engineLog.write(QString(QStringLiteral(" [stream] resync(header-span): skipped %1 bytes at %2\n"))
                                        .arg(resync - m_bufferOffset)
                                        .arg(m_bufferOffset)
                                        .toUtf8());
                    engineLog.close();
                }
                m_bufferOffset = resync;
                continue;
            }
            m_buffer.clear();
            m_bufferOffset = 0;
            abortEngine(tr("ThothPad Engine returned an oversized frame header."));
            return;
        }

        const QByteArray header = m_buffer.mid(m_bufferOffset, headerEnd - m_bufferOffset);
        if (!header.startsWith("Content-Length:")) {
            // Self-heal a desynchronized stream: scan forward for the next
            // plausible frame header instead of tearing the engine down. The
            // skipped bytes are logged so the underlying writer race can be
            // diagnosed from the engine log after the fact.
            const QByteArray headerStart = QByteArrayLiteral("Content-Length:");
            const int resync = m_buffer.indexOf(headerStart, m_bufferOffset + 1);
            if (resync > 0 && (headerEnd < 0 || resync < headerEnd + MaxResyncWindow)) {
                QFile engineLog(QCoreApplication::applicationDirPath() + QStringLiteral("/thothpad-engine.log"));
                if (engineLog.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
                    engineLog.write(QDateTime::currentDateTime().toString(Qt::ISODate).toUtf8());
                    engineLog.write(QString(QStringLiteral(" [stream] resync: skipped %1 bytes at %2; skipped-head=%3\n"))
                                        .arg(resync - m_bufferOffset)
                                        .arg(m_bufferOffset)
                                        .arg(QString::fromLatin1(header.left(80).toHex()))
                                        .toUtf8());
                    engineLog.close();
                }
                m_bufferOffset = resync;
                continue;
            }
            QFile engineLog(QCoreApplication::applicationDirPath() + QStringLiteral("/thothpad-engine.log"));
            if (engineLog.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
                engineLog.write(QDateTime::currentDateTime().toString(Qt::ISODate).toUtf8());
                engineLog.write(QString(QStringLiteral(" [stream] invalid-header offset=%1 preview=%2\n"))
                                    .arg(m_bufferOffset)
                                    .arg(QString::fromLatin1(header.left(120).toHex()))
                                    .toUtf8());
                engineLog.close();
            }
            m_buffer.clear();
            m_bufferOffset = 0;
            abortEngine(tr("ThothPad Engine returned an invalid frame."));
            return;
        }

        bool ok = false;
        const qlonglong contentLength = header.mid(sizeof("Content-Length:") - 1)
                                            .trimmed().toLongLong(&ok);
        if (!ok || contentLength < 0 || contentLength > MaxResponseFrameBytes) {
            m_buffer.clear();
            m_bufferOffset = 0;
            abortEngine(tr("ThothPad Engine returned an invalid frame size."));
            return;
        }

        // headerEnd is an absolute index into m_buffer, so calculate an
        // absolute frame end as well. Treating headerEnd as a relative frame
        // size causes every frame after the first to advance too far and can
        // silently strand profile/analysis responses in the buffer.
        const qsizetype bodyStart = headerEnd + 4;
        const qsizetype frameEnd = bodyStart + static_cast<qsizetype>(contentLength);
        if (m_buffer.size() < frameEnd) {
            compactBuffer();
            return;
        }

        const QByteArray body = m_buffer.mid(bodyStart, contentLength);
        {
            QFile engineLog(QCoreApplication::applicationDirPath() + QStringLiteral("/thothpad-engine.log"));
            if (engineLog.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
                engineLog.write(QDateTime::currentDateTime().toString(Qt::ISODate).toUtf8());
                engineLog.write(QString(QStringLiteral(" [frame] offset=%1 len=%2 head=%3\n"))
                                    .arg(m_bufferOffset)
                                    .arg(contentLength)
                                    .arg(QString::fromLatin1(body.left(40).toHex()))
                                    .toUtf8());
                engineLog.close();
            }
        }
        m_bufferOffset = frameEnd;
        compactBuffer();
        const QPointer<WriterEngineClient> guard(this);
        const quint64 generation = m_processGeneration;
        responseParsePool().start([guard, body, generation]() {
            QJsonParseError error;
            const QJsonDocument document = QJsonDocument::fromJson(body, &error);
            if (!guard) {
                return;
            }
            if (error.error != QJsonParseError::NoError || !document.isObject()) {
                QMetaObject::invokeMethod(
                    guard,
                    [guard]() {
                        if (guard) {
                            emit guard->engineError(WriterEngineClient::tr("ThothPad Engine returned malformed JSON."));
                        }
                    },
                    Qt::QueuedConnection);
                return;
            }
            const QJsonObject response = document.object();
            QMetaObject::invokeMethod(
                guard,
                [guard, response, generation]() {
                    if (guard) {
                        guard->deliverParsedResponse(response, generation);
                    }
                },
                Qt::QueuedConnection);
        });
    }
}

void WriterEngineClient::deliverParsedResponse(const QJsonObject &response, quint64 generation)
{
    if (generation != m_processGeneration) {
        return;
    }
    const QString requestId = response.value(QStringLiteral("request_id")).toString();
    m_deadlines.remove(requestId);
    m_timedOutRequests.remove(requestId);
    const QString operation = m_pendingOperations.take(requestId);
    if (operation == QStringLiteral("initialize") && response.value(QStringLiteral("ok")).toBool()) {
        const QJsonObject result = response.value(QStringLiteral("result")).toObject();
        m_negotiatedProtocolMinor = result.value(QStringLiteral("protocol")).toObject().value(QStringLiteral("minor")).toInt();
        m_operations.clear();
        const QJsonArray operations = result.value(QStringLiteral("operations")).toArray();
        for (const QJsonValue &value : operations) {
            const QString candidate = value.toString();
            if (!candidate.isEmpty()) {
                m_operations.insert(candidate);
            }
        }
        setReady(true);
    }
    emit responseReceived(requestId, response);
}

void WriterEngineClient::readStandardError()
{
    // Stderr is not part of the framed protocol. Drain it so a verbose Python
    // dependency cannot block the sidecar, but rely on process/protocol errors
    // for user-facing failures. Mirror it to a log file so engine startup
    // failures are diagnosable from packaged/development launches alike.
    const QByteArray stderrBytes = m_process.readAllStandardError();
    if (stderrBytes.isEmpty()) {
        return;
    }
    QFile engineLog(QCoreApplication::applicationDirPath() + QStringLiteral("/thothpad-engine.log"));
    if (engineLog.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
        engineLog.write(QDateTime::currentDateTime().toString(Qt::ISODate).toUtf8());
        engineLog.write(" ");
        engineLog.write(stderrBytes);
        engineLog.close();
    }
}

void WriterEngineClient::processStarted()
{
    QJsonObject payload;
    payload.insert(QStringLiteral("client"), QStringLiteral("thothpad"));
    payload.insert(QStringLiteral("client_version"), QStringLiteral(APPVERSION));
    payload.insert(QStringLiteral("performance"), PerformancePolicy::load().toJson());
    static int initializeSendCount = 0;
    ++initializeSendCount;
    QFile engineLog(QCoreApplication::applicationDirPath() + QStringLiteral("/thothpad-engine.log"));
    if (engineLog.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
        engineLog.write(QDateTime::currentDateTime().toString(Qt::ISODate).toUtf8());
        engineLog.write(QString(QStringLiteral(" [init] sent #%1 pid=%2\n")).arg(initializeSendCount).arg(m_process.processId()).toUtf8());
        engineLog.close();
    }
    send(QStringLiteral("initialize"), payload);
}

void WriterEngineClient::processFinished(int exitCode, QProcess::ExitStatus status)
{
    QFile engineLog(QCoreApplication::applicationDirPath() + QStringLiteral("/thothpad-engine.log"));
    if (engineLog.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
        engineLog.write(QDateTime::currentDateTime().toString(Qt::ISODate).toUtf8());
        engineLog.write(QString(QStringLiteral(" [finished] exit=%1 status=%2 program=%3\n")).arg(exitCode).arg((int)status).arg(m_process.program()).toUtf8());
        engineLog.close();
    }
    ++m_processGeneration;
    setReady(false);
    const QStringList invalidated = m_pendingOperations.keys();
    m_pendingOperations.clear();
    m_deadlines.clear();
    m_timedOutRequests.clear();
    m_buffer.clear();
    m_negotiatedProtocolMinor = 0;
    m_operations.clear();
    emit requestsInvalidated(invalidated);
    m_aborting = false;
    if (!m_stopping && m_restartCount == 0) {
        ++m_restartCount;
        QTimer::singleShot(500, this, &WriterEngineClient::start);
    }
}

void WriterEngineClient::abortEngine(const QString &reason)
{
    QFile engineLog(QCoreApplication::applicationDirPath() + QStringLiteral("/thothpad-engine.log"));
    if (engineLog.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
        engineLog.write(QDateTime::currentDateTime().toString(Qt::ISODate).toUtf8());
        engineLog.write(QString(QStringLiteral(" [abort] %1\n")).arg(reason).toUtf8());
        engineLog.close();
    }
    if (m_aborting || m_process.state() == QProcess::NotRunning) {
        return;
    }
    m_aborting = true;
    emit engineError(reason);
    const QStringList requestIds = m_pendingOperations.keys();
    for (const QString &requestId : requestIds) {
        const QString operation = m_pendingOperations.value(requestId);
        if (operation != QStringLiteral("cancel")
            && operation != QStringLiteral("shutdown")) {
            QJsonObject cancelRequest;
            cancelRequest.insert(QStringLiteral("protocol_major"), ProtocolMajor);
            cancelRequest.insert(QStringLiteral("protocol_minor"), ProtocolMinor);
            cancelRequest.insert(QStringLiteral("request_id"),
                QUuid::createUuid().toString(QUuid::WithoutBraces));
            cancelRequest.insert(QStringLiteral("operation"), QStringLiteral("cancel"));
            cancelRequest.insert(QStringLiteral("target_request_id"), requestId);
            cancelRequest.insert(QStringLiteral("persist"), false);
            writeMessage(cancelRequest);
        }
    }
    QJsonObject shutdownRequest;
    shutdownRequest.insert(QStringLiteral("protocol_major"), ProtocolMajor);
    shutdownRequest.insert(QStringLiteral("protocol_minor"), ProtocolMinor);
    shutdownRequest.insert(QStringLiteral("request_id"),
        QUuid::createUuid().toString(QUuid::WithoutBraces));
    shutdownRequest.insert(QStringLiteral("operation"), QStringLiteral("shutdown"));
    shutdownRequest.insert(QStringLiteral("persist"), false);
    writeMessage(shutdownRequest);
    m_process.closeWriteChannel();
    const quint64 abortGeneration = m_processGeneration;
    QTimer::singleShot(2000, this, [this, abortGeneration]() {
        if (m_processGeneration != abortGeneration) {
            return;
        }
        if (m_process.state() != QProcess::NotRunning) {
            forceTerminateProcessTree();
        }
    });
}

void WriterEngineClient::forceTerminateProcessTree()
{
    if (m_process.state() == QProcess::NotRunning) {
        return;
    }
    // The sidecar owns its report worker in a kill-on-close job/process group.
    // Terminating the supervisor therefore tears down the tree without a
    // synchronous taskkill subprocess on the GUI thread.
    m_process.kill();
}

void WriterEngineClient::processError(QProcess::ProcessError error)
{
    QFile engineLog(QCoreApplication::applicationDirPath() + QStringLiteral("/thothpad-engine.log"));
    if (engineLog.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
        engineLog.write(QDateTime::currentDateTime().toString(Qt::ISODate).toUtf8());
        engineLog.write(QString(QStringLiteral(" [error] code=%1 program=%2\n")).arg((int)error).arg(m_process.program()).toUtf8());
        engineLog.close();
    }
    setReady(false);
    emit engineError(tr("ThothPad Engine is unavailable. Editing and saving remain available."));
}

void WriterEngineClient::setReady(bool ready)
{
    if (m_ready == ready) {
        return;
    }
    m_ready = ready;
    emit readyChanged(ready);
}
}
