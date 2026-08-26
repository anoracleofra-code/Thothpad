/*
 * SPDX-FileCopyrightText: 2022-2023 Megan Conkle <megan.conkle@kdemail.net>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef ASYNCTEXTWRITER_H
#define ASYNCTEXTWRITER_H

#include <QtGlobal>
#include <QObject>
#include <QScopedPointer>
#include <QString>

#include <QStringDecoder>

namespace ghostwriter
{
/**
 * Writes document text asynchronously to a file.
 */
class AsyncTextWriterPrivate;
class AsyncTextWriter : public QObject
{
    Q_OBJECT
    Q_DECLARE_PRIVATE(AsyncTextWriter)

public:
    // typedef encoding/codec to simplify transition to Qt 6 while still
    // maintaining backward compatibility with Qt 5.
    typedef QStringConverter::Encoding Encoding;

    /**
     * Constructor with file path to which text will be written.
     */
    AsyncTextWriter(const QString &fileName,
        QObject *parent = nullptr);

    /**
     * Destructor.
     */
    ~AsyncTextWriter();

    /**
     * Returns the file name.
     */
    QString fileName() const;

    /**
     * Sets the file name. If a write is still in flight, this waits for that
     * transaction to finish before changing the destination so completion
     * signals can never be associated with the wrong path.
     */
    void setFileName(const QString &fileName);

    /**
     * Returns the encoding.
     */
    Encoding encoding() const;

    /**
     * Sets the encoding.  The default encoding if none is
     * set with this method is UTF-8.
     */
    void setEncoding(Encoding encoding);

    /**
     * Returns true if a write is currently in progress, false otherwise.
     */
    bool writeInProgress() const;

    /**
     * Waits for the current write to finish (if needed) and returns true only
     * when the most recently completed write was durably committed.
     */
    bool waitForFinished();

    /**
     * Returns the error from the most recently completed write, or an empty
     * string when the write succeeded (or no write has completed yet).
     */
    QString lastError() const;

    /**
     * Writes the given text to the file.  Note: Previous contents of the file
     * will be replaced only after the atomic save transaction commits.
     */
    bool write(const QString &text);

signals:
    /**
     * Emitted when the write is complete.  Signal will not be emitted if
     * an error occurs.  (See writeError signal instead.)
     */
    void writeComplete();

    /**
     * Emitted when an error occurs while attempting to write to the file.
     * The error description will be set in the errorString parameter.
     */
    void writeError(const QString &errorString);

private:
    QScopedPointer<AsyncTextWriterPrivate> d_ptr;
};
} //namespace ghostwriter

#endif // ASYNCTEXTWRITER_H
