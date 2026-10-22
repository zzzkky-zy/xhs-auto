# Private business-profile transfer safety

This utility transfers business settings and referenced material, **not** an operational
checkpoint. It contains no prior task history, does not restore receipts, and cannot
prove that an account has no pending or unknown submissions. Same-host continuation
must continue using the original state. A new prepared profile still requires the
existing login, exact-identity verification and user review/authorization flow.

## Compatibility change (2026-10-22)

Exports no longer write `same-host-settings.json`. Full settings may contain private
credentials, so they are not exported by default and there is no sensitive-config
export option. Runtime preferences are an explicit allowlist, including separately
allowlisted operations, resource and retention fields. Unknown fields are omitted;
unsupported structured values for known fields fail visibly rather than silently
changing their meaning. Existing format-1 bundles remain importable, but unknown
preferences are filtered again and any old full-settings file is not copied.

Business profile text, material content, confirmation references and allowed-tools
review inputs are preserved. `allow_write` and `allow_reconcile` stay false, and the
new expected state identifier stays empty. Account identity must agree between the
bundle's identity fields and its portable preferences. No task database is created.

## File safety

Only manifest-listed, hash-verified files directly under `assets/` are copied.
Extra assets, nested asset directories, traversal/absolute paths, symbolic links
(including parent directories) and files with multiple hard links are rejected.
Required product/face materials must bind to a listed file with the expected hash.
Files are safely read again immediately before copying and the written copy's hash
is checked. The original config/database are opened read-only, never rewritten.
New files use private permissions; existing destinations are never overwritten.
A failed copy can leave an incomplete new directory, but no prepared configuration
or success result is issued; do not treat that directory as a completed transfer.

This is not a sandbox against a malicious concurrent process that controls parent
directories, nor an arbitrary-content secret detector. Business text/material may
itself be private; keep all bundles private and do not publish them. The exporter
no longer implicitly includes the full runtime config, but that does not imply
that every possible user-supplied document is credential-free. No browser, real
account, upload or operational actions are performed by these tests.

## Regression coverage

`tests/test_private_profile.py` uses temporary SQLite business profiles and material
files. It checks normal relocation, original-state preservation, default omission
of full configuration and unknown nested credentials, safe legacy preferences,
extra assets, links, traversal, changed files between validation and copy, exact
identity mismatch, required material bindings and disabled execution defaults.

Cloud-host exports read the existing `cloud-batches.sqlite3` and check its recorded
account identity; local exports read the existing `tasks.sqlite3`. A missing cloud
database is not replaced with or inferred from a local database. Regression tests
also construct a real temporary CloudBatch/CloudActions profile, close it, export
and prepare it, and verify that its original database/configuration are unchanged.
The original configuration, selected business database and commonly named credential
files are explicitly rejected if a business-profile reference tries to include
them as ordinary material; they are not silently omitted from a supposedly complete
transfer. This filename/path guard is still not a general secret-content detector.

Exact `${PRIVATE_ROOT}/...` string references anywhere in a profile, including
confirmation sources, must name a manifest-listed file. Missing references and
traversal fail before creating the destination. References embedded in prose are
not a complete machine-verifiable inventory; explicit traversal there is rejected,
but callers should use separate exact file-reference fields for completeness checks.
