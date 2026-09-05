import tempfile
import unittest
from pathlib import Path

from cedarhq.db import migrate, transaction
from cedarhq.services import create_user, ensure_reference_data, save_onboarding
from cedarhq.workspaces import (
    analytics_context,
    ask_assistant,
    assistant_context,
    bookkeeping_context,
    connect_sandbox_commerce,
    connect_sandbox_finance,
    create_tax_filing,
    save_tax_questionnaire,
    tax_action,
    taxes_context,
    update_transaction,
)


class ProductWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        migrate(self.db_path)
        with transaction(self.db_path) as conn:
            ensure_reference_data(conn)
            user = create_user(conn, "workspace@example.com", "Password123", "Workspace Founder", verified=True)
            save_onboarding(
                conn,
                user["id"],
                {
                    "entity_type": "c_corp",
                    "state_code": "DE",
                    "name_choice_1": "Workspace Sample Inc",
                    "business_purpose": "Commerce software.",
                    "founder_full_name": "Workspace Founder",
                    "founder_email": "workspace@example.com",
                    "address_line1": "1 Market Street",
                    "city": "Wilmington",
                    "country": "United States",
                    "plan_slug": "complete_back_office",
                },
            )
            company = conn.execute("SELECT * FROM companies WHERE owner_user_id = ?", (user["id"],)).fetchone()
            self.user_id = user["id"]
            self.company_id = company["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_bookkeeping_connection_and_reconciliation(self):
        with transaction(self.db_path) as conn:
            connect_sandbox_finance(conn, self.company_id, self.user_id)
            context = bookkeeping_context(conn, self.company_id)
            self.assertEqual(len(context["accounts"]), 2)
            self.assertGreater(len(context["transactions"]), 5)
            uncategorized = next(row for row in context["transactions"] if row["status"] == "uncategorized")
            update_transaction(conn, self.company_id, uncategorized["id"], self.user_id, "Travel", True)
            updated = conn.execute("SELECT * FROM bookkeeping_transactions WHERE id = ?", (uncategorized["id"],)).fetchone()
            self.assertEqual(updated["status"], "reconciled")
            self.assertEqual(updated["category"], "Travel")

    def test_tax_workflow_requires_answers_and_creates_submission_evidence(self):
        with transaction(self.db_path) as conn:
            filing = create_tax_filing(conn, self.company_id, self.user_id, "1120", 2025)
            context = taxes_context(conn, self.company_id)
            payload = {f"answer_{answer['question_key']}": "Confirmed" for answer in context["answers"]}
            payload.update({f"document_{document['id']}": "yes" for document in context["documents"]})
            save_tax_questionnaire(conn, self.company_id, filing["id"], self.user_id, payload)
            prepared = tax_action(conn, self.company_id, filing["id"], self.user_id, "submit_questionnaire")
            self.assertEqual(prepared["status"], "preparation")
            reviewed = tax_action(conn, self.company_id, filing["id"], self.user_id, "mark_review_ready", is_ops=True)
            self.assertEqual(reviewed["status"], "founder_review")
            tax_action(conn, self.company_id, filing["id"], self.user_id, "approve_return")
            tax_action(conn, self.company_id, filing["id"], self.user_id, "sign_return")
            submitted = tax_action(conn, self.company_id, filing["id"], self.user_id, "sandbox_submit", is_ops=True)
            self.assertEqual(submitted["status"], "submitted")
            self.assertTrue(submitted["receipt_id"])
            self.assertTrue(submitted["evidence_id"])

    def test_analytics_and_assistant_are_database_backed(self):
        with transaction(self.db_path) as conn:
            connect_sandbox_commerce(conn, self.company_id, self.user_id, "shopify")
            connect_sandbox_commerce(conn, self.company_id, self.user_id, "amazon")
            analytics = analytics_context(conn, self.company_id)
            self.assertEqual(len(analytics["connections"]), 2)
            self.assertGreater(analytics["totals"]["orders_count"], 0)
            ask_assistant(conn, self.company_id, self.user_id, "Please submit my tax filing")
            assistant = assistant_context(conn, self.company_id, self.user_id)
            self.assertEqual(len(assistant["messages"]), 2)
            self.assertEqual(assistant["actions"][0]["status"], "pending_approval")
            self.assertIn("did not perform", assistant["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()
