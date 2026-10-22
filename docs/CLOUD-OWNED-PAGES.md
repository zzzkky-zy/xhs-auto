# Owned-page lifecycle and one-use cleanup

This is an offline-tested host contract, not a browser controller or proof of
live cleanup. Continue allocating a fresh owned page for every logical action.
Never infer ownership from a URL, adopt a user's tab, or close an unknown scene.
No cleanup resumes operations, resolves an incident, or changes action receipts.

## Integration

`CloudOwnedPages(actions)` reuses the existing CloudActions connection and
`BEGIN IMMEDIATE` transaction. Its constructor, `get` and `candidate` do not
create tables, append events or migrate storage. A host must also avoid constructing
an initializing CloudActions instance merely to service a cold read-only request;
report unavailable existing state rather than claim a read-only cold startup.
Registration creates the table during its write transaction. Records are keyed by
exact expected account and resource ID, not the local account alias. A duplicate
page slot cannot be registered through another actor while its old owner remains
unresolved. Distinct state directories are not a shared coordination domain.

Methods (all arguments after `actions` are named JSON inputs):

- `register(operation_id, resource_id, observation)`
- `get(operation_id, resource_id)`
- `candidate(operation_id, resource_id, observation)`
- `request_close(operation_id, resource_id, observation, capability)`
- `record_result(operation_id, resource_id, outcome, observation)`
- `observe_inventory(operation_id, resource_id, observation)`

## Real ownership evidence

A `page` has exactly `browser_session`, `browser_context`, `tab_id`,
`page_instance` (nonempty strings). The context must equal the action's actual
claim context, and the action must already have an attempt. Register during
`dispatching`, from the actual new-page creation result, before completing the
action. There is no historical adoption by matching a URL.

The instance may identify a native stable page handle/object with its session
binding, or the documented session-and-tab identity where official tool docs
explicitly guarantee that IDs cannot be reused in that session. An opaque host
reference to that handle is acceptable when the host can revalidate the same
handle. A random UUID, locally incremented generation, guessed birth timestamp,
or URL is **not** externally checkable instance evidence. If the available tool
only supplies reusable numeric IDs and no verifiable instance guarantee, retain
the page and report the missing capability; do not fabricate an instance to
obtain permission. Inspection-only manual candidate lists may describe such
pages as unverified; they are not registered ownership or eligible close targets.

Creation observation includes `observed_at`, `evidence_ref`, `account_id`, `page`,
`created_by_host: true`, `instance_verifiable: true`, `creation_evidence_ref` and
`instance_documentation_ref`. Evidence timestamps must be no more than 120 seconds
old (or the shorter configured identity maximum age), never in the future.

## Close preflight

Candidate and close observations include `observed_at`, `evidence_ref`,
`account_id`, exact `page`, `inventory_evidence_ref`, `instance_evidence_ref`;
all of `call_ended`, `steps_resolved`, `editor_empty`, `owned_by_host`,
`instance_verified`, `tab_present` must be true, and `challenge_active`,
`rate_limited`, `permission_denied` must be explicitly false. Missing is unknown.
The host checks the real pacing ledger and outstanding calls before attesting
`steps_resolved`; this module does not inspect a separate pacing database.

The authoritative action must have a successful result, or a `not_applied`
result with `host_no_submit`, `confirmed_not_applied`, or `platform_rejection`
evidence. A cancelled action is eligible only with that same preserved definitive
not-applied result; cancellation alone is not proof. Every path requires an empty
editor, including success. Unknown and nonterminal actions are refused. An
unverified incident with challenge/rate/permission latches or active restriction
observations prevents account cleanup. Incidents on this action/context/page also
prevent cleanup. An unrelated incident does not prevent cleanup only when its
original action is explicitly read-only, its distinct context is known, its call
has ended, and all three restriction flags are explicitly false. Missing proof
of independence remains blocked. The host separately checks actual account-level
pacing blocks from the existing pacing store; this module does not clear or
ignore those blocks.

`capability` is exactly `method: "close_owned_tab"`, `supported: true`, and
`documentation_ref`. It describes an actually supported official tool method,
not a claim this Python module supplies one. The host must revalidate tool support,
actual permission, page identity, pause state, and absence of competing calls
immediately before the one browser call. A permit cannot atomically control an
external browser or retract a call already in progress.

The close request checks persistent pause directly for the exact expected account,
even when the associated action was a read. Pause allows local evidence recording
and inspection, not new close permits. A candidate is not execution permission.

## Outcomes and uncertainty

Only the first successful request returns `dispatch_once: true`. It persists
`close_consumed` before returning. Reads, retries, new resource IDs, restarts and
another actor cannot renew that instruction. Execute exactly one supported close,
not an extra close if a dialog or uncertain response appears.

`record_result` accepts `completed`, `not_applied`, or `unknown`, with a fresh
observation containing `account_id`, exact `page`, `observed_at`, `evidence_ref`.
All outcomes retain consumption. A completed tool reply is not yet confirmed
absence. The result is immutable; contradictory later observations belong in
inventory evidence, not a rewritten result.

`observe_inventory` accepts fresh `observed_at`, `evidence_ref`, `account_id`,
`browser_session`, `complete: true`, `call_ended: true`, and `pages`, the full
same-session inventory of exact page objects. It must follow the close request.
Missing the exact tab ID yields `closed_observed`; any reuse of the same tab ID
remains `retained_after_close`, never an excuse to close its replacement. Lost
close responses can be resolved by this inventory without another close. A
retained page has no automatic retry even after a known not-applied close.

No inventory observation clears recovery restrictions or proves a browser restart,
account login, publication, comment success, or permission to retry a submission.

## Native recovery boundary

Use the existing recovery contract, not cleanup, for unavailable controls or a
challenge. A supported interpreter reset is not a browser restart. Never kill
browser processes, clear cookies, switch sessions to evade a challenge, or close
uncertain editors to reduce memory. A supported native task-manager observation
may provide diagnostic measurements only; it does not establish OOM causation or
authorize terminating a process. No native task-manager action is implemented here.

Offline tests cover loss of responses, restart, separate actors, account/context
mismatches, stale evidence, ID reuse, user pages, pause, cancellation, unknown
outcomes, recovery incidents and post-close inventory. They do not measure live
memory reduction or establish that a real page was closed.
