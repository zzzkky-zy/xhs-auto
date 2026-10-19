import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
import cloud_browser as cloud
import cloud_mcp


class CloudBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name)
        self.config = self.base / 'cloud.json'
        self.data = {'schema_version': 1, 'browser_mode': 'cloud_host', 'account_id': 'test', 'state_dir': 'state', 'allow_write': False, 'allow_reconcile': False}
        self.config.write_text(json.dumps(self.data)); self.bridge = cloud.CloudBrowser(self.config)

    def tearDown(self):
        self.bridge.close(); self.tmp.cleanup()

    def observation(self, status='observed'):
        return dict(status=status, final_url='https://www.xiaohongshu.com/explore', title='Public page', visible_text='Visible page text', evidence_ref='fixture:read-1')

    def test_request_pending_not_execution_or_authentication(self):
        request = self.bridge.prepare('list_feeds', {})
        self.assertEqual(self.bridge.get(request['request_id'])['status'], 'pending_host_action')
        self.assertFalse(self.bridge.status()['autonomous_browser'])
        self.assertEqual(self.bridge.status()['authenticated'], 'not_checked')
        self.assertEqual(os.stat(self.bridge.path).st_mode & 0o777, 0o600)

    def test_search_encodes_keyword_and_preserves_destination(self):
        result = self.bridge.prepare('search_feeds', {'keyword': '猫 & 狗/测试'})
        self.assertIn('%26', result['url']); self.assertNotIn(' ', result['url'])
        obs = self.observation(); obs['final_url'] = result['url']
        self.assertEqual(self.bridge.record(result['request_id'], obs)['status'], 'host_reported')

    def test_public_id_routes(self):
        ident = 'a' * 24
        self.assertTrue(cloud.navigation('get_feed_detail', {'feed_id': ident}).endswith('/explore/' + ident))
        self.assertTrue(cloud.navigation('user_profile', {'user_id': ident}).endswith('/user/profile/' + ident))

    def test_writes_auth_and_arbitrary_code_are_not_capabilities(self):
        for tool in ('publish_content', 'login_with_sms', 'reply_direct_message', 'evaluate', 'goto', 'delete_cookies'):
            with self.assertRaises(ValueError): self.bridge.prepare(tool, {})
        for args in ({'url': 'https://evil.invalid'}, {'keyword': 'ok', 'script': 'evil'}, {'keyword': ''}, {'keyword': 'a' * 201}):
            with self.assertRaises(ValueError): self.bridge.prepare('search_feeds', args)
        with self.assertRaises(ValueError): self.bridge.prepare('get_feed_detail', {'feed_id': '../bad'})

    def test_blocked_login_does_not_mark_authenticated(self):
        req = self.bridge.prepare('list_feeds', {})
        value = self.bridge.record(req['request_id'], self.observation('login_required'))
        self.assertEqual(value['observation']['status'], 'login_required')
        self.assertEqual(value['observation']['provenance'], 'host_reported_unverified')
        self.assertEqual(self.bridge.status()['authenticated'], 'not_checked')

    def test_idempotent_observation_and_conflicting_replay(self):
        req = self.bridge.prepare('list_feeds', {}); obs = self.observation()
        first = self.bridge.record(req['request_id'], obs)
        self.assertEqual(first, self.bridge.record(req['request_id'], obs))
        with self.assertRaises(ValueError): self.bridge.record(req['request_id'], self.observation('captcha'))

    def test_pending_survives_process_exit(self):
        req = self.bridge.prepare('list_feeds', {})
        result = subprocess.run([sys.executable, str(ROOT / 'runtime/cloud_browser.py'), '--config', str(self.config), 'get', req['request_id']], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['status'], 'pending_host_action')

    def test_unknown_account_and_request_are_rejected(self):
        with self.assertRaises(ValueError): self.bridge.record('f' * 32, self.observation())
        req = self.bridge.prepare('list_feeds', {})
        self.data['account_id'] = 'other'; self.config.write_text(json.dumps(self.data))
        other = cloud.CloudBrowser(self.config)
        try:
            with self.assertRaises(ValueError): other.get(req['request_id'])
        finally: other.close()

    def test_observation_bounds_credentials_and_destinations(self):
        req = self.bridge.prepare('list_feeds', {})
        for key, value in [('visible_text', 'a'*32001), ('visible_text', 'web_session=secret'), ('final_url', 'https://evil.invalid/explore'), ('final_url', 'https://www.xiaohongshu.com.evil.invalid/explore'), ('final_url', 'https://name:secret@www.xiaohongshu.com/explore'), ('final_url', 'https://www.xiaohongshu.com/explore?access_token=secret'), ('final_url', 'https://www.xiaohongshu.com/wrong'), ('status', 'published'), ('evidence_ref', '')]:
            obs = self.observation(); obs[key] = value
            with self.assertRaises(ValueError, msg=str((key, value[:100]))): self.bridge.record(req['request_id'], obs)
        obs = self.observation(); obs['cookies'] = 'not allowed'
        with self.assertRaises(ValueError): self.bridge.record(req['request_id'], obs)

    def test_unavailable_without_page_has_no_fabricated_url(self):
        req = self.bridge.prepare('list_feeds', {})
        obs = self.observation('unavailable'); obs['final_url'] = ''
        self.assertEqual(self.bridge.record(req['request_id'], obs)['observation']['final_url'], '')

    def test_url_fragments_are_not_recorded(self):
        req = self.bridge.prepare('list_feeds', {})
        obs = self.observation(); obs['final_url'] += '#access_token=secret'
        with self.assertRaises(ValueError): self.bridge.record(req['request_id'], obs)

    def test_mode_and_write_switches_fail_closed(self):
        for key, value in [('browser_mode','local_cdp'), ('allow_write',True), ('allow_reconcile',True)]:
            bad = dict(self.data); bad[key] = value; self.config.write_text(json.dumps(bad))
            with self.assertRaises(ValueError): cloud.CloudBrowser(self.config)

    def test_no_browser_network_or_subprocess_in_adapter(self):
        with mock.patch('subprocess.Popen', side_effect=AssertionError('no subprocess')), mock.patch('socket.socket', side_effect=AssertionError('no networking')):
            req = self.bridge.prepare('list_feeds', {})
            self.bridge.record(req['request_id'], self.observation())

    def test_legacy_entry_rejects_cloud_before_network_or_state(self):
        from xhs_ops import Config
        with self.assertRaisesRegex(ValueError, "ERR_BROWSER_MODE"):
            Config(self.config)

    def test_stdio_entrypoint_routes_without_go_or_backend(self):
        requests = [dict(jsonrpc='2.0', id=1, method='initialize', params={'protocolVersion':'2025-11-25'}), dict(jsonrpc='2.0', method='notifications/initialized'), dict(jsonrpc='2.0', id=2, method='tools/list'), dict(jsonrpc='2.0',id=3,method='tools/call',params={'name':'cloud_browser_status','arguments':{}}), dict(jsonrpc='2.0',id=4,method='tools/call',params={'name':'publish_content','arguments':{}})]
        run = subprocess.run([sys.executable, str(ROOT/'runtime/codex_mcp.py'), '--config', str(self.config)], input=''.join(json.dumps(r)+'\n' for r in requests), capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode,0,run.stderr)
        rows = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual(len(rows),4); self.assertEqual(len(rows[1]['result']['tools']),4)
        self.assertFalse(json.loads(rows[2]['result']['content'][0]['text'])['autonomous_browser'])
        self.assertTrue(rows[3]['result']['isError'])

    def test_stdio_invalid_and_oversized_requests(self):
        incoming = io.StringIO('not json\n'+('x'*(cloud.MAX_INPUT+1))+'\n'+json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/list'})+'\n')
        output = io.StringIO(); cloud_mcp.serve(self.config,incoming,output)
        rows=[json.loads(x) for x in output.getvalue().splitlines()]
        self.assertEqual(len(rows),3); self.assertTrue(all('error' in x for x in rows))

    def test_existing_public_directory_and_ledger_rejected(self):
        os.chmod(self.bridge.path, 0o644)
        with self.assertRaisesRegex(ValueError, 'private'): cloud.CloudBrowser(self.config)
        os.chmod(self.bridge.path, 0o600); os.chmod(self.bridge.path.parent, 0o755)
        with self.assertRaisesRegex(ValueError, 'private'): cloud.CloudBrowser(self.config)
        os.chmod(self.bridge.path.parent, 0o700)

    def test_record_admission_budget_preserves_pending(self):
        req = self.bridge.prepare('list_feeds', {})
        with mock.patch.object(Path, 'stat', return_value=mock.Mock(st_size=32*1024*1024)):
            with self.assertRaisesRegex(ValueError, 'ERR_QUOTA'):
                self.bridge.record(req['request_id'], self.observation())
        self.assertEqual(self.bridge.get(req['request_id'])['status'], 'pending_host_action')

    def test_authentication_markers_in_metadata_rejected(self):
        req = self.bridge.prepare('list_feeds', {})
        for key in ('title', 'evidence_ref'):
            obs = self.observation(); obs[key] = 'web_session=secret'
            with self.assertRaises(ValueError): self.bridge.record(req['request_id'], obs)

    def test_deep_json_and_bad_ids_do_not_crash_protocol(self):
        raw = '['*20000+'0'+']'*20000+'\n'
        raw += ''.join(json.dumps({'jsonrpc':'2.0','id':value,'method':'ping'})+'\n' for value in ({}, True, None))
        raw += json.dumps({'jsonrpc':'bad','id':{},'method':'ping'})+'\n'
        raw += json.dumps({'jsonrpc':'2.0','id':7,'method':'ping'})+'\n'
        output=io.StringIO(); cloud_mcp.serve(self.config,io.StringIO(raw),output)
        rows=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(rows[0]['error']['code'],-32700)
        self.assertTrue(all(row['id'] is None and row['error']['code']==-32600 for row in rows[1:5]))
        self.assertEqual(rows[-1]['result'],{})

    def test_stdio_round_trip_pending_then_report(self):
        req=self.bridge.prepare('list_feeds',{})
        incoming=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'record_browser_observation','arguments':{'request_id':req['request_id'],'observation':self.observation()}}}]
        out=io.StringIO();cloud_mcp.serve(self.config,io.StringIO(''.join(json.dumps(r)+'\n' for r in incoming)),out)
        rows=[json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(json.loads(rows[1]['result']['content'][0]['text'])['status'],'host_reported')


if __name__ == '__main__': unittest.main()
