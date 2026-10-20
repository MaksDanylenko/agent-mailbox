---
name: mailbox-teammate
description: Use when receiving an Agent Mailbox teammate startup packet, handling an addressed task, exchanging questions or results, or continuing the original task from a versioned checkpoint.
---

# Work as a mailbox teammate

Read the startup packet: absolute CLI/database paths, run ID, your unique session ID, leader ID, current assignment/task/thread IDs, original task ID, skill path, allowed artifact references, and optional checkpoint path/version. Bind these as shell variables in each shell that needs them: `MAILBOX_CLI`, `MAILBOX_DB`, `RUN_ID`, `AGENT_ID`, `LEAD_ID`, `TASK_ID`. Values do not automatically carry over from a leader's process. If required routing data is absent, ask the leader through the existing conversation before guessing.

Use the CLI for every database operation. Register/verify your membership:

```sh
"$MAILBOX_CLI" --db "$MAILBOX_DB" --run "$RUN_ID" --as "$AGENT_ID" \
  register --role teammate --lead "$LEAD_ID"
```

## Accept delivery

Claim the initial task with `claim --id "$TASK_ID"`; read the returned `data.message` and its fresh `lease_token`. Verify the body/thread against the startup packet. `TASK_ID` is this current addressed assignment; `original_task_message_id` is a traceability reference, not an old message to claim. Send an idempotent receipt (type `message`, `reply` to the task, key derived from task ID and receipt purpose), then ack with the claimed ID/token. Ack confirms acceptance, not completed work. Default lease is 120 seconds; do not hold it throughout the task or while waiting on a person.

Read the current assignment and permitted task artifacts. For a successor, also read the provided checkpoint version and continue its next action; you inherit neither the old session's context nor its inbox. Artifact references define work scope, not OS-enforced permissions.

## Ask and report

Write UTF-8 message text to a file or pass stdin as data. Bind `SEND_KEY` to a stable identifier for this logical question:

```sh
"$MAILBOX_CLI" --db "$MAILBOX_DB" --run "$RUN_ID" --as "$AGENT_ID" \
  reply --to "$LEAD_ID" --reply-to "$TASK_ID" --type question \
  --send-key "$SEND_KEY" --body-file question.txt
```

For a result use the same pattern with type `result`, its own key and result body. Include evidence and remaining blockers. Questions/results reply to your current addressed assignment; include the original task reference in their text when resuming. The leader routes human questions and sends `answer` replying to the question ID. Use explicit teammate addresses for peer discussion within the run; send product decisions to your leader.

## Poll and recover

`inbox` returns `data.messages`; `wait --timeout 20` returns available `data.message` without reserving it. `--timeout` accepts integer seconds 1..30; a larger value is rejected with exit 2, so cover a longer budget by repeating short waits. Claim/ack incoming answers before continuing. Empty inbox/claim and wait timeout exit 5 are ordinary. Limit repeated waits to the assigned work budget. No CLI operation wakes a completed session.

After an uncertain send, repeat its same key and exact input. If lease/ack conflicts, use status and a new claim when available; the old token cannot confirm a replacement lease. Duplicate delivery is possible. Do not blindly repeat external effects.

Periodically save a versioned checkpoint with decisions, progress, next action, artifacts, code state, tests, dead ends and blockers; send its path/version to the leader. Self-messages are allowed notes for your own address, not automatic reassignment. Ask the leader to coordinate a successor with headroom when context is constrained; no universal 200K threshold exists. There is no separate handoff document/skill. On reassignment, stop work so two sessions do not execute the same task concurrently; message leases do not lock task execution.
