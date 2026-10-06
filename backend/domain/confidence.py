"""Confidence bands frozen in design doc 10.1.

high   confidence >= 0.85  -> "recognised", needs_review = False
medium 0.60 <= c < 0.85    -> "confirm suggested", needs_review = True
low    confidence <  0.60  -> "cannot determine", needs_review = True
"""

from __future__ import annotations

from enum import StrEnum

HIGH_THRESHOLD = 0.85
LOW_THRESHOLD = 0.60


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


def confidence_level(confidence: float) -> ConfidenceLevel:
    if confidence >= HIGH_THRESHOLD:
        return ConfidenceLevel.HIGH
    if confidence >= LOW_THRESHOLD:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def default_needs_review(confidence: float) -> bool:
    """Medium and low confidence inferences must enter the review queue."""
    return confidence < HIGH_THRESHOLD
