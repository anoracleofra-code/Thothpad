// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <QJsonArray>
#include <QJsonObject>
#include <QString>
#include <QStringList>

namespace ghostwriter
{
// Portable manuscript data; credentials and executable configuration never belong here.
class StoryWorkspace
{
public:
    bool open(const QString &path, const QJsonObject &legacy, QString *error);
    bool save(QString *error);
    QJsonObject data;
    QString path() const
    {
        return m_path;
    }
    bool writable() const
    {
        return m_writable;
    }
    static QString newId();
    static QJsonObject defaults();
    static QJsonObject agentTemplate(const QString &kind);
    static QJsonObject importAgent(const QByteArray &bytes, const QString &suffix, QString *error);
    static QByteArray exportAgent(const QJsonObject &agent, const QJsonArray &memories, const QString &level);
    static QJsonObject cleanAgent(const QJsonObject &source);
    void reconcileHeadings(const QJsonArray &headings);
    QJsonObject scope(const QString &id) const;
    void setScope(const QJsonObject &record);
    QString scopeAt(int position, const QString &mode) const;
    QString scopeKind(const QString &scopeId) const;
    QStringList ancestry(const QString &scopeId) const;
    QJsonObject effectiveContext(const QString &scopeId) const;
    QJsonObject effectiveSettings(const QString &scopeId) const;
    QJsonArray scopedMemories(const QString &scopeId, const QStringList &agentIds) const;
    QJsonObject agent(const QString &id) const;
    void putAgent(const QJsonObject &agent);

private:
    QString m_path;
    QByteArray m_diskHash;
    bool m_writable = false;
};
}
