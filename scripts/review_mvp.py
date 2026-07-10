from __future__ import annotations

import html
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scoring.scoring_core import calculate_total_score, load_schema, score_band_label, validate_category_scores


PROJECT_DIR = Path(__file__).resolve().parents[1]
PRIVATE_REVIEWS_DIR = PROJECT_DIR / "local-review-data"
PUBLIC_REVIEWS_DIR = PROJECT_DIR / "public" / "reviews"


CHOICE_SCORES = {
    "overall": {
        "Amazing": 10,
        "Great": 8,
        "Mixed": 5,
        "Disappointing": 2,
        "Never again": 0,
    },
    "age_fit": {
        "More than enough": 20,
        "Enough": 16,
        "Some": 11,
        "Not much": 5,
        "Nothing useful": 0,
        "Not travelling with kids": 11,
        "They loved it": 20,
        "They enjoyed it": 16,
        "It was okay": 11,
        "They barely used it": 5,
        "There was nothing for them": 0,
    },
    "nature_added": {
        "A lot": 20,
        "Somewhat": 15,
        "Neutral": 10,
        "Not really": 5,
        "Made it worse": 0,
        "Beautiful": 20,
        "Good": 15,
        "Fine": 10,
        "Plain": 5,
        "Poor": 0,
    },
    "cleanliness": {
        "Excellent": 15,
        "Good": 12,
        "Average": 8,
        "Poor": 4,
        "Unacceptable": 0,
    },
    "site_size": {
        "Spacious": 10,
        "Comfortable": 8,
        "Average": 6,
        "Tight": 3,
        "Difficult access": 2,
        "Not applicable": 6,
    },
    "value": {
        "Excellent value": 15,
        "Good value": 12,
        "Fair": 8,
        "Poor value": 4,
        "Not sure": 8,
    },
    "location": {
        "Very convenient": 10,
        "Good": 8,
        "Okay": 6,
        "Inconvenient": 3,
        "Wrong fit": 1,
    },
    "stay_again": {
        "Definitely": 10,
        "Probably": 8,
        "Maybe": 5,
        "Probably not": 2,
        "Definitely not": 0,
    },
    "recommend": {
        "Definitely": 10,
        "Probably": 8,
        "Maybe": 5,
        "Probably not": 2,
        "Definitely not": 0,
    },
}


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "review"


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def clamp_text(value: Any, max_len: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_len].rstrip()


def word_count(value: Any) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", str(value or "")))


def choice_score(group: str, value: Any, default: int | float) -> int | float:
    return CHOICE_SCORES.get(group, {}).get(str(value or ""), default)


def build_family_composition(members: list[dict[str, Any]]) -> str:
    bits: list[str] = []
    for member in members:
        desc = str(member.get("description") or "").strip()
        age = str(member.get("age") or "").strip()
        if desc and age:
            bits.append(f"{desc}, {age}")
        elif desc:
            bits.append(desc)
    return ", ".join(bits)


def public_family_name(payload: dict[str, Any]) -> str:
    name = str(payload.get("family_name") or "").strip()
    if not name:
        return "A family"
    if name.lower().endswith("family"):
        return name
    return f"{name} Family"


def score_review(payload: dict[str, Any]) -> dict[str, Any]:
    schema = load_schema()
    warnings: list[str] = []

    story = str(payload.get("holiday_story") or "")
    story_words = word_count(story)
    if story_words < 35:
        warnings.append("The holiday story is short, so moderation should check score confidence.")

    biggest_impact = str(payload.get("biggest_impact") or "")
    if biggest_impact in {"Weather", "Travelling there", "Budget", "Family time", "Something else"}:
        warnings.append(f"Holiday context was strongly affected by {biggest_impact.lower()}; park score separated where possible.")

    activities = payload.get("kids_activities") or []
    if not isinstance(activities, list):
        activities = []
    entertainment_base = choice_score("age_fit", payload.get("age_fit"), 11)
    activity_bonus = min(4, len([a for a in activities if str(a).strip()]))
    entertainment_score = min(20, round(float(entertainment_base) + activity_bonus))

    setting_types = payload.get("setting_types") or []
    if not isinstance(setting_types, list):
        setting_types = []
    nature_base = choice_score("nature_added", payload.get("nature_added"), 10)
    nature_bonus = min(3, len([s for s in setting_types if str(s).strip()]))
    nature_score = min(20, round(float(nature_base) + nature_bonus))

    cleanliness_score = int(choice_score("cleanliness", payload.get("cleanliness"), 8))
    site_size_score = int(choice_score("site_size", payload.get("site_size"), 6))
    value_score = int(choice_score("value", payload.get("value_feel"), 8))
    location_score = int(choice_score("location", payload.get("location_fit"), 6))

    stay_again = float(choice_score("stay_again", payload.get("stay_again"), 5))
    recommend = float(choice_score("recommend", payload.get("recommend"), 5))
    overall = float(choice_score("overall", payload.get("overall_holiday"), 5))
    sentiment_raw = (stay_again * 0.4) + (recommend * 0.4) + (overall * 0.2)
    sentiment_score = int(round(max(0, min(10, sentiment_raw))))

    scores = {
        "entertainment_score": entertainment_score,
        "nature_score": nature_score,
        "value_score": value_score,
        "cleanliness_score": cleanliness_score,
        "site_size_score": site_size_score,
        "sentiment_score": sentiment_score,
        "location_score": location_score,
    }
    validated = validate_category_scores(scores, schema)
    total = calculate_total_score(validated, schema)

    answered = sum(
        1
        for key in [
            "holiday_story",
            "age_fit",
            "nature_added",
            "cleanliness",
            "site_size",
            "value_feel",
            "location_fit",
            "stay_again",
            "recommend",
        ]
        if payload.get(key)
    )
    confidence = "high" if answered >= 9 and story_words >= 60 else "medium" if answered >= 7 else "low"
    if confidence == "low":
        warnings.append("Low scoring confidence; hold for moderation before public release.")

    return {
        "schema_version": schema["schema_version"],
        "scores": validated,
        "total_score": total,
        "score_label": score_band_label(total, schema),
        "confidence": confidence,
        "warnings": warnings,
        "evidence": {
            "entertainment": clamp_text(payload.get("kids_activity_comment") or ", ".join(activities), 220),
            "nature": clamp_text(payload.get("nature_comment") or ", ".join(setting_types), 220),
            "cleanliness": clamp_text(payload.get("cleanliness_comment") or payload.get("cleanliness"), 220),
            "site_size": clamp_text(payload.get("site_comment") or payload.get("site_size"), 220),
            "value": clamp_text(payload.get("value_comment") or payload.get("value_feel"), 220),
            "location": clamp_text(payload.get("location_comment") or payload.get("location_fit"), 220),
            "sentiment": clamp_text(payload.get("recommend") or payload.get("stay_again"), 220),
        },
    }


def generate_summary(payload: dict[str, Any], score: dict[str, Any]) -> str:
    park = str(payload.get("park_name") or "the park").strip()
    overall = str(payload.get("overall_holiday") or "the stay").lower()
    best = clamp_text(payload.get("best_part") or payload.get("kids_activity_comment") or payload.get("holiday_story"), 120)
    watch = clamp_text(payload.get("could_be_better") or payload.get("before_booking_tip"), 120)
    if watch:
        return f"{public_family_name(payload)} had a {overall} stay at {park}, with {best or 'family time'} standing out most. Their main note for other families is: {watch}."
    return f"{public_family_name(payload)} had a {overall} stay at {park}, with {best or 'family time'} standing out most."


def pull_quote(payload: dict[str, Any]) -> str:
    candidates = [
        payload.get("one_sentence_summary"),
        payload.get("best_part"),
        payload.get("before_booking_tip"),
        payload.get("holiday_story"),
    ]
    for candidate in candidates:
        text = clamp_text(candidate, 150)
        if len(text) >= 24:
            return text
    return "A family holiday worth sharing with other parents."


def build_public_review(payload: dict[str, Any], score: dict[str, Any]) -> dict[str, Any]:
    members = payload.get("travellers") if isinstance(payload.get("travellers"), list) else []
    stay_month = str(payload.get("stay_month") or "").strip()
    return {
        "review_id": payload.get("review_id"),
        "published_at": datetime.now(timezone.utc).isoformat(),
        "family_display_name": public_family_name(payload),
        "park_name": str(payload.get("park_name") or "").strip(),
        "park_location": ", ".join(
            [p for p in [str(payload.get("park_town") or "").strip(), str(payload.get("park_state") or "").strip()] if p]
        ),
        "stay_month": stay_month,
        "nights": payload.get("nights") or "",
        "accommodation_type": payload.get("accommodation_type") or "",
        "family_composition": build_family_composition(members),
        "summary": generate_summary(payload, score),
        "pull_quote": pull_quote(payload),
        "story": clamp_text(payload.get("holiday_story"), 1200),
        "before_booking_tip": clamp_text(payload.get("before_booking_tip"), 500),
        "score": score,
        "verification_status": "unverified",
        "status": "published_local",
    }


def save_private_submission(payload: dict[str, Any], score: dict[str, Any], public_review: dict[str, Any]) -> Path:
    PRIVATE_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    review_id = str(payload["review_id"])
    path = PRIVATE_REVIEWS_DIR / f"{review_id}.json"
    data = {
        "payload": payload,
        "score": score,
        "public_review": public_review,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def submit_for_moderation(payload: dict[str, Any]) -> dict[str, Any]:
    PRIVATE_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    normalized = dict(payload)
    normalized["review_id"] = str(normalized.get("review_id") or uuid.uuid4())
    normalized["submitted_at"] = datetime.now(timezone.utc).isoformat()
    normalized["status"] = "submitted_for_moderation"
    path = PRIVATE_REVIEWS_DIR / f"{normalized['review_id']}.json"
    data = {
        "payload": normalized,
        "status": "submitted_for_moderation",
        "next_step": "Score and generate Family Holiday Report after moderation approval.",
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "review_id": normalized["review_id"],
        "status": "submitted_for_moderation",
        "message": "Review submitted for moderation.",
    }


def report_html(public_review: dict[str, Any]) -> str:
    score = public_review["score"]
    score_values = score["scores"]
    location = public_review.get("park_location") or "Location not supplied"
    badge = "Verified stay" if public_review.get("verification_status") == "verified" else "Unverified local review"
    categories = [
        ("Entertainment", "entertainment_score", 20),
        ("Nature", "nature_score", 20),
        ("Value", "value_score", 15),
        ("Cleanliness", "cleanliness_score", 15),
        ("Site Size", "site_size_score", 10),
        ("Sentiment", "sentiment_score", 10),
        ("Location", "location_score", 10),
    ]
    bars = "\n".join(
        f'<div class="cat"><span>{label}</span><strong>{score_values[field]}/{max_score}</strong></div>'
        for label, field, max_score in categories
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(public_review.get("family_display_name"))} Holiday Report | Family Holiday Parks</title>
<meta name="robots" content="noindex">
<style>
:root {{ --blue:#0072CE; --ink:#1f2933; --muted:#62717f; --line:#dde5ec; --bg:#f6f8fb; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:Inter, Arial, sans-serif; color:var(--ink); background:var(--bg); line-height:1.5; }}
main {{ width:min(760px, 100%); margin:0 auto; padding:20px; }}
.report {{ background:#fff; border:1px solid var(--line); border-radius:8px; overflow:hidden; box-shadow:0 12px 34px rgba(20,40,60,.08); }}
.hero {{ padding:28px 22px; background:#fff; border-bottom:1px solid var(--line); }}
.kicker {{ font-size:12px; text-transform:uppercase; letter-spacing:.08em; color:var(--blue); font-weight:800; }}
h1 {{ margin:8px 0 8px; font-size:32px; line-height:1.08; }}
.meta {{ color:var(--muted); font-size:15px; }}
.score {{ display:flex; align-items:flex-end; gap:14px; padding:22px; background:#0f3048; color:#fff; }}
.score strong {{ font-size:58px; line-height:.9; }}
.score span {{ font-size:15px; max-width:310px; }}
.body {{ padding:22px; display:grid; gap:18px; }}
blockquote {{ margin:0; padding:18px; border-left:4px solid var(--blue); background:#f7fbff; font-size:20px; font-weight:700; }}
.grid {{ display:grid; gap:8px; }}
.cat {{ display:flex; justify-content:space-between; gap:12px; min-height:44px; align-items:center; border-bottom:1px solid var(--line); }}
.cat strong {{ color:var(--blue); }}
.note {{ color:var(--muted); font-size:14px; }}
.brand {{ padding:18px 22px; border-top:1px solid var(--line); color:var(--muted); font-size:13px; }}
@media (max-width:520px) {{ h1 {{ font-size:27px; }} .score strong {{ font-size:48px; }} .score {{ align-items:flex-start; flex-direction:column; }} }}
</style>
</head>
<body>
<main>
  <article class="report">
    <section class="hero">
      <div class="kicker">Family Holiday Report</div>
      <h1>{esc(public_review.get("family_display_name"))}</h1>
      <div class="meta">{esc(public_review.get("park_name"))} · {esc(location)} · {esc(public_review.get("stay_month"))}</div>
    </section>
    <section class="score" aria-label="Family stay score">
      <strong>{esc(score.get("total_score"))}/100</strong>
      <span>This family scored their stay {esc(score.get("total_score"))}/100. This is one family's experience, not the park's overall platform score.</span>
    </section>
    <section class="body">
      <blockquote>{esc(public_review.get("pull_quote"))}</blockquote>
      <p>{esc(public_review.get("summary"))}</p>
      <div>
        <h2>Who travelled</h2>
        <p>{esc(public_review.get("family_composition") or "Family details kept private.")}</p>
      </div>
      <div>
        <h2>Category scores</h2>
        <div class="grid">{bars}</div>
      </div>
      <div>
        <h2>Review</h2>
        <p>{esc(public_review.get("story"))}</p>
      </div>
      <p class="note">{esc(badge)}. Reviewer email and private verification evidence are not published.</p>
    </section>
    <footer class="brand">Family Holiday Parks · Find better family holidays</footer>
  </article>
</main>
</body>
</html>
"""


def publish_review(public_review: dict[str, Any]) -> Path:
    PUBLIC_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    base = slugify(f"{public_review.get('family_display_name')} {public_review.get('park_name')}")
    review_id = str(public_review.get("review_id") or uuid.uuid4().hex[:8])
    path = PUBLIC_REVIEWS_DIR / f"{base}-{review_id[:8]}.html"
    path.write_text(report_html(public_review), encoding="utf-8")
    return path


def prepare_review(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["review_id"] = str(normalized.get("review_id") or uuid.uuid4())
    normalized["submitted_at"] = datetime.now(timezone.utc).isoformat()
    score = score_review(normalized)
    public_review = build_public_review(normalized, score)
    save_private_submission(normalized, score, public_review)
    return {"review_id": normalized["review_id"], "score": score, "public_review": public_review}


def publish_payload(payload: dict[str, Any]) -> dict[str, Any]:
    public_review = payload.get("public_review")
    if not isinstance(public_review, dict):
        raise ValueError("public_review is required")
    if not public_review.get("review_id"):
        public_review["review_id"] = str(uuid.uuid4())
    path = publish_review(public_review)
    try:
        published_path = str(path.relative_to(PROJECT_DIR)).replace("\\", "/")
    except ValueError:
        published_path = str(path)
    try:
        url = f"/{path.relative_to(PROJECT_DIR / 'public').as_posix()}"
    except ValueError:
        url = f"/reviews/{path.name}"
    return {"published_path": published_path, "url": url}
