/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QJsonArray>
#include <QJsonObject>
#include <QPushButton>
#include <QSignalSpy>
#include <QTest>

#include "../../src/story/storyintelligencewidget.h"
#include "../../src/story/storytoolharness.h"

using namespace ghostwriter;

class StoryIntelligenceWidgetTest : public QObject
{
    Q_OBJECT

private slots:
    void goToEmitsExactAnnotationRange();
    void activityCardEmitsOperationSpecificUndo();
    void activityCardWithoutOperationHasNoUndoButton();
    void currentModelRevisionAllowsMutation();
    void staleModelRevisionRejectsMutation();
};

namespace
{
QPushButton *buttonWithText(QWidget &root, const QString &text)
{
    const QList<QPushButton *> buttons = root.findChildren<QPushButton *>();
    for (QPushButton *button : buttons) {
        if (button->text() == text) {
            return button;
        }
    }
    return nullptr;
}
}

void StoryIntelligenceWidgetTest::goToEmitsExactAnnotationRange()
{
    StoryIntelligenceWidget widget;
    QJsonObject annotation;
    annotation.insert(QStringLiteral("id"), QStringLiteral("mark-1"));
    annotation.insert(QStringLiteral("category"), QStringLiteral("voice"));
    annotation.insert(QStringLiteral("comment"), QStringLiteral("Inspect this phrase."));
    annotation.insert(QStringLiteral("quote"), QStringLiteral("ember"));
    annotation.insert(QStringLiteral("start_utf16"), 7);
    annotation.insert(QStringLiteral("end_utf16"), 12);
    widget.setAnnotations(QJsonArray{annotation});

    QSignalSpy spy(&widget, &StoryIntelligenceWidget::annotationNavigationRequested);
    QPushButton *goTo = buttonWithText(widget, QStringLiteral("Go to"));
    QVERIFY(goTo);

    goTo->click();

    QCOMPARE(spy.count(), 1);
    const QList<QVariant> arguments = spy.takeFirst();
    QCOMPARE(arguments.at(0).toInt(), 7);
    QCOMPARE(arguments.at(1).toInt(), 12);
    QCOMPARE(arguments.at(2).toString(), QStringLiteral("ember"));
}

void StoryIntelligenceWidgetTest::activityCardEmitsOperationSpecificUndo()
{
    StoryIntelligenceWidget widget;
    QSignalSpy spy(&widget, &StoryIntelligenceWidget::undoAgentTransactionRequested);

    widget.appendActivityCard(
        QStringLiteral("Apply objective grammar fixes"),
        QStringLiteral("Applied 3 verified changes"),
        QStringLiteral("operation-123"));

    QPushButton *undo = buttonWithText(widget, QStringLiteral("Undo AI edit"));
    QVERIFY(undo);
    undo->click();

    QCOMPARE(spy.count(), 1);
    QCOMPARE(spy.takeFirst().at(0).toString(), QStringLiteral("operation-123"));
}

void StoryIntelligenceWidgetTest::activityCardWithoutOperationHasNoUndoButton()
{
    StoryIntelligenceWidget widget;
    widget.appendActivityCard(
        QStringLiteral("Prose scan completed"),
        QStringLiteral("Fresh findings are available"));

    QVERIFY(!buttonWithText(widget, QStringLiteral("Undo AI edit")));
}

void StoryIntelligenceWidgetTest::currentModelRevisionAllowsMutation()
{
    QVERIFY(StoryToolHarness::mutationRevisionCurrent(12, 12));
}

void StoryIntelligenceWidgetTest::staleModelRevisionRejectsMutation()
{
    QVERIFY(!StoryToolHarness::mutationRevisionCurrent(12, 13));
    QVERIFY(!StoryToolHarness::mutationRevisionCurrent(-1, 0));
}

QTEST_MAIN(StoryIntelligenceWidgetTest)

#include "storyintelligencewidgettest.moc"
