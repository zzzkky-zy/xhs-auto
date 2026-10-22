# Cloud host actions: v3 operational guide

This local candidate extends the v1 read-only adapter. `CLOUD-HOST-BROWSER.md`
describes that historical four-tool read surface; this document describes the
v3 action lifecycle. It is **assistant-mediated**, not an autonomous browser
backend. Python stores plans and evidence. The assistant separately calls the
currently supported official browser tools. There is no browser RPC endpoint,
CDP fallback, background daemon, or claim that native plugin registration worked.

## Surfaces and compatibility

Current source exposes 57 MCP tools: 30 original names, 23 lifecycle/batch helpers,
and four legacy inspection/read names. Legacy `prepare_browser_read` and
`record_browser_observation` are disabled; use the unified action lifecycle. The authoritative inventory is `tools/list` from
`runtime/codex_mcp.py` and `cloud_capabilities` from the host entry point.
Original names preserve argument schemas where applicable, **not original API
results**. A pending plan is not website execution. UI observations do not promise
hidden-state API fields, exhaustive pagination, or full upstream parity.

One-shot CLI (generic paths; choose an actual private configuration):

```sh
python3 runtime/cloud_host.py --config /private/xhs.cloud.json cloud_capabilities --arguments '{}'
python3 runtime/cloud_host.py --config /private/xhs.cloud.json get_runtime_status --arguments '{}'
python3 runtime/cloud_host.py --config /private/xhs.cloud.json list_cloud_actions --arguments '{"due_only":true,"limit":20}'
```

The CLI positional name is exactly the MCP tool name. `--arguments` is a JSON
object; when omitted, bounded JSON is read from stdin. Never put credentials,
OTP, cookies, browser storage, or access tokens into either form. A compatible
MCP host can use `python3 runtime/codex_mcp.py --config /private/xhs.cloud.json`;
installation alone does not prove the current agent has registered that server.
MCP calls have the standard form:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_cloud_action","arguments":{"operation_id":"actual-returned-operation-id"}}}
```

Initialize the MCP session before calls. Do not send MCP messages to an imagined
cloud browser endpoint.

## Trust and permission boundary

`ready` means a bound host attestation was recorded. It does not grant permission.
The adapter cannot authenticate chat authors or verify that an evidence reference
is genuine. The host must independently inspect the actual user instruction,
current policy, account, destination, exact content, and permitted action before
any side effect. `user_source`, `approval_ref`, attestation JSON, and configuration
flags are inert records, never authorization credentials. Do not invent a quote,
reference, receipt, account identity, or consent because a gate needs a value.

Keep `allow_write: false` until the intended scope is explicitly approved.
`allow_reconcile` is not permission to send anything. Evidence-only reconciliation
can be recorded while writes are disabled. These local gates do not replace host
permission checks. The default command host has no independent evidence verifier.

Draft-only tests are external changes too: obtaining specific permission for the
exact account, media, text, and upload/fill/save target is required. Approval to
prepare a draft does not authorize publication. Publishing, comments, direct
messages, likes, and favorites each need the actual target and appropriate user
scope. Opening notifications may mark them read and is treated as an account
state change. Do not use a publish-shaped plan to claim that draft save and
publication are equivalent. Any narrower draft-only test must retain that exact
scope in the host's real authorization and evidence.

## Exact lifecycle

The helper schemas below reject unknown top-level keys. Nested evidence objects
require the documented fields; read result reports additionally allow `read_at`
and `cached: false` for the actual new dispatch. Replace all example placeholders
with actual returned values or observed evidence; they are not executable consent.

1. Inspect the current official browser documentation, discover browser/tab IDs,
   and inspect only what can safely be inspected before a side effect. Do not
   navigate to a mark-read surface before entering the dispatch boundary.
2. Prepare the exact action:

```json
{"operation":"search_feeds","payload":{"keyword":"家居设计"},"user_source":"actual-user-message-reference","operation_id":"stable-operation-id","due_at":"2027-01-15T09:00:00+08:00","timezone":"Asia/Shanghai"}
```

Call `prepare_cloud_action`. Required: `operation`, `payload`, `user_source`.
Optional: `operation_id`, `due_at`, `timezone`, `media_manifest`, `retry_of`. Content writes
require a valid reviewed `run_id` inside the payload. The host derives
account and target from config and the validated original operation. `due_at`
requires an explicit IANA timezone. Reuse the same ID only for the identical
binding; changing content, destination, schedule, or assets requires a new action.
Save the returned `binding`, `binding_digest`, and ID. A repeated preparation with
identical binding is idempotent; a different binding conflicts.

3. Call `request_cloud_authorization` with `{"operation_id":"..."}`. Reads become
   ready; mutations await authorization. For a mutation, after independently
   checking actual user permission, call `record_cloud_authorization` with:

```json
{"operation_id":"...","attestation":{"operation_id":"...","binding_digest":"...","expected_account":"...","user_source":"...","user_quote":"Exact relevant user instruction","confirmed_at":"actual-offset-ISO-time","expires_at":"actual-offset-ISO-time","evidence_ref":"actual-owner-message-reference"}}
```

The first four binding values must match the saved action. Dates must be real,
current and bounded; do not manufacture a later expiry to expand permission.

4. For identity-required actions, visibly inspect the current account and call
   `record_cloud_identity`:

```json
{"operation_id":"...","observation":{"status":"observed","account_id":"actual-visible-account-id","observed_at":"actual-offset-ISO-time","evidence_ref":"actual-browser-observation-reference","browser_context":"actual-browser-and-tab-context"}}
```

Identity status is `observed`, `logged_out`, or `unavailable`. For the latter two,
account_id must be empty. Default identity freshness is 120 seconds. Identity must
match the expected account; creator identity must also be visibly checked for
publishing. Stored identity is host-reported evidence, not authenticated proof.

5. Call `claim_cloud_action` with `operation_id`, `worker_id`, optional
   `lease_seconds` (5–600; default 120), and `browser_context`. Save its returned
   `claim_token` privately and `attempt_id`. A claim reserves local work only.
   Due time, queue enablement, dependencies, account conflicts, authorization,
   identity, and media are checked. A duplicate wake must not replace this claim.
6. Immediately before the **first possible side effect**, call
   `begin_cloud_dispatch` with `operation_id` and `claim_token`. This commits
   `submitted_unknown` before browser input, upload, save, submit, or mark-read
   navigation. A failed/uncertain response is not permission to repeat the step.
   Recheck actual host permission and context. Only then execute the approved UI
   steps through official browser tools. Do not wait until the final Publish or
   Send button to mark dispatch; fills and uploads already change external state.
7. Inspect actual results, then call `record_cloud_result` with `operation_id`,
   `claim_token`, and this exact report shape:

```json
{"operation_id":"...","binding_digest":"...","attempt_id":"...","account_id":"...","browser_context":"...","status":"submitted_unknown","observed_at":"actual-offset-ISO-time","evidence_ref":"actual-browser-observation-reference","evidence_kind":"uncertain","details":"What was actually observed and what remains unknown."}
```

That object is the nested `report`, not the whole call. Supported status/evidence:

- `host_reported_success`: `visible_result` or `platform_receipt`
- `not_applied`: `platform_rejection` or `confirmed_not_applied`
- `submitted_unknown`: `uncertain`
- While still pre-dispatch (`dispatching`), only `not_applied` with
  `host_no_submit` is accepted.

`platform_receipt` requires a real observed receipt; the label does not verify it.
Never claim success merely because a click returned. Report evidence must bind to
this attempt/account/context and follow its dispatch time. Success remains
host-reported and `platform_success_independently_verified` remains false.

## Recovery and evidence

States are prepared → awaiting_authorization/ready → dispatching →
submitted_unknown → host_reported_success/not_applied, with pre-dispatch cancel.
`release_cloud_claim(operation_id, claim_token, reason)` is allowed only before
dispatch. `recover_cloud_leases({})` restores expired pre-dispatch claims; it never
replays submitted work. `cancel_cloud_action(operation_id, reason)` cancels only
unclaimed prepared/awaiting/ready work.

An interruption after dispatch may have changed the site. Keep unknown and inspect
read-only evidence before any retry. `reconcile_cloud_action(operation_id, report)`
uses the same report schema, without a claim token, and accepts only an observed
terminal outcome for an unknown action. It does not execute a browser action.
A replacement browser context needs fresh matching identity evidence. Missing
results do not prove nothing happened. No automatic replay or overwrite of a
conflicting terminal result is allowed. `cloud_action_events(operation_id, limit)`
reads append-only history; `get_cloud_action` returns current evidence.

The original `submit_task` now hashes local publication assets before saving an
action (remote URLs are rejected) and reports `auto_run: false`;
`control_task_queue` only gates local claims. `get_task_status` and
`get_operation_status` return actual cloud ledger state. `reconcile_task`,
`resolve_operation`, and `resume_task` do not turn free text into successful
receipts or silently restart unknown work; follow their bound-evidence next step.

## Media and reviewed batches

`media_manifest` contains exactly `{path, size, sha256}` per asset: normalized
absolute local path, actual byte size, lowercase SHA256. Every image/video payload
path must match the manifest. Linked, missing, changed, or unreviewed bytes are
rejected. Materialize remote media through an authorized supported route before
preparing the immutable action; do not substitute remote URLs at upload time.
The ledger limits each file to 2 GiB and total media to 4 GiB. The reviewed batch
pipeline has a narrower 64 MiB/file limit. These are intentional local limitations,
not parity with the platform's larger advertised upload limits.

`manage_operation_run` accepts `{operation, data}` and supports all 11 local
suboperations: configure, begin, update, schedule, review, status, maintain, cancel,
finish, accept, and repair. These remain local-only. Finish requires correlated
terminal action evidence and records `host_reported_complete`; outcome counts
distinguish success, not-applied, and cancelled. Acceptance records the real user
acceptance bound to that completion digest and never grants automatic authority.
Maintenance retains evidence and assets; cancellation is pre-dispatch local
cancellation, not cancellation of a platform post. Repair checks local hashes and
records host-reported tests, without enabling Go/CDP.

For validation-batch acceptance, `data` includes `run_id`,
`human_acceptance_ref`, and `host_acceptance` with exactly `run_id`, `account_id`,
`completion_digest`, `user_source`, `user_quote`, `accepted_at`, and `evidence_ref`.
Use actual dated acceptance after completion, never a generated placeholder.

After reviewing the
exact assets/content, `prepare_cloud_batch_payload(run_id, tool, payload,
approval_ref)` returns the sealed payload and manifest; it does not upload or
approve them. Pass those exact returned values to `prepare_cloud_action`, with the explicit `run_id` in the payload. Preparation reviews, seals, and
binds the action automatically; `bind_cloud_batch_action` can inspect/reaffirm
that same association. Binding preserves host provenance
and does not create a platform receipt or authorize dispatch.

A documented browser file chooser may support this sequence: start
`tab.playwright.waitForEvent('filechooser', {timeoutMs: 10000})`, click the observed
file input, await the chooser, inspect `chooser.isMultiple()`, then
`chooser.setFiles(['/absolute/path'])`. Confirm current tool documentation before
use. Do not invent `locator.setInputFiles` or use an undocumented upload API.
Discovery of this capability is not an upload test. Post-upload controls, requested
visibility/schedule/products/activity/templates and actual draft/publication
outcomes need separate UI verification. Stop if any requested setting cannot be
observed or fulfilled.

## Authentication and future scheduling

`get_login_qrcode` and `login_with_sms` return host-auth handoff guidance. Use the
supported authentication route; never insert credentials or OTP into this ledger.
`delete_cookies` is not applicable: the adapter does not own cloud browser cookies,
and deleting a local backup would not log out the browser.

No scheduler or standing communications authorization is created by this adapter.
A future approved schedule should wake an assistant, reopen durable state, claim
one due action, use official browser tools, record evidence, and exit. The
scheduler provides wake opportunities; the ledger controls due times and stable
operation IDs. Do not enqueue a new duplicate action on every wake. Overlap and
ambiguous submission must fail closed. Exactly-once website delivery cannot be
guaranteed across a browser interruption.

Before enabling recurring operations, establish the actual account, allowed
content and audience/recipients, allowed actions, whether unreviewed future sends
are approved, daily limits, IANA timezone, active hours/slots, and stopping/review
condition. Never infer the user's timezone from a workspace clock. A prepared
future `due_at` is not a running automation. Confirm that supported scheduler
creation is available and verify a harmless real scheduled read can access both
the durable state and official browser before claiming unattended readiness.
Current tool availability can vary; unsupported scheduling or missing browser
access must be reported rather than replaced with a private API or daemon.

## Safe acceptance scope

Local schema, lease, hashing, recovery and protocol tests do not prove live site
writes. Start with read-only observations. Any real upload/fill/save test requires
specific draft-only authorization; keep publishing, commenting, messaging, likes,
and favorites out of that test unless separately approved. Preserve writes-off
configuration until approval and test readiness are established. Label results as
local-contract-tested, live-read-observed, or live-action-observed as appropriate.

## Strict skill gates in v3

All four content writes require a current reviewed batch. Preparation, claim and
begin-dispatch recheck profile, active run, plan, stages, allowed tools, quota and
sealed bytes. A previous review is not a permanent bypass after edits. Legacy
read execution names are disabled so they cannot bypass the unified lifecycle.

`begin_cloud_dispatch` also reserves persistent account-wide pacing before any
actual browser action, including reads and identity inspections. An inspection
needed for later identity evidence must itself use a suitable read action first;
recording evidence does not perform another inspection. Deadlines survive process
restarts and new operation IDs: browser actions/read intervals 10–20 seconds,
120 reads/hour; search intervals 60–90 seconds, 20 searches/hour; write intervals
60–180 seconds and publication submission at least 60 seconds. Notifications
consume read quota and write pacing because opening them may mark them read.
A pacing denial returns a saved retry deadline, not permission to execute.
These safeguards honor the plugin's limits; they do not bypass website controls.

Comments require observed `target_author_id`, `thread_id` and `author_evidence`.
For main comments and paired replies, `target_author_id` is the original note
author used by the three-day author guard, not the recipient of a reply.
`thread_id` is the note `feed_id`. The reply recipient remains the immutable
payload `user_id`, and its exact target comment remains payload `comment_id`
and matching `author_evidence.target_comment_id`. Record the observed note
author and reply recipient separately in `author_evidence`.
The three-day author guard persists. A suitable reply can name the successful
main comment's actual action ID as `parent_operation_id`; distinct targeted
replies require their actual `author_evidence.target_comment_id`. A repeated
target or unknown sibling outcome is not a new permission to send. Strict reply
dispatch requires a concrete observed `comment_id`; the original user-ID-only
reply shape cannot identify a deduplicated comment target and is rejected.

Report observed login, captcha or platform restrictions with
`report_cloud_platform_block(scope, reason, evidence)`. Time alone never clears
them. `clear_cloud_platform_block` records fresh matching-account resolution
evidence; the host must actually observe recovery using permitted means.

The cloud-only `voice_mode` may use `mode: owner_instructions`,
`calibrated: false`, exact `persona_quote`/`persona_source` and exact
`no_review_quote`/`no_review_source` when the real owner explicitly provided
these instructions. Empty `voice_samples` remain empty. This mode does not invent
approved examples, waive content review or accept a first batch for the user.

For an explicitly approved fixed calendar, `manage_operation_run` schedule data
can supply `explicit_slots`: ordered, unique offset-bearing ISO timestamps.
Slots must meet the plugin's scheduling window and spacing, avoid reservations
from other runs, and cannot silently replace submitted slots. Repeating the same
slots is idempotent; replacing an unsubmitted reservation must be explicit.
The original local runtime and default randomized calendar remain unchanged.
A local reservation is not a platform scheduled-post receipt. Actual account
scheduling controls and limits must still be checked on the live platform.

Identity bootstrap uses `check_login_status`, whose claim does not require prior
identity. Complete that gated read, then reuse its exact fresh observation in
`record_cloud_identity` for a later identity-required action. Do not perform an
extra ungated browser inspection merely to populate that record. A bounded
`get_feed_detail` read can include its target's complete image carousel and
comments; separate search/detail/profile targets require separate actions.
Account serialization remains held throughout the bounded read operation.

Semantic content deduplication uses the expected platform account, content target,
content/settings and asset hashes independently of action IDs and provenance.
Pending, successful or unknown content cannot be resent with a fresh ID. Only a
confirmed not-applied or cancelled predecessor permits an explicit new `retry_of`
attempt, with a new bound host authorization. Its original receipt is preserved.
Not-applied batch reservations free replacement quota; successes and unknown
submissions remain counted. Reads and writes share account serialization. An
expired lease never proves a browser call ended: the host must establish that
before starting reconciliation reads, and unresolved mutations block writes.

For an explicitly bounded operating scope, private configuration may set
`allowed_write_operations` to the exact allowed original operation names.
An empty list disables all mutations; malformed lists fail closed. The list is
rechecked at claim and dispatch and does not replace review or host authorization.
Omission preserves existing local configuration compatibility.

### Bounded first-batch calendar exception

A private `cloud_schedule_overlap_exception` may contain `first_run_id`,
`user_source`, and `user_quote` only after the owner explicitly permits the
first batch to overlap later regular publication times. These are evidence
records, not external authorization. The ID must identify the original first
run. This cloud-only calendar supports same-time reservations across runs,
while preserving unique owned slots within each run and atomic migration.

With this exception configured, scheduling requires explicit slots: exactly ten
first-batch slots separated by 90 minutes, or exactly ten slots on one
Asia/Shanghai day at 09:00, 10:30, 12:00, 13:30, 15:00, 16:30, 18:00, 19:30,
21:00 and 22:30. Only first-versus-regular overlap is exempted. Two regular
batches still cannot collide; no duplicate-content permission or platform-limit
exemption is created. Ordinary run-only schedule inspection remains read-only.
Dates are chosen only when the actual content and platform settings are ready.

The cloud-only `content_requirements.exact_person_covers` requires a positive
bounded integer and the original minimum/cover-evidence fields. It checks the
complete planned post batch at review and authorization, rejecting too few or
too many person covers and incomplete/split cover groups. Comment-only reviews
remain possible and make no claim about post cover completion. Visual likeness
and real authorization still require actual host review of the supplied image.

## Single-browser-operation pacing (v3.3)

The previous logical-action gate did **not** prove that clicks, carousel moves
or image requests inside one host script were spaced. Historical runs must not
be described as step-paced. The new protocol is assistant-enforced: Python
cannot intercept official CUA calls or detect a host hiding multiple calls in a
script. One returned permit covers exactly one official browser operation, never
a loop, a compound click/navigation script or a batch of image downloads.

After the logical claim and begin-dispatch, call `reserve_cloud_browser_step`
with `operation_id`, `claim_token`, stable `step_id`, `kind`, and a bounded
`target` description grounded in the observed page. Kinds are `navigation`,
`search`, `click`, `scroll`, `media_request`, and `read_observation`. A denial
returns the persisted `retry_at`; wait without browser polling, then retry the
same step. A consumed permit is never reissued, even after restart or timeout.
Only when `permitted: true`, execute ONE corresponding official CUA operation.
Then call `record_cloud_browser_step` with the same binding, `outcome`
(`completed`, `not_applied`, or `unknown`) and actual `evidence`.

Step IDs are unique across the account, not just within an operation. Prefix
them with the operation ID (for example, `research-17:identity`) and retain the
same ID on wait retries. A binding-mismatch rejection issued before a permit
does not execute a browser call; choose a new account-unique ID for that new
step, without changing or replaying any previously permitted step.

An explicitly reported unknown mutation pauses subsequent mutations. Once its
browser calls have ended and all steps are resolved, independent read operations
may use the normal lifecycle without releasing or clearing the unknown action.
Preserve its page and receipt. After its lease expires, obtain further evidence
through a new read action; `reconcile_cloud_action` itself needs no claim token
and only records a supported terminal outcome. Absence from loaded comments,
unchanged counts, or a timeout cannot establish that a send was not applied.

For example, opening a filter menu, applying a filter, inspecting the resulting
DOM, changing an image carousel page, taking a screenshot and retrieving one
image are separate operations. A filter application that requests a new search
uses `search`; merely opening its menu uses `click`. Pure local JSON/file reads
do not consume browser permits. DOM and screenshot observations use the shared
internal-step gate conservatively, but do not debit the new-page read quota.
Media requests and carousel work within an existing detail also use only the
internal gap; they are not new-page reads.
The first matching actual network step uses the logical reservation's existing
quota credit exactly once. Later new requests debit the applicable quotas.

Report actual CAPTCHA/login/permission/300013 observations in optional
`platform_restriction: {scope, reason, evidence}` on the step result. This is
atomically saved as a durable block; result prose is not automatically classified
and screenshots are not analyzed by Python. The host must report the observed
restriction explicitly. The same structured field on `record_cloud_result`
provides a conservative fallback block. Do not invent retrospective blocks.

An in-flight step blocks all other browser steps. An ended but uncertain call
remains unknown indefinitely; elapsed time never makes it safe to repeat.
A recovery step must be `read_observation` and include `recovery_evidence` with
`expected_account`, `evidence_ref`, `user_source`, `call_ended: true`, and
`recovery_reason`. This records the host's actual permission and ended-call
checks, not credentials or proof; it cannot bypass a call still in flight.
Recovery never clears the original uncertainty automatically. Resolve its same
step ID with fresh matching-account observed evidence. Actual platform blocks
still require their explicit recovery evidence to clear.

With private `require_browser_steps: true`, a successful action receipt requires
completed step evidence and no unfinished steps. `get_cloud_browser_steps` reads
these receipts locally. This validates the ledger protocol, not whether a host
secretly executed extra CUA calls: actual host call logs remain the audit source.

An explicitly approved per-account override can set
`browser_step_interval_min_seconds` and `browser_step_interval_max_seconds`
(for example 1 and 5). Without these fields, the internal defaults remain 10
and 20 seconds. Internal click/scroll/media/read-observation steps use the
separate persisted internal deadline. New navigation/search operations retain
the original logical 10–20-second/read/search deadlines and quotas. Comments
retain their original write pacing. Every new random draw and next deadline is
saved in immutable events; a restart or configuration change does not redraw
an existing deadline. This makes future timing auditable, not retroactively
certified, and does not guarantee the platform will not require verification.

A terminal action that encountered a platform block may retain its original
claim binding for an explicitly permitted recovery observation only. This
requires an actual unresolved step or active block and cannot reopen execution
or repeat its publication/comment. Completing the observation does not clear
the block; record fresh actual resolution separately. Once resolved, the
recovery-only route closes. An in-flight call is never bypassed.

Passive `read_observation` under the same valid claim can inspect the current
DOM/screenshot to verify a submitted result or refresh an expired identity
observation. It retains account binding, serialization and step pacing. It does
not grant clicks, navigation, typing or another submission; those still recheck
fresh identity and current authorization. This avoids circular identity checks
without relabeling a possibly completed send as not applied.

### Explicit continuation past preserved unknown actions

Original RUNTIME permits separately authorized, different-target direct work
while preserving an old unknown. The cloud helper
`record_cloud_unknown_continuation` records that narrow host decision for one
new comment or reviewed image-publication action and one blocking action. It takes `operation_id`,
`blocked_operation_id`, and an `attestation` containing both IDs, `binding_digest`,
`blocked_binding_digest`, `expected_account`, `user_source`, `user_quote`,
`confirmed_at`, `expires_at`, and `evidence_ref`. Read the immutable digests from
`get_cloud_action`; references and quotes must describe actual user approval.
The assistant host, not these strings, is responsible for checking that approval.

Record the grant after preparing the new reviewed comment and before claiming
it. It supplements the ordinary bound authorization; it never replaces it.
For comment continuations, both actions must be comments/replies on different
feeds with different content. Image-publication continuations also support old
unknown comments or image publications; they reject reused publication copy or
media hashes. Video and other mutations are excluded.
Each grant bypasses only its named unresolved action, never another unresolved
write or a browser call still running. Normal review, current identity, author
history, quotas, pacing, semantic deduplication and per-step gates still apply.
The old unknown stays unchanged and cannot be replayed. The grant's expiration
is a local execution window, not a claim that the user's broader approval has
expired. A new target requires its own exact bound record.

Page lifecycle remains a host responsibility. Original RUNTIME requests a fresh
owned page per action and preservation of unknown scenes. Earlier cloud runs
reused a research tab; they must not be described as literal page-lifecycle
parity. Never navigate or close the retained unknown scene merely to continue
unrelated research. For subsequent logical actions the host must allocate a fresh owned tab, bind its
actual context, perform gated navigation and operations, then close that tab on
a known success or a diagnosed definite no-submit. Retain unknown tabs. Blank-tab
allocation is host context setup; navigation and page operations require their
normal permits. This ledger neither creates nor closes official CUA tabs.

### Comment composer preflight and single submission

A live cloud observation on 2026-10-19 found that placing text in the collapsed
comment strip did not reliably populate the expanded composer: the text could
appear in the DOM and disappear when the editor opened. A later comment was
visibly confirmed after opening the composer first and entering the reviewed
text in that actual editor. This is a UI-state finding, not evidence that any
earlier unknown submission failed.

For each authorized, reviewed comment:

1. Inspect the current page and open the visible comment composer using a
   supported native interaction. Do not treat the collapsed strip as the editor.
2. After the composer is visibly open, use supported native text entry/paste to
   enter the exact reviewed text. Never populate it with DOM mutation/evaluate.
3. Read back the visible expanded editor. Require exact complete text equality
   and a visibly enabled Send control before submission. DOM text in a hidden
   or collapsed element is insufficient. If opening the editor clears text,
   re-enter only before submission and repeat this preflight; do not assume it
   survived. Any content correction must still match the reviewed payload.
4. Click the visible Send control once with a native interaction, under the
   existing dispatch record and a fresh step permit. Never resend merely because
   the input cleared, the modal closed, the tool timed out, or a count did not
   change.
5. Immediately obtain a separately permitted screenshot and separately permitted
   DOM observation. Compare the exact full comment, expected account attribution
   and visible posting time. A count increase is supporting evidence only;
   neither it nor completion of the click independently proves success.
6. Record the actual evidence. If the exact posted comment cannot be confirmed
   and no definite platform rejection is observed, preserve submitted_unknown
   and the owned page. Follow the normal read-only reconciliation contract.

Every native interaction and observation above requires its own step permit;
this numbered sequence is not permission for one compound browser script.
Local fixtures can exercise ledger transitions but cannot establish that the
real editor accepted text or that the platform published a comment. Live host
observations, checked for tool errors before recording results, remain required.

### Investigate before continuation

An unknown outcome is not a platform failure. Before recording a continuation,
the host must inspect the original tool outcome, editor/input evidence, submit
step and available platform readback. Save the evidence and classify the cause
as confirmed host issue, confirmed platform rejection, or unresolved. Correct
and verify host issues before resuming the affected workflow. A visible platform
rejection may justify skipping that item; preserve the actual restriction and
never bypass account/security blocks. Inconclusive absence or a timeout remains
unknown even when the user authorizes independent work to continue.

The observed collapsed-editor text loss was a host-workflow defect. Subsequent
expanded-editor/native-entry/exact-visible-text checks produced a visibly posted
comment. That verifies the corrected interaction path, not the final outcome of
the earlier unknown comment. Keep both observations without rewriting history.

For comment submission the host must now record `record_cloud_comment_preflight`
with `operation_id`, the live `claim_token`, and a `report` containing exactly:
`expected_account`, `browser_context`, `observed_at` (timezone-aware),
`evidence_ref`, `browser_call_succeeded`, `editor_expanded`, `editor_visible`,
`visible_text`, and `send_enabled`. All four boolean fields must be true and
`visible_text` must equal the full immutable reviewed content. The report is
bound to the current attempt and expires after 120 seconds. Check the actual
CUA tool result for errors before asserting `browser_call_succeeded`.

Reserve the single native Send click with `purpose: "submit_comment"`; this
requires the fresh recorded preflight. This is a host-mediated contract: Python
cannot detect a caller disguising a Send click as a generic click or prove that
reported DOM text was visible. The host must classify the actual action honestly
and never perform a second Send merely because an outcome is uncertain.

For independently reviewed image publications, the optional attestation field
`instruction_scope: "future_independent_image_publications"` records an actual
standing instruction covering future unknown cases. It permits the truthful
original `confirmed_at` to predate a later unknown; never invent a new user
confirmation time. Normal execution-window expiry and host-policy validation
remain. Each blocking unknown needs its own immutable bound attestation; the
publication grant list is additive and does not change any old receipt. Existing
comment-only grants retain their narrower scope and are not publication approval.

### Preserve the observed note route

When preparing `get_feed_detail`, `post_comment_to_feed`, or
`reply_comment_in_feed`, pass the original actually observed navigable link as
`observed_target_url` when available. The helper checks the official HTTPS origin
and exact immutable note ID, then preserves its query string byte-for-byte in
the action's bound target and host plan. An empty sealed payload `xsec_token`
is not a reason to strip a routing parameter from a known working observed URL.
Do not fabricate, recover from private browser state, or guess routing tokens.
The host must navigate using the bound observed target rather than reconstructing
another URL. Existing actions cannot have their immutable target changed.

An unavailable page reached via a generated tokenless URL does not establish
that a note was deleted or that the platform is down. Compare the requested
route with the original observed link before classifying the cause. A corrected
read-only preflight must be explicitly documented and still use normal pacing;
it does not authorize a send or a replay of an uncertain publication.

### Review content before inspecting hidden schedule controls

Some creator pages reveal scheduling only after media upload. Do not invent a
date to pass normal publication review. Use the separate upload-inspection path:

1. `review_cloud_upload_inspection` takes `run_id`, the complete planned batch of
   `entries` (`content_id`, `tool: "publish_content"`, exact registered final
   `payload` with no `schedule_at`), and actual QA `evidence`. It applies the
   original content/stage/profile/strategy/cover/media checks to a detached
   review, stores an immutable `inspection_id`, and leaves ordinary review and
   calendar slots untouched.
2. `prepare_cloud_upload_inspection` takes `run_id`, one exact payload,
   `approval_ref`, `inspection_id`, `user_source`, and optional `operation_id`.
   It revalidates the inspection receipt and seals the actual images. The ledger
   operation is `inspect_publication_schedule`, not `publish_content`. Its
   successful outcome means schedule controls were observed, never publication.
3. Bind actual user authority, fresh identity and any required per-blocker
   unknown-continuation grants; claim and begin dispatch normally. Both claim
   and dispatch recheck content/media review. Every browser step remains gated.
4. For inspection steps, use only these declared purposes: navigation
   `open_creator`; click `select_image_upload_mode`, `open_upload_chooser` or `inspect_schedule_controls`;
   media_request `upload_reviewed_media`; passive read_observation; scroll
   `inspect_schedule_controls`. Registering a filechooser listener is local host
   setup. The actual chooser-opening click and setFiles upload require separate
   permits. Upload only the exact sealed manifest paths. Do not fill content,
   click Publish/Save, or submit a schedule under this operation.
5. Record actual visible timing constraints, date/time options and account
   identity. Inspection completion creates no post receipt and consumes no
   planned publication slot. An unclear upload outcome remains unknown.
6. Only after this observation, reserve actual feasible batch slots through the
   existing schedule API, register genuinely updated dated finals, and perform
   normal complete publication review. Prepare a separate `publish_content`
   action. No inspection receipt substitutes for that final review or authority.

The upload is an external change and may cause platform-side draft state; it is
not a read-only dry run. The host must enforce this restricted purpose through
actual supported browser actions. These guards cannot detect a host disguising
a final Publish click as an allowed inspection-control click. Preserve uncertain
scenes and never publish from the inspection action, even if all assets look
ready. Follow the existing owned-tab lifecycle on a known inspection result.

During an active upload inspection, a creator page may display only a name.
If exact identity evidence expires, keep the same live claim and reserve a
passive `read_observation` step with no purpose. Its target may accurately name
an already-open self-profile tab in the same verified browser session. Read the
actual unique account identifier without navigation or input, record that step,
and record the fresh identity against the active inspection action. Keep the
browser/session context unchanged and identify the observed tab in evidence.
A display name alone is insufficient. Do not start a competing identity action,
release the active dispatch, or invent recovery flags for ordinary stale identity.
The subsequent upload remains subject to the usual fresh-identity checks.

If a schedule-inspection claim expires before any upload was invoked, the narrow
`renew_cloud_upload_inspection_claim` helper may extend the same attempt. It takes
`operation_id`, the original `claim_token`, bounded `lease_seconds`, and an
`observation` with exact `expected_account`, `browser_context`, `call_ended: true`,
`upload_invoked: false`, fresh `observed_at`, and `evidence_ref`. All browser calls
must have ended; every attempted media_request must have a definite not_applied
receipt. Completed or uncertain media, ordinary publications/comments, and a
finished inspection cannot use this path. It revalidates content/media review,
keeps every consumed step and original attempt, and never reissues a permit.
Subsequent upload still needs current authorization/identity and a new gated step.

For chooser setup, attach a rejection handler to the pending filechooser promise
immediately, inspect actual click/chooser results, and use the actual retained
chooser handle. Do not assume a binding declared inside a prior script branch
is available later. A local ReferenceError before setFiles invocation is not a
platform rejection or evidence of an upload. Preserve precise evidence of which
call actually ran; do not repeat chooser acquisition if a valid handle exists.

Post-upload inspection verification: an ended, completed media upload in the same
inspect_publication_schedule attempt permits bounded passive read_observation
with truthful recovery_evidence even after the claim lease expires. It grants
no clicks, navigation, upload replay, saving or publication; account binding,
in-flight serialization and persisted pacing still apply.

For a publication whose final click was explicitly denied before execution,
renew_cloud_unsubmitted_publication_claim preserves its same attempt and editor.
It requires the exact not_applied final-click step, all browser calls ended,
fresh identity and truthful same-account/context observation with
final_submit_invoked:false. It revalidates the full dated review and authority.
No unknown or completed final submission qualifies. Subsequent navigation or
media upload is prohibited. This never permits repeating an uncertain write.

### Persisted browser recovery

On an ended browser timeout, missing original tab/session or CAPTCHA, follow
[CLOUD-BROWSER-RECOVERY.md](CLOUD-BROWSER-RECOVERY.md) before continuing. The host
API now returns this workflow from unknown step and platform-block receipts.
Use begin/advance/get recovery helpers to preserve the original action and
consume only a documented supported recovery attempt. Verify exact identity and
challenge absence, then `clear_cloud_recovered_block` with the same incident.
Unknown writes never replay; kernel reset, browser restart and a new page remain
distinct. Current CUA has no advertised browser-restart/reconnect API.

### Observed profile-card note routes

A note link actually observed on an official profile can use
`https://www.xiaohongshu.com/user/profile/<24-hex-profile-id>/<24-hex-note-id>`.
The adapter accepts this exact route only when the final note ID matches the
immutable `feed_id`, and preserves the complete observed query spelling/order.
The profile segment is a route identifier, not proof of logged-in identity or
note authorship. Do not reconstruct a tokenless URL, manufacture a token, or
substitute the configured account ID. Existing challenge and recovery gates
still apply; accepting a route is not evidence that a page or challenge cleared.
This route has offline adapter/host binding regression tests; a live read may
only be verified after any current platform challenge is actually resolved.

### Profile history: native cards only (owner workflow override)

For profile history, use one owned `user_profile`/`get_my_profile` action and
remain on that profile. The initial bound profile URL may be opened once.
Open each actually visible card with a native click, inspect its modal, and
close it using an observed native control. Do not extract its note href to a
new page or silently fall back to an `/explore/` URL. This is the owner's chosen
workflow; it does not assert that the platform never supports those URL routes.
The low-level URL validator retains safe route compatibility, while host prepare
rejects direct profile-note detail navigation. Pass `source_context:profile_history`
for history provenance; canonical detail preparation with that context also fails.

Reserve each card opening as `kind:card_read`, with
`target:profile-card:<actual observed 24-hex note ID>`, then execute exactly one
native click. This consumes a separate shared logical-read budget and 10–20s
read spacing; it is not a cheap generic click. Internal observations/carousel/
close controls retain the separate user-selected internal step interval.
No per-card goto/new-tab is allowed. The step guard blocks repeat profile goto,
other navigation targets and searches. Host classification remains trusted:
local Python cannot inspect a CUA call disguised under the wrong kind/context.
Card summaries alone never count as a fully read source. Record the actual note
ID, complete body/image coverage and any missing material separately for every
card; only complete observations qualify for the source pool. No live card-modal
verification is claimed until a permitted real read succeeds.

### Search results: native cards in the owned search page

`search_feeds` now supports the same-page native-card workflow. Open the bound
initial query with `kind:search` (including when the actual native call is goto),
and apply each observed query/filter interaction as its own search-budgeted step.
Do not hide repeated searches in generic clicks. Within the owned results page,
reserve `kind:card_read`, `target:search-card:<actual observed 24-hex note ID>`
for each single native click on that visible card. Each card consumes the shared
read quota and read spacing independently; existing search quota is unchanged.
Read complete body and all images in the resulting modal, then close an observed
native control and continue in the same results page. No extracted detail URL,
external-link or iframe navigation is authorized. Search actions reject generic
navigation and URL switches. Retain note ID, actual author, body/image coverage,
observation time and source evidence separately for each card; card snippets do
not qualify as full sources or establish a reviewed comment target on their own.
Local guards depend on honest host classification; they do not execute CUA or
independently recognize a click disguised as another kind.

### Native card entry for reviewed comments/replies

For a newly prepared comment/reply, provide `observed_entry_url` to
`prepare_cloud_action`: the exact actually observed official search-results or
profile URL, never a reconstructed detail link. Original tool payload schemas
are unchanged. Review, account, target-author dedup, pacing and preflight all
remain mandatory. The immutable target records this native-entry mode and the
sealed payload's `feed_id`; do not combine `observed_target_url` with it.

Use a new owned page for that comment action. Enter its exact bound search URL
with a `search` step, or profile with a `navigation` step, once. Existing native
search filters may use separately charged `search` steps with inert target
`native-filter:<actual observed control label>` after entry, without changing
the query. Then reserve `card_read` with `comment-card:<sealed feed_id>` and
native-click that exact observed card. This independently charges read quota.
Verify the actual modal's note/author and exact reply recipient before opening
the editor. Existing `record_cloud_comment_preflight` and one `submit_comment`
click remain required; Send is blocked until the bound card step completed.
Do not transfer a terminal research action into a comment, open detail URLs,
click a different note, or retry an unknown submission. Legacy already-bound
records are retained; operators must select this native-entry mode for new
card-based comments rather than fall back to legacy direct-detail plans.

Ledger-backed dispatch validation may re-enter cover-history validation while a
claim/begin transaction is active. Nested ledger work uses SQLite savepoints:
it cannot commit the outer action state, and either inner or outer failures
retain rollback boundaries. The six-post/three-person upload-inspection test
uses real synthetic image fingerprints and stored visual reviews through
prepare, claim and begin; this does not skip the full cover review.

### Read-only batch projection diagnosis

`cloud_batch_projection_status({"run_id":"..."})` compares each immutable linked
CloudActions outcome with the stored batch task/host projection and returns
`ledger_status`, `stored_task_status`, `expected_task_status`, receipt equality,
and mismatch count. It does not synchronize databases, release quota, clear
unknown results, create browser permits, or grant execution authority. This is
not an atomic cross-database snapshot; a concurrent outcome may require another
read. `not_applied` remains the raw ledger fact and maps to `failed` only in the
batch projection. A stale `submitted_unknown` projection is not evidence of an
unknown platform send when the bound ledger contains a valid not-applied receipt.

If explicitly reconciling a projection, the existing `bind_cloud_batch_action`
revalidates the original account/run/content binding and refreshes it. Never
rewrite the original receipt or retry a platform action to repair a projection.

### One retry per original action

A definite cancelled/not-applied original may have at most one separately
reviewed retry. `retry_of` must reference that original, never another retry.
Neither cancellation of a retry nor a process restart resets this limit. Exact
idempotent preparation of an already bound action remains a readback, not a new
attempt. Success, pending and unknown actions remain non-retryable. This rule
narrows technical admission only; it does not authorize a retry or replace
required materially new evidence after a denial.

Projection diagnosis requires an already initialized CloudBatch in the host.
A cold host returns `ERR_BATCH_NOT_OPEN` without creating or migrating batch
storage; it does not manufacture an empty ledger. Missing or changed immutable
bindings fail closed, not as an empty consistent result. Normal batch
initialization has its existing storage/migration effects and is not part of
this read-only operation.

### Persistent execution pause (separate from queue control)

`set_cloud_execution_pause({"paused":true,"user_source":"...","reason":"..."})`
records the owner's explicit pause in the existing action ledger. The exact
platform account is resolved from configured `expected_user_id`, not supplied
by the caller; aliases sharing that account and ledger share the latch.
Different platform accounts remain isolated. An absent latch preserves legacy
behavior, while malformed stored latch values fail closed. Existing ledger
schema version stays compatible: an additive audit table and immutable audit
triggers are initialized by the normal CloudActions constructor.

Pause blocks new mutation claims, begin-submit permits of already claimed
mutations, and every new non-observation browser step on mutation actions.
Read-observation on the original bound context is still subject to the original
identity/recovery checks; step completion, actual result recording and evidence
reconciliation remain available. A pause does not clear unknowns or undo calls
already in flight. The original queue control still only stops new claims.

Only a separate explicit owner-directed `paused:false` resumes admission.
`user_source` and reason are audit fields, never trusted proof of authorization;
the host must verify actual user permission before invoking this helper. Resume
does not extend old claims, renew authorization, clear account restrictions,
or grant a new action. Restart, elapsed time and challenge clearance do not
resume execution. No helper was applied to any real account during validation.

Limits: the latch coordinates only the shared ledger, not other independent
installations or platform-scheduled posts. There is a gap between issuing a
permit and the external tool call; callers must stop using unconsumed permits
when pause arrives. An already issued/in-flight browser call cannot be revoked
by this SQLite latch. Save its actual outcome without retrying blindly.

## Optional efficiency helpers (offline-verified contracts)

- `plan_cloud_read_reuse(operation_id, original_read?, current_observation?)` loads the actual
  stored action binding; no caller-supplied binding is accepted. Returns scoped补读 advice only,
  no pacing credit, freshness forgery or Send permission. See `CLOUD-READ-REUSE.md`.
- `explain_cloud_effective_rules(run_id, user_rules?, mandatory_rules?, runtime_values?)` requires
  an already open batch subsystem and the run's stored profile version. It never creates a batch
  for diagnostics or substitutes the latest profile. Caller rules/runtime snapshots remain
  unverified explanatory inputs, not authorizations. See `EFFECTIVE-RULES.md`.
- `register_cloud_owned_page`, `get_cloud_owned_page`, `plan_cloud_owned_page_cleanup`,
  `request_cloud_owned_page_close`, `record_cloud_owned_page_close`, and
  `observe_cloud_owned_page_inventory` implement the explicit lifecycle in `CLOUD-OWNED-PAGES.md`.
  The helper does not itself close a tab; the host must verify official-tool capabilities and
  normal execution authority. Missing native instance evidence means preserve/skip, never invent.

Before issuing any close permit, the host checks exact-account pause and the **existing** pacing
ledger using `mode=ro`, `query_only`, and SELECT in one read transaction (not immutable, so WAL is
visible). Missing/unrecognized state is not initialized into a clean ledger. Real account blocks,
any account call still in flight, and related/unproven-independent unknown steps prevent closing.
An unknown step from a positively different nonempty browser context and a stored pure-read action
is not, by itself, a permanent global cleanup block. Unmapped historical attempts remain blocked.
No pacing counts or historical facts are modified by this check; SQLite WAL reader bookkeeping is
not claimed to have zero filesystem side effects. The read check and external browser close are
not atomic; the tool host must still honor authorization and current browser evidence at dispatch.

These helpers do not add browser acceptance or live speed claims. Old paths stay available;
existing CloudHost initialization semantics remain unchanged. Only explicit register/request/result
calls write local owned-page records; a consumed unknown close is never reissued after restart.

The independent old-read exception requires an actual persisted step result: after the single call
ends, `record_cloud_browser_step(..., outcome='unknown', evidence={call_ended:true,
challenge_active:false, rate_limited:false, permission_denied:false, ...})` may preserve the observed
facts through the existing tool. Do not invent these fields to obtain cleanup. Missing proof stays
blocked. The action must also be a stored READ_OPERATION in a different known browser context.
All own historical `attempt_id` values in action events stay related, even if another peer has reused
an attempt identifier. This does not reconcile the old unknown or allow replay; it only avoids
blocking an independent already-finished page. Active account restrictions always take precedence.

Rule explanation additionally returns `authoritative_execution_control` read from persistent exact
account state. A caller's `runtime_values.execution_paused` is only a supplied snapshot; disagreement
appears in `runtime_control_mismatches` and cannot hide the true pause or resume execution.
