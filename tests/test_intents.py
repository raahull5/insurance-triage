"""Unit tests for the 26-intent insurance classification taxonomy."""

import unittest
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.intents import (
    ALL_INTENTS,
    INTENT_LOOKUP,
    INTENT_PRIORITY_MAP,
    INTENT_CATEGORY_MAP,
    INTENT_ROUTING_MAP,
    IntentCategory,
    Priority,
    get_intent_info,
)


class TestIntents(unittest.TestCase):
    def test_taxonomy_size(self):
        # Must have at least 20-25 distinct intents
        self.assertGreaterEqual(len(ALL_INTENTS), 24)

    def test_categories_coverage(self):
        categories = {intent.category for intent in ALL_INTENTS}
        self.assertIn(IntentCategory.CLAIMS, categories)
        self.assertIn(IntentCategory.POLICY_COVERAGE, categories)
        self.assertIn(IntentCategory.GARAGE_ROADSIDE, categories)
        self.assertIn(IntentCategory.SUPPORT_BILLING, categories)

    def test_critical_scenarios_priorities(self):
        critical_intents = ["NEW_CLAIM", "CLAIM_DELAY", "ROADSIDE_ASSISTANCE", "COMPLAINT", "FRAUD_SUSPICION"]
        for code in critical_intents:
            info = get_intent_info(code)
            self.assertEqual(info.priority, Priority.CRITICAL)
            self.assertTrue(len(info.routing_destination) > 0)

    def test_routing_destinations(self):
        for intent in ALL_INTENTS:
            self.assertIsNotNone(intent.routing_destination)
            self.assertTrue(len(intent.routing_destination) > 0)
            self.assertIsNotNone(intent.description)

    def test_alias_resolution(self):
        self.assertEqual(get_intent_info("CLAIM_REJECTION").code, "CLAIM_REJECTION_DISPUTE")
        self.assertEqual(get_intent_info("UNKNOWN_XYZ").code, "GENERAL_QUERY")


if __name__ == "__main__":
    unittest.main()
