"""Apply an RFC 6902 subset JSON Patch to a JSON-like document.

Supported ops: add, remove, replace, move, copy, test (matching the
PatchOp schema frozen on Day 1). Pure function: the input document is
never mutated.
"""

from __future__ import annotations

import copy
from typing import Any

from backend.patch.json_patch import PatchOp


class PatchError(Exception):
    """A patch operation cannot be applied to the document."""


def _parse_pointer(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PatchError(f"invalid JSON Pointer: {pointer!r}")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:]]


def _parse_array_index(token: str, length: int, *, allow_append: bool) -> int:
    if token == "-":
        if not allow_append:
            raise PatchError("'-' may only be used to append with add")
        return length
    if not token.isdigit() or (len(token) > 1 and token[0] == "0"):
        raise PatchError(f"invalid array index: {token!r}")
    index = int(token)
    upper = length if allow_append else length - 1
    if index > upper:
        raise PatchError(f"array index out of range: {index} (length {length})")
    return index


def _navigate(document: Any, tokens: list[str]) -> tuple[Any, str]:
    """Return (parent, key) for the location addressed by tokens."""
    if not tokens:
        raise PatchError("empty pointer cannot address a container member")
    current = document
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                raise PatchError(f"path segment not found: {token!r}")
            current = current[token]
        elif isinstance(current, list):
            index = _parse_array_index(token, len(current), allow_append=False)
            current = current[index]
        else:
            raise PatchError(f"cannot traverse into value at segment {token!r}")
    return current, tokens[-1]


def _get(document: Any, tokens: list[str]) -> Any:
    if not tokens:
        return document
    parent, key = _navigate(document, tokens)
    if isinstance(parent, dict):
        if key not in parent:
            raise PatchError(f"path segment not found: {key!r}")
        return parent[key]
    index = _parse_array_index(key, len(parent), allow_append=False)
    return parent[index]


def _add(document: Any, tokens: list[str], value: Any) -> None:
    if not tokens:
        raise PatchError("cannot replace the whole document with add (use import)")
    parent, key = _navigate(document, tokens)
    if isinstance(parent, dict):
        parent[key] = value
    elif isinstance(parent, list):
        index = _parse_array_index(key, len(parent), allow_append=True)
        parent.insert(index, value)
    else:
        raise PatchError(f"cannot add member to value at {key!r}")


def _remove(document: Any, tokens: list[str]) -> Any:
    if not tokens:
        raise PatchError("cannot remove the whole document")
    parent, key = _navigate(document, tokens)
    if isinstance(parent, dict):
        if key not in parent:
            raise PatchError(f"path segment not found: {key!r}")
        return parent.pop(key)
    index = _parse_array_index(key, len(parent), allow_append=False)
    return parent.pop(index)


def apply_patch(document: dict[str, Any], ops: list[PatchOp]) -> dict[str, Any]:
    """Return a deep-copied document with all operations applied sequentially."""
    result = copy.deepcopy(document)
    for op in ops:
        tokens = _parse_pointer(op.path)
        if op.op == "add":
            _add(result, tokens, op.value)
        elif op.op == "remove":
            _remove(result, tokens)
        elif op.op == "replace":
            if not tokens:
                raise PatchError("cannot replace the whole document (use import)")
            _remove(result, tokens)
            _add(result, tokens, op.value)
        elif op.op == "move":
            assert op.from_ is not None  # guaranteed by PatchOp validation
            from_tokens = _parse_pointer(op.from_)
            if from_tokens and from_tokens == tokens[: len(from_tokens)]:
                raise PatchError("move: 'from' must not be a prefix of 'path'")
            moved = copy.deepcopy(_get(result, from_tokens))
            _remove(result, from_tokens)
            _add(result, tokens, moved)
        elif op.op == "copy":
            assert op.from_ is not None  # guaranteed by PatchOp validation
            moved = copy.deepcopy(_get(result, _parse_pointer(op.from_)))
            _add(result, tokens, moved)
        elif op.op == "test":
            if _get(result, tokens) != op.value:
                raise PatchError(f"test failed at {op.path}")
        else:  # pragma: no cover - blocked by the enum
            raise PatchError(f"unsupported op: {op.op}")
    return result
