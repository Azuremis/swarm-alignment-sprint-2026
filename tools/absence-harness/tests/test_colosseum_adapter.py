import unittest
from colosseum_adapter import adapt_tool_events, Projection
from harness import paired_masks, render_observer_input

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.records = [dict(tool_name='fixture_handoff', agent_name='A', arguments={'message':'done', 'secret':'SECRET'}, result={'accepted':True,'private_state':'SECRET'}, phase='planning', iteration=0, planning_round=0), dict(tool_name='fixture_check', agent_name='B', arguments={}, result={'status':'pending'}, phase='execution', iteration=0, planning_round=None)]
        self.projections = [Projection('fixture_handoff', ('message',), ('accepted',), 'handoff'), Projection('fixture_check', (), ('status',), 'public')]
    def adapt(self, **kw):
        args=dict(episode_id='authored-source-shape',family_id='fixture-only',stratum='active',adverse_state_at_decision=True,decision_index=1,projections=self.projections)
        args.update(kw)
        return adapt_tool_events(self.records,**args)
    def test_order_is_list_index(self):
        self.assertEqual([e.tick for e in self.adapt().events],[0,1])
    def test_no_evaluator_or_extra_fields(self):
        e=self.adapt(); payload=str(render_observer_input(e,paired_masks(e)[0]))
        for key in ['SECRET','private_state','adverse_state','fixture-only','phase']:
            self.assertNotIn(key,payload)
    def test_pairs_retain_equal_count(self):
        e=self.adapt(); _,random,selected=paired_masks(e)
        self.assertEqual(len(random.retained_ids),len(selected.retained_ids))
        self.assertEqual(selected.retained_ids,('tool-1',))
    def test_future_not_forwarded(self):
        e=self.adapt(decision_index=0)
        self.assertNotIn('pending',str(render_observer_input(e,paired_masks(e)[0])))
    def test_no_label_inference(self):
        self.records[0]['result']['collusion']=True
        e=self.adapt(stratum='benign',adverse_state_at_decision=False)
        self.assertFalse(e.adverse_state_at_decision)
    def test_missing_schema_fails(self):
        del self.records[0]['planning_round']
        with self.assertRaises(ValueError):self.adapt()
    def test_unknown_tool_fails(self):
        self.records[0]['tool_name']='unknown'
        with self.assertRaises(ValueError):self.adapt()
    def test_nested_context_fails(self):
        self.records[0]['arguments']['message']={'private':'bad'}
        with self.assertRaises(ValueError):self.adapt()
    def test_missing_projected_field_fails(self):
        del self.records[0]['arguments']['message']
        with self.assertRaises(ValueError):self.adapt()
    def test_bad_cutoff_fails(self):
        with self.assertRaises(ValueError):self.adapt(decision_index=2)
    def test_non_boolean_truth_fails(self):
        with self.assertRaises(ValueError):self.adapt(adverse_state_at_decision=1)
    def test_nan_fails(self):
        self.records[0]['arguments']['message']=float('nan')
        with self.assertRaises(ValueError):self.adapt()
    def test_duplicate_projection_fails(self):
        with self.assertRaises(ValueError):self.adapt(projections=self.projections*2)

if __name__=='__main__':unittest.main()
