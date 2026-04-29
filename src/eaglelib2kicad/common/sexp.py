"""Small S-expression parser for KiCad table/symbol files.

This parser intentionally supports a minimal KiCad subset:
- parentheses lists
- quoted strings with escaped quotes
- bare symbols/tokens
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SexpParseError(Exception):
    """Raised when an S-expression payload cannot be parsed."""

    message: str

    def __str__(self) -> str:
        return self.message


def parse_sexp(text: str) -> list[object]:
    """Parse an S-expression string into nested Python lists."""
    tokens = _tokenize(text)
    if not tokens:
        raise SexpParseError("Empty S-expression input")

    root, index = _parse_value(tokens, 0)
    if index != len(tokens):
        raise SexpParseError("Unexpected trailing tokens in S-expression")
    if not isinstance(root, list):
        raise SexpParseError("Expected top-level list in S-expression")
    return root


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    in_string = False
    escaped = False

    def flush_current() -> None:
        if current:
            tokens.append("".join(current))
            current.clear()

    for char in text:
        if in_string:
            if escaped:
                current.append(char)
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                tokens.append(f'"{"".join(current)}"')
                current.clear()
                in_string = False
                continue
            current.append(char)
            continue

        if char.isspace():
            flush_current()
            continue
        if char == "(" or char == ")":
            flush_current()
            tokens.append(char)
            continue
        if char == '"':
            flush_current()
            in_string = True
            continue
        current.append(char)

    if in_string:
        raise SexpParseError("Unterminated quoted string in S-expression")
    flush_current()
    return tokens


def _parse_value(tokens: list[str], index: int) -> tuple[object, int]:
    token = tokens[index]
    if token == "(":
        return _parse_list(tokens, index + 1)
    if token == ")":
        raise SexpParseError("Unexpected ')' in S-expression")
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1], index + 1
    return token, index + 1


def _parse_list(tokens: list[str], index: int) -> tuple[list[object], int]:
    result: list[object] = []
    position = index
    while position < len(tokens):
        token = tokens[position]
        if token == ")":
            return result, position + 1
        value, position = _parse_value(tokens, position)
        result.append(value)
    raise SexpParseError("Unterminated '(' in S-expression")

