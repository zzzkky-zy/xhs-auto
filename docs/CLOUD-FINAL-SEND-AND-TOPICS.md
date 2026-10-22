# Final Send and separate topic readback

These are local safety gates. They do not execute a browser, prove platform
acceptance, authenticate a user's instruction, or resume a paused account.

## One-use final step

For `reserve_cloud_browser_step`, classify the actual final comment Send as
`purpose=submit_comment`, and the final future-policy image publication as
`purpose=submit_publication`. Never disguise either as an ordinary click.
Editor expansion and other internal clicks remain separate steps. This protocol
does not automatically infer whether an arbitrary unlabelled click is Send;
the host must classify it honestly and may not bypass the final-step entrypoint.

After all existing account, authorization, content and editor checks, the host
commits `final_send_consumed` in the action ledger before asking the pacing
ledger for a step. One attempt may receive at most one final Send permit,
regardless of `step_id`, process restart, lost response, replacement claim/attempt ID on the same action, or a later not-applied
browser report. Do not renew/rewrite a publication whose final permission was
consumed. A pre-permit pacing response explicitly returning `permitted=false`
(other than `already_reserved`) records `final_send_not_issued`; only this
known non-issuance allows waiting and requesting later. Exceptions never release
consumption. The stores are not atomically committed together: a crash may lose
an opportunity to send, but never creates a second opportunity to send.

A consumed permit is NOT a successful comment, public post, or scheduled post.
Keep an unknown result unknown; record/reconcile only genuine visible evidence.
If the compatibility publication audit event was not saved after a crash,
use passive observation rather than manufacturing a Publish event or replaying.

## Actual topic lists, not a count or invented tags

Only configured template/activity requirements apply. Existing historical
published/scheduled receipts are not revalidated or modified by this new gate.
For a future-policy action using a template or activity:

1. Read the real template's complete topic list and the separately observed
   activity-derived topic list. An activity may genuinely add no topics; retain
   that observed empty list and its evidence rather than guessing.
2. Use the pinned run profile's `publication_topic_expectations` when present.
   Otherwise call `record_cloud_publication_topic_expectations` on the existing
   action. It retains the following against its exact binding and run profile:

   `template_name`, `template_topics`, `template_evidence_ref`,
   `activity_name`, `activity_topics`, `activity_evidence_ref`.

   Names exactly match the reviewed payload. For an unrequested source the name
   and evidence are null and the list is empty. Template and activity evidence
   are separate actual observations. This is retained host evidence, not an
   independently authenticated page result or new user permission. No user
   reapproval is needed merely to read an already authorized template.
3. The final preflight additionally supplies `visible_template_topics` and
   `visible_activity_topics`, alongside the existing `visible_topics`. Check
   each source list against its retained baseline and the combined chips against
   their union. Ordering is not material; duplicate, missing or extra chips fail.
4. Missing actual baseline returns `ERR_PUBLICATION_TOPICS_UNVERIFIED` with the
   above read-and-retain path. Do not invent nine topics, silently replace a
   baseline, automatically delete an activity chip, or change the reviewed body
   or fixed CTA to make a check pass. A changed binding needs fresh observations.

Tests use synthetic temporary ledgers and synthetic observations only. They
prove conservative permission handling and comparisons, not live UI acceptance.

## Upgrade of already active comments and image publications

New genuine `begin_submit` commits `final_send_protocol_started` in the same
transaction as entering submitted-unknown. An already active old comment or image publication with
no such marker is observation/reconciliation only. Adding a fresh preflight,
changing a step ID or reusing a claim cannot create the missing marker. Do not
infer that an old completed click was or was not Send. A genuine not-applied
outcome can use the existing separately reviewed single-retry/new-action path;
unknown is never rewritten to not-applied for migration. Authorized actions not
yet begun can enter normally and receive their marker. Existing legacy
`publication_final_step_permitted` events block another final permit regardless
of whether the new shared consumed event exists. Result recording stays open.

Old image publication also requires the genuine begin marker: the old pacing
permit could have committed before its compatibility event was saved. Missing
that event alone is never proof of no prior Send. Preserve the old unknown and
observe/reconcile; do not use a new preflight to bypass this migration guard.
