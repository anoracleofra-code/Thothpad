/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "welcomedialog.h"

#include <QCheckBox>
#include <QFont>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QIcon>
#include <QLabel>
#include <QPixmap>
#include <QPushButton>
#include <QStackedWidget>
#include <QStyle>
#include <QTabWidget>
#include <QVBoxLayout>

namespace ghostwriter
{
namespace
{
QLabel *makeBodyLabel(const QString &text, QWidget *parent)
{
    auto *label = new QLabel(text, parent);
    label->setWordWrap(true);
    label->setTextFormat(Qt::RichText);
    label->setTextInteractionFlags(Qt::TextSelectableByMouse);
    label->setAlignment(Qt::AlignLeft | Qt::AlignTop);
    label->setMargin(12);
    return label;
}

QWidget *makeTourPage(const QString &title, const QString &body, QWidget *parent)
{
    auto *page = new QWidget(parent);
    auto *layout = new QVBoxLayout(page);
    auto *heading = new QLabel(title, page);
    QFont headingFont = heading->font();
    headingFont.setPointSize(16);
    headingFont.setBold(true);
    heading->setFont(headingFont);
    layout->addWidget(heading);
    layout->addWidget(makeBodyLabel(body, page), 1);
    return page;
}
} // namespace

WelcomeDialog::WelcomeDialog(const QString &version, QWidget *parent)
    : QDialog(parent)
    , pages(new QStackedWidget(this))
    , backButton(new QPushButton(tr("Back"), this))
    , showAfterUpdatesCheckBox(new QCheckBox(tr("Show this welcome after updates"), this))
{
    setWindowTitle(tr("Welcome to ThothPad"));
    setAttribute(Qt::WA_DeleteOnClose);
    setMinimumSize(660, 570);
    resize(720, 620);

    auto *welcomePage = new QWidget(pages);
    auto *welcomeLayout = new QVBoxLayout(welcomePage);
    welcomeLayout->setContentsMargins(54, 28, 54, 24);
    welcomeLayout->setSpacing(10);

    auto *logo = new QLabel(welcomePage);
    logo->setObjectName(QStringLiteral("welcomeLogo"));
    logo->setPixmap(QIcon(QStringLiteral(":/resources/icons/128-apps-thothpad.png")).pixmap(QSize(104, 104)));
    logo->setAlignment(Qt::AlignCenter);
    welcomeLayout->addWidget(logo);

    auto *title = new QLabel(tr("ThothPad"), welcomePage);
    QFont titleFont = title->font();
    titleFont.setPointSize(25);
    titleFont.setBold(true);
    title->setFont(titleFont);
    title->setAlignment(Qt::AlignCenter);
    welcomeLayout->addWidget(title);

    auto *tagline = new QLabel(tr("See what your prose is doing."), welcomePage);
    QFont taglineFont = tagline->font();
    taglineFont.setPointSize(12);
    tagline->setFont(taglineFont);
    tagline->setAlignment(Qt::AlignCenter);
    welcomeLayout->addWidget(tagline);
    welcomeLayout->addSpacing(18);

    auto *actions = new QGridLayout();
    actions->setHorizontalSpacing(12);
    actions->setVerticalSpacing(8);

    auto addAction = [actions, welcomePage](int row, const QString &text, QStyle::StandardPixmap icon, const QString &objectName) {
        auto *button = new QPushButton(welcomePage->style()->standardIcon(icon), text, welcomePage);
        button->setObjectName(objectName);
        button->setMinimumHeight(40);
        button->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
        actions->addWidget(button, row / 2, row % 2);
        return button;
    };

    auto *newButton = addAction(0, tr("New document"), QStyle::SP_FileIcon, QStringLiteral("welcomeNewButton"));
    auto *openButton = addAction(1, tr("Open document"), QStyle::SP_DialogOpenButton, QStringLiteral("welcomeOpenButton"));
    auto *proseButton = addAction(2, tr("Prose Awareness"), QStyle::SP_MessageBoxInformation, QStringLiteral("welcomeProseButton"));
    auto *tourButton = addAction(3, tr("Quick tour"), QStyle::SP_ComputerIcon, QStringLiteral("welcomeTourButton"));
    auto *whatsNewButton = addAction(4, tr("What's new in %1").arg(version), QStyle::SP_BrowserReload, QStringLiteral("welcomeWhatsNewButton"));
    actions->addWidget(whatsNewButton, 2, 0, 1, 2);
    welcomeLayout->addLayout(actions);
    welcomeLayout->addStretch();

    auto *tourPage = new QWidget(pages);
    auto *tourLayout = new QVBoxLayout(tourPage);
    tourLayout->setContentsMargins(24, 20, 24, 20);
    auto *tourHeading = new QLabel(tr("Quick tour"), tourPage);
    QFont sectionFont = tourHeading->font();
    sectionFont.setPointSize(20);
    sectionFont.setBold(true);
    tourHeading->setFont(sectionFont);
    tourLayout->addWidget(tourHeading);

    auto *tourTabs = new QTabWidget(tourPage);
    tourTabs->setObjectName(QStringLiteral("welcomeTourTabs"));
    tourTabs->addTab(makeTourPage(tr("Write"),
                                  tr("<p>ThothPad opens as a focused, single-pane Markdown editor. "
                                     "Use the left sidebar for your folder and outline. Live Preview "
                                     "is optional and stays off until you turn it on.</p>"),
                                  tourTabs),
                     tr("Write"));
    tourTabs->addTab(makeTourPage(tr("Notice"),
                                  tr("<p>Open <b>Prose Awareness</b> to enable color-coded lenses for "
                                     "adverbs, filter words, cliches, repetition, formulaic patterns, "
                                     "and other habits. Every finding is advisory and every lens can "
                                     "be disabled, recolored, or tuned.</p>"),
                                  tourTabs),
                     tr("Notice"));
    tourTabs->addTab(makeTourPage(tr("Review"),
                                  tr("<p>Run a review on a selection, document, or manuscript folder. "
                                     "Use <b>F8</b> and <b>Shift+F8</b> to move through findings. "
                                     "Suggested revisions open in a before-and-after review and are "
                                     "never applied without your approval.</p>"),
                                  tourTabs),
                     tr("Review"));
    tourTabs->addTab(makeTourPage(tr("AI and privacy"),
                                  tr("<p>Typing lenses and deterministic reports run locally and need "
                                     "no API key. AI operations only run when you request them. Before "
                                     "remote prose is sent, ThothPad shows the provider and exact scope.</p>"),
                                  tourTabs),
                     tr("AI and privacy"));
    tourLayout->addWidget(tourTabs, 1);

    auto *whatsNewPage = new QWidget(pages);
    auto *whatsNewLayout = new QVBoxLayout(whatsNewPage);
    whatsNewLayout->setContentsMargins(36, 24, 36, 24);
    auto *whatsNewHeading = new QLabel(tr("What's new in ThothPad %1").arg(version), whatsNewPage);
    whatsNewHeading->setFont(sectionFont);
    whatsNewLayout->addWidget(whatsNewHeading);
    whatsNewLayout->addWidget(makeBodyLabel(tr("<p><b>A prose-aware writing studio.</b></p>"
                                               "<p>This release adds optional live prose lenses, document and manuscript reports, "
                                               "custom profiles, grammar and copyediting checks, and explicit AI-assisted rewrite review.</p>"
                                               "<p>The editor now starts in a focused single-pane layout with the Kanagawa Lotus "
                                               "writing palette. Live Preview remains available from the View menu when you need it.</p>"
                                               "<p>Use the Prose Awareness sidebar to decide which observations matter to your work. "
                                               "ThothPad does not label subjective style choices as errors.</p>"),
                                            whatsNewPage),
                              1);

    pages->setObjectName(QStringLiteral("welcomePages"));
    pages->addWidget(welcomePage);
    pages->addWidget(tourPage);
    pages->addWidget(whatsNewPage);

    backButton->setObjectName(QStringLiteral("welcomeBackButton"));
    backButton->setVisible(false);
    showAfterUpdatesCheckBox->setObjectName(QStringLiteral("welcomeUpdateCheckbox"));
    showAfterUpdatesCheckBox->setChecked(true);
    auto *closeButton = new QPushButton(tr("Close"), this);
    closeButton->setDefault(true);

    auto *footer = new QHBoxLayout();
    footer->addWidget(backButton);
    footer->addWidget(showAfterUpdatesCheckBox);
    footer->addStretch();
    footer->addWidget(closeButton);

    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(12, 12, 12, 12);
    layout->addWidget(pages, 1);
    layout->addLayout(footer);

    connect(newButton, &QPushButton::clicked, this, [this]() {
        emit newDocumentRequested();
        accept();
    });
    connect(openButton, &QPushButton::clicked, this, [this]() {
        emit openDocumentRequested();
        accept();
    });
    connect(proseButton, &QPushButton::clicked, this, [this]() {
        emit proseAwarenessRequested();
        accept();
    });
    connect(tourButton, &QPushButton::clicked, this, &WelcomeDialog::showTourPage);
    connect(whatsNewButton, &QPushButton::clicked, this, &WelcomeDialog::showWhatsNewPage);
    connect(backButton, &QPushButton::clicked, this, &WelcomeDialog::showWelcomePage);
    connect(closeButton, &QPushButton::clicked, this, &QDialog::accept);
}

bool WelcomeDialog::showAfterUpdates() const
{
    return showAfterUpdatesCheckBox->isChecked();
}

void WelcomeDialog::setShowAfterUpdates(bool enabled)
{
    showAfterUpdatesCheckBox->setChecked(enabled);
}

void WelcomeDialog::showWelcomePage()
{
    pages->setCurrentIndex(0);
    backButton->setVisible(false);
}

void WelcomeDialog::showTourPage()
{
    pages->setCurrentIndex(1);
    backButton->setVisible(true);
}

void WelcomeDialog::showWhatsNewPage()
{
    pages->setCurrentIndex(2);
    backButton->setVisible(true);
}
} // namespace ghostwriter
