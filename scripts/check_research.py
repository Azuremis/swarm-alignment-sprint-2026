"""Verify released research code offline using mocked providers."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
commands = [
    ([sys.executable, '-m', 'ruff', 'check', '--select', 'E4,E7,E9,F',
      'tools/research-workflow', 'scripts/check_research.py',
      'tools/absence-harness/experiment_patterns.py',
      'tools/absence-harness/tests/test_experiment_patterns.py',
      'tools/absence-harness/unverified_success.py',
      'tools/absence-harness/artifact_capture.py', 'tools/absence-harness/empirical.py', 'tools/absence-harness/dated_contract.py', 'tools/absence-harness/dated_turn.py', 'tools/absence-harness/model_comparison_turn.py', 'tools/absence-harness/precedence_policy.py', 'tools/absence-harness/relay_contract.py', 'tools/absence-harness/ring_turn.py', 'tools/absence-harness/verification_state.py', 'tools/absence-harness/verification_turn.py',
      'tools/absence-harness/tests/test_unverified_success.py'], root),
    ([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
     root / 'tools/research-workflow'),
    ([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
     root / 'tools/absence-harness'),
]
for cmd, cwd in commands:
    print('CHECK', cwd.relative_to(root), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)
print('All released-code checks passed. Offline tests only; no paid model calls.')
