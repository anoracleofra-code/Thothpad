/* SPDX-License-Identifier: GPL-3.0-or-later */
#include "lenslistswidget.h"
#include "../messageboxhelper.h"
#include <QCheckBox>
#include <QComboBox>
#include <QFile>
#include <QFileDialog>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QLineEdit>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QSaveFile>
#include <QSet>
#include <QStackedWidget>
#include <QVBoxLayout>

namespace ghostwriter {
namespace {
const QStringList LensIds = {"general_rules", "possible_adverbs", "possible_adjectives", "possible_verbs",
    "filter_words", "cliches", "formulaic_patterns", "repetition_rhythm", "repetition",
    "body_cinematic", "abstraction_agency", "metaphor_texture"};
QJsonArray phrases(const QString &text)
{
    QJsonArray result;
    QSet<QString> seen;
    for (const QString &line : text.split('\n')) {
        const QString phrase = line.trimmed();
        if (!phrase.isEmpty() && !seen.contains(phrase.toCaseFolded())) {
            result.append(phrase);
            seen.insert(phrase.toCaseFolded());
        }
    }
    return result;
}
QString lines(const QJsonValue &value)
{
    QStringList result;
    for (const auto &phrase : value.toArray()) result.append(phrase.toString());
    return result.join('\n');
}
}

LensListsWidget::LensListsWidget(const QJsonObject &lists, QWidget *parent)
    : QWidget(parent), m_lens(new QComboBox(this)), m_original(lists)
{
    const QStringList labels = {tr("Profile phrases"), tr("Adverbs"), tr("Adjectives"), tr("Verbs"),
        tr("Filter/filler"), tr("Cliches"), tr("Formulaic prose"), tr("Echoes"), tr("Repetition"),
        tr("Body/cinematic"), tr("Abstraction"), tr("Metaphor/texture")};
    m_lens->setObjectName(QStringLiteral("editableLens"));
    auto *pages = new QStackedWidget(this);
    for (int i = 0; i < LensIds.size(); ++i) {
        const QString id = LensIds[i];
        m_lens->addItem(labels[i], id);
        auto *page = new QWidget(pages);
        Editors editors{new QLineEdit(page), new QPlainTextEdit(page), new QPlainTextEdit(page),
            new QCheckBox(tr("Keep built-in checks alongside my phrases"), page),
            new QCheckBox(tr("Ignore quoted dialogue for this list"), page)};
        editors.name->setObjectName(id + QStringLiteral("ListName"));
        editors.include->setObjectName(id + QStringLiteral("Include"));
        editors.exclude->setObjectName(id + QStringLiteral("Exclude"));
        editors.builtin->setObjectName(id + QStringLiteral("Builtin"));
        editors.dialogue->setObjectName(id + QStringLiteral("Dialogue"));
        for (auto *edit : {editors.include, editors.exclude}) {
            edit->setPlaceholderText(tr("One literal word or phrase per line (up to 500). No regular expressions."));
            edit->setTabChangesFocus(true);
        }
        auto *form = new QFormLayout(page);
        form->addRow(tr("List name"), editors.name);
        form->addRow(editors.builtin);
        form->addRow(editors.dialogue);
        form->addRow(tr("Flag these phrases"), editors.include);
        form->addRow(tr("Ignore these matches"), editors.exclude);
        m_editors.insert(id, editors);
        setValues(id, lists.value(id).toObject());
        m_initial.insert(id, values(id));
        pages->addWidget(page);
    }
    connect(m_lens, &QComboBox::currentIndexChanged, pages, &QStackedWidget::setCurrentIndex);
    auto *help = new QLabel(tr("Custom phrases are case-insensitive whole-word matches, not grammatical classifications. "
        "Ignore removes an exact flagged phrase, not a whole paragraph containing that word. "
        "Uncheck built-in checks to use only your list. Existing profile hard/soft phrases are editable on the Writing tab. "
        "Save the profile to apply changes; import/export below shares just one lens list."), this);
    help->setWordWrap(true);
    auto *importButton = new QPushButton(tr("Load list…"), this);
    auto *exportButton = new QPushButton(tr("Export list…"), this);
    importButton->setAutoDefault(false);
    exportButton->setAutoDefault(false);
    connect(importButton, &QPushButton::clicked, this, [this]() {
        const QString path = QFileDialog::getOpenFileName(this, tr("Load Lens List"), {}, tr("Lens lists (*.json)"));
        if (path.isEmpty()) return;
        QFile file(path);
        if (!file.open(QIODevice::ReadOnly) || file.size() > 262144) {
            MessageBoxHelper::warning(this, tr("List not loaded"), tr("Cannot read this file, or it exceeds 256 KiB."));
            return;
        }
        const auto document = QJsonDocument::fromJson(file.readAll());
        const QString lens = document.object().value(QStringLiteral("lens")).toString();
        QString error;
        // Validate before replacing any draft. The apply method validates again.
        if (document.object().value(QStringLiteral("format")).toString() != QStringLiteral("thothpad-lens-list")
            || document.object().value(QStringLiteral("version")) != QJsonValue(1)
            || !validateLists(QJsonObject{{lens, document.object().value(QStringLiteral("list"))}}, &error)) {
            MessageBoxHelper::warning(this, tr("List not loaded"), error.isEmpty() ? tr("Not a supported ThothPad lens-list file.") : error);
            return;
        }
        if (MessageBoxHelper::question(this, tr("Replace this lens list?"),
                tr("This replaces the %1 list in the editor. Nothing is applied until you save the profile.").arg(m_lens->itemText(m_lens->findData(lens))),
                QMessageBox::Yes | QMessageBox::No, QMessageBox::No) != QMessageBox::Yes) return;
        loadListDocument(document.object(), &error);
    });
    connect(exportButton, &QPushButton::clicked, this, [this]() {
        const auto document = selectedListDocument();
        QString error;
        if (!validateLists(QJsonObject{{m_lens->currentData().toString(), document.value(QStringLiteral("list"))}}, &error)) {
            MessageBoxHelper::warning(this, tr("List not exported"), error);
            return;
        }
        const QString path = QFileDialog::getSaveFileName(this, tr("Export Lens List"),
            m_lens->currentData().toString() + QStringLiteral(".json"), tr("Lens lists (*.json)"));
        if (path.isEmpty()) return;
        QSaveFile file(path);
        const QByteArray bytes = QJsonDocument(document).toJson(QJsonDocument::Indented);
        if (!file.open(QIODevice::WriteOnly) || file.write(bytes) != bytes.size() || !file.commit())
            MessageBoxHelper::warning(this, tr("List not exported"), file.errorString());
    });
    auto *buttons = new QHBoxLayout;
    buttons->addWidget(importButton);
    buttons->addWidget(exportButton);
    buttons->addStretch();
    auto *layout = new QVBoxLayout(this);
    layout->addWidget(m_lens);
    layout->addWidget(help);
    layout->addWidget(pages, 1);
    layout->addLayout(buttons);
}

QJsonObject LensListsWidget::values(const QString &lens) const
{
    const auto e = m_editors.value(lens);
    return {{"name", e.name->text().trimmed()}, {"include", phrases(e.include->toPlainText())},
        {"exclude", phrases(e.exclude->toPlainText())}, {"use_builtin", e.builtin->isChecked()},
        {"ignore_dialogue", e.dialogue->isChecked()}};
}
void LensListsWidget::setValues(const QString &lens, const QJsonObject &object)
{
    const auto e = m_editors.value(lens);
    e.name->setText(object.value("name").toString());
    e.include->setPlainText(lines(object.value("include")));
    e.exclude->setPlainText(lines(object.value("exclude")));
    e.builtin->setChecked(object.value("use_builtin").toBool(true));
    e.dialogue->setChecked(object.value("ignore_dialogue").toBool(false));
}
QJsonObject LensListsWidget::lists() const
{
    QJsonObject result = m_original;
    for (const QString &lens : LensIds) {
        const auto current = values(lens);
        if (current != m_initial.value(lens)) result.insert(lens, current);
    }
    return result;
}
void LensListsWidget::selectLens(const QString &lens)
{
    const int index = m_lens->findData(lens);
    if (index >= 0) m_lens->setCurrentIndex(index);
}
QJsonObject LensListsWidget::selectedListDocument() const
{
    const QString lens = m_lens->currentData().toString();
    return {{"format", "thothpad-lens-list"}, {"version", 1}, {"lens", lens}, {"list", values(lens)}};
}
bool LensListsWidget::loadListDocument(const QJsonObject &document, QString *error)
{
    const QString lens = document.value("lens").toString();
    if (document.value("format").toString() != QStringLiteral("thothpad-lens-list") || document.value("version") != QJsonValue(1)) {
        *error = tr("Not a supported ThothPad lens-list file.");
        return false;
    }
    if (!validateLists(QJsonObject{{lens, document.value("list")}}, error)) return false;
    setValues(lens, document.value("list").toObject());
    selectLens(lens);
    return true;
}
bool LensListsWidget::validateLists(const QJsonValue &value, QString *error)
{
    const auto fail = [error](const QString &message) { *error = message; return false; };
    if (!value.isObject()) return fail(tr("Lens lists must be an object."));
    const auto lists = value.toObject();
    for (auto it = lists.begin(); it != lists.end(); ++it) {
        if (!LensIds.contains(it.key()) || !it.value().isObject()) return fail(tr("Unknown lens or invalid list."));
        const auto list = it.value().toObject();
        for (const QString &key : list.keys()) {
            if (!QStringList{"name", "include", "exclude", "use_builtin", "ignore_dialogue"}.contains(key))
                return fail(tr("Unknown list setting: %1").arg(key));
        }
        const auto name = list.value("name");
        if (!name.isUndefined() && (!name.isString() || name.toString().size() > 128 || name.toString().contains('\n')
            || name.toString().contains('\r') || name.toString().contains(QChar::Null))) return fail(tr("List names must be one line, at most 128 characters."));
        for (const QString &key : {QStringLiteral("use_builtin"), QStringLiteral("ignore_dialogue")})
            if (list.contains(key) && !list.value(key).isBool()) return fail(tr("List switches must be true or false."));
        for (const QString &key : {QStringLiteral("include"), QStringLiteral("exclude")}) {
            if (!list.contains(key)) continue;
            const auto entries = list.value(key);
            if (!entries.isArray() || entries.toArray().size() > 500) return fail(tr("Each list can contain at most 500 phrases."));
            for (const auto &entry : entries.toArray()) {
                const QString phrase = entry.toString();
                if (!entry.isString() || phrase.trimmed().isEmpty() || phrase.size() > 256 || phrase.contains('\n')
                    || phrase.contains('\r') || phrase.contains(QChar::Null)) return fail(tr("Phrases must be single lines, 1–256 characters each."));
            }
        }
    }
    return true;
}
}
