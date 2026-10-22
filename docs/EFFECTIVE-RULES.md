# Read-only effective-rule explanation

This diagnostic finds disagreements **before** an operator attempts work. It does
not fix configuration, schedule content, approve an action, or resume a paused
account. A clean report means only that the supplied rules agree—not that a
browser is logged in, evidence is authentic, or publication is authorized.

## Interface

`runtime/effective_rules.py` exports:

```python
explain_effective_rules(
    config=config_snapshot,
    profile=run_pinned_profile,
    context={
        "account_id": "exact expected_user_id, not an alias",
        "run_id": "existing run identifier",
        "batch_kind": "legacy",  # or future
        "evaluated_at": "2026-10-22T02:00:00Z",
        "profile_version": "content_review.digest(profile)",
    },
    user_rules=[], mandatory_rules=[], runtime_values=None,
)
```

All inputs are already-read JSON values. The function does not open any state
store, create one, call a browser, read the clock, or change input objects. The
host must obtain the **run's pinned profile**, never fall back to the current
profile. Exact configured account and profile fingerprint must match. The host
must select the scheduler path from that profile; caller-provided labels are not
proof that a run actually uses that path.

Each user rule contains `field`, `value`, `source_ref`, `issued_at` (with timezone),
and `scope` with exactly `account_id`, `run_id`, and `batch_kind`. Missing evidence,
unknown fields, future timestamps, and incomplete scopes are unresolved. Rules
for other exact scopes are ignored. A reference is not authentication of consent.
The latest timestamp wins within a scope; contradictory equally recent rules are
reported instead of selecting a convenient value.

Mandatory constraints contain those fields plus `operator` (`equal`, `minimum`,
or `maximum`) and `expires_at`. Numeric bounds support integer fields; unsupported
constraints remain unresolved, not silently skipped. A constraint contradicting
the requested value is a conflict: the diagnostic does not choose a workaround.
Expires-at is required for this input format; callers may not invent an expiry or
pretend a stale platform observation is current.

Precedence is mandatory boundaries, latest evidenced user requirements, confirmed
pinned profile/configuration, then generic defaults. Mandatory boundaries are
intersected, not overwritten. Existing permissions and pause checks remain in the
executor; `execution_authorized` is **always false**, even if no conflicts exist.

## Coverage and limits

- `publication_spacing_seconds` means spacing between public publication times,
  **not** delay between submitting two appointments. Existing legacy minimum is
  3,600 seconds and the future scheduler minimum is 5,400 seconds. These different
  paths are not automatically a conflict. The full legacy random 60–90 minute
  schedule, time window, existing reservations and daily quota are not re-planned.
- `browser_step_interval_seconds` is independent of read/search/write intervals
  and hourly read/search limits. Changing it to `[1, 5]` leaves those unchanged.
- `comment_strategy` preserves `C_max` and optional zero-C fallback. C is a cap,
  never a required number of promotional comments. `allow_c` remains separate.
  `check_comment_allocation(strategy, counts, allow_c=...)` checks integer rounding
  and the existing A/B proportions; it neither chooses comments nor grants quota.
- Future profile fields `group_chat` and `activity` become `required_group` and
  `required_activity`; `is_original` is preserved. These are exact requirements,
  not substitutes if a platform selector is missing. Actual selection still needs
  browser readback. The diagnostic does not evaluate fallback availability.
- Existing first-batch overlap exceptions are **unresolved here** and require the
  existing scheduler's first-run/grid checks. They never become general permission
  or carry into another run through this helper.
- `execution_paused` may be explained when explicitly supplied; omission is not
  evidence that execution is running. Grant validity, pause generation, action
  permissions, platform restrictions and quotas are not authenticated here.

The output contains per-field value, source, exact scope, overridden sources,
`conflicts`, `unresolved`, `ignored`, `runtime_mismatches`, and `needs_decision`.
If a `runtime_values` snapshot is supplied, its known fields are compared with the
explained values. A required 90-minute gap against an actual 60-minute executor
snapshot is reported. An absent snapshot yields `runtime_compatibility=unverified`;
a partial snapshot checks only its supplied fields. No output means the existing
executor has been reconfigured to match the explanation.

Malformed core context raises `ValueError`; malformed individual rules produce
unresolved entries. The caller must not use `needs_decision=false` as a send gate.

## Integration and verification

A host wrapper should read only already-open existing state, obtain the pinned
profile and exact identity, and call this function. It must not initialize a batch
or migrate/create a database merely to explain rules. Keep the diagnostic separate
from application of new configuration and from historical appointment changes.

The dedicated offline tests cover path differences, explicit 90-vs-60 mismatch,
internal-only timing changes, precedence and conflicting timestamps, exact scope,
expired constraints, missing source, profile/identity mismatch, C cap/fallback,
input immutability and deterministic output. Source consistency checks pin generic
pacing defaults and scheduler minimums; intentional executor changes must update
these checks and explanation together. These are not live-account tests.

Evidence references must be nonempty strings; mappings, lists, numbers and booleans
are not converted into references. Every mandatory constraint value passes the
same field-specific validation as a user requirement before comparison. In
particular `False` cannot be replaced by numeric `0`, numeric fields do not accept
booleans or string/float coercion, and NaN/infinity are rejected. Invalid evidence
or values remain unresolved even if Python's loose equality would compare them
equal. These checks validate shape, not the truth or authority of a reference.
