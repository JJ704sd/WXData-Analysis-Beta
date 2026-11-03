"""Small, non-executing parser for Word HYPERLINK fields.

The caller owns visible text extraction.  ``FieldLinks`` only tracks field
instructions and returns a display marker when a completed field is a
HYPERLINK field.
"""

from dataclasses import dataclass, field


def _tokens(instruction):
    """Tokenize the small Word field syntax without treating backslashes as escapes."""
    tokens = []
    token = []
    quoted = False
    started = False
    for char in instruction:
        if char == '"':
            quoted = not quoted
            started = True
        elif char.isspace() and not quoted:
            if started:
                tokens.append(''.join(token))
                token = []
                started = False
        else:
            token.append(char)
            started = True
    if quoted:
        return None
    if started:
        tokens.append(''.join(token))
    return tokens


def hyperlink_target(instruction: str) -> str:
    """Return a HYPERLINK destination, or ``''`` for anything else.

    Only the target and the internal-bookmark switch are rendered.  Tooltip
    and window switches are metadata, and no destination is opened or fetched.
    """
    tokens = _tokens(instruction)
    if not tokens or tokens[0].casefold() != 'hyperlink':
        return ''

    switches = {'\\l', '\\o', '\\t'}
    index = 1
    target = ''
    if index < len(tokens) and tokens[index].casefold() not in switches:
        target = tokens[index]
        index += 1

    bookmark = ''
    while index < len(tokens):
        option = tokens[index].casefold()
        index += 1
        if option == '\\l':
            if index < len(tokens) and tokens[index].casefold() not in switches:
                bookmark = tokens[index]
                index += 1
        elif option in ('\\o', '\\t'):
            if index < len(tokens) and tokens[index].casefold() not in switches:
                index += 1

    if bookmark:
        return (target + '#' if target else '#') + bookmark
    return target


@dataclass
class _Field:
    parts: list[str] = field(default_factory=list)
    separated: bool = False


class FieldLinks:
    """Track nested Word fields without interpreting field languages."""

    def __init__(self):
        self._stack = []

    def instruction(self, text):
        """Append instruction text to the currently open field."""
        if self._stack and not self._stack[-1].separated and text is not None:
            self._stack[-1].parts.append(str(text))

    def control(self, kind):
        """Handle ``begin``, ``separate`` and ``end`` field controls."""
        if kind == 'begin':
            self._stack.append(_Field())
            return ''
        if not self._stack:
            return ''
        if kind == 'separate':
            self._stack[-1].separated = True
            return ''
        if kind == 'end':
            current = self._stack.pop()
            if not current.separated:
                return ''
            target = hyperlink_target(''.join(current.parts))
            return ' [链接: ' + target + ']' if target else ''
        return ''
