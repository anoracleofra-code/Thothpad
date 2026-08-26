/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef BREATH_MAP_WIDGET_H
#define BREATH_MAP_WIDGET_H

#include <QTextDocument>
#include <QVector>
#include <QWidget>

namespace ghostwriter
{
/**
 * A thin strip that visualizes sentence-length rhythm: one bar per
 * sentence, taller/wider bars for longer sentences. Sustained runs of
 * near-identical sentence lengths (monotony) are highlighted, since varied
 * rhythm is generally the stronger prose. Updates are debounced and skipped
 * entirely while hidden, so the widget costs nothing when not in use.
 */
class BreathMapWidget : public QWidget
{
    Q_OBJECT

public:
    BreathMapWidget(QWidget *parent = nullptr);

    QSize sizeHint() const override;

    /**
     * Attaches the widget to a document. Calling again with a different
     * document rebinds and refreshes.
     */
    void setDocument(QTextDocument *document);

    /**
     * Recomputes the sentence model from the document (debounced
     * automatically on contentsChange; safe to call directly).
     */
    void refresh();

    /**
     * Word count per sentence, in document order.
     */
    QVector<int> sentenceWordCounts() const;

    /**
     * True when the model contains a run of MONOTONY_RUN_MIN consecutive
     * sentences whose lengths stay within MONOTONY_TOLERANCE_WORDS of each
     * other.
     */
    bool monotonyRunDetected() const;

protected:
    void paintEvent(QPaintEvent *event) override;

private:
    QTextDocument *document = nullptr;
    QVector<int> sentenceWords;
    bool monotonyRun = false;
};
} // namespace ghostwriter

#endif // BREATH_MAP_WIDGET_H
