/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QCheckBox>
#include <QLabel>
#include <QPushButton>
#include <QSignalSpy>
#include <QStackedWidget>
#include <QTest>

#include "welcomedialog.h"

using ghostwriter::WelcomeDialog;

class WelcomeDialogTest : public QObject
{
    Q_OBJECT

private slots:
    void navigatesTourAndReleaseNotes();
    void emitsDocumentActions();
    void storesUpdatePreference();
};

void WelcomeDialogTest::navigatesTourAndReleaseNotes()
{
    WelcomeDialog dialog(QStringLiteral("0.1.0"));
    auto *pages = dialog.findChild<QStackedWidget *>(QStringLiteral("welcomePages"));
    auto *logo = dialog.findChild<QLabel *>(QStringLiteral("welcomeLogo"));
    auto *back = dialog.findChild<QPushButton *>(QStringLiteral("welcomeBackButton"));
    auto *tour = dialog.findChild<QPushButton *>(QStringLiteral("welcomeTourButton"));
    auto *whatsNew = dialog.findChild<QPushButton *>(QStringLiteral("welcomeWhatsNewButton"));

    QVERIFY(pages);
    QVERIFY(logo);
    QVERIFY(!logo->pixmap(Qt::ReturnByValue).isNull());
    QVERIFY(back);
    QVERIFY(tour);
    QVERIFY(whatsNew);
    QCOMPARE(pages->currentIndex(), 0);
    QVERIFY(!back->isVisible());

    tour->click();
    QCOMPARE(pages->currentIndex(), 1);
    back->click();
    QCOMPARE(pages->currentIndex(), 0);
    whatsNew->click();
    QCOMPARE(pages->currentIndex(), 2);
}

void WelcomeDialogTest::emitsDocumentActions()
{
    WelcomeDialog dialog(QStringLiteral("0.1.0"));
    QSignalSpy newSpy(&dialog, &WelcomeDialog::newDocumentRequested);
    auto *newButton = dialog.findChild<QPushButton *>(QStringLiteral("welcomeNewButton"));

    QVERIFY(newButton);
    newButton->click();
    QCOMPARE(newSpy.count(), 1);
}

void WelcomeDialogTest::storesUpdatePreference()
{
    WelcomeDialog dialog(QStringLiteral("0.1.0"));
    auto *checkBox = dialog.findChild<QCheckBox *>(QStringLiteral("welcomeUpdateCheckbox"));

    QVERIFY(checkBox);
    QVERIFY(dialog.showAfterUpdates());
    dialog.setShowAfterUpdates(false);
    QVERIFY(!dialog.showAfterUpdates());
    QVERIFY(!checkBox->isChecked());
}

QTEST_MAIN(WelcomeDialogTest)

#include "welcomedialogtest.moc"
