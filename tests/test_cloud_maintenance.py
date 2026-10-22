"""Offline local maintenance tests, no real account or page creation."""
import json
import unittest
from unittest import mock
import test_cloud_batch
import test_cloud_owned_pages
from cloud_maintenance import repair_projection, owned_resource_pressure


class ProjectionRepairTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_cloud_batch.CloudBatchTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.f = self.fixture
    def test_paused_repair_is_idempotent_and_never_changes_ledger(self):
        f=self.f; run,items=f.reviewed(); action=f.prepare_action(run,items[1])
        f.report_success(action,outcome='not_applied')
        f.actions.set_execution_pause(True,'fixture user','fixture pause')
        before=f.actions.db.total_changes
        f.batch.db.execute("UPDATE tasks SET status='submitted_unknown',result=NULL WHERE id=?",(action['operation_id'],))
        first=repair_projection(f.batch,run['id'])
        self.assertEqual(first['repaired_count'],1)
        self.assertEqual(repair_projection(f.batch,run['id'])['repaired_count'],0)
        self.assertEqual(f.actions.db.total_changes,before)
        self.assertTrue(f.actions.execution_pause_status()['paused'])
    def test_unknown_remains_unknown(self):
        f=self.f; run,items=f.reviewed(); action=f.prepare_action(run,items[1])
        f.report_success(action,outcome='submitted_unknown')
        f.batch.db.execute("UPDATE tasks SET status='failed' WHERE id=?",(action['operation_id'],))
        repair_projection(f.batch,run['id'])
        self.assertEqual(f.actions.get(action['operation_id'])['status'],'submitted_unknown')
        self.assertEqual(f.batch.db.execute('SELECT status FROM tasks').fetchone()[0],'submitted_unknown')
    def test_invalid_binding_rolls_back_all_mirrors(self):
        f=self.f; run,items=f.reviewed(); a=f.prepare_action(run,items[0]); b=f.prepare_action(run,items[1])
        f.batch.db.execute("UPDATE tasks SET status='failed'")
        f.batch.db.execute("DELETE FROM cloud_batch_bindings WHERE operation_id=?",(b['operation_id'],))
        with self.assertRaises(ValueError):repair_projection(f.batch,run['id'])
        self.assertEqual({r[0] for r in f.batch.db.execute('SELECT status FROM tasks')},{'failed'})
    def test_failed_update_rolls_back(self):
        f=self.f; run,items=f.reviewed(); f.prepare_action(run,items[0]); f.prepare_action(run,items[1])
        f.batch.db.execute("UPDATE tasks SET status='failed'")
        original=f.batch._sync_action_projection; count=[0]
        def fail(value):
            original(value); count[0]+=1
            if count[0]==2:raise RuntimeError('fixture interrupted')
        with mock.patch.object(f.batch,'_sync_action_projection',side_effect=fail):
            with self.assertRaises(RuntimeError):repair_projection(f.batch,run['id'])
        self.assertEqual({r[0] for r in f.batch.db.execute('SELECT status FROM tasks')},{'failed'})
    def test_closed_run_maintenance_does_not_reopen(self):
        f=self.f; run,items=f.reviewed(); f.prepare_action(run,items[1])
        f.batch.db.execute("UPDATE ops_runs SET state='completed' WHERE id=?",(run['id'],))
        f.batch.db.execute("UPDATE tasks SET status='failed'")
        self.assertEqual(repair_projection(f.batch,run['id'])['repaired_count'],1)
        self.assertEqual(f.batch.continuous.run(run['id'])['state'],'completed')
    def test_account_change_rejected(self):
        f=self.f; run,items=f.reviewed(); f.prepare_action(run,items[1])
        changed=json.loads(f.path.read_text()); changed['expected_user_id']='other'
        f.path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError,'ACCOUNT_CHANGED'):repair_projection(f.batch,run['id'])
    def test_missing_store_not_created(self):
        with self.assertRaisesRegex(ValueError,'NOT_OPEN'):repair_projection(None,'missing')


class ResourcePressureTests(unittest.TestCase):
    def setUp(self):
        self.f=test_cloud_owned_pages.OwnedPageTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)
    def test_missing_registry_not_zero_and_no_schema_write(self):
        before=self.f.actions.db.total_changes
        result=owned_resource_pressure(self.f.actions,1)
        self.assertEqual(result['registry_status'],'no_registry'); self.assertIsNone(result['retained_count'])
        self.assertIsNone(result['admit_new_owned_page']); self.assertEqual(before,self.f.actions.db.total_changes)
    def test_explicit_cap_only_and_context_scope(self):
        self.f.register()
        self.assertIsNone(owned_resource_pressure(self.f.actions)['admit_new_owned_page'])
        self.assertFalse(owned_resource_pressure(self.f.actions,1)['admit_new_owned_page'])
        self.assertTrue(owned_resource_pressure(self.f.actions,2)['admit_new_owned_page'])
        self.assertEqual(owned_resource_pressure(self.f.actions,1,'other')['retained_count'],0)
    def test_unknown_close_still_counts_until_inventory(self):
        f=self.f; f.ready(); f.close()
        f.pages.record_result('op','page','unknown',f.obs())
        self.assertEqual(owned_resource_pressure(f.actions,1)['retained_count'],1)
        f.pages.observe_inventory('op','page',f.inventory())
        self.assertEqual(owned_resource_pressure(f.actions,1)['retained_count'],0)
    def test_corrupt_closed_label_alone_cannot_release_capacity(self):
        f=self.f; saved=f.register(); saved['state']='closed_observed'
        f.actions.db.execute('UPDATE owned_pages SET data=?',(json.dumps(saved),))
        self.assertEqual(owned_resource_pressure(f.actions,1)['retained_count'],1)
    def test_invalid_caps(self):
        for cap in (True,0,-1,1.5,'2'):
            with self.assertRaises(ValueError):owned_resource_pressure(self.f.actions,cap)
    def test_other_account_excluded(self):
        f=self.f; f.register()
        f.actions.db.execute("UPDATE owned_pages SET expected_account='other'")
        self.assertEqual(owned_resource_pressure(f.actions,1)['retained_count'],0)

if __name__=='__main__':unittest.main()
