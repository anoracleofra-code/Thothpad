/*
 * SPDX-FileCopyrightText: 2020-2023 Megan Conkle <megan.conkle@kdemail.net>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef CMARK_PROCESSOR_H
#define CMARK_PROCESSOR_H

#include <QScopedPointer>

#include "markdownast.h"

namespace ghostwriter
{
/**
 * This class wraps the cmark-gfm API to make it thread-safe.
 */
class CmarkGfmAPIPrivate;
class CmarkGfmAPI
{
    Q_DECLARE_PRIVATE(CmarkGfmAPI)

public:
    /**
     * Returns the single instance of this class.
     */
    static CmarkGfmAPI *instance();

    /**
     * Destructor.
     */
    ~CmarkGfmAPI();

    /**
     * Parses the given Markdown text, returning an AST representation.
     * of the text.  Pass in true for smartTypographyEnabled to enable
     * smart typography.
     */
    MarkdownAST *parse(const QString &text, const bool smartTypographyEnabled);

    /**
     * Returns HTML text for the Markdown text.  Pass in true for
     * smartTypographyEnabled to enable smart typography.
     *
     * Safe mode controls how raw HTML and unsafe link URLs are rendered:
     * - safeMode == false: CMARK_OPT_UNSAFE is set, so raw HTML in the
     *   Markdown (including event-handler attributes such as onerror=)
     *   passes through to the output.  Use this ONLY for output that
     *   the user deliberately exports from their own document (HTML
     *   export to file, "Copy as HTML"), never for content rendered
     *   automatically from untrusted files.
     * - safeMode == true: CMARK_OPT_UNSAFE is unset, so raw HTML blocks
     *   and inline HTML are replaced with a placeholder comment and
     *   dangerous link/image URLs (javascript:, vbscript:, file:, and
     *   non-image data: schemes) are stripped.  The tagfilter extension
     *   stays enabled in both modes.  Use this for the live preview,
     *   which renders whatever is in the document the user opens,
     *   including files from untrusted sources.
     */
    QString renderToHtml(const QString &text, const bool smartTypographyEnabled, const bool safeMode = false);

protected:
    /**
     * Constructor.
     */
    CmarkGfmAPI();

private:
    QScopedPointer<CmarkGfmAPIPrivate> d_ptr;

};
}

#endif
