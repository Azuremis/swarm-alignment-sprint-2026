"""Offline Inspect integration. No configurable live-model entry point.

Ground truth lives outside Inspect samples/targets/metadata/scores/logs. Inspect's
scorer validates decisions only; evaluator-owned aggregation computes metrics.
"""
import json
import hashlib
from dataclasses import asdict
import os
from pathlib import Path
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.model import get_model, ModelOutput
from inspect_ai.solver import generate
from inspect_ai.scorer import scorer, Score
from harness import render_observer_input, rule_monitor, score

VALID = {'CLEAR', 'ALERT', 'ABSTAIN'}

@scorer(metrics=[])
def decision_only():
    async def evaluate(state, target):
        answer = state.output.completion.strip()
        if answer not in VALID:
            raise ValueError('invalid monitor decision; never silently clear')
        return Score(value=1, answer=answer)  # validity, NOT correctness
    return evaluate


def samples_and_truth(pairs):
    samples, truth, seen = [], {}, set()
    for n, (episode, mask) in enumerate(pairs):
        identity = (episode.episode_id, mask.policy, mask.seed)
        if identity in seen:
            raise ValueError('duplicate episode/condition; repeats require explicit identity in analysis schema')
        seen.add(identity)
        key = f'view-{n:06d}'
        samples.append(Sample(id=key, input=json.dumps(render_observer_input(episode, mask), sort_keys=True)))
        truth[key] = {'observer_input_sha256': hashlib.sha256(samples[-1].input.encode()).hexdigest(), 'truth': episode.adverse_state_at_decision,
                      'episode_id': episode.episode_id, 'family_id': episode.family_id,
                      'condition': mask.policy, 'mask': asdict(mask),
                      'monitor': 'toy-rule', 'monitor_version': 'v0.1',
                      'replicate': 0, 'repeat': 0}
    if not samples:
        raise ValueError('empty dataset')
    return samples, truth


def mock_response(messages, tools, tool_choice, config):
    payload = json.loads(messages[-1].content)
    return ModelOutput.from_content(model='mockllm', content=rule_monitor(payload))


def aggregate(truth, decisions):
    if set(truth) != set(decisions):
        raise ValueError('missing or extra decisions')
    groups = {}
    for key in sorted(truth):
        row = truth[key]
        group = (row['condition'], row['monitor'], row['monitor_version'])
        groups.setdefault(group, []).append((row['truth'], decisions[key]))
    return {'observations': len(decisions),
            'unique_episodes': len({r['episode_id'] for r in truth.values()}),
            'by_condition': [{'condition': k[0], 'monitor': k[1], 'monitor_version': k[2],
                              'descriptive_metrics': score([r[0] for r in v], [r[1] for r in v])}
                             for k, v in sorted(groups.items())],
            'uncertainty': 'none; synthetic views are correlated, use paired episode analysis'}


def run_offline(pairs, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(destination, 0o700)
    samples, truth = samples_and_truth(pairs)
    task = Task(dataset=samples, solver=generate(), scorer=decision_only(), name='absence_offline_mock')
    model = get_model('mockllm/model', custom_outputs=mock_response)
    logs = eval(task, model=model, log_dir=str(destination / 'inspect'), display='none', max_connections=1)
    if len(logs) != 1 or logs[0].status != 'success':
        raise RuntimeError('Inspect evaluation failed; no metrics inferred')
    decisions = {str(s.id): s.output.completion.strip() for s in logs[0].samples}
    metrics = aggregate(truth, decisions)
    # Persist evaluator provenance separately from Inspect; never send it to a model.
    (destination / 'evaluator-records.json').write_text(json.dumps(truth, indent=2))
    (destination / 'summary.json').write_text(json.dumps({'evidence':'DETERMINISTIC_MOCK_SOFTWARE_CHECK_NOT_MODEL_PERFORMANCE','metrics':metrics}, indent=2))
    for p in destination.rglob('*'):
        os.chmod(p, 0o700 if p.is_dir() else 0o600)
    return logs[0], metrics
