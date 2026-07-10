# Scoring System

## Official categories

| Key | Label | Maximum |
|---|---|---:|
| `entertainment` | Entertainment | 20 |
| `nature` | Nature | 20 |
| `value` | Value | 15 |
| `cleanliness` | Cleanliness | 15 |
| `site_size` | Site Size | 10 |
| `sentiment` | Sentiment | 10 |
| `location` | Location | 10 |

Total maximum: 100.

## Required implementation rule

The AI must never be treated as the final calculator.

The model may assess each category, but application code must:

- validate every category
- clamp or reject invalid scores according to policy
- calculate the total
- compare any model-provided total with the calculated total
- log discrepancies

## Proposed score object

```json
{
  "schema_version": "1.0",
  "scores": {
    "entertainment": 16,
    "nature": 14,
    "value": 11,
    "cleanliness": 13,
    "site_size": 8,
    "sentiment": 9,
    "location": 8
  },
  "total": 79,
  "confidence": "medium",
  "evidence": {
    "entertainment": ["Kids used the pool and jumping pillow every day."],
    "nature": ["Riverside setting with shade and birdlife."]
  },
  "warnings": []
}
```

## Rubric design requirements

Each category rubric should explain:

- what the category measures
- what evidence is relevant
- what evidence is not relevant
- examples of low, middle and high scores
- how to handle missing evidence
- how to avoid double-counting the same fact

## Important distinction

Park-level scoring and single-family scoring use the same categories and weights, but not necessarily the same evidence volume.

- Park-level scores may aggregate many reviews.
- A family review score represents one family's experience.
- The UI must label these clearly so users do not confuse an individual family score with the park's overall platform score.

## Confidence

The scoring system should support confidence or completeness metadata.

Examples:

- High: clear evidence across nearly all categories
- Medium: enough evidence to score, but some categories are thin
- Low: several categories are unsupported

Low-confidence results should be flagged for moderation rather than displayed as equally reliable.
