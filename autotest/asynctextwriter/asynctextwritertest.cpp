/*
 * SPDX-FileCopyrightText: 2022-2023 Megan Conkle <megan.conkle@kdemail.net>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QApplication>
#include <QTest>
#include <QThread>
#include <QDebug>
#include <QFile>
#include <QDir>
#include <QFileInfo>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QTextStream>
#include <QString>

#include "../../src/editor/asynctextwriter.h"

using namespace ghostwriter;

/**
 * Unit test for the AsyncTextWriter class.
 */
class AsyncTextWriterTest: public QObject
{
    Q_OBJECT

private:
    AsyncTextWriter::Encoding DefaultEncoding;
    AsyncTextWriter::Encoding Utf8Encoding;
    AsyncTextWriter::Encoding Utf16Encoding;

    /**
     * Write helper method (nominal cases).
     * Can use to specify different file name and encoding combinations.
     * Set successExpected parameter to verify whether the test is expected
     * to complete the write successfully (true) or else have an
     * error (false).
     */
    void runWriteTest(const QString &fileName,
        AsyncTextWriter::Encoding encoding,
        bool successExpected);

private slots:
    void initTestCase();
    void constructor1();
    void setFileName();
    void setEncoding();
    void emptyFileName();
    void nullFileName();
    void write();
    void writeToReadOnlyFile();
    void writeToReadOnlyDirectory();
    void writeAlreadyInProgress();
    void waitForFinishedReportsFailure();
};

void AsyncTextWriterTest::runWriteTest(const QString &fileName,
        AsyncTextWriter::Encoding encoding,
        bool successExpected)
{
    bool writeCompleted = false;
    bool noErrors = true;
    QString expectedContents = "abcdefg\nxyz\n";

    AsyncTextWriter::Encoding expectedEncoding = encoding;
    AsyncTextWriter writer(fileName);

    writer.setEncoding(encoding);

    this->connect(
        &writer,
        &AsyncTextWriter::writeComplete,
        [this, &writer, &writeCompleted]() {
            QCOMPARE(writer.writeInProgress(), false);
            writeCompleted = true;
        }
    );

    this->connect(
        &writer,
        &AsyncTextWriter::writeError,
        [this, &writer, &noErrors](const QString &err) {
            noErrors = false;
            qWarning() << QString("Error writing to file: ") + err;
        }
    );

    QCOMPARE(writer.writeInProgress(), false);

    bool status = writer.write(expectedContents);
    QCOMPARE(status, true);

    QThread::sleep(1);

    QVERIFY(writer.writeInProgress());

    qApp->processEvents();

    QCOMPARE(noErrors, successExpected);
    QCOMPARE(writeCompleted, successExpected);
    QCOMPARE(writer.writeInProgress(), false);
    QCOMPARE(writer.lastError().isEmpty(), successExpected);

    if (successExpected) {
        QFile file(writer.fileName());
        bool fileReadable = file.open(QIODevice::ReadOnly | QIODevice::Text);

        QVERIFY(fileReadable);

        if (fileReadable) {
            QTextStream stream(&file);

            QString actualContents;
            actualContents = stream.readAll();

            QCOMPARE(stream.encoding(), expectedEncoding);

            file.close();

            QCOMPARE(actualContents, expectedContents);

            file.remove();
        }
    }
}

void AsyncTextWriterTest::initTestCase()
{
    Utf8Encoding = QStringConverter::Utf8;
    Utf16Encoding = QStringConverter::Utf16;
    DefaultEncoding = Utf8Encoding;
}

void AsyncTextWriterTest::constructor1()
{
    QString fileName = "constructor.txt";
    QString expectedFileName = QFileInfo(fileName).absoluteFilePath();

    AsyncTextWriter writer(fileName, this);

    QCOMPARE(writer.fileName(), expectedFileName);
    QCOMPARE(writer.encoding(), DefaultEncoding);
    QCOMPARE(writer.parent(), this);
    QVERIFY(writer.lastError().isEmpty());
}

void AsyncTextWriterTest::setFileName()
{
    QString oldfileName = "oldname.txt";
    QString expectedFileName = QFileInfo(oldfileName).absoluteFilePath();

    AsyncTextWriter writer(oldfileName);
    QCOMPARE(writer.fileName(), expectedFileName);

    QString newFileName = "newname.txt";
    expectedFileName = QFileInfo(newFileName).absoluteFilePath();
    writer.setFileName(newFileName);
    QCOMPARE(writer.fileName(), expectedFileName);
}

void AsyncTextWriterTest::setEncoding()
{
    QString fileName = "encodingtest.txt";
    AsyncTextWriter writer("encodingtest.txt");
    writer.setEncoding(Utf16Encoding);
    QCOMPARE(writer.encoding(), Utf16Encoding);
}

void AsyncTextWriterTest::write()
{
    runWriteTest("write.txt", Utf8Encoding, true);
}

void AsyncTextWriterTest::emptyFileName()
{
    QString fileName = QString("");

    AsyncTextWriter writer(fileName);

    QCOMPARE(writer.write("empty"), false);
    QVERIFY(!writer.lastError().isEmpty());
}

void AsyncTextWriterTest::nullFileName()
{
    QString fileName = QString();

    AsyncTextWriter writer(fileName);

    QCOMPARE(writer.write("null"), false);
    QVERIFY(!writer.lastError().isEmpty());
}

void AsyncTextWriterTest::writeToReadOnlyFile()
{
    QString fileName = "readonly.txt";
    QFile file(fileName);

    if (file.exists()) {
        file.remove();
    }

    bool readOnlyFileCreated =
        file.open(QIODevice::WriteOnly | QIODevice::Truncate
            | QIODevice::Text | QIODeviceBase::NewOnly);

    if (!readOnlyFileCreated) {
        qCritical() << file.error();
    }
    QVERIFY(readOnlyFileCreated);

    QTextStream stream(&file);

    stream << "This is a read-only file.";

    file.close();

    file.setPermissions(QFileDevice::ReadOwner |
        QFileDevice::ReadUser |
        QFileDevice::ReadGroup |
        QFileDevice::ReadOwner |
        QFileDevice::ReadOther);

    runWriteTest(fileName, Utf8Encoding, false);

    file.setPermissions(QFileDevice::WriteOwner |
        QFileDevice::WriteUser);

    file.remove();
}

void AsyncTextWriterTest::writeToReadOnlyDirectory()
{
#if defined(Q_OS_WIN)
    QSKIP("Cannot programmatically create read-only directories on Windows.");
#else
    bool readOnlyDirectoryCreated;
    QDir dir("");

    readOnlyDirectoryCreated = dir.mkdir("readonly");

    QVERIFY(readOnlyDirectoryCreated);

    dir.cd("readonly");

    QFile dirFile(dir.path());

    dirFile.setPermissions(QFileDevice::ReadOwner |
        QFileDevice::ReadUser |
        QFileDevice::ReadGroup |
        QFileDevice::ReadOwner |
        QFileDevice::ReadOther);

    QString fileName = dir.path() + "/newfile.txt";

    runWriteTest(fileName, Utf8Encoding, false);

    dirFile.setPermissions(QFileDevice::WriteOwner |
        QFileDevice::WriteUser);

    dir.cdUp();
    dir.rmdir("readonly");
#endif
}

void AsyncTextWriterTest::writeAlreadyInProgress()
{
    bool writeCompleted = false;
    bool noErrors = true;
    QString fileName = "inprogress.txt";
    QString expectedContents = "12345\n6789\n0";
    bool firstCallStatus;
    bool secondCallStatus;

    AsyncTextWriter writer(fileName);

    this->connect(
        &writer,
        &AsyncTextWriter::writeComplete,
        [this, &writer, &writeCompleted]() {
            writeCompleted = true;
        }
    );

    this->connect(
        &writer,
        &AsyncTextWriter::writeError,
        [this, &writer, &noErrors](const QString &err) {
            noErrors = false;
            qWarning() << QString("Error writing to file: ") + err;
        }
    );

    firstCallStatus = writer.write("Hello, world!\n");
    secondCallStatus = writer.write(expectedContents);

    QThread::sleep(1);

    qApp->processEvents();

    QCOMPARE(firstCallStatus, true);
    QCOMPARE(secondCallStatus, true);
    QVERIFY(noErrors);
    QVERIFY(writeCompleted);
    QCOMPARE(writer.writeInProgress(), false);
    QVERIFY(writer.lastError().isEmpty());

    QFile file(writer.fileName());
    bool fileReadable = file.open(QIODevice::ReadOnly | QIODevice::Text);

    QVERIFY(fileReadable);

    if (fileReadable) {
        QString actualContents;
        QTextStream stream(&file);
        actualContents = stream.readAll();
        file.close();

        QCOMPARE(actualContents, expectedContents);

        file.remove();
    }
}

void AsyncTextWriterTest::waitForFinishedReportsFailure()
{
    QTemporaryDir temporary;
    QVERIFY(temporary.isValid());

    const QString missingParentFile = temporary.filePath("missing/child.md");
    AsyncTextWriter writer(missingParentFile);
    QSignalSpy errorSpy(&writer, &AsyncTextWriter::writeError);
    QSignalSpy completeSpy(&writer, &AsyncTextWriter::writeComplete);

    QVERIFY(writer.write("unsaved text"));
    QVERIFY(!writer.waitForFinished());
    QVERIFY(!writer.lastError().isEmpty());
    QCOMPARE(errorSpy.count(), 1);
    QCOMPARE(completeSpy.count(), 0);
    QVERIFY(!QFileInfo::exists(missingParentFile));
}

QTEST_MAIN(AsyncTextWriterTest)
#include "asynctextwritertest.moc"
