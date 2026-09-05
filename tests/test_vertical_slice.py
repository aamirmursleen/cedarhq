import tempfile
import unittest
from pathlib import Path

from cedarhq.db import migrate, transaction
from cedarhq.services import (
    calculate_cost,
    create_checkout_and_order,
    create_user,
    ensure_reference_data,
    get_dashboard_context,
    get_timeline,
    list_compliance,
    list_documents,
    ops_transition_order,
    save_onboarding,
)


class VerticalSliceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        migrate(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cost_review_is_itemized(self):
        with transaction(self.db_path) as conn:
            ensure_reference_data(conn)
            cost = calculate_cost(conn, "DE", "formation_only")
            labels = [line["label"] for line in cost["lines"]]
            self.assertIn("Formation Only first-year service", labels)
            self.assertTrue(any("Delaware" in label for label in labels))
            self.assertGreater(cost["first_year_cents"], cost["renewal_cents"])

    def test_signup_to_order_creates_timeline_documents_and_compliance(self):
        with transaction(self.db_path) as conn:
            ensure_reference_data(conn)
            user = create_user(conn, "founder@example.com", "Password123", "Founder One", verified=True)
            save_onboarding(
                conn,
                user["id"],
                {
                    "venture_funding": "no",
                    "pass_through_tax": "yes",
                    "entity_type": "llc",
                    "state_code": "DE",
                    "name_choice_1": "Cedar Sample LLC",
                    "business_purpose": "Software consulting and analytics.",
                    "industry": "Software",
                    "founder_full_name": "Founder One",
                    "founder_email": "founder@example.com",
                    "founder_ownership_percent": "100",
                    "address_line1": "1 Market Street",
                    "city": "San Francisco",
                    "country": "United States",
                    "plan_slug": "formation_only",
                },
            )
            order = create_checkout_and_order(conn, user, "http://127.0.0.1:8088")
            self.assertEqual(order["status"], "information_received")
            timeline = get_timeline(conn, order["id"])
            completed = [step for step in timeline if step["status"] == "completed"]
            self.assertEqual(len(completed), 1)
            self.assertTrue(completed[0]["evidence_id"])
            self.assertTrue(completed[0]["receipt_id"])
            docs = list_documents(conn, order["company_id"])
            self.assertGreaterEqual(len(docs), 2)
            compliance = list_compliance(conn, order["company_id"])
            self.assertGreaterEqual(len(compliance), 4)
            dashboard = get_dashboard_context(conn, user["id"])
            self.assertEqual(dashboard["order"]["id"], order["id"])
            self.assertEqual(dashboard["progress_percent"], 12)
            self.assertEqual(dashboard["next_step"]["step_key"], "operations_review")
            self.assertGreaterEqual(len(dashboard["documents"]), 2)
            self.assertGreaterEqual(len(dashboard["attention_items"]), 1)

    def test_ops_transitions_generate_evidence_before_completion(self):
        with transaction(self.db_path) as conn:
            ensure_reference_data(conn)
            founder = create_user(conn, "founder@example.com", "Password123", "Founder One", verified=True)
            staff = create_user(conn, "ops@example.com", "Password123", "Ops User", role="staff", verified=True)
            save_onboarding(
                conn,
                founder["id"],
                {
                    "entity_type": "c_corp",
                    "state_code": "DE",
                    "name_choice_1": "Evidence Sample Inc",
                    "business_purpose": "Developer tools.",
                    "founder_full_name": "Founder One",
                    "founder_email": "founder@example.com",
                    "address_line1": "1 Market Street",
                    "city": "San Francisco",
                    "country": "United States",
                    "plan_slug": "compliance",
                },
            )
            order = create_checkout_and_order(conn, founder, "http://127.0.0.1:8088")
            updated = ops_transition_order(conn, order["id"], staff["id"], "complete_review", "Looks complete.")
            self.assertEqual(updated["status"], "operations_review")
            timeline = get_timeline(conn, order["id"])
            review = [step for step in timeline if step["step_key"] == "operations_review"][0]
            self.assertEqual(review["status"], "completed")
            self.assertTrue(review["evidence_id"])
            self.assertTrue(review["receipt_id"])


if __name__ == "__main__":
    unittest.main()
