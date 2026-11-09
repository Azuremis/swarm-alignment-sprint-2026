"""Released channel-factorial source-to-Inspect replay, using authored replies."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools/absence-harness'))

from channel_inspect import prepare, run_replay  # noqa: E402
from channel_phase import execute  # noqa: E402
from relay_contract import sha  # noqa: E402
import test_channel_phase as fixtures  # noqa: E402


class ChannelInspectTests(unittest.TestCase):
    def test_full_offline_channel_replay_preserves_denominators(self):
        fixture = fixtures.RingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        execute(fixture.phase, fixture.gate, fixture.invoke)
        review = {
            'manifest_sha256': sha((fixture.phase / 'evidence-manifest.json').read_bytes()),
            'reviewed_by': 'authored release regression fixture',
            'scope': 'software checks only',
            'rights': 'authored',
        }
        samples, records, source, selections = prepare(fixture.phase, review)
        self.assertEqual(len(source['records']), 192)
        self.assertEqual(len(selections), 192)
        self.assertEqual(len(samples), 2448)
        self.assertEqual(len(records), 2448)
        self.assertTrue(all(not sample.target and not sample.metadata for sample in samples))
        with patch('inspect_ai._util.appdirs.user_data_path',
                   side_effect=lambda n: fixture.root / n / 'data'), patch(
                'inspect_ai._util.appdirs.user_cache_path',
                side_effect=lambda n: fixture.root / n / 'cache'):
            rows = run_replay(fixture.phase, review, fixture.root / 'replay')
        self.assertEqual(len(rows), 2448)
        self.assertEqual({row['evidence_kind'] for row in rows}, {'synthetic_fixture'})
        receipt = json.loads((fixture.root / 'replay/receipt.json').read_text())
        self.assertTrue(receipt)
