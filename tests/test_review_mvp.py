import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import scripts.review_mvp as review_mvp
from scripts.review_mvp import build_public_review, prepare_review, publish_payload, report_html, score_review, submit_for_moderation


def sample_payload():
    return {
        "park_name": "Example Holiday Park",
        "park_town": "Noosa",
        "park_state": "QLD",
        "stay_month": "2026-07",
        "nights": 4,
        "accommodation_type": "Powered site",
        "family_name": "McCarthy",
        "traveller_count": 4,
        "travellers": [
            {"first_name": "Pat", "description": "Dad", "age": 38, "gender": "Male"},
            {"first_name": "Sam", "description": "Mum", "age": 37, "gender": "Female"},
            {"first_name": "A", "description": "Daughter", "age": 8, "gender": "Female"},
            {"first_name": "B", "description": "Son", "age": 5, "gender": "Male"},
        ],
        "overall_holiday": "Great",
        "biggest_impact": "The holiday park",
        "holiday_story": (
            "We had a great family stay with plenty for the kids to do around the pool, playground "
            "and scooter paths. The amenities were clean, the site was comfortable, the location was "
            "easy for beach trips and shops, and it felt like good value for the school holiday price."
        ),
        "age_fit": "Enough",
        "kids_activities": ["Pool or splash play", "Playground", "Bikes or scooters"],
        "kids_activity_comment": "The kids used the pool and playground every day.",
        "setting_types": ["Beach or coastal", "Green and shady"],
        "nature_added": "Somewhat",
        "cleanliness": "Good",
        "cleanliness_comment": "Amenities and grounds were clean.",
        "site_size": "Comfortable",
        "site_comment": "Enough room for van, awning and car.",
        "value_feel": "Good value",
        "value_comment": "Worth it for the facilities.",
        "location_fit": "Very convenient",
        "location_comment": "Close to the beach and shops.",
        "stay_again": "Probably",
        "recommend": "Definitely",
        "best_part": "The kids could move between the pool, playground and bikes without getting bored.",
        "could_be_better": "A little more shade near the pool would help.",
        "before_booking_tip": "Bring bikes or scooters.",
        "one_sentence_summary": "A relaxed family stay with enough to keep primary school kids happy.",
        "reviewer_email": "private@example.com",
        "consent": {"public_review": True, "holiday_report": True, "privacy": True},
    }


class ReviewMvpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.old_private = review_mvp.PRIVATE_REVIEWS_DIR
        self.old_public = review_mvp.PUBLIC_REVIEWS_DIR
        root = Path(self.tmp.name)
        review_mvp.PRIVATE_REVIEWS_DIR = root / "private"
        review_mvp.PUBLIC_REVIEWS_DIR = root / "public"

    def tearDown(self):
        review_mvp.PRIVATE_REVIEWS_DIR = self.old_private
        review_mvp.PUBLIC_REVIEWS_DIR = self.old_public
        self.tmp.cleanup()

    def test_score_review_returns_valid_total(self):
        score = score_review(sample_payload())
        self.assertEqual(score["total_score"], sum(score["scores"].values()))
        self.assertGreater(score["total_score"], 0)

    def test_prepare_review_excludes_private_email_from_public_review(self):
        prepared = prepare_review(sample_payload())
        public_review = prepared["public_review"]
        self.assertNotIn("reviewer_email", public_review)
        self.assertNotIn("private@example.com", report_html(public_review))

    def test_report_distinguishes_family_score_from_platform_score(self):
        prepared = prepare_review(sample_payload())
        html = report_html(prepared["public_review"])
        self.assertIn("one family's experience", html)
        self.assertIn("not the park's overall platform score", html)

    def test_publish_writes_public_html_without_private_email(self):
        prepared = prepare_review(sample_payload())
        result = publish_payload({"public_review": prepared["public_review"]})
        self.assertTrue(result["url"].startswith("/reviews/"))
        published = next(review_mvp.PUBLIC_REVIEWS_DIR.glob("*.html"))
        html = published.read_text(encoding="utf-8")
        self.assertIn("Family Holiday Report", html)
        self.assertNotIn("private@example.com", html)

    def test_public_review_uses_family_composition_without_email(self):
        payload = sample_payload()
        score = score_review(payload)
        public_review = build_public_review(payload, score)
        self.assertIn("Dad, 38", public_review["family_composition"])
        self.assertNotIn("private@example.com", str(public_review))

    def test_submit_for_moderation_does_not_generate_public_report(self):
        result = submit_for_moderation(sample_payload())
        self.assertEqual(result["status"], "submitted_for_moderation")
        self.assertTrue(next(review_mvp.PRIVATE_REVIEWS_DIR.glob("*.json")).exists())
        self.assertFalse(review_mvp.PUBLIC_REVIEWS_DIR.exists())


if __name__ == "__main__":
    unittest.main()
