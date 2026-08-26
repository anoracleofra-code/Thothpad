/*
 * SPDX-FileCopyrightText: 2014-2024 Megan Conkle <megan.conkle@kdemail.net>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QApplication>
#include <QCryptographicHash>
#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFileSystemWatcher>
#include <QMessageBox>
#include <QStandardPaths>
#include <QString>
#include <QTextDocument>
#include <QTextStream>
#include <QTimer>

#include "editor/asynctextwriter.h"
#include "editor/markdowndocument.h"
#include "editor/markdowneditor.h"
#include "export/exportdialog.h"
#include "export/exporter.h"
#include "export/exporterfactory.h"
#include "theme/themerepository.h"

#include "library.h"
#include "documentmanager.h"
#include "messageboxhelper.h"

namespace ghostwriter
{
class DocumentManagerPrivate
{
    Q_DECLARE_PUBLIC(DocumentManager)

public:
    static const QString FILE_CHOOSER_FILTER;

    DocumentManagerPrivate
    (
        DocumentManager *q_ptr
    ) : q_ptr(q_ptr)
    {
        ;
    }

    ~DocumentManagerPrivate()
    {

    }

    const QString draftName = DocumentManager::tr("untitled");

    QString draftLocation;
    QString backupLocation;

    DocumentManager *q_ptr;
    MarkdownDocument *document;
    MarkdownEditor *editor;
    QFileSystemWatcher *fileWatcher;
    bool fileHistoryEnabled;
    bool restoreSessionEnabled;
    bool createBackupOnSave;
    AsyncTextWriter *writer;

    /*
    * This flag is used to prevent notifying the user that the document
    * was modified when the user is the one who modified it by saving.
    * See code for onFileChangedExternally() for details.
    */
    bool saveInProgress;

    /*
     * Save transaction state. The document stays dirty until the exact
     * revision captured here is durably committed. Path-changing operations
     * can be rolled back after an asynchronous failure, while draft/rename
     * source files are only removed after success.
     */
    int pendingSaveRevision;
    bool rollbackPathOnFailure;
    QString rollbackPath;
    QString cleanupPathAfterSave;
    bool cleanupBackupAfterSave;

    /*
    * This timer's timeout signal is connected to the autoSaveFile() slot,
    * which saves the document if it can be saved and has been modified.
    */
    QTimer *autoSaveTimer;
    bool autoSaveEnabled;

    /*
    * Boolean flag used to track if the prompt for the file having been
    * externally modified is already displayed and should not be displayed
    * again.
    */
    bool documentModifiedNotifVisible;

    /* Begins an asynchronous save transaction. */
    bool saveFile();

    /* Handles the event where a file has been modified externally on disk. */
    void onFileChangedExternally(const QString &path);

    /* Loads the document with the file contents at the given path. */
    bool loadFile(const Bookmark &location);

    /* Updates the document/writer destination and file watcher. */
    void setFilePath(const QString &filePath);

    /* Checks whether changes must be durably saved before an operation. */
    bool checkSaveChanges();

    /* Confirms whether a protected file should be overwritten. */
    bool checkPermissionsBeforeSave();

    /* Returns a collision-resistant backup path for a source document. */
    QString backupFilePath(const QString &filePath) const;

    /* Creates a backup of the specified source document. */
    void backupFile(const QString &filePath) const;

    /* Handles autosave operation upon autosave timer expiration. */
    void autoSaveFile();

    /* Returns true when the current file is an autosaved draft. */
    bool documentIsDraft();

    /* Creates an autosaved draft for a previously untitled document. */
    void createDraft();
};

const QString DocumentManagerPrivate::FILE_CHOOSER_FILTER =
    QString("%1 (*.md *.markdown *.mdown *.mkdn *.mkd *.mdwn *.mdtxt *.mdtext *.text *.Rmd *.txt);;%2 (*.txt);;%3 (*)")
    .arg(DocumentManager::tr("Markdown"))
    .arg(DocumentManager::tr("Text"))
    .arg(DocumentManager::tr("All"));

DocumentManager::DocumentManager
(
    MarkdownEditor *editor,
    QObject *parent
) : QObject(parent),
    d_ptr(new DocumentManagerPrivate(this))
{
    Q_D(DocumentManager);

    d->editor = editor;
    d->fileHistoryEnabled = true;
    d->createBackupOnSave = true;
    d->saveInProgress = false;
    d->pendingSaveRevision = -1;
    d->rollbackPathOnFailure = false;
    d->cleanupBackupAfterSave = false;
    d->autoSaveEnabled = false;
    d->documentModifiedNotifVisible = false;

    d->draftLocation =
        QStandardPaths::writableLocation(QStandardPaths::DocumentsLocation);

    d->fileWatcher = new QFileSystemWatcher(this);
    d->document = (MarkdownDocument *) editor->document();

    d->writer = new AsyncTextWriter(d->document->filePath());

    // Markdown files need to be in UTF-8, since most Markdown processors
    // (i.e., Pandoc, et. al.) can only read UTF-8 encoded text files.
    d->writer->setEncoding(QStringConverter::Utf8);

    connect(d->writer, &AsyncTextWriter::writeComplete, [d]() {
        const QString savedPath = d->writer->fileName();
        d->saveInProgress = false;

        // Only clear the dirty bit when the document is still exactly the
        // revision that was written. Edits made while the worker was saving
        // remain dirty and therefore cannot be lost on a subsequent close.
        if (d->pendingSaveRevision >= 0
                && d->document->revision() == d->pendingSaveRevision) {
            d->document->setModified(false);
            emit d->q_ptr->documentModifiedChanged(false);
        }

        const QFileInfo savedInfo(savedPath);
        if (savedInfo.exists()) {
            d->document->setTimestamp(savedInfo.lastModified());
            const bool readOnly = !savedInfo.isWritable();
            d->document->setReadOnly(readOnly);
            d->editor->setReadOnly(readOnly);

            if (!d->fileWatcher->files().contains(savedPath)) {
                d->fileWatcher->addPath(savedPath);
            }
        }

        // A Save As from an autosaved draft, or a rename implemented as a
        // safe copy+commit, never removes the source until the destination has
        // committed successfully.
        if (!d->cleanupPathAfterSave.isEmpty()
                && QFileInfo(d->cleanupPathAfterSave).absoluteFilePath()
                    != QFileInfo(savedPath).absoluteFilePath()) {
            QFile source(d->cleanupPathAfterSave);
            if (source.exists() && !source.remove()) {
                qWarning() << "Saved destination, but could not remove old source:"
                           << d->cleanupPathAfterSave << source.errorString();
            }
            if (d->cleanupBackupAfterSave) {
                QFile::remove(d->backupFilePath(d->cleanupPathAfterSave));
                // Clean up backups created by older ThothPad builds as well.
                QFile::remove(d->cleanupPathAfterSave + QStringLiteral(".backup"));
            }
        }

        d->cleanupPathAfterSave.clear();
        d->cleanupBackupAfterSave = false;
        d->rollbackPathOnFailure = false;
        d->rollbackPath.clear();
        d->pendingSaveRevision = -1;

        // Session history should describe only destinations that really exist
        // on disk. A failed asynchronous save must never create a phantom
        // recent-file entry.
        if (d->restoreSessionEnabled) {
            Bookmark location(savedPath, d->editor->textCursor().position());
            if (location.isValid()) {
                Library().updateLastOpened(location);
                emit d->q_ptr->sessionHistoryChanged();
            }
        }
    });

    connect(d->writer, &AsyncTextWriter::writeError, [d](const QString &err) {
        const QString failedPath = d->writer->fileName();
        d->saveInProgress = false;
        d->pendingSaveRevision = -1;
        d->cleanupPathAfterSave.clear();
        d->cleanupBackupAfterSave = false;

        // A Save As/rename/draft creation that failed asynchronously must not
        // strand the document on a destination that was never successfully
        // created. Restore the previous identity before surfacing the error.
        if (d->rollbackPathOnFailure) {
            const QString path = d->rollbackPath;
            d->rollbackPathOnFailure = false;
            d->rollbackPath.clear();
            d->setFilePath(path);
        }

        // Failure is never a clean state. This is deliberately explicit even
        // if QTextDocument already considers itself modified, because autosave
        // mode suppresses the normal modificationChanged presentation signal.
        d->document->setModified(true);
        emit d->q_ptr->documentModifiedChanged(true);

        if (!err.isNull() && !err.isEmpty()) {
            MessageBoxHelper::critical(
                d->editor,
                DocumentManager::tr("Error saving %1").arg(failedPath),
                err
            );
        }
    });

    // Set up auto-save timer to save the file once every minute.
    d->autoSaveTimer = new QTimer(this);
    d->autoSaveTimer->start(60000);

    connect(d->autoSaveTimer, &QTimer::timeout, [d]() {
        d->autoSaveFile();
    });

    connect(d->document,
        &MarkdownDocument::modificationChanged,
        [this, d](bool modified) {
            if (d->document->isReadOnly()
                    || !d->autoSaveEnabled) {
                emit documentModifiedChanged(modified);
            }

            if (modified
                    && d->autoSaveEnabled
                    && d->document->isNew()
                    && (!d->document->isEmpty())) {
                d->createDraft();
            }
        }
    );

    connect(d->fileWatcher, &QFileSystemWatcher::fileChanged, [d](const QString &path) {
        d->onFileChangedExternally(path);
    });
}

DocumentManager::~DocumentManager()
{
    ;
}

MarkdownDocument *DocumentManager::document() const
{
    Q_D(const DocumentManager);

    return d->document;
}

bool DocumentManager::autoSaveEnabled() const
{
    Q_D(const DocumentManager);

    return d->autoSaveEnabled;
}

void DocumentManager::setAutoSaveEnabled(bool enabled)
{
    Q_D(DocumentManager);

    if (d->autoSaveEnabled == enabled) {
        return;
    }

    d->autoSaveEnabled = enabled;

    if (enabled) {
        if (d->document->isModified()) {
            if (d->document->isNew() && !d->document->isEmpty()) {
                d->createDraft();
            } else if (!d->document->isNew() && !d->document->isReadOnly()) {
                // Enabling autosave on an already-dirty named document should
                // make good on that promise immediately rather than hiding the
                // dirty indicator until the next one-minute timer tick.
                d->autoSaveFile();
            } else {
                emit documentModifiedChanged(true);
            }
        } else {
            emit documentModifiedChanged(false);
        }
    } else {
        // Disabling autosave must never clear unsaved work. The historical
        // implementation called setModified(false) here, which could make a
        // failed/unsaved document appear safe to close.
        emit documentModifiedChanged(d->document->isModified());
    }
}

bool DocumentManager::fileBackupEnabled() const
{
    Q_D(const DocumentManager);

    return d->createBackupOnSave;
}

void DocumentManager::setFileBackupEnabled(bool enabled)
{
    Q_D(DocumentManager);

    d->createBackupOnSave = enabled;
}

void DocumentManager::setDraftLocation(const QString &directory)
{
    Q_D(DocumentManager);

    QDir draftDir(directory);

    if (!draftDir.exists()) {
        if (!draftDir.mkpath(draftDir.path())) {
            qWarning() << "Could not create draft directory:" << draftDir.path();
        }
    }

    d->draftLocation = draftDir.absolutePath();
}

void DocumentManager::setBackupLocation(const QString &directory)
{
    Q_D(DocumentManager);

    QDir backupDir(directory);

    if (!backupDir.exists()) {
        if (!backupDir.mkpath(backupDir.path())) {
            qWarning() << "Could not create backup directory:" << backupDir.path();
        }
    }

    d->backupLocation = backupDir.absolutePath();
}

void DocumentManager::setFileHistoryEnabled(bool enabled)
{
    Q_D(DocumentManager);

    d->fileHistoryEnabled = enabled;
}

void DocumentManager::setRestoreSessionEnabled(bool enabled)
{
    Q_D(DocumentManager);

    d->restoreSessionEnabled = enabled;
}

void DocumentManager::open()
{
    Q_D(DocumentManager);

    if (d->checkSaveChanges()) {
        QString startingDirectory = QString();

        if (!d->document->isNew()) {
            startingDirectory = QFileInfo(d->document->filePath()).dir().path();
        }

        QString path = QFileDialog::getOpenFileName(d->editor, tr("Open File"), startingDirectory, DocumentManagerPrivate::FILE_CHOOSER_FILTER);

        if (!path.isEmpty()) {
            Library library;
            Bookmark location = library.lookup(path);

            if (!location.isValid()) {
                location = Bookmark(path);
            }

            openFileAt(location);
        }
    }
}

void DocumentManager::openFileAt(const Bookmark &location, bool omitFromHistory)
{
    Q_D(DocumentManager);

    if (d->checkSaveChanges()) {
        if (location.isValid()) {
            if (!location.isReadable()) {
                MessageBoxHelper::critical(d->editor, tr("Could not open %1").arg(location.filePath()), tr("Permission denied."));
                return;
            }

            QString oldFilePath = d->document->filePath();
            int oldCursorPosition = d->editor->textCursor().position();

            if (!close()) {
                return;
            }

            if (!d->loadFile(location)) {
                return;
            } else if (oldFilePath == d->document->filePath()) {
                d->editor->navigateDocument(oldCursorPosition);
            }

            if (d->restoreSessionEnabled) {
                Library().setLastOpened(location, d->fileHistoryEnabled && !omitFromHistory);
                emit sessionHistoryChanged();
            }
        }
    }
}

void DocumentManager::createUntitled()
{
    Q_D(DocumentManager);

    if (!close()) {
        return;
    }

    if (d->restoreSessionEnabled) {
        Library().setLastOpened(Library::UNTITLED);

        if (d->fileHistoryEnabled) {
            emit sessionHistoryChanged();
        }
    }
}

void DocumentManager::reload()
{
    Q_D(DocumentManager);

    if (d->writer->writeInProgress() && !d->writer->waitForFinished()) {
        return;
    }

    if (!d->document->isNew()) {
        if (d->document->isModified()) {
            int response =
                MessageBoxHelper::question
                (
                    d->editor,
                    tr("The document has been modified."),
                    tr("Discard changes?"),
                    QMessageBox::Yes | QMessageBox::No,
                    QMessageBox::No
                );

            if (QMessageBox::No == response) {
                return;
            }
        }

        QString filePath = d->document->filePath();
        int pos = d->editor->textCursor().position();

        if (d->loadFile(filePath)) {
            QTextCursor cursor = d->editor->textCursor();
            cursor.setPosition(qMin(pos, d->document->characterCount() - 1));
            d->editor->setTextCursor(cursor);
        }
    }
}

void DocumentManager::rename()
{
    Q_D(DocumentManager);

    if (d->document->isNew()) {
        saveAs();
        return;
    }

    if (d->writer->writeInProgress()) {
        d->writer->waitForFinished();
    }

    const QString oldFilePath = d->document->filePath();
    const QString filePath = QFileDialog::getSaveFileName(
        d->editor,
        tr("Rename File"),
        QFileInfo(oldFilePath).absoluteDir().absolutePath(),
        DocumentManagerPrivate::FILE_CHOOSER_FILTER
    );

    if (filePath.isNull() || filePath.isEmpty()) {
        return;
    }

    if (QFileInfo(filePath).absoluteFilePath() == QFileInfo(oldFilePath).absoluteFilePath()) {
        return;
    }

    // Implement rename as destination commit followed by source removal.
    // This preserves both files if the write fails and also avoids deleting an
    // existing destination before we know the replacement is safe.
    d->rollbackPathOnFailure = true;
    d->rollbackPath = oldFilePath;
    d->cleanupPathAfterSave = oldFilePath;
    d->cleanupBackupAfterSave = false;
    d->setFilePath(filePath);

    if (!d->saveFile()) {
        d->setFilePath(oldFilePath);
        d->rollbackPathOnFailure = false;
        d->rollbackPath.clear();
        d->cleanupPathAfterSave.clear();
    }
}

bool DocumentManager::saveFile()
{
    Q_D(DocumentManager);

    if (d->documentIsDraft()) {
        return saveAs();
    }
    return save();
}

bool DocumentManager::save()
{
    Q_D(DocumentManager);

    if (d->document->isNew() || !d->checkPermissionsBeforeSave()) {
        return saveAs();
    }

    d->rollbackPathOnFailure = false;
    d->rollbackPath.clear();
    d->cleanupPathAfterSave.clear();
    d->cleanupBackupAfterSave = false;
    return d->saveFile();
}

bool DocumentManager::saveAs()
{
    Q_D(DocumentManager);

    if (d->writer->writeInProgress()) {
        d->writer->waitForFinished();
    }

    QString startingDirectory = QString();
    if (!d->document->isNew()) {
        startingDirectory = QFileInfo(d->document->filePath()).dir().path();
    }

    const QString filePath = QFileDialog::getSaveFileName(
        d->editor,
        tr("Save File"),
        startingDirectory,
        DocumentManagerPrivate::FILE_CHOOSER_FILTER
    );

    if (filePath.isNull() || filePath.isEmpty()) {
        return false;
    }

    const QString previousPath = d->document->filePath();
    const bool wasDraft = d->documentIsDraft();

    d->rollbackPathOnFailure = true;
    d->rollbackPath = previousPath;
    d->cleanupPathAfterSave = wasDraft ? previousPath : QString();
    d->cleanupBackupAfterSave = wasDraft;
    d->setFilePath(filePath);

    if (!d->saveFile()) {
        d->setFilePath(previousPath);
        d->rollbackPathOnFailure = false;
        d->rollbackPath.clear();
        d->cleanupPathAfterSave.clear();
        d->cleanupBackupAfterSave = false;
        return false;
    }

    return true;
}

bool DocumentManager::close()
{
    Q_D(DocumentManager);

    if (!d->checkSaveChanges()) {
        return false;
    }

    if (d->writer->writeInProgress() && !d->writer->waitForFinished()) {
        return false;
    }

    if (d->restoreSessionEnabled && !d->document->isNew()) {
        Bookmark location(d->document->filePath(), d->editor->textCursor().position());
        Library().updateLastOpened(location);
        emit sessionHistoryChanged();
    }

    QTextCursor cursor(d->document);
    cursor.setPosition(0);
    d->editor->setTextCursor(cursor);

    d->document->clear();
    d->document->clearUndoRedoStacks();

    d->editor->setReadOnly(false);
    d->document->setReadOnly(false);
    d->setFilePath(QString());
    d->document->setModified(false);

    emit documentClosed();
    return true;
}

void DocumentManager::exportFile()
{
    Q_D(DocumentManager);

    ExportDialog exportDialog(d->document);

    connect(&exportDialog, SIGNAL(exportStarted(QString)), this, SIGNAL(operationStarted(QString)));
    connect(&exportDialog, SIGNAL(exportComplete()), this, SIGNAL(operationFinished()));

    exportDialog.exec();
}

void DocumentManagerPrivate::onFileChangedExternally(const QString &path)
{
    Q_Q(DocumentManager);

    QFileInfo fileInfo(path);

    if (!fileInfo.exists()) {
        emit q->documentModifiedChanged(true);
        document->setModified(true);
    } else {
        if (fileInfo.isWritable() && document->isReadOnly()) {
            document->setReadOnly(false);
            editor->setReadOnly(false);

            if (autoSaveEnabled) {
                emit q->documentModifiedChanged(false);
            }
        } else if (!fileInfo.isWritable() && !document->isReadOnly()) {
            document->setReadOnly(true);
            editor->setReadOnly(true);

            if (document->isModified()) {
                emit q->documentModifiedChanged(true);
            }
        }

        if
        (
            !saveInProgress &&
            (fileInfo.lastModified() > document->timestamp()) &&
            !documentModifiedNotifVisible
        ) {
            documentModifiedNotifVisible = true;

            int response =
                MessageBoxHelper::question
                (
                    editor,
                    DocumentManager::tr("The document has been modified by another program."),
                    DocumentManager::tr("Would you like to reload the document?"),
                    QMessageBox::Yes | QMessageBox::No,
                    QMessageBox::Yes
                );

            documentModifiedNotifVisible = false;

            if (QMessageBox::Yes == response) {
                q->reload();
            }
        }
    }
}

bool DocumentManagerPrivate::saveFile()
{
    // Serialize writer transactions before reusing the single watcher. This
    // also guarantees that path/revision bookkeeping from the prior save has
    // been finalized before a new transaction is captured.
    if (writer->writeInProgress()) {
        writer->waitForFinished();
    }

    const QString text = document->toPlainText();
    pendingSaveRevision = document->revision();
    saveInProgress = true;

    if (createBackupOnSave) {
        backupFile(writer->fileName());
    }

    if (!writer->write(text)) {
        const QString error = writer->lastError().isEmpty()
            ? DocumentManager::tr("No file path specified")
            : writer->lastError();
        saveInProgress = false;
        pendingSaveRevision = -1;
        cleanupPathAfterSave.clear();
        cleanupBackupAfterSave = false;
        document->setModified(true);
        emit q_ptr->documentModifiedChanged(true);
        MessageBoxHelper::critical(
            editor,
            DocumentManager::tr("Error saving %1").arg(writer->fileName()),
            error
        );
        return false;
    }

    return true;
}

bool DocumentManagerPrivate::loadFile(const Bookmark &location)
{
    Q_Q(DocumentManager);

    QFile inputFile(location.filePath());

    if (!inputFile.open(QIODevice::ReadOnly)) {
        MessageBoxHelper::critical(editor, DocumentManager::tr("Could not read %1").arg(location.filePath()), inputFile.errorString());
        return false;
    }

    QTextCursor cursor(document);
    cursor.setPosition(0);
    editor->setTextCursor(cursor);

    document->clearUndoRedoStacks();
    document->setUndoRedoEnabled(false);
    document->clear();

    QApplication::setOverrideCursor(Qt::WaitCursor);
    emit q->operationStarted(DocumentManager::tr("opening %1").arg(location.filePath()));
    QTextStream inStream(&inputFile);

    inStream.setEncoding(QStringConverter::Utf8);
    inStream.setAutoDetectUnicode(true);

    QString text = inStream.readAll();

    if (QFile::NoError != inputFile.error()) {
        MessageBoxHelper::critical(editor, DocumentManager::tr("Could not read %1").arg(location.filePath()), inputFile.errorString());
        inputFile.close();
        document->setUndoRedoEnabled(true);
        QApplication::restoreOverrideCursor();
        emit q->operationFinished();
        return false;
    }

    inputFile.close();

    setFilePath(location.filePath());
    editor->setPlainText(text);
    editor->navigateDocument(0);
    emit q->operationUpdate();

    document->setUndoRedoEnabled(true);

    editor->navigateDocument(location.cursorPosition());
    editor->setReadOnly(!location.isWriteable());
    document->setReadOnly(!location.isWriteable());

    document->setModified(false);
    document->setTimestamp(QFileInfo(location.filePath()).lastModified());

    for (QString watchedFile : fileWatcher->files()) {
        if (watchedFile != location.filePath()) {
            fileWatcher->removePath(watchedFile);
        }
    }

    if (!fileWatcher->files().contains(location.filePath())) {
        fileWatcher->addPath(location.filePath());
    }
    emit q->operationFinished();
    emit q->documentModifiedChanged(false);
    QApplication::restoreOverrideCursor();

    editor->centerCursor();
    emit q->documentLoaded();

    return true;
}

void DocumentManagerPrivate::setFilePath(const QString &filePath)
{
    Q_Q(DocumentManager);

    if (!document->isNew()) {
        fileWatcher->removePath(document->filePath());
    }

    document->setFilePath(filePath);
    writer->setFileName(filePath);

    if (!filePath.isNull() && !filePath.isEmpty()) {
        QFileInfo fileInfo(filePath);

        if (fileInfo.exists()) {
            const bool readOnly = !fileInfo.isWritable();
            document->setReadOnly(readOnly);
            editor->setReadOnly(readOnly);
            if (!fileWatcher->files().contains(filePath)) {
                fileWatcher->addPath(filePath);
            }
        } else {
            document->setReadOnly(false);
            editor->setReadOnly(false);
        }
    } else {
        document->setReadOnly(false);
        editor->setReadOnly(false);
    }

    emit q->documentDisplayNameChanged(document->displayName());
}

bool DocumentManagerPrivate::checkSaveChanges()
{
    Q_Q(DocumentManager);

    // A destructive operation must observe the result of any save already in
    // flight. If it failed, abort this operation; the error handler restores
    // the dirty state and any path rollback before control returns here.
    if (writer->writeInProgress() && !writer->waitForFinished()) {
        return false;
    }

    if (!document->isModified()) {
        return true;
    }

    auto saveAndConfirm = [this, q]() -> bool {
        const bool started = document->isNew() ? q->saveAs() : q->save();
        if (!started) {
            return false;
        }
        if (writer->writeInProgress() && !writer->waitForFinished()) {
            return false;
        }
        return writer->lastError().isEmpty() && !document->isModified();
    };

    if (autoSaveEnabled && !document->isNew() && !document->isReadOnly()) {
        return saveAndConfirm();
    }

    QString text;
    if (document->isNew()) {
        text = DocumentManager::tr("File has been modified.");
    } else {
        text = DocumentManager::tr("%1 has been modified.")
            .arg(document->displayName());
    }

    int response =
        MessageBoxHelper::question
        (
            editor,
            text,
            DocumentManager::tr("Would you like to save your changes?"),
            QMessageBox::Save | QMessageBox::Discard | QMessageBox::Cancel,
            QMessageBox::Save
        );

    switch (response) {
    case QMessageBox::Save:
        return saveAndConfirm();
    case QMessageBox::Cancel:
        return false;
    default:
        return true;
    }
}

bool DocumentManagerPrivate::checkPermissionsBeforeSave()
{
    if (!document->isReadOnly()) {
        return true;
    }

    int response =
        MessageBoxHelper::question
        (
            editor,
            DocumentManager::tr("%1 is read only.").arg(document->filePath()),
            DocumentManager::tr("Overwrite protected file?"),
            QMessageBox::Yes | QMessageBox::No,
            QMessageBox::Yes
        );

    // Never delete the protected original as a precondition for saving. The
    // atomic writer gets one chance to replace it safely; if the filesystem
    // refuses, the original remains intact and writeError keeps the editor
    // dirty. Choosing No falls back to Save As via DocumentManager::save().
    return QMessageBox::Yes == response;
}

QString DocumentManagerPrivate::backupFilePath(const QString &filePath) const
{
    QFileInfo fileInfo(filePath);
    QString identity = fileInfo.canonicalFilePath();
    if (identity.isEmpty()) {
        identity = QDir::cleanPath(fileInfo.absoluteFilePath());
    }
#ifdef Q_OS_WIN32
    identity = identity.toCaseFolded();
#endif
    const QByteArray digest = QCryptographicHash::hash(
        identity.toUtf8(), QCryptographicHash::Sha256
    ).toHex().left(12);

    return backupLocation
        + QDir::separator()
        + fileInfo.fileName()
        + QStringLiteral("--")
        + QString::fromLatin1(digest)
        + QStringLiteral(".backup");
}

void DocumentManagerPrivate::backupFile(const QString &filePath) const
{
    QFile source(filePath);
    if (!source.exists()) {
        return;
    }

    QDir backupDir(backupLocation);
    if (!backupDir.exists() && !backupDir.mkpath(backupLocation)) {
        MessageBoxHelper::critical(
            editor,
            DocumentManager::tr("File backup failed"),
            DocumentManager::tr("Error creating backup location: %1").arg(backupLocation)
        );
        return;
    }

    const QString targetPath = backupFilePath(filePath);
    QFile target(targetPath);
    if (target.exists() && !target.remove()) {
        MessageBoxHelper::critical(
            editor,
            DocumentManager::tr("File backup failed: Could not replace %1").arg(targetPath),
            target.errorString()
        );
        return;
    }

    if (!source.copy(targetPath)) {
        MessageBoxHelper::critical(
            editor,
            DocumentManager::tr("File backup failed: Could not copy %1 to %2").arg(filePath).arg(targetPath),
            source.errorString()
        );
    }
}

void DocumentManagerPrivate::autoSaveFile()
{
    Q_Q(DocumentManager);

    if (autoSaveEnabled && !document->isNew() && !document->isReadOnly() && document->isModified()) {
        q->save();
    }
}

bool DocumentManagerPrivate::documentIsDraft()
{
    if (document->isNew()) {
        return false;
    }

    QFileInfo info(document->filePath());

    return ((info.dir().absolutePath() == draftLocation)
        && (info.baseName().startsWith(draftName)));
}

void DocumentManagerPrivate::createDraft()
{
    if (!document->isNew()) {
        return;
    }

    int i = 1;
    QString draftPath;
    do {
        draftPath = draftLocation + QDir::separator()
            + draftName + "-" + QString::number(i) + ".md";
        i++;
    } while (QFileInfo(draftPath).exists());

    rollbackPathOnFailure = true;
    rollbackPath = QString();
    cleanupPathAfterSave.clear();
    cleanupBackupAfterSave = false;
    setFilePath(draftPath);
    saveFile();
}

}
