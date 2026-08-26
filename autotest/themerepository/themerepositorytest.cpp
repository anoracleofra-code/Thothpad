/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QTemporaryDir>
#include <QTest>

#include "theme/themerepository.h"

using ghostwriter::Theme;
using ghostwriter::ThemeRepository;

class ThemeRepositoryTest : public QObject
{
    Q_OBJECT

private slots:
    void includesThothPadPalettes();
    void preservesKanagawaLotusColors();
};

void ThemeRepositoryTest::includesThothPadPalettes()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    ThemeRepository repository(directory.path());

    const QStringList themes = repository.availableThemes();
    QVERIFY(themes.contains(QStringLiteral("Kanagawa Lotus")));
    QVERIFY(themes.contains(QStringLiteral("Kanagawa Lotus Paper")));
    QVERIFY(themes.contains(QStringLiteral("Kanagawa Wave")));
    QVERIFY(themes.contains(QStringLiteral("ThothPad Teal")));
    QVERIFY(themes.contains(QStringLiteral("ThothPad Neutral")));
}

void ThemeRepositoryTest::preservesKanagawaLotusColors()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    ThemeRepository repository(directory.path());
    QString error;
    const Theme theme = repository.loadTheme(QStringLiteral("Kanagawa Lotus"), error);

    QVERIFY(error.isNull());
    QCOMPARE(theme.lightColorScheme().background, QColor(QStringLiteral("#f4eed1")));
    QCOMPARE(theme.lightColorScheme().foreground, QColor(QStringLiteral("#222222")));
    QCOMPARE(theme.lightColorScheme().selection, QColor(QStringLiteral("#c8bb86")));
    QCOMPARE(theme.lightColorScheme().headingText, QColor(QStringLiteral("#111111")));
    QCOMPARE(theme.darkColorScheme().background, QColor(QStringLiteral("#09090b")));
    QCOMPARE(theme.darkColorScheme().foreground, QColor(QStringLiteral("#e4e4e7")));
}

QTEST_GUILESS_MAIN(ThemeRepositoryTest)

#include "themerepositorytest.moc"
