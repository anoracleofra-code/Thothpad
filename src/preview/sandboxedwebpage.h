/*
 * SPDX-FileCopyrightText: 2018-2023 Megan Conkle <megan.conkle@kdemail.net>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef SANDBOXEDWEBPAGE_H
#define SANDBOXEDWEBPAGE_H

#include <QWebEnginePage>

namespace ghostwriter
{
/**
 * Web page for use with QWebEngineView that is "sandboxed" such that
 * external links cannot be visited without launching the default system
 * browser.
 *
 * Navigation is additionally locked down to the live preview's own
 * internal wrapper page: link clicks are handed to the system browser,
 * and every other navigation (redirects, form submissions, scripted
 * navigation, sub-frame navigations) is rejected unless it is a
 * re-display of the preview's own wrapper document.  This prevents
 * script or markup embedded in a hostile Markdown file from navigating
 * the preview to remote or file URLs.
 */
class SandboxedWebPage : public QWebEnginePage
{
public:
    /**
     * Constructor.
     */
    SandboxedWebPage(QObject *parent = nullptr);

    /**
     * Destructor.
     */
    virtual ~SandboxedWebPage();

    /**
     * Handles link clicks and opens external links with the
     * default system browser; rejects all other navigations
     * except re-displays of the preview's own wrapper page.
     */
    bool acceptNavigationRequest(
        const QUrl &url,
        QWebEnginePage::NavigationType type,
        bool isMainFrame
    ) override;

    /**
     * Denies all requests for new windows or popups, which would
     * otherwise create a page outside this navigation policy.
     */
    QWebEnginePage *createWindow(WebWindowType type) override;
};
} // namespace ghostwriter

#endif // SANDBOXEDWEBPAGE_H
