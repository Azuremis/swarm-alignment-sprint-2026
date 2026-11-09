"""Executed local storage checks; no model-generated traces or findings."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from artifact_capture import ArtifactStore, MAX_ARTIFACT_BYTES, MAX_KEYS, export_snapshot
from empirical import ingest, sha
from test_empirical import fixture, write


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'state.db'
        self.store = ArtifactStore(self.db, run_id='run1')

    def export(self, tick):
        return export_snapshot(self.db, run_id='run1', decision_tick=tick)

    def test_separate_process_reads_actual_bytes_not_receipt(self):
        self.store.write('output', b'wrong')
        tick = self.store.seal()
        output = self.root / 'captured.json'
        subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'artifact_capture.py'),
                        str(self.db), str(output), '--run-id', 'run1', '--decision-tick', str(tick)], check=True)
        trace, _, review = [json.loads(p.read_bytes()) for p in fixture(self.root)]
        snapshot = json.loads(output.read_bytes())
        case = ingest(*write(self.root, trace, snapshot, review))
        self.assertTrue(case['episode'].adverse_state_at_decision)
        self.assertEqual(case['actual_artifact_sha256'], sha(b'wrong'))
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)

    def test_unsealed_wrong_run_and_late_write_rejected(self):
        with self.assertRaises(ValueError):
            self.export(1)
        tick = self.store.seal()
        for run, cutoff in [('other', tick), ('run1', tick + 1)]:
            with self.assertRaises(ValueError):
                export_snapshot(self.db, run_id=run, decision_tick=cutoff)
        with self.assertRaises(ValueError):
            self.store.write('output', b'late')
        with self.assertRaises(ValueError):
            self.store.seal()
        self.assertEqual(self.export(tick)['artifacts'], {})

    def test_write_then_tool_exception_keeps_actual_state(self):
        try:
            self.store.write('output', b'expected')
            raise RuntimeError('tool failed after write')
        except RuntimeError:
            pass
        state = self.export(self.store.seal())
        self.assertEqual(base64.b64decode(state['artifacts']['output']), b'expected')

    def test_bounds_and_replacement(self):
        for i in range(MAX_KEYS):
            self.store.write(str(i), b'x')
        self.store.write('0', b'replacement')
        with self.assertRaises(ValueError):
            self.store.write('overflow', b'x')
        with self.assertRaises(ValueError):
            self.store.write('0', b'x' * (MAX_ARTIFACT_BYTES + 1))
        state = self.export(self.store.seal())
        self.assertEqual(len(state['artifacts']), MAX_KEYS)
        self.assertEqual(base64.b64decode(state['artifacts']['0']), b'replacement')

    def test_concurrent_write_and_seal_have_one_serial_cutoff(self):
        def writer():
            try:
                return self.store.write('output', b'expected')
            except ValueError:
                return None
        with ThreadPoolExecutor(2) as pool:
            written, sealed = pool.submit(writer), pool.submit(self.store.seal)
        state = self.export(sealed.result())
        if written.result() is None:
            self.assertEqual(state['artifacts'], {})
        else:
            self.assertLess(written.result(), sealed.result())
            self.assertEqual(base64.b64decode(state['artifacts']['output']), b'expected')

    def test_no_overwrite_or_symlink_capture(self):
        with self.assertRaises(FileExistsError):
            ArtifactStore(self.db, run_id='run1')
        link = self.root / 'link.db'
        link.symlink_to(self.db)
        with self.assertRaises(ValueError):
            export_snapshot(link, run_id='run1', decision_tick=self.store.seal())
