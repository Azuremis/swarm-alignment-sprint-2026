"""Verify the released synthetic apparatus using the active Python environment."""
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
      'tools/absence-harness/tests/test_unverified_success.py'], root),
    ([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
     root / 'tools/research-workflow'),
    ([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
     root / 'tools/absence-harness'),
]
for cmd, cwd in commands:
    print('CHECK', cwd.relative_to(root), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)
print('All released-apparatus checks passed. Synthetic software checks only.')
