import unittest

from cedarhq.status import StatusError, entity_recommendation, validate_formation_transition


class StatusMachineTests(unittest.TestCase):
    def test_completed_formation_steps_require_evidence(self):
        with self.assertRaises(StatusError):
            validate_formation_transition("paid", "information_received")
        validate_formation_transition("paid", "information_received", evidence_id="evd_123")

    def test_invalid_transition_is_rejected(self):
        with self.assertRaises(StatusError):
            validate_formation_transition("paid", "state_approved", evidence_id="evd_123")

    def test_quiz_recommends_c_corp_for_venture_path(self):
        entity, reason = entity_recommendation({"venture_funding": "yes"})
        self.assertEqual(entity, "c_corp")
        self.assertIn("venture", reason.lower())

    def test_quiz_recommends_llc_for_simple_tax_path(self):
        entity, reason = entity_recommendation({"pass_through_tax": "yes", "venture_funding": "no"})
        self.assertEqual(entity, "llc")
        self.assertIn("simpler", reason.lower())


if __name__ == "__main__":
    unittest.main()

