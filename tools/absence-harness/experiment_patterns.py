"""Mesa-inspired records and paired observation comparisons; Mesa not required.

These patterns do not simulate network causality or choose a scientific protocol.
"""
from dataclasses import asdict, dataclass
import hashlib
import random

import numpy as np

from harness import make_mask, past_events, render_observer_input


@dataclass(frozen=True)
class SeedPlan:
    master: int

    def __post_init__(self):
        if type(self.master) is not int or self.master < 0:
            raise ValueError('master seed must be a nonnegative integer')

    def derive(self, namespace):
        if not isinstance(namespace, str) or not namespace:
            raise ValueError('explicit RNG namespace required')
        data = f'v1:{self.master}:{namespace}'.encode()
        return int.from_bytes(hashlib.sha256(data).digest()[:8], 'big')

    def python_rng(self, namespace):
        return random.Random(self.derive(namespace))

    def numpy_rng(self, namespace):
        return np.random.Generator(np.random.PCG64(self.derive(namespace)))

    def sklearn_seed(self, namespace):
        return self.derive(namespace) % (2 ** 32)

    def manifest(self, namespaces):
        return {'master': self.master, 'derivation': 'sha256-v1-first-64bits',
                'streams': {n: self.derive(n) for n in sorted(set(namespaces))},
                'rng_sources': {'python': 'local random.Random/MT19937',
                                'numpy': 'local Generator/PCG64',
                                'sklearn': 'explicit random_state via sklearn_seed; no stochastic estimator fitted',
                                'masking': 'existing make_mask random.Random with derived seed'},
                'scope': 'software streams only; model/provider, external frameworks and hardware determinism unverified'}


@dataclass(frozen=True)
class ScopedRecord:
    """Evaluator-only instrumentation; never passed to an observer implicitly."""
    record_id: str
    tick: int
    scope: str
    subjects: tuple[str, ...]
    kind: str
    source_event_ids: tuple[str, ...]
    count: int


def scoped_records(episode):
    events = past_events(episode)
    agents = sorted({e.sender for e in events} | {e.recipient for e in events})
    records = []
    for agent in agents:
        sent = tuple(e.event_id for e in events if e.sender == agent)
        records.append(ScopedRecord(f'agent-{len(records)}', episode.decision_tick,
                                    'agent', (agent,), 'messages_sent', sent, len(sent)))
    records.append(ScopedRecord('group-0', episode.decision_tick, 'group', tuple(agents),
                                'directed_messages', tuple(e.event_id for e in events), len(events)))
    return records


def compare_observation_conditions(episode, networks, seed_plan):
    """Cross directed edge-visibility conditions with existing full/channel/random masks.

    Networks map names to observable directed edges. Same underlying episode and
    truth are reused. This does NOT rewire the acting swarm or alter its dynamics.
    Random count matches channel-hidden count within each network condition.
    """
    events = past_events(episode)
    agents = {e.sender for e in events} | {e.recipient for e in events}
    result = []
    for name in sorted(networks):
        if not isinstance(name, str) or not name:
            raise ValueError('condition needs a name')
        edges = networks[name]
        if any(not isinstance(e, tuple) or len(e) != 2 or any(a not in agents for a in e) for e in edges):
            raise ValueError('unknown agent or malformed edge')
        if len(edges) != len(set(edges)):
            raise ValueError('duplicate edge')
        visible = [e for e in events if (e.sender, e.recipient) in edges]
        channel_visible = [e for e in visible if e.channel != 'handoff']
        namespace = f'mask:{episode.episode_id}:{name}'
        rng = seed_plan.python_rng(namespace)
        random_ids = set(rng.sample([e.event_id for e in visible], len(channel_visible)))
        for policy in ('full', 'channel', 'random'):
            retained = (visible if policy == 'full' else channel_visible if policy == 'channel'
                        else [e for e in visible if e.event_id in random_ids])
            # Reuse the canonical allowlist, never serialize evaluator fields.
            payload = render_observer_input(episode, make_mask(episode, 'full'))
            keep = {e.event_id for e in retained}
            payload['events'] = [r for e, r in zip(events, payload['events']) if e.event_id in keep]
            result.append({'observer_input': payload,
                           'evaluator_audit': {'condition': name, 'policy': policy,
                                               'episode_hash': episode.hash,
                                               'retained_ids': [e.event_id for e in retained],
                                               'removed_ids': [e.event_id for e in events if e.event_id not in keep],
                                               'seed': seed_plan.derive(namespace) if policy == 'random' else None,
                                               'interpretation': 'observation-edge filter; not network intervention'}})
    return result


def records_as_dicts(episode):
    return [asdict(r) for r in scoped_records(episode)]
