/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef WRITER_ENGINE_CLIENT_H
#define WRITER_ENGINE_CLIENT_H

#include <QHash>
#include <QJsonObject>
#include <QObject>
#include <QProcess>
#include <QSet>
#include <QThreadPool>
#include <QTimer>
#include <QUuid>

class WriterEngineClientTest;

namespace ghostwriter
{
class WriterEngineClient : public QObject
{
    Q_OBJECT

public:
    explicit WriterEngineClient(QObject *parent = nullptr);
    ~WriterEngineClient() override;

    bool isReady() const;
    bool supportsOperation(const QString &operation) const;
    int negotiatedProtocolMinor() const;
    QString enginePath() const;
    void setEnginePath(const QString &path);
    void start();
    void stop();

    QString send(const QString &operation, const QJsonObject &payload = {});
    void cancel(const QString &requestId);

signals:
    void readyChanged(bool ready);
    void responseReceived(const QString &requestId, const QJsonObject &response);
    void requestsInvalidated(const QStringList &requestIds);
    void engineError(const QString &message);

private slots:
    void readStandardOutput();
    void readStandardError();
    void processStarted();
    void processFinished(int exitCode, QProcess::ExitStatus status);
    void processError(QProcess::ProcessError error);

private:
    friend class ::WriterEngineClientTest;

    bool writeMessage(const QJsonObject &message);
    void parseMessages();
    void compactBuffer();
    void deliverParsedResponse(const QJsonObject &response, quint64 generation);
    void setReady(bool ready);
    void abortEngine(const QString &reason);
    void forceTerminateProcessTree();
    QString resolveEngineProgram(QStringList &arguments, QString &workingDirectory) const;

    QProcess m_process;
    QByteArray m_buffer;
    qsizetype m_bufferOffset = 0;
    QHash<QString, QString> m_pendingOperations;
    QHash<QString, qint64> m_deadlines;
    QTimer m_deadlineTimer;
    QString m_enginePath;
    bool m_ready = false;
    bool m_stopping = false;
    bool m_aborting = false;
    int m_restartCount = 0;
    quint64 m_processGeneration = 0;
    int m_negotiatedProtocolMinor = 0;
    QSet<QString> m_operations;
};
}

#endif
