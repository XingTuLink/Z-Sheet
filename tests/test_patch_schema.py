import pytest
from pydantic import ValidationError

from backend.patch.json_patch import PatchDocument, PatchOpType


def test_valid_add_patch_from_bare_array():
    doc = PatchDocument.from_raw(
        [
            {
                "op": "add",
                "path": "/entities/0/fields/-",
                "value": {"key": "follow_status", "name": "跟进状态", "type": "enum"},
            }
        ]
    )
    assert doc.ops[0].op is PatchOpType.ADD
    assert doc.ops[0].from_ is None


def test_move_requires_from():
    with pytest.raises(ValidationError, match="'from'"):
        PatchDocument.from_raw([{"op": "move", "path": "/views/0"}])


def test_remove_must_not_carry_value():
    with pytest.raises(ValidationError, match="remove"):
        PatchDocument.from_raw(
            [{"op": "remove", "path": "/entities/1", "value": "unexpected"}]
        )


def test_replace_requires_value():
    with pytest.raises(ValidationError, match="requires a value"):
        PatchDocument.from_raw([{"op": "replace", "path": "/app/name"}])


def test_path_must_be_json_pointer():
    with pytest.raises(ValidationError, match="JSON Pointer"):
        PatchDocument.from_raw([{"op": "remove", "path": "entities/0"}])


def test_unknown_op_rejected():
    with pytest.raises(ValidationError):
        PatchDocument.from_raw([{"op": "rewrite_everything", "path": "/"}])


def test_empty_patch_rejected():
    with pytest.raises(ValidationError):
        PatchDocument.from_raw([])
