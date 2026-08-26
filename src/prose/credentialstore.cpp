/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "credentialstore.h"

#ifdef THOTHPAD_HAS_QTKEYCHAIN
#include <qt6keychain/keychain.h>
#endif

namespace ghostwriter
{
namespace
{
const QString ServiceName = QStringLiteral("ThothPad");
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
