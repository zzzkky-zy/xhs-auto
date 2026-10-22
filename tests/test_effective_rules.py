"""Offline rule explanations do not authorize or execute operations."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from effective_rules import explain_effective_rules, check_comment_allocation
from content_review import digest


class EffectiveRulesTests(unittest.TestCase):
    def setUp(self):
        self.config = {'expected_user_id': 'exact-owner', 'allow_write': False}
        self.profile = {'strategy': {'A': 40, 'B': 40, 'C_max': 20}, 'allow_c': True,
            'confirmation_sources': {'strategy': 'instruction:1', 'allow_c': 'instruction:1'}}
        self.context = {'account_id': 'exact-owner', 'run_id': 'old-run', 'batch_kind': 'legacy',
            'evaluated_at': '2026-10-22T02:00:00Z', 'profile_version': digest(self.profile)}

    def explain(self, **kwargs):
        self.context['profile_version'] = digest(self.profile)
        return explain_effective_rules(config=self.config, profile=self.profile, context=self.context, **kwargs)

    def rule(self, field, value):
        return {'field': field, 'value': value, 'source_ref': 'user:2',
            'issued_at': '2026-10-22T01:00:00Z',
            'scope': {k: self.context[k] for k in ('account_id', 'run_id', 'batch_kind')}}

    def test_default_is_legacy_not_false_ninety_conflict(self):
        out = self.explain(runtime_values={'publication_spacing_seconds': 3600})
        self.assertFalse(out['runtime_mismatches'])
        self.assertFalse(out['execution_authorized'])
        self.assertTrue(out['diagnostic_only'])

    def test_explicit_ninety_reports_executor_mismatch(self):
        out = self.explain(user_rules=[self.rule('publication_spacing_seconds', 5400)],
                           runtime_values={'publication_spacing_seconds': 3600})
        self.assertEqual(len(out['runtime_mismatches']), 1)
        self.assertEqual(out['effective_rules']['publication_spacing_seconds']['source_ref'], 'user:2')

    def test_future_path_and_actual_profile_keys(self):
        self.context['batch_kind'] = 'future'
        self.profile['future_content_policy'] = {'is_original': False, 'group_chat': 'Exact group', 'activity': 'Exact activity'}
        self.profile['confirmation_sources']['future_content_policy'] = 'user:3'
        out = self.explain(runtime_values={'publication_spacing_seconds': 5400})
        self.assertFalse(out['runtime_mismatches'])
        self.assertEqual(out['effective_rules']['required_group']['value'], 'Exact group')
        self.assertFalse(out['effective_rules']['is_original']['value'])

    def test_internal_interval_does_not_change_other_budgets(self):
        before = self.explain()['effective_rules']
        after = self.explain(user_rules=[self.rule('browser_step_interval_seconds', [1, 5])])['effective_rules']
        for field in before:
            if field != 'browser_step_interval_seconds':
                self.assertEqual(before[field], after[field])

    def test_same_timestamp_conflict_is_not_chosen(self):
        out = self.explain(user_rules=[self.rule('read_max_per_hour', 100), self.rule('read_max_per_hour', 200)])
        self.assertTrue(out['conflicts'])
        self.assertEqual(out['effective_rules']['read_max_per_hour']['value'], 120)

    def test_later_instruction_wins(self):
        earlier = self.rule('read_max_per_hour', 50)
        earlier['issued_at'] = '2026-10-21T01:00:00Z'
        out = self.explain(user_rules=[earlier, self.rule('read_max_per_hour', 80)])
        self.assertEqual(out['effective_rules']['read_max_per_hour']['value'], 80)

    def test_mandatory_is_not_overridden(self):
        constraint = dict(self.rule('read_max_per_hour', 30), operator='maximum', expires_at='2026-10-23T00:00:00Z')
        out = self.explain(user_rules=[self.rule('read_max_per_hour', 80)], mandatory_rules=[constraint])
        self.assertTrue(out['conflicts'])
        self.assertFalse(out['execution_authorized'])

    def test_expired_constraint_is_unresolved(self):
        constraint = dict(self.rule('read_max_per_hour', 30), operator='maximum', expires_at='2026-10-21T00:00:00Z')
        self.assertTrue(self.explain(mandatory_rules=[constraint])['unresolved'])

    def test_wrong_scope_does_not_inherit(self):
        rule = self.rule('publication_spacing_seconds', 1)
        rule['scope']['run_id'] = 'first-batch'
        out = self.explain(user_rules=[rule])
        self.assertEqual(out['effective_rules']['publication_spacing_seconds']['value'], 3600)
        self.assertTrue(out['ignored'])

    def test_exception_requires_existing_validator(self):
        self.config['cloud_schedule_overlap_exception'] = {'first_run_id': 'old-run'}
        self.assertTrue(self.explain()['unresolved'])

    def test_bad_source_scope_field(self):
        for change in ({'source_ref': ''}, {'scope': {}}, {'field': 'authorize_send'}, {'issued_at': '2026-10-23T00:00:00Z'}):
            with self.subTest(change=change):
                rule = self.rule('read_max_per_hour', 30)
                rule.update(change)
                self.assertTrue(self.explain(user_rules=[rule])['unresolved'])

    def test_profile_and_identity_are_exact(self):
        self.context['profile_version'] = 'not-a-fingerprint'
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            explain_effective_rules(config=self.config, profile=self.profile, context=self.context)
        self.config['expected_user_id'] = 'different'
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.explain()

    def test_allocation_c_is_cap_not_required_quota(self):
        strategy = self.profile['strategy']
        for counts in ({'A': 25, 'B': 25, 'C': 0}, {'A': 25, 'B': 24, 'C': 1}):
            self.assertTrue(check_comment_allocation(strategy, counts, allow_c=True))
        self.assertFalse(check_comment_allocation(strategy, {'A': 19, 'B': 20, 'C': 11}, allow_c=True))
        self.assertFalse(check_comment_allocation(strategy, {'A': 25, 'B': 24, 'C': 1}, allow_c=False))

    def test_fallback_is_preserved(self):
        strategy = {'A': 40, 'B': 40, 'C_max': 20, 'fallback': {'A': 60, 'B': 40, 'C': 0}}
        self.assertTrue(check_comment_allocation(strategy, {'A': 30, 'B': 20, 'C': 0}, allow_c=True))

    def test_deterministic_and_no_input_mutation(self):
        args = {'config': self.config, 'profile': self.profile, 'context': self.context,
                'user_rules': [self.rule('browser_step_interval_seconds', [1, 5])]}
        before = deepcopy(args)
        self.assertEqual(explain_effective_rules(**args), explain_effective_rules(**args))
        self.assertEqual(args, before)
        self.assertEqual(self.explain()['runtime_compatibility'], 'unverified')

    def test_mandatory_boolean_cannot_be_integer_zero(self):
        rule = dict(self.rule('is_original', 0), operator='equal', expires_at='2026-10-23T00:00:00Z')
        out = self.explain(user_rules=[self.rule('is_original', False)], mandatory_rules=[rule])
        self.assertTrue(out['needs_decision'])
        self.assertTrue(out['unresolved'])

    def test_mandatory_source_must_be_nonempty_string(self):
        for source in ({'fake': 'source'}, [], 1, True, '', '   '):
            with self.subTest(source=source):
                rule = dict(self.rule('read_max_per_hour', 120), operator='equal',
                            expires_at='2026-10-23T00:00:00Z', source_ref=source)
                self.assertTrue(self.explain(mandatory_rules=[rule])['unresolved'])

    def test_profile_sources_are_not_string_coerced(self):
        for field in ('strategy', 'allow_c', 'future_content_policy'):
            for source in ({'not': 'ref'}, [], False, 0, '', '  '):
                with self.subTest(field=field, source=source):
                    self.context['batch_kind'] = 'future'
                    self.profile['future_content_policy'] = {'is_original': False}
                    self.profile['confirmation_sources'][field] = source
                    out = self.explain()
                    self.assertTrue(out['needs_decision'])
                    self.assertTrue(any(item['field'] == field or
                        (field == 'strategy' and item['field'] == 'comment_strategy')
                        for item in out['unresolved']))
                    self.profile['confirmation_sources'][field] = 'user:valid'

    def test_numeric_bool_float_nan_not_coerced(self):
        for value in (True, False, float('nan'), float('inf'), 120.0, '120'):
            with self.subTest(value=value):
                rule = dict(self.rule('read_max_per_hour', value), operator='equal',
                            expires_at='2026-10-23T00:00:00Z')
                self.assertTrue(self.explain(mandatory_rules=[rule])['unresolved'])
                self.assertTrue(self.explain(user_rules=[self.rule('read_max_per_hour', value)])['unresolved'])
        for value in ([True, 5], [1, float('nan')], [1, 5.0]):
            self.assertTrue(self.explain(user_rules=[self.rule('browser_step_interval_seconds', value)])['unresolved'])

    def test_defaults_track_existing_executor_sources(self):
        source = (ROOT / 'runtime/cloud_pacing.py').read_text()
        for fragment in ("'read', (10, 20)", "'search', (60, 90)", "'write', (60, 180)", "'browser_step',(10,20)", "'read_max_per_hour', 120", "'search_max_per_hour', 20"):
            self.assertIn(fragment, source)
        self.assertIn('spacing = 5400 if future else 3600', (ROOT / 'runtime/cloud_batch.py').read_text())


if __name__ == '__main__':
    unittest.main()
