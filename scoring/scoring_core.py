from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_DIR / "scoring" / "schema.json"


class ScoreValidationError(ValueError):
    """Raised when a category score payload does not match the scoring schema."""


def load_schema(path: Path | None = None) -> dict[str, Any]:
    schema_path = path or SCHEMA_PATH
    return json.loads(schema_path.read_text(encoding="utf-8"))


def category_fields(schema: dict[str, Any] | None = None) -> list[str]:
    active_schema = schema or load_schema()
    return [str(cat["score_field"]) for cat in active_schema["categories"]]


def _coerce_score(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ScoreValidationError(f"{field} must be numeric, not boolean")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ScoreValidationError(f"{field} must be numeric") from exc


def validate_category_scores(
    scores: dict[str, Any],
    schema: dict[str, Any] | None = None,
) -> dict[str, float | int]:
    active_schema = schema or load_schema()
    validated: dict[str, float | int] = {}
    for cat in active_schema["categories"]:
        field = str(cat["score_field"])
        max_score = float(cat["max_score"])
        if field not in scores:
            raise ScoreValidationError(f"{field} is missing")
        score = _coerce_score(scores[field], field)
        if score < 0 or score > max_score:
            raise ScoreValidationError(f"{field} must be between 0 and {max_score:g}")
        validated[field] = int(score) if score.is_integer() else score
    return validated


def calculate_total_score(
    scores: dict[str, Any],
    schema: dict[str, Any] | None = None,
) -> int:
    active_schema = schema or load_schema()
    validated = validate_category_scores(scores, active_schema)
    total = sum(float(validated[str(cat["score_field"])]) for cat in active_schema["categories"])
    return int(math.floor(total + 0.5))


def score_band_label(total_score: int | float, schema: dict[str, Any] | None = None) -> str:
    active_schema = schema or load_schema()
    total = float(total_score)
    for band in active_schema.get("score_band_labels", []):
        if float(band["min"]) <= total <= float(band["max"]):
            return str(band["label"])
    return ""
