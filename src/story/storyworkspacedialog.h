// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <QJsonObject>
class QWidget;
namespace ghostwriter
{
bool editStoryRecord(QJsonObject &record, const QString &type, QWidget *parent);
bool editStoryWorkspace(QJsonObject &data, const QString &scopeId, int tab, QWidget *parent);
}
