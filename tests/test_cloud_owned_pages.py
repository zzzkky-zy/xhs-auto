"""Offline fixtures only: no account login, browser call or actual cleanup."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from cloud_actions import CloudActions, _iso
from cloud_owned_pages import CloudOwnedPages


class OwnedPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / 'cloud.json'
        self.config.write_text(json.dumps(dict(schema_version=1, browser_mode='cloud_host', account_id='fixture', expected_user_id='user', state_dir='state', allow_write=False, allow_reconcile=False)))
        self.now = 1792404000.0
        self.actions = CloudActions(self.config, clock=lambda: self.now)
        self.actions.prepare(operation='get_my_profile', payload={}, expected_account='user', target={'own_account':'user'}, user_source='fixture', operation_id='op')
        # Deliberately arrange authoritative ledger fixtures, not fake live receipts.
        self.actions.db.execute("UPDATE actions SET status='dispatching', attempt_id='attempt', claim_context='context' WHERE operation_id='op'")
        self.actions.db.commit()
        self.pages = CloudOwnedPages(self.actions)
        self.page = dict(browser_session='session', browser_context='context', tab_id='7', page_instance='official-instance-1')
        self.capability = dict(method='close_owned_tab', supported=True, documentation_ref='fixture:official-doc')

    def tearDown(self):
        self.actions.close()
        self.tmp.cleanup()

    def obs(self, **changes):
        value = dict(observed_at=_iso(self.now), evidence_ref='fixture:observation', account_id='user', page=dict(self.page))
        value.update(changes)
        return value

    def register(self):
        return self.pages.register('op', 'page', self.obs(created_by_host=True, instance_verifiable=True, creation_evidence_ref='fixture:creation', instance_documentation_ref='fixture:instance-doc'))

    def terminal(self, status='host_reported_success', result=None):
        if result is None:
            result = dict(status=status, observed_at=_iso(self.now), evidence_kind='visible_result' if status=='host_reported_success' else 'confirmed_not_applied')
        self.actions.db.execute('UPDATE actions SET status=?, result=? WHERE operation_id=?', (status,json.dumps(result),'op'))
        self.actions.db.commit()

    def eligible(self, **changes):
        return self.obs(inventory_evidence_ref='fixture:inventory', instance_evidence_ref='fixture:instance', call_ended=True, steps_resolved=True, editor_empty=True, owned_by_host=True, instance_verified=True, tab_present=True, challenge_active=False, rate_limited=False, permission_denied=False, **changes)

    def ready(self):
        self.register(); self.terminal()

    def close(self):
        return self.pages.request_close('op','page',self.eligible(),self.capability)

    def inventory(self, pages=None, **changes):
        value=dict(observed_at=_iso(self.now), evidence_ref='fixture:inventory', account_id='user', browser_session='session', complete=True, call_ended=True, pages=[] if pages is None else pages)
        value.update(changes)
        return value

    def test_query_does_not_create_schema(self):
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'): self.pages.get('op','page')
        self.assertFalse(self.pages._exists())

    def test_success_requires_inventory_not_close_reply(self):
        self.ready(); self.assertTrue(self.close()['dispatch_once'])
        result=self.pages.record_result('op','page','completed',self.obs())
        self.assertEqual(result['state'],'awaiting_inventory')
        result=self.pages.observe_inventory('op','page',self.inventory())
        self.assertEqual(result['state'],'closed_observed')
        self.assertFalse(result['dispatch_once'])
        self.assertEqual(self.actions.get('op')['status'],'host_reported_success')

    def test_unknown_consumed_and_reopen_does_not_retry(self):
        self.ready(); self.close()
        self.pages.record_result('op','page','unknown',self.obs())
        self.actions.close(); self.actions=CloudActions(self.config,clock=lambda:self.now); self.pages=CloudOwnedPages(self.actions)
        with self.assertRaisesRegex(ValueError,'consumed'): self.close()
        self.assertFalse(self.pages.get('op','page')['dispatch_once'])
        self.assertEqual(self.pages.observe_inventory('op','page',self.inventory([self.page]))['state'],'retained_after_close')
        with self.assertRaisesRegex(ValueError,'consumed'): self.close()

    def test_lost_response_still_only_inventory(self):
        self.ready(); self.close()
        with self.assertRaisesRegex(ValueError,'consumed'): self.close()
        self.assertEqual(self.pages.observe_inventory('op','page',self.inventory())['state'],'closed_observed')

    def test_pause_blocks_even_read_action_cleanup(self):
        self.ready(); self.actions.set_execution_pause(True,'fixture:user','paused')
        self.assertTrue(self.pages.candidate('op','page',self.eligible())['paused'])
        with self.assertRaisesRegex(ValueError,'EXECUTION_PAUSED'): self.close()
        self.assertFalse(self.pages.get('op','page')['close_consumed'])

    def test_each_missing_safety_flag_denied(self):
        self.ready()
        for key in ('call_ended','steps_resolved','editor_empty','owned_by_host','instance_verified','tab_present','challenge_active','rate_limited','permission_denied'):
            with self.subTest(key=key):
                observation=self.eligible(); observation.pop(key)
                with self.assertRaises(ValueError): self.pages.request_close('op','page',observation,self.capability)
        self.assertFalse(self.pages.get('op','page')['close_consumed'])

    def test_wrong_instance_context_session_account_and_url_only_rejected(self):
        self.ready()
        for key in self.page:
            observation=self.eligible(); observation['page'][key]='other'
            with self.subTest(key=key), self.assertRaises(ValueError): self.pages.request_close('op','page',observation,self.capability)
        observation=self.eligible(); observation['account_id']='other'
        with self.assertRaises(ValueError): self.pages.request_close('op','page',observation,self.capability)
        observation=self.eligible(); observation['page']={'url':'https://example.test/same'}
        with self.assertRaises(ValueError): self.pages.request_close('op','page',observation,self.capability)

    def test_nonterminal_unknown_cancelled_no_receipt_rejected(self):
        self.register()
        for status in ('dispatching','submitted_unknown','cancelled','ready'):
            self.terminal(status,{})
            with self.subTest(status=status),self.assertRaises(ValueError): self.close()
        self.terminal('cancelled',dict(status='not_applied',evidence_kind='confirmed_not_applied'))
        self.assertTrue(self.close()['dispatch_once'])

    def test_stale_future_evidence_and_unsupported_capability(self):
        self.ready()
        for delta in (-121,1):
            observation=self.eligible(); observation['observed_at']=_iso(self.now+delta)
            with self.assertRaises(ValueError): self.pages.request_close('op','page',observation,self.capability)
        with self.assertRaises(ValueError): self.pages.request_close('op','page',self.eligible(),dict(self.capability,supported=False))

    def test_inventory_reused_id_not_assumed_absent(self):
        self.ready(); self.close()
        replacement=dict(self.page,page_instance='instance-2')
        result=self.pages.observe_inventory('op','page',self.inventory([replacement]))
        self.assertEqual(result['state'],'retained_after_close')
        self.assertTrue(result['close_consumed'])

    def test_inventory_wrong_session_incomplete_or_prior_rejected(self):
        self.ready(); self.close()
        for changes in ({'browser_session':'other'},{'complete':False},{'call_ended':False},{'account_id':'other'},{'observed_at':_iso(self.now-1)}):
            with self.subTest(changes=changes),self.assertRaises(ValueError): self.pages.observe_inventory('op','page',self.inventory(**changes))

    def test_creation_cannot_adopt_terminal_or_user_page(self):
        self.terminal()
        with self.assertRaises(ValueError): self.register()
        self.actions.db.execute("UPDATE actions SET status='dispatching' WHERE operation_id='op'");self.actions.db.commit()
        with self.assertRaises(ValueError): self.pages.register('op','page',self.obs(created_by_host=False,instance_verifiable=True,creation_evidence_ref='fixture',instance_documentation_ref='fixture'))

    def test_second_actor_cannot_double_close(self):
        self.ready()
        other=CloudActions(self.config,clock=lambda:self.now)
        try:
            self.close()
            with self.assertRaisesRegex(ValueError,'consumed'): CloudOwnedPages(other).request_close('op','page',self.eligible(),self.capability)
        finally:other.close()

    def test_simultaneous_actors_only_one_permit(self):
        import threading
        self.ready()
        barrier=threading.Barrier(2)
        outcomes=[]
        def actor():
            actions=CloudActions(self.config,clock=lambda:self.now)
            try:
                barrier.wait(timeout=5)
                try:
                    result=CloudOwnedPages(actions).request_close('op','page',self.eligible(),self.capability)
                    outcomes.append(result['dispatch_once'])
                except ValueError as error:
                    outcomes.append(str(error))
            finally:actions.close()
        threads=[threading.Thread(target=actor) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(outcomes.count(True),1)
        self.assertEqual(sum(isinstance(v,str) and 'consumed' in v for v in outcomes),1)

    def test_duplicate_resource_cannot_obtain_second_permit(self):
        self.ready();self.close()
        with self.assertRaises(ValueError):
            self.pages.register('op','different-resource',self.obs(created_by_host=True,instance_verifiable=True,creation_evidence_ref='fixture:creation',instance_documentation_ref='fixture:doc'))

    def test_candidate_and_get_do_not_write_events(self):
        self.ready()
        count=self.actions.db.execute('SELECT COUNT(*) FROM action_events').fetchone()[0]
        self.pages.get('op','page');self.pages.candidate('op','page',self.eligible())
        self.assertEqual(count,self.actions.db.execute('SELECT COUNT(*) FROM action_events').fetchone()[0])

    def test_result_binding_and_immutable_outcome(self):
        self.ready();self.close()
        with self.assertRaises(ValueError):self.pages.record_result('op','page','completed',self.obs(account_id='other'))
        self.pages.record_result('op','page','unknown',self.obs())
        with self.assertRaisesRegex(ValueError,'immutable'):self.pages.record_result('op','page','completed',self.obs())

    def test_unresolved_incident_preserved(self):
        from cloud_recovery import CloudRecovery
        self.register()
        recovery=CloudRecovery(self.actions)
        recovery.begin('op','incident',dict(observed_at=_iso(self.now),evidence_ref='fixture',call_ended=True,control_available=True,tab_present=True,challenge_active=True,rate_limited=False,permission_denied=False))
        self.terminal()
        with self.assertRaisesRegex(ValueError,'incident'):self.close()
        self.assertEqual(recovery.get('incident')['state'],'challenge')

    def other_read_incident(self, context='other-context', **changes):
        from cloud_recovery import CloudRecovery
        self.actions.prepare(operation='get_my_profile', payload={}, expected_account='user', target={'own_account':'user'}, user_source='fixture', operation_id='other')
        self.actions.db.execute("UPDATE actions SET status='dispatching', attempt_id='other-attempt', claim_context=? WHERE operation_id='other'",(context,))
        self.actions.db.commit()
        observation=dict(observed_at=_iso(self.now),evidence_ref='fixture:other',call_ended=True,control_available=False,tab_present=True,challenge_active=False,rate_limited=False,permission_denied=False)
        observation.update(changes)
        recovery=CloudRecovery(self.actions)
        recovery.begin('other','other-incident',observation)
        return recovery

    def test_independent_unrestricted_read_incident_does_not_block_cleanup(self):
        self.ready();recovery=self.other_read_incident()
        self.assertTrue(self.close()['dispatch_once'])
        self.assertEqual(recovery.get('other-incident')['state'],'hung_control')

    def test_same_context_read_incident_preserved(self):
        self.ready();self.other_read_incident(context='context')
        with self.assertRaisesRegex(ValueError,'incident'):self.close()

    def test_other_page_challenge_still_account_restriction(self):
        self.ready();self.other_read_incident(challenge_active=True)
        with self.assertRaisesRegex(ValueError,'account restriction'):self.close()

    def test_other_page_running_call_still_blocked(self):
        self.ready();self.other_read_incident(call_ended=False)
        with self.assertRaisesRegex(ValueError,'incident'):self.close()

    def test_other_page_unknown_restriction_still_blocked(self):
        self.ready();self.other_read_incident()
        saved=self.actions.db.execute("SELECT data FROM recovery_incidents WHERE incident_id='other-incident'").fetchone()
        record=json.loads(saved['data']);record['observation'].pop('rate_limited')
        self.actions.db.execute("UPDATE recovery_incidents SET data=? WHERE incident_id='other-incident'",(json.dumps(record),));self.actions.db.commit()
        with self.assertRaisesRegex(ValueError,'incident'):self.close()

    def test_other_page_latest_false_does_not_clear_latch(self):
        self.ready();self.other_read_incident()
        saved=self.actions.db.execute("SELECT data FROM recovery_incidents WHERE incident_id='other-incident'").fetchone()
        record=json.loads(saved['data']);record['restriction_latches']=['challenge_active']
        self.actions.db.execute("UPDATE recovery_incidents SET data=? WHERE incident_id='other-incident'",(json.dumps(record),));self.actions.db.commit()
        with self.assertRaisesRegex(ValueError,'account restriction'):self.close()

    def test_other_page_missing_call_ended_or_mutation_unknown_denied(self):
        self.ready();self.other_read_incident()
        saved=self.actions.db.execute("SELECT data FROM recovery_incidents WHERE incident_id='other-incident'").fetchone()
        original=json.loads(saved['data'])
        for field in ('call_ended','mutation'):
            record=json.loads(json.dumps(original))
            if field=='call_ended':record['observation'].pop(field)
            else:record['snapshot']['binding'].pop(field)
            self.actions.db.execute("UPDATE recovery_incidents SET data=? WHERE incident_id='other-incident'",(json.dumps(record),));self.actions.db.commit()
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'incident'):self.close()


if __name__=='__main__':unittest.main()
