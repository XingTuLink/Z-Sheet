"""YAML / JSON (de)serialization and JSON Schema export for Business Models.

YAML is the human-readable persistence and exchange format; it is never the
AI wire protocol (design doc 28.3).
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from .models import MODEL_SCHEMA_VERSION, BusinessModel


class _Yaml12Loader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 boolean rules.

    PyYAML follows YAML 1.1, where the unquoted key `on` (used by Link.on in
    model.yaml) is parsed as boolean True. Restrict implicit booleans to
    true/false so business keys keep their string meaning.
    """


_Yaml12Loader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Yaml12Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"),
    list(".tTfF"),
)


def model_from_yaml(text: str) -> BusinessModel:
    data = yaml.load(text, Loader=_Yaml12Loader)  # noqa: S506 - loader is a SafeLoader subclass
    if not isinstance(data, dict):
        raise ValueError("model YAML must contain a mapping at the top level")
    return BusinessModel.model_validate(data)


def model_to_yaml(model: BusinessModel) -> str:
    data = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    return yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )


def model_from_dict(data: dict[str, Any]) -> BusinessModel:
    return BusinessModel.model_validate(data)


def model_to_dict(model: BusinessModel) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def export_json_schema() -> dict[str, Any]:
    """Machine-readable contract for validators, editors and AI structured output."""
    schema = BusinessModel.model_json_schema()
    schema["title"] = "Z-Sheet Business Model"
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["x-zsheet-schema-version"] = MODEL_SCHEMA_VERSION
    return schema
