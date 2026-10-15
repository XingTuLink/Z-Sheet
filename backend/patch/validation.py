"""Three-layer patch validation as a pure, persistence-free function.

The same core is shared by the version writer (Day 21) and the Day 24 patch
generator dry run:

1. patch shape — RFC 6902 subset via :class:`PatchDocument`;
2. structure — apply the ops to a JSON-like snapshot (:func:`apply_patch`);
3. semantics — re-validate the result as a full :class:`BusinessModel`.

Nothing here touches the database; callers persist the returned snapshot only
after their own gates (preview/confirm) have passed.
"""

from __future__ import annotations

from typing import Any

from backend.domain.models import BusinessModel
from backend.patch.apply import apply_patch
from backend.patch.json_patch import PatchDocument


def validate_patch_snapshot(
    snapshot: dict[str, Any], raw_ops: list[dict[str, Any]]
) -> dict[str, Any]:
    """Validate raw patch ops against a model snapshot and return the result.

    Raises ``ValueError``/``pydantic.ValidationError`` on layer 1/3 failures and
    ``backend.patch.apply.PatchError`` on layer 2 failures.
    """
    document = PatchDocument.from_raw(raw_ops)
    new_snapshot = apply_patch(snapshot, document.ops)
    model = BusinessModel.model_validate(new_snapshot)
    return model.model_dump(mode="json")
