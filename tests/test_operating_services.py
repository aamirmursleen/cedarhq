import tempfile
import unittest
from pathlib import Path

from cedarhq.db import migrate, transaction
from cedarhq.expansion import (
    choose_mail_address,
    connect_sales_tax_sandbox,
    mailroom_context,
    ops_process_mail_action,
    partner_application_action,
    partners_context,
    rewards_context,
    sales_tax_action,
    sales_tax_context,
    save_discovery_profile,
    start_foreign_qualification,
    registered_agent_context,
)
from cedarhq.services import create_checkout_and_order, create_user, ensure_reference_data, save_onboarding


class OperatingServicesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        migrate(self.db_path)
        with transaction(self.db_path) as conn:
            ensure_reference_data(conn)
            user = create_user(conn, "ops-services@example.com", "Password123", "Ops Services", verified=True)
            save_onboarding(
                conn,
                user["id"],
                {
                    "entity_type": "c_corp",
                    "state_code": "DE",
                    "name_choice_1": "Operating Services Inc",
                    "business_purpose": "Commerce back office software.",
                    "founder_full_name": "Ops Services",
                    "founder_email": "ops-services@example.com",
                    "address_line1": "1 Market Street",
                    "city": "Wilmington",
                    "country": "United States",
                    "plan_slug": "complete_back_office",
                },
            )
            order = create_checkout_and_order(conn, user, "http://127.0.0.1:8088")
            self.user_id = user["id"]
            self.company_id = order["company_id"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_mailroom_request_and_staff_processing_create_scan_document(self):
        with transaction(self.db_path) as conn:
            ctx = mailroom_context(conn, self.company_id)
            choose_mail_address(conn, self.company_id, ctx["addresses"][0]["id"], self.user_id)
            ctx = mailroom_context(conn, self.company_id)
            item = ctx["items"][0]
            self.assertEqual(item["status"], "received")

            from cedarhq.expansion import request_mail_action

            request_mail_action(conn, self.company_id, item["id"], self.user_id, "scan")
            ops_process_mail_action(conn, item["id"], self.user_id)
            updated = conn.execute("SELECT * FROM mail_items WHERE id = ?", (item["id"],)).fetchone()
            self.assertEqual(updated["status"], "scanned")
            self.assertTrue(updated["scan_document_id"])

    def test_registered_agent_and_foreign_qualification_are_database_backed(self):
        with transaction(self.db_path) as conn:
            ctx = registered_agent_context(conn, self.company_id)
            self.assertEqual(ctx["services"][0]["state_code"], "DE")
            self.assertTrue(ctx["services"][0]["evidence_id"])
            start_foreign_qualification(conn, self.company_id, self.user_id, "CA", "Hiring employees.")
            ctx = registered_agent_context(conn, self.company_id)
            self.assertEqual(ctx["qualifications"][0]["status"], "questionnaire")

    def test_partner_application_preserves_external_approval_disclaimer(self):
        with transaction(self.db_path) as conn:
            ctx = partners_context(conn, self.company_id)
            application = next(row for row in ctx["applications"] if row["partner_type"] == "banking")
            partner_application_action(conn, self.company_id, application["id"], self.user_id, "complete_checklist")
            partner_application_action(conn, self.company_id, application["id"], self.user_id, "send_sandbox")
            updated = conn.execute("SELECT * FROM partner_applications WHERE id = ?", (application["id"],)).fetchone()
            self.assertEqual(updated["status"], "sent_to_partner")
            self.assertTrue(updated["evidence_id"])
            self.assertIn("never guarantees approval", updated["disclaimer"])

    def test_rewards_discovery_requires_explicit_opt_in(self):
        with transaction(self.db_path) as conn:
            ctx = rewards_context(conn, self.company_id)
            self.assertEqual(ctx["profile"]["permission_to_share"], 0)
            save_discovery_profile(
                conn,
                self.company_id,
                self.user_id,
                {
                    "founder_headline": "Building operational software",
                    "target_investor": "Seed investors",
                    "permission_to_share": "yes",
                },
            )
            ctx = rewards_context(conn, self.company_id)
            self.assertEqual(ctx["profile"]["status"], "opted_in")
            self.assertGreaterEqual(len(ctx["rewards"]), 4)

    def test_sales_tax_connector_and_return_statuses_are_gated(self):
        with transaction(self.db_path) as conn:
            connect_sales_tax_sandbox(conn, self.company_id, self.user_id)
            ctx = sales_tax_context(conn, self.company_id)
            self.assertEqual(ctx["account"]["status"], "connected")
            state_return = next(row for row in ctx["returns"] if row["status"] == "registration_required")
            sales_tax_action(conn, self.company_id, state_return["id"], self.user_id, "mark_registered")
            sales_tax_action(conn, self.company_id, state_return["id"], self.user_id, "prepare_return")
            sales_tax_action(conn, self.company_id, state_return["id"], self.user_id, "send_for_approval")
            sales_tax_action(conn, self.company_id, state_return["id"], self.user_id, "approve_to_file")
            submitted = sales_tax_action(conn, self.company_id, state_return["id"], self.user_id, "sandbox_submit")
            self.assertEqual(submitted, None)
            updated = conn.execute("SELECT * FROM sales_tax_returns WHERE id = ?", (state_return["id"],)).fetchone()
            self.assertEqual(updated["status"], "submitted")
            self.assertTrue(updated["receipt_id"])
            self.assertTrue(updated["evidence_id"])


if __name__ == "__main__":
    unittest.main()
