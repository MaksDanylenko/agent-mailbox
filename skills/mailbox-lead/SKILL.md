---
name: mailbox-lead
description: Use when acting as the leader of an Agent Mailbox run, delegating tasks to addressed coding sessions, returning human answers, or reassigning work from a checkpoint.
---

# Lead an Agent Mailbox run

Use the absolute CLI/database paths and your run/session address from setup. Bind shell variables anew when needed: `MAILBOX_CLI`, `MAILBOX_DB`, `RUN_ID`, `AGENT_ID`. Scope every call with all four values. Use CLI operations, not raw SQL. Read JSON `data.message` / `data.messages`; errors carry `error.code` and a nonzero exit.

## Delegate

Create the run with `run-create --label LABEL` if requested and not already initialized. Assign each intended teammate a unique session ID; register that address with `register --role teammate --lead LEAD_ID` using `--as TEAMMATE_ID` before the first send. Send a `task` with explicit `--to`, stable `--send-key`, and UTF-8 body file or stdin.

Record returned task ID and thread ID. Give the teammate its first prompt through an available launcher with the CLI/database paths, run ID, its own and leader IDs, task/thread IDs, skill path and allowed artifacts. A database message cannot bootstrap an unstarted process.

## Receive and route

Use `inbox`, then `claim --id MESSAGE_ID`; alternatively `wait --timeout 20` signals availability without reserving it. Read the claim's current lease token. After accepting or routing the message, `ack --id MESSAGE_ID --lease-token TOKEN`. Default lease is 120 seconds for delivery processing, not an entire task or human conversation. Acknowledge a question after routing it to your chat, before waiting on the human.

Present product questions in your current human conversation. Return the person's actual answer to the question sender with its ID as parent. Bind `QUESTION_ID`, `TEAMMATE_ID`, and `SEND_KEY` from that message and this logical response:

```sh
"$MAILBOX_CLI" --db "$MAILBOX_DB" --run "$RUN_ID" --as "$AGENT_ID" \
  reply --to "$TEAMMATE_ID" --reply-to "$QUESTION_ID" --type answer \
  --send-key "$SEND_KEY" --body-file answer.txt
```

The CLI inherits the thread. Report a task complete only after evaluating its separate `result`; receipt messages and ack confirm acceptance only. Continue polling within the current task's agreed budget; timeout exit 5 is not message loss. Do not start an unbounded loop.

## Retry and replace

Repeat a logical send with the same key and identical input after uncertain output; changed input needs a new key. If ack reports conflict, read current status and reclaim only if available; never substitute a stale token. Delivery may repeat; external effects are not exactly-once.

For long tasks, keep the original task plus a versioned checkpoint artifact. Teammates send checkpoint path/version to you. Stop old work before explicitly launching a successor with a new session ID. Register it and send a newly addressed task using `send --thread ORIGINAL_THREAD_ID`, with original task and checkpoint references in its body. Its startup `task_message_id` is the new assignment ID; `original_task_message_id` keeps the original ID. The successor claims and replies to its new assignment, not the old task addressed to another session. Do not edit old recipients/history or expect self-messages in the old inbox to migrate. No separate handoff document/skill or automatic 200K-token trigger is required. The mailbox has no task-wide execution lock or automatic wakeup.
