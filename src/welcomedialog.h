/*
 * SPDX-FileCopyrightText: 2026 ThothPad contributors
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef WELCOME_DIALOG_H
#define WELCOME_DIALOG_H

#include <QDialog>

class QCheckBox;
class QPushButton;
class QStackedWidget;

namespace ghostwriter
{
class WelcomeDialog : public QDialog
{
    Q_OBJECT

public:
    explicit WelcomeDialog(const QString &version, QWidget *parent = nullptr);

    bool showAfterUpdates() const;
    void setShowAfterUpdates(bool enabled);

signals:
    void newDocumentRequested();
    void openDocumentRequested();
    void proseAwarenessRequested();

private slots:
    void showWelcomePage();
    void showTourPage();
    void showWhatsNewPage();

private:
    QStackedWidget *pages;
    QPushButton *backButton;
    QCheckBox *showAfterUpdatesCheckBox;
};
} // namespace ghostwriter

#endif
