"""Local projection repair and owned-resource diagnostics; no browser actions."""
import json


def repair_projection(batch, run_id):
    """Repair only an existing batch mirror, even while execution is paused.

    This deliberately does not create/open stores, authorize content, alter the
    action ledger, reconcile outcomes, or renew any permission. Cross-store
    observations can become stale again; they are not an atomic global snapshot.
    """
    if batch is None or batch.action_ledger is None:
        raise ValueError('ERR_BATCH_NOT_OPEN: existing batch and action ledger required')
    batch._guard()
    batch.action_ledger._check_storage()
    changed = []
    with batch.continuous.lock():
        batch.continuous.run(run_id)
        with batch.store.transaction():
            rows = batch.db.execute('SELECT id,status,result FROM tasks WHERE run_id=? ORDER BY id', (run_id,)).fetchall()
            # Validate every binding and authoritative outcome before any update.
            values = [batch._linked_action(run_id, row['id']) for row in rows]
            for value in values:
                batch._project_host_status(value)
            for row, value in zip(rows, values):
                mirror = batch.db.execute('SELECT host_status FROM cloud_batch_bindings WHERE operation_id=?', (row['id'],)).fetchone()
                expected = batch._project_host_status(value)
                # Compare decoded receipts, not irrelevant JSON whitespace.
                receipt = json.loads(row['result']) if row['result'] is not None else None
                if row['status'] != expected or mirror['host_status'] != value['status'] or receipt != value.get('result'):
                    batch._sync_action_projection(value)
                    changed.append(row['id'])
    return dict(run_id=run_id, repaired_operation_ids=changed, repaired_count=len(changed),
                state_changed=bool(changed), action_ledger_changed=False,
                cross_store_atomic_snapshot=False, browser_action_performed=False,
                grants_execution_authority=False, unknown_outcomes_preserved=True)


def owned_resource_pressure(actions, max_owned_open_pages=None, browser_context=None):
    """Conservative registry-only advisory; never proves the live tab count.

    A cap must be explicitly configured by the host. None imposes no limit.
    A missing registry is unknown, not zero. Closed means actual post-close
    inventory was recorded by the owned-page protocol, not just a close reply.
    """
    if max_owned_open_pages is not None and (type(max_owned_open_pages) is not int or max_owned_open_pages < 1):
        raise ValueError('ERR_RESOURCE_CAP: explicit positive integer required')
    if browser_context is not None and (not isinstance(browser_context, str) or not browser_context.strip()):
        raise ValueError('ERR_RESOURCE_SCOPE: nonempty browser context required')
    actions._check_storage()
    expected = actions._config().get('expected_user_id')
    if not isinstance(expected, str) or not expected.strip():
        raise ValueError('ERR_RESOURCE_SCOPE: exact expected account required')
    result = dict(expected_account=expected, browser_context=browser_context,
                  max_owned_open_pages=max_owned_open_pages, registry_status='no_registry',
                  retained_count=None, closed_observed_count=None, admit_new_owned_page=None,
                  registry_only=True, needs_inventory_baseline=True, live_inventory_verified=False, browser_action_performed=False,
                  grants_execution_authority=False, changes_read_search_budget=False,
                  automatic_cleanup=False)
    exists = actions.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='owned_pages'").fetchone()
    if not exists:
        return result
    retained = closed = 0
    for row in actions.db.execute('SELECT resource_id,data FROM owned_pages WHERE expected_account=?', (expected,)):
        record = json.loads(row['data'])
        if record.get('expected_account') != expected or record.get('resource_id') != row['resource_id']:
            raise ValueError('ERR_RESOURCE_SCOPE: corrupt owned-page binding')
        page = record.get('page')
        if not isinstance(page, dict) or not isinstance(page.get('browser_context'), str) or not page['browser_context']:
            raise ValueError('ERR_RESOURCE_SCOPE: missing native context')
        if browser_context is not None and page['browser_context'] != browser_context:
            continue
        # Only verified post-close absence removes an owned registry resource.
        inventory = record.get('inventory')
        absent = (record.get('state') == 'closed_observed' and record.get('close_consumed') is True
                  and isinstance(inventory, dict) and inventory.get('complete') is True
                  and inventory.get('call_ended') is True and inventory.get('account_id') == expected
                  and inventory.get('browser_session') == page.get('browser_session')
                  and isinstance(inventory.get('pages'), list)
                  and all(isinstance(p, dict) and p.get('tab_id') != page.get('tab_id') for p in inventory['pages']))
        if absent:
            closed += 1
        else:
            retained += 1
    result.update(registry_status='available', needs_inventory_baseline=False, retained_count=retained, closed_observed_count=closed,
                  admit_new_owned_page=None if max_owned_open_pages is None else retained < max_owned_open_pages)
    return result
