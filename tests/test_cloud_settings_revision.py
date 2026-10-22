"""Synthetic retained-editor settings revisions; no browser or genuine approval."""
import json
import sqlite3
import unittest

import test_cloud_schedule_inspection as fixtures
from test_cloud_actions import cloud


class PublicationSettingsRevisionTests(unittest.TestCase):
    for _name in ('setUp', 'tearDown', 'save_config', 'stamp', 'prepare', 'attestation',
                  'identity', 'ready', 'enabled', 'claimed', 'submitted', 'result',
                  'unknown', 'continuation', 'grant', 'inspection_args'):
        locals()[_name] = getattr(fixtures.ScheduleInspectionTests, _name)

    def start(self, blocked=None):
        args = self.inspection_args('pub')
        args['operation'] = 'publish_content'
        args['payload'].update(is_original=True, group_chat='SYNTHETIC old group',
                               activity='SYNTHETIC old activity')
        action = self.ready('pub', **args)
        self.enabled()
        self.ledger.record_authorization('pub', self.attestation(action, expires_at=self.stamp(3600)))
        if blocked:
            self.grant(action, blocked, expires_at=self.stamp(3600),
                       instruction_scope='future_independent_image_publications')
        claim = self.ledger.claim('pub', 'host', 5)
        self.ledger.begin_submit('pub', claim['claim_token'])
        self.time += 10
        self.ledger.observe_identity('pub', self.identity())
        return claim

    def proposed(self, **changes):
        payload = dict(self.ledger.get('pub')['binding']['payload'])
        payload.update(is_original=False, group_chat='SYNTHETIC new group',
                       activity='SYNTHETIC new activity', schedule_at=self.stamp(7200))
        payload.update(changes)
        return payload

    def preview(self, payload=None, source='SYNTHETIC explicit settings instruction'):
        return self.ledger.preview_unsubmitted_publication_settings(
            'pub', self.proposed() if payload is None else payload, source)

    def observation(self, **changes):
        payload = self.ledger.get('pub')['binding']['payload']
        return {'expected_account': 'public-user-1', 'browser_context': 'fixture:browser-1/tab-2',
                'call_ended': True, 'final_submit_invoked': False, 'editor_retained': True,
                'visible_title': payload['title'], 'visible_body': payload['content'],
                'uploaded_media_match': True, 'media_evidence_ref': 'SYNTHETIC actual uploaded media',
                'observed_at': self.stamp(), 'evidence_ref': 'SYNTHETIC ended call and retained editor',
                **changes}

    def revise(self, claim, *, payload=None, attestation=None, observation=None):
        payload = self.proposed() if payload is None else payload
        preview = self.preview(payload)
        return self.ledger.revise_unsubmitted_publication_settings(
            'pub', claim['claim_token'], payload,
            self.attestation(preview) if attestation is None else attestation,
            self.observation() if observation is None else observation)

    def test_preview_is_read_only_and_revision_keeps_original_attempt_claim_and_evidence(self):
        claim = self.start()
        before = self.ledger.get('pub')
        original = dict(self.ledger.db.execute('SELECT * FROM actions WHERE operation_id="pub"').fetchone())
        trigger = self.ledger.db.execute("SELECT sql FROM sqlite_master WHERE name='action_immutable'").fetchone()[0]
        preview = self.preview()
        self.assertEqual(self.ledger.get('pub'), before)
        revised = self.revise(claim)
        self.assertEqual(revised['binding_digest'], preview['binding_digest'])
        self.assertEqual(revised['semantic_digest'], preview['semantic_digest'])
        self.assertNotEqual(revised['binding_digest'], before['binding_digest'])
        for key in ('attempt', 'attempt_id', 'owner', 'claim_browser_context', 'lease_until', 'submitted_at', 'status', 'result'):
            self.assertEqual(revised[key], before[key])
        self.ledger._claim(self.ledger._row('pub'), claim['claim_token'])
        with self.assertRaisesRegex(ValueError, 'ERR_LEASE_EXPIRED'):
            self.ledger._claim(self.ledger._row('pub'), claim['claim_token'], live=True)
        stored = dict(self.ledger.db.execute('SELECT * FROM actions WHERE operation_id="pub"').fetchone())
        for key in original:
            if key != 'updated_at':
                self.assertEqual(stored[key], original[key])
        self.assertEqual(trigger, self.ledger.db.execute(
            "SELECT sql FROM sqlite_master WHERE name='action_immutable'").fetchone()[0])
        revision = revised['binding_revisions'][0]
        self.assertEqual(revision['old_binding'], before['binding'])
        self.assertEqual(revision['new_binding'], revised['binding'])
        self.assertEqual(revision['attestation'], revised['authorization'])
        for table, field in [('actions', 'binding_json'), ('publication_binding_revisions', 'new_binding_json')]:
            with self.assertRaises(sqlite3.IntegrityError):
                self.ledger.db.execute(f'UPDATE {table} SET {field}=?', ('{}',))
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.db.execute('DELETE FROM publication_binding_revisions')
        self.ledger.close()
        self.ledger = cloud.CloudActions(self.config, clock=lambda: self.time)
        self.assertEqual(self.ledger.get('pub'), revised)

    def test_manuscript_run_media_and_all_unlisted_fields_stay_immutable(self):
        self.start()
        for change in ({'title': 'changed'}, {'content': 'changed'}, {'run_id': 'other'},
                       {'images': []}, {'visibility': '仅自己可见'}, {'operation_id': 'other'},
                       {'tags': ['new']}, {'approval_ref': 'new provenance'}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'only original'):
                self.preview(self.proposed(**change))
        for change in ({'is_original': 0}, {'group_chat': []}, {'activity': None},
                       {'schedule_at': 'tomorrow'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.preview(self.proposed(**change))
        with self.assertRaisesRegex(ValueError, 'settings change'):
            self.preview(self.ledger.get('pub')['binding']['payload'])

    def test_payload_validator_is_used_and_cannot_mutate(self):
        self.start()
        calls = []
        self.ledger.payload_validator = lambda operation, payload: calls.append((operation, payload))
        self.preview()
        self.assertEqual(calls[0][0], 'publish_content')
        self.ledger.payload_validator = lambda operation, payload: payload.update(title='changed')
        with self.assertRaisesRegex(ValueError, 'must not transform'):
            self.preview()

    def test_actual_editor_context_flags_and_freshness_required(self):
        claim = self.start()
        for change in ({'call_ended': False}, {'call_ended': 1}, {'final_submit_invoked': True},
                       {'final_submit_invoked': 0}, {'editor_retained': False},
                       {'uploaded_media_match': False}, {'expected_account': 'other'},
                       {'browser_context': 'other'}, {'visible_title': 'wrong'},
                       {'visible_body': 'wrong'}, {'media_evidence_ref': ''}, {'evidence_ref': ''},
                       {'observed_at': self.stamp(-121)}, {'observed_at': self.stamp(1)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.revise(claim, observation=self.observation(**change))
        self.assertEqual(self.ledger.get('pub')['binding_revisions'], [])

    def test_exact_new_authorization_required_and_old_result_invalid(self):
        claim = self.start()
        before = self.ledger.get('pub')
        valid = self.attestation(self.preview())
        for change in ({'operation_id': 'other'}, {'binding_digest': before['binding_digest']},
                       {'expected_account': 'other'}, {'user_source': 'other'},
                       {'user_source': ''}, {'user_quote': ''}, {'evidence_ref': ''},
                       {'expires_at': self.stamp()}, {'confirmed_at': self.stamp(6)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.revise(claim, attestation={**valid, **change})
        revised = self.revise(claim)
        self.assertEqual(revised['authorization']['binding_digest'], revised['binding_digest'])
        self.ledger._guard_authorization(self.ledger._row('pub'))
        stale = dict(self.ledger._row('pub'), authorization=cloud._json(before['authorization']))
        with self.assertRaisesRegex(ValueError, 'current effective binding'):
            self.ledger._guard_authorization(stale)
        with self.assertRaisesRegex(ValueError, 'result binding_digest'):
            self.ledger.report_result('pub', claim['claim_token'], self.result(before))
        self.ledger.report_result('pub', claim['claim_token'], self.result(revised))
        with self.assertRaisesRegex(ValueError, 'unreported'):
            self.preview(self.proposed(is_original=True))

    def test_expired_original_authorization_replaced_by_exact_valid_new_attestation(self):
        claim = self.start()
        self.time += 3601
        self.ledger.observe_identity('pub', self.identity())
        with self.assertRaisesRegex(ValueError, 'ERR_AUTHORIZATION_EXPIRED'):
            self.ledger._guard_authorization(self.ledger._row('pub'))
        revised = self.revise(claim)
        self.ledger._guard_authorization(self.ledger._row('pub'))
        self.assertEqual(revised['attempt_id'], claim['attempt_id'])
        self.assertGreater(cloud._timestamp(revised['authorization']['expires_at'], 'expires_at'), self.time)

    def test_fresh_identity_original_media_and_claim_required(self):
        claim = self.start()
        with self.assertRaisesRegex(ValueError, 'ERR_CLAIM'):
            self.revise({**claim, 'claim_token': 'wrong'})
        self.time += 121
        with self.assertRaisesRegex(ValueError, 'ERR_IDENTITY_STALE'):
            self.revise(claim)
        self.ledger.observe_identity('pub', self.identity(browser_context='other'))
        with self.assertRaisesRegex(ValueError, 'ERR_BROWSER_CONTEXT_CHANGED'):
            self.revise(claim)
        self.ledger.observe_identity('pub', self.identity())
        (self.base / 'pub.png').write_bytes(b'tampered original')
        with self.assertRaisesRegex(ValueError, 'ERR_MEDIA_CHANGED'):
            self.revise(claim)

    def test_final_permit_or_reported_unknown_disallows_revision(self):
        claim = self.start()
        preview = self.preview()
        with self.ledger._transaction():
            self.ledger._event('pub', 'publication_final_step_permitted', {'step_id': 'final'})
        with self.assertRaisesRegex(ValueError, 'permit already issued'):
            self.ledger.revise_unsubmitted_publication_settings(
                'pub', claim['claim_token'], self.proposed(), self.attestation(preview), self.observation())
        with self.assertRaisesRegex(ValueError, 'permit already issued'):
            self.preview()
        self.ledger.report_result('pub', claim['claim_token'], self.result(
            claim, status='submitted_unknown', evidence_kind='uncertain'))
        with self.assertRaisesRegex(ValueError, 'unreported'):
            self.preview()

    def test_semantic_dedup_preserves_original_and_all_revised_bindings(self):
        claim = self.start()
        original = self.ledger.get('pub')
        first = self.revise(claim)
        self.time += 1
        second = self.revise(claim, payload=self.proposed(activity='SYNTHETIC third activity'))
        self.assertEqual(len(second['binding_revisions']), 2)
        self.assertEqual(second['binding_revisions'][1]['old_binding_digest'], first['binding_digest'])
        for index, action in enumerate((original, first, second)):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'ERR_CONTENT_DUPLICATE'):
                self.prepare('duplicate-' + str(index), operation='publish_content',
                             payload=action['binding']['payload'], media_manifest=action['binding']['media_manifest'])

    def test_collision_with_other_action_and_verifier_veto_roll_back(self):
        claim = self.start()
        before = self.ledger.get('pub')
        candidate = self.preview()
        self.prepare('collision', operation='publish_content', payload=candidate['binding']['payload'],
                     media_manifest=candidate['binding']['media_manifest'])
        with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_DUPLICATE'):
            self.revise(claim)
        self.assertEqual(self.ledger.get('pub'), before)
        self.ledger.host_verifier = lambda phase, binding, evidence: phase != 'authorization_at_dispatch'
        with self.assertRaisesRegex(ValueError, 'ERR_HOST_VERIFIER_DENIED'):
            self.revise(claim, payload=self.proposed(activity='SYNTHETIC different new activity'))
        self.assertEqual(self.ledger.get('pub'), before)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM publication_binding_revisions').fetchone()[0], 0)

    def test_continuation_rebind_preserves_exact_source_and_old_unknown(self):
        blocked = self.unknown()
        old = self.ledger.get(blocked['operation_id'])
        claim = self.start(blocked)
        original = self.ledger.get('pub')['unknown_continuations'][0]['attestation']
        revised = self.revise(claim)
        continuation = revised['unknown_continuations'][0]['attestation']
        self.assertEqual(continuation, {**original, 'binding_digest': revised['binding_digest']})
        self.assertEqual(revised['binding_revisions'][0]['continuation_rebindings'],
                         [{'old': original, 'new': continuation}])
        stored = self.ledger.db.execute(
            'SELECT attestation FROM publication_unknown_continuations WHERE operation_id="pub"').fetchone()[0]
        self.assertEqual(json.loads(stored), original)
        self.assertEqual(self.ledger.get(blocked['operation_id']), old)
        self.ledger._guard_dispatch(self.ledger._row('pub'), browser_context='fixture:browser-1/tab-2')
        again = self.revise(claim, payload=self.proposed(activity='SYNTHETIC next activity'))
        self.assertEqual(again['unknown_continuations'][0]['attestation'],
                         {**original, 'binding_digest': again['binding_digest']})
        self.assertEqual(again['binding_revisions'][1]['continuation_rebindings'][0]['old'], continuation)

    def test_insert_guard_disallows_manuscript_changes_and_broken_revision_chain(self):
        claim = self.start()
        self.revise(claim)
        row = dict(self.ledger.db.execute('SELECT * FROM publication_binding_revisions').fetchone())
        row.pop('id')
        row['new_binding_digest'] = 'f' * 64
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'invalid publication binding revision'):
            self.ledger.db.execute(
                'INSERT INTO publication_binding_revisions(' + ','.join(row) + ') VALUES ('
                + ','.join('?' for _ in row) + ')', tuple(row.values()))

    def test_other_action_browser_guard_uses_revised_unknown_binding(self):
        claim = self.start()
        revised = self.revise(claim)
        self.ledger.report_result('pub', claim['claim_token'], self.result(
            revised, status='submitted_unknown', evidence_kind='uncertain'))
        args = self.inspection_args('next', content='SYNTHETIC independent next publication',
                                    data=b'different synthetic image')
        args['operation'] = 'publish_content'
        next_action = self.ready('next', **args)
        self.grant(next_action, revised, instruction_scope='future_independent_image_publications')
        next_claim = self.ledger.claim('next', 'host')
        self.assertEqual(next_claim['status'], 'dispatching')
        self.ledger.begin_submit('next', next_claim['claim_token'])
        self.assertEqual(self.ledger.get('pub')['binding_digest'], revised['binding_digest'])


if __name__ == '__main__':
    unittest.main()
