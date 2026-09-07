/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QApplication>
#include <QStackedWidget>
#include <QTest>

#include "../../src/sidebar.h"

using namespace ghostwriter;

class SidebarTest : public QObject
{
    Q_OBJECT

private slots:
    void removeActiveTabSelectsNeighbor();
    void removeNonActiveTabKeepsSelection();
    void removeTabBeforeActiveKeepsWidgetSelected();
    void setCurrentTabIndexClamps();
    void removeOnlyTab();
    void removeMiddleActiveTabSelectsPrevious();
};

void SidebarTest::removeActiveTabSelectsNeighbor()
{
    Sidebar sidebar;
    QWidget *tabWidgets[3];

    for (int i = 0; i < 3; i++) {
        tabWidgets[i] = new QWidget(&sidebar);
        sidebar.addTab(QIcon(), tabWidgets[i], QStringLiteral("tab%1").arg(i));
    }

    sidebar.setCurrentTabIndex(0);
    QCOMPARE(sidebar.tabCount(), 3);

    sidebar.removeTab(0);
    QCOMPARE(sidebar.tabCount(), 2);
    QVERIFY(nullptr != sidebar.findChild<QStackedWidget *>(QStringLiteral("sidebarStack")));
    QTRY_COMPARE(sidebar.findChild<QStackedWidget *>(QStringLiteral("sidebarStack"))->currentIndex(), 0);
}

void SidebarTest::removeNonActiveTabKeepsSelection()
{
    Sidebar sidebar;
    QWidget *tabWidgets[4];

    for (int i = 0; i < 4; i++) {
        tabWidgets[i] = new QWidget(&sidebar);
        sidebar.addTab(QIcon(), tabWidgets[i], QStringLiteral("tab%1").arg(i));
    }

    // active tab 2 of 4: removing tab 0 shifts the active widget to
    // index 1, so index-preservation and widget-preservation differ
    sidebar.setCurrentTabIndex(2);
    sidebar.removeTab(0);
    QCOMPARE(sidebar.tabCount(), 3);

    QStackedWidget *stack = sidebar.findChild<QStackedWidget *>(QStringLiteral("sidebarStack"));
    QVERIFY(nullptr != stack);
    QCOMPARE(stack->currentIndex(), 1);
    QVERIFY(stack->currentWidget() == tabWidgets[2]);
}

void SidebarTest::removeTabBeforeActiveKeepsWidgetSelected()
{
    Sidebar sidebar;
    QWidget *tabWidgets[3];

    for (int i = 0; i < 3; i++) {
        tabWidgets[i] = new QWidget(&sidebar);
        sidebar.addTab(QIcon(), tabWidgets[i], QStringLiteral("tab%1").arg(i));
    }

    sidebar.setCurrentTabIndex(1);
    sidebar.removeTab(0);
    QCOMPARE(sidebar.tabCount(), 2);

    QStackedWidget *stack = sidebar.findChild<QStackedWidget *>(QStringLiteral("sidebarStack"));
    QVERIFY(nullptr != stack);
    QCOMPARE(stack->currentIndex(), 0);
    QVERIFY(stack->currentWidget() == tabWidgets[1]);
}

void SidebarTest::setCurrentTabIndexClamps()
{
    Sidebar sidebar;
    QWidget *tabWidgets[3];

    for (int i = 0; i < 3; i++) {
        tabWidgets[i] = new QWidget(&sidebar);
        sidebar.addTab(QIcon(), tabWidgets[i], QStringLiteral("tab%1").arg(i));
    }

    sidebar.setCurrentTabIndex(0);

    // one-past-the-end used to slip past the `>` check and null-deref
    sidebar.setCurrentTabIndex(3);
    QStackedWidget *stack = sidebar.findChild<QStackedWidget *>(QStringLiteral("sidebarStack"));
    QVERIFY(nullptr != stack);
    QCOMPARE(stack->currentIndex(), 2);

    sidebar.setCurrentTabIndex(99);
    QCOMPARE(stack->currentIndex(), 2);

    sidebar.setCurrentTabIndex(-1);
    QCOMPARE(stack->currentIndex(), 0);
}

void SidebarTest::removeOnlyTab()
{
    Sidebar sidebar;
    QWidget *widget = new QWidget(&sidebar);
    sidebar.addTab(QIcon(), widget, QStringLiteral("only"));
    QCOMPARE(sidebar.tabCount(), 1);

    sidebar.removeTab(0);
    QCOMPARE(sidebar.tabCount(), 0);

    QStackedWidget *stack = sidebar.findChild<QStackedWidget *>(QStringLiteral("sidebarStack"));
    QVERIFY(nullptr != stack);
    QCOMPARE(stack->count(), 0);
    QCOMPARE(stack->currentIndex(), -1);
}

void SidebarTest::removeMiddleActiveTabSelectsPrevious()
{
    Sidebar sidebar;
    QWidget *tabWidgets[3];

    for (int i = 0; i < 3; i++) {
        tabWidgets[i] = new QWidget(&sidebar);
        sidebar.addTab(QIcon(), tabWidgets[i], QStringLiteral("tab%1").arg(i));
    }

    sidebar.setCurrentTabIndex(1);
    sidebar.removeTab(1);
    QCOMPARE(sidebar.tabCount(), 2);

    QStackedWidget *stack = sidebar.findChild<QStackedWidget *>(QStringLiteral("sidebarStack"));
    QVERIFY(nullptr != stack);
    QCOMPARE(stack->currentIndex(), 0);
    QVERIFY(stack->currentWidget() == tabWidgets[0]);
}

QTEST_MAIN(SidebarTest)
#include "sidebartest.moc"
