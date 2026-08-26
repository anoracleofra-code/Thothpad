/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "htmlpreview.h"

#include <QCloseEvent>
#include <QContextMenuEvent>

namespace ghostwriter
{
class HtmlPreviewPrivate
{
};

HtmlPreview::HtmlPreview(MarkdownDocument *, Exporter *, QWidget *parent)
    : QWidget(parent)
    , d_ptr(new HtmlPreviewPrivate)
{
    setVisible(false);
}

HtmlPreview::~HtmlPreview() = default;

void HtmlPreview::contextMenuEvent(QContextMenuEvent *event)
{
    event->ignore();
}

void HtmlPreview::updatePreview()
{
}

void HtmlPreview::navigateToHeading(int)
{
}

void HtmlPreview::setHtmlExporter(Exporter *)
{
}

void HtmlPreview::setStyleSheet(const QString &)
{
}

void HtmlPreview::setMathEnabled(bool)
{
}

void HtmlPreview::closeEvent(QCloseEvent *event)
{
    QWidget::closeEvent(event);
}
}
