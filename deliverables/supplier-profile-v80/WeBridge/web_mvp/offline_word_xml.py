"""Small, Word-specific reader for markup-compatibility alternatives.

Word parts can put the same logical content in ``mc:Choice`` and
``mc:Fallback``.  The normal XML reader must see one branch only, otherwise
inline runs, block-level parts, and relationships referenced from the inactive
branch are all counted twice.  This module deliberately implements only the
Word transitional namespace as a supported capability.
"""

from io import BytesIO
import xml.etree.ElementTree as ET


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
SUPPORTED_URIS = frozenset((WORD_NS,))


def _expanded(namespace, local):
    return "{" + namespace + "}" + local


ALTERNATE_CONTENT = _expanded(MC_NS, "AlternateContent")
CHOICE = _expanded(MC_NS, "Choice")
FALLBACK = _expanded(MC_NS, "Fallback")


def _requires_supported(value, namespaces):
    """Return whether all required prefixes resolve to the Word capability."""
    if not value or not value.strip():
        return False
    prefixes = value.split()
    return all(prefix in namespaces and namespaces[prefix] in SUPPORTED_URIS
               for prefix in prefixes)


def _append_text(parent, siblings, text):
    if not text:
        return
    if siblings:
        siblings[-1].tail = (siblings[-1].tail or "") + text
    else:
        parent.text = (parent.text or "") + text


def _rewrite_children(parent, choice_support, omitted):
    """Rewrite direct children and return the updated omitted count."""
    original = list(parent)
    rewritten = []
    for child in original:
        if child.tag != ALTERNATE_CONTENT:
            omitted = _rewrite_children(child, choice_support, omitted)
            rewritten.append(child)
            continue

        selected = None
        fallback = None
        for branch in list(child):
            if branch.tag == CHOICE and selected is None:
                if choice_support.get(id(branch), False):
                    selected = branch
            elif branch.tag == FALLBACK and fallback is None:
                fallback = branch

        branch = selected if selected is not None else fallback
        if branch is None:
            omitted += 1
            _append_text(parent, rewritten, child.text)
            _append_text(parent, rewritten, child.tail)
            continue

        branch_children = list(branch)
        # The two wrapper elements' direct text is normally indentation, but
        # retaining it keeps ElementTree ordering and tail semantics intact.
        _append_text(parent, rewritten, child.text)
        _append_text(parent, rewritten, branch.text)
        for replacement in branch_children:
            if replacement.tag == ALTERNATE_CONTENT:
                holder = ET.Element("_word_alternative_holder")
                holder.append(replacement)
                omitted = _rewrite_children(holder, choice_support, omitted)
                _append_text(parent, rewritten, holder.text)
                rewritten.extend(list(holder))
            else:
                omitted = _rewrite_children(replacement, choice_support, omitted)
                rewritten.append(replacement)
        _append_text(parent, rewritten, branch.tail)
        _append_text(parent, rewritten, child.tail)

    parent[:] = rewritten
    return omitted


def parse_word_xml(data: bytes):
    """Parse Word XML and select supported ``mc:AlternateContent`` branches.

    The return value is ``(root, omitted_without_fallback_count)``.  Namespace
    declarations are tracked from the single ``iterparse`` pass so a Choice
    can use a prefix declared locally or rebound at that element.  The parser
    intentionally leaves malformed XML errors visible to the caller.
    """
    pending_namespaces = []
    namespaces = {}
    scope_stack = []
    choice_support = {}
    root = None
    for event, item in ET.iterparse(BytesIO(data), events=("start-ns", "start", "end")):
        if event == "start-ns":
            pending_namespaces.append(item)
            continue
        if event == "start":
            changes = []
            for prefix, uri in pending_namespaces:
                previous = namespaces.get(prefix)
                changes.append((prefix, previous))
                namespaces[prefix] = uri
            pending_namespaces.clear()
            scope_stack.append(changes)
            if root is None:
                root = item
            if item.tag == CHOICE:
                choice_support[id(item)] = _requires_supported(
                    item.get("Requires"), namespaces)
            continue
        changes = scope_stack.pop()
        for prefix, previous in reversed(changes):
            if previous is None:
                namespaces.pop(prefix, None)
            else:
                namespaces[prefix] = previous

    if root is None:
        raise ET.ParseError("empty XML document")
    omitted = _rewrite_children(root, choice_support, 0)
    return root, omitted
