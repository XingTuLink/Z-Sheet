from pathlib import Path

import pytest

from backend.domain.models import MODEL_SCHEMA_VERSION
from backend.domain.serialization import (
    export_json_schema,
    model_from_yaml,
    model_to_yaml,
)

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_sales_example_loads():
    model = model_from_yaml((EXAMPLES_DIR / "sales_management.yaml").read_text(encoding="utf-8"))
    assert {e.key for e in model.entities} == {"customer", "order"}
    assert model.links[0].on.from_ == "customer_name"
    view_kinds = {v.kind for v in model.views}
    assert view_kinds == {"list", "detail", "form", "dashboard"}
    assert len(model.views) == 6


def test_yaml_roundtrip_preserves_model():
    text = (EXAMPLES_DIR / "sales_management.yaml").read_text(encoding="utf-8")
    model = model_from_yaml(text)
    reloaded = model_from_yaml(model_to_yaml(model))
    assert reloaded == model


def test_yaml11_on_key_is_not_parsed_as_bool():
    # PyYAML's YAML 1.1 rules turn unquoted `on` into True; our loader must not.
    minimal = """
version: "0.1"
app:
  name: "t"
entities:
  - key: a
    name: A
    key_field: n
    fields:
      - key: n
        name: n
        type: string
        role: identifier
        confidence: 0.9
  - key: b
    name: B
    key_field: n
    fields:
      - key: n
        name: n
        type: string
        role: identifier
        confidence: 0.9
links:
  - key: a_b
    from: a
    to: b
    type: one_to_many
    on:
      from: n
      to: n
    confidence: 0.9
"""
    model = model_from_yaml(minimal)
    assert model.links[0].on.from_ == "n"


def test_garbage_yaml_rejected():
    with pytest.raises(ValueError, match="mapping"):
        model_from_yaml("- just\n- a list\n")


def test_json_schema_export_has_core_definitions():
    schema = export_json_schema()
    assert schema["x-zsheet-schema-version"] == MODEL_SCHEMA_VERSION
    assert schema["title"] == "Z-Sheet Business Model"
    assert "entities" in schema["properties"]
    definitions = schema.get("$defs", {})
    for required in ("Entity", "BusinessField", "Link", "Metric"):
        assert required in definitions
