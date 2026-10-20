# Agent Mailbox: messaging plugin for Codex and Claude Code

[![CI](https://github.com/MaksDanylenko/agent-mailbox/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/MaksDanylenko/agent-mailbox/actions/workflows/ci.yml) · [MIT license](LICENSE) · Python 3.10+

Agent Mailbox is an open-source plugin for local agent-to-agent messaging between **Codex and Claude Code**. A shared SQLite mailbox, Python CLI, and coordination skills let sessions exchange tasks, questions, answers, and results on the same computer.

Keep your conversation with a team lead while teammates use addressed inboxes to ask questions and return work. Either product can be the lead. No MCP server or hosted mailbox service is required; agents receive messages when they check their inbox.

[Quickstart](#quickstart) · [Install the plugin](#install-the-skills) · [Start a team](#start-a-team) · [CLI reference](#cli-reference) · [FAQ](#frequently-asked-questions) · [Architecture](docs/architecture.md)

## What you can use it for

- **Coordinate Codex and Claude Code teammates:** delegate a task, route a question to the human through the lead, and return the answer in the same conversation thread.
- **Run independent teams:** give each top-level task its own run and lead in one database. Every session has its own address; run boundaries keep mailbox queries separate.
- **Continue long tasks:** give a replacement session a newly addressed assignment with the original task reference and a versioned checkpoint. See [session replacement](#replace-a-session-from-a-checkpoint).
- **Recover unacknowledged messages:** retry sends with stable keys and reclaim delivery after a lease expires. See [retry and recovery](#retry-and-recovery) for the limits.

## Requirements

Python 3.10 or newer, Git, and a local filesystem. The runtime has no third-party Python dependencies. Both agent processes must be able to read/write the selected database and access the CLI. Plugin installation does not install Python or start agents.

## Quickstart

```sh
git clone https://github.com/MaksDanylenko/agent-mailbox.git
cd agent-mailbox
python3 --version
./bin/agent-mailbox --help
```

Try the local messaging demo from the checkout:

```sh
python3 scripts/demo.py
```

It sends a task, receipt, question, simulated human answer, and result through the real CLI. It makes no model calls and does not launch Codex or Claude agents. To connect your own sessions, [install the skills](#install-the-skills), then [create a run and send a task](#start-a-team).

Keep this checkout at a stable location and pass its absolute `bin/agent-mailbox` path to agents. No pip install is required. If an archive or plugin cache loses executable permissions, invoke it as `python3 /absolute/path/agent-mailbox/bin/agent-mailbox`; the Git checkout preserves its executable bit.

## Install the skills

Codex and Claude Code use the same skills and core with their own package metadata. Installation commands below refer to the checkout directory. Use current native CLI versions with plugin support; start a new session after installation.

### Codex plugin

```sh
codex plugin marketplace add /absolute/path/agent-mailbox
codex plugin add agent-mailbox@agent-mailbox-local
```

Then invoke `$mailbox-setup`, `$mailbox-lead`, or `$mailbox-teammate` from the installed plugin's skill picker. If the host displays a namespaced name, use that displayed name.

### Claude Code plugin

```sh
claude plugin marketplace add /absolute/path/agent-mailbox
claude plugin install agent-mailbox@agent-mailbox-local
```

Invoke `/agent-mailbox:mailbox-setup`, `/agent-mailbox:mailbox-lead`, or `/agent-mailbox:mailbox-teammate`. For a session-only local preview: `claude --plugin-dir /absolute/path/agent-mailbox`.

The catalogs are third-party/local sources, not listings in either provider's public directory. If a host version lacks a command above, use its native plugin browser or upgrade that host; the standalone CLI still works. Native setup details: [OpenAI packaging](https://developers.openai.com/plugins/build/plugins), [Claude marketplaces](https://code.claude.com/docs/en/plugin-marketplaces).

Use a database directory outside the checkout and plugin caches. Installed copies may move on updates. In the examples below, replace the paths and export the variables in each new shell that needs them:

```sh
export MAILBOX_CLI="/absolute/path/agent-mailbox/bin/agent-mailbox"
export MAILBOX_DB="/absolute/path/team-state/mailbox.db"
export MAILBOX_RUN="website-review"
mkdir -p "$(dirname "$MAILBOX_DB")"
```

## Start a team

Create an independent run and its leader:

```sh
"$MAILBOX_CLI" --db "$MAILBOX_DB" --run "$MAILBOX_RUN" \
  --as codex-session-17 run-create --label "Review the website" --client codex
```

Register the teammate:

```sh
"$MAILBOX_CLI" --db "$MAILBOX_DB" --run "$MAILBOX_RUN" \
  --as claude-session-82 register --role teammate --lead codex-session-17 --client claude
```

Use an actual unique session ID, not just `codex` or `claude`. Names in this guide are examples. A second task uses another `--run` value and its own leader. Membership in several runs does not create separate agent contexts.

Send the task, using UTF-8 stdin or `--body-file /path/to/task.txt`:

```sh
"$MAILBOX_CLI" --db "$MAILBOX_DB" --run "$MAILBOX_RUN" \
  --as codex-session-17 send --to claude-session-82 --type task \
  --send-key website-review-task-1 <<'MESSAGE'
Review the website changes. Ask me if a product decision is needed.
MESSAGE
```

Save `data.message.id` and `data.message.thread_id` from the returned JSON. The leader or launcher now passes the teammate a startup packet like [examples/startup-teammate.json](examples/startup-teammate.json), filled with actual paths and returned IDs. The first prompt supplies that packet and the teammate skill. The mailbox cannot deliver a first prompt to an unstarted process.

## Read, acknowledge, and reply

Commands below assume `TASK_ID` and `LEASE_TOKEN` have been set from actual JSON responses. Shell variables do not carry over automatically to another agent or shell.

```sh
"$MAILBOX_CLI" --db "$MAILBOX_DB" --run "$MAILBOX_RUN" \
  --as claude-session-82 claim --id "$TASK_ID"
```

Claim returns `data.message` with a fresh `lease_token`. Send the leader an idempotent receipt before acknowledging acceptance:

```sh
"$MAILBOX_CLI" --db "$MAILBOX_DB" --run "$MAILBOX_RUN" \
  --as claude-session-82 reply --to codex-session-17 --reply-to "$TASK_ID" \
  --type message --send-key "$TASK_ID-receipt" <<'MESSAGE'
Task accepted. I am starting the review.
MESSAGE

"$MAILBOX_CLI" --db "$MAILBOX_DB" --run "$MAILBOX_RUN" \
  --as claude-session-82 ack --id "$TASK_ID" --lease-token "$LEASE_TOKEN"
```

An ack means the task message was accepted. It is not a finished task. Send questions and results separately with `reply --type question` and `reply --type result`, their own stable send keys, and the current addressed assignment ID (`task_message_id`). The leader claims and acknowledges a question, asks the human in its current chat, and sends `reply --type answer --reply-to QUESTION_ID` to the teammate. Both sides preserve the thread automatically.

## CLI reference

All mailbox commands require `--db ABSOLUTE_PATH --run RUN_ID --as AGENT_ID` before the command. `--help` and `--version` are human-readable exceptions; other success and error output is one JSON object on stdout.

| Command | Options / result |
|---|---|
| `run-create` | `--label LABEL [--client CLIENT]`; initializes database/run and caller as leader |
| `register` | `--role lead\|teammate [--lead ID] [--client CLIENT]`; joins selected run |
| `send` | `--to ID --type TYPE --send-key KEY [--thread ID] [--body-file PATH]`; stdin by default |
| `reply` | `--to ID --reply-to ID --type TYPE --send-key KEY [--body-file PATH]`; same thread, recipient is parent's sender |
| `inbox` | `[--limit 1..100]`; `data.messages`, available incoming mail, no reservation |
| `claim` | `[--id ID] [--lease-seconds 1..3600]`; `data.message`, default lease 120 seconds |
| `ack` | `--id ID --lease-token TOKEN`; confirms incoming message acceptance |
| `status` | `[--id ID]`; incoming counts, or an accessible incoming/outgoing message |
| `wait` | `--timeout 1..30`; available message without claiming, or timeout |

Types: `task`, `message`, `question`, `answer`, `result`. Self-messages are allowed. A reply can only refer to mail addressed to the caller; its `--to` must equal the parent's sender. Inbox and wait never acknowledge or reserve messages.

```json
{"schema_version":1,"ok":true,"data":{"messages":[]}}
```

```json
{"schema_version":1,"ok":false,"error":{"code":"not_found","message":"Message not found"}}
```

Exit codes: `0` success (including empty inbox/claim), `1` storage/schema/unexpected failure, `2` invalid input, `3` not found, `4` conflict, `5` wait timeout, `6` database busy timeout, `130` SIGINT. Read [the complete contract](docs/design.md) for JSON fields and conflict behavior.

## Retry and recovery

- Reuse a send key for the same logical request and identical content. The key is scoped by run and sender. A different request with that key is a conflict.
- Claim reserves message delivery. If a process crashes before ack, the message becomes available after its lease expires. A new claim replaces the token; old or expired tokens cannot ack it. Repeating an already successful ack with its accepted token is safe.
- Delivery is at-least-once while recipients keep checking; external effects can be repeated. Use message IDs and stable keys for deduplication. No SQL or shell interpolation of message content is needed.
- After a task has been accepted/acked, recovering unfinished work is a leader action. This package has no task-wide execution lock. Stop the old teammate before assigning a successor.

Long tasks use their original task plus a versioned checkpoint artifact, not a separate handoff document or skill. Send its path and version to the leader. A successor gets a new session address and a newly addressed task referring to the original task/checkpoint. The leader uses `send --thread ORIGINAL_THREAD_ID`; startup `task_message_id` is the new assignment to claim and reply to, while `original_task_message_id` is only traceability. See [examples/startup-successor.json](examples/startup-successor.json). A self-message belongs to the old session and does not automatically reach its replacement. There is no universal automatic 200K-token trigger.

## Replace a session from a checkpoint

Stop the old teammate's work first. Bind `ORIGINAL_TASK_ID` and `THREAD_ID` from the original task, and `CHECKPOINT_PATH` / `CHECKPOINT_VERSION` from its latest checkpoint. Register the new session and send a new assignment:

```sh
"$MAILBOX_CLI" --db "$MAILBOX_DB" --run "$MAILBOX_RUN" \
  --as claude-session-99 register --role teammate --lead codex-session-17 --client claude

"$MAILBOX_CLI" --db "$MAILBOX_DB" --run "$MAILBOX_RUN" \
  --as codex-session-17 send --to claude-session-99 --type task \
  --thread "$THREAD_ID" --send-key "$ORIGINAL_TASK_ID-successor-v$CHECKPOINT_VERSION" <<MESSAGE
Continue the original task referenced by $ORIGINAL_TASK_ID.
Read the permitted task brief and checkpoint: $CHECKPOINT_PATH (version $CHECKPOINT_VERSION).
Reply to this new assignment for questions and results.
MESSAGE
```

In the successor's startup packet set `task_message_id` to this send's returned `data.message.id`, keep `original_task_message_id` as the original ID, and include the exact checkpoint path/version and permitted task brief. The new teammate claims/replies to the new assignment; it cannot claim or reply to an older task addressed to another session. The original ID is a reference, not authorization to read that old inbox. See [the successor packet](examples/startup-successor.json).

## Limits

The mailbox does not launch or wake agents, inject messages into arbitrary open applications, or make a Claude process a native Codex teammate. Agents receive mail only while actively checking it. Use bounded `wait` calls within the current task's agreed budget. Completed sessions need another prompt or separate launcher integration.

Local identities and run boundaries are logical safeguards, not authentication. Any process with direct file access can read or change the database. Use a local disk, not a network filesystem. Neither skills nor startup artifact lists enforce operating-system permissions.

## Frequently asked questions

### Can Codex and Claude Code send messages to each other?

Yes, through this local CLI and a database both sessions can access. The skills describe when to send, read, claim, acknowledge, and reply. This is shared-inbox coordination, not native membership in the other product's agent team. It does not inject messages into arbitrary open application windows.

### Can I run more than one team lead?

Yes. Use a different `run_id` for each independent task, with one lead and its teammate memberships in each run. The same session can join several runs, but that does not give it separate contexts. See the [schema and routing contract](docs/design.md#identity-and-schema).

### Do I need MCP or an additional model API key?

The mailbox itself uses neither MCP nor a model API. Its Python runtime has no third-party dependencies. Codex and Claude Code keep their own authentication and usage requirements; this plugin does not replace either product.

### Will messages wake an idle agent or resume a crashed task?

No. An active recipient must check its inbox or use a bounded wait. A message claimed but not acknowledged becomes available when its lease expires. After an assignment is accepted, the lead coordinates any unfinished task recovery. See [delivery recovery](#retry-and-recovery) and [session replacement from a checkpoint](#replace-a-session-from-a-checkpoint).

### Does acknowledgement mean the task is complete?

No. `ack` confirms message acceptance. The teammate sends a separate `result` message with its work and evidence. Repeated delivery is possible, so external actions are not guaranteed to happen exactly once.

## Documentation and support

- [Architecture and message delivery](docs/architecture.md): components, tables, logical boundaries, and tasks that outlive sessions.
- [CLI contract and delivery semantics](docs/design.md#complete-cli-contract): command arguments, JSON fields, exit codes, leases, and retry rules.
- [First teammate startup packet](examples/startup-teammate.json) and [replacement session packet](examples/startup-successor.json): paths, addresses, current assignment, and original task references.
- [Setup skill](skills/mailbox-setup/SKILL.md), [lead skill](skills/mailbox-lead/SKILL.md), and [teammate skill](skills/mailbox-teammate/SKILL.md): workflows bundled in the plugin.
- [Report a bug or ask a usage question](https://github.com/MaksDanylenko/agent-mailbox/issues). Include the CLI version and a minimal example without private task content.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 scripts/check_package.py
python3 scripts/demo.py
git diff --check
```

CI runs the same commands on Python 3.10 and 3.13. Tests use temporary databases.

Verified locally: 29 CLI tests, the conversation demo, package checks, skill metadata validation, Claude Code 2.1.304 plugin/marketplace validation, and Codex CLI 0.163.0 marketplace discovery. Codex discovery used a per-command configuration override, without installing the plugin or changing global settings:

```sh
codex -c 'marketplaces.agent-mailbox-local={source_type="local",source="/absolute/path/agent-mailbox"}' \
  plugin list --marketplace agent-mailbox-local --available --json
```

The returned available plugin was `agent-mailbox@agent-mailbox-local` version `0.1.0`. These checks do not establish a completed live conversation between Codex and Claude Code; the included demo uses ordinary CLI subprocesses. Native validation, plugin installation, and live-agent execution are separate checks.

MIT licensed. Contributions should include tests for observable behavior and keep the runtime dependency-free. Architecture: [docs/architecture.md](docs/architecture.md).
