/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef THOTHPAD_CREDENTIAL_STORE_H
#define THOTHPAD_CREDENTIAL_STORE_H

#include <QObject>
#include <QString>
#include <QUrl>

namespace ghostwriter
{
class CredentialStore : public QObject
{
    Q_OBJECT

public:
    explicit CredentialStore(QObject *parent = nullptr);

    bool isAvailable() const;
    void write(const QString &credentialId, const QString &secret);
    void read(const QString &credentialId);
    void remove(const QString &credentialId);

    // Canonical provider credential-id derivation shared by the provider
    // settings dialog, prose controller, and story intelligence controller:
    // provider/<kind>/<first-16-hex-of-sha256(scheme://host:port)>/<model>.
    // Default port is 443 for https, 80 otherwise.
    static QString providerCredentialId(const QString &kind, const QUrl &baseurl, const QString &model);

signals:
    void written(const QString &credentialId);
    void loaded(const QString &credentialId, const QString &secret);
    void removed(const QString &credentialId);
    void error(const QString &credentialId, const QString &message);
};
}

#endif
