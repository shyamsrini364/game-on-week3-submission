"""Executed LangGraph scenarios and public projection; no paid inference."""
import json
import unittest
import test_erne_venues as fixtures
from gameon.erne.activity import project_activity, completion_state, ActivityEvent
from gameon.erne.providers import DeterministicTestProvider
from gameon.venues.contracts import VenuePreferences, GroupProfile
from gameon.venues.directory import CuratedDirectory


class FixtureSelector(DeterministicTestProvider):
    pass


class ActivityTests(unittest.TestCase):
    setUp = fixtures.VenueTests.setUp
    tearDown = fixtures.VenueTests.tearDown
    run_graph = fixtures.VenueTests.run_graph

    def test_normal_order_and_bound_evidence(self):
        result = self.run_graph()
        events = project_activity(result)
        self.assertEqual(completion_state(result), 'approval_required')
        self.assertEqual([e['order'] for e in events], list(range(1,len(events)+1)))
        self.assertEqual(len({e['event_id'] for e in events}),len(events))
        for event in events:
            ActivityEvent.model_validate(event)
            self.assertEqual(event['run_id'], result.run_id)
            self.assertIn(event['trace_event_id'], {t.event_id for t in result.trace})
            if event['observation_id']:
                self.assertIn(event['observation_id'], {o.evidence_id for o in result.observations})
        self.assertFalse(any(e['stage']=='replanning' for e in events))
        self.assertEqual(events[-1]['stage'],'approval_required')
        self.assertIn('recommendation_ready', [e['stage'] for e in events])

    def test_unsuitable_candidate_genuinely_replans(self):
        prefs=VenuePreferences(group=GroupProfile(preferred_venue_ids=('the-rally-altona',),indoor_only=True))
        result=self.run_graph(preferences=prefs,provider=FixtureSelector())
        searches=[o.payload for o in result.observations if o.source=='search_pickleball_venues']
        self.assertFalse(searches[0].venues)
        self.assertTrue(searches[0].excluded_ids)
        self.assertTrue(searches[1].venues)
        events=project_activity(result)
        replan=next(e for e in events if e['stage']=='replanning')
        self.assertEqual(replan['provenance'],'fixture')
        later=events[replan['order']:]
        self.assertTrue(any(e['status']=='started' and e['stage']=='searching_venues' for e in later))
        self.assertEqual(events[-1]['stage'],'approval_required')
        self.assertIn('does not mean courts are unavailable',replan['detail'])

    def test_exhausted_requests_help_without_inventing_inventory(self):
        result=self.run_graph(directory=CuratedDirectory(()))
        self.assertEqual(completion_state(result),'needs_user_help')
        self.assertEqual(sum(t.phase=='tool' and t.name=='search_pickleball_venues' for t in result.trace),2)
        self.assertEqual(project_activity(result)[-1]['title'],'Need your help')
        self.assertFalse(result.recommendations)

    def test_failure_is_not_inventory_unavailable(self):
        result=self.run_graph(directory=fixtures.BrokenDirectory(self.venues))
        events=project_activity(result)
        self.assertEqual(sum(e['status']=='failed' for e in events),2)
        self.assertTrue(any('temporary tool failure' in e['detail'] for e in events))
        self.assertEqual(events[-1]['stage'],'needs_user_help')
        self.assertNotIn('private provider details',json.dumps(events))

    def test_no_raw_reasoning_or_identifiers(self):
        result=self.run_graph()
        poisoned=result.model_copy(update={'reason':'SECRET reasoning','trace':tuple(t.model_copy(update={'summary':'SECRET player cookie'}) for t in result.trace)})
        text=json.dumps(project_activity(poisoned))
        self.assertNotIn('SECRET',text)
        for observation in result.observations:
            if hasattr(observation.payload,'players'):
                for player in observation.payload.players:self.assertNotIn(player.id,text)
        self.assertNotIn(result.game_id,text)

    def test_model_provenance_requires_model_decision(self):
        result=self.run_graph()
        model=result.model_copy(update={'trace':tuple(t.model_copy(update={'decision_source':'llm'}) if t.phase=='decision' else t for t in result.trace)})
        self.assertTrue(all(e['provenance']=='model' for e in project_activity(model)))
        fixture=result.model_copy(update={'trace':tuple(t.model_copy(update={'decision_source':'mock'}) if t.phase=='decision' else t for t in result.trace)})
        self.assertTrue(all(e['provenance']=='fixture' for e in project_activity(fixture)))

    def test_invalid_observation_fails_safely(self):
        result=self.run_graph(directory=fixtures.CorruptDirectory(self.venues))
        self.assertEqual(completion_state(result),'failed')
        self.assertTrue(any('safely validated' in e['detail'] for e in project_activity(result)))

    def test_run_scoping_has_no_shared_activity_state(self):
        result=self.run_graph()
        first=project_activity(result)
        second=project_activity(result.model_copy(update={'run_id':'independent-run'}))
        self.assertFalse({e['event_id'] for e in first} & {e['event_id'] for e in second})
        self.assertTrue(all(e['run_id']=='independent-run' for e in second))
        self.assertEqual(project_activity(result),first)

    def test_missing_votes_waits_without_searching(self):
        self.game_id=fixtures.create_scenario(self.store,'missing_votes')
        result=self.run_graph(preferences=VenuePreferences())
        events=project_activity(result)
        self.assertEqual(events[-1]['stage'],'waiting_for_group')
        self.assertFalse(any(e['stage']=='searching_venues' for e in events))


if __name__=='__main__':unittest.main()
