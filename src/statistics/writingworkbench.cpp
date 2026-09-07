// SPDX-License-Identifier: GPL-3.0-or-later
#include "writingworkbench.h"
#include "../editor/markdowndocument.h"
#include "../editor/markdowneditor.h"
#include "../prose/proseawarenesswidget.h"
#include "../story/agentedittransactionmanager.h"
#include "../story/storyintelligencecontroller.h"
#include <QComboBox>
#include <QDateTime>
#include <QDialog>
#include <QDialogButtonBox>
#include <QEvent>
#include <QFileDialog>
#include <QFormLayout>
#include <QHeaderView>
#include <QInputDialog>
#include <QJsonDocument>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPainter>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QRegularExpression>
#include <QSaveFile>
#include <QScrollArea>
#include <QSignalBlocker>
#include <QTreeWidget>
#include <QToolButton>
#include <QVBoxLayout>
#include <QtConcurrent>
#include <algorithm>

namespace ghostwriter
{
namespace
{
class SentenceRhythmChart : public QWidget
{
public:
    explicit SentenceRhythmChart(QWidget *parent)
        : QWidget(parent)
    {
        setFixedHeight(112);
        setMinimumWidth(0);
        setAccessibleName(tr("Sentence length distribution"));
    }
    void setSentences(const QJsonArray &sentences)
    {
        counts.fill(0, 6);
        for (const auto &v : sentences)
            ++counts[qMin(5, qMax(0, (v.toObject().value("words").toInt() - 1) / 10))];
        QStringList description;
        for (int i = 0; i < counts.size(); ++i)
            description << tr("%1 words: %2 sentences").arg(labels[i]).arg(counts[i]);
        setToolTip(description.join('\n'));
        setAccessibleDescription(toolTip());
        update();
    }

protected:
    void paintEvent(QPaintEvent *) override
    {
        if (counts.isEmpty())
            return;
        QPainter painter(this);
        painter.setRenderHint(QPainter::Antialiasing);
        QColor ink = palette().color(QPalette::Text);
        QColor fill = ink;
        fill.setAlpha(65);
        painter.setPen(ink);
        const int maximum = qMax(1, *std::max_element(counts.cbegin(), counts.cend()));
        const double cell = width() / 6.0;
        for (int i = 0; i < 6; ++i) {
            const double height = 60.0 * counts[i] / maximum;
            painter.setPen(Qt::NoPen);
            painter.setBrush(fill);
            painter.drawRoundedRect(QRectF(i * cell + 5, 80 - height, cell - 10, height), 3, 3);
            painter.setPen(ink);
            painter.drawText(QRectF(i * cell, 80 - height - 20, cell, 18), Qt::AlignCenter, QString::number(counts[i]));
            painter.drawText(QRectF(i * cell, 86, cell, 22), Qt::AlignCenter, labels[i]);
        }
    }

private:
    QList<int> counts;
    const QStringList labels{"1–10", "11–20", "21–30", "31–40", "41–50", "51+"};
};
QLabel *label(const QString &text, QWidget *parent)
{
    auto *l = new QLabel(text, parent);
    l->setTextFormat(Qt::PlainText);
    l->setWordWrap(true);
    l->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Fixed);
    return l;
}
QPushButton *button(const QString &text, QHBoxLayout *row, QWidget *parent)
{
    auto *b = new QPushButton(text, parent);
    row->addWidget(b);
    return b;
}
QTreeWidget *tree(QWidget *parent, const QStringList &headers)
{
    auto *t = new QTreeWidget(parent);
    t->setHeaderLabels(headers);
    t->setRootIsDecorated(false);
    t->setMinimumWidth(0);
    t->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Expanding);
    t->setUniformRowHeights(true);
    t->setAlternatingRowColors(false);
    t->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    t->header()->setStretchLastSection(false);
    t->header()->setSectionResizeMode(0, QHeaderView::Stretch);
    for (int i = 1; i < headers.size(); ++i)
        t->header()->setSectionResizeMode(i, QHeaderView::ResizeToContents);
    return t;
}
QTreeWidgetItem *row(QTreeWidget *tree, const QString &title, const QString &value, const QJsonObject &record = {})
{
    auto *r = new QTreeWidgetItem(tree, {title, value});
    r->setToolTip(0, title);
    r->setData(0, Qt::UserRole, record);
    return r;
}
QString path(MarkdownEditor *editor)
{
    return static_cast<MarkdownDocument *>(editor->document())->filePath();
}
}

WritingWorkbench::WritingWorkbench(MarkdownEditor *editor,
                                   ProseAwarenessWidget *prose,
                                   StoryIntelligenceController *story,
                                   AgentEditTransactionManager *transactions,
                                   QObject *parent)
    : QObject(parent)
    , m_editor(editor)
    , m_prose(prose)
    , m_story(story)
    , m_transactions(transactions)
{
    m_analyticsPage = new QWidget(prose);
    m_analyticsPage->setObjectName("writingAnalyticsPage");
    m_dialoguePage = new QWidget(prose);
    m_dialoguePage->setObjectName("writingDialoguePage");
    auto *a = new QVBoxLayout(m_analyticsPage);
    auto *d = new QVBoxLayout(m_dialoguePage);
    for (auto *layout : {a, d}) {
        layout->setContentsMargins(12, 12, 12, 12);
        layout->setSpacing(8);
    }
    m_analyticsScope = createScope(m_analyticsPage);
    a->addWidget(m_analyticsScope);
    m_reportKind = new QComboBox(m_analyticsPage);
    m_reportKind->setObjectName("writingReportKind");
    m_reportKind->addItems({tr("Overview"),
                            tr("Sentence rhythm"),
                            tr("Repeated words"),
                            tr("Repeated phrases"),
                            tr("Chapter comparison"),
                            tr("Character voices"),
                            tr("Lens summary"),
                            tr("Revision history")});
    a->addWidget(m_reportKind);
    m_summary = label({}, m_analyticsPage);
    a->addWidget(m_summary);
    m_rhythmChart = new SentenceRhythmChart(m_analyticsPage);
    a->addWidget(m_rhythmChart);
    m_reports = tree(m_analyticsPage, {tr("Measure / passage"), tr("Value")});
    m_reports->setObjectName("writingAnalyticsResults");
    a->addWidget(m_reports, 1);
    a->addWidget(
        label(tr("Double-click a passage to find it. Sentence boundaries and readability are estimates; they describe structure, not writing quality."),
              m_analyticsPage));
    auto *actions = new QHBoxLayout;
    auto *snapshot = button(tr("Snapshot"), actions, m_analyticsPage);
    snapshot->setToolTip(tr("Save these measurements for comparison after revisions"));
    auto *exportButton = button(tr("Export…"), actions, m_analyticsPage);
    auto *refreshButton = button(tr("Refresh"), actions, m_analyticsPage);
    a->addLayout(actions);
    m_analyticsStatus = label({}, m_analyticsPage);
    a->addWidget(m_analyticsStatus);

    m_dialogueScope = createScope(m_dialoguePage);
    d->addWidget(m_dialogueScope);
    auto *speakerRow = new QHBoxLayout;
    m_speaker = new QComboBox(m_dialoguePage);
    m_speaker->setObjectName("dialogueSpeaker");
    m_speaker->setMinimumContentsLength(8);
    m_speaker->setSizeAdjustPolicy(QComboBox::AdjustToMinimumContentsLengthWithIcon);
    speakerRow->addWidget(m_speaker, 1);
    auto *aliases = button(tr("Aliases…"), speakerRow, m_dialoguePage);
    d->addLayout(speakerRow);
    m_saveCharacter = new QPushButton(tr("Save detected character…"), m_dialoguePage);
    m_saveCharacter->hide();
    d->addWidget(m_saveCharacter);
    connect(m_saveCharacter, &QPushButton::clicked, this, [this] {
        if (!current())
            return;
        const QString id = m_speaker->currentData().toString();
        QString name;
        for (const auto &v : m_analysis.dialogue)
            if (v.toObject().value("speaker_id").toString() == id) {
                name = v.toObject().value("speaker").toString();
                break;
            }
        if (!id.startsWith("detected:") || name.isEmpty())
            return;
        if (m_story->saveDetectedCharacter(m_workspace.value("id").toString(), name, id)) {
            m_timer.start(0);
        } else
            setStatus(tr("Could not save the character. Refresh and try again."));
    });
    m_search = new QLineEdit(m_dialoguePage);
    m_search->setPlaceholderText(tr("Find dialogue…"));
    m_search->setAccessibleName(tr("Search dialogue"));
    d->addWidget(m_search);
    m_voiceSummary = label({}, m_dialoguePage);
    d->addWidget(m_voiceSummary);
    m_lines = tree(m_dialoguePage, {tr("Dialogue / speaker"), tr("Words")});
    m_lines->setObjectName("dialogueResults");
    m_lines->setColumnCount(1);
    m_lines->setHeaderHidden(true);
    m_lines->setUniformRowHeights(false);
    m_lines->viewport()->installEventFilter(this);
    d->addWidget(m_lines, 1);
    auto *bulk = new QPushButton(tr("Find / replace in these lines…"), m_dialoguePage);
    d->addWidget(bulk);
    bulk->setToolTip(tr("Preview replacements in the filtered dialogue. Apply them together with Undo and a recovery checkpoint."));
    m_dialogueStatus = label({}, m_dialoguePage);
    d->addWidget(m_dialogueStatus);
    prose->addWorkspacePage(tr("Analytics"), m_analyticsPage);
    prose->addWorkspacePage(tr("Dialogue"), m_dialoguePage);
    m_analyticsPage->installEventFilter(this);
    m_dialoguePage->installEventFilter(this);
    m_timer.setSingleShot(true);
    m_timer.setInterval(500);
    connect(&m_timer, &QTimer::timeout, this, &WritingWorkbench::refresh);
    connect(editor, &QPlainTextEdit::textChanged, this, [this] {
        if (m_analyticsPage->isVisible() || m_dialoguePage->isVisible()) {
            setStatus(tr("Updating…"));
            m_timer.start();
        }
    });
    connect(editor, &QPlainTextEdit::cursorPositionChanged, this, [this] {
        const auto *combo = m_analyticsPage->isVisible() ? m_analyticsScope : m_dialogueScope;
        if ((m_analyticsPage->isVisible() || m_dialoguePage->isVisible()) && combo->currentData().toString().startsWith('*'))
            m_timer.start();
    });
    connect(&m_worker, &QFutureWatcher<WritingAnalysis>::finished, this, [this] {
        m_analysisPending = false;
        auto result = m_worker.result();
        if (m_documentPath != path(m_editor) || m_revision != m_editor->document()->revision() || result.text != m_editor->toPlainText()) {
            m_timer.start();
            return;
        }
        m_analysis = std::move(result);
        populateScopes();
        render();
        setStatus(tr("Up to date · local analysis"));
    });
    connect(m_reportKind, &QComboBox::currentIndexChanged, this, &WritingWorkbench::renderAnalytics);
    connect(m_analyticsScope, &QComboBox::currentIndexChanged, this, &WritingWorkbench::renderAnalytics);
    connect(m_dialogueScope, &QComboBox::currentIndexChanged, this, &WritingWorkbench::renderDialogue);
    connect(m_speaker, &QComboBox::currentIndexChanged, this, &WritingWorkbench::renderDialogue);
    connect(m_search, &QLineEdit::textChanged, this, &WritingWorkbench::renderDialogue);
    connect(m_reports, &QTreeWidget::itemActivated, this, [this](QTreeWidgetItem *item) {
        navigate(item->data(0, Qt::UserRole).toJsonObject());
    });
    connect(m_lines, &QTreeWidget::itemActivated, this, [this](QTreeWidgetItem *item) {
        navigate(item->data(0, Qt::UserRole).toJsonObject());
    });
    connect(aliases, &QPushButton::clicked, this, &WritingWorkbench::editAliases);
    connect(bulk, &QPushButton::clicked, this, &WritingWorkbench::bulkEditDialogue);
    connect(snapshot, &QPushButton::clicked, this, &WritingWorkbench::saveSnapshot);
    connect(exportButton, &QPushButton::clicked, this, &WritingWorkbench::exportReport);
    connect(refreshButton, &QPushButton::clicked, this, &WritingWorkbench::refresh);
}

QComboBox *WritingWorkbench::createScope(QWidget *page)
{
    auto *combo = new QComboBox(page);
    combo->setAccessibleName(tr("Analysis scope"));
    combo->setSizeAdjustPolicy(QComboBox::AdjustToMinimumContentsLengthWithIcon);
    combo->setMinimumContentsLength(8);
    combo->addItem(tr("Whole manuscript"), "manuscript");
    combo->addItem(tr("Current chapter"), "*chapter");
    combo->addItem(tr("Current scene"), "*scene");
    return combo;
}
bool WritingWorkbench::eventFilter(QObject *object, QEvent *event)
{
    if (object == m_lines->viewport() && event->type() == QEvent::Resize)
        resizeDialogueRows();
    if ((object == m_analyticsPage || object == m_dialoguePage) && event->type() == QEvent::Show)
        m_timer.start(0);
    return QObject::eventFilter(object, event);
}
void WritingWorkbench::setStatus(const QString &message)
{
    m_analyticsStatus->setText(message);
    m_dialogueStatus->setText(message);
}
void WritingWorkbench::setActionsEnabled(bool enabled)
{
    for (auto *page : {m_analyticsPage, m_dialoguePage})
        for (auto *button : page->findChildren<QPushButton *>()) button->setEnabled(enabled);
}
void WritingWorkbench::refresh()
{
    if (m_inlineEditor) {
        m_timer.stop();
        if (!current()) setStatus(tr("The manuscript changed. Your draft is kept here; copy it or cancel before refreshing."));
        return;
    }
    if (m_analysisPending) {
        m_timer.start();
        return;
    }
    m_timer.stop();
    setActionsEnabled(false);
    const auto workspace = m_story->reviewWorkspace();
    const QString text = m_editor->toPlainText();
    if (current() && m_workspace == workspace) {
        populateScopes();
        render();
        return;
    }
    m_workspace = workspace;
    m_revision = m_editor->document()->revision();
    m_documentPath = path(m_editor);
    setStatus(tr("Analyzing…"));
    m_analysisPending = true;
    m_worker.setFuture(QtConcurrent::run([text, workspace] {
        return WritingAnalysis::build(text, workspace);
    }));
}
bool WritingWorkbench::current() const
{
    return !m_analysisPending && !m_timer.isActive() && m_revision == m_editor->document()->revision() && m_documentPath == path(m_editor)
        && m_analysis.text == m_editor->toPlainText();
}
void WritingWorkbench::populateScopes()
{
    StoryWorkspace workspace;
    workspace.data = m_workspace;
    for (auto *combo : {m_analyticsScope, m_dialogueScope}) {
        const QSignalBlocker blocker(combo);
        const auto selected = combo->currentData();
        combo->clear();
        combo->addItem(tr("Whole manuscript"), "manuscript");
        for (const auto &mode : {QStringLiteral("chapter"), QStringLiteral("scene")}) {
            const QString id = workspace.scopeAt(m_editor->textCursor().position(), mode);
            const bool exists = workspace.scopeKind(id) == mode;
            combo->addItem((mode == "chapter" ? tr("Current chapter") : tr("Current scene")) + " · "
                               + (exists ? workspace.scope(id).value("title").toString() : tr("No heading here")),
                           QString("*" + mode));
        }
        for (const auto &v : m_workspace.value("scopes").toArray()) {
            const auto s = v.toObject();
            const QString id = s.value("id").toString();
            if (id == "manuscript" || s.value("orphaned").toBool())
                continue;
            combo->addItem((workspace.scopeKind(id) == "chapter" ? tr("Chapter: ") : tr("Scene: ")) + s.value("title").toString(), id);
        }
        const int index = combo->findData(selected);
        combo->setCurrentIndex(qMax(0, index));
        combo->setToolTip(combo->currentText());
    }
    const QSignalBlocker blocker(m_speaker);
    const auto selected = m_speaker->currentData();
    m_speaker->clear();
    m_speaker->addItem(tr("All characters"), "");
    m_speaker->addItem(tr("Unknown speaker"), "unknown");
    for (const auto &v : m_workspace.value("agents").toArray()) {
        const auto agent = v.toObject();
        if (agent.value("kind").toString() == "character" && !agent.value("archived").toBool())
            m_speaker->addItem(agent.value("name").toString(), agent.value("id").toString());
    }
    for (const auto &v : m_analysis.dialogue) {
        const auto line = v.toObject();
        const QString id = line.value("speaker_id").toString();
        if (id.startsWith("detected:") && m_speaker->findData(id) < 0)
            m_speaker->addItem(tr("Detected: %1").arg(line.value("speaker").toString()), id);
    }
    m_speaker->setCurrentIndex(qMax(0, m_speaker->findData(selected)));
}
QString WritingWorkbench::scopeId(QComboBox *combo) const
{
    const QString id = combo->currentData().toString();
    if (!id.startsWith('*'))
        return id;
    StoryWorkspace workspace;
    workspace.data = m_workspace;
    const QString actual = workspace.scopeAt(m_editor->textCursor().position(), id.mid(1));
    return workspace.scopeKind(actual) == id.mid(1) ? actual : QString();
}
QPair<int, int> WritingWorkbench::range(QComboBox *combo) const
{
    const QString id = scopeId(combo);
    if (id.isEmpty())
        return {0, 0};
    if (id == "manuscript")
        return {0, int(m_analysis.text.size())};
    for (const auto &v : m_workspace.value("scopes").toArray()) {
        const auto s = v.toObject();
        if (s.value("id").toString() == id)
            return {s.value("start").toInt(), qMin(int(m_analysis.text.size()), s.value("end").toInt())};
    }
    return {0, 0};
}
void WritingWorkbench::render()
{
    setActionsEnabled(current());
    renderAnalytics();
    renderDialogue();
}
void WritingWorkbench::renderAnalytics()
{
    if (!current())
        return;
    const auto bounds = range(m_analyticsScope);
    const auto report = m_analysis.report(bounds.first, bounds.second);
    m_summary->setText(tr("%1 words · %2% dialogue\n%3 words per sentence · variation %4")
                           .arg(report.value("words").toInt())
                           .arg(report.value("dialogue_percent").toDouble(), 0, 'f', 1)
                           .arg(report.value("mean_length").toDouble(), 0, 'f', 1)
                           .arg(report.value("length_variation").toDouble(), 0, 'f', 1));
    m_reports->clear();
    const int kind = m_reportKind->currentIndex();
    m_rhythmChart->setVisible(kind == 0 || kind == 1);
    static_cast<SentenceRhythmChart *>(m_rhythmChart)->setSentences(report.value("passages").toArray());
    if (kind == 0) {
        for (const auto &entry : QList<QPair<QString, QString>>{{tr("Words"), "words"},
                                                                {tr("Sentences (estimated)"), "sentences"},
                                                                {tr("Dialogue lines"), "dialogue_lines"},
                                                                {tr("Unknown speakers"), "unknown_lines"},
                                                                {tr("Explicit dialogue tags"), "tagged_lines"}})
            row(m_reports, entry.first, QString::number(report.value(entry.second).toInt()));
        row(m_reports, tr("Long words (%)"), QString::number(report.value("long_word_percent").toDouble(), 'f', 1));
        row(m_reports,
            tr("Coleman–Liau grade (English)"),
            report.value("words").toInt() >= 100 ? QString::number(report.value("coleman_liau").toDouble(), 'f', 1) : tr("Needs 100 words"));
    } else if (kind == 1) {
        for (const auto &v : report.value("passages").toArray()) {
            const auto s = v.toObject();
            const QString quote = m_analysis.text.mid(s.value("start").toInt(), s.value("end").toInt() - s.value("start").toInt());
            row(m_reports, quote.simplified(), QString::number(s.value("words").toInt()), s);
        }
    } else if (kind == 2 || kind == 3) {
        for (const auto &v : report.value(kind == 2 ? "frequency" : "phrases").toArray()) {
            auto r = v.toObject();
            const QString phrase = r.value("text").toString();
            const QString pattern = "(?<![\\p{L}\\p{N}])" + QRegularExpression::escape(phrase).replace(" ", "\\s+") + "(?![\\p{L}\\p{N}])";
            QRegularExpression rx(pattern, QRegularExpression::CaseInsensitiveOption);
            auto matches = rx.globalMatch(m_analysis.body.mid(bounds.first, bounds.second - bounds.first));
            auto *parent = row(m_reports, phrase, QString::number(r.value("count").toInt()));
            int count = 0;
            while (matches.hasNext() && count++ < 200) {
                const auto match = matches.next();
                const int start = bounds.first + match.capturedStart();
                auto *child = new QTreeWidgetItem(
                    parent,
                    {m_analysis.text.mid(qMax(bounds.first, start - 25), match.capturedLength() + 50).simplified(), QString::number(count)});
                child->setData(0, Qt::UserRole, QJsonObject{{"start", start}, {"end", start + match.capturedLength()}});
            }
        }
        m_reports->setRootIsDecorated(true);
    } else if (kind == 4) {
        StoryWorkspace workspace;
        workspace.data = m_workspace;
        for (const auto &v : m_workspace.value("scopes").toArray()) {
            const auto s = v.toObject();
            if (s.value("orphaned").toBool() || workspace.scopeKind(s.value("id").toString()) != "chapter")
                continue;
            const auto r = m_analysis.report(s.value("start").toInt(), s.value("end").toInt());
            auto *item = row(m_reports, s.value("title").toString(), QString::number(r.value("words").toInt()), s);
            item->setToolTip(0,
                             tr("%1% dialogue; %2 words per sentence")
                                 .arg(r.value("dialogue_percent").toDouble(), 0, 'f', 1)
                                 .arg(r.value("mean_length").toDouble(), 0, 'f', 1));
        }
    } else if (kind == 5) {
        StoryWorkspace workspace;
        workspace.data = m_workspace;
        for (int i = 1; i < m_speaker->count(); ++i) {
            const auto r = m_analysis.report(bounds.first, bounds.second, m_speaker->itemData(i).toString());
            auto *item = row(m_reports, m_speaker->itemText(i), QString::number(r.value("dialogue_words").toInt()));
            QStringList signatures;
            for (const auto &v : r.value("frequency").toArray()) {
                if (signatures.size() == 8)
                    break;
                signatures << v.toObject().value("text").toString();
            }
            item->setToolTip(0,
                             tr("%1 lines; %2 words per line\nRepeated words: %3")
                                 .arg(r.value("dialogue_lines").toInt())
                                 .arg(r.value("mean_length").toDouble(), 0, 'f', 1)
                                 .arg(signatures.join(", ")));
            new QTreeWidgetItem(item, {tr("Lines"), QString::number(r.value("dialogue_lines").toInt())});
            new QTreeWidgetItem(item, {tr("Average words / line"), QString::number(r.value("mean_length").toDouble(), 'f', 1)});
            const int dialogueWords = report.value("dialogue_words").toInt();
            new QTreeWidgetItem(
                item,
                {tr("Share of dialogue"), QString::number(dialogueWords ? 100.0 * r.value("dialogue_words").toInt() / dialogueWords : 0, 'f', 1) + "%"});
            auto *signatureRow = new QTreeWidgetItem(item, {tr("Repeated words"), signatures.join(", ")});
            signatureRow->setToolTip(1, signatures.join(", "));
            for (const auto &v : m_workspace.value("scopes").toArray()) {
                const auto scope = v.toObject();
                if (scope.value("orphaned").toBool() || workspace.scopeKind(scope.value("id").toString()) != "chapter")
                    continue;
                const int start = qMax(bounds.first, scope.value("start").toInt()), end = qMin(bounds.second, scope.value("end").toInt());
                if (end <= start)
                    continue;
                int chapterLines = 0, chapterWords = 0;
                for (const auto &value : r.value("dialogue").toArray()) {
                    const auto line = value.toObject();
                    if (line.value("start").toInt() >= start && line.value("end").toInt() <= end) {
                        ++chapterLines;
                        chapterWords += line.value("words").toInt();
                    }
                }
                if (!chapterLines)
                    continue;
                auto *entry = new QTreeWidgetItem(item, {scope.value("title").toString(), tr("%1 / line").arg(double(chapterWords) / chapterLines, 0, 'f', 1)});
                entry->setToolTip(0, tr("%1 lines · %2 dialogue words").arg(chapterLines).arg(chapterWords));
                entry->setData(0, Qt::UserRole, scope);
            }
        }
        m_reports->header()->setSectionResizeMode(1, QHeaderView::Interactive);
        m_reports->setColumnWidth(1, 85);
    } else if (kind == 6) {
        const auto counts = m_prose->categoryCountsSnapshot();
        row(m_reports, tr("Latest lens counts"), tr("Document"));
        for (auto it = counts.cbegin(); it != counts.cend(); ++it)
            row(m_reports, m_prose->categoryLabel(it.key()), QString::number(it.value()));
        m_summary->setText(
            tr("Lens counts come from the latest document scan. Use Tools → Scan document to refresh them; they are not scoped to this report."));
    } else {
        const auto history = m_workspace.value("writing_review").toObject().value("snapshots").toArray();
        int previous = -1;
        for (const auto &v : history) {
            const auto s = v.toObject();
            if (s.value("scope_id").toString() != scopeId(m_analyticsScope))
                continue;
            const auto r = s.value("report").toObject();
            const int words = r.value("words").toInt();
            auto *item = row(m_reports, s.value("created").toString(), QString::number(words));
            item->setToolTip(0,
                             tr("%1% dialogue · %2 words/sentence%3")
                                 .arg(r.value("dialogue_percent").toDouble(), 0, 'f', 1)
                                 .arg(r.value("mean_length").toDouble(), 0, 'f', 1)
                                 .arg(previous < 0 ? QString() : tr(" · word change %1").arg(words - previous)));
            previous = words;
        }
    }
    m_reports->setRootIsDecorated(kind == 2 || kind == 3 || kind == 5);
    if (kind != 5)
        m_reports->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
}
void WritingWorkbench::renderDialogue()
{
    if (!current() || m_inlineEditor)
        return;
    m_saveCharacter->setVisible(m_speaker->currentData().toString().startsWith("detected:"));
    const QString previous = m_lines->currentItem() ? m_lines->currentItem()->data(0, Qt::UserRole).toJsonObject().value("anchor").toString() : QString();
    m_lines->clear();
    const auto bounds = range(m_dialogueScope);
    const auto report = m_analysis.report(bounds.first, bounds.second, m_speaker->currentData().toString());
    for (const auto &v : report.value("dialogue").toArray()) {
        const auto line = v.toObject();
        if (!line.value("content").toString().contains(m_search->text(), Qt::CaseInsensitive))
            continue;
        auto *item = new QTreeWidgetItem(m_lines);
        item->setData(0, Qt::UserRole, line);
        auto *card = new QWidget(m_lines);
        auto *layout = new QVBoxLayout(card);
        layout->setContentsMargins(8, 8, 8, 10);
        layout->setSpacing(5);
        auto *actions = new QHBoxLayout;
        auto *speaker = new QPushButton(line.value("speaker").toString(), card);
        speaker->setFlat(true);
        speaker->setFixedHeight(28);
        speaker->setMinimumWidth(0);
        speaker->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Fixed);
        speaker->setToolTip(tr("Assign speaker · %1").arg(line.value("confidence").toString()));
        speaker->setObjectName("dialogueAssign");
        actions->addWidget(speaker, 1);
        connect(speaker, &QPushButton::clicked, this, [this, item] { m_lines->setCurrentItem(item); assignSpeaker(); });
        for (const auto &action : {QStringLiteral("edit"), QStringLiteral("find")}) {
            auto *b = new QToolButton(card);
            b->setAutoRaise(true);
            b->setFixedSize(28, 28);
            b->setIconSize(QSize(16, 16));
            b->setIcon(QIcon(action == "edit" ? ":/icons/shell-edit.svg" : ":/icons/find.svg"));
            b->setObjectName(action == "edit" ? "dialogueEdit" : "dialogueLocate");
            b->setToolTip(action == "edit" ? tr("Edit this dialogue") : tr("Highlight this quote in the manuscript"));
            b->setAccessibleName(b->toolTip());
            actions->addWidget(b);
            connect(b, &QToolButton::clicked, this, [this, item, action] {
                if (m_inlineEditor) return;
                m_lines->setCurrentItem(item);
                if (action == "edit") editDialogue();
                else navigate(item->data(0, Qt::UserRole).toJsonObject());
            });
        }
        layout->addLayout(actions);
        auto *words = label(line.value("content").toString(), card);
        words->setObjectName("dialogueWords");
        words->setTextInteractionFlags(Qt::TextSelectableByMouse);
        words->setMinimumWidth(0);
        layout->addWidget(words);
        card->setToolTip(line.value("confidence").toString() + "\n" + line.value("chapter").toString());
        m_lines->setItemWidget(item, 0, card);
        if (line.value("anchor").toString() == previous)
            m_lines->setCurrentItem(item);
    }
    m_voiceSummary->setText(tr("%1 lines shown · %2 dialogue words\n%3 unknown speakers in this scope")
                                .arg(m_lines->topLevelItemCount())
                                .arg(report.value("dialogue_words").toInt())
                                .arg(report.value("unknown_lines").toInt()));
    if (!m_lines->currentItem() && m_lines->topLevelItemCount())
        m_lines->setCurrentItem(m_lines->topLevelItem(0));
    resizeDialogueRows();
    if (m_speaker->count() == 2)
        m_dialogueStatus->setText(tr("Add characters in Story Intelligence, then enter their aliases here. Untagged dialogue stays Unknown."));
}
void WritingWorkbench::resizeDialogueRows()
{
    const int width = qMax(80, m_lines->viewport()->width() - 4);
    for (int i = 0; i < m_lines->topLevelItemCount(); ++i) {
        auto *item = m_lines->topLevelItem(i);
        auto *card = m_lines->itemWidget(item, 0);
        if (!card) continue;
        card->setFixedWidth(width);
        if (auto *words = card->findChild<QLabel *>("dialogueWords")) {
            words->setFixedHeight(words->heightForWidth(width - 16));
        }
        card->layout()->invalidate();
        const int height = card->layout()->totalHeightForWidth(width);
        item->setSizeHint(0, QSize(width, qMax(card->minimumSizeHint().height(), height)));
    }
}
void WritingWorkbench::navigate(const QJsonObject &record)
{
    if (!current()) {
        setStatus(tr("The manuscript changed. Refresh before navigating."));
        m_timer.start(0);
        return;
    }
    const int start = record.value("start").toInt(-1), end = record.value("end").toInt(-1);
    if (start < 0 || end <= start)
        return;
    QTextCursor cursor(m_editor->document());
    cursor.setPosition(start);
    cursor.setPosition(qMin(end, int(m_analysis.text.size())), QTextCursor::KeepAnchor);
    m_editor->setTextCursor(cursor);
    m_editor->ensureCursorVisible();
    m_editor->setFocus();
}
bool WritingWorkbench::saveReview(const QJsonObject &review)
{
    if (!current() || !m_story->saveWritingReview(m_workspace.value("id").toString(), review)) {
        setStatus(tr("Could not save. The manuscript or workspace changed; refresh and try again."));
        return false;
    }
    m_timer.start(0);
    return true;
}
void WritingWorkbench::assignSpeaker()
{
    if (!current() || m_inlineEditor || !m_lines->currentItem())
        return;
    const auto line = m_lines->currentItem()->data(0, Qt::UserRole).toJsonObject();
    if (!line.value("unique_anchor").toBool()) {
        setStatus(tr("This passage repeats with identical surrounding text. Give it distinct context before saving a speaker assignment."));
        return;
    }
    const int revision = m_revision;
    const QString workspaceId = m_workspace.value("id").toString();
    QStringList choices;
    for (int i = 1; i < m_speaker->count(); ++i)
        choices << m_speaker->itemText(i);
    bool ok = false;
    const auto choice = QInputDialog::getItem(m_dialoguePage, tr("Assign speaker"), tr("Who speaks this line?"), choices, 0, false, &ok);
    if (!ok || !current() || revision != m_revision || workspaceId != m_workspace.value("id").toString())
        return;
    auto review = m_workspace.value("writing_review").toObject(), assignments = review.value("assignments").toObject();
    assignments.insert(line.value("anchor").toString(), m_speaker->itemData(choices.indexOf(choice) + 1).toString());
    review.insert("assignments", assignments);
    saveReview(review);
}
void WritingWorkbench::editAliases()
{
    if (!current())
        return;
    const QString id = m_speaker->currentData().toString();
    const QString workspaceId = m_workspace.value("id").toString();
    const int revision = m_revision;
    if (id.isEmpty() || id == "unknown") {
        setStatus(tr("Select a saved character first, then edit their aliases."));
        return;
    }
    if (id.startsWith("detected:")) {
        setStatus(tr("Save this detected character first, then add their aliases."));
        return;
    }
    auto review = m_workspace.value("writing_review").toObject(), aliases = review.value("aliases").toObject();
    QStringList names;
    for (const auto &v : aliases.value(id).toArray())
        names << v.toString();
    bool ok = false;
    const auto value = QInputDialog::getMultiLineText(m_dialoguePage,
                                                      tr("Character aliases"),
                                                      tr("One name or nickname per line. Include short names used in dialogue tags."),
                                                      names.join('\n'),
                                                      &ok);
    if (!ok || !current() || revision != m_revision || workspaceId != m_workspace.value("id").toString())
        return;
    QJsonArray result;
    for (const auto &name : value.split('\n'))
        if (!name.trimmed().isEmpty())
            result.append(name.trimmed());
    aliases.insert(id, result);
    review.insert("aliases", aliases);
    saveReview(review);
}
void WritingWorkbench::editDialogue()
{
    if (!current() || m_inlineEditor || !m_lines->currentItem())
        return;
    m_editLine = m_lines->currentItem()->data(0, Qt::UserRole).toJsonObject();
    auto *card = m_lines->itemWidget(m_lines->currentItem(), 0);
    card->findChild<QLabel *>("dialogueWords")->hide();
    m_inlineEditor = new QPlainTextEdit(m_editLine.value("content").toString(), card);
    m_inlineEditor->setObjectName("dialogueInlineEditor");
    m_inlineEditor->setAccessibleName(tr("Edit spoken words"));
    m_inlineEditor->setFixedHeight(140);
    card->layout()->addWidget(m_inlineEditor);
    auto *actions = new QWidget(card);
    auto *buttons = new QHBoxLayout(actions);
    auto *save = button(tr("Save"), buttons, actions);
    auto *cancel = button(tr("Cancel"), buttons, actions);
    card->layout()->addWidget(actions);
    for (QWidget *control : QList<QWidget *>{m_search, m_speaker, m_dialogueScope}) control->setEnabled(false);
    connect(save, &QPushButton::clicked, this, [this] { finishDialogueEdit(true); });
    connect(cancel, &QPushButton::clicked, this, [this] { finishDialogueEdit(false); });
    resizeDialogueRows();
    m_inlineEditor->setFocus();
}
void WritingWorkbench::finishDialogueEdit(bool save)
{
    if (!m_inlineEditor) return;
    const auto line = m_editLine;
    const auto replacement = m_inlineEditor->toPlainText();
    if (save && !current()) {
        setStatus(tr("The manuscript changed. Your draft is preserved; copy it or cancel before refreshing."));
        return;
    }
    if (!save || replacement == line.value("content").toString()) {
        m_inlineEditor.clear();
        for (QWidget *control : QList<QWidget *>{m_search, m_speaker, m_dialogueScope}) control->setEnabled(true);
        refresh();
        return;
    }
    const int start = line.value("start").toInt() + 1, end = line.value("end").toInt() - 1;
    if (start == end)
        return;
    const QJsonArray changes{QJsonObject{{"start_utf16", start}, {"end_utf16", end}, {"expected", line.value("content")}, {"replacement", replacement}}};
    const auto result = m_transactions->applyVerifiedReplacements(changes, tr("Edit character dialogue"), "dialogue.edit");
    setStatus(result.value("ok").toBool() ? tr("Dialogue updated. Undo is available in the editor.")
                                          : result.value("error").toString(tr("Dialogue edit failed.")));
    if (result.value("ok").toBool()) {
        m_inlineEditor.clear();
        for (QWidget *control : QList<QWidget *>{m_search, m_speaker, m_dialogueScope}) control->setEnabled(true);
        retainSpeakerAssignments(changes);
        m_timer.start(0);
    }
}
void WritingWorkbench::retainSpeakerAssignments(const QJsonArray &changes)
{
    auto review = m_workspace.value("writing_review").toObject();
    const auto original = review.value("assignments").toObject();
    auto assignments = original;
    const QString text = m_editor->toPlainText();
    for (const auto &v : m_analysis.dialogue) {
        const auto line = v.toObject();
        const auto assigned = original.value(line.value("anchor").toString());
        if (!assigned.isString() || !line.value("unique_anchor").toBool())
            continue;
        const int oldStart = line.value("start").toInt(), oldEnd = line.value("end").toInt();
        int start = oldStart, end = oldEnd;
        for (const auto &change : changes) {
            const auto c = change.toObject();
            const int delta = c.value("replacement").toString().size() - c.value("expected").toString().size();
            if (c.value("end_utf16").toInt() <= oldStart) {
                start += delta;
                end += delta;
            } else if (c.value("start_utf16").toInt() >= oldStart && c.value("end_utf16").toInt() <= oldEnd)
                end += delta;
        }
        // Retain the original anchor as well, so normal Undo restores its assignment.
        assignments.insert(WritingAnalysis::anchor(text, start, end), assigned);
    }
    if (assignments == original)
        return;
    review.insert("assignments", assignments);
    if (!m_story->saveWritingReview(m_workspace.value("id").toString(), review))
        QMessageBox::warning(m_dialoguePage,
                             tr("Speaker assignments"),
                             tr("The dialogue was edited, but its updated speaker assignments could not be saved. The manuscript edit can still be undone."));
}
void WritingWorkbench::bulkEditDialogue()
{
    if (!current() || m_inlineEditor || !m_lines->topLevelItemCount())
        return;
    const int revision = m_revision;
    const QString document = m_documentPath;
    QDialog dialog(m_dialoguePage);
    dialog.setWindowTitle(tr("Replace in filtered dialogue"));
    auto *layout = new QVBoxLayout(&dialog);
    auto *form = new QFormLayout;
    auto *find = new QLineEdit(&dialog), *replace = new QLineEdit(&dialog);
    form->addRow(tr("Exact text (case sensitive)"), find);
    form->addRow(tr("Replace with"), replace);
    layout->addLayout(form);
    auto *preview = new QPlainTextEdit(&dialog);
    preview->setReadOnly(true);
    layout->addWidget(preview);
    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Apply | QDialogButtonBox::Cancel, &dialog);
    layout->addWidget(buttons);
    QJsonArray changes;
    const auto update = [&] {
        changes = {};
        QStringList lines;
        if (!find->text().isEmpty())
            for (int i = 0; i < m_lines->topLevelItemCount(); ++i) {
                const auto line = m_lines->topLevelItem(i)->data(0, Qt::UserRole).toJsonObject();
                const QString original = line.value("content").toString();
                QString changed = original;
                changed.replace(find->text(), replace->text());
                if (original == changed)
                    continue;
                changes.append(QJsonObject{{"start_utf16", line.value("start").toInt() + 1},
                                           {"end_utf16", line.value("end").toInt() - 1},
                                           {"expected", original},
                                           {"replacement", changed}});
                lines << line.value("speaker").toString() + "\n− " + original + "\n+ " + changed;
            }
        preview->setPlainText(tr("%1 lines will change\n\n").arg(changes.size()) + lines.join("\n\n"));
        buttons->button(QDialogButtonBox::Apply)->setEnabled(!changes.isEmpty());
    };
    connect(find, &QLineEdit::textChanged, &dialog, update);
    connect(replace, &QLineEdit::textChanged, &dialog, update);
    connect(buttons->button(QDialogButtonBox::Apply), &QPushButton::clicked, &dialog, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    update();
    dialog.resize(620, 460);
    if (dialog.exec() != QDialog::Accepted)
        return;
    if (!current() || revision != m_revision || document != m_documentPath) {
        setStatus(tr("The manuscript changed while previewing. Refresh and try again."));
        return;
    }
    const auto result = m_transactions->applyVerifiedReplacements(changes, tr("Replace text in filtered dialogue"), "dialogue.replace");
    setStatus(result.value("ok").toBool() ? tr("Dialogue updated as one Undo step.") : result.value("error").toString(tr("Dialogue replacement failed.")));
    if (result.value("ok").toBool()) {
        retainSpeakerAssignments(changes);
        m_timer.start(0);
    }
}
void WritingWorkbench::saveSnapshot()
{
    if (!current() || scopeId(m_analyticsScope).isEmpty())
        return;
    const auto bounds = range(m_analyticsScope);
    auto report = m_analysis.report(bounds.first, bounds.second);
    for (const auto *key : {"dialogue", "passages", "frequency", "phrases", "voices"})
        report.remove(key);
    auto review = m_workspace.value("writing_review").toObject();
    auto snapshots = review.value("snapshots").toArray();
    snapshots.append(
        QJsonObject{{"created", QDateTime::currentDateTimeUtc().toString(Qt::ISODate)}, {"scope_id", scopeId(m_analyticsScope)}, {"report", report}});
    if (snapshots.size() > 200)
        snapshots.removeFirst();
    review.insert("snapshots", snapshots);
    if (saveReview(review)) {
        m_reportKind->setCurrentIndex(7);
        setStatus(tr("Snapshot saved."));
    }
}
void WritingWorkbench::exportReport()
{
    if (!current())
        return;
    const auto bounds = range(m_analyticsScope);
    const auto report = m_analysis.report(bounds.first, bounds.second);
    const QString filename = QFileDialog::getSaveFileName(m_analyticsPage, tr("Export writing analysis"), "writing-analysis.json", tr("JSON report (*.json)"));
    if (filename.isEmpty())
        return;
    QSaveFile file(filename);
    const QByteArray bytes = QJsonDocument(QJsonObject{{"version", 1}, {"scope", m_analyticsScope->currentText()}, {"report", report}}).toJson();
    if (!file.open(QIODevice::WriteOnly) || file.write(bytes) != bytes.size() || !file.commit()) {
        setStatus(tr("Export failed: %1").arg(file.errorString()));
        return;
    }
    setStatus(tr("Report exported."));
}
}
