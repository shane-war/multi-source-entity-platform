import copy
import unittest
from entity_platform import adapt, connect, ingest, reconcile


def record(source="crm", revision=1, name="Juniper"):
    return dict(source=source, external_id=source+"-1", supplier_id="SUP-1",
                revision=revision, fields=dict(name=name, city=None))


class EntityTests(unittest.TestCase):
    def setUp(self):
        self.db = connect(":memory:")
        self.addCleanup(self.db.close)

    def test_distinct_upstream_contracts_map_to_common_fields(self):
        crm = adapt("crm", dict(account_id="A1", supplier_id="S1", revision=1, display_name="Demo"))
        registry = adapt("registry", dict(registration_id="R1", supplier_id="S1", revision=1, legal_name="Demo"))
        self.assertEqual(crm["fields"], registry["fields"])
        self.assertNotEqual(crm["external_id"], registry["external_id"])

    def test_authority_and_provenance_survive_conflict(self):
        ingest(self.db, [record(), record("registry", name="Juniper Supply")])
        field = reconcile(self.db, "SUP-1")["attributes"]["name"]
        self.assertEqual(field["selected"]["value"], "Juniper Supply")
        self.assertTrue(field["conflict"])
        self.assertEqual(len(field["candidates"]), 2)

    def test_null_authority_falls_back(self):
        high = record("registry")
        high["fields"]["name"] = None
        ingest(self.db, [record(), high])
        self.assertEqual(reconcile(self.db, "SUP-1")["attributes"]["name"]["selected"]["source"], "crm")

    def test_replay_and_stale_input_do_not_duplicate_audit(self):
        ingest(self.db, [record(revision=2)])
        self.assertEqual(ingest(self.db, [record(revision=2)])["unchanged"], 1)
        self.assertEqual(ingest(self.db, [record()])["stale"], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM ingest_events").fetchone()[0], 1)

    def test_conflicting_revision_rolls_back_whole_batch(self):
        ingest(self.db, [record()])
        with self.assertRaises(ValueError):
            ingest(self.db, [record("partner"), record(name="Changed")])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM source_records").fetchone()[0], 1)

    def test_identity_cannot_silently_change(self):
        ingest(self.db, [record()])
        changed = record(revision=2)
        changed["supplier_id"] = "SUP-2"
        with self.assertRaises(ValueError):
            ingest(self.db, [changed])

    def test_invalid_batch_has_no_partial_writes(self):
        with self.assertRaises(ValueError):
            ingest(self.db, [record(), record("unknown")])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM source_records").fetchone()[0], 0)

    def test_arrival_order_does_not_change_result(self):
        rows = [record(), record("registry")]
        ingest(self.db, rows)
        other = connect(":memory:")
        self.addCleanup(other.close)
        ingest(other, reversed(copy.deepcopy(rows)))
        self.assertEqual(reconcile(self.db, "SUP-1"), reconcile(other, "SUP-1"))
