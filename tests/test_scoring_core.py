import unittest

from scoring.scoring_core import ScoreValidationError, calculate_total_score, load_schema, validate_category_scores


VALID_SCORES = {
    "entertainment_score": 16,
    "nature_score": 18,
    "value_score": 12,
    "cleanliness_score": 13,
    "site_size_score": 8,
    "sentiment_score": 9,
    "location_score": 8,
}


class ScoringCoreTest(unittest.TestCase):
    def test_schema_totals_100(self):
        schema = load_schema()
        self.assertEqual(sum(cat["max_score"] for cat in schema["categories"]), 100)

    def test_total_is_sum_of_valid_categories(self):
        self.assertEqual(calculate_total_score(VALID_SCORES), 84)

    def test_missing_score_rejected(self):
        scores = dict(VALID_SCORES)
        del scores["nature_score"]
        with self.assertRaises(ScoreValidationError):
            validate_category_scores(scores)

    def test_out_of_range_score_rejected(self):
        scores = dict(VALID_SCORES)
        scores["site_size_score"] = 11
        with self.assertRaises(ScoreValidationError):
            validate_category_scores(scores)

    def test_non_numeric_score_rejected(self):
        scores = dict(VALID_SCORES)
        scores["value_score"] = "great"
        with self.assertRaises(ScoreValidationError):
            validate_category_scores(scores)


if __name__ == "__main__":
    unittest.main()
