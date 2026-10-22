# Local maintenance and resource-pressure diagnostics

These helpers perform no browser action, account submission, new authorization,
unknown-outcome reconciliation, scheduling or automatic cleanup.

## Repair a stale batch mirror

`cloud_maintenance.repair_projection(batch, run_id)` requires already-open stores.
It validates exact existing action/run/account bindings and authoritative outcome
reports for **all** tasks before updating the mirror. A single batch transaction
updates only task status/result and linked host-status fields. A failure rolls
back every mirror update. Missing stores and bindings are errors, never reasons
to initialize an empty ledger. The action ledger, receipts, calendar, execution
pause and authorizations are unchanged. A confirmed not-applied receipt projects
to failed; an unknown receipt remains unknown. A paused or completed run can be
maintained without reopening its execution or re-reviewing content.

The source ledger and batch mirror are separate databases. This is not an atomic
cross-store snapshot: concurrent authoritative changes may cause new lag after
repair. The response says so; use the existing projection-status diagnostic to
inspect the latest evidence rather than claiming platform success.

## Inspect owned-resource pressure

`owned_resource_pressure(actions, max_owned_open_pages=None, browser_context=None)`
is a read-only registry diagnostic across local aliases of the exact configured
platform account, optionally narrowed to a native browser context. Only an
explicit positive integer cap is considered; no default cap or search/read
budget is introduced. All resources without recorded post-close absence still
count, including unknown closes, incomplete close replies and retained evidence.
Nothing is closed and no unknown action is cleared.

`admit_new_owned_page` is tri-state: false means the registered retained count
already meets the explicit cap; true means it is below that cap; null means no
cap or insufficient registry information. **True is not creation permission or
proof of complete live inventory.** The host must independently verify actual
session inventory, identity, permission, pause and supported creation capability.
It must only apply a cap to explicit new-page creation, never existing-page reads,
evidence collection or result recording.

A missing registry returns `no_registry`, null counts and
`needs_inventory_baseline=true`, not zero pages. There is no baseline-initializing
API in this helper. The host must obtain real complete inventory before deciding
about a capped first new page; do not invent an empty registry as proof. Without
a cap the existing host workflow is unchanged. With no reliable new-page hook,
this remains diagnostic guidance, not an automatic full-browser resource limit.
Registry presence likewise does not prove all live pages were registered.

## Offline acceptance

`test_cloud_maintenance` checks paused idempotent repair, authoritative ledger
preservation, unknown retention, missing binding, rollback after partial update,
missing store refusal, absent registry without schema writes, optional caps,
context/account scopes, unknown close occupancy, post-close inventory and malformed
caps. These fixtures are not real browser or account tests.
