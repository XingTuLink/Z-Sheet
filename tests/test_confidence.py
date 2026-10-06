from backend.domain.confidence import (
    ConfidenceLevel,
    confidence_level,
    default_needs_review,
)


def test_confidence_bands_are_frozen():
    assert confidence_level(0.98) is ConfidenceLevel.HIGH
    assert confidence_level(0.85) is ConfidenceLevel.HIGH
    assert confidence_level(0.849) is ConfidenceLevel.MEDIUM
    assert confidence_level(0.61) is ConfidenceLevel.MEDIUM
    assert confidence_level(0.60) is ConfidenceLevel.MEDIUM
    assert confidence_level(0.59) is ConfidenceLevel.LOW


def test_review_flag_derivation():
    assert default_needs_review(0.85) is False
    assert default_needs_review(0.61) is True
    assert default_needs_review(0.10) is True
