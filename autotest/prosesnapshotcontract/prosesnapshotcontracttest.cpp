/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QJsonArray>
#include <QJsonObject>
#include <QTest>

#include "../../src/prose/prosecontroller.h"

using namespace ghostwriter;

class ProseSnapshotContractTest : public QObject
{
    Q_OBJECT

private slots:
    void noSnapshotCannotHydrateLens();
    void unknownLensCannotHydrateEvenWithSnapshot();
    void currentLoadedLensIsHydrated();
    void staleFindingPagesAreRejected();
};

void ProseSnapshotContractTest::noSnapshotCannotHydrateLens()
{
    QVERIFY(!AgentProseSnapshotContract::snapshotAvailable(QString()));
    QVERIFY(!AgentProseSnapshotContract::categoryCanHydrate(false, true));
    QVERIFY(!AgentProseSnapshotContract::categoryHydrated(false, true, true));
}

void ProseSnapshotContractTest::unknownLensCannotHydrateEvenWithSnapshot()
{
    QVERIFY(AgentProseSnapshotContract::snapshotAvailable(QStringLiteral("analysis-current")));
    QVERIFY(!AgentProseSnapshotContract::categoryCanHydrate(true, false));
    QVERIFY(!AgentProseSnapshotContract::categoryHydrated(true, false, true));
}

void ProseSnapshotContractTest::currentLoadedLensIsHydrated()
{
    QVERIFY(AgentProseSnapshotContract::categoryCanHydrate(true, true));
    QVERIFY(!AgentProseSnapshotContract::categoryHydrated(true, true, false));
    QVERIFY(AgentProseSnapshotContract::categoryHydrated(true, true, true));
}

void ProseSnapshotContractTest::staleFindingPagesAreRejected()
{
    SnapshotFindingPageState pages;
    pages.begin(QStringLiteral("analysis-current"), QStringLiteral("grammar_mechanics"), 42);

    QJsonObject current;
    current.insert(QStringLiteral("analysis_id"), QStringLiteral("analysis-current"));
    current.insert(QStringLiteral("diagnostics"), QJsonArray());
    current.insert(QStringLiteral("has_more"), false);

    QJsonObject stale = current;
    stale.insert(QStringLiteral("analysis_id"), QStringLiteral("analysis-old"));

    QVERIFY(!pages.accepts(QStringLiteral("grammar_mechanics"), 41, QString(), current));
    QVERIFY(!pages.accepts(QStringLiteral("grammar_mechanics"), 42, QString(), stale));
    QVERIFY(!pages.accepts(QStringLiteral("possible_adverbs"), 42, QString(), current));
    QVERIFY(pages.accepts(QStringLiteral("grammar_mechanics"), 42, QString(), current));
}

QTEST_MAIN(ProseSnapshotContractTest)
#include "prosesnapshotcontracttest.moc"
