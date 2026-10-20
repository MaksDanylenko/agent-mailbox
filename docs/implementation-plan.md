# Agent Mailbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a public, documented Codex/Claude mailbox plugin with a durable local CLI.

**Architecture:** A standard-library Python CLI owns all SQLite transactions. Skills supply startup/routing/polling workflow and share one core across native plugin metadata. A leader coordinates replacement sessions using original tasks and versioned checkpoints.

**Tech Stack:** Python >=3.10, sqlite3, argparse, json, unittest, subprocess; GitHub Actions.

**Spec:** `design.md` alongside this plan in the new project's `docs/` directory.

## Global Constraints

- Python >=3.10; runtime uses only the standard library.
- Executable entrypoint: `bin/agent-mailbox`.
- Public documentation, CLI messages, skill text, and plugin metadata are English.
- No MCP, remote service, daemon, automatic agent launching, or wakeup mechanism.
- No private company references, paths, secrets, or existing project content.
- No task-wide execution lock or exactly-once external-action claims.

## Review Focus

- Auto-created threads: retry with omitted thread must recover the original message.
- Human-written bodies: Unicode, apostrophes, shell-looking text, multiline content, empty text, invalid UTF-8 remain data.
- Scope confusion: one session participating in two runs never reads/replies across them accidentally.
- Lease races: two consumers, expired tokens and repeated accepted acks preserve delivery semantics.
- Install boundaries: host plugin cache paths, PATH behavior, interpreter availability, and state location do not silently break shared access.

## File map

- `bin/agent-mailbox`: executable launcher using its own path to locate source, independent of cwd.
- `src/agent_mailbox/__init__.py`, `__main__.py`: version and module entry.
- `src/agent_mailbox/cli.py`: parser, input files, envelopes, exit mapping, wait cancellation.
- `src/agent_mailbox/store.py`: schema/init/connections and transactional mailbox operations; keep smaller helpers in `schema.py` if needed.
- `tests/test_cli.py`, `tests/test_delivery.py`: independent black-box tests using subprocesses and temporary directories.
- `plugin.json`, `.claude-plugin/plugin.json`: native metadata, no fabricated manifest fields.
- `skills/mailbox-setup/SKILL.md`, `skills/mailbox-lead/SKILL.md`, `skills/mailbox-teammate/SKILL.md`: host setup, routing, delivery, checkpoints.
- `README.md`, `LICENSE`, `.gitignore`, `docs/architecture.md`, `examples/startup-teammate.json`, `examples/startup-successor.json`: public project documentation and generic examples.
- `scripts/demo.py`, `scripts/check_package.py`, `.github/workflows/ci.yml`: executable demo, local packaging check, CI.

### Task 1: Durable CLI and independently tested delivery contract

**Files:** entrypoint/source and two test files from the map; minimal `.gitignore`.

**Interfaces:** Consumes spec's complete CLI contract. Produces executable subprocess interface `bin/agent-mailbox --db DB --run RUN --as AGENT COMMAND`, JSON envelopes, documented exits. Internal implementation is private; tests may create SQLite locks or adjust lease deadlines with a separate connection solely to exercise failure conditions.

- [ ] Separate tester writes failing black-box unittest cases first from spec. Core tests cover `run-create/register`, exact JSON/exit contracts, read commands not creating databases, newer schemas, two-run membership and self-messaging; assert body round-trip for `"it's $(data)\nУкраїнська"`, empty body, and invalid UTF-8. Test names clearly identify each behavior.
- [ ] Run `python3 -m unittest discover -s tests -v`; confirm failures demonstrate absent CLI/behavior, not test syntax or environment errors.
- [ ] Developer implements launcher/schema/commands from spec using bound SQL parameters, short transactions, WAL and stable error handling. Public signatures remain only the CLI; avoid exposing speculative library APIs.
- [ ] Tester adds delivery/failure cases: same-key omitted-thread retry yields one ID/thread; changed body conflicts; foreign-run thread/reply rejected; two concurrent claim subprocesses yield one reservation; 1-second lease expiry allows re-claim; old token ack fails, new token ack succeeds, repeat accepted ack succeeds. Add wait timeout/SIGINT, concurrent send while waiting, held write-lock busy exit, and restart durability.
- [ ] Run full unittest discovery again; all tests must pass. Review source independently for at-least-once/task distinction and unknown exception JSON behavior; fix real findings and rerun affected tests.
- [ ] Commit explicit Task 1 paths: `feat: add durable local mailbox CLI`.

### Task 2: Native plugin, skills, public docs, CI, and reproducible demo

**Files:** metadata, three skills, public docs/license, demo/check scripts, CI from map. Preserve the baseline `docs/design.md` and `docs/implementation-plan.md` with generic public paths.

**Interfaces:** Consumes Task 1 executable/JSON/exit contract. Produces a package usable by both hosts and a demo runnable as `python3 scripts/demo.py`. `python3 scripts/check_package.py` validates manifest syntax, required files and referenced bundled paths, executable bit, no MCP configuration, and skill metadata; do not claim it replaces native host validation.

- [ ] Tester defines acceptance checks that fail for missing package files; checks skills include startup packet, JSON-derived IDs/tokens, scope, bounded polling, receipt/result difference and checkpoint reassignment. Pin exact names `mailbox-setup`, `mailbox-lead`, `mailbox-teammate`.
- [ ] Developer adds current supported root plugin.json plus Claude metadata and shared skills, using official packaging docs. Setup resolves absolute CLI/Python paths and shared db location; no install hooks that claim to deploy Python, no reliance on portable auto-PATH, no state in plugin version cache. Use MIT license unless the owner supplied another license during implementation.
- [ ] Developer writes English README/architecture/startup example with exact CLI recipes and generic paths. Include local-marketplace installation for both hosts, supported environment, dependency check, commands/errors, logical boundaries, original-task/checkpoint protocol, no wakeup, and acked-task recovery limits. Verify referenced files exist and examples match actual `--help`.
- [ ] Developer adds demo that invokes the real CLI in a temporary directory, creates leader/teammate, sends task, question, answer and result, claims/acks each delivery, and asserts correct run/thread/reply routing. It cleans its own temporary data and prints a short success statement.
- [ ] Developer adds CI matrix Python 3.10/3.13 executing unittest discovery, package checker and demo without runtime dependencies.
- [ ] Run `python3 -m unittest discover -s tests -v`, `python3 scripts/check_package.py`, `python3 scripts/demo.py`; require exit 0. Check `git diff --check` and inspect full staged content for accidental private paths/data. Run native plugin validation and live mixed-agent smoke check if installed/available; report omissions explicitly, never claim simulated subprocess agents are live AI agents.
- [ ] Independent reviewer checks entire new-project diff, scripts/manifest paths, native packaging assumptions, SQL concurrency and routing. Fix verified findings; rerun relevant checks.
- [ ] Commit explicit Task 2 paths: `feat: package mailbox plugin with skills and documentation`.
- [ ] Implementing lead creates authorized public repository in selected namespace, pushes feature/default branch as appropriate for a new empty repository, confirms public visibility and actual remote checks. Give owner repository URL, tested behavior and any native-host check limitations. Never publish temporary databases, checkpoints containing private work, credentials, or unrelated repository files.

## Execution and self-review

Execution method follows the session's team-lead model: developer and independent tester own separate allowed files; reviewer checks completed changes. Do not ask for redundant permission to do the already-authorized implementation/publication. No code was produced by this planning step.

Coverage checked against spec: schema/routing/CLI/durability in Task 1; startup/skills/checkpoints/packaging/docs/CI/demo/publication in Task 2. Review Focus cases map to Task 1 tests or Task 2 packaging/native checks. Boundaries remain explicit: task ownership recovery is manual, live-host checks can be unavailable, and local trusted identities are not authentication.
