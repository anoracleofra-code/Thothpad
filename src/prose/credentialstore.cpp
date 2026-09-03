/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "credentialstore.h"

#ifdef THOTHPAD_HAS_QTKEYCHAIN
#include <qt6keychain/keychain.h>
#endif

#include <QCryptographicHash>
#include <QUrl>

namespace ghostwriter
{
namespace
{
const QString ServiceName = QStringLiteral("ThothPad");
}

QString CredentialStore::providerCredentialId(const QString &kind, const QUrl &baseurl, const QString &model)
{
    const int defaultPort = baseurl.scheme().compare(QStringLiteral("https"), Qt::CaseInsensitive) == 0 ? 443 : 80;
    const QString origin = QStringLiteral("%1://%2:%3").arg(baseurl.scheme().toLower(), baseurl.host().toLower()).arg(baseurl.port(defaultPort));
    const QString endpointId = QString::fromLatin1(QCryptographicHash::hash(origin.toUtf8(), QCryptographicHash::Sha256).toHex().left(16));
    return QStringLiteral("provider/%1/%2/%3").arg(kind, endpointId, model);
}

CredentialStore::CredentialStore(QObject *parent)
    : QObject(parent)
{
}

bool CredentialStore::isAvailable() const
{
#ifdef THOTHPAD_HAS_QTKEYCHAIN
    return true;
#else
    return false;
#endif
}

void CredentialStore::write(const QString &credentialId, const QString &secret)
{
#ifdef THOTHPAD_HAS_QTKEYCHAIN
    auto *job = new QKeychain::WritePasswordJob(ServiceName, this);
    job->setKey(credentialId);
    job->setTextData(secret);
    connect(job, &QKeychain::Job::finished, this, [this, job, credentialId]() {
        if (job->error()) {
            emit error(credentialId, job->errorString());
        } else {
            emit written(credentialId);
        }
        job->deleteLater();
    });
    job->start();
#else
    Q_UNUSED(secret)
    emit error(credentialId, tr("Secure credential storage is unavailable in this build."));
#endif
}

void CredentialStore::read(const QString &credentialId)
{
#ifdef THOTHPAD_HAS_QTKEYCHAIN
    auto *job = new QKeychain::ReadPasswordJob(ServiceName, this);
    job->setKey(credentialId);
    connect(job, &QKeychain::Job::finished, this, [this, job, credentialId]() {
        if (job->error()) {
            emit error(credentialId, job->errorString());
        } else {
            emit loaded(credentialId, job->textData());
        }
        job->deleteLater();
    });
    job->start();
#else
    emit error(credentialId, tr("Secure credential storage is unavailable in this build."));
#endif
}

void CredentialStore::remove(const QString &credentialId)
{
#ifdef THOTHPAD_HAS_QTKEYCHAIN
    auto *job = new QKeychain::DeletePasswordJob(ServiceName, this);
    job->setKey(credentialId);
    connect(job, &QKeychain::Job::finished, this, [this, job, credentialId]() {
        if (job->error()) {
            emit error(credentialId, job->errorString());
        } else {
            emit removed(credentialId);
        }
        job->deleteLater();
    });
    job->start();
#else
    emit error(credentialId, tr("Secure credential storage is unavailable in this build."));
#endif
}
}
