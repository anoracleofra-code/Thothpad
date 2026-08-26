/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef REWRITE_REVIEW_DIALOG_H
#define REWRITE_REVIEW_DIALOG_H

#include <QDialog>
#include <QList>
#include <QStringList>

class QLabel;
class QListWidget;
class QPlainTextEdit;

namespace ghostwriter
{
class RewriteReviewDialog : public QDialog
{
    Q_OBJECT

public:
    RewriteReviewDialog(
        const QString &before,
        const QString &proposed,
        double scoreBefore,
        double scoreAfter,
        QWidget *parent = nullptr);

    QString acceptedText() const;

private:
    struct DiffHunk {
        int beforeStart = 0;
        int beforeCount = 0;
        int proposedStart = 0;
        int proposedCount = 0;
    };

    void buildHunks();
    void highlightChangedLines();

    QPlainTextEdit *m_before;
    QPlainTextEdit *m_proposed;
    QListWidget *m_hunkList;
    QStringList m_beforeLines;
    QStringList m_proposedLines;
    QList<DiffHunk> m_hunks;
};
}

#endif
