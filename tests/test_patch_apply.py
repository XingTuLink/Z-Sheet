import pytest
from pydantic import ValidationError

from backend.patch.apply import PatchError, apply_patch
from backend.patch.json_patch import PatchDocument


def ops(raw: list[dict]):
    return PatchDocument.from_raw(raw).ops


def test_add_object_key_and_nested_path():
    doc = {"app": {"name": "x"}}
    result = apply_patch(doc, ops([{"op": "add", "path": "/app/name", "value": "y"}]))
    assert result == {"app": {"name": "y"}}
    assert doc == {"app": {"name": "x"}}  # original untouched


def test_add_array_insert_and_append_dash():
    doc = {"items": ["a", "b"]}
    result = apply_patch(
        doc,
        ops(
            [
                {"op": "add", "path": "/items/1", "value": "x"},
                {"op": "add", "path": "/items/-", "value": "z"},
            ]
        ),
    )
    assert result["items"] == ["a", "x", "b", "z"]


def test_remove_and_replace():
    doc = {"a": 1, "b": 2}
    result = apply_patch(
        doc,
        ops(
            [
                {"op": "remove", "path": "/a"},
                {"op": "replace", "path": "/b", "value": 9},
            ]
        ),
    )
    assert result == {"b": 9}


def test_move_and_copy():
    doc = {"a": {"x": 1}, "b": []}
    result = apply_patch(
        doc,
        ops(
            [
                {"op": "move", "from": "/a/x", "path": "/m"},
                {"op": "copy", "from": "/m", "path": "/b/0"},
            ]
        ),
    )
    assert result == {"a": {}, "b": [1], "m": 1}


def test_escaped_pointer_tokens():
    doc = {"a/b": 1, "c~d": 2}
    result = apply_patch(doc, ops([{"op": "replace", "path": "/a~1b", "value": 9}]))
    assert result["a/b"] == 9
    result2 = apply_patch(result, ops([{"op": "test", "path": "/c~0d", "value": 2}]))
    assert result2 == result


def test_test_op_failure_aborts_and_keeps_input():
    doc = {"a": 1}
    with pytest.raises(PatchError, match="test failed"):
        apply_patch(
            doc,
            ops(
                [
                    {"op": "replace", "path": "/a", "value": 2},
                    {"op": "test", "path": "/a", "value": 1},
                ]
            ),
        )
    assert doc == {"a": 1}


def test_add_to_escaped_entity_path_on_real_model_shape():
    doc = {"entities": [{"key": "customer", "fields": []}]}
    result = apply_patch(
        doc,
        ops([{"op": "add", "path": "/entities/0/fields/-", "value": {"key": "name"}}]),
    )
    assert result["entities"][0]["fields"] == [{"key": "name"}]


@pytest.mark.parametrize(
    "raw",
    [
        [{"op": "add", "path": "/items/9", "value": 1}],
        [{"op": "remove", "path": "/missing"}],
        [{"op": "move", "from": "/a", "path": "/a/child"}],
        [{"op": "add", "path": "/items/01", "value": 1}],
    ],
)
def test_invalid_operations_raise(raw):
    base = {"items": ["a"], "a": {"x": 1}}
    with pytest.raises(PatchError):
        apply_patch(base, ops(raw))


@pytest.mark.parametrize(
    "raw",
    [
        [{"op": "add", "path": "noslash", "value": 1}],
        [{"op": "replace", "path": "", "value": {}}],
    ],
)
def test_schema_rejects_non_absolute_pointers_before_apply(raw):
    with pytest.raises(ValidationError):
        ops(raw)
