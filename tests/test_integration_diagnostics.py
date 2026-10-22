import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import integration_diagnostics as diag


class IntegrationDiagnosticTests(unittest.TestCase):
    def test_failure_metadata_is_private_and_redacted_success_leaves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'diagnostics'
            self.assertIsNone(diag.export_failure(directory, 'fixture', [], {}, False))
            self.assertFalse(directory.exists())
            path = diag.export_failure(directory, 'fixture', [{'stage': 'runtime_probe_failed', 'probe_seconds': .768}],
                {'backend': 'token=SECRET\nERR_TASK_EXECUTOR /private/path\nGET /runtime 200\n[GIN] | 200 | 768.338005ms |'}, True)
            content = path.read_text()
            self.assertNotIn('SECRET', content); self.assertNotIn('/private/path', content)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads(content)['logs']['backend']['gin_durations'], ['768.338005ms'])

    def test_probe_accepts_768ms_within_total_deadline(self):
        now = [0.0]; timeouts = []
        def probe(url, timeout):
            timeouts.append(timeout); self.assertGreaterEqual(timeout, .768); now[0] += .768
            return io.StringIO('{"data":{"state_id":"fixture"}}')
        result = diag.await_runtime(mock.Mock(poll=lambda: None), 'synthetic', [], probe=probe, clock=lambda: now[0])
        self.assertEqual(result['data']['state_id'], 'fixture'); self.assertEqual(timeouts, [1.0])

    def test_deadline_is_total_and_dead_process_exits_early(self):
        now = [0.0]; timeouts = []
        def probe(url, timeout):
            timeouts.append(timeout); now[0] += timeout; raise TimeoutError()
        with self.assertRaisesRegex(AssertionError, 'deadline'):
            diag.await_runtime(mock.Mock(poll=lambda: None), 'synthetic', [], probe=probe,
                clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
        self.assertEqual(now[0], 5); self.assertLessEqual(max(timeouts), 1)
        with self.assertRaisesRegex(AssertionError, 'exited'):
            diag.await_runtime(mock.Mock(poll=lambda: 1), 'synthetic', [], probe=mock.Mock(side_effect=AssertionError('must not probe')))

    def test_invalid_ready_identity_is_not_accepted(self):
        with self.assertRaisesRegex(AssertionError, 'identity'):
            diag.await_runtime(mock.Mock(poll=lambda: None), 'synthetic', [], probe=lambda *a, **k: io.StringIO('{}'))

    def test_real_fixture_exports_on_failure_before_cleanup(self):
        import test_direct_integration
        class SyntheticFailure(test_direct_integration.DirectIntegrationTests):
            def runTest(self):
                self.backend_log = (self.root / 'backend.log').open('w+')
                self.backend_log.write('secret=DO_NOT_EXPORT ERR_TASK_EXECUTOR')
                self.diagnostic_events.append({'stage': 'synthetic_failure', 'elapsed': 8.0})
                self.fail('INTENTIONAL synthetic diagnostics fixture')
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'diagnostics'
            with mock.patch.dict('os.environ', {'XHS_TEST_DIAGNOSTIC_DIR': str(directory)}):
                result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite([SyntheticFailure()]))
            if result.skipped:
                self.skipTest('native fixture unavailable')
            self.assertEqual(len(result.failures), 1)
            reports = list(directory.glob('failure-*.json'))
            self.assertEqual(len(reports), 1)
            content = reports[0].read_text()
            self.assertNotIn('DO_NOT_EXPORT', content)
            self.assertIn('synthetic_failure', content)
