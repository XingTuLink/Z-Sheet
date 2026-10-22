import pytest
from pydantic import ValidationError

from backend.domain.enums import FieldRole, FieldType
from backend.domain.models import BusinessField, BusinessModel


def _field(**overrides) -> dict:
    base = {
        "key": "name",
        "name": "名称",
        "type": "string",
        "role": "identifier",
        "confidence": 0.95,
        "needs_review": False,
    }
    base.update(overrides)
    return base


def _minimal_model(**overrides) -> dict:
    model = {
        "version": "0.1",
        "app": {"name": "最小系统"},
        "entities": [
            {
                "key": "thing",
                "name": "事物",
                "key_field": "name",
                "fields": [_field()],
            }
        ],
    }
    model.update(overrides)
    return model


def test_minimal_valid_model():
    model = BusinessModel.model_validate(_minimal_model())
    assert model.version == "0.1"
    assert model.entities[0].fields[0].needs_review is False


def test_legacy_metric_snapshot_without_confidence_still_boots():
    data = _minimal_model(
        metrics=[
            {
                "key": "thing_count",
                "name": "事物总数",
                "entity": "thing",
                "formula": {"op": "count"},
                "business_definition": "事物记录的总条数（自动生成口径）",
            }
        ]
    )
    model = BusinessModel.model_validate(data)
    metric = model.metrics[0]
    assert metric.confidence == 0.9
    assert metric.needs_review is False
    assert metric.review_reason is None


def test_needs_review_derived_when_omitted():
    data = _field(confidence=0.61)
    del data["needs_review"]  # genuinely omitted -> must be derived from confidence
    field = BusinessField.model_validate(data)
    assert field.needs_review is True


def test_medium_confidence_cannot_opt_out_of_review():
    with pytest.raises(ValidationError):
        BusinessField.model_validate(_field(confidence=0.61, needs_review=False))


def test_high_confidence_may_be_forced_into_review():
    field = BusinessField.model_validate(_field(confidence=0.88, needs_review=True))
    assert field.needs_review is True


def test_enum_requires_values():
    with pytest.raises(ValidationError):
        BusinessField.model_validate(
            _field(type=FieldType.ENUM, role=FieldRole.DIMENSION)
        )


def test_values_forbidden_on_non_enum():
    with pytest.raises(ValidationError):
        BusinessField.model_validate(_field(values=["a", "b"]))


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        BusinessModel.model_validate(_minimal_model(unkown_top_level=True))


def test_key_field_must_exist():
    bad = _minimal_model()
    bad["entities"][0]["key_field"] = "missing"
    with pytest.raises(ValidationError, match="key_field"):
        BusinessModel.model_validate(bad)


def test_duplicate_entity_keys_rejected():
    bad = _minimal_model()
    bad["entities"].append(
        {
            "key": "thing",
            "name": "重复",
            "key_field": "name",
            "fields": [_field()],
        }
    )
    with pytest.raises(ValidationError, match="duplicate entity"):
        BusinessModel.model_validate(bad)


def test_dangling_link_reference_rejected():
    bad = _minimal_model()
    bad["links"] = [
        {
            "key": "thing_other",
            "from": "thing",
            "to": "other",
            "type": "one_to_many",
            "on": {"from": "name", "to": "name"},
            "confidence": 0.9,
            "needs_review": False,
        }
    ]
    with pytest.raises(ValidationError, match="unknown entity"):
        BusinessModel.model_validate(bad)


def test_dangling_navigation_view_rejected():
    bad = _minimal_model()
    bad["navigation"] = [{"label": "不存在", "view": "ghost_view"}]
    with pytest.raises(ValidationError, match="unknown view"):
        BusinessModel.model_validate(bad)


def _self_link_model(**link_overrides) -> dict:
    link = {
        "key": "employee_manager",
        "from": "employee",
        "to": "employee",
        "type": "one_to_one",
        "on": {"from": "employee_id", "to": "manager_id"},
        "confidence": 1.0,
        "needs_review": False,
    }
    link.update(link_overrides)
    return _minimal_model(
        entities=[
            {
                "key": "employee",
                "name": "员工",
                "key_field": "employee_id",
                "fields": [
                    _field(key="employee_id", name="工号"),
                    _field(
                        key="manager_id",
                        name="上级工号",
                        role="dimension",
                    ),
                ],
            }
        ],
        links=[link],
    )


def test_one_to_one_self_link_is_valid():
    model = BusinessModel.model_validate(_self_link_model())
    assert model.links[0].from_ == model.links[0].to
    assert model.links[0].type.value == "one_to_one"


def test_self_link_must_match_two_fields():
    with pytest.raises(ValidationError, match="different fields"):
        BusinessModel.model_validate(
            _self_link_model(on={"from": "employee_id", "to": "employee_id"})
        )


def test_duplicate_link_between_same_fields_rejected():
    data = _minimal_model(
        entities=[
            {
                "key": "thing",
                "name": "事物",
                "key_field": "name",
                "fields": [_field()],
            },
            {
                "key": "note",
                "name": "备注",
                "key_field": "name",
                "fields": [_field()],
            },
        ],
        links=[
            {
                "key": "thing_note",
                "from": "thing",
                "to": "note",
                "type": "one_to_many",
                "on": {"from": "name", "to": "name"},
                "confidence": 0.9,
                "needs_review": False,
            },
            {
                "key": "thing_note_again",
                "from": "thing",
                "to": "note",
                "type": "one_to_many",
                "on": {"from": "name", "to": "name"},
                "confidence": 0.9,
                "needs_review": False,
            },
        ],
    )
    with pytest.raises(ValidationError, match="duplicate link"):
        BusinessModel.model_validate(data)


def test_dashboard_metric_reference_rejected():
    bad = _minimal_model()
    bad["views"] = [
        {"key": "home", "kind": "dashboard", "title": "首页", "metrics": ["ghost"]}
    ]
    with pytest.raises(ValidationError, match="unknown metric"):
        BusinessModel.model_validate(bad)
