/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "rewritereviewdialog.h"

#include <utility>

#include <QColor>
#include <QDialogButtonBox>
#include <QHBoxLayout>
#include <QLabel>
#include <QListWidget>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QTextBlock>
#include <QTextCursor>
#include <QTextFormat>
#include <QVBoxLayout>

namespace ghostwriter
{
RewriteReviewDialog::RewriteReviewDialog(
    const QString &before,
    const QString &proposed,
    double scoreBefore,
    double scoreAfter,
    QWidget *parent)
    : QDialog(parent)
    , m_before(new QPlainTextEdit(before, this))
    , m_proposed(new QPlainTextEdit(proposed, this))
    , m_hunkList(new QListWidget(this))
    , m_beforeLines(before.split(QLatin1Char('\n'), Qt::KeepEmptyParts))
    , m_proposedLines(proposed.split(QLatin1Char('\n'), Qt::KeepEmptyParts))
{
    setWindowTitle(tr("Review Suggested Revision"));
    resize(1000, 700);
    m_before->setReadOnly(true);
    m_proposed->setReadOnly(true);
    m_before->setAccessibleName(tr("Original text"));
    m_proposed->setAccessibleName(tr("Proposed text"));
    m_hunkList->setAccessibleName(tr("Selected revision changes"));
    m_hunkList->setMaximumHeight(160);

    auto *beforeLayout = new QVBoxLayout;
    beforeLayout->addWidget(new QLabel(tr("Original"), this));
    beforeLayout->addWidget(m_before);
    auto *proposedLayout = new QVBoxLayout;
    proposedLayout->addWidget(new QLabel(tr("Proposed"), this));
    proposedLayout->addWidget(m_proposed);
    auto *editors = new QHBoxLayout;
    editors->addLayout(beforeLayout, 1);
    editors->addLayout(proposedLayout, 1);

    auto *buttons = new QDialogButtonBox(
        QDialogButtonBox::Apply | QDialogButtonBox::Cancel, this);
    connect(buttons->button(QDialogButtonBox::Apply), &QPushButton::clicked,
        this, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);

    auto *layout = new QVBoxLayout(this);
    layout->addWidget(new QLabel(
        tr("Full proposal score: %1 to %2 (%3%4)")
            .arg(scoreBefore, 0, 'f', 3)
            .arg(scoreAfter, 0, 'f', 3)
            .arg(scoreAfter - scoreBefore >= 0.0 ? QStringLiteral("+") : QString())
            .arg(scoreAfter - scoreBefore, 0, 'f', 3),
        this));
    layout->addWidget(new QLabel(tr("Selected changes"), this));
    layout->addWidget(m_hunkList);
    layout->addLayout(editors, 1);
    layout->addWidget(buttons);
    buildHunks();
    highlightChangedLines();
}

QString RewriteReviewDialog::acceptedText() const
{
    QStringList accepted;
    int cursor = 0;
    for (int index = 0; index < m_hunks.size(); ++index) {
        const DiffHunk &hunk = m_hunks.at(index);
        while (cursor < hunk.beforeStart) {
            accepted.append(m_beforeLines.at(cursor++));
        }
        const bool useProposal = m_hunkList->item(index)->checkState() == Qt::Checked;
        const QStringList &source = useProposal ? m_proposedLines : m_beforeLines;
        const int start = useProposal ? hunk.proposedStart : hunk.beforeStart;
        const int count = useProposal ? hunk.proposedCount : hunk.beforeCount;
        for (int line = 0; line < count; ++line) {
            accepted.append(source.at(start + line));
        }
        cursor = hunk.beforeStart + hunk.beforeCount;
    }
    while (cursor < m_beforeLines.size()) {
        accepted.append(m_beforeLines.at(cursor++));
    }
    return accepted.join(QLatin1Char('\n'));
}

void RewriteReviewDialog::buildHunks()
{
    const int beforeCount = m_beforeLines.size();
    const int proposedCount = m_proposedLines.size();
    if (static_cast<qint64>(beforeCount) * proposedCount > 1'000'000) {
        m_hunks.append({0, beforeCount, 0, proposedCount});
    } else {
        const int columns = proposedCount + 1;
        QList<int> lcs((beforeCount + 1) * columns, 0);
        const auto at = [&lcs, columns](int beforeLine, int proposedLine) -> int & {
            return lcs[beforeLine * columns + proposedLine];
        };
        for (int left = beforeCount - 1; left >= 0; --left) {
            for (int right = proposedCount - 1; right >= 0; --right) {
                at(left, right) = m_beforeLines.at(left) == m_proposedLines.at(right)
                    ? at(left + 1, right + 1) + 1
                    : qMax(at(left + 1, right), at(left, right + 1));
            }
        }

        int left = 0;
        int right = 0;
        int hunkLeft = -1;
        int hunkRight = -1;
        const auto closeHunk = [this, &hunkLeft, &hunkRight, &left, &right]() {
            if (hunkLeft >= 0) {
                m_hunks.append({hunkLeft, left - hunkLeft,
                    hunkRight, right - hunkRight});
                hunkLeft = -1;
                hunkRight = -1;
            }
        };
        while (left < beforeCount || right < proposedCount) {
            if (left < beforeCount && right < proposedCount
                && m_beforeLines.at(left) == m_proposedLines.at(right)) {
                closeHunk();
                ++left;
                ++right;
                continue;
            }
            if (hunkLeft < 0) {
                hunkLeft = left;
                hunkRight = right;
            }
            if (right >= proposedCount
                || (left < beforeCount && at(left + 1, right) >= at(left, right + 1))) {
                ++left;
            } else {
                ++right;
            }
        }
        closeHunk();
    }

    for (const DiffHunk &hunk : std::as_const(m_hunks)) {
        QString label;
        if (hunk.beforeCount == 0) {
            label = tr("Add %1 line(s) after original line %2")
                .arg(hunk.proposedCount)
                .arg(hunk.beforeStart);
        } else if (hunk.proposedCount == 0) {
            label = tr("Remove original line(s) %1-%2")
                .arg(hunk.beforeStart + 1)
                .arg(hunk.beforeStart + hunk.beforeCount);
        } else {
            label = tr("Replace original line(s) %1-%2 with %3 line(s)")
                .arg(hunk.beforeStart + 1)
                .arg(hunk.beforeStart + hunk.beforeCount)
                .arg(hunk.proposedCount);
        }
        auto *item = new QListWidgetItem(label, m_hunkList);
        item->setFlags(item->flags() | Qt::ItemIsUserCheckable);
        item->setCheckState(Qt::Checked);
    }
}

void RewriteReviewDialog::highlightChangedLines()
{
    QList<QTextEdit::ExtraSelection> removed;
    QList<QTextEdit::ExtraSelection> added;
    for (const DiffHunk &hunk : std::as_const(m_hunks)) {
        QTextBlock beforeBlock = m_before->document()->findBlockByNumber(hunk.beforeStart);
        for (int line = 0; line < hunk.beforeCount && beforeBlock.isValid(); ++line) {
                QTextEdit::ExtraSelection selection;
                selection.cursor = QTextCursor(beforeBlock);
                selection.cursor.select(QTextCursor::BlockUnderCursor);
                QColor color("#F87171");
                color.setAlpha(52);
                selection.format.setBackground(color);
                selection.format.setUnderlineColor(QColor("#DC2626"));
                selection.format.setUnderlineStyle(QTextCharFormat::SingleUnderline);
                selection.format.setProperty(QTextFormat::FullWidthSelection, true);
                removed.append(selection);
                beforeBlock = beforeBlock.next();
        }
        QTextBlock proposedBlock = m_proposed->document()->findBlockByNumber(hunk.proposedStart);
        for (int line = 0; line < hunk.proposedCount && proposedBlock.isValid(); ++line) {
                QTextEdit::ExtraSelection selection;
                selection.cursor = QTextCursor(proposedBlock);
                selection.cursor.select(QTextCursor::BlockUnderCursor);
                QColor color("#4ADE80");
                color.setAlpha(52);
                selection.format.setBackground(color);
                selection.format.setUnderlineColor(QColor("#16A34A"));
                selection.format.setUnderlineStyle(QTextCharFormat::SingleUnderline);
                selection.format.setProperty(QTextFormat::FullWidthSelection, true);
                added.append(selection);
                proposedBlock = proposedBlock.next();
        }
    }
    m_before->setExtraSelections(removed);
    m_proposed->setExtraSelections(added);
}
}
