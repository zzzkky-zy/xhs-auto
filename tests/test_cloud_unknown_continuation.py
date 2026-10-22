"""Offline synthetic continuation evidence. No website or genuine user approval."""
import json
import sqlite3
import unittest
from unittest import mock

import test_cloud_actions as fixtures

cloud = fixtures.cloud


class UnknownContinuationTests(unittest.TestCase):
    setUp = fixtures.CloudActionTests.setUp
    tearDown = fixtures.CloudActionTests.tearDown
    save_config = fixtures.CloudActionTests.save_config
    stamp = fixtures.CloudActionTests.stamp
    prepare = fixtures.CloudActionTests.prepare
    attestation = fixtures.CloudActionTests.attestation
    identity = fixtures.CloudActionTests.identity
    ready = fixtures.CloudActionTests.ready
    enabled = fixtures.CloudActionTests.enabled
    claimed = fixtures.CloudActionTests.claimed
    submitted = fixtures.CloudActionTests.submitted
    result = fixtures.CloudActionTests.result

    def unknown(self, ident='old', **overrides):
        claimed = self.submitted(ident, **overrides)
        self.ledger.report_result(ident, claimed['claim_token'], self.result(
            claimed, status='submitted_unknown', evidence_kind='uncertain',
            details='SYNTHETIC: browser call ended; application remains unknown.'))
        return claimed

    def new(self, ident='next', feed='b' * 24, content='A different substantive fixture comment', **overrides):
        return self.ready(ident, payload={'feed_id': feed, 'content': content}, target={'feed_id': feed}, **overrides)

    def continuation(self, new, old, **overrides):
        value = {'operation_id': new['operation_id'], 'binding_digest': new['binding_digest'],
                 'blocked_operation_id': old['operation_id'], 'blocked_binding_digest': old['binding_digest'],
                 'expected_account': new['binding']['expected_account'], 'user_source': 'SYNTHETIC separate continuation message',
                 'user_quote': 'SYNTHETIC permission for this different target; preserve the unresolved action.',
                 'confirmed_at': self.stamp(), 'expires_at': self.stamp(600), 'evidence_ref': 'SYNTHETIC quoted source'}
        value.update(overrides)
        return value

    def grant(self, new, old, **overrides):
        return self.ledger.record_unknown_continuation(new['operation_id'], old['operation_id'], self.continuation(new, old, **overrides))

    def test_exact_scoped_record_is_durable_idempotent_and_does_not_resolve_old(self):
        old = self.unknown(); new = self.new()
        before = self.ledger.get('old'); events = self.ledger.events('old')
        value = self.grant(new, old)
        self.assertEqual(value['status'], 'ready')
        self.assertFalse(value['duplicate'])
        self.assertIn('host_reported_unverified', value['unknown_continuation']['verification'])
        self.assertTrue(self.grant(new, old)['duplicate'])
        self.assertEqual(len([e for e in self.ledger.events('next') if e['event'] == 'unknown_continuation_reported']), 1)
        self.assertEqual(self.ledger.get('old'), before)
        self.assertEqual(self.ledger.events('old'), events)
        self.ledger.close(); self.ledger = cloud.CloudActions(self.config, clock=lambda: self.time)
        claimed = self.ledger.claim('next', 'host')
        self.ledger.begin_submit('next', claimed['claim_token'])
        self.assertEqual(self.ledger.get('old'), before)
        self.assertEqual(self.ledger.get('next')['status'], 'submitted_unknown')
        with self.assertRaisesRegex(ValueError, 'ERR_STATE'):
            self.ledger.claim('old', 'host')

    def test_prepared_evidence_does_not_advance_or_authorize_action(self):
        old = self.unknown()
        new = self.prepare('next', payload={'feed_id': 'b' * 24, 'content': 'SYNTHETIC different meaning'}, target={'feed_id': 'b' * 24})
        self.assertEqual(self.grant(new, old)['status'], 'prepared')
        with self.assertRaisesRegex(ValueError, 'ERR_STATE'):
            self.ledger.claim('next', 'host')
        self.ledger.request_authorization('next')
        with self.assertRaisesRegex(ValueError, 'ERR_STATE'):
            self.ledger.claim('next', 'host')

    def test_reply_operation_can_continue_on_different_feed(self):
        old = self.unknown()
        new = self.ready('reply', operation='reply_comment_in_feed',
                         payload={'feed_id': 'b' * 24, 'comment_id': 'c' * 24, 'content': 'SYNTHETIC different reply'},
                         target={'feed_id': 'b' * 24, 'comment_id': 'c' * 24})
        self.grant(new, old)
        self.assertEqual(self.ledger.claim('reply', 'host')['status'], 'dispatching')

    def test_bare_flags_and_wrong_exact_bindings_cannot_grant(self):
        old = self.unknown(); new = self.new(); valid = self.continuation(new, old)
        invalid = [True, 'approval-ref', {'approved': True}, {**valid, 'extra': 'x'}]
        for key in ('operation_id', 'binding_digest', 'blocked_operation_id', 'blocked_binding_digest', 'expected_account'):
            invalid.append({**valid, key: 'wrong'})
        invalid.extend([{**valid, 'user_source': ''}, {**valid, 'user_quote': False}, {**valid, 'evidence_ref': ''}])
        for evidence in invalid:
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                self.ledger.record_unknown_continuation('next', 'old', evidence)
        self.assertIsNone(self.ledger.get('next')['unknown_continuation'])
        with self.assertRaisesRegex(ValueError, 'ERR_ACCOUNT_UNCERTAIN'):
            self.ledger.claim('next', 'host')

    def test_unreported_live_and_expired_browser_calls_never_qualify(self):
        old = self.submitted('old'); new = self.new()
        for elapsed in (0, 121):
            self.time += elapsed
            with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_UNREPORTED'):
                self.grant(new, old)
        self.assertIsNone(self.ledger.get('old')['result'])

    def test_same_feed_case_variants_and_same_normalized_text_are_rejected(self):
        old = self.unknown(payload={'feed_id': 'a' * 24, 'content': 'Fixture text!'}, target={'feed_id': 'a' * 24})
        for ident, feed, content in (('same-feed', 'A' * 24, 'Distinct fixture'),
                                     ('same-copy', 'b' * 24, 'Ｆｉｘｔｕｒｅ  TEXT。'),
                                     ('empty-copy', 'c' * 24, '!!!')):
            new = self.new(ident, feed, content)
            with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_TARGET|ERR_CONTINUATION_CONTENT'):
                self.grant(new, old)

    def test_other_mutation_kinds_are_never_exempt(self):
        old = self.unknown()
        for operation in ('like_feed', 'favorite_feed', 'reply_direct_message', 'publish_with_video'):
            new = self.ready(operation, operation=operation, payload={'feed_id': 'b' * 24, 'content': 'SYNTHETIC other action'},
                             target={'feed_id': 'b' * 24})
            with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_SCOPE'):
                self.grant(new, old)

    def test_old_noncomment_unknown_cannot_be_exempted(self):
        old = self.unknown(operation='like_feed', payload={'feed_id': 'a' * 24}, target={'feed_id': 'a' * 24})
        with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_SCOPE'):
            self.grant(self.new(), old)

    def test_different_account_or_conflicting_target_cannot_be_exempted(self):
        old = self.unknown()
        new = self.new(expected_account='different-account')
        with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_SCOPE'):
            self.grant(new, old)
        for target in ({'feed_id': 'c' * 24}, {'url': 'https://www.xiaohongshu.com/explore/' + 'c' * 24}):
            new = self.ready('conflict-' + str(len(target.get('url', ''))),
                             payload={'feed_id': 'b' * 24, 'content': 'SYNTHETIC ' + str(target)}, target=target)
            with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_TARGET'):
                self.grant(new, old)

    def test_second_unknown_remains_blocking_and_cannot_replace_record(self):
        old = self.unknown(); second = self.new('second'); self.grant(second, old)
        claim = self.ledger.claim('second', 'host'); self.ledger.begin_submit('second', claim['claim_token'])
        self.ledger.report_result('second', claim['claim_token'], self.result(claim, status='submitted_unknown', evidence_kind='uncertain'))
        third = self.new('third', 'c' * 24, 'Third distinct fixture message'); self.grant(third, old)
        with self.assertRaisesRegex(ValueError, 'ERR_ACCOUNT_UNCERTAIN'):
            self.ledger.claim('third', 'host')
        with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_CONFLICT'):
            self.grant(third, claim)
        self.assertEqual(self.ledger.get('old')['status'], 'submitted_unknown')
        self.assertEqual(self.ledger.get('second')['status'], 'submitted_unknown')

    def test_other_live_read_blocks_and_existing_semantic_dedup_still_applies(self):
        old = self.unknown(); new = self.new(); self.grant(new, old)
        self.ready('read', operation='get_my_profile', payload={})
        read = self.ledger.claim('read', 'host')
        with self.assertRaisesRegex(ValueError, 'ERR_ACCOUNT_BUSY'):
            self.ledger.claim('next', 'host')
        self.ledger.release_claim('read', read['claim_token'], 'SYNTHETIC ended before submit')
        with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_DUPLICATE'):
            self.prepare('duplicate', payload=new['binding']['payload'], target=new['binding']['target'])

    def test_normal_write_identity_dependency_and_verifier_guards_remain(self):
        old = self.unknown(); new = self.new(); self.grant(new, old)
        self.data['allow_write'] = False; self.save_config()
        with self.assertRaisesRegex(ValueError, 'ERR_WRITE_DISABLED'):
            self.ledger.claim('next', 'host')
        self.enabled(); self.time += 121
        with self.assertRaisesRegex(ValueError, 'ERR_IDENTITY_STALE'):
            self.ledger.claim('next', 'host')
        self.ledger.observe_identity('next', self.identity())
        self.ledger.host_verifier = lambda phase, _binding, _evidence: phase != 'claim'
        with self.assertRaisesRegex(ValueError, 'ERR_HOST_VERIFIER_DENIED: claim'):
            self.ledger.claim('next', 'host')
        self.ledger.host_verifier = None
        dependent = self.new('dependent', 'c' * 24, 'SYNTHETIC dependency', dependency='old'); self.grant(dependent, old)
        with self.assertRaisesRegex(ValueError, 'ERR_DEPENDENCY_NOT_SUCCEEDED'):
            self.ledger.claim('dependent', 'host')

    def test_expiration_revalidated_before_begin_submit(self):
        old = self.unknown(); new = self.new(); self.grant(new, old, expires_at=self.stamp(10))
        claim = self.ledger.claim('next', 'host'); self.time += 11
        with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_EXPIRED'):
            self.ledger.begin_submit('next', claim['claim_token'])
        self.assertEqual(self.ledger.get('next')['status'], 'dispatching')

    def test_optional_verifier_and_pacing_boundary_remain_compatible(self):
        old = self.unknown(); new = self.new(); phases = []
        def verifier(phase, binding, evidence):
            phases.append(phase)
            if phase == 'begin_submit':
                self.assertTrue(evidence['attempt_id'])
                raise ValueError('ERR_BROWSER_PACING: SYNTHETIC active browser step')
            return True
        self.ledger.host_verifier = verifier
        self.grant(new, old); claim = self.ledger.claim('next', 'host')
        with self.assertRaisesRegex(ValueError, 'ERR_BROWSER_PACING'):
            self.ledger.begin_submit('next', claim['claim_token'])
        self.assertEqual(phases.count('unknown_continuation_at_dispatch'), 2)
        self.assertIn('unknown_continuation_report', phases)
        self.assertEqual(self.ledger.get('next')['status'], 'dispatching')

    def test_immutable_evidence_rejects_update_delete_and_conflicting_replay(self):
        old = self.unknown(); new = self.new(); self.grant(new, old)
        for command in ('UPDATE unknown_continuations SET attestation=\'{}\'', 'DELETE FROM unknown_continuations'):
            with self.assertRaises(sqlite3.IntegrityError):
                self.ledger.db.execute(command)
        with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_CONFLICT'):
            self.grant(new, old, user_quote='A changed SYNTHETIC quote')

    def test_exact_replay_is_readback_after_dispatch_expiration_and_reconciliation(self):
        old = self.unknown(); new = self.new(); evidence = self.continuation(new, old)
        self.ledger.record_unknown_continuation('next', 'old', evidence)
        claim = self.ledger.claim('next', 'host')
        self.assertTrue(self.ledger.record_unknown_continuation('next', 'old', evidence)['duplicate'])
        self.ledger.begin_submit('next', claim['claim_token'])
        self.ledger.report_result('next', claim['claim_token'], self.result(claim))
        self.ledger.reconcile('old', self.result(old))
        self.time += 601
        self.ledger.host_verifier = lambda *_args: False
        before = self.ledger.events('next')
        result = self.ledger.record_unknown_continuation('next', 'old', evidence)
        self.assertTrue(result['duplicate']); self.assertEqual(result['status'], 'host_reported_success')
        self.assertEqual(self.ledger.events('next'), before)
        with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_CONFLICT'):
            self.ledger.record_unknown_continuation('next', 'old', {**evidence, 'user_quote': 'Changed synthetic quote'})
        with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_CONFLICT'):
            self.ledger.record_unknown_continuation('next', 'wrong-old', evidence)

    def test_terminal_new_action_without_existing_record_cannot_gain_exception(self):
        old = self.unknown(); new = self.new()
        self.ledger.cancel('next', 'SYNTHETIC cancellation')
        with self.assertRaisesRegex(ValueError, 'ERR_STATE'):
            self.grant(new, old)

    def publication(self, ident='publication', content=None, assets=None, **payload_overrides):
        assets = assets if assets is not None else [('SYNTHETIC image ' + ident).encode()]
        paths, manifest = [], []
        for index, data in enumerate(assets):
            path = self.base / (ident + '-' + str(index) + '.png'); path.write_bytes(data)
            paths.append(str(path)); manifest.append({'path': str(path), 'size': len(data),
                                                     'sha256': cloud.hashlib.sha256(data).hexdigest()})
        payload = {'title': 'SYNTHETIC title ' + ident, 'content': content or 'SYNTHETIC independent body ' + ident,
                   'images': paths, 'schedule_at': self.stamp(7200), **payload_overrides}
        return self.ready(ident, operation='publish_content', payload=payload,
                          target={'url': 'https://creator.xiaohongshu.com/publish/publish'}, media_manifest=manifest)

    def report_unknown(self, action):
        self.enabled(); claim = self.ledger.claim(action['operation_id'], 'host')
        self.ledger.begin_submit(action['operation_id'], claim['claim_token'])
        self.ledger.report_result(action['operation_id'], claim['claim_token'], self.result(
            claim, status='submitted_unknown', evidence_kind='uncertain', details='SYNTHETIC ended call, unknown application'))
        return claim

    def test_new_image_publication_can_skip_one_reported_comment_without_changing_it(self):
        old = self.unknown(); new = self.publication()
        before = self.ledger.get('old'); old_events = self.ledger.events('old')
        self.grant(new, old)
        claim = self.ledger.claim(new['operation_id'], 'host')
        self.ledger.begin_submit(new['operation_id'], claim['claim_token'])
        self.assertEqual(self.ledger.get('old'), before)
        self.assertEqual(self.ledger.events('old'), old_events)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM unknown_continuations').fetchone()[0], 0)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM publication_unknown_continuations').fetchone()[0], 1)

    def test_independent_publication_requires_new_copy_and_new_media(self):
        old = self.report_unknown(self.publication('old-image'))
        new = self.publication('new-image')
        self.grant(new, old)
        claim = self.ledger.claim('new-image', 'host'); self.ledger.begin_submit('new-image', claim['claim_token'])
        self.assertEqual(self.ledger.get('old-image')['status'], 'submitted_unknown')
        self.assertEqual(self.ledger.get('new-image')['status'], 'submitted_unknown')

    def test_changed_schedule_title_and_metadata_cannot_hide_same_publication_copy(self):
        old = self.report_unknown(self.publication('old-image', content='Fixture original body.'))
        for n, body in enumerate(('Fixture original body.', 'Ｆｉｘｔｕｒｅ ORIGINAL  BODY!')):
            new = self.publication('copy-' + str(n), content=body, schedule_at=self.stamp(10800),
                                   title='Changed title', tags=['different tag'], approval_ref='different inert provenance')
            with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_CONTENT'):
                self.grant(new, old)

    def test_renamed_reordered_or_duplicated_images_cannot_hide_same_asset_set(self):
        old = self.report_unknown(self.publication('old-image', assets=[b'SYNTHETIC A', b'SYNTHETIC B']))
        for n, images in enumerate(([b'SYNTHETIC B', b'SYNTHETIC A'],
                                     [b'SYNTHETIC A', b'SYNTHETIC B', b'SYNTHETIC A'])):
            new = self.publication('same-assets-' + str(n), assets=images, schedule_at=self.stamp(10800))
            with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_MEDIA'):
                self.grant(new, old)

    def test_existing_exact_semantic_dedup_still_blocks_disguised_fresh_id(self):
        old = self.report_unknown(self.publication('old-image', assets=[b'SYNTHETIC fixed image']))
        with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_DUPLICATE'):
            self.publication('copy-id', content=old['binding']['payload']['content'], assets=[b'SYNTHETIC fixed image'],
                             title=old['binding']['payload']['title'])

    def test_publication_requires_a_fully_bound_manifest_before_exception(self):
        old = self.unknown()
        for n, payload in enumerate(({'title': 'SYNTHETIC', 'content': 'Different body', 'images': ['/missing.png']},
                                     {'title': 'SYNTHETIC', 'content': 'Different body', 'images': [], 'long_article_template': 'template'},
                                     {'title': 'SYNTHETIC', 'content': 'Different body', 'images': ['https://example.invalid/media.png']})):
            new = self.ready('unbound-' + str(n), operation='publish_content', payload=payload, target={'surface': 'creator'})
            with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_MEDIA'):
                self.grant(new, old)

    def test_new_publication_does_not_reuse_unknown_comment_as_its_body(self):
        old = self.unknown()
        new = self.publication(content=old['binding']['payload']['content'])
        with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_CONTENT'):
            self.grant(new, old)

    def test_publications_require_separate_bound_grants_for_every_unknown(self):
        old_comment = self.unknown(); earlier = self.publication('earlier-publication')
        self.grant(earlier, old_comment); old_publication = self.report_unknown(earlier)
        new = self.publication('next-publication'); self.grant(new, old_comment)
        with self.assertRaisesRegex(ValueError, 'ERR_ACCOUNT_UNCERTAIN'):
            self.ledger.claim('next-publication', 'host')
        self.grant(new, old_publication)
        value = self.ledger.get('next-publication')
        self.assertIsNone(value['unknown_continuation'])
        self.assertEqual(len(value['unknown_continuations']), 2)
        self.assertTrue(self.grant(new, old_comment)['duplicate'])
        self.ledger.close(); self.ledger = cloud.CloudActions(self.config, clock=lambda: self.time)
        self.assertEqual(len(self.ledger.get('next-publication')['unknown_continuations']), 2)
        claim = self.ledger.claim('next-publication', 'host')
        self.ledger.begin_submit('next-publication', claim['claim_token'])
        self.assertEqual(self.ledger.get('old')['status'], 'submitted_unknown')
        self.assertEqual(self.ledger.get('earlier-publication')['status'], 'submitted_unknown')

    def test_additive_publication_evidence_is_immutable_and_old_comment_record_survives(self):
        old = self.unknown(); comment = self.new(); self.grant(comment, old)
        legacy_before = self.ledger.get('next')['unknown_continuation']
        publication = self.publication(); self.grant(publication, old)
        for command in ("UPDATE publication_unknown_continuations SET attestation='{}'", 'DELETE FROM publication_unknown_continuations'):
            with self.assertRaises(sqlite3.IntegrityError): self.ledger.db.execute(command)
        self.ledger.close(); self.ledger = cloud.CloudActions(self.config, clock=lambda: self.time)
        self.assertEqual(self.ledger.get('next')['unknown_continuation'], legacy_before)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM unknown_continuations').fetchone()[0], 1)

    def test_typed_standing_scope_preserves_genuine_earlier_approval_timestamp(self):
        old = self.unknown(); new = self.publication()
        actual_confirmation = self.stamp(-3600)
        with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_EXPIRED'):
            self.grant(new, old, confirmed_at=actual_confirmation)
        value = self.grant(new, old, confirmed_at=actual_confirmation,
                           instruction_scope='future_independent_image_publications')
        self.assertEqual(value['unknown_continuation']['attestation']['confirmed_at'], actual_confirmation)
        claim = self.ledger.claim('publication', 'host'); self.ledger.begin_submit('publication', claim['claim_token'])

    def test_standing_scope_does_not_expand_comments_or_other_actions_or_expiration(self):
        old = self.unknown(); comment = self.new()
        with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_SCOPE'):
            self.grant(comment, old, confirmed_at=self.stamp(-1), instruction_scope='future_independent_image_publications')
        new = self.publication()
        for invalid in (True, 'all_actions', '', None):
            with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_SCOPE'):
                self.grant(new, old, instruction_scope=invalid)
        with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_EXPIRED'):
            self.grant(new, old, confirmed_at=self.stamp(-3600), expires_at=self.stamp(-1),
                       instruction_scope='future_independent_image_publications')

    def test_publication_continuation_rehashes_assets_at_claim_and_submission(self):
        old = self.unknown(); new = self.publication(); self.grant(new, old)
        path = self.base / 'publication-0.png'; original = path.read_bytes(); path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'ERR_MEDIA_CHANGED'):
            self.ledger.claim('publication', 'host')
        path.write_bytes(original); claim = self.ledger.claim('publication', 'host'); path.write_bytes(b'changed again')
        with self.assertRaisesRegex(ValueError, 'ERR_MEDIA_CHANGED'):
            self.ledger.begin_submit('publication', claim['claim_token'])
        self.assertEqual(self.ledger.get('publication')['status'], 'dispatching')

    def test_publication_keeps_normal_review_authorization_identity_and_pacing_gates(self):
        old = self.unknown(); new = self.publication(); self.grant(new, old)
        self.data['allow_write'] = False; self.save_config()
        with self.assertRaisesRegex(ValueError, 'ERR_WRITE_DISABLED'): self.ledger.claim('publication', 'host')
        self.enabled(); self.ledger.host_verifier = lambda phase, _binding, _evidence: phase != 'claim'
        with self.assertRaisesRegex(ValueError, 'ERR_HOST_VERIFIER_DENIED: claim'): self.ledger.claim('publication', 'host')
        self.ledger.host_verifier = None; self.time += 121
        with self.assertRaisesRegex(ValueError, 'ERR_IDENTITY_STALE'): self.ledger.claim('publication', 'host')
        self.ledger.observe_identity('publication', self.identity())
        claim = self.ledger.claim('publication', 'host')
        def deny_step(phase, _binding, evidence):
            if phase == 'begin_submit':
                self.assertTrue(evidence['attempt_id']); raise ValueError('ERR_BROWSER_PACING: SYNTHETIC block')
            return True
        self.ledger.host_verifier = deny_step
        with self.assertRaisesRegex(ValueError, 'ERR_BROWSER_PACING'):
            self.ledger.begin_submit('publication', claim['claim_token'])

    def test_publications_do_not_bypass_other_mutation_types_or_unreported_calls(self):
        old = self.unknown(operation='like_feed', payload={'feed_id': 'a' * 24}, target={'feed_id': 'a' * 24})
        with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_SCOPE'):
            self.grant(self.publication(), old)
        self.ledger.reconcile('old', self.result(old))
        action = self.publication('unreported'); claim = self.ledger.claim('unreported', 'host')
        self.ledger.begin_submit('unreported', claim['claim_token']); self.time += 121
        with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_UNREPORTED'):
            self.grant(self.publication('after-unreported'), claim)

    def test_comment_continuation_does_not_expand_to_old_publication(self):
        old = self.report_unknown(self.publication('old-image'))
        with self.assertRaisesRegex(ValueError, 'ERR_CONTINUATION_SCOPE'):
            self.grant(self.new(), old)

    def test_no_browser_network_or_subprocess_invoked(self):
        old = self.unknown(); new = self.new()
        with mock.patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             mock.patch('subprocess.Popen', side_effect=AssertionError('subprocess forbidden')):
            self.grant(new, old)
            claim = self.ledger.claim('next', 'host')
            self.ledger.begin_submit('next', claim['claim_token'])


if __name__ == '__main__':
    unittest.main()
