/*
 * SPDX-FileCopyrightText: 2020-2023 Megan Conkle <megan.conkle@kdemail.net>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <QStack>
#include <QTextStream>
#include <QVector>
#include <QtGlobal>

#include <3rdparty/cmark-gfm/src/cmark-gfm.h>
#include "markdownast.h"

namespace ghostwriter
{
class MarkdownASTPrivate
{
public:
    MarkdownASTPrivate()
    {
        ;
    }

    ~MarkdownASTPrivate()
    {
        ;
    }

    MemoryArena<MarkdownNode> arena;
    MarkdownNode *root;
    QVector<MarkdownNode *> blocksByLine;
    QVector<quint64> signaturesByLine;

    void buildLineIndex();
};

namespace
{
constexpr quint64 SignatureSeed = 1469598103934665603ULL;
constexpr quint64 SignaturePrime = 1099511628211ULL;

quint64 mixSignature(quint64 signature, quint64 value)
{
    signature ^= value;
    signature *= SignaturePrime;
    return signature;
}

quint64 nodeSignature(const MarkdownNode *node)
{
    quint64 signature = SignatureSeed;
    signature = mixSignature(signature, static_cast<quint64>(node->type()));
    signature = mixSignature(signature, static_cast<quint64>(node->position() + 1));
    signature = mixSignature(signature, static_cast<quint64>(node->length() + 1));
    signature = mixSignature(signature, static_cast<quint64>(node->endLine() - node->startLine() + 1));
    signature = mixSignature(signature, static_cast<quint64>(node->headingLevel()));
    signature = mixSignature(signature, node->isFencedCodeBlock() ? 1ULL : 0ULL);
    signature = mixSignature(signature, node->isNumberedListItem() ? 1ULL : 0ULL);
    signature = mixSignature(signature, node->isInsideBlockquote() ? 1ULL : 0ULL);

    if (node->parent() != nullptr) {
        signature = mixSignature(signature, static_cast<quint64>(node->parent()->type()));
    }

    const QString text = node->text();
    for (const QChar character : text) {
        signature = mixSignature(signature, character.unicode());
    }

    return signature;
}
}

MarkdownAST::MarkdownAST()
    : d_ptr(new MarkdownASTPrivate())
{
    Q_D(MarkdownAST);
    
    d->root = nullptr;
}

MarkdownAST::MarkdownAST(cmark_node *root)
    : d_ptr(new MarkdownASTPrivate())
{    
    setRoot(root);
}

MarkdownAST::~MarkdownAST()
{
    Q_D(MarkdownAST);
    
    d->arena.freeAll();
    d->root = nullptr;
}

MarkdownNode *MarkdownAST::root()
{
    Q_D(MarkdownAST);
    
    return d->root;
}

void MarkdownAST::setRoot(cmark_node *root)
{
    Q_D(MarkdownAST);
    
    d->arena.freeAll();

    if (nullptr == root) {
        d->root = nullptr;
        d->blocksByLine.clear();
        d->signaturesByLine.clear();
        return;
    }

    d->root = d->arena.allocate();

    // Clone the node into memory that isn't allocated to
    // cmark-gfm's arena memory.
    QStack<cmark_node *> fromNodes;
    QStack<MarkdownNode *> toNodes;

    d->root->setDataFrom(root);
    fromNodes.push(root);
    toNodes.push(d->root);

    while (!fromNodes.isEmpty()) {
        cmark_node *source = fromNodes.pop();
        MarkdownNode *dest = toNodes.pop();

        // Prep children nodes for cloning.
        MarkdownNode *destParent = dest;
        source = cmark_node_first_child(source);

        while (NULL != source) {
            fromNodes.push(source);
            dest = d->arena.allocate();
            dest->setDataFrom(source);
            destParent->appendChild(dest);
            toNodes.push(dest);
            source = cmark_node_next(source);
        }
    }

    d->buildLineIndex();
}

MarkdownNode *MarkdownAST::findBlockAtLine(int lineNumber) const
{
    Q_D(const MarkdownAST);

    if ((lineNumber <= 0) || (lineNumber >= d->blocksByLine.size())) {
        return nullptr;
    }

    return d->blocksByLine.at(lineNumber);
}

QVector<quint64> MarkdownAST::lineSignatures() const
{
    Q_D(const MarkdownAST);
    return d->signaturesByLine;
}

QVector<MarkdownNode *> MarkdownAST::headings() const
{
    Q_D(const MarkdownAST);
    
    QVector<MarkdownNode *> headings;

    if ((nullptr == d->root) || (MarkdownNode::Invalid == d->root->type())) {
        return headings;
    }

    MarkdownNode *node = d->root->firstChild();

    while (nullptr != node) {
        if (MarkdownNode::Heading == node->type()) {
            headings.append(node);
        }

        node = node->next();
    }

    return headings;
}

void MarkdownAST::clear()
{
    Q_D(MarkdownAST);
    
    d->arena.freeAll();
    d->root = nullptr;
    d->blocksByLine.clear();
    d->signaturesByLine.clear();
}

void MarkdownASTPrivate::buildLineIndex()
{
    blocksByLine.clear();
    signaturesByLine.clear();

    if ((root == nullptr) || (root->type() == MarkdownNode::Invalid)) {
        return;
    }

    int maximumLine = 0;
    QStack<MarkdownNode *> nodes;
    nodes.push(root);

    while (!nodes.isEmpty()) {
        MarkdownNode *node = nodes.pop();
        maximumLine = qMax(maximumLine, qMax(node->startLine(), node->endLine()));

        for (MarkdownNode *child = node->lastChild(); child != nullptr; child = child->previous()) {
            nodes.push(child);
        }
    }

    blocksByLine.fill(nullptr, maximumLine + 1);
    signaturesByLine.fill(SignatureSeed, maximumLine + 1);
    QVector<int> indexedDepth(maximumLine + 1, -1);

    struct WorkItem {
        MarkdownNode *node;
        int depth;
        bool blockSelectionLocked;
    };

    QStack<WorkItem> work;
    work.push({root, 0, false});

    while (!work.isEmpty()) {
        const WorkItem item = work.pop();
        MarkdownNode *node = item.node;
        const int startLine = qMax(1, node->startLine());
        const int endLine = qMax(startLine, node->endLine());
        const bool selectable =
            node->isBlockType() && (node->type() != MarkdownNode::Document) && (node->type() != MarkdownNode::TableCell) && !item.blockSelectionLocked;

        if (selectable) {
            for (int line = startLine; line <= endLine && line < blocksByLine.size(); ++line) {
                if (item.depth > indexedDepth.at(line)) {
                    blocksByLine[line] = node;
                    indexedDepth[line] = item.depth;
                }
            }
        }

        const quint64 baseSignature = nodeSignature(node);
        if (node->type() == MarkdownNode::Document) {
            // The document node is never passed to the highlighter. Including
            // its changing end position would invalidate every line after a
            // local insertion or deletion.
        } else if (node->isBlockType()) {
            for (int line = startLine; line <= endLine && line < signaturesByLine.size(); ++line) {
                quint64 signature = mixSignature(baseSignature, line == startLine ? 1ULL : 0ULL);
                signature = mixSignature(signature, line == endLine ? 1ULL : 0ULL);
                signaturesByLine[line] = mixSignature(signaturesByLine.at(line), signature);
            }
        } else {
            if (startLine < signaturesByLine.size()) {
                signaturesByLine[startLine] = mixSignature(signaturesByLine.at(startLine), baseSignature);
            }
            if ((endLine != startLine) && (endLine < signaturesByLine.size())) {
                signaturesByLine[endLine] = mixSignature(signaturesByLine.at(endLine), baseSignature);
            }
        }

        const bool locksChildren = item.blockSelectionLocked || (node->type() == MarkdownNode::ListItem) || (node->type() == MarkdownNode::TaskListItem)
            || (node->type() == MarkdownNode::TableCell);

        for (MarkdownNode *child = node->lastChild(); child != nullptr; child = child->previous()) {
            work.push({child, item.depth + 1, locksChildren});
        }
    }
}

QString MarkdownAST::toString() const
{
    Q_D(const MarkdownAST);
    
    if (nullptr == d->root) {
        return "AST is empty";
    }

    QString text;
    QTextStream stream(&text);
    QStack<MarkdownNode *> nodes;
    QStack<QString> indentation;

    nodes.push(d->root);
    indentation.push("");

    while (!nodes.empty()) {
        MarkdownNode *node = nodes.pop();
        QString indent = indentation.pop();


#if (QT_VERSION >= QT_VERSION_CHECK(5, 14, 0))
        stream << indent << "->" << node->toString() << Qt::endl;
#else
        stream << indent << "->" << node->toString() << endl;
#endif

        MarkdownNode *child = node->lastChild();
        indent += "   ";

        while (nullptr != child) {
            nodes.push(child);
            indentation.push(indent);
            child = child->previous();
        }
    }

    return text;
}
} // namespace ghostwriter
