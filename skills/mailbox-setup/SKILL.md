---
name: mailbox-setup
description: Use when setting up Agent Mailbox for local Codex and Claude Code sessions, choosing their shared database, or preparing the first startup packet.
---

# Set up Agent Mailbox

Use the bundled CLI to coordinate active local sessions. The skill supplies workflow; the CLI handles SQLite. Locate the plugin root from this skill's installed path: it contains `bin/agent-mailbox`, `src/`, and `plugin.json`. A stable checkout is also suitable. Resolve an absolute CLI path; never assume Codex inherits Claude's plugin `bin/` PATH integration.

Check `python3 --version` (minimum 3.10) and run `python3 /absolute/path/bin/agent-mailbox --help`. No pip dependencies are required. Use the executable directly when its executable bit is present. Do not write SQL or install a database server.

Choose one absolute database path on local disk, outside plugin caches and source checkouts. Both processes need filesystem access. Create its parent directory when setting up the requested mailbox. Use the same database path even if each product has its own installed plugin copy.

Choose a unique `run_id` for each independent top-level task and a unique session address for each agent. Bind `MAILBOX_CLI`, `MAILBOX_DB`, `RUN_ID`, `AGENT_ID`, and `TASK_LABEL` from this setup in each relevant shell; exported variables do not survive a different agent's process automatically.

```sh
"$MAILBOX_CLI" --db "$MAILBOX_DB" --run "$RUN_ID" --as "$AGENT_ID" \
  run-create --label "$TASK_LABEL"
```

This creates the schema/run and registers the caller as leader. Repeating it is safe only with the same run label and leader. A teammate joins with `register --role teammate --lead LEAD_ID`. The leader can pre-register an intended teammate address before sending the initial task.

Provide the first prompt through the user's existing agent launcher. Include resolved CLI/database paths, run/session/leader IDs, current addressed task message ID, original task reference, thread ID, teammate skill path, allowed artifact references, and checkpoint path/version when resuming. IDs come from returned JSON. A startup example is at `examples/startup-teammate.json` under the plugin root.

The CLI neither launches a process nor wakes a completed agent. Keep active sessions checking mail with bounded waits. Run membership is logical routing in a trusted local environment, not authentication or an OS permission boundary. Report the configured paths/addresses and distinguish setup from any unperformed live-agent test.
