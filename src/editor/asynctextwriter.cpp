/*
 * SPDX-FileCopyrightText: 2022-2023 Megan Conkle <megan.conkle@kdemail.net>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QApplication>
#include <QFileInfo>
#include <QFuture>
#include <QFutureWatcher>
#include <QSaveFile>
#include <QtConcurrentRun>
#include <QTextStream>

#include "asynctextwriter.h"

#define DEFAULT_STREAM_CODEC QStringConverter::Utf8;

namespace ghostwriter
{
class AsyncTextWriterPrivate
{
    Q_DECLARE_PUBLIC(AsyncTextWriter)

public:
    AsyncTextWriterPrivate(AsyncTextWriter *q_ptr) :
        q_ptr(q_ptr) { }
    ~AsyncTextWriterPrivate() { }

    AsyncTextWriter *q_ptr;
    QString fileName;
    QString lastError;
    AsyncTextWriter::Encoding encoding;
    QFutureWatcher<QString> *writeFutureWatcher = nullptr;
    bool writeInProgress = false;

    void initialize(const QString &fileName);

    /*
    * Writes the given text to the given file path, returning a null
    * string if successful, otherwise an error message.  Note that this
    * method is intended to be run in a separate thread from the main
    * Qt event loop, and should thus never interact with any widgets.
    */
    static QString writeToDisk(const QString &text,
        const QString &fileName,
        AsyncTextWriter::Encoding encoding);

    /*
    * Handles any errors or tidying up after an asynchronous save operation.
    */
    void onWriteCompleted();
};

AsyncTextWriter::AsyncTextWriter(const QString &fileName,
    QObject *parent) :
        QObject(parent),
        d_ptr(new AsyncTextWriterPrivate(this))
{
    Q_D(AsyncTextWriter);

    d->initialize(fileName);
}

AsyncTextWriter::~AsyncTextWriter()
{
    ;
}

QString AsyncTextWriter::fileName() const
{
    Q_D(const AsyncTextWriter);

    return d->fileName;
}

void AsyncTextWriter::setFileName(const QString &fileName)
{
    Q_D(AsyncTextWriter);

    // A QFuture captures the destination by value, while completion handlers
    // historically consulted d->fileName.  Do not let callers retarget the
    // writer until the current transaction has fully completed and its signal
    // has been delivered.
    if (d->writeFutureWatcher->isRunning()
            || d->writeFutureWatcher->isStarted()) {
        waitForFinished();
    }

    d->fileName = QFileInfo(fileName).absoluteFilePath();
}

void AsyncTextWriter::setEncoding(AsyncTextWriter::Encoding encoding)
{
    Q_D(AsyncTextWriter);

    d->encoding = encoding;
}

AsyncTextWriter::Encoding AsyncTextWriter::encoding() const
{
    Q_D(const AsyncTextWriter);

    return d->encoding;
}

bool AsyncTextWriter::writeInProgress() const
{
    Q_D(const AsyncTextWriter);

    return d->writeInProgress;
}

bool AsyncTextWriter::waitForFinished()
{
    Q_D(AsyncTextWriter);

    if (d->writeFutureWatcher->isRunning() || d->writeFutureWatcher->isStarted()) {
        d->writeFutureWatcher->waitForFinished();
    }

    // QFutureWatcher emits finished through the event loop.  Process the
    // queued completion before reporting status so callers that gate a close,
    // rename, or retarget operation observe the final transaction result.
    qApp->processEvents();
    return d->lastError.isEmpty();
}

QString AsyncTextWriter::lastError() const
{
    Q_D(const AsyncTextWriter);

    return d->lastError;
}

bool AsyncTextWriter::write(const QString &text)
{
    Q_D(AsyncTextWriter);

    if (d->fileName.isNull() || d->fileName.isEmpty()) {
        d->lastError = tr("No file path specified");
        return false;
    }

    if (d->writeFutureWatcher->isRunning()
            || d->writeFutureWatcher->isStarted()) {
        waitForFinished();
    }

    d->lastError.clear();
    d->writeInProgress = true;

    QFuture<QString> future =
        QtConcurrent::run
        (
            &AsyncTextWriterPrivate::writeToDisk,
            text,
            d->fileName,
            d->encoding
        );

    d->writeFutureWatcher->setFuture(future);
    return true;
}

void AsyncTextWriterPrivate::initialize(const QString &fileName)
{
    Q_Q(AsyncTextWriter);

    this->fileName = QFileInfo(fileName).absoluteFilePath();
    this->encoding = DEFAULT_STREAM_CODEC;
    this->writeFutureWatcher = new QFutureWatcher<QString>(q);

    q->connect(this->writeFutureWatcher,
        &QFutureWatcher<QString>::finished,
        [this]() {
            this->onWriteCompleted();
        }
    );
}

QString AsyncTextWriterPrivate::writeToDisk(const QString &text,
    const QString &fileName,
    AsyncTextWriter::Encoding encoding)
{
    QSaveFile file(fileName);

    // Preserve QSaveFile's atomicity guarantee.  Direct-write fallback can
    // truncate the destination before a failed write is known to the caller,
    // which is unacceptable for an editor save path.
    file.setDirectWriteFallback(false);

    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate | QIODevice::Text)) {
        return file.errorString();
    }

    QTextStream stream(&file);
    stream.setEncoding(encoding);
    stream << text;
    stream.flush();

    if (stream.status() != QTextStream::Ok || QFile::NoError != file.error()) {
        const QString error = file.errorString().isEmpty()
            ? AsyncTextWriter::tr("Failed to write file contents")
            : file.errorString();
        file.cancelWriting();
        return error;
    }

    // QSaveFile::commit() is the durability boundary: only a successful
    // commit means the replacement reached its destination.  Never translate
    // a failed commit into writeComplete().
    if (!file.commit()) {
        return file.errorString().isEmpty()
            ? AsyncTextWriter::tr("Failed to commit saved file")
            : file.errorString();
    }

    return QString();
}

void AsyncTextWriterPrivate::onWriteCompleted()
{
    Q_Q(AsyncTextWriter);

    this->lastError = this->writeFutureWatcher->result();
    this->writeInProgress = false;

    if (!this->lastError.isNull() && !this->lastError.isEmpty()) {
        emit q->writeError(this->lastError);
        return;
    }

    emit q->writeComplete();
}

} //namespace ghostwriter
