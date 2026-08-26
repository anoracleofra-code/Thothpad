/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QListWidget>
#include <QTest>

#include "../../src/prose/rewritereviewdialog.h"

using namespace ghostwriter;

class RewriteReviewDialogTest : public QObject
{
    Q_OBJECT

private slots:
    void acceptsAllChanges();
    void rejectsAllChanges();
    void acceptsSelectedChanges();
};

namespace
{
const QString Before = QStringLiteral("alpha\nbeta\ngamma");
const QString Proposed = QStringLiteral("alpha\nBETA\ngamma\ndelta");
}

void RewriteReviewDialogTest::acceptsAllChanges()
{
    RewriteReviewDialog dialog(Before, Proposed, 4.0, 2.0);
    auto *changes = dialog.findChild<QListWidget *>();
    QVERIFY(changes);
    QCOMPARE(changes->count(), 2);
    QCOMPARE(dialog.acceptedText(), Proposed);
}

void RewriteReviewDialogTest::rejectsAllChanges()
{
    RewriteReviewDialog dialog(Before, Proposed, 4.0, 2.0);
    auto *changes = dialog.findChild<QListWidget *>();
    QVERIFY(changes);
    for (int index = 0; index < changes->count(); ++index) {
        changes->item(index)->setCheckState(Qt::Unchecked);
    }
    QCOMPARE(dialog.acceptedText(), Before);
}

void RewriteReviewDialogTest::acceptsSelectedChanges()
{
    RewriteReviewDialog dialog(Before, Proposed, 4.0, 2.0);
    auto *changes = dialog.findChild<QListWidget *>();
    QVERIFY(changes);
    changes->item(0)->setCheckState(Qt::Unchecked);
    QCOMPARE(dialog.acceptedText(), QStringLiteral("alpha\nbeta\ngamma\ndelta"));
}

QTEST_MAIN(RewriteReviewDialogTest)

#include "rewritereviewdialogtest.moc"
