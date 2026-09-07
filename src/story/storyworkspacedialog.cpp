// SPDX-License-Identifier: GPL-3.0-or-later
#include "storyworkspacedialog.h"
#include "storyworkspace.h"
#include <QCheckBox>
#include <QComboBox>
#include <QCoreApplication>
#include <QDateTime>
#include <QDialog>
#include <QDialogButtonBox>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QInputDialog>
#include <QJsonDocument>
#include <QLabel>
#include <QLineEdit>
#include <QListWidget>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QSaveFile>
#include <QScrollArea>
#include <QTabWidget>
#include <QVBoxLayout>
#include <functional>

namespace ghostwriter
{
namespace
{
QString tr(const char *text)
{
    return QCoreApplication::translate("StoryWorkspace", text);
}
QPushButton *button(QHBoxLayout *row, const QString &text, const std::function<void()> &action)
{
    auto *b = new QPushButton(text);
    row->addWidget(b);
    QObject::connect(b, &QPushButton::clicked, b, action);
    return b;
}
void writeExport(QWidget *parent, const QString &path, const QByteArray &bytes)
{
    if (path.isEmpty())
        return;
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly) || file.write(bytes) != bytes.size() || !file.commit())
        QMessageBox::warning(parent, tr("Export failed"), file.errorString());
}
}

bool editStoryRecord(QJsonObject &record, const QString &type, QWidget *parent)
{
    QDialog dialog(parent);
    dialog.setWindowTitle(type == "agent" ? tr("Character / agent") : type == "scene" ? tr("Scene context") : tr("Memory"));
    dialog.setObjectName(QStringLiteral("storyRecordDialog"));
    dialog.resize(670, 730);
    auto *layout = new QVBoxLayout(&dialog);
    auto *scroll = new QScrollArea(&dialog);
    scroll->setWidgetResizable(true);
    auto *body = new QWidget(scroll);
    auto *form = new QFormLayout(body);
    QHash<QString, QPlainTextEdit *> texts;
    QHash<QString, QLineEdit *> lines;
    QHash<QString, QComboBox *> choices;
    auto line = [&](const QString &key, const QString &caption) {
        auto *edit = new QLineEdit(record.value(key).toString(), body);
        edit->setObjectName(QStringLiteral("storyField_") + key);
        lines.insert(key, edit);
        form->addRow(caption, edit);
    };
    auto text = [&](const QString &key, const QString &caption) {
        auto *edit = new QPlainTextEdit(record.value(key).toString(), body);
        edit->setObjectName(QStringLiteral("storyField_") + key);
        edit->setMinimumHeight(80);
        edit->setMaximumHeight(key == "instructions" ? 220 : 120);
        texts.insert(key, edit);
        form->addRow(caption, edit);
    };
    auto choice = [&](const QString &key, const QString &caption, const QStringList &labels, const QStringList &values) {
        auto *combo = new QComboBox(body);
        for (int i = 0; i < labels.size(); ++i)
            combo->addItem(labels[i], values[i]);
        combo->setCurrentIndex(qMax(0, combo->findData(record.value(key).toString())));
        choices.insert(key, combo);
        form->addRow(caption, combo);
    };
    if (type == "agent") {
        line("name", tr("Name"));
        choice("kind",
               tr("Type"),
               {tr("Character"), tr("Co-Writer"), tr("Scene Architect"), tr("Continuity Editor"), tr("Custom")},
               {"character", "co_writer", "scene_architect", "continuity", "custom"});
        line("role", tr("Role"));
        text("summary", tr("Identity / backstory"));
        text("instructions", tr("Soul / instructions"));
        text("voice", tr("Voice / dialogue examples"));
        text("knowledge", tr("Knowledge / secrets"));
        text("goals", tr("Goals"));
        text("boundaries", tr("Boundaries / unknown facts"));
        text("sources", tr("Canon references"));
        choice("tools", tr("Manuscript tools"), {tr("Read, navigate and suggest"), tr("Request edits with confirmation")}, {"suggest", "edit"});
        choice("memory_policy", tr("Memory suggestions"), {tr("Offer for review"), tr("Do not propose memories")}, {"ask", "off"});
        line("model", tr("Model override (blank = current)"));
        line("provider", tr("Provider for override (e.g. openrouter)"));
        line("base_url", tr("Endpoint for override"));
        auto *hint = new QLabel(tr("Model overrides use credentials saved in Model Settings for that provider, endpoint and model. References are notes; files "
                                   "are read only from an explicitly opened project."),
                                body);
        hint->setWordWrap(true);
        form->addRow(hint);
    } else if (type == "scene") {
        text("setting", tr("Setting"));
        text("goal", tr("Current goal"));
        line("pov", tr("POV"));
        line("location", tr("Location"));
        line("time", tr("Time"));
        text("conflict", tr("Conflict"));
        text("stakes", tr("Stakes"));
        text("outcome", tr("Intended turn / outcome"));
        text("canon", tr("Established facts"));
        text("notes", tr("Notes"));
    } else {
        line("title", tr("Title"));
        text("body", tr("Memory"));
        choice("kind",
               tr("Kind"),
               {tr("Canon fact"), tr("Core identity"), tr("Private knowledge"), tr("Author preference"), tr("Session note")},
               {"canon", "core", "private", "preference", "session"});
        choice("state", tr("Status"), {tr("Awaiting review"), tr("Approved"), tr("Rejected")}, {"proposed", "approved", "rejected"});
        text("source", tr("Evidence / source"));
    }
    scroll->setWidget(body);
    layout->addWidget(scroll);
    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Save | QDialogButtonBox::Cancel, &dialog);
    layout->addWidget(buttons);
    QObject::connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    QObject::connect(buttons, &QDialogButtonBox::accepted, &dialog, [&]() {
        if (type == "agent" && !lines["provider"]->text().trimmed().isEmpty()
            && (lines["base_url"]->text().trimmed().isEmpty() || lines["model"]->text().trimmed().isEmpty())) {
            QMessageBox::information(&dialog,
                                     tr("Complete the model override"),
                                     tr("A provider override needs its endpoint and model. Leave all three blank to use Model Settings."));
            return;
        }
        if ((lines.contains("name") && lines["name"]->text().trimmed().isEmpty())
            || (texts.contains("body") && texts["body"]->toPlainText().trimmed().isEmpty())) {
            QMessageBox::information(&dialog, tr("Complete the form"), tr("Enter a name or memory text before saving."));
            return;
        }
        dialog.accept();
    });
    if (dialog.exec() != QDialog::Accepted)
        return false;
    for (auto i = lines.begin(); i != lines.end(); ++i)
        record.insert(i.key(), i.value()->text().trimmed());
    for (auto i = texts.begin(); i != texts.end(); ++i)
        record.insert(i.key(), i.value()->toPlainText().trimmed().left(48000));
    for (auto i = choices.begin(); i != choices.end(); ++i)
        record.insert(i.key(), i.value()->currentData().toString());
    return true;
}

bool editStoryWorkspace(QJsonObject &data, const QString &scopeId, int selectedTab, QWidget *parent)
{
    QDialog dialog(parent);
    dialog.setWindowTitle(tr("Story Workspace"));
    dialog.setObjectName(QStringLiteral("storyWorkspaceDialog"));
    dialog.resize(850, 670);
    StoryWorkspace work;
    work.data = data;
    auto *layout = new QVBoxLayout(&dialog);
    auto *tabs = new QTabWidget(&dialog);
    layout->addWidget(tabs);
    auto page = [&](const QString &title, QListWidget **list, QHBoxLayout **actions) {
        auto *p = new QWidget(tabs);
        auto *box = new QVBoxLayout(p);
        *list = new QListWidget(p);
        box->addWidget(*list, 1);
        *actions = new QHBoxLayout;
        box->addLayout(*actions);
        tabs->addTab(p, title);
        return box;
    };
    QListWidget *agents;
    QHBoxLayout *agentActions;
    page(tr("Characters & agents"), &agents, &agentActions);
    auto refreshAgents = [&]() {
        const int row = agents->currentRow();
        agents->clear();
        for (const auto &v : work.data.value("agents").toArray()) {
            auto a = v.toObject();
            auto *item = new QListWidgetItem(a.value("name").toString() + QStringLiteral(" · ") + a.value("kind").toString()
                                                 + (a.value("archived").toBool() ? tr(" (archived)") : QString()),
                                             agents);
            item->setData(Qt::UserRole, a.value("id").toString());
        }
        agents->setCurrentRow(qBound(0, row, agents->count() - 1));
    };
    auto selectedAgent = [&]() {
        return agents->currentItem() ? work.agent(agents->currentItem()->data(Qt::UserRole).toString()) : QJsonObject();
    };
    auto editAgent = [&]() {
        auto a = selectedAgent();
        if (!a.isEmpty() && editStoryRecord(a, "agent", &dialog)) {
            work.putAgent(a);
            refreshAgents();
        }
    };
    button(agentActions, tr("New"), [&]() {
        bool ok;
        const QStringList types{"character", "co_writer", "scene_architect", "continuity", "custom"};
        auto type = QInputDialog::getItem(&dialog, tr("New agent"), tr("Template"), types, 0, false, &ok);
        if (!ok)
            return;
        auto a = StoryWorkspace::agentTemplate(type);
        if (editStoryRecord(a, "agent", &dialog)) {
            work.putAgent(a);
            refreshAgents();
        }
    });
    button(agentActions, tr("Edit"), editAgent);
    QObject::connect(agents, &QListWidget::itemDoubleClicked, &dialog, [&](QListWidgetItem *) {
        editAgent();
    });
    button(agentActions, tr("Duplicate"), [&]() {
        auto a = selectedAgent();
        if (a.isEmpty())
            return;
        a.insert("id", StoryWorkspace::newId());
        a.insert("name", QString(a.value("name").toString() + tr(" copy")));
        work.putAgent(a);
        refreshAgents();
    });
    button(agentActions, tr("Archive / restore"), [&]() {
        auto a = selectedAgent();
        if (a.isEmpty())
            return;
        a.insert("archived", !a.value("archived").toBool());
        work.putAgent(a);
        refreshAgents();
    });
    button(agentActions, tr("Delete"), [&]() {
        auto a = selectedAgent();
        if (a.isEmpty())
            return;
        if (QMessageBox::question(&dialog, tr("Delete agent"), tr("Remove this profile? Saved conversations and memories remain available."))
            != QMessageBox::Yes)
            return;
        auto array = work.data.value("agents").toArray();
        for (int i = array.size() - 1; i >= 0; --i)
            if (array[i].toObject().value("id") == a.value("id"))
                array.removeAt(i);
        work.data.insert("agents", array);
        refreshAgents();
    });
    button(agentActions, tr("Import"), [&]() {
        auto path = QFileDialog::getOpenFileName(&dialog, tr("Import agent or soul"), QString(), tr("Agents (*.agent.json *.agent.png *.md);;All files (*)"));
        if (path.isEmpty())
            return;
        QFile file(path);
        if (!file.open(QIODevice::ReadOnly) || file.size() > 16 * 1024 * 1024) {
            QMessageBox::warning(&dialog, tr("Import failed"), tr("Cannot read file, or file exceeds 16 MB."));
            return;
        }
        QString error;
        auto a = StoryWorkspace::importAgent(file.readAll(), QFileInfo(path).suffix().toLower(), &error);
        if (a.isEmpty()) {
            QMessageBox::warning(&dialog, tr("Import failed"), error);
            return;
        }
        const auto imported = a.take("imported_memories").toArray();
        if (a.value("name").toString() == QStringLiteral("Imported soul"))
            a.insert("name", QFileInfo(path).completeBaseName());
        if (!editStoryRecord(a, "agent", &dialog))
            return;
        work.putAgent(a);
        if (!imported.isEmpty()
            && QMessageBox::question(&dialog,
                                     tr("Import memories"),
                                     tr("This profile includes %1 plaintext memories. Import them as proposals for review?").arg(imported.size()))
                == QMessageBox::Yes) {
            auto memories = work.data.value("memories").toArray();
            for (const auto &v : imported) {
                auto m = v.toObject();
                m.insert("id", StoryWorkspace::newId());
                m.insert("scope_id", scopeId);
                m.insert("agent_id", a.value("id"));
                m.insert("state", "proposed");
                m.insert("kind", m.value("title").toString() == "core" ? "core" : "private");
                memories.append(m);
            }
            work.data.insert("memories", memories);
        }
        refreshAgents();
    });
    button(agentActions, tr("Export"), [&]() {
        auto a = selectedAgent();
        if (a.isEmpty())
            return;
        bool ok;
        const auto level = QInputDialog::getItem(&dialog,
                                                 tr("Export agent"),
                                                 tr("Memory to include (plaintext in the shared file)"),
                                                 {"none", "core", "everything"},
                                                 0,
                                                 false,
                                                 &ok);
        if (!ok)
            return;
        auto path = QFileDialog::getSaveFileName(&dialog, tr("Export agent"), QStringLiteral("character.agent.json"), tr("Agent JSON (*.agent.json)"));
        writeExport(&dialog, path, StoryWorkspace::exportAgent(a, work.data.value("memories").toArray(), level));
    });
    refreshAgents();

    QListWidget *scopes;
    QHBoxLayout *scopeActions;
    auto *scopeBox = page(tr("Chapters & scenes"), &scopes, &scopeActions);
    auto *scopeHint =
        new QLabel(tr("Settings inherit from manuscript → chapter → scene. Deleted headings retain their saved data until you relink or remove it."));
    scopeHint->setWordWrap(true);
    scopeBox->insertWidget(0, scopeHint);
    auto refreshScopes = [&]() {
        const QString previous = scopes->currentItem() ? scopes->currentItem()->data(Qt::UserRole).toString() : scopeId;
        scopes->clear();
        for (const auto &v : work.data.value("scopes").toArray()) {
            auto r = v.toObject();
            auto *item = new QListWidgetItem(QString(qMax(0, r.value("level").toInt() - 1) * 2, QChar(' ')) + r.value("title").toString()
                                                 + (r.value("orphaned").toBool() ? tr(" — needs relinking") : QString()),
                                             scopes);
            item->setData(Qt::UserRole, r.value("id").toString());
            if (r.value("id").toString() == previous)
                scopes->setCurrentItem(item);
        }
    };
    auto selectedScope = [&]() {
        return scopes->currentItem() ? work.scope(scopes->currentItem()->data(Qt::UserRole).toString()) : QJsonObject();
    };
    button(scopeActions, tr("Edit context"), [&]() {
        auto s = selectedScope();
        if (s.isEmpty())
            return;
        auto c = work.effectiveContext(s.value("id").toString());
        if (editStoryRecord(c, "scene", &dialog)) {
            s.insert("context", c);
            work.setScope(s);
        }
    });
    button(scopeActions, tr("Inherit context"), [&]() {
        auto s = selectedScope();
        if (s.isEmpty())
            return;
        s.insert("context", QJsonObject());
        work.setScope(s);
    });
    button(scopeActions, tr("Assign agents"), [&]() {
        auto s = selectedScope();
        if (s.isEmpty())
            return;
        QDialog choose(&dialog);
        choose.setWindowTitle(tr("Agents for this scope"));
        auto *box = new QVBoxLayout(&choose);
        auto *writer = new QComboBox(&choose);
        writer->addItem(tr("Inherit co-writer"), QString());
        auto *cast = new QListWidget(&choose);
        auto settings = s.value("settings").toObject();
        auto selected = settings.value("cast").toArray();
        auto *inheritCast = new QCheckBox(tr("Inherit cast from parent"), &choose);
        inheritCast->setChecked(!settings.contains("cast"));
        for (const auto &v : work.data.value("agents").toArray()) {
            auto a = v.toObject();
            if (a.value("archived").toBool())
                continue;
            writer->addItem(a.value("name").toString(), a.value("id").toString());
            auto *item = new QListWidgetItem(a.value("name").toString(), cast);
            item->setData(Qt::UserRole, a.value("id").toString());
            item->setCheckState(selected.contains(a.value("id")) ? Qt::Checked : Qt::Unchecked);
        }
        writer->setCurrentIndex(qMax(0, writer->findData(settings.value("co_writer").toString())));
        box->addWidget(new QLabel(tr("Co-writer")));
        box->addWidget(writer);
        box->addWidget(inheritCast);
        box->addWidget(cast);
        auto *buttons = new QDialogButtonBox(QDialogButtonBox::Save | QDialogButtonBox::Cancel, &choose);
        box->addWidget(buttons);
        QObject::connect(buttons, &QDialogButtonBox::accepted, &choose, &QDialog::accept);
        QObject::connect(buttons, &QDialogButtonBox::rejected, &choose, &QDialog::reject);
        if (choose.exec() != QDialog::Accepted)
            return;
        if (writer->currentData().toString().isEmpty())
            settings.remove("co_writer");
        else
            settings.insert("co_writer", writer->currentData().toString());
        QJsonArray ids;
        for (int i = 0; i < cast->count(); ++i)
            if (cast->item(i)->checkState() == Qt::Checked)
                ids.append(cast->item(i)->data(Qt::UserRole).toString());
        if (inheritCast->isChecked())
            settings.remove("cast");
        else
            settings.insert("cast", ids);
        s.insert("settings", settings);
        work.setScope(s);
    });
    button(scopeActions, tr("Relink"), [&]() {
        auto s = selectedScope();
        if (!s.value("orphaned").toBool())
            return;
        QStringList labels, ids;
        for (const auto &v : work.data.value("scopes").toArray()) {
            auto r = v.toObject();
            if (!r.value("orphaned").toBool() && r.value("level").toInt() > 0) {
                labels.append(r.value("title").toString() + " · " + r.value("id").toString().left(8));
                ids.append(r.value("id").toString());
            }
        }
        bool ok;
        auto selected = QInputDialog::getItem(&dialog,
                                              tr("Relink saved context"),
                                              tr("Destination heading (its context and assignments will be replaced)"),
                                              labels,
                                              0,
                                              false,
                                              &ok);
        if (!ok)
            return;
        auto target = work.scope(ids.value(labels.indexOf(selected)));
        if (target.isEmpty())
            return;
        target.insert("context", s.value("context"));
        target.insert("settings", s.value("settings"));
        work.setScope(target);
        for (const auto *key : {"memories", "sessions", "markers"}) {
            auto records = work.data.value(key).toArray();
            for (int i = 0; i < records.size(); ++i) {
                auto r = records[i].toObject();
                if (r.value("scope_id") == s.value("id"))
                    r.insert("scope_id", target.value("id"));
                records[i] = r;
            }
            work.data.insert(key, records);
        }
        s.insert("context", QJsonObject());
        s.insert("settings", QJsonObject());
        work.setScope(s);
        refreshScopes();
    });
    refreshScopes();

    QListWidget *memories;
    QHBoxLayout *memoryActions;
    page(tr("Memories"), &memories, &memoryActions);
    auto refreshMemories = [&]() {
        memories->clear();
        for (const auto &v : work.data.value("memories").toArray()) {
            auto m = v.toObject();
            auto *item = new QListWidgetItem(m.value("title").toString() + " · " + m.value("state").toString() + " · "
                                                 + work.scope(m.value("scope_id").toString()).value("title").toString(),
                                             memories);
            item->setData(Qt::UserRole, m.value("id").toString());
            item->setToolTip(m.value("body").toString());
        }
    };
    auto editMemory = [&](bool fresh) {
        auto records = work.data.value("memories").toArray();
        int index = -1;
        QJsonObject m;
        if (!fresh && memories->currentItem())
            for (int i = 0; i < records.size(); ++i)
                if (records[i].toObject().value("id").toString() == memories->currentItem()->data(Qt::UserRole).toString()) {
                    index = i;
                    m = records[i].toObject();
                }
        if (!fresh && index < 0)
            return;
        if (fresh)
            m = QJsonObject{{"id", StoryWorkspace::newId()}, {"scope_id", scopeId}, {"state", "proposed"}};
        bool ok;
        QStringList names{tr("Shared with active agents")}, ids{QString()};
        for (const auto &v : work.data.value("agents").toArray()) {
            auto a = v.toObject();
            names.append(a.value("name").toString() + " · " + a.value("id").toString().left(8));
            ids.append(a.value("id").toString());
        }
        auto owner = QInputDialog::getItem(&dialog,
                                           tr("Memory owner"),
                                           tr("Who may receive this memory?"),
                                           names,
                                           qMax(0, ids.indexOf(m.value("agent_id").toString())),
                                           false,
                                           &ok);
        if (!ok)
            return;
        m.insert("agent_id", ids[names.indexOf(owner)]);
        QStringList scopeNames, scopeIds;
        for (const auto &v : work.data.value("scopes").toArray()) {
            const auto s = v.toObject();
            if (s.value("orphaned").toBool())
                continue;
            scopeNames.append(s.value("title").toString() + " · " + s.value("id").toString().left(8));
            scopeIds.append(s.value("id").toString());
        }
        const auto target = QInputDialog::getItem(&dialog,
                                                  tr("Memory scope"),
                                                  tr("Where should this memory apply?"),
                                                  scopeNames,
                                                  qMax(0, scopeIds.indexOf(m.value("scope_id").toString())),
                                                  false,
                                                  &ok);
        if (!ok || !scopeNames.contains(target))
            return;
        m.insert("scope_id", scopeIds[scopeNames.indexOf(target)]);
        m.insert("session_id", work.data.value("active_session"));
        if (!editStoryRecord(m, "memory", &dialog))
            return;
        if (m.value("kind").toString() == "private" && m.value("agent_id").toString().isEmpty()) {
            QMessageBox::information(&dialog, tr("Choose a private owner"), tr("Private knowledge must belong to a particular character or agent."));
            return;
        }
        if (index < 0)
            records.append(m);
        else
            records[index] = m;
        work.data.insert("memories", records);
        refreshMemories();
    };
    button(memoryActions, tr("New for current scope"), [&]() {
        editMemory(true);
    });
    button(memoryActions, tr("Review / edit"), [&]() {
        editMemory(false);
    });
    button(memoryActions, tr("Remove"), [&]() {
        if (!memories->currentItem())
            return;
        auto records = work.data.value("memories").toArray();
        for (int i = records.size() - 1; i >= 0; --i)
            if (records[i].toObject().value("id").toString() == memories->currentItem()->data(Qt::UserRole).toString())
                records.removeAt(i);
        work.data.insert("memories", records);
        refreshMemories();
    });
    refreshMemories();
    QObject::connect(tabs, &QTabWidget::currentChanged, &dialog, [&](int index) {
        if (index == 2)
            refreshMemories();
    });

    QListWidget *sessions;
    QHBoxLayout *sessionActions;
    page(tr("Saved conversations"), &sessions, &sessionActions);
    for (const auto &v : work.data.value("sessions").toArray()) {
        auto s = v.toObject();
        auto *item = new QListWidgetItem(s.value("title").toString() + " · " + work.scope(s.value("scope_id").toString()).value("title").toString(), sessions);
        item->setData(Qt::UserRole, s.value("id").toString());
    }
    button(sessionActions, tr("Open conversation"), [&]() {
        if (sessions->currentItem()) {
            work.data.insert("active_session", sessions->currentItem()->data(Qt::UserRole).toString());
            dialog.accept();
        }
    });
    button(sessionActions, tr("Export conversation"), [&]() {
        if (!sessions->currentItem())
            return;
        for (const auto &v : work.data.value("sessions").toArray()) {
            auto s = v.toObject();
            if (s.value("id").toString() != sessions->currentItem()->data(Qt::UserRole).toString())
                continue;
            QString output = "# " + s.value("title").toString() + "\n\n";
            for (const auto &entry : s.value("messages").toArray()) {
                auto m = entry.toObject();
                output += "## " + m.value("role").toString() + " " + m.value("speaker").toString() + "\n\n" + m.value("content").toString() + "\n\n";
            }
            writeExport(&dialog, QFileDialog::getSaveFileName(&dialog, tr("Export conversation"), "conversation.md", tr("Markdown (*.md)")), output.toUtf8());
        }
    });
    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Save | QDialogButtonBox::Cancel, &dialog);
    layout->addWidget(buttons);
    QObject::connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    QObject::connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    button(sessionActions, tr("Delete conversation"), [&]() {
        if (!sessions->currentItem())
            return;
        if (QMessageBox::question(&dialog,
                                  tr("Delete conversation"),
                                  tr("Delete this saved chat? Export it first if you want to retain a copy. Memories and manuscript marks are kept."))
            != QMessageBox::Yes)
            return;
        const auto id = sessions->currentItem()->data(Qt::UserRole).toString();
        auto records = work.data.value("sessions").toArray();
        for (int i = records.size() - 1; i >= 0; --i)
            if (records[i].toObject().value("id").toString() == id)
                records.removeAt(i);
        work.data.insert("sessions", records);
        if (work.data.value("active_session").toString() == id)
            work.data.remove("active_session");
        delete sessions->takeItem(sessions->currentRow());
    });
    tabs->setCurrentIndex(qBound(0, selectedTab, 3));
    if (dialog.exec() != QDialog::Accepted)
        return false;
    data = work.data;
    return true;
}
}
