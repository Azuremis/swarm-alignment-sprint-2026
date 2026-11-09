"""Generate mechanical smoke artifacts only. No network and no model adapter."""
from dataclasses import asdict
import argparse
import json
from pathlib import Path
from hashlib import sha256
from harness import fixtures, paired_masks, render_observer_input, rule_monitor, score, split_manifest, digest


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows))


def run(destination):
    destination.mkdir(parents=True, exist_ok=False)  # do not overwrite evidence
    observer = destination / 'observer'
    evaluator = destination / 'evaluator'
    observer.mkdir()
    evaluator.mkdir()
    episodes = fixtures()
    private_masks, inputs, outputs, run_rows = [], [], [], []
    by_policy = {}
    for episode in episodes:
        for mask in paired_masks(episode, seed=17):
            view_id = f'opaque-view-{len(inputs):06d}'  # lookup ID, not a hidden-state hash
            payload = render_observer_input(episode, mask)
            private_masks.append({'view_id': view_id, **asdict(mask)})
            inputs.append({'view_id': view_id, 'input': payload, 'input_hash': digest(payload)})
            decision = rule_monitor(payload)
            outputs.append({'view_id': view_id, 'monitor': 'toy-rule-v0.1', 'decision': decision,
                            'evidence_kind': 'deterministic fixture output; NOT LLM response'})
            run_rows.append({'view_id': view_id, 'episode_id': episode.episode_id,
                             'policy': mask.policy, 'status': 'local_success', 'calls': 0,
                             'network_calls': 0, 'measured_api_cost': 0,
                             'retained_events': len(mask.retained_ids),
                             'visible_characters': sum(len(e['text']) for e in payload['events']),
                             'token_count': None})
            by_policy.setdefault(mask.policy, []).append((episode.adverse_state_at_decision, decision))
    write_jsonl(evaluator / 'episodes.jsonl', [asdict(e) for e in episodes])
    write_jsonl(evaluator / 'masks.jsonl', private_masks)
    write_jsonl(observer / 'inputs.jsonl', inputs)
    write_jsonl(observer / 'outputs.jsonl', outputs)
    write_jsonl(evaluator / 'runs.jsonl', run_rows)
    write_json(evaluator / 'split_manifest.json', split_manifest(episodes, {
        'fixture-family-0': 'development', 'fixture-family-1': 'calibration',
        'fixture-family-2': 'held_out'}))
    write_json(destination / 'smoke_summary.json', {
        'status': 'SOFTWARE_SMOKE_ONLY_NOT_SCIENTIFIC_RESULTS',
        'protocol': 'PROVISIONAL_NOT_FROZEN', 'episodes': len(episodes),
        'model_evaluations': 0, 'rule_evaluations': len(outputs),
        'warning': 'Fixtures authored to exercise edge cases. Not research-held-out data or population estimates.',
        'toy_metrics': {k: score([r[0] for r in v], [r[1] for r in v]) for k, v in by_policy.items()}})
    files = {str(p.relative_to(destination)): sha256(p.read_bytes()).hexdigest()
             for p in sorted(destination.rglob('*')) if p.is_file()}
    write_json(destination / 'artifact_manifest.json', {'sha256': files})
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(run(args.destination))
