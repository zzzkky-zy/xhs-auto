# Future-batch content and publication quality

Applies only to a newly created cloud run whose immutable profile contains
`future_content_policy.version=1`. Existing runs retain their original profile,
review, schedule and cover quota. This does not edit or invalidate earlier posts.
The new plan is six posts and fifty total main comments/replies per batch/day as
configured by the owner. The confirmed preferred Shanghai slots are 09:00, 11:00,
14:00, 16:00, 19:00 and 22:00. The next complete batch starts production now;
reserve its actual times only when content is ready and the platform range has
been observed. Start at its earliest allowed time, preserve at least 90 minutes
between posts, keep every time within 09:00–23:00, and continue next day when
needed. Later missed preferred slots may shift sequentially under those same
constraints. Never backfill a past time or compress the gap. Existing first-batch
schedules and the specifically approved first-batch overlap exception remain.

## Useful, compelling content

Record the actual title and cover wording, the specific curiosity/question/
contrast/pain-point mechanism, and where the body delivers that promise. A
keyword count, a catalogue heading or a generic “passed” is not a quality review.
Do not invent personal experience, results, offers or success guarantees.

Every educational post includes a complete worked example and a directly reusable
fill-in template, formula or script. Self-introduction posts need both a blank
fill-in template and a completed example. Record exact excerpts and explain how
the reader can save and reuse them. Body product mentions are optional; do not
force an unrelated placement. All future posts end with this exact CTA, including
line breaks and punctuation:

⬇️下方加入用户群，用Answereq轻松陪跑面试

（群内发放免费体验资格）

关注我获取最新大厂岗位list（日更）。

The group entry and job list are supplied/managed by the owner. This text does not
authorize creating a group, collecting members or producing a separate daily list.

## Three distinct reference-person covers

Exactly three of six covers use the same supplied reference person. Actually use
that reference when generating, then inspect likeness and the final cover. Change
pose/action, expression, clothes and composition to fit each post. Bolder visual
contrast is allowed; sexualization, invented identity and claims of authentic
photography are not. Other three covers follow the existing non-person rules.

Use `record_cloud_cover_history` only for exact known published or successfully
scheduled cover assets, with their actual source evidence. Keep both states;
passing the scheduled time is not proof of publication. An uploaded reference
photo is not a published cover. Coverage is the explicitly recorded history,
not automatically the entire account.

Before final review, compare each proposed person cover against published AND
scheduled history and the other two candidate person covers. The helper uses
pixel fingerprints plus aHash/dHash similarity and a bound host visual comparison
of pose, expression, clothes and composition. Exact pixel duplicates fail; near
hash matches require explicit supported visual differences. Perceptual similarity
alone neither proves identity nor proves originality. Record a passing visual
review and attach its review ID to the cover evidence. Changing any image, peer
or history requires a fresh review. Preserve the actual assets and evidence.

## Native topic-template and original-declaration workflow

Original implementation: `mcp/xhs-mcp-custom/xiaohongshu/publish.go`,
`applyTopicTemplate` and `setOriginal`. Topic templates are separate from long
article layout templates. Cloud hosts must reproduce the observed UI through
supported native locators/clicks, never port Go's arbitrary-JavaScript clicks.

1. In the authorized, reviewed post editor, inspect the actual topic control.
   Open the control next to “话题”, choose “使用/管理话题模版” (the UI may spell 模板),
   find the exact existing `answereq` template card and apply it. Do not create or
   modify a template and do not silently substitute hand-entered tags.
2. Read back the applied template and actual topic chips/text. A click returning
   without an error is not evidence of application. Missing template/control is
   a concrete compatibility blocker, not permission to publish without it.
3. Enable 原创声明 and inspect its real resulting state. Optional 内容声明 remains
   unselected. If the platform requires a truthful declaration, assess the actual
   content and complete it truthfully within authority; otherwise stop for the
   missing decision. Never choose a false label to pass a validation gate.
4. Read back the exact reviewed title/body, topic template, topics, original
   switch, optional declaration state and Publish enabled state. Record them with
   `record_cloud_publication_preflight` under the current claim. Final click uses
   `reserve_cloud_browser_step` with `kind=click,purpose=submit_publication` and
   requires that fresh bound observation. Normal scheduling, identity, review,
   media and unknown-result guards remain in force.

This is an assistant-executed CUA workflow. The local report validates consistency
and freshness; it cannot independently verify the browser or identify a disguised
final click. The host must accurately label actions and obey the tool boundary.
No new upload, draft or final publication was performed to test these controls in
this patch. The actual template branch remains unverified until the next
specifically prepared authorized post exposes it.

### Local evidence fields

Future profile opt-in is the exact object:
`future_content_policy={version:1,posts:6,comments:50,exact_person_covers:3,post_cta:<above exact text>,topic_template:'answereq',is_original:true,optional_content_declaration:'none'}`.
Keep actual provenance in `confirmation_sources.future_content_policy` and the
same CTA/count in `content_requirements` (`min_person_covers=3`,
`exact_person_covers=3`). Configure via `manage_operation_run configure`, which
pins pre-existing runs before changing the default; do not rewrite old run data.

Final `learning_review` records reviewer/review_source, `content_type`
(educational/self_introduction/other), title and cover_text objects each with exact
text, mechanism, mechanism_explanation, promise, and payoff {excerpt,explanation}.
Educational content adds worked_example {problem,steps,result} excerpt/explanation
objects plus completeness_check; copyable {kind,excerpt,explanation,
usage_instructions}. Self-introduction adds self_introduction {fill_in_blank,
filled_example,substitution_check}. “Other” requires a substantive actual
noneducational_reason. Misclassifying instructional content is not a supported
escape from the requirements. Structural/excerpt checks do not replace reading.

Cover history CLI helpers are `record_cloud_cover_history`,
`list_cloud_cover_history`, `assess_cloud_cover_visuals`,
`record_cloud_cover_visual_review`. Use `reference_person='sha256:'+` the pinned
profile reference image hash. For each of three person candidates supply sorted
`peer_image_paths` of the OTHER TWO. Assess first to obtain candidate pixel hash,
history digest and comparison entry IDs, then actually inspect images and record
`visual_review`: visual_reviewed=true, reviewer, evidence_ref,
candidate_pixel_sha256, history_digest, reference_person,
same_reference_person=true, reference_comparison and comparisons. Qualitative
keys are pose, facial_expression, clothes, composition; each comparison names
entry_id and changed_dimensions. Near matches also need near_match_override
with actual evidence. Attach returned review_id to final.cover_review as
`visual_history_review_id`. These are host observations, not fabricated user
approvals. All three are checked together against their real assets.

Record newly successful scheduled/published covers with their actual receipts.
Do not label a scheduled post published on elapsed time alone. Recheck remaining
unsubmitted covers when history changes; already dispatched same-run cover
fingerprints retain their original valid review, and unknown dispatches cannot
be replayed. Do not “fix” a historical successful post to satisfy new rules.

## Batch handoff and comment accounting

An explicit successor_of may create one new independent production run only after
all of its predecessor's planned posts have actual successful bound publication/
scheduling receipts. The predecessor's unfinished comments and unknowns remain
open. Repeating the same successor request returns the same new run, not another
six posts. Browser work is still serialized at the actual account boundary.

Count fifty daily comments/replies across ALL runs by Asia/Shanghai calendar day
from real receipts. A prior cross-day task's cumulative twelve successes is not
twelve comments today. Show cumulative and today's confirmed/pending counts
separately. New run creation never resets author dedup, pacing or daily counts.
Unknown and pending submissions reserve capacity conservatively; they are never
reported successful. No count snapshot substitutes for the global live ledger.

### Planning evidence is not platform acceptance

The historical field `platform_earliest` is the planner's lower bound, not an
independent certification of the earliest accepted platform instant. When UI
only shows a default value, use `lower_bound_kind:conservative_candidate` and
`planning_lower_bound_ref` describing the actual default evidence and why the
later proposed candidate is conservative. Do not label that candidate a proven
platform minimum. Likewise, an enabled last calendar date may be represented
with `upper_bound_kind:calendar_date_window` and `planning_upper_bound_ref`;
this does not prove every time on that date is accepted. The evidence object is
retained with the plan. Every final scheduled Publish preflight must include
`visible_schedule_at` exactly matching the sealed timestamp (normalize the
actually observed date/time with its known timezone) and
`schedule_error_absent:true` from actual UI inspection. Any field error blocks
Publish; revise only still-unsubmitted reservations and re-review exact content.

Future slots share a six-post Asia/Shanghai calendar-day capacity across runs.
Carryover slots consume the next day's capacity, so a later six-post batch may
span more than one day instead of adding six on top of two carried posts.
All reservations, including submitted/unknown records, remain counted unless
explicitly cancelled/failed; no clock-based assumption that a slot published.
Only the already recorded original-first-batch overlap exception is excluded.
The planner keeps 09:00–23:00 and >=90-minute spacing, skips occupied capacity,
and the reservation transaction rechecks the shared daily cap. Old first-batch
appointments and records are not modified.

### Same-editor renewal before any final submission

A long real editing session can use `renew_cloud_unsubmitted_publication_claim`
with the same action, claim token and attempt, including after lease expiry.
It does not require manufacturing a denied Publish click. The new observation
variant omits `denied_step_id` and supplies `mode: retained_editor`, the usual
`expected_account`, `browser_context`, `call_ended: true`,
`final_submit_invoked: false`, fresh `observed_at` and `evidence_ref`, plus:
`visible_title`, `visible_body`, `uploaded_media_match: true`,
`media_evidence_ref`, `editor_retained: true`, `target_schedule_at` (sealed target),
and `visible_schedule_at` (actual current field, null if not set yet).
All browser calls must have ended and their steps resolved. Title/body, media,
account/context and target must match; current authorization, identity and full
batch review are rechecked. Actual editor content may have been restored from a
platform draft: preserve that real history rather than assert no side effects.
Topic chips are separately observed: reconstruct actual paragraph text excluding
only actual topic elements, preserving paragraph breaks; never substitute the
sealed body or loosely normalize a mismatch. This is host observation evidence,
not independent platform verification.

Once a final-submit step permit has been issued the retained-editor variant is
closed, even if the host now claims it did not click. Use the existing explicitly
verified denied-click path where applicable; unknown final submissions remain
unknown and must never be resent. Renewal neither uploads again nor changes any
reviewed payload. Actual date/time and all required visible settings still need
fresh final preflight. An absent Original control cannot be reported checked.

For an expired claim/identity, first reserve `read_observation` on the same
attempt with the usual `recovery_evidence` fields (`call_ended`, matching
`expected_account`, `evidence_ref`, `recovery_reason`, `user_source`) plus
`retained_editor: true`, `final_submit_invoked: false`. This is only a passive
DOM/screenshot observation after a completed upload and fully resolved browser
calls; no navigation/input/upload is admitted. It does not clear blocks or
refresh identity automatically. Record the actual newly observed identity and
editor, then renew using the observation above. No denied/unknown step needs
to be invented. A previously issued final-submit permit closes this route.

### Explicit later publication-settings revisions

A user's later settings instruction may version the current **unsubmitted**
six-post run and future defaults without changing prior published/scheduled runs
or rewriting genuine source, draft, editing, visual-review or media evidence.
`revise_cloud_publication_batch_settings` takes `run_id`, exact `settings`
(`is_original: false`, `group_chat`, `activity`, both actual observed names),
`user_source`, `user_quote`, and `evidence_ref`. No guessed group/activity IDs.
Reserve a new feasible six-slot plan with the existing schedule replacement
interface, register revised final settings only, and re-review the full batch.
Retain the exact original body/CTA and bytes; platform topic chips added by the
selected template or activity are observed separately from the real body.

For a retained already-uploaded editor, first use
`preview_cloud_publication_settings_revision(operation_id,payload,user_source)`
to obtain the reviewed new binding digest. Then
`revise_cloud_publication_settings(operation_id,claim_token,payload,attestation,observation)`
records new explicit host authorization for that digest and an append-only
settings revision. The original binding and evidence remain immutable. This is
not permission to resend an unknown final submission or to replace media.
Any final-submit permit or unresolved browser call prevents this path. Host
must refresh actual identity/editor observation and use the existing renewal
flow where necessary. New selected group/activity must exactly match final
preflight readback; original=false must be truthfully reported false. Existing
answereq template, date/time, body, image, account and declaration checks remain.

### Read back a submitted publication after lease expiry

A completed final Publish step is never an unsubmitted editor. Do not renew it
with `final_submit_invoked: false`. Use the same action/token/attempt and
`recovery_evidence` with the usual account, ended-call, source and evidence
fields, plus `post_submit_readback: true`, `final_submit_invoked: true`,
`final_step_id` (the actual completed, persistently permitted Publish click),
and the same `browser_context`. Passive `read_observation` can refresh identity
and inspect the actual result without reviving any write lease.

Exactly one native navigation to an **actually observed** management menu is
also allowed: kind `navigation`, purpose `verify_publication_result`,
`verification_step_id` equal to `final_step_id`, target equal to evidence
`observed_management_target`, with `management_target_evidence_ref`. A native
click on that real menu is navigation for this permit, not a generic editing
click. No guessed URL, input, upload, search, repeated final click or arbitrary
navigation is permitted. Any unfinished call blocks it; an active platform
block prevents navigation and remains recorded. Subsequent passive DOM or
screenshots retain the same binding. Persist actual receipt evidence; a
completed click or cleared editor alone never proves the requested appointment.
