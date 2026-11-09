"""Offline source-shaped Colosseum tool_events adapter. No live-run validation.

Input schema verified at Colosseum 26ad60beb4006e6101477820d4b19b43a9c86033.
Labels and projection contracts must be supplied independently by the evaluator.
"""
from dataclasses import dataclass
import json
from harness import Episode, Event, digest

SOURCE_REF = '26ad60beb4006e6101477820d4b19b43a9c86033'
REQUIRED = {'tool_name', 'agent_name', 'arguments', 'result', 'phase', 'iteration', 'planning_round'}

@dataclass(frozen=True)
class Projection:
    tool_name: str
    argument_fields: tuple[str, ...]
    result_fields: tuple[str, ...]
    channel: str


def _scalar_subset(data, keys):
    out = {}
    for key in keys:
        if key not in data:
            raise ValueError(f'missing explicitly requested field: {key}')
        value = data[key]
        if value is not None and type(value) not in (str, int, float, bool):
            raise ValueError('only scalar observation fields supported; no nested context forwarding')
        out[key] = value
    return out


def adapt_tool_events(records, *, episode_id, family_id, stratum,
                      adverse_state_at_decision, decision_index, projections):
    """Map ordered tool calls only, not board events or agent reasoning.

    tick is EXPORT LIST INDEX, not elapsed time or an interaction round.
    Unsupported tools reject the episode rather than silently disappear.
    Source JSON remains evaluator-owned; monitor receives renderer output only.
    """
    if not isinstance(records, list) or not records:
        raise ValueError('nonempty tool-event list required')
    if type(decision_index) is not int or not 0 <= decision_index < len(records):
        raise ValueError('decision index outside export')
    if type(adverse_state_at_decision) is not bool:
        raise ValueError('independent boolean outcome required')
    if len({p.tool_name for p in projections}) != len(projections):
        raise ValueError('duplicate projection')
    mapping = {p.tool_name: p for p in projections}
    events = []
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not REQUIRED <= set(record):
            raise ValueError('invalid source tool-event shape')
        if not isinstance(record['tool_name'], str) or not isinstance(record['agent_name'], str):
            raise ValueError('invalid tool/agent identifier')
        if not isinstance(record['arguments'], dict) or not isinstance(record['result'], dict):
            raise ValueError('arguments/result must be objects')
        projection = mapping.get(record['tool_name'])
        if projection is None or not projection.channel:
            raise ValueError('unreviewed tool or channel')
        payload = {'tool': record['tool_name'],
                   'arguments': _scalar_subset(record['arguments'], projection.argument_fields),
                   'result': _scalar_subset(record['result'], projection.result_fields)}
        text = json.dumps(payload, sort_keys=True, allow_nan=False)
        events.append(Event(f'tool-{index}', index, record['agent_name'],
                            'environment-tool', projection.channel, text))
    return Episode(episode_id, family_id, stratum, decision_index, tuple(events),
                   adverse_state_at_decision,
                   f'Colosseum source-shaped tool export adapter; source ref {SOURCE_REF}; input {digest(records)}; execution provenance must be verified externally')
