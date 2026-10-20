# Agent Mailbox: implementation specification

Date: 2026-10-20. Scope: standalone public project, unrelated to the current repository. The owner explicitly authorized creating and publishing the plugin. This document specifies the first implementation; it does not claim that code, installation, or publication exists.

## Outcome

Codex and Claude Code use the same local SQLite mailbox through a Python CLI and packaged skills. Independent top-level tasks use separate `run_id` values in one database. Leaders speak with the human; teammates exchange questions, answers, tasks, and results. Self-messaging is allowed.

Python >=3.10; runtime uses only the standard library. Executable entrypoint: `bin/agent-mailbox`. No MCP, remote service, daemon, automatic agent launching, or wakeup mechanism. Public documentation, CLI messages, skill text, and plugin metadata are English. Source filenames and examples are generic; no private company references, paths, secrets, or existing project content. License: MIT (practical default for the authorized public project).

## Identity and schema

- `runs(id PRIMARY KEY, label, created_at)`.
- `agents(id PRIMARY KEY, client, created_at)`: a unique session identity, not a model name. `client` is an optional informational string; omitted values remain null.
- `memberships(run_id, agent_id, role, lead_agent_id, PRIMARY KEY(run_id,agent_id))`; role is `lead|teammate`. Exactly one lead per run, enforced by partial unique index; teammates reference that lead within the same run. The lead has no parent. Agent and run foreign keys are enabled. An agent can join multiple runs.
- `threads(run_id,id,created_at, PRIMARY KEY(run_id,id))`.
- `messages(run_id,id,thread_id,sender_id,recipient_id,reply_to,type,body,send_key,created_at,state,lease_token,lease_until,attempts,acked_at, PRIMARY KEY(run_id,id))`.

Message types: `task|message|question|answer|result`. States: `pending|leased|acked`. IDs are generated opaque strings except explicit agent/run IDs. Public timestamps `created_at`, `lease_until`, and `acked_at` are integer UTC epoch milliseconds, nullable where absent. Message order is `(created_at,id)` ascending.

Composite foreign keys bind thread, sender membership, recipient membership, and reply parent to the same run. Reply parent also belongs to the same thread, using `UNIQUE(run_id,thread_id,id)` and a corresponding composite FK. `UNIQUE(run_id,sender_id,send_key)` implements idempotent sending. Index inbox on `(run_id,recipient_id,state,created_at,id)` and expired leases on `(run_id,recipient_id,state,lease_until)`.

Role does not imply recipient: every message is addressed explicitly. Inbox/claim/wait use both run and recipient; status-by-ID allows only the sender or recipient in the selected run. These boundaries prevent mistakes in a trusted local environment, not malicious direct database access.

## Complete CLI contract

Global options precede the command: `agent-mailbox --db ABSOLUTE_PATH --run RUN_ID --as AGENT_ID COMMAND`. They are required except for help/version. No environment-variable identity inference. No plugin install-directory state. Parent directories must already exist; missing parent/path failures return exit 1. All commands except `run-create` require an existing supported database/schema and run; missing database/run returns exit 3 without creating a file. Unknown newer schema returns exit 1 without mutation. Schema version is recorded with `PRAGMA user_version=1`.

- `run-create --label LABEL [--client CLIENT]`: atomically initialize schema if absent, register the caller agent if needed, create the run, and register the caller as its lead. Repeat with identical run label/lead is successful; conflicting label/lead/client returns conflict. If the optional client is omitted, an existing client is not changed.
- `register --role lead|teammate [--lead AGENT_ID] [--client CLIENT]`: join the selected run. A teammate must provide its existing lead ID. Lead registration is only an idempotent check of the run creator. Conflicting membership/client returns conflict; omission of client does not change it. New agent registration and membership insertion are atomic.
- `send --to AGENT_ID --type TYPE --send-key KEY [--thread THREAD_ID] [--body-file PATH]`: create a message. Both participants must be members. Without thread, create one. With thread, it must exist in the selected run. Body file defaults to `-` (UTF-8 stdin); preserve its text exactly, reject invalid UTF-8 as exit 2. Empty text is allowed.
- `reply --to AGENT_ID --reply-to MESSAGE_ID --type TYPE --send-key KEY [--body-file PATH]`: parent must be addressed to caller; explicit recipient must match parent's sender. Inherit thread and set reply_to. Self-reply is allowed. Same body handling as send.
- `inbox [--limit N]`: list available incoming messages with full body; default 20, range 1..100. Includes pending and expired leased messages, excludes live leases and acked. Does not reserve. Return `data.messages` array.
- `claim [--id MESSAGE_ID] [--lease-seconds N]`: reserve one available incoming message; default oldest. Lease default 120 seconds, integer range 1..3600. Return `data.message` containing all message fields including token/deadline/attempts. Empty untargeted claim returns `data.message:null`; missing/foreign targeted ID returns exit 3; targeted unavailable message returns exit 4.
- `ack --id MESSAGE_ID --lease-token TOKEN`: require incoming ownership and current unexpired token. Return `data.message`; repeated ack with the accepted token is success even later. Retain the accepted token on acked messages for this purpose. Wrong, expired, or superseded token returns exit 4 without changing state; missing/foreign ID returns exit 3.
- `status [--id MESSAGE_ID]`: by ID returns `data.message` for caller's incoming/outgoing messages. Without ID returns `data.counts` of incoming available/live-leased/acked messages; self-messages count once. Availability includes expired leases without mutating them.
- `wait --timeout N`: integer 1..30 seconds, poll incoming availability about once per second. Return first available message as `data.message`, without claiming, or exit 5 on timeout. Cancellation via SIGINT returns exit 130. No transaction remains open between polls. Database lock waiting must not exceed the remaining timeout budget.

Successful calls return exactly one JSON object to stdout: `{"schema_version":1,"ok":true,"data":{...}}`. Send/reply return `data.message`. Run-create returns `data.run` and `data.membership`; register returns `data.membership`. Public message objects contain `id`, `run_id`, `thread_id`, `sender_id`, `recipient_id`, `reply_to` (null or string), `type`, `body`, `send_key`, `created_at`, `state`, `lease_token` (null or string), `lease_until` (null or integer epoch milliseconds), `attempts` (integer, initially 0), and `acked_at` (null or integer epoch milliseconds). Initial pending messages have null token/deadline/ack time. Claim increments attempts and assigns token/deadline. Ack retains its accepted token, clears lease_until to null and sets acked_at. Without ID, status returns `data.counts={"available":N,"leased":N,"acked":N}` for incoming messages, with leased counting only live leases. Run objects contain `id`, `label`, and `created_at`; membership objects contain `run_id`, `agent_id`, `role`, and nullable `lead_agent_id`. Errors return one JSON envelope: `{"schema_version":1,"ok":false,"error":{"code":"stable_machine_code","message":"English text"}}`. Diagnostics go to stderr, without automatically echoing message bodies. No tracebacks in normal CLI failures. Help/version are the explicit non-JSON exceptions.

Exit codes: 0 success (including empty inbox/claim); 1 storage/schema/unexpected failure; 2 invalid invocation or body; 3 object not found; 4 conflict; 5 wait timeout; 6 SQLite busy timeout; 130 SIGINT. Argument parser errors follow JSON/exit-2 contract. Stable error codes include `invalid_input`, `not_found`, `conflict`, `timeout`, `busy`, `storage_error`, `interrupted`.

## Durability and retry

Local disk only, SQLite WAL, foreign_keys ON, synchronous FULL, busy timeout 5000ms. Short write transactions; one writer at a time. All SQL values use parameter binding, never raw string substitution. Claim uses BEGIN IMMEDIATE and atomically checks availability, changes token/deadline, increments attempts, and returns the message. Message creation and idempotency check occur in the same transaction.

Same send key + identical input returns original message. Different body/type/recipient/thread/reply input is conflict. For omitted auto-generated thread, check the original request intent before generating another thread; persist a small request fingerprint or equivalent request metadata to distinguish omitted versus explicit thread consistently.

Delivery is at-least-once if recipients keep polling. Expired leases are logically available even if stored state remains leased until another claim. Tokens prevent stale acknowledgment. No automatic deletion/dead-lettering. No exactly-once external side-effect guarantee. No task-wide execution lock.

## Skills and startup

Package `mailbox-setup`, `mailbox-lead`, `mailbox-teammate`. The core and workflow are shared; packaging metadata may differ. Modern Codex entrypoint is root plugin.json; provide .claude-plugin/plugin.json for Claude Code. Bundled CLI is executable. Claude Code's bin directory integration must not be assumed portable to Codex: startup packet includes resolved absolute CLI path, and skills use it. Python availability/setup is explicit; plugin install is not claimed to install interpreters. Plugin artifacts can include both manifests with shared skills and core, then validate each host separately.

Leader/launcher passes: CLI path, db path, run ID, session agent ID, lead ID, original task message ID, thread ID, skill references, allowed artifact references, and checkpoint reference/version if resuming. This first prompt comes from launcher/leader, not the mailbox. Teammate verifies/registers membership, claims task, sends idempotent receipt (type message), then acks acceptance and performs work. Questions/results reply to original task; human answers reply to the question. Ack is delivery/acceptance, not task completion. Reply IDs and send keys come from actual JSON responses, not guessed values.

Long tasks retain original task identity across new session IDs. Periodic versioned checkpoints record progress, decisions, next action, artifacts, code state, tests, dead ends, and blockers. No separate handoff document or skill. Leader stops old work before assigning a successor, sends successor a new message referencing original task and checkpoint/version, and never edits historical senders or recipients. No guaranteed trigger at 200K tokens; use available context signals and headroom. Without an explicit launcher integration, successor creation remains a leader/user action. Acked task recovery is coordinated manually; a crash after ack does not imply automatic execution recovery.

## Acceptance

Independent black-box unittest tests execute the real CLI against temporary databases: JSON/errors; two isolated runs; self-send; multi-run membership; foreign reply/recipient rejection; quoting, Unicode and multiline bodies; retry equivalence/conflict; lease concurrency with two subprocesses; expiry and stale ack; repeat accepted ack; bounded wait/cancellation; busy database behavior; restart durability; malformed invocations; no database created by read commands; newer schema protection. Test assertions use public contract, not core internals.

Public README contains install/setup, both hosts, startup packet, commands, receipt vs result, crash/retry behavior, run isolation, checkpoints, and limits. CI runs unittest and packaging/skill checks on Python 3.10 and 3.13. A subprocess demo proves complete task → question → answer → result routing; documentation distinguishes this from validation with two live AI products. Native plugin checks and real Codex/Claude conversations are performed when those tools are available; unavailable checks are reported honestly.

## Self-review

One run has one lead; roles belong to memberships. No alias silently changes recipient. Task execution and message delivery are separate. SQLite transactions never span polling or AI work. Idempotent send covers generated threads. Two-host packaging has explicit path/runtime setup. No confidential project data is needed. Publication is authorized; namespace selection and actual checks are reported by the implementing lead.
