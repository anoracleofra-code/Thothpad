/*
 * SPDX-FileCopyrightText: 2014-2024 Megan Conkle <megan.conkle@kdemail.net>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QApplication>
#include <QCryptographicHash>
#include <QDesktopServices>
#include <QDir>
#include <QFile>
#include <QFuture>
#include <QFutureWatcher>
#include <QMenu>
#include <QStack>
#include <QString>
#include <QTextStream>
#include <QTimer>
#include <QVariant>
#include <QWebChannel>
#include <QtConcurrentRun>

#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
#include <QWebEngineSettings>
#endif

#include <export/exporter.h>
#include "htmlpreview.h"
#include "previewproxy.h"
#include "sandboxedwebpage.h"

#ifdef THOTHPAD_INSTRUMENTATION
#include "../prose/proseinstrumentation.h"
#include <QElapsedTimer>
#endif

namespace ghostwriter
{
class HtmlPreviewPrivate
{
    Q_DECLARE_PUBLIC(HtmlPreview)

public:
    HtmlPreviewPrivate(HtmlPreview *q_ptr)
        : q_ptr(q_ptr)
    {
        proxy = new PreviewProxy(q_ptr);
    }

    ~HtmlPreviewPrivate()
    {
        ;
    }

    HtmlPreview *q_ptr;

    MarkdownDocument *document;
    bool updateInProgress;
    bool updateAgain;
    // Coalesces rapid typing-paused signals so the full-document export runs
    // at most once per quiet window instead of on every scaled pause.
    QTimer *updateDebounceTimer;
    // Hash of the text that produced the currently rendered HTML; a matching
    // hash skips the export entirely.
    QByteArray lastRenderedTextHash;
    // Hash of the text currently being exported by the worker.  It is
    // captured on the GUI thread at dispatch time and committed to
    // lastRenderedTextHash only after the export finished successfully,
    // so a failed export never marks its text as rendered.
    QByteArray pendingTextHash;
    PreviewProxy *proxy;
    QString baseUrl;
    QRegularExpression headingTagExp;
    Exporter *exporter;
    QString wrapperHtml;
    QFutureWatcher<QString> *futureWatcher;

    void onHtmlReady();
    void onLoadFinished(bool ok);

    /**
     * Sets the base directory path for determining resource
     * paths relative to the web page being previewed.
     * This method is called whenever the file path changes.
     */
    void updateBaseDir();
    /*
    * Sets the HTML contents to display, and creates a backup of the old
    * HTML for diffing to scroll to the first difference whenever
    * updatePreview() is called.
    */
    void setHtmlContent(const QString &html);

    void updatePreviewNow();

    static QString exportToHtml(const QString &text, Exporter *exporter);

    static QByteArray textHash(const QString &text)
    {
        return QCryptographicHash::hash(text.toUtf8(), QCryptographicHash::Sha256);
    }
};

HtmlPreview::HtmlPreview
(
    MarkdownDocument *document,
    Exporter *exporter,
    QWidget *parent
) : QWebEngineView(parent),
    d_ptr(new HtmlPreviewPrivate(this))
{
    Q_D(HtmlPreview);
    
    d->document = document;
    d->updateInProgress = false;
    d->updateAgain = false;
    d->exporter = exporter;
    d->proxy->setMathEnabled(d->exporter->supportsMath());

    d->updateDebounceTimer = new QTimer(this);
    d->updateDebounceTimer->setSingleShot(true);
    d->updateDebounceTimer->setInterval(300);
    connect(d->updateDebounceTimer, &QTimer::timeout, this, [this]() {
        Q_D(HtmlPreview);
        d->updatePreviewNow();
    });

    d->baseUrl = "";

    this->setPage(new SandboxedWebPage(this));
    this->settings()->setDefaultTextEncoding("utf-8");
    this->settings()->setAttribute(
        QWebEngineSettings::LocalContentCanAccessFileUrls,
        true);
    this->settings()->setAttribute(QWebEngineSettings::LocalContentCanAccessRemoteUrls, false);
    this->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Preferred);
    this->page()->action(QWebEnginePage::Reload)->setVisible(false);
    this->page()->action(QWebEnginePage::ReloadAndBypassCache)
        ->setVisible(false);
    this->page()->action(QWebEnginePage::OpenLinkInThisWindow)
        ->setVisible(false);
    this->page()->action(QWebEnginePage::OpenLinkInNewWindow)
        ->setVisible(false);
    this->page()->action(QWebEnginePage::ViewSource)->setVisible(false);
    this->page()->action(QWebEnginePage::SavePage)->setVisible(false);
    // The default profile is off-the-record. Do not clear its cache here:
    // clearHttpCache() is asynchronous, and this constructor navigates below.
    // Qt forbids navigation while a profile cache clear is in progress.

    this->connect(
        this,
        &QWebEngineView::loadFinished,
        [d](bool ok) {
            d->onLoadFinished(ok);
        }
    );

    d->headingTagExp.setPattern("^[Hh][1-6]$");

    d->futureWatcher = new QFutureWatcher<QString>(this);
    this->connect(
        d->futureWatcher,
        &QFutureWatcher<QString>::finished,
        [d]() {
            d->onHtmlReady();
        }
    );

    this->connect(
        document,
        &MarkdownDocument::filePathChanged,
        [d]() {
            d->updateBaseDir();
        }
    );

    // Set zoom factor for Chromium browser to account for system DPI settings,
    // since Chromium assumes 96 DPI as a fixed resolution.
    //
    qreal horizontalDpi =
        QGuiApplication::primaryScreen()->logicalDotsPerInchX();
    this->setZoomFactor((horizontalDpi / 96.0));

    QWebChannel *channel = new QWebChannel(this);
    channel->registerObject(QStringLiteral("previewProxy"), d->proxy);
    this->page()->setWebChannel(channel);

    QFile wrapperHtmlFile(":/resources/preview.html");

    if (!wrapperHtmlFile.open(QFile::ReadOnly | QFile::Text)) {
        d->wrapperHtml = tr("Error loading resources/preview.html");
    } else {
        QTextStream stream(&wrapperHtmlFile);
        d->wrapperHtml = stream.readAll();
        wrapperHtmlFile.close();
    }

    // Set the base URL and load the preview using wrapperHtml above.
    d->updateBaseDir();
}

HtmlPreview::~HtmlPreview()
{
    Q_D(HtmlPreview);
    
    // Wait for thread to finish if in the middle of updating the preview.
    d->futureWatcher->waitForFinished();
}

void HtmlPreview::contextMenuEvent(QContextMenuEvent *event)
{
    QMenu *menu = createStandardContextMenu();

    menu->popup(event->globalPos());
}

void HtmlPreview::updatePreview()
{
    Q_D(HtmlPreview);
    // Restart the quiet-window on every request: bursts of typing-paused
    // signals collapse into a single export after 300 ms of silence.
    d->updateDebounceTimer->start();
}

void HtmlPreviewPrivate::updatePreviewNow()
{
    Q_Q(HtmlPreview);

    if (updateInProgress) {
        updateAgain = true;
        return;
    }

    if (q->isVisible()) {
        // Some markdown processors don't handle empty text very well
        // and will err.  Thus, only pass in text from the document
        // into the markdown processor if the text isn't empty or null.
        //
        const QString currentText = document->toPlainText();
        const QByteArray hash = textHash(currentText);
        if (hash == lastRenderedTextHash) {
            return;
        }
        if (document->isEmpty()) {
            lastRenderedTextHash = hash;
            setHtmlContent("");
        } else if (nullptr != exporter) {
            if (!currentText.isNull() && !currentText.isEmpty()) {
                pendingTextHash = hash;
                updateInProgress = true;
                QFuture<QString> future = QtConcurrent::run(&HtmlPreviewPrivate::exportToHtml, currentText, exporter);
                futureWatcher->setFuture(future);
            }
        }
    }
}

void HtmlPreview::navigateToHeading(int headingSequenceNumber)
{
    this->page()->runJavaScript
    (
        QString
        (
            "scrollToHeading(%1);"
        ).arg(headingSequenceNumber)
    );
}

void HtmlPreview::setHtmlExporter(Exporter *exporter)
{
    Q_D(HtmlPreview);

    d->exporter = exporter;
    // Clear both hashes: the completed-render hash so the new exporter
    // re-renders, and the in-flight hash so a worker finishing after this
    // call cannot mark the (old-exporter) result as current.
    d->lastRenderedTextHash.clear();
    d->pendingTextHash.clear();
    d->setHtmlContent("");
    d->proxy->setMathEnabled(d->exporter->supportsMath());
    updatePreview();
}

void HtmlPreview::setStyleSheet(const QString &css)
{
    Q_D(HtmlPreview);

    d->proxy->setStyleSheet(css);
}

void HtmlPreview::setMathEnabled(bool enabled)
{
    Q_D(HtmlPreview);

    d->proxy->setMathEnabled(enabled);
}

void HtmlPreviewPrivate::onHtmlReady()
{
    Q_Q(HtmlPreview);
    
    setHtmlContent(futureWatcher->result());

    // The export finished successfully (any exception thrown by the
    // worker would have propagated out of result() above, skipping this
    // commit), so it is now safe to mark the text as rendered.
    lastRenderedTextHash = pendingTextHash;

    updateInProgress = false;

    if (updateAgain) {
        updateAgain = false;
        q->updatePreview();
    }

}

void HtmlPreviewPrivate::onLoadFinished(bool ok)
{
    Q_Q(HtmlPreview);
    
    if (ok) {
        q->page()->runJavaScript(
            "document.documentElement.contentEditable = false;");
    }
}

void HtmlPreviewPrivate::updateBaseDir()
{
    Q_Q(HtmlPreview);
    
    if (!document->filePath().isNull() && !document->filePath().isEmpty()) {
        // Note that a forward slash ("/") is appended to the path to
        // ensure it works.  If the slash isn't there, then it won't
        // recognize the base URL for some reason.
        //
        baseUrl = QUrl::fromLocalFile(
            QFileInfo(document->filePath()).dir().absolutePath() 
                      + "/").toString();
    } else {
        this->baseUrl = "";
    }

    q->setHtml(wrapperHtml, QUrl(baseUrl));
    q->updatePreview();
}

void HtmlPreview::closeEvent(QCloseEvent *event)
{
    Q_UNUSED(event);
    Q_D(HtmlPreview);
    
    d->setHtmlContent("");
}

void HtmlPreviewPrivate::setHtmlContent(const QString &html)
{
    this->proxy->setHtmlContent(html);
}

QString HtmlPreviewPrivate::exportToHtml
(
    const QString &text,
    Exporter *exporter
)
{
    QString html;

#ifdef THOTHPAD_INSTRUMENTATION
    QElapsedTimer exportTimer;
    exportTimer.start();
#endif

    // Export to HTML.  Smart typography is enabled per call for the
    // preview.  Note: do not toggle Exporter::setSmartTypographyEnabled()
    // here; this method runs on a worker thread while the GUI thread uses
    // the same Exporter instance, and mutating (or reading and restoring)
    // its shared state from this thread races with GUI-thread use.
    //
    // Safe mode is always on for the live preview: it renders whatever
    // document is open, including untrusted files, so raw HTML must be
    // escaped and dangerous URLs stripped.  Deliberate exports (file
    // export, "Copy as HTML") use their own call sites without safe mode.
    //
    exporter->exportToHtml(text, html, true, true);

#ifdef THOTHPAD_INSTRUMENTATION
    ProseInstrumentation::instance()->recordPreviewExport(exportTimer.elapsed());
#endif

    return html;
}
} // namespace ghostwriter
