/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <algorithm>
#include <stdexcept>

#include <QApplication>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QKeyEvent>
#include <QRegularExpression>
#include <QScrollBar>
#include <QTextBlock>
#include <QTextCursor>
#include <QThread>
#include <QTimer>
#include <QUuid>

#include "editor/markdowndocument.h"
#include "editor/markdowneditor.h"
#include "editor/textformatoverlaycontroller.h"

using namespace ghostwriter;

namespace
{
constexpr int PaintTimeoutMs = 10'000;
// Grace period for a naturally scheduled post-show paint/exposure before an
// explicit update is requested (the offscreen platform does not always queue
// one on its own).
constexpr int NaturalPaintGraceMs = 250;
constexpr auto CollectorName = "qt-native-efficiency-v1";

class InstrumentedEditor final : public MarkdownEditor
{
public:
    explicit InstrumentedEditor(MarkdownDocument *document)
        : MarkdownEditor(document, ColorScheme{})
    {
        resize(1024, 768);
    }

    void armPaint(QElapsedTimer *timer)
    {
        m_paintTimer = timer;
        m_paintMilliseconds = -1.0;
    }

    double waitForPaint()
    {
        viewport()->update();
        QElapsedTimer timeout;
        timeout.start();
        while (m_paintMilliseconds < 0.0 && timeout.elapsed() < PaintTimeoutMs) {
            QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
        }
        if (m_paintMilliseconds < 0.0) {
            throw std::runtime_error("viewport paint timed out");
        }
        return m_paintMilliseconds;
    }

    // Pumps the event loop waiting for a naturally scheduled paint without
    // forcing an update; returns true when one arrived within graceMs.
    bool waitForNaturalPaint(int graceMs)
    {
        QElapsedTimer timeout;
        timeout.start();
        while (m_paintMilliseconds < 0.0 && timeout.elapsed() < graceMs) {
            QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
        }
        return m_paintMilliseconds >= 0.0;
    }

    double firstPaintMilliseconds() const
    {
        return m_paintMilliseconds;
    }

protected:
    void paintEvent(QPaintEvent *event) override
    {
        MarkdownEditor::paintEvent(event);
        if (m_paintTimer != nullptr) {
            m_paintMilliseconds = m_paintTimer->nsecsElapsed() / 1'000'000.0;
            m_paintTimer = nullptr;
        }
    }

private:
    QElapsedTimer *m_paintTimer{nullptr};
    double m_paintMilliseconds{-1.0};
};

QJsonObject readObject(const QString &path)
{
    QFile input(path);
    if (!input.open(QIODevice::ReadOnly)) {
        throw std::runtime_error(qPrintable(QStringLiteral("cannot read %1: %2").arg(path, input.errorString())));
    }
    QJsonParseError error;
    const QJsonDocument document = QJsonDocument::fromJson(input.readAll(), &error);
    if (error.error != QJsonParseError::NoError || !document.isObject()) {
        throw std::runtime_error(qPrintable(QStringLiteral("invalid JSON in %1: %2").arg(path, error.errorString())));
    }
    return document.object();
}

void writeObject(const QString &path, const QJsonObject &object)
{
    QFile output(path);
    QDir().mkpath(QFileInfo(path).absolutePath());
    if (!output.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        throw std::runtime_error(qPrintable(QStringLiteral("cannot write %1: %2").arg(path, output.errorString())));
    }
    output.write(QJsonDocument(object).toJson(QJsonDocument::Indented));
}

QString readFixture(const QJsonObject &fixture)
{
    QFile input(fixture.value(QStringLiteral("path")).toString());
    if (!input.open(QIODevice::ReadOnly)) {
        throw std::runtime_error(qPrintable(QStringLiteral("cannot read fixture: %1").arg(input.errorString())));
    }
    const QByteArray bytes = input.readAll();
    const QString actualHash = QString::fromLatin1(QCryptographicHash::hash(bytes, QCryptographicHash::Sha256).toHex());
    const QString expectedHash = fixture.value(QStringLiteral("corpus_sha256")).toString();
    if (actualHash != expectedHash) {
        throw std::runtime_error(qPrintable(QStringLiteral("fixture hash mismatch: expected %1, got %2").arg(expectedHash, actualHash)));
    }
    return QString::fromUtf8(bytes);
}

QJsonArray numbers(const QVector<double> &values)
{
    QJsonArray result;
    for (const double value : values) {
        result.append(value);
    }
    return result;
}

QJsonObject metric(const QString &name, const QString &trialKind, const QVector<double> &samples)
{
    return {
        {QStringLiteral("metric"), name},
        {QStringLiteral("unit"), QStringLiteral("ms")},
        {QStringLiteral("status"), QStringLiteral("measured")},
        {QStringLiteral("collector"), QString::fromLatin1(CollectorName)},
        {QStringLiteral("trial_kind"), trialKind},
        {QStringLiteral("samples"), numbers(samples)},
    };
}

struct TrialSamples {
    QVector<double> inputToPaint;
    QVector<double> guiEdit;
    QVector<double> uiStall;
    QVector<double> scrollFrame;
    QVector<double> tooltipLookup;
    QVector<double> openToEditable;
    QVector<double> windowVisible;
    QVector<double> hydrationFirst;
    QVector<double> hydrationComplete;
};

using FormatsByBlock = QHash<int, QList<QTextLayout::FormatRange>>;

FormatsByBlock findingFormats(const MarkdownDocument &document, const QString &corpus)
{
    FormatsByBlock result;
    QTextCharFormat format;
    format.setBackground(QColor(96, 165, 250, 72));
    format.setToolTip(QStringLiteral("Native benchmark finding"));
    TextFormatOverlayController::setPriority(format, 100);

    const int stride = corpus == QStringLiteral("dense") ? 1 : (corpus == QStringLiteral("unicode") ? 4 : 32);
    int token = 0;
    const QRegularExpression words(QStringLiteral("\\S+"));
    for (QTextBlock block = document.begin(); block.isValid(); block = block.next()) {
        auto iterator = words.globalMatch(block.text());
        auto &formats = result[block.position()];
        while (iterator.hasNext()) {
            const QRegularExpressionMatch match = iterator.next();
            if ((token++ % stride) != 0) {
                continue;
            }
            QTextLayout::FormatRange range;
            range.start = match.capturedStart();
            range.length = match.capturedLength();
            range.format = format;
            formats.append(range);
        }
        if (formats.isEmpty()) {
            result.remove(block.position());
        }
    }
    return result;
}

double coldOpenTrial(const QString &text, double *windowVisibleMilliseconds)
{
    // Anchor: the start of this cold trial. The collector hosts many
    // sequential trials in one process, so raw process uptime would charge
    // earlier fixtures' runtime to later windows; each cold trial stands in
    // for one application launch, and window_visible_ms spans trial start ->
    // first exposure/paint after show() -- the same launch-anchor convention
    // as open_to_editable_ms.
    QElapsedTimer timer;
    timer.start();
    MarkdownDocument document;
    InstrumentedEditor editor(&document);
    editor.armPaint(&timer);
    editor.show();
    // First exposure/paint after show(): prefer the naturally scheduled
    // paint; request one explicitly only when the platform queued none.
    if (!editor.waitForNaturalPaint(NaturalPaintGraceMs)) {
        editor.viewport()->update();
        *windowVisibleMilliseconds = editor.waitForPaint();
    } else {
        *windowVisibleMilliseconds = editor.firstPaintMilliseconds();
    }

    editor.armPaint(&timer);
    editor.setPlainText(text);
    editor.setFocus(Qt::OtherFocusReason);
    editor.waitForPaint();
    QTextCursor cursor(&document);
    cursor.movePosition(QTextCursor::End);
    document.setModified(false);
    return timer.nsecsElapsed() / 1'000'000.0;
}

void measureEditing(InstrumentedEditor &editor, MarkdownDocument &document, TrialSamples &samples)
{
    QTextCursor cursor(&document);
    cursor.setPosition(qMax(0, document.characterCount() / 2));
    editor.setTextCursor(cursor);
    editor.setFocus(Qt::OtherFocusReason);

    QElapsedTimer inputTimer;
    inputTimer.start();
    editor.armPaint(&inputTimer);

    bool heartbeatComplete = false;
    double heartbeatMilliseconds = -1.0;
    QTimer::singleShot(0, &editor, [&]() {
        heartbeatMilliseconds = inputTimer.nsecsElapsed() / 1'000'000.0;
        heartbeatComplete = true;
    });

    QKeyEvent press(QEvent::KeyPress, Qt::Key_X, Qt::NoModifier, QStringLiteral("x"));
    QElapsedTimer guiTimer;
    guiTimer.start();
    QApplication::sendEvent(&editor, &press);
    samples.guiEdit.append(guiTimer.nsecsElapsed() / 1'000'000.0);
    samples.inputToPaint.append(editor.waitForPaint());

    QElapsedTimer heartbeatTimeout;
    heartbeatTimeout.start();
    while (!heartbeatComplete && heartbeatTimeout.elapsed() < PaintTimeoutMs) {
        QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
    }
    if (!heartbeatComplete) {
        throw std::runtime_error("GUI heartbeat timed out");
    }
    samples.uiStall.append(heartbeatMilliseconds);
    document.undo();
    QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
}

void measureScroll(InstrumentedEditor &editor, TrialSamples &samples, int trial)
{
    QScrollBar *scrollbar = editor.verticalScrollBar();
    const int maximum = scrollbar->maximum();
    const int destination = maximum == 0 ? 0 : ((trial % 2 == 0) ? maximum : maximum / 3);
    QElapsedTimer timer;
    timer.start();
    editor.armPaint(&timer);
    scrollbar->setValue(destination);
    samples.scrollFrame.append(editor.waitForPaint());
}

void measureHydrationAndLookup(InstrumentedEditor &editor, MarkdownDocument &document, const FormatsByBlock &formats, TrialSamples &samples)
{
    TextFormatOverlayController *controller = editor.textFormatOverlayController();
    controller->clearChannel(QStringLiteral("benchmark"));
    QCoreApplication::processEvents(QEventLoop::AllEvents, 20);

    QElapsedTimer hydrationTimer;
    hydrationTimer.start();
    FormatsByBlock page;
    int pageSpans = 0;
    double firstPaint = -1.0;
    double finalPaint = -1.0;
    const auto flushPage = [&]() {
        if (page.isEmpty()) {
            return;
        }
        editor.armPaint(&hydrationTimer);
        controller->updateChannelFormats(QStringLiteral("benchmark"), page);
        finalPaint = editor.waitForPaint();
        if (firstPaint < 0.0) {
            firstPaint = finalPaint;
        }
        page.clear();
        pageSpans = 0;
    };
    QList<int> blockPositions = formats.keys();
    std::sort(blockPositions.begin(), blockPositions.end());
    for (const int blockPosition : blockPositions) {
        const auto &blockFormats = formats.value(blockPosition);
        if (!page.isEmpty() && (page.size() >= 64 || pageSpans + blockFormats.size() > 4'096)) {
            flushPage();
        }
        page.insert(blockPosition, blockFormats);
        pageSpans += blockFormats.size();
    }
    flushPage();
    if (firstPaint < 0.0 || finalPaint < 0.0) {
        throw std::runtime_error("fixture produced no hydration page");
    }
    samples.hydrationFirst.append(firstPaint);
    samples.hydrationComplete.append(finalPaint);

    QTextBlock lookupBlock;
    int lookupPosition = -1;
    for (const int blockPosition : blockPositions) {
        const auto &blockFormats = formats.value(blockPosition);
        if (!blockFormats.isEmpty()) {
            lookupBlock = document.findBlock(blockPosition);
            lookupPosition = blockFormats.constFirst().start;
            break;
        }
    }
    if (!lookupBlock.isValid() || lookupPosition < 0) {
        throw std::runtime_error("fixture produced no overlay lookup target");
    }

    constexpr int LookupIterations = 2'000;
    QTextLayout::FormatRange found;
    QElapsedTimer lookupTimer;
    lookupTimer.start();
    for (int index = 0; index < LookupIterations; ++index) {
        if (!controller->findFormatAt(QStringLiteral("benchmark"), lookupBlock, lookupPosition, found)) {
            throw std::runtime_error("overlay lookup failed");
        }
    }
    samples.tooltipLookup.append((lookupTimer.nsecsElapsed() / 1'000'000.0) / static_cast<double>(LookupIterations));
}

TrialSamples collectFixture(const QString &text, const QString &corpus, int warmTrials, int coldTrials)
{
    TrialSamples samples;
    for (int trial = 0; trial < coldTrials; ++trial) {
        double visibleMilliseconds = -1.0;
        samples.openToEditable.append(coldOpenTrial(text, &visibleMilliseconds));
        samples.windowVisible.append(visibleMilliseconds);
    }

    MarkdownDocument document;
    InstrumentedEditor editor(&document);
    editor.show();
    editor.setPlainText(text);
    editor.setFocus(Qt::OtherFocusReason);
    QCoreApplication::processEvents(QEventLoop::AllEvents, 50);
    const FormatsByBlock formats = findingFormats(document, corpus);

    for (int trial = 0; trial < warmTrials; ++trial) {
        measureEditing(editor, document, samples);
        measureHydrationAndLookup(editor, document, formats, samples);
        measureScroll(editor, samples, trial);
    }
    return samples;
}

QJsonObject resultObject(const QJsonObject &config, const QJsonObject &fixture, const TrialSamples &samples)
{
    const QJsonArray metrics = {
        metric(QStringLiteral("window_visible_ms"), QStringLiteral("cold"), samples.windowVisible),
        metric(QStringLiteral("input_to_paint_ms"), QStringLiteral("warm"), samples.inputToPaint),
        metric(QStringLiteral("gui_edit_ms"), QStringLiteral("warm"), samples.guiEdit),
        metric(QStringLiteral("ui_stall_ms"), QStringLiteral("warm"), samples.uiStall),
        metric(QStringLiteral("scroll_frame_ms"), QStringLiteral("warm"), samples.scrollFrame),
        metric(QStringLiteral("tooltip_lookup_ms"), QStringLiteral("warm"), samples.tooltipLookup),
        metric(QStringLiteral("open_to_editable_ms"), QStringLiteral("cold"), samples.openToEditable),
        metric(QStringLiteral("hydration_first_spans_ms"), QStringLiteral("warm"), samples.hydrationFirst),
        metric(QStringLiteral("hydration_complete_ms"), QStringLiteral("warm"), samples.hydrationComplete),
    };
    return {
        {QStringLiteral("schema_version"), 1},
        {QStringLiteral("run_id"), QUuid::createUuid().toString(QUuid::WithoutBraces)},
        {QStringLiteral("created_utc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)},
        {QStringLiteral("suite"), config.value(QStringLiteral("suite"))},
        {QStringLiteral("platform"), config.value(QStringLiteral("platform"))},
        {QStringLiteral("hardware"), config.value(QStringLiteral("hardware"))},
        {QStringLiteral("build"), config.value(QStringLiteral("build"))},
        {QStringLiteral("target"), config.value(QStringLiteral("target"))},
        {QStringLiteral("workload"),
         QJsonObject{
             {QStringLiteral("id"), fixture.value(QStringLiteral("id"))},
             {QStringLiteral("corpus"), fixture.value(QStringLiteral("corpus"))},
             {QStringLiteral("word_count"), fixture.value(QStringLiteral("word_count"))},
             {QStringLiteral("corpus_sha256"), fixture.value(QStringLiteral("corpus_sha256"))},
         }},
        {QStringLiteral("metrics"), metrics},
        {QStringLiteral("automatic_failures"), QJsonArray{}},
    };
}

int runCollector(const QString &configPath)
{
    const QJsonObject config = readObject(configPath);
    if (config.value(QStringLiteral("schema_version")).toInt() != 1) {
        throw std::runtime_error("unsupported collector configuration");
    }
    const int warmTrials = config.value(QStringLiteral("warm_trials")).toInt();
    const int coldTrials = config.value(QStringLiteral("cold_trials")).toInt();
    if (warmTrials < 1 || coldTrials < 1) {
        throw std::runtime_error("collector requires at least one warm and cold trial");
    }
    const QString outputDirectory = config.value(QStringLiteral("output_directory")).toString();
    const QJsonArray fixtures = config.value(QStringLiteral("fixtures")).toArray();
    if (outputDirectory.isEmpty() || fixtures.isEmpty()) {
        throw std::runtime_error("collector configuration has no output directory or fixtures");
    }

    for (const QJsonValue value : fixtures) {
        const QJsonObject fixture = value.toObject();
        const QString text = readFixture(fixture);
        const TrialSamples samples = collectFixture(text, fixture.value(QStringLiteral("corpus")).toString(), warmTrials, coldTrials);
        const QJsonObject result = resultObject(config, fixture, samples);
        writeObject(QDir(outputDirectory).filePath(fixture.value(QStringLiteral("id")).toString() + QStringLiteral(".native-ui.json")), result);
    }
    return 0;
}
}

int main(int argc, char *argv[])
{
    if (qEnvironmentVariableIsEmpty("QT_QPA_PLATFORM")) {
        qputenv("QT_QPA_PLATFORM", QByteArrayLiteral("offscreen"));
    }
    QApplication application(argc, argv);
    application.setQuitOnLastWindowClosed(false);
    try {
        const QString configPath = qEnvironmentVariable("THOTHPAD_EFFICIENCY_CONFIG");
        if (configPath.isEmpty()) {
            qInfo("Efficiency collector skipped: THOTHPAD_EFFICIENCY_CONFIG is not set");
            return 77;
        }
        return runCollector(configPath);
    } catch (const std::exception &error) {
        qCritical("Efficiency collector failed: %s", error.what());
        return 2;
    }
}
