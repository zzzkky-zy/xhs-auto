# Reliability hardening — 2026-10-22

Status: local candidate only. Version remains 0.6.0; no release, installation, or live-account acceptance is claimed.

## Implemented
- Retry lineage: prohibit chains and allow at most one child per root. Re-check legacy lineages before dispatch and mutation steps; retain original receipts.
- Projection diagnostics: report authoritative linked action versus stored task status, without silently reconciling. Cold host refuses to initialize storage; missing bindings fail explicitly.
- Persistent execution pause: separate from queue pause, shared by exact configured account within one state directory. Check before new mutation claims, submit and non-observation UI steps. Preserve read observations and result recording. Resume is explicit, does not renew authorization, and requires the host to verify the user instruction.
- Correct repository metadata; document delivery evidence and fifteen reliability mechanisms with an open backlog.

## Verification
Baseline: 728 tests, one skipped. Final: `python -m unittest discover -s tests -q`, 739 tests in 39.551 seconds, OK with one skipped (738 passed). Two intermediate full runs failed on obsolete mocked bindings; fixtures were corrected, not production guards weakened. Focused independent review: 27 tests passed. `git diff --check` and runtime compile checks passed.

Review also caught and fixed cold-query storage creation, hidden missing bindings, and legacy retry-chain dispatch. No live browser or account actions were used for testing.

## Limits and remaining work
Pause cannot retract calls already sent or permits already handed to an external tool. The check-to-external-call boundary is not atomic. Different state directories are not a global synchronization service. Read-only projection diagnosis currently requires an already initialized batch.

This is not restoration of all historical fixes. Missing or unverified areas include durable cross-environment recovery, semantic authorization evidence, bounded external-day baselines, finite daily grants, comment lease renewal, read-only incident abandonment, owned-tab cleanup, and single-Send permit consumption. See OPERATIONAL-RELIABILITY.md and reliability-backlog.json.

The local source includes earlier unpublished commits. Local commit/bundle/test evidence does not prove remote publication or installation. User-requested operational pause remains in force; engineering work must not restart operations or alter existing platform schedules.

## Follow-on hardening

Added `scripts/cloud_handoff.py`, a read-only integrity checker for existing, quiescent, same-path snapshots. It refuses absent or incomplete databases rather than constructing empty state. It checks exact identity, state counters, unresolved outcomes, covered assets and configuration/code fingerprints. It does not prove the snapshot is the latest, perform a backup, restore data, or enable execution. Real runtime-created temporary databases are integration-tested; initial handwritten schema fixtures missed two incompatibilities that independent review identified and the implementation corrected.

Also fixed the service ready/ownership publication race found during full regression. Stop now waits a bounded interval for a held supervisor lock to publish its record, retaining all original process-identity checks; timeout never authorizes a kill. A deterministic regression fails with the old behavior. See CLOUD-HANDOFF-INTEGRITY.md and SERVICE-LIFECYCLE-RACE.md.

## Current release gate (supersedes earlier partial test summaries)

The final root regression reproduced one service-stop race in 754 tests and later three direct-integration failures in 757 tests. The stop race has a deterministic fix and independent regression evidence. The readiness probe had an unrealistic 200ms per-request ceiling; the fixture now validates actual readiness within the same total deadline and retains sanitized failure diagnostics. Two executor failures from that original run remain unclassified because the old bridge discarded their underlying errors. A later isolated pass is not proof those failures are resolved.

New Go bridge error classification source/tests are pending formatting, compilation and execution: the pinned Go toolchain is not currently installed. The existing binary is unchanged and cannot validate this source patch. Downloading the official project-local toolchain is awaiting user approval. No new executable package, public release, installation or complete stable acceptance is claimed. Full regression must be rerun against the rebuilt binary, with failures retained and classified.

## Verified local candidate — final 2026-10-22 result

The toolchain approval and build blocker above are now resolved. Official Go 1.27.2 was installed only inside this project, after the archive SHA256 matched the official release and lock file. Go focused tests and the complete module test suite passed; the pinned offline vendor build produced linux-amd64 binaries with identical SHA256 `e6f764052f8d2d0bbd2ac129a60456718cc72ac162f7d8dd244587523802e47b`.

The first rebuilt-binary integration run exposed a free-text missing-task error that the safer bridge correctly refused to relay. The runtime now emits `ERR_TASK_NOT_FOUND`; tests assert that exact code and no state changes, rather than accepting a generic executor failure. The runtime package now includes the handoff checker as well as its documentation.

Final rebuilt-binary Python regression: 763 tests total, 762 passed, one skipped, zero failures, 40.625 seconds. All 2564 covered source/test/doc/vendor and native binary files had identical before/after hashes. Earlier failed runs remain part of the evidence and are not overwritten. The two earlier unclassified executor failures were not retrospectively assigned a cause; future failures now have safer classification and explicit diagnostic retention.

This closes the local candidate verification stage, not remote release, installation, live-account testing, or every reliability backlog item. No real social account, existing platform schedule, or user-requested operational pause was changed. A linux-amd64 candidate does not establish Mac compatibility.
