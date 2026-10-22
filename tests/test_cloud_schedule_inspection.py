"""Synthetic schedule-inspection ledger tests; no browser or platform actions."""
import unittest
import test_cloud_unknown_continuation as fixtures
from test_cloud_actions import cloud


class ScheduleInspectionTests(unittest.TestCase):
    for _name in ('setUp', 'tearDown', 'save_config', 'stamp', 'prepare', 'attestation',
                  'identity', 'ready', 'enabled', 'claimed', 'submitted', 'result',
                  'unknown', 'continuation', 'grant', 'publication', 'report_unknown'):
        locals()[_name] = getattr(fixtures.UnknownContinuationTests, _name)

    def inspection_args(self, ident='inspect', content='Synthetic distinct inspection body', data=b'inspection image'):
        path = self.base / (ident + '.png'); path.write_bytes(data)
        return dict(operation=cloud.SCHEDULE_INSPECTION,
                    payload={'title': 'Synthetic title', 'content': content, 'images': [str(path)], 'run_id': 'fixture-run'},
                    target={'inspection_id': ident}, media_manifest=[{'path': str(path), 'size': len(data),
                    'sha256': cloud.hashlib.sha256(data).hexdigest()}])

    def test_distinct_upload_scope_and_publish_allowlist_without_publication_receipt(self):
        args = self.inspection_args(); value = self.ready('inspect', **args)
        self.assertIsNone(value['semantic_digest'])
        self.assertTrue(value['binding']['mutation']); self.assertTrue(value['binding']['identity_required'])
        self.assertFalse(value['inspection_scope']['final_publish_allowed'])
        self.data.update(allow_write=True, allowed_write_operations=['publish_content']); self.save_config()
        claim = self.ledger.claim('inspect', 'host'); self.ledger.begin_submit('inspect', claim['claim_token'])
        with self.assertRaisesRegex(ValueError, 'ERR_EVIDENCE_REQUIRED'):
            self.ledger.report_result('inspect', claim['claim_token'], self.result(claim, evidence_kind='platform_receipt'))
        self.ledger.report_result('inspect', claim['claim_token'], self.result(claim, details='Synthetic schedule controls observed only'))
        # A successful inspection does not consume the eventual publication semantic identity.
        payload = dict(args['payload']); payload['schedule_at'] = self.stamp(7200)
        self.prepare('actual-publish', operation='publish_content', payload=payload, media_manifest=args['media_manifest'])

    def test_target_schedule_and_images_required(self):
        for change in ({'target': {}}, {'target': {'inspection_id': ''}},
                       {'payload': {'title': 'x', 'content': 'y', 'images': []}},
                       {'payload': {'title': 'x', 'content': 'y', 'images': ['/tmp/fixture'], 'schedule_at': ''}}):
            args = self.inspection_args(); args.update(change)
            with self.assertRaises(ValueError): self.prepare('inspect', **args)

    def test_uses_publication_schema_validator(self):
        seen = []
        self.ledger.payload_validator = lambda op, payload: seen.append(op)
        self.prepare('inspect', **self.inspection_args())
        self.assertEqual(seen, ['publish_content'])

    def test_write_identity_and_changed_media_fail_closed(self):
        args = self.inspection_args(); self.ready('inspect', **args)
        with self.assertRaisesRegex(ValueError, 'ERR_WRITE_DISABLED'): self.ledger.claim('inspect', 'host')
        self.enabled(); self.ledger.observe_identity('inspect', self.identity(account_id='wrong'))
        with self.assertRaisesRegex(ValueError, 'ERR_IDENTITY_MISMATCH'): self.ledger.claim('inspect', 'host')
        self.ledger.observe_identity('inspect', self.identity())
        claim = self.ledger.claim('inspect', 'host')
        (self.base / 'inspect.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'ERR_MEDIA_CHANGED'): self.ledger.begin_submit('inspect', claim['claim_token'])

    def test_standing_continuation_preserves_unknown_and_rejects_reused_content_or_media(self):
        old = self.report_unknown(self.publication('old-image', content='Synthetic old body', assets=[b'old image']))
        for ident, body, image, error in [('same-copy', 'Synthetic old body', b'new', 'ERR_CONTINUATION_CONTENT'),
                                         ('same-media', 'Different synthetic copy', b'old image', 'ERR_CONTINUATION_MEDIA')]:
            new = self.ready(ident, **self.inspection_args(ident, body, image))
            with self.assertRaisesRegex(ValueError, error):
                self.grant(new, old, instruction_scope='future_independent_image_publications')
        new = self.ready('inspect', **self.inspection_args())
        self.grant(new, old, confirmed_at=self.stamp(-100), instruction_scope='future_independent_image_publications')
        claim = self.ledger.claim('inspect', 'host'); self.ledger.begin_submit('inspect', claim['claim_token'])
        self.assertEqual(self.ledger.get('old-image')['status'], 'submitted_unknown')
        with self.assertRaisesRegex(ValueError, 'ERR_STATE'): self.ledger.claim('inspect', 'host')


if __name__ == '__main__': unittest.main()
