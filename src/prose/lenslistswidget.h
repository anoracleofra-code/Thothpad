/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef THOTHPAD_LENS_LISTS_WIDGET_H
#define THOTHPAD_LENS_LISTS_WIDGET_H
#include <QWidget>
#include <QHash>
#include <QJsonObject>
class QCheckBox;
class QComboBox;
class QLineEdit;
class QPlainTextEdit;
namespace ghostwriter {
class LensListsWidget : public QWidget
{
    Q_OBJECT
public:
    explicit LensListsWidget(const QJsonObject &lists, QWidget *parent = nullptr);
    QJsonObject lists() const;
    void selectLens(const QString &lens);
    QJsonObject selectedListDocument() const;
    bool loadListDocument(const QJsonObject &document, QString *error);
    static bool validateLists(const QJsonValue &lists, QString *error);
private:
    struct Editors {
        QLineEdit *name;
        QPlainTextEdit *include;
        QPlainTextEdit *exclude;
        QCheckBox *builtin;
        QCheckBox *dialogue;
    };
    QJsonObject values(const QString &lens) const;
    void setValues(const QString &lens, const QJsonObject &values);
    QComboBox *m_lens;
    QHash<QString, Editors> m_editors;
    QHash<QString, QJsonObject> m_initial;
    QJsonObject m_original;
};
}
#endif
