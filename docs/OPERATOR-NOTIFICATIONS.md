# Proactive operational notification decisions

`CloudNotifications` is a durable decision ledger, not a sender. It applies ONLY
to proactive/automatic operational updates, not immediate answers to a user's
explicit question. It never changes pause, permissions, or automation settings.

Use one shared action state directory and exact configured `expected_user_id` for
all runs and aliases. `decide(event, kind, evidence_ref, urgent=False,
requests_resume=False, urgency_evidence_ref=None)` accepts only `blocker`, `decision`, or `completed`.
The host must verify that the event is substantive, genuinely needs a decision,
or is actually complete; a function return or a draft is not public delivery.
There is deliberately no progress/heartbeat notification type.

`event` has exactly `target_id`, `reason_code`, and `state_version`: a stable
subject, reason, and substantive state. The module generates the fingerprint
from canonical sorted `kind + event`; callers cannot supply a different hash,
run ID or timestamp field. Reordering fields or changing evidence refs does not
create a new event. The host must preserve stable meaning: changing state_version
merely because a new run started is forbidden. Real material changes can change
the state version. Arbitrarily rewritten identifiers cannot be proved semantically
equivalent by this module; this is structural deduplication, not omniscient NLP.

Urgency requires a separate nonempty `urgency_evidence_ref` plus host verification
of genuine urgency. A caller boolean or reference alone is NOT proof or permission
to disturb the user. The host must verify the referenced evidence before invoking
the urgent path; the module does not independently observe the real world.

Between 22:00 and 09:00 Asia/Shanghai, nonurgent notifications are held until
09:00. During explicit user pause, nonurgent blocker/decision/resume requests are
suppressed, not sent as recurring scan/restart nags. Genuine urgent events bypass
quiet hours and pause suppression. Actual completion still follows quiet hours.
A pending held event can be upgraded to urgent with new evidence; consumed events
are never repeated just because a later run changes urgency.

`pending(limit=100)` rediscovers held/suppressed pending records after restart,
returning current decisions without consuming them; `truncated` means more remain.
`get(notification_id)` re-evaluates time/pause without dispatch. Immediately
before an actual external send, call `consume(notification_id)`. Only the first
return with `send_once=true` allows the host to consider a single send, subject
to its actual permissions. The ledger commits `unknown` BEFORE this return, so
lost responses or crashes suppress repeats. `record_result(notification_id,
status, evidence_ref)` accepts `sent` or `unknown`; only actual delivery evidence
supports `sent`. A read/decision alone never marks delivery. There is no resend
or pending-reset API. A consumed-but-unsent notice may therefore be omitted; this
is intentional, safer than sending duplicate messages after uncertainty.

Limit: this module cannot observe arbitrary host messages. Every automatic send
must use the same ledger and canonical stable event for cross-run deduplication.
Different state directories cannot coordinate. Actual message integration and
all delivery/urgency evidence remain the host's responsibility.

`message_sent=false` means this module invocation did not send anything. The
separate `status=sent` means the host previously recorded delivery evidence.
