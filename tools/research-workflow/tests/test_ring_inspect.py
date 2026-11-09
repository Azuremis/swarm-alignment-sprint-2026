"""Ring source/observer separation and local-only Inspect execution."""
import json
import unittest
from unittest.mock import patch
from ring_phase import execute
from relay_contract import sha
from ring_inspect import prepare, run_replay
import test_ring_phase as fixtures


class RingInspectTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        execute(self.fixture.phase, self.fixture.gate, self.fixture.invoke)
        self.review = {"manifest_sha256": sha((self.fixture.phase / "evidence-manifest.json").read_bytes()),
                       "reviewed_by": "authored fixture", "scope": "software checks only", "rights": "authored"}

    def test_observer_has_only_opaque_ids_visible_packets_and_no_truth(self):
        samples, records, source, selections = prepare(self.fixture.phase, self.review)
        self.assertEqual(len(samples), 1224)
        self.assertEqual(len(source["records"]), 96)
        self.assertEqual(len(selections), 96)
        self.assertTrue(all(not s.target and not s.metadata for s in samples))
        self.assertTrue(all("actual_matches" not in s.input and "cell-" not in s.input for s in samples))
        self.assertEqual(sum(s["distinct_comparison_available"] for s in selections), 18)
        for sample in samples:
            self.assertEqual(sha(sample.input.encode()), records[str(sample.id)]["input_sha256"])
        self.assertFalse(set(records) & set(prepare(self.fixture.phase, self.review)[1]))

    def test_real_inspect_offline_reload_and_source_denominator(self):
        root = self.fixture.root
        with patch("inspect_ai._util.appdirs.user_data_path", side_effect=lambda n: root / n / "data"), patch(
                "inspect_ai._util.appdirs.user_cache_path", side_effect=lambda n: root / n / "cache"):
            rows = run_replay(self.fixture.phase, self.review, root / "replay")
        self.assertEqual(len(rows), 1224)
        self.assertEqual({x["evidence_kind"] for x in rows}, {"synthetic_fixture"})
        self.assertEqual(len(list((root / "replay/inspect").glob("*.eval"))), 1)
        receipt = json.loads((root / "replay/receipt.json").read_text())
        self.assertEqual(receipt["remote_calls"], 0)
        self.assertEqual(len(json.loads((root / "replay/source-flow.json").read_text())["records"]), 96)

    def test_review_must_bind_manifest(self):
        with self.assertRaises(ValueError):
            prepare(self.fixture.phase, {**self.review, "manifest_sha256": "a"*64})
