# Daily comment boundaries and public-invitation evidence

This adds scoped **host evidence**, not an authorization service, platform
verification, or a replacement ledger. No browser access occurs in this module.
`DailyComments` remains the source for current-day local successes and uncertain
reservations, across all runs/aliases for the exact platform account in one
shared state directory. Separate devices/state directories are not magically
coordinated.

## Practical effect

A gap three days ago no longer consumes all of today's capacity. Known old
authors and uncertain targets remain excluded. An unknown current-day upper
bound blocks comment writes only; research and evidence/status reading continue.
A finite day-specific allowance expires at Beijing midnight or its earlier
expiry. It does not reset the count or override the user's operational pause.

Publicly inviting a reader to message the account is distinct from sending a
private message or actually issuing points. Structured evidence for the former
cannot authorize either of the latter.

## API and trust boundary

`CloudCommentBoundary(actions, evidence_verifier=None)` exposes:

* `record(operation_id, kind, evidence)` — stores a host-reported packet for
  `history_gap`, `daily_coverage`, `public_scope`, or `daily_grant`.
* `status(expected_account=None)` — reports current evidence, including explicit
  coverage scope; no external-account activity is represented as independently
  verified by the plugin.
* `guard(operation_id, profile_version=None, author_evidence=None)` — an additive
  veto at write admission/steps/final Send. Non-comment reads are not blocked.

All packets contain exact expected account, unique immutable evidence ID,
original user-message reference and quote, source-read evidence reference and
observation time. Those strings **do not authenticate themselves**. The existing
host verification hook still runs; a denial is not bypassed.

Only a separate, genuinely source-checking `evidence_verifier(kind, binding,
packet)` returning exactly `True` can mark a packet independently verified.
The default host's permissive verification hook must NOT be substituted for
this adapter. Without an adapter packets remain `host_reported_unverified`:
coverage cannot remove a current-day unknown, and a public scope/finite grant
cannot be treated as verified reusable evidence. This produces a request for
**evidence verification**, not a blanket request for renewed user consent.
Actual tool policy and original action-bound user authorization always remain
necessary. No adapter is claimed to authenticate real user messages in offline
tests.

## Coverage, history and finite scopes

Normal known managed execution continues under the original daily guard. It
is not stopped merely because another hypothetical device could exist. Its
reported scope is only known plugin executions, not proof of zero outside
activity. Missing/recovered/unreconciled history must be explicitly recorded as
`history_gap`; recovery quarantine is handled separately by the recovery guard.
An empty ledger plus a relevant gap never implies zero usage.

A gap records an inclusive date range, or two null dates for unknown scope,
plus known excluded author and target IDs. A current-day/unknown-date gap needs
fresh verified coverage. `account_activity_upper_bound` risk cannot be cleared
by `known_plugin_executions`-only coverage. Old dated gaps retain target/author
exclusions without imposing a new global three-day-history completeness rule.

Coverage binds the exact Shanghai date and expires no later than midnight.
The module captures actual charged keys itself, never caller-selected keys.
The effective charge is the larger of current local charges and the verified
snapshot upper bound plus newly charged local keys. Thus known snapshot actions
are not double-counted and later reconciliation does not silently release an
outside conservative bound. Refresh verified evidence to establish a smaller
bound; do not edit history or derive it from an empty database.

Public scope packets bind public channel, semantic scope, benefit-commitment
fingerprint and risk fingerprint. Private-message and benefit-fulfilment flags
must both be false. Changed commitments/risks, revoked scopes, different exact
accounts, or expired day-specific evidence cannot be reused silently.

Finite grants additionally bind run ID, pinned profile version, scope ID,
Shanghai day, and expiry. Once configured for an account, subsequent writes
need matching current evidence; setting
`cloud_comment_authorization_mode="limited_daily"` explicitly also enables
this requirement. Existing normal workflows without this mode/grant do not
acquire a new impossible prerequisite. Grant checks do not create a second
quota counter or extend the existing action authorization expiry.

## Offline verification

`tests/test_cloud_comment_boundary.py` uses synthetic temporary ledgers only.
It covers old gaps versus current uncertainty, account/day/expiry mismatch,
shared-ledger cross-run charges, trusted upper bounds and actual snapshot keys,
non-release after reconciliation, scope changes/revocation, invitation versus
DM/fulfilment, source-string forgery, host denial, and unrestricted read/status
access when finite writing evidence expires. Native browser/user-message
adapter validation is not asserted by these tests.

### Revision and reuse safety

Coverage snapshots also bind the actual relevant history-gap IDs. A newly
discovered current-day/unknown-date gap requires new verified coverage; a newly
recorded strictly old dated gap does not invalidate today's coverage. Individual
finite grants can be revoked independently of the standing public scope.

Each finite-grant use calls the independent adapter with
`daily_grant_use`, the **current** immutable action binding, and the saved grant
and scope packet. The adapter must check that the actual comment remains inside
the semantic, benefit and risk scope. Reusing a source message is not permission
to change its promises. Every host/core construction must forward the same
trusted adapter. If it is absent, verified stored packets alone cannot pass the
optional finite-mode dispatch check. This check is not a new user-consent prompt.

A locally known single undated uncertain submission already has a conservative
one-comment charge in `DailyComments`; it does not, by itself, invent an
unbounded missing-history requirement. New known uncertain keys increase the
existing upper bound normally. Only an actual relevant history gap makes the
outside upper bound unknown. Adapter failure closes the finite write gate with
a clear verification error and without exposing raw adapter diagnostics;
status, ordinary reads, and delayed outcome reconciliation remain available.
