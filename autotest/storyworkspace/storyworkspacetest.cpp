// SPDX-License-Identifier: GPL-3.0-or-later
#include "story/storyworkspace.h"
#include "story/storyworkspacedialog.h"
#include <QApplication>
#include <QDialog>
#include <QDialogButtonBox>
#include <QFile>
#include <QJsonDocument>
#include <QLineEdit>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QTemporaryDir>
#include <QTest>
#include <QTimer>
#include <QtEndian>
using namespace ghostwriter;

class StoryWorkspaceTest : public QObject
{
    Q_OBJECT
    static QJsonObject heading(QString title, QString path, QString fingerprint, int level, int start, int end)
    {
        return {{"title", title}, {"heading_path", path}, {"fingerprint", fingerprint}, {"level", level}, {"start", start}, {"end", end}};
    }
private slots:
    void migrationAndAtomicPersistence()
    {
        QTemporaryDir dir;
        StoryWorkspace work;
        QString error;
        QVERIFY(work.open(dir.filePath("sub/story.json"),
                          {{"scene_context", QJsonObject{{"setting", "Desert"}}},
                           {"characters", QJsonArray{QJsonObject{{"id", "mara"}, {"name", "Mara"}, {"voice", "Quiet"}}}}},
                          &error));
        QCOMPARE(work.effectiveContext("manuscript").value("setting").toString(), QString("Desert"));
        QVERIFY(work.effectiveSettings("manuscript").value("cast").toArray().contains("mara"));
        QVERIFY(work.save(&error));
        StoryWorkspace reopened;
        QVERIFY(reopened.open(work.path(), {}, &error));
        QCOMPARE(reopened.data, work.data);
        work.data.insert("test", "external change");
        QVERIFY(work.save(&error));
        QVERIFY(!reopened.save(&error));
        QVERIFY(error.contains("outside"));
        QFile file(work.path());
        QVERIFY(file.open(QIODevice::ReadOnly));
        QCOMPARE(QJsonDocument::fromJson(file.readAll()).object().value("test").toString(), QString("external change"));
    }
    void malformedWorkspaceIsNeverOverwritten()
    {
        QTemporaryDir dir;
        QFile file(dir.filePath("story.json"));
        QVERIFY(file.open(QIODevice::WriteOnly));
        file.write("{damaged");
        file.close();
        StoryWorkspace work;
        QString error;
        QVERIFY(!work.open(file.fileName(), {}, &error));
        QVERIFY(!work.save(&error));
        QVERIFY(file.open(QIODevice::ReadOnly));
        QCOMPARE(file.readAll(), QByteArray("{damaged"));
    }
    void scopesInheritAndKeepIdentityThroughRenameAndReorder()
    {
        StoryWorkspace work;
        work.data = StoryWorkspace::defaults();
        auto root = work.scope("manuscript");
        root.insert("context", QJsonObject{{"location", "Earth"}, {"goal", "Survive"}});
        work.setScope(root);
        auto first = heading("Same", "/Same#1", "first", 1, 0, 100);
        auto scene = heading("Scene", "/Same/Scene#1", "scene", 2, 10, 100);
        auto second = heading("Same", "/Same#2", "second", 1, 100, 200);
        work.reconcileHeadings({first, scene, second});
        const auto one = work.scopeAt(5, "chapter"), two = work.scopeAt(105, "chapter"), child = work.scopeAt(20, "scene");
        QVERIFY(one != two);
        QCOMPARE(work.scopeAt(20, "chapter"), one);
        QCOMPARE(work.scopeKind(one), QString("chapter"));
        QCOMPARE(work.scopeKind(child), QString("scene"));
        QCOMPARE(work.scopeKind("manuscript"), QString("manuscript"));
        QCOMPARE(work.scopeKind(work.scopeAt(5, "scene")), QString("chapter"));
        auto s = work.scope(one);
        s.insert("context", QJsonObject{{"goal", "Find the gate"}});
        work.setScope(s);
        QCOMPARE(work.effectiveContext(child).value("goal").toString(), QString("Find the gate"));
        QCOMPARE(work.effectiveContext(child).value("location").toString(), QString("Earth"));
        second.insert("start", 0);
        second.insert("end", 100);
        second.insert("heading_path", "/Same#1");
        first.insert("start", 100);
        first.insert("end", 200);
        first.insert("heading_path", "/Renamed#1");
        first.insert("title", "Renamed");
        work.reconcileHeadings({second, first});
        QCOMPARE(work.scopeAt(5, "chapter"), two);
        QCOMPARE(work.scopeAt(105, "chapter"), one);
        QVERIFY(work.scope(child).value("orphaned").toBool());
        QCOMPARE(work.effectiveContext(one).value("goal").toString(), QString("Find the gate"));
    }
    void memoriesAreScopedApprovedAndPrivate()
    {
        StoryWorkspace work;
        work.data = StoryWorkspace::defaults();
        work.reconcileHeadings({heading("One", "/One#1", "one", 1, 0, 100), heading("Two", "/Two#1", "two", 1, 100, 200)});
        const auto one = work.scopeAt(5, "chapter"), two = work.scopeAt(105, "chapter");
        auto memory = [](QString body, QString scope, QString owner, QString state, QString kind = "canon") {
            return QJsonObject{{"body", body}, {"scope_id", scope}, {"agent_id", owner}, {"state", state}, {"kind", kind}};
        };
        work.data.insert("memories",
                         QJsonArray{memory("shared", "manuscript", "", "approved"),
                                    memory("one", one, "a", "approved", "private"),
                                    memory("two", two, "a", "approved"),
                                    memory("other", one, "b", "approved"),
                                    memory("proposal", one, "a", "proposed"),
                                    memory("ownerless", one, "", "approved", "private")});
        const auto result = work.scopedMemories(one, {"a"});
        QCOMPARE(result.size(), 2);
        QCOMPARE(result[1].toObject().value("body").toString(), QString("one"));
        QCOMPARE(work.scopedMemories(two, {"a"}).size(), 2);
    }
    void buzzImportsArePortableAndNeverImportExecutionAuthority()
    {
        QJsonObject source{
            {"format", "buzz-agent-snapshot"},
            {"version", 1},
            {"definition",
             QJsonObject{{"name", "Mara"}, {"systemPrompt", "Stay quiet."}, {"command", "unsafe"}, {"apiKey", "must-not-import"}, {"provider", "other"}}},
            {"profile", QJsonObject{{"displayName", "Mara"}, {"about", "A traveler"}}},
            {"memory", QJsonObject{{"entries", QJsonArray{QJsonObject{{"slug", "core"}, {"body", "A secret"}}}}}}};
        QString error;
        const auto bytes = QJsonDocument(source).toJson();
        const auto agent = StoryWorkspace::importAgent(bytes, "json", &error);
        QCOMPARE(agent.value("name").toString(), QString("Mara"));
        QCOMPARE(agent.value("tools").toString(), QString("suggest"));
        QVERIFY(!agent.contains("provider"));
        QVERIFY(!agent.contains("apiKey"));
        QVERIFY(!agent.contains("command"));
        QCOMPARE(agent.value("imported_memories").toArray().size(), 1);
        const auto core = QJsonObject{{"agent_id", agent.value("id")}, {"title", "core"}, {"body", "secret"}, {"kind", "core"}, {"state", "approved"}};
        auto unapproved = core;
        unapproved.insert("body", "unapproved");
        unapproved.insert("state", "proposed");
        const auto none = StoryWorkspace::exportAgent(agent, {core}, "none");
        QVERIFY(!none.contains("secret"));
        QVERIFY(!none.contains("apiKey"));
        const auto withMemory = StoryWorkspace::exportAgent(agent, {core, unapproved}, "core");
        QVERIFY(withMemory.contains("secret"));
        QVERIFY(!withMemory.contains("unapproved"));
        QVERIFY(!StoryWorkspace::importAgent(withMemory, "json", &error).isEmpty());
        QByteArray chunk("buzz_agent_snapshot\0", 20);
        chunk += bytes.toBase64();
        QByteArray png("\x89PNG\r\n\x1a\n", 8);
        char size[4];
        qToBigEndian<quint32>(chunk.size(), reinterpret_cast<uchar *>(size));
        png.append(size, 4);
        png += "tEXt";
        png += chunk;
        png.append(4, '\0');
        QCOMPARE(StoryWorkspace::importAgent(png, "png", &error).value("name").toString(), QString("Mara"));
        QVERIFY(StoryWorkspace::importAgent(png.left(14), "png", &error).isEmpty());
        QCOMPARE(StoryWorkspace::importAgent("# Soul\nBe observant.", "md", &error).value("instructions").toString(), QString("# Soul\nBe observant."));
    }
    void sceneAndAgentFormsSaveWithoutProjectFolder()
    {
        QJsonObject scene;
        QTimer::singleShot(0, []() {
            auto *dialog = qobject_cast<QDialog *>(QApplication::activeModalWidget());
            QVERIFY(dialog);
            auto *setting = dialog->findChild<QPlainTextEdit *>("storyField_setting");
            QVERIFY(setting);
            setting->setPlainText("Desert ruins");
            dialog->findChild<QDialogButtonBox *>()->button(QDialogButtonBox::Save)->click();
        });
        QVERIFY(editStoryRecord(scene, "scene", nullptr));
        QCOMPARE(scene.value("setting").toString(), QString("Desert ruins"));
        auto agent = StoryWorkspace::agentTemplate("character");
        QTimer::singleShot(0, []() {
            auto *dialog = qobject_cast<QDialog *>(QApplication::activeModalWidget());
            QVERIFY(dialog);
            dialog->findChild<QLineEdit *>("storyField_name")->setText("Mara");
            dialog->findChild<QPlainTextEdit *>("storyField_instructions")->setPlainText("Observe before speaking.");
            dialog->findChild<QDialogButtonBox *>()->button(QDialogButtonBox::Save)->click();
        });
        QVERIFY(editStoryRecord(agent, "agent", nullptr));
        QCOMPARE(agent.value("name").toString(), QString("Mara"));
        QCOMPARE(agent.value("instructions").toString(), QString("Observe before speaking."));
    }
};
QTEST_MAIN(StoryWorkspaceTest)
#include "storyworkspacetest.moc"
