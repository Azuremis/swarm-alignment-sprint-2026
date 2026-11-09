"""Offline saved-Inspect -> evaluator-only schema-v2 paired analysis bridge."""
import json
import os
from pathlib import Path

from inspect_ai.log import read_eval_log
from workflow import paired_analysis, run, sha, validate


def load_saved(evaluator_path, inspect_path, split_path):
    """Require explicit reviewed synthetic family assignments; never infer splits."""
    evaluator = json.loads(Path(evaluator_path).read_text())
    assignment = json.loads(Path(split_path).read_text())
    if (set(assignment) != {'reviewed_by', 'purpose', 'family_splits'}
            or not isinstance(assignment['reviewed_by'], str)
            or not assignment['reviewed_by'].strip()
            or assignment['purpose'] != 'synthetic_software_test_only'):
        raise ValueError('explicit reviewed synthetic-only split assignment required')
    splits = assignment['family_splits']
    if not isinstance(evaluator, dict) or not evaluator:
        raise ValueError('empty/invalid evaluator records')
    families = {r['family_id'] for r in evaluator.values()}
    if not isinstance(splits, dict) or set(splits) != families:
        raise ValueError('assign exactly the observed families; no silent defaults')
    log = read_eval_log(str(inspect_path))
    if log.status != 'success' or not log.eval.model.startswith('mockllm/'):
        raise ValueError('only successful offline mock Inspect runs supported')
    decisions = {}
    for sample in log.samples or []:
        key = str(sample.id)
        if key in decisions:
            raise ValueError('duplicate Inspect sample ID')
        if sample.target or sample.metadata:
            raise ValueError('unexpected Inspect target/metadata at evaluator boundary')
        if key not in evaluator or not isinstance(sample.input, str):
            raise ValueError('missing evaluator record or non-text input')
        if evaluator[key].get('observer_input_sha256') != sha(sample.input.encode()):
            raise ValueError('Inspect/evaluator input provenance mismatch; regenerate old fixture run')
        answer = sample.output.completion.strip()
        if answer not in {'CLEAR', 'ALERT', 'ABSTAIN'}:
            raise ValueError('invalid decision')
        decisions[key] = answer
    if set(decisions) != set(evaluator):
        raise ValueError('saved Inspect/evaluator IDs do not match exactly')
    rows = []
    for key in sorted(evaluator):
        record = evaluator[key]
        if record['mask']['policy'] != record['condition']:
            raise ValueError('mask/condition provenance mismatch')
        rows.append({'episode_id': record['episode_id'], 'family_id': record['family_id'],
                     'split': splits[record['family_id']], 'truth': record['truth'],
                     'decision': decisions[key], 'probability': None,
                     'evidence_kind': 'synthetic_fixture', 'condition': record['condition'],
                     'mask': record['mask']['policy'] + '-v1', 'monitor': record['monitor'],
                     'monitor_version': record['monitor_version'],
                     'replicate': record['replicate'], 'repeat': record['repeat']})
    return validate(rows)


def analyze_saved(evaluator_path, inspect_path, split_path, comparison_path, destination, repo):
    frame = load_saved(evaluator_path, inspect_path, split_path)
    comparison = json.loads(Path(comparison_path).read_text())
    if set(comparison) != {'left', 'right', 'endpoint', 'split'}:
        raise ValueError('explicit comparison and split required')
    selected = frame[frame['split'] == comparison['split']]
    contrast = paired_analysis(selected, comparison['left'], comparison['right'], endpoint=comparison['endpoint'])
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(destination, 0o700)
    rows = destination / 'evaluator-analysis-rows.jsonl'
    rows.write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in frame.to_dict('records')))
    rows.chmod(0o600)
    run(rows, destination / 'analysis', repo)
    (destination / 'paired-result.json').write_text(json.dumps(contrast, indent=2, sort_keys=True))
    receipt = {'purpose': 'SYNTHETIC_SOFTWARE_CHECK_NOT_EMPIRICAL_RESULTS',
               'inputs_sha256': {name: sha(Path(path).read_bytes()) for name, path in
                                 [('evaluator_records', evaluator_path), ('inspect_log', inspect_path),
                                  ('split_assignments', split_path), ('comparison', comparison_path)]},
               'split_assignment': json.loads(Path(split_path).read_text()),
               'comparison': comparison,
               'paired_result_sha256': sha((destination / 'paired-result.json').read_bytes()),
               'privacy': 'evaluator artifacts only; never pass these rows or labels to a monitor'}
    (destination / 'bridge-receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True))
    for p in destination.iterdir():
        if p.is_file():
            p.chmod(0o600)
    return contrast
