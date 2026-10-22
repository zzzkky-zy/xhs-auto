"""Offline synthetic evidence tests; no user account, browser or external writes."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_cloud_daily_comments as fixtures
from cloud_comment_boundary import CloudCommentBoundary


class BoundaryTests(unittest.TestCase):
    setUp = fixtures.DailyCommentTests.setUp
    tearDown = fixtures.DailyCommentTests.tearDown
    add = fixtures.DailyCommentTests.add

    def setup_boundary(self, verified=True):
        self.boundary = CloudCommentBoundary(self.actions, evidence_verifier=(lambda *a: True) if verified else None)
        self.op = self.add('prepared')

    def evidence(self, **kwargs):
        return dict(evidence_id='e'+str(len(self.actions.db.execute('SELECT * FROM comment_boundary_evidence').fetchall())),
                    expected_account='platform-a', source_ref='user-message:fixture', source_quote='synthetic user instruction',
                    source_read_evidence_ref='verified-read:fixture', observed_at='2026-10-20T04:00:00Z', **kwargs)

    def gap(self, first=None, last=None, **kwargs):
        return self.boundary.record(self.op, 'history_gap', self.evidence(first_date=first, last_date=last, **kwargs))

    def coverage(self, upper=0):
        return self.boundary.record(self.op, 'daily_coverage', self.evidence(date='2026-10-20', timezone='Asia/Shanghai',
            expires_at='2026-10-20T16:00:00Z', coverage_scope='account_activity_upper_bound', total_upper_bound=upper,
            undated_risk_accounted=True))

    def scope(self, **changes):
        data = dict(scope_id='scope-1', channel='public_comment', private_messages=False, benefit_fulfilment=False,
                    semantic_scope='public invitation only', commitment_fingerprint='free-trial-v1', risk_fingerprint='risk-v1')
        data.update(changes)
        return self.boundary.record(self.op, 'public_scope', self.evidence(**data))

    def grant(self, **changes):
        data = dict(date='2026-10-20', timezone='Asia/Shanghai', expires_at='2026-10-20T16:00:00Z',
                    run_id='run-1', profile_version='profile-1', scope_id='scope-1',
                    commitment_fingerprint='free-trial-v1', risk_fingerprint='risk-v1')
        data.update(changes)
        return self.boundary.record(self.op, 'daily_grant', self.evidence(**data))

    def test_normal_managed_flow_needs_no_new_permission(self):
        self.setup_boundary(False)
        self.assertEqual(self.boundary.guard(self.op)['projected_total'], 1)
        self.assertFalse(self.boundary.status()['account_wide_external_activity_verified'])

    def test_empty_ledger_gap_not_zero(self):
        self.setup_boundary()
        self.gap()
        self.assertIsNone(self.boundary.status()['current_day_upper_bound'])
        with self.assertRaisesRegex(ValueError, 'COVERAGE_REQUIRED'): self.boundary.guard(self.op)

    def test_unverified_coverage_cannot_restore_capacity(self):
        self.setup_boundary(False)
        self.gap()
        report = self.coverage()
        self.assertFalse(report['independently_verified'])
        with self.assertRaisesRegex(ValueError, 'COVERAGE_REQUIRED'): self.boundary.guard(self.op)

    def test_verified_upper_bound_and_snapshot_not_double_counted(self):
        self.setup_boundary()
        self.add()
        self.gap()
        self.coverage(49)
        self.assertEqual(self.boundary.guard(self.op)['projected_total'], 50)
        self.add(run='other-run', alias='other-alias')
        with self.assertRaisesRegex(ValueError, 'DAILY_COMMENT_LIMIT'): self.boundary.guard(self.op)

    def test_bound_cannot_be_lower_than_real_snapshot(self):
        self.setup_boundary()
        self.add()
        with self.assertRaisesRegex(ValueError, 'below known'): self.coverage(0)

    def test_old_gap_does_not_block_today_but_target_exclusion_remains(self):
        self.setup_boundary()
        self.gap('2026-10-16', '2026-10-17')
        self.assertEqual(self.boundary.guard(self.op)['projected_total'], 1)
        self.gap('2026-10-16', '2026-10-17', excluded_target_ids=['a'*24])
        with self.assertRaisesRegex(ValueError, 'HISTORY_EXCLUDED'): self.boundary.guard(self.op)

    def test_known_count_undated_unknown_uses_existing_conservative_charge(self):
        self.setup_boundary()
        self.add('submitted_unknown', submitted=None)
        self.assertEqual(self.boundary.guard(self.op)['projected_total'],2)

    def test_public_invitation_never_dm_or_fulfilment(self):
        self.setup_boundary()
        for change in ({'channel':'private_message'}, {'private_messages':True}, {'benefit_fulfilment':True}):
            with self.assertRaisesRegex(ValueError, 'not DM'): self.scope(**change)
        self.assertFalse(self.scope()['grants_execution_authority'])

    def test_finite_grant_requires_verified_evidence_not_source_string(self):
        self.setup_boundary(False)
        self.scope(); self.grant()
        with self.assertRaisesRegex(ValueError, 'VERIFICATION_REQUIRED'): self.boundary.guard(self.op, 'profile-1')

    def test_finite_grant_expires_at_midnight_and_read_still_allowed(self):
        self.setup_boundary()
        self.scope(); self.grant()
        self.assertEqual(self.boundary.guard(self.op, 'profile-1')['projected_total'], 1)
        self.now = fixtures.stamp('2026-10-20T16:00:00Z')
        with self.assertRaisesRegex(ValueError, 'new run/day'): self.boundary.guard(self.op, 'profile-1')
        self.actions.prepare('search_feeds', {'keyword':'fixture'}, expected_account='platform-a', target={'keyword':'fixture'}, user_source='fixture', operation_id='read')
        self.assertFalse(self.boundary.guard('read')['applies'])
        self.assertEqual(self.boundary.status()['date'], '2026-10-21')

    def test_profile_change_and_risk_change_cannot_reuse(self):
        self.setup_boundary()
        self.scope(); self.grant()
        with self.assertRaisesRegex(ValueError, 'pinned profile'): self.boundary.guard(self.op, 'profile-2')
        with self.assertRaisesRegex(ValueError, 'changed commitment'): self.grant(risk_fingerprint='risk-v2')

    def test_revocation_blocks_old_grant(self):
        self.setup_boundary()
        self.scope(); self.grant(); self.scope(revoked=True)
        with self.assertRaisesRegex(ValueError, 'active public scope'): self.boundary.guard(self.op, 'profile-1')

    def test_wrong_account_and_expiry_rejected(self):
        self.setup_boundary()
        data=self.evidence(first_date=None,last_date=None);data['expected_account']='other'
        with self.assertRaisesRegex(ValueError,'account mismatch'): self.boundary.record(self.op,'history_gap',data)
        self.scope()
        with self.assertRaisesRegex(ValueError,'finite current-day'): self.grant(expires_at='2026-10-21T16:00:00Z')

    def test_old_snapshot_key_reconciliation_never_releases_upper_bound(self):
        self.setup_boundary()
        unknown=self.add('submitted_unknown')
        self.gap(); self.coverage(49)
        self.actions.db.execute("UPDATE actions SET status='not_applied' WHERE operation_id=?", (unknown,))
        self.assertEqual(self.boundary.guard(self.op)['projected_total'],50)

    def test_author_exclusions_from_fresh_host_observation(self):
        self.setup_boundary()
        self.gap('2026-10-16','2026-10-17',excluded_author_ids=['prior-author'])
        with self.assertRaisesRegex(ValueError,'HISTORY_EXCLUDED'):
            self.boundary.guard(self.op,author_evidence={'note_author_id':'prior-author'})

    def test_explicit_limited_mode_requires_evidence_without_blocking_status(self):
        import json
        self.setup_boundary()
        self.data['cloud_comment_authorization_mode']='limited_daily'
        self.config.write_text(json.dumps(self.data))
        with self.assertRaisesRegex(ValueError,'DAILY_GRANT'): self.boundary.guard(self.op)
        self.assertEqual(self.boundary.status()['today_confirmed'],0)

    def test_account_risk_not_cleared_by_plugin_only_coverage(self):
        self.setup_boundary()
        self.gap(coverage_scope='account_activity_upper_bound')
        data=self.evidence(date='2026-10-20',timezone='Asia/Shanghai',expires_at='2026-10-20T16:00:00Z',
            coverage_scope='known_plugin_executions',total_upper_bound=0)
        self.boundary.record(self.op,'daily_coverage',data)
        with self.assertRaisesRegex(ValueError,'COVERAGE_REQUIRED'): self.boundary.guard(self.op)

    def test_caller_cannot_choose_ledger_snapshot_or_claim_verified(self):
        self.setup_boundary(False)
        self.add()
        data=self.evidence(date='2026-10-20',timezone='Asia/Shanghai',expires_at='2026-10-20T16:00:00Z',
            coverage_scope='known_plugin_executions',total_upper_bound=1,
            snapshot_charged_keys=[],independently_verified=True)
        result=self.boundary.record(self.op,'daily_coverage',data)
        self.assertEqual(len(result['snapshot_charged_keys']),1)
        self.assertFalse(result['independently_verified'])

    def test_prior_day_coverage_cannot_silence_unknown_date_gap(self):
        self.setup_boundary()
        self.gap(); self.coverage()
        self.now=fixtures.stamp('2026-10-20T16:00:00Z')
        with self.assertRaisesRegex(ValueError,'COVERAGE_REQUIRED'): self.boundary.guard(self.op)

    def test_narrowed_semantic_scope_cannot_reuse_old_grant(self):
        self.setup_boundary()
        self.scope(); self.grant(); self.scope(semantic_scope='public advice only; no trial invitation')
        with self.assertRaisesRegex(ValueError,'scope changed or narrowed'):
            self.boundary.guard(self.op,'profile-1')

    def test_expired_finite_grant_does_not_prevent_late_result_accounting(self):
        import json
        self.setup_boundary()
        self.scope(); self.grant(expires_at='2026-10-20T05:00:00Z')
        self.now=fixtures.stamp('2026-10-20T06:00:00Z')
        with self.assertRaisesRegex(ValueError,'expired'): self.boundary.guard(self.op,'profile-1')
        # Accounting is intentionally independent of write admission. A delayed
        # receipt remains visible even though it must never authorize a resend.
        self.actions.db.execute("UPDATE actions SET status='submitted_unknown',submitted_at=? WHERE operation_id=?",
                                (fixtures.stamp('2026-10-20T04:59:00Z'),self.op))
        row=self.actions._row(self.op)
        self.actions.reconcile(self.op,dict(operation_id=self.op,binding_digest=row['binding_digest'],
            attempt_id=row['attempt_id'],account_id='platform-a',browser_context=row['claim_context'],
            status='host_reported_success',observed_at='2026-10-20T05:01:00Z',evidence_ref='late-visible-receipt',
            evidence_kind='visible_result',details='synthetic delayed observation'))
        self.assertEqual(self.boundary.status()['today_confirmed'],1)

    def test_later_gap_invalidates_older_coverage(self):
        self.setup_boundary()
        self.coverage(); self.gap()
        with self.assertRaisesRegex(ValueError,'COVERAGE_REQUIRED'): self.boundary.guard(self.op)

    def test_new_known_count_undated_unknown_adds_charge_not_new_coverage_requirement(self):
        self.setup_boundary()
        self.gap(); self.coverage()
        self.add('submitted_unknown',submitted=None)
        self.assertEqual(self.boundary.guard(self.op)['projected_total'],2)

    def test_individual_finite_grant_revocation_blocks_dispatch(self):
        self.setup_boundary()
        self.scope(); self.grant(); self.grant(revoked=True)
        with self.assertRaisesRegex(ValueError,'grant revoked'): self.boundary.guard(self.op,'profile-1')

    def test_old_dated_gap_does_not_invalidate_current_coverage(self):
        self.setup_boundary()
        self.gap(); self.coverage(3)
        self.gap('2026-10-16','2026-10-17')
        self.assertEqual(self.boundary.guard(self.op)['projected_total'],4)

    def test_grant_reuse_checks_current_semantics_not_only_saved_hashes(self):
        self.setup_boundary()
        self.scope(); self.grant()
        self.boundary.evidence_verifier=lambda phase,*args: phase != 'daily_grant_use'
        with self.assertRaisesRegex(ValueError,'current comment semantics'):
            self.boundary.guard(self.op,'profile-1')

    def test_default_host_cannot_reuse_verified_packet_without_source_adapter(self):
        self.setup_boundary()
        self.scope(); self.grant()
        self.boundary.evidence_verifier=None
        with self.assertRaisesRegex(ValueError,'VERIFICATION_REQUIRED'):
            self.boundary.guard(self.op,'profile-1')

    def test_same_scope_new_content_risk_must_pass_adapter(self):
        self.setup_boundary()
        self.scope(); self.grant()
        original=self.actions.get(self.op)['binding']['payload']['content']
        self.boundary.evidence_verifier=lambda phase,binding,packet: binding['payload']['content']==original
        second=self.add('prepared')
        with self.assertRaisesRegex(ValueError,'current comment semantics'):
            self.boundary.guard(second,'profile-1')

    def test_adapter_failure_blocks_write_but_not_read_or_result_accounting(self):
        self.setup_boundary()
        self.scope(); self.grant()
        def broken(*args): raise RuntimeError('sensitive host diagnostics')
        self.boundary.evidence_verifier=broken
        with self.assertRaisesRegex(ValueError,'verification unavailable') as error:
            self.boundary.guard(self.op,'profile-1')
        self.assertNotIn('sensitive',str(error.exception))
        self.assertEqual(self.boundary.status()['today_confirmed'],0)
        self.actions.prepare('search_feeds',{'keyword':'fixture'},expected_account='platform-a',
                             target={'keyword':'fixture'},user_source='fixture',operation_id='read')
        self.assertFalse(self.boundary.guard('read')['applies'])
        self.actions.db.execute("UPDATE actions SET status='submitted_unknown' WHERE operation_id=?",(self.op,))
        row=self.actions._row(self.op)
        self.actions.reconcile(self.op,dict(operation_id=self.op,binding_digest=row['binding_digest'],
            attempt_id=row['attempt_id'],account_id='platform-a',browser_context=row['claim_context'],
            status='host_reported_success',observed_at='2026-10-20T04:00:00Z',evidence_ref='fixture-receipt',
            evidence_kind='visible_result',details='synthetic receipt'))
        self.assertEqual(self.boundary.status()['today_confirmed'],1)

    def test_host_denial_not_overridden_by_verified_packet(self):
        self.setup_boundary()
        self.actions.host_verifier=lambda *a: False
        with self.assertRaisesRegex(ValueError,'HOST_VERIFIER_DENIED'): self.scope()

if __name__ == '__main__': unittest.main()
