/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef STORY_LAB_DIALOG_H
#define STORY_LAB_DIALOG_H

#include <QDialog>
#include <QJsonArray>
#include <QJsonObject>
#include <QString>

class QComboBox;
class QLabel;
class QLineEdit;
class QPlainTextEdit;
class QPushButton;
class QTableWidget;

namespace ghostwriter
{
class WriterEngineClient;

/**
 * Advanced Story Engine workspace.
 *
 * The right rail stays deliberately light; state-heavy diagnostics live here
 * and are invoked only when the writer asks for them. All model-facing tools
 * are read-only. The only mutations in this dialog are explicit writer actions
 * for Story Lens definitions and Writer Model preference review.
 */
class StoryLabDialog : public QDialog
{
public:
    explicit StoryLabDialog(WriterEngineClient *engine, QWidget *parent = nullptr);
    ~StoryLabDialog() override;

    void setStoryContext(const QString &projectRoot,
                         const QString &activeStoryUnit,
                         const QString &activeBranch,
                         const QString &sourcePath,
                         const QString &scopeTitle,
                         const QString &activeCharacter);

private:
    void resolveStoryPosition();
    void setPositionSensitiveEnabled(bool enabled);
    void requestTool(const QString &toolId, QJsonObject arguments = {});
    void requestWriterMutation(const QString &mutation, const QJsonObject &payload);
    void handleResponse(const QString &requestId, const QJsonObject &response);
    void runReaderTool();
    void runCouncil();
    void refreshEntities();
    void addEntityAlias();
    void runGrounding(bool conflicts);
    void refreshLenses();
    void createLens();
    void runLens();
    void runExperience(bool timeline);
    void runAdvancedTool();
    void rebuildIndex();
    void exportProjectMetadata();
    void importProjectMetadata();
    void refreshWriterModel();
    void reviewSelectedPreference(const QString &status);
    void populateLenses(const QJsonArray &lenses);
    void populateEntities(const QJsonArray &entities);
    void populateWriterModel(const QJsonObject &model);
    QString formatResult(const QString &toolId, const QJsonObject &result) const;
    QString formatJson(const QJsonValue &value) const;
    void setBusy(const QString &message);
    void setReady(const QString &message = QString());

    WriterEngineClient *m_engine;
    QString m_projectRoot;
    QString m_activeStoryUnit;
    QString m_activeBranch = QStringLiteral("mainline");
    QString m_sourcePath;
    QString m_scopeTitle;
    QString m_activeCharacter;
    QString m_requestId;
    QString m_requestKind;
    QString m_pendingMutation;
    QString m_pendingExportPath;

    QLabel *m_positionLabel;
    QLabel *m_statusLabel;
    QComboBox *m_readerAction;
    QLineEdit *m_readerCharacter;
    QPushButton *m_readerRun;
    QPlainTextEdit *m_readerOutput;
    QTableWidget *m_entityTable;
    QPushButton *m_entityRefresh;
    QPushButton *m_entityAlias;
    QLineEdit *m_groundingEntity;
    QPushButton *m_groundingClaims;
    QPushButton *m_groundingConflicts;
    QPlainTextEdit *m_groundingOutput;
    QPushButton *m_councilRun;
    QPlainTextEdit *m_councilOutput;
    QComboBox *m_lensCombo;
    QPushButton *m_lensNew;
    QPushButton *m_lensRun;
    QPushButton *m_lensRefresh;
    QPlainTextEdit *m_lensOutput;
    QPushButton *m_experienceCurrent;
    QPushButton *m_experienceTimeline;
    QPlainTextEdit *m_experienceOutput;
    QComboBox *m_advancedAction;
    QLineEdit *m_advancedQuery;
    QLineEdit *m_advancedCharacter;
    QLineEdit *m_advancedOtherEntity;
    QPushButton *m_advancedRun;
    QPushButton *m_indexRebuild;
    QPushButton *m_projectExport;
    QPushButton *m_projectImport;
    QPlainTextEdit *m_advancedOutput;
    QTableWidget *m_writerTable;
    QPushButton *m_writerRefresh;
    QPushButton *m_writerConfirm;
    QPushButton *m_writerIgnore;
    QPlainTextEdit *m_writerOutput;
};
}

#endif
