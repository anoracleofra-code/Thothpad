/*
 * SPDX-FileCopyrightText: 2017-2023 Megan Conkle <megan.conkle@kdemail.net>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QComboBox>
#include <QDialogButtonBox>
#include <QFileInfo>
#include <QFormLayout>
#include <QLineEdit>
#include <QPushButton>
#include <QSettings>
#include <QVBoxLayout>

#include "../export/exporterfactory.h"

#include "appsettings.h"
#include "previewoptionsdialog.h"
#include "simplefontdialog.h"

namespace ghostwriter
{
class PreviewOptionsDialogPrivate
{
public:
    PreviewOptionsDialogPrivate()
    {
        ;
    }

    ~PreviewOptionsDialogPrivate()
    {
        ;
    }

    void onExporterChanged(int index) const;
    QString fontToString(const QFont &font) const;

    AppSettings *appSettings;
    ExporterFactory *exporterFactory;
    QComboBox *previewerComboBox;
    QLineEdit *paramsLineEdit;
};

PreviewOptionsDialog::PreviewOptionsDialog(QWidget *parent)
    : QDialog(parent),
      d_ptr(new PreviewOptionsDialogPrivate())
{
    Q_D(PreviewOptionsDialog);

    this->setWindowTitle(tr("Preview Options"));
    d->appSettings = AppSettings::instance();
    d->exporterFactory = ExporterFactory::instance();

    QWidget *mainContents = new QWidget(this);
    QVBoxLayout *layout = new QVBoxLayout();
    this->setLayout(layout);
    layout->addWidget(mainContents);

    QFormLayout *optionsLayout = new QFormLayout();
    mainContents->setLayout(optionsLayout);

    d->previewerComboBox = new QComboBox(this);

    QList<Exporter *> exporters = d->exporterFactory->htmlExporters();
    Exporter *currentExporter = d->appSettings->currentHtmlExporter();

    int currentExporterIndex = 0;

    for (int i = 0; i < exporters.length(); i++) {
        Exporter *exporter = exporters.at(i);
        d->previewerComboBox->addItem(exporter->name(), QVariant::fromValue((void *) exporter));

        if (exporter == currentExporter) {
            currentExporterIndex = i;
        }
    }

    d->previewerComboBox->setCurrentIndex(currentExporterIndex);

    connect
    (
        d->previewerComboBox,
        static_cast<void (QComboBox::*)(int)>(&QComboBox::currentIndexChanged),
        [d](int index) {
            d->onExporterChanged(index);
            d->paramsLineEdit->setDisabled(!d->appSettings->currentHtmlExporter()->supportsUserOptions());
        }
    );

    optionsLayout->addRow(tr("Markdown Flavor"), d->previewerComboBox);

    QHBoxLayout *fontLayout = new QHBoxLayout();

    QLineEdit *currentTextFont = new QLineEdit(d->fontToString(d->appSettings->previewTextFont()));
    currentTextFont->setReadOnly(true);
    fontLayout->addWidget(currentTextFont);

    QPushButton *chooseButton = new QPushButton(tr("Choose..."));
    fontLayout->addWidget(chooseButton);

    connect(chooseButton,
        &QPushButton::clicked,
        [this, d, currentTextFont]() {
            bool success = false;

            QFont font = SimpleFontDialog::font(&success,
                d->appSettings->previewTextFont(),
                this);

            if (success) {
                currentTextFont->setText(d->fontToString(font));
                d->appSettings->setPreviewTextFont(font);
            }
        });

    optionsLayout->addRow(tr("Text Font:"), fontLayout);

    fontLayout = new QHBoxLayout();

    QLineEdit *currentCodeFont = new QLineEdit(d->fontToString(d->appSettings->previewCodeFont()));
    currentCodeFont->setReadOnly(true);
    fontLayout->addWidget(currentCodeFont);

    chooseButton = new QPushButton(tr("Choose..."));
    fontLayout->addWidget(chooseButton);

    connect(chooseButton,
        &QPushButton::clicked,
        [this, d, currentCodeFont]() {
            bool success = false;

            QFont font = SimpleFontDialog::monospaceFont(&success, d->appSettings->previewCodeFont(), this);

            if (success) {
                currentCodeFont->setText(d->fontToString(font));
                d->appSettings->setPreviewCodeFont(font);
            }
        });

    optionsLayout->addRow(tr("Code Font:"), fontLayout);

    QDialogButtonBox *buttonBox = new QDialogButtonBox(Qt::Horizontal, this);
    buttonBox->addButton(QDialogButtonBox::Close);
    layout->addWidget(buttonBox);

    d->paramsLineEdit = new QLineEdit();
    d->paramsLineEdit->setText(d->appSettings->currentHtmlExporter()->options());
    optionsLayout->addRow(tr("Command line options:"), d->paramsLineEdit);
    connect(d->paramsLineEdit, &QLineEdit::textChanged, [=](const QString& obj) {
        Exporter *exporter = d->appSettings->currentHtmlExporter();
        exporter->setOptions(obj);
        d->appSettings->setCurrentHtmlExporter(exporter);
    });
    d->paramsLineEdit->setDisabled(!d->appSettings->currentHtmlExporter()->supportsUserOptions());

    connect(buttonBox->button(QDialogButtonBox::Close), SIGNAL(clicked()), this, SLOT(close()));
}

PreviewOptionsDialog::~PreviewOptionsDialog()
{
    ;
}

void PreviewOptionsDialogPrivate::onExporterChanged(int index) const
{
    QVariant exporterVariant = this->previewerComboBox->itemData(index);
    Exporter *exporter = (Exporter *) exporterVariant.value<void *>();

    // Persist the outgoing exporter's options under its own name so command
    // line params do not bleed across markdown flavors, then restore the
    // incoming exporter's previously saved options (if any).
    Exporter *previous = this->appSettings->currentHtmlExporter();
    if (nullptr != previous) {
        QSettings().setValue(QStringLiteral("Preview/exporterParams/") + previous->name(), previous->options());
    }
    const QString savedOptions = QSettings().value(QStringLiteral("Preview/exporterParams/") + exporter->name()).toString();
    exporter->setOptions(savedOptions);
    appSettings->setCurrentHtmlExporter(exporter);

    // Keep the visible options field in sync with the loaded options.
    // Without this the field would keep showing the previous exporter's
    // text and the next keystroke would write it back over the incoming
    // exporter's own saved options. Setting identical text is a no-op (Qt
    // emits textChanged only on actual change), so no recursion is possible.
    this->paramsLineEdit->setText(savedOptions);
}

QString PreviewOptionsDialogPrivate::fontToString(const QFont &font) const
{
    return PreviewOptionsDialog::tr("%1 %2pt")
        .arg(font.family())
        .arg(font.pointSize());
}

} // namespace ghostwriter
