/* SPDX-License-Identifier: GPL-3.0-or-later */
#include <QCheckBox>
#include <QComboBox>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLineEdit>
#include <QPlainTextEdit>
#include <QTest>
#include "../../src/prose/lenslistswidget.h"
#include "../../src/prose/profileeditordialog.h"
using namespace ghostwriter;

class LensListsTest : public QObject
{
    Q_OBJECT
private slots:
    void unchangedListsPreserveOriginal()
    {
        const QJsonObject original{{"filter_words", QJsonObject{{"include", QJsonArray{"moon dust"}}}}};
        LensListsWidget widget(original);
        QCOMPARE(widget.lists(), original);
        LensListsWidget empty({});
        QVERIFY(empty.lists().isEmpty());
    }
    void everyLensEditsAndSharesIndependently()
    {
        LensListsWidget widget({});
        auto *selector = widget.findChild<QComboBox *>(QStringLiteral("editableLens"));
        QCOMPARE(selector->count(), 12);
        for (int index = 0; index < selector->count(); ++index) {
            const QString lens = selector->itemData(index).toString();
            widget.selectLens(lens);
            widget.findChild<QLineEdit *>(lens + "ListName")->setText("Shared phrases");
            widget.findChild<QPlainTextEdit *>(lens + "Include")->setPlainText("moon dust\nMOON DUST\na+b\n");
            widget.findChild<QPlainTextEdit *>(lens + "Exclude")->setPlainText("saw");
            widget.findChild<QCheckBox *>(lens + "Builtin")->setChecked(false);
            const auto document = widget.selectedListDocument();
            QCOMPARE(document.value("lens").toString(), lens);
            QCOMPARE(document.value("list").toObject().value("include").toArray(), QJsonArray({"moon dust", "a+b"}));
            LensListsWidget recipient({});
            QString error;
            const auto serialized = QJsonDocument(document).toJson();
            QVERIFY2(recipient.loadListDocument(QJsonDocument::fromJson(serialized).object(), &error), qPrintable(error));
            QCOMPARE(recipient.selectedListDocument(), document);
            QCOMPARE(recipient.lists().size(), 1);
            QVERIFY(recipient.lists().contains(lens));
        }
        QCOMPARE(widget.lists().size(), 12);
    }
    void invalidImportLeavesDraftUntouched()
    {
        LensListsWidget widget({{"filter_words", QJsonObject{{"include", QJsonArray{"keep"}}}}});
        const auto before = widget.lists();
        QString error;
        QVERIFY(!widget.loadListDocument({{"format", "not-our-format"}}, &error));
        QVERIFY(!widget.loadListDocument({{"format", "thothpad-lens-list"}, {"version", 999}, {"lens", "filter_words"}, {"list", QJsonObject{}}}, &error));
        QVERIFY(!widget.loadListDocument({{"format", "thothpad-lens-list"}, {"version", 1}, {"lens", "grammar_mechanics"}, {"list", QJsonObject{}}}, &error));
        QVERIFY(!LensListsWidget::validateLists(QJsonObject{{"filter_words", QJsonObject{{"include", "not-array"}}}}, &error));
        QVERIFY(!LensListsWidget::validateLists(QJsonObject{{"filter_words", QJsonObject{{"use_builtin", "false"}}}}, &error));
        QCOMPARE(widget.lists(), before);
    }
    void oversizedListIsNotSilentlyTruncated()
    {
        LensListsWidget widget({});
        QStringList phrases;
        for (int i = 0; i < 501; ++i) phrases.append(QStringLiteral("phrase %1").arg(i));
        auto *editor = widget.findChild<QPlainTextEdit *>("filter_wordsInclude");
        editor->setPlainText(phrases.join('\n'));
        QCOMPARE(editor->document()->blockCount(), 501);
        QString error;
        QVERIFY(!LensListsWidget::validateLists(widget.lists(), &error));
        QCOMPARE(editor->document()->blockCount(), 501);
    }
    void profileSaveAsPreservesOtherSettings()
    {
        const QJsonObject original{{"name", "original"}, {"custom_metadata", QJsonObject{{"keep", 42}}},
            {"filter_words", QJsonObject{{"ignore_dialogue", true}}}, {"hard_bans", QJsonArray{"keep hard rule"}}};
        ProfileEditorDialog dialog(original);
        dialog.findChild<QLineEdit *>("profileSaveName")->setText("my-shared-list");
        dialog.findChild<QPlainTextEdit *>("filter_wordsInclude")->setPlainText("moon dust");
        dialog.accept();
        QCOMPARE(dialog.result(), int(QDialog::Accepted));
        const auto saved = dialog.profile();
        QCOMPARE(saved.value("name").toString(), QStringLiteral("my-shared-list"));
        QCOMPARE(saved.value("custom_metadata"), original.value("custom_metadata"));
        QCOMPARE(saved.value("hard_bans"), original.value("hard_bans"));
        QCOMPARE(saved.value("filter_words"), original.value("filter_words"));
        ProfileEditorDialog reopened(saved);
        QCOMPARE(reopened.profile(), saved);
        const QString image = qEnvironmentVariable("THOTHPAD_TEST_LISTS_IMAGE");
        if (!image.isEmpty()) {
            reopened.selectLens("filter_words");
            reopened.show();
            QTest::qWait(100);
            QVERIFY(reopened.grab().save(image));
        }
    }
};
QTEST_MAIN(LensListsTest)
#include "lensliststest.moc"
