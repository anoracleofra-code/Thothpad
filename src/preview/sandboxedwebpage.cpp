/*
 * SPDX-FileCopyrightText: 2018-2023 Megan Conkle <megan.conkle@kdemail.net>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QDesktopServices>

#include "sandboxedwebpage.h"

namespace ghostwriter
{
SandboxedWebPage::SandboxedWebPage(QObject *parent)
    : QWebEnginePage(parent)
{

}

SandboxedWebPage::~SandboxedWebPage()
{
    ;
}

QWebEnginePage *SandboxedWebPage::createWindow(QWebEnginePage::WebWindowType type)
{
    Q_UNUSED(type)

    // Deny all requests for new windows/popups: a page spawned from the
    // preview would not be governed by this class's navigation policy.
    return nullptr;
}

bool SandboxedWebPage::acceptNavigationRequest
(
    const QUrl &url,
    QWebEnginePage::NavigationType type,
    bool isMainFrame
)
{
    if (QWebEnginePage::NavigationTypeLinkClicked == type) {
        QDesktopServices::openUrl(url);
        return false;
    }

    // The preview is a single-document view: its content is set via
    // QWebEnginePage::setHtml(), which navigates to a data: URL holding
    // the internal preview.html wrapper.  Nothing else should ever
    // navigate.  Sub-frame (e.g., iframe) navigations are always
    // rejected, since hostile Markdown could otherwise embed frames
    // pointing at remote or file content.
    if (!isMainFrame) {
        return false;
    }

    const QUrl current = this->url();

    if (current.isEmpty()) {
        // Initial load of the preview wrapper: QWebEnginePage::setHtml()
        // commits the wrapper as a data: URL, so that is the only URL
        // accepted here.
        return url.scheme() == QStringLiteral("data");
    }

    // After the wrapper has committed, the only legitimate navigation is
    // a re-display of the same wrapper (setHtml() of identical content
    // produces an identical data: URL).  Redirects, form submissions,
    // scripted navigation, and navigations to any other URL are rejected.
    return url == current;
}
} // namespace ghostwriter
