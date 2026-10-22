# Scoped read reuse (local advice, not Send permission)

## Practical result

A completely read note need not be read from top to bottom again merely because
its account/target observation expired. Retain the original reading record,
observe the current exact target, compare the actually observed content
fingerprints, and read only the changed or missing scopes. This is offline-tested
planning behavior, **not a measured live-platform speed improvement**.

## API and evidence

`cloud_read_reuse.plan_read_reuse(binding, original_read, current_observation, now)`
is a pure function. The host must load `binding` from the persisted action, never
accept an arbitrary caller's replacement binding. Supported bindings are a
note-bound detail read, main comment or reply, with `expected_account` and
`payload.feed_id`; reply also requires `payload.comment_id`.

`original_read` is either null (no reusable proof) or the original host reading
record: `account_id`, `feed_id`, `author_id`, timezone-aware `read_at`,
`evidence_ref`, `body_complete`, `body_fingerprint`, `images_complete`,
`image_count`, `image_fingerprints` (actual image ID -> fingerprint), and
`comment_fingerprints` (actual read comment ID -> text fingerprint). Image count
must exactly match the completely read image set. Zero images is an explicit
complete zero-image record, not a missing list. A `cached: true` lookup flag alone
does not supply this coverage. The host retains actual source artifacts; hashes
cannot prove that a human/agent read them. Do not reconstruct absent coverage
by assuming old successful actions were complete.

`current_observation` is either null or a separate actual UI observation:
`account_id`, `feed_id`, `author_id`, `browser_context`, `observed_at`,
`evidence_ref`, observed `body_fingerprint` and `image_fingerprints`,
`relevant_comment_ids`, `relevant_comments_observed`, `comment_fingerprints`, and
for a reply `reply_comment_id`. When the persisted reply payload contains
`user_id` (the reply recipient, not the note author), current evidence additionally
needs `reply_user_id`: absence requests only `reply_author` observation and a
mismatch is rejected. A legacy binding without that field does not invent a reply
author requirement or claim that a recipient identity was verified. Empty relevant IDs means genuinely observed no
related comments, not an unexamined or collapsed thread. The host must select all
comments needed to judge this intended comment/reply and observe their actual
contents. No requirement to expand unrelated nested threads is introduced.

An unchanged comment count **never** proves unchanged comments. A missing related
comment needs its own read; a new/edited related comment needs only that comment
read. A missing current content fingerprint requests observation of that
fingerprint, not a fictional claim of unchanged content. Generating a fingerprint
from an actual UI capture is not itself a full content reading.

The planner preserves original `read_at` and `original_evidence_ref`, returns
`cached: true`, `fresh_read: false`, `send_authorized: false`, and
`browser_state_independently_verified: false`. It returns either `reusable` or
`needs_read`, with exact `{scope, reason}` work items. Future timestamps and
account/note/author/reply mismatches raise `ERR_READ_REUSE`; they are not repaired
or converted to fresh evidence. Observations older than 120 seconds require fresh
target checks, without automatically invalidating prior full body/image reading.

**This is not Send preflight.** A reusable result grants no permission. Existing
identity, review, exact editor, actual target/author/reply/comments, pause,
unknown-submit and final-send checks still apply. Before actual Send, the host
must obtain fresh observations in the actual claimed browser context; a planner
result cannot substitute for those checks or clear a block. The original normal
reading path remains available; missing historical evidence is not a new global
stop condition for independent tasks.

## Budget scope (existing implementation retained)

`CloudPacing` already atomically shares reservations by exact account in one
configured canonical private state directory. Distinct run IDs and configurations
pointing at that directory share rolling limits and persisted deadlines. Restart
does not reset these limits. Actual native card reads still each use `card_read`;
searches still consume search and read budgets. Local reuse advice costs zero
because it opens no browser and reserves nothing. It does not refund earlier reads.

Separate state directories or machines are **not** a global shared ledger. Do
not claim otherwise or create another directory to obtain a new quota. This
change introduces no second quota database, directory migration, synchronization
service, relaxed limit, new random delay or extra permission.

## Suggested skill insertion

> 复用来源时保留原完整阅读时间、正文及全图覆盖证据，不把本地旧结果
> 当成新读。可对已登记动作显式请求复用检查；根据返回的具体缺项补读，
> 不因120秒目标检查过期重新展开无关楼中楼。发送前仍实际核对当前精确
> 账号、笔记、作者、回复对象、相关评论和编辑器；复用建议不是发送许可。
> 缺少历史完整证据时沿正常阅读流程补齐，不因此停止其他独立目标。

## Offline verification

`tests/test_cloud_read_reuse.py` uses synthetic evidence only. Cases cover scoped
reuse, old/future timestamps, account/note/author/reply mismatch, incomplete image
coverage, changed body/images, unchanged count with changed comments, missing
relevant scope, no I/O or input mutation, and two configurations/runs plus restart
sharing the existing account budget. Existing `test_cloud_pacing.py` separately
covers native-card charging, atomic concurrency and consumed permits.
