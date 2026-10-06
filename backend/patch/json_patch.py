"""JSON Patch (RFC 6902 subset) — the only shape an AI modification may take.

Layer-1 structural validation lives here. Semantic and business-safety
validation against a concrete Business Model is applied separately before a
patch is previewed/confirmed (design doc chapters 8 and 9).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PatchOpType(StrEnum):
    ADD = "add"
    REMOVE = "remove"
    REPLACE = "replace"
    MOVE = "move"
    COPY = "copy"
    TEST = "test"


_OPS_WITH_VALUE = {PatchOpType.ADD, PatchOpType.REPLACE, PatchOpType.TEST}
_OPS_WITH_FROM = {PatchOpType.MOVE, PatchOpType.COPY}


class PatchOp(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    op: PatchOpType
    path: str = Field(description="RFC 6901 JSON Pointer, e.g. /entities/0/fields/-")
    value: Any | None = None
    from_: str | None = Field(default=None, alias="from")

    @model_validator(mode="after")
    def _check_shape(self) -> PatchOp:
        if not self.path.startswith("/"):
            raise ValueError(
                f"patch path must be an absolute JSON Pointer starting with '/', got {self.path!r}"
            )
        if self.op in _OPS_WITH_VALUE and self.value is None:
            raise ValueError(f"op {self.op.value} requires a value")
        if self.op in _OPS_WITH_FROM:
            if not self.from_:
                raise ValueError(f"op {self.op.value} requires 'from'")
            if self.value is not None:
                raise ValueError(f"op {self.op.value} must not carry a value")
        if self.op is PatchOpType.REMOVE and self.value is not None:
            raise ValueError("op remove must not carry a value")
        return self


class PatchDocument(BaseModel):
    """An ordered list of patch operations; applied atomically."""

    ops: list[PatchOp] = Field(min_length=1)

    @classmethod
    def from_raw(cls, data: Any) -> PatchDocument:
        """Accept the AI wire shape: a bare JSON array of operations."""
        if not isinstance(data, list):
            raise ValueError("patch payload must be a JSON array of operations")
        return cls.model_validate({"ops": data})
