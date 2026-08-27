/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef STORY_INTELLIGENCE_WIDGET_H
#define STORY_INTELLIGENCE_WIDGET_H

#include <QJsonArray>
#include <QJsonObject>
#include <QPointer>
#include <QString>
#include <QWidget>

class QLabel;
class QPlainTextEdit;
class QPushButton;
class QScrollArea;
class QToolButton;
class QVBoxLayout;

namespace ghostwriter
{
class StoryIntelligenceWidget : public QWidget
{
    Q_OBJECT

public:
    explicit StoryIntelligenceWidget(QWidget *parent = nullptr);

    void setCollapseIcon(const QIcon &icon);
    void setProviderSummary(const QString &provider, const QString &model, bool credentialConfigured);
    void setProjectFolder(const QString &path);
    void setSceneContext(const QJsonObject &context);
    void setCharacters(const QJsonArray &characters);
    void setActiveCharacter(const QString &characterId);
    void appendChatMessage(const QString &role, const QString &text, const QString &speaker = QString());
    void clearChat();
    void setBusy(bool busy);
    void setStatusMessage(const QString &message);
    QString activeCharacterId() const;

signals:
    void collapseRequested();
    void modelSettingsRequested();
    void projectFolderRequested();
    void editSceneRequested();
    void addCharacterRequested();
    void editCharactersRequested();
    void characterActivated(const QString &characterId);
    void chatRequested(const QString &message);
    void clearAnnotationsRequested();

private:
    QFrame *makeCard(const QString &objectName);
    QLabel *makeSectionTitle(const QString &text);
    void rebuildCharacters();
    void submitChat();
    QString characterId(const QJsonObject &character) const;

    QToolButton *m_collapseButton;
    QPushButton *m_modelSettingsButton;
    QLabel *m_providerLabel;
    QLabel *m_modelLabel;
    QLabel *m_keyStateLabel;
    QLabel *m_projectPathLabel;
    QLabel *m_settingLabel;
    QLabel *m_goalLabel;
    QLabel *m_contextDetailLabel;
    QWidget *m_charactersContainer;
    QVBoxLayout *m_charactersLayout;
    QWidget *m_chatContainer;
    QVBoxLayout *m_chatLayout;
    QScrollArea *m_chatScrollArea;
    QPlainTextEdit *m_chatInput;
    QPushButton *m_sendButton;
    QLabel *m_statusLabel;
    QJsonArray m_characters;
    QString m_activeCharacterId;
};
}

#endif
