# Cloud browser recovery (host executed)

This flow is part of the cloud-host operator contract. It does not run a browser
from Python, create a login, or grant permission. Use official host browser tools
and their current documentation. Preserve the normal action/step ledgers and
rate limits throughout recovery.

## Enter this flow before continuing

On a CAPTCHA, unavailable original tab/session, or ended control-channel timeout:
1. Stop new side effects. Save the operation ID, immutable binding, attempt, last
   step result, owned tab/context, and an evidence reference for the editor/media
   state. Do not store cookies, passwords or tokens in recovery evidence.
2. Begin a recovery incident attached to that original operation. Inspect its
   existing receipts first. A timeout does not mean an action failed; a previous
   submit remains unknown until actual result evidence resolves it.
3. Diagnose through bounded official inventory/control probes and, when possible,
   the original page. Record whether the prior call ended, original tab exists,
   control is responsive, and CAPTCHA/rate/permission restrictions are observed.
   Missing observations are unknown, never an assertion that a challenge is gone.
4. With an actual visible CAPTCHA, use its ordinary challenge UI under actual
   user authorization. Do not replace sessions to evade a challenge, rate limit,
   permission restriction or login requirement. Permission/limit blocks remain
   blocked; repeated reset/new-session loops are not recovery.
5. For an unavailable session or stuck control channel only, choose a recovery
   method that current official documentation actually supports. Preserve login
   and editor state where possible. The incident consumes the selected attempt
   before the host executes it; a lost response cannot authorize repeating it.
6. Read the resulting real page and exact account identity. Only a fresh matching
   account, explicit absence of challenge, and no observed rate/permission block
   can mark the incident verified. Then clear an existing platform block using
   its separate evidence-bound API. Verification never resends an unknown action,
   publishes a draft, changes a receipt, or itself grants new write permission.

## Distinct recovery operations

- `kernel_reset`: resets the host JavaScript interpreter and object bindings.
  Re-read official documentation and rebind existing browser/tab handles. This is
  **not a browser restart** and must not be reported as one.
- `browser_restart`: only when an official documented tool explicitly provides
  it. The currently observed CUA surface does not advertise this capability.
- `reconnect`: only when explicitly supported and documented; not an invented
  socket/CDP route. It is not currently advertised by the observed CUA surface.
- Opening a new page: not a reset or restart. When official inventory proves the
  original session/tab is unavailable, one scoped official-home recovery may be
  used through the existing `recover_official_session` browser step. It must not
  carry an old write payload, reupload, resubmit or dismiss an unresolved challenge.

Never kill browser processes through a shell, export/delete cookies, switch
accounts, or use private endpoints. A supported kernel reset can be attempted
without waiting for the user when its actual purpose is restoring a failed
control channel and it is within the user's instructions. If no supported recovery
method is available, report that concrete limitation rather than waiting silently
or pretending a browser restart occurred.

## Host execution boundary

The recovery APIs persist diagnoses, a one-use action recommendation and actual
host evidence. They cannot authenticate conversation approvals, execute CUA, or
prove a screenshot is genuine. The assistant host remains responsible for one
real documented operation per permit, interpreting user scope, checking tool
errors, and recording truthful results. Local tests cover the state transitions;
they do not demonstrate an automatic browser restart.

The October 20 live observation was: the old tab was absent; a new inventory had
only a blank tab; opening the official home retained the expected login and no
challenge was visible. This establishes recovered access, **not** that restarting
removed a CAPTCHA, nor why the prior tab disappeared.

## Exact local helper contract

Use the existing `runtime/cloud_host.py --config <private-config> <tool>` CLI
with JSON on standard input, or the same tool names over cloud MCP:

- `begin_cloud_browser_recovery`: `operation_id`, unique `incident_id`,
  `observation`. The original operation supplies immutable account/attempt data.
- `advance_cloud_browser_recovery`: `incident_id`, `event`, `observation`.
  Events: `diagnose`, `request_recovery`, `recovery_result`, `request_new_tab`,
  `verify`.
- `get_cloud_browser_recovery`: `incident_id`. Reading never reissues a consumed
  recovery instruction.
- `clear_cloud_recovered_block`: `incident_id`, `scope` (`account`, `search`, or
  `write`). Uses the verified incident's original fresh identity timestamp and
  evidence, not a caller-invented later timestamp. No active block is an explicit
  error; do not create a fake block just to clear it.

Each observation has actual `observed_at` (timezone-aware ISO), `evidence_ref`,
with diagnostic booleans `call_ended`, `control_available`, `tab_present`,
`challenge_active`, `rate_limited`, `permission_denied`. Omit what is unknown;
missing flags cannot authorize a recovery action. `editor_evidence` may reference
saved local progress. Do not include credentials or browser session secrets.

`request_recovery` additionally requires `capability` with exactly `method`,
`supported: true`, and `documentation_ref`. Method is one of `kernel_reset`,
`browser_restart`, `reconnect`. This is a host report of actual official tool
support, not a switch that manufactures that support. Only the first successful
response has `dispatch_once: true`; on lost response inspect rather than retry.
The host must also use the existing browser-step gate where a page operation is
involved. `request_new_tab` is a separately bounded missing-page official-home
inspection, never an escape route from a challenge or an instruction to replay.

`verify` additionally requires `challenge_absent: true` and `identity` containing
exactly `account_id`, `observed_at`, `evidence_ref`, `browser_context`. Read the
actual exact account, not only its display name. Verification and clearing do not
refresh the action's write identity: record new actual identity through the
existing action protocol before any independently allowed later action.

The host's unknown browser-step receipt and explicit platform-block response now
point directly into this recovery flow. Follow the skill entry before continuing
blocked work; no background recovery daemon or native browser RPC is claimed.

### Restricted recovery exceptions preserve the restriction

A previously observed challenge remains latched even if a later inventory has no
original tab. There are only two narrow control-restoration exceptions:
- When a prior call has ended and the control channel is actually unavailable,
  a documented `kernel_reset` may restore the interpreter for observation. This
  does not restart/replace the browser or clear the CAPTCHA block. A latched rate or
  permission restriction still prevents this exception.
  Responsive controls plus an active challenge never justify reset.
- If official inventory proves the original page/session was already absent
  independently of this recovery, `request_new_tab` may open the official home
  once for inspection. Supply `session_already_absent: true` and actual
  `inventory_evidence_ref`. Keep the old challenge latch and block. This exception
  does not apply to a present challenge page or rate/permission restrictions and
  cannot be used for repeated replacement sessions.

A verified closure requires fresh real observations after restoration. An unknown
write stays unknown even when the browser is healthy again.

For the actual homepage navigation, pass the same `incident_id` inside the
existing `reserve_cloud_browser_step.recovery_evidence`, in addition to its
existing call-ended/session-unavailable/account/source evidence. The incident
must have consumed `request_new_tab`; the host binds it to one immutable step ID.
A new step ID cannot reopen the homepage again. Reuse of the original waiting
step ID follows the normal persisted pacing deadline; a consumed step never
reissues permission.

## Bounded same-page CAPTCHA recovery chain

When supported official browser instructions allow one reasonable low-risk
recovery, the host may use this chain before requesting takeover. It never
permits alternate-route probing, account/session replacement or clearing cookies.
No browser restart API is assumed. Do not trigger a challenge to test this flow.

1. Preserve the current tab and incident. Record actual control/page/challenge
   flags. A read-only action with an ended call and live exact original page may
   request `advance_cloud_browser_recovery` event `request_page_reload`.
   Include fresh observation flags plus `same_page` with exact `url`, `tab_id`,
   and the original claim's `browser_context`. URL must equal the original bound
   target. Writes/editors and rate/permission restrictions cannot use this path.
   If the page has changed or the context is uncertain, skip reload and use
   the normal authorized challenge flow. Never reconstruct a route.
2. The incident consumes its sole reload attempt before returning. Wait until
   its persisted `reload_not_before` (five seconds), and respect the step gate.
   Reserve one step with `kind:reload`, the exact original URL as `target`, and
   `recovery_evidence` containing `page_reload:true`, `incident_id`, `tab_id`,
   matching `expected_account`, `call_ended:true`, actual `evidence_ref`,
   `user_source`, and `recovery_reason`. Retain the SAME step ID on a pacing wait.
   One permitted step means exactly one official same-tab `reload()` call.
   New step/incident IDs cannot replenish the allowance. No pending/unknown
   browser step may be hidden by a reload. Record actual outcome normally.
3. Use gated passive observation, then event `page_reload_result` with actual
   fresh flags. The platform block remains. If the challenge remains, request
   event `request_challenge`; it consumes the incident's single normal challenge
   attempt. Actual challenge interaction still requires the existing host-policy
   approval and `solve_captcha`/`challenge_interaction` browser-step contract.
   The local incident itself cannot grant CAPTCHA permission.
4. Report `challenge_result` from actual passive observation. A remaining
   challenge selects `handoff`. Do not restart the chain, try alternate paths,
   or use a new incident ID. If the challenge is absent, use existing `verify`
   with fresh exact account identity and then the existing evidence-bound block
   clear API. A completed click/reload alone never means recovery.

A genuinely hung control channel may still use the existing documented kernel
reset path to regain observation; reset does not clear platform restrictions.
Unknown writes/editors retain their original page and immutable uncertainty,
are never reloaded by this read-only path, and are never resubmitted. The host
executes these steps through official CUA; Python stores and gates the chain,
it cannot automatically call CUA or independently verify a host report.

## End a failed pure read without replay (`abandon_read_only`)

This terminal incident state is NOT a successful read, verified browser recovery,
permission to clear account restrictions, or a retry permit. Use only after the
host checks the **existing** pacing ledger and original ended call evidence: no
active account restriction/in-flight call, original pure-read operation, explicit
false challenge/rate/permission flags, and no historical incident restriction
latch. Missing ledger/evidence is not a clean state. The recovery observation
must be fresh; time passing or a disappearing tab never proves these conditions.

`advance(..., 'abandon_read_only', observation)` preserves the original action's
unknown status, result, immutable binding/digest, attempt and spent budgets. It
returns no dispatch permit. Any newly observed restriction is retained instead
of abandonment. The incident remains inspectable and cannot be advanced/reopened
for that original operation. A different independent read/health operation may
start a new incident with newer evidence; all its normal gates still apply.
This does not authorize recreating or replaying the old unknown query under a
new ID, releasing pacing unknowns, clearing account blocks, or continuing a write.
