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
    void noScanRequiresAnalysisBeforeHydration();
    void nonexistentLensIsRejected();
    void documentMutationInvalidatesSnapshotState();
    void staleInflightAnalysisCannotBecomeCurrent();
    void validCurrentZeroFindingLensIsComplete();
    void currentLoadedLensIsHydrated();
    void staleFindingPagesAreRejected();
};

void ProseSnapshotContractTest::noScanRequiresAnalysisBeforeHydration()
{
    QVERIFY(!AgentProseSnapshotContract::snapshotAvailable(QString()));
    QVERIFY(!AgentProseSnapshotContract::categoryCanHydrate(false, true));
    QVERIFY(!AgentProseSnapshotContract::categoryHydrated(false, true, true));
}

void ProseSnapshotContractTest::nonexistentLensIsRejected()
{
    QVERIFY(AgentProseSnapshotContract::snapshotAvailable(QStringLiteral("analysis-current")));
    QVERIFY(!AgentProseSnapshotContract::categoryCanHydrate(true, false));
    QVERIFY(!AgentProseSnapshotContract::categoryHydrated(true, false, true));
}

void ProseSnapshotContractTest::documentMutationInvalidatesSnapshotState()
{
    QString analysisId = QStringLiteral("analysis-current");
    SnapshotFindingPageState pages;
    pages.begin(analysisId, QStringLiteral("grammar_mechanics"), 42);

    QVERIFY(AgentProseSnapshotContract::snapshotAvailable(analysisId));
    QVERIFY(pages.active);

    // This is the state transition performed by ProseController when the
    // manuscript changes: the analysis identity and any in-flight snapshot
    // pagination state are discarded together.
    analysisId.clear();
    pages.reset();

    QVERIFY(!AgentProseSnapshotContract::snapshotAvailable(analysisId));
    QVERIFY(!pages.active);
    QCOMPARE(pages.revision, -1);
    QVERIFY(pages.analysisId.isEmpty());
}

void ProseSnapshotContractTest::staleInflightAnalysisCannotBecomeCurrent()
{
    QVERIFY(AgentProseSnapshotContract::analysisResponseCurrent(42, 42, 42));
    QVERIFY(!AgentProseSnapshotContract::analysisResponseCurrent(42, 42, 43));
    QVERIFY(!AgentProseSnapshotContract::analysisResponseCurrent(43, 42, 43));
    QVERIFY(!AgentProseSnapshotContract::analysisResponseCurrent(42, 43, 43));
}

void ProseSnapshotContractTest::validCurrentZeroFindingLensIsComplete()
{
    const bool snapshotAvailable = true;
    const bool categoryKnown = true;
    const int findingCount = 0;
    const bool categoryLoaded = findingCount == 0;

    QVERIFY(AgentProseSnapshotContract::categoryCanHydrate(snapshotAvailable, categoryKnown));
    QVERIFY(AgentProseSnapshotContract::categoryHydrated(
        snapshotAvailable, categoryKnown, categoryLoaded));
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
