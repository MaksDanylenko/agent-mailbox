"""Black-box acceptance tests. All state lives in isolated temporary databases."""

import concurrent.futures
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(os.environ.get("MAILBOX_PROJECT_ROOT", Path(__file__).resolve().parents[1]))
CLI = ROOT / "bin" / "agent-mailbox"


class MailboxCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-mailbox-test-")
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "mailbox.sqlite3"
        self.call("run-create", "--label", "Acceptance", agent="lead")
        self.call("register", "--role", "teammate", "--lead", "lead", agent="mate")
        self.call("register", "--role", "teammate", "--lead", "lead", agent="other")

    def argv(self, *args, agent="lead", run="run-a", db=None):
        return [sys.executable, str(CLI), "--db", str(db or self.db),
                "--run", run, "--as", agent, *args]

    def decode(self, result, expected=0):
        self.assertEqual(result.returncode, expected, (result.stdout, result.stderr))
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["schema_version"], 1)
        self.assertEqual(envelope["ok"], expected == 0)
        if expected:
            self.assertIsInstance(envelope["error"]["code"], str)
            self.assertTrue(envelope["error"]["message"])
        return envelope

    def call(self, *args, body=None, expected=0, **context):
        result = subprocess.run(self.argv(*args, **context), input=body, text=True,
                                capture_output=True, timeout=12)
        return self.decode(result, expected)

    def send(self, key="task-1", body="Build the thing", to="mate", **context):
        return self.call("send", "--to", to, "--type", "task", "--send-key", key,
                         body=body, **context)["data"]["message"]

    def inbox(self, agent="mate", **context):
        return self.call("inbox", agent=agent, **context)["data"]["messages"]

    def claim(self, message_id=None, **context):
        args = ["claim"]
        if message_id:
            args += ["--id", message_id]
        return self.call(*args, agent="mate", **context)["data"]["message"]

    def test_run_and_registration_are_idempotent_but_conflicts_are_rejected(self):
        self.call("register", "--role", "teammate", "--lead", "lead", agent="mate")
        self.call("register", "--role", "lead", agent="mate", expected=4)
        self.call("register", "--role", "lead", agent="second-lead", expected=4)
        self.call("register", "--role", "teammate", "--lead", "missing",
                  agent="orphan", expected=3)

    def test_messages_are_persistent_and_addressed_only_to_the_recipient(self):
        message = self.send()
        self.assertEqual([m["id"] for m in self.inbox()], [message["id"]])
        self.assertEqual(self.inbox("other"), [])
        self.assertEqual(self.inbox("lead"), [])
        got = self.call("status", "--id", message["id"])["data"]["message"]
        self.assertEqual(got["body"], "Build the thing")
        self.assertEqual(got["state"], "pending")

    def test_independent_runs_do_not_mix_shared_agent_addresses_or_keys(self):
        original = self.send()
        self.call("run-create", "--label", "Independent", run="run-b")
        self.call("register", "--role", "teammate", "--lead", "lead", agent="mate", run="run-b")
        second = self.send(body="A different task", run="run-b")
        self.assertNotEqual(original["id"], second["id"])
        self.assertEqual([m["id"] for m in self.inbox(run="run-b")], [second["id"]])
        self.assertEqual([m["id"] for m in self.inbox()], [original["id"]])
        self.call("status", "--id", original["id"], run="run-b", expected=3)
        self.call("claim", "--id", original["id"], run="run-b", agent="mate", expected=3)

    def test_multiple_leads_can_create_independent_runs_in_one_new_database_concurrently(self):
        path = Path(self.temp.name) / "concurrent.sqlite3"

        def create(run):
            return self.call("run-create", "--label", run, run=run, agent=run, db=path)

        leads = [f"lead-{number}" for number in range(12)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(create, leads))
        for lead in leads:
            with self.subTest(lead=lead):
                self.assertEqual(self.call("inbox", run=lead, agent=lead, db=path)["data"]["messages"], [])

    def test_agent_can_send_to_itself(self):
        message = self.send(to="lead", body="Checkpoint: resume from acceptance tests")
        self.assertEqual(self.inbox("lead")[0]["id"], message["id"])
        claimed = self.call("claim", agent="lead")["data"]["message"]
        self.call("ack", "--id", claimed["id"], "--lease-token", claimed["lease_token"])
        self.assertEqual(self.inbox("lead"), [])

    def test_send_retry_keeps_the_original_message_and_generated_thread(self):
        first = self.send()
        retried = self.send()
        self.assertEqual(retried["id"], first["id"])
        self.assertEqual(retried["thread_id"], first["thread_id"])
        self.assertEqual(len(self.inbox()), 1)

    def test_send_key_rejects_changed_payload(self):
        self.send()
        variants = [(["--to", "mate", "--type", "task"], "changed body"),
                    (["--to", "other", "--type", "task"], "Build the thing"),
                    (["--to", "mate", "--type", "question"], "Build the thing")]
        for flags, body in variants:
            with self.subTest(flags=flags, body=body):
                self.call("send", *flags, "--send-key", "task-1", body=body, expected=4)
        self.assertEqual(len(self.inbox()), 1)
        self.assertEqual(self.inbox("other"), [])

    def test_send_keys_are_scoped_to_sender(self):
        first = self.send(to="lead", agent="mate")
        second = self.send(to="lead", agent="other")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self.inbox("lead")), 2)

    def test_reply_preserves_thread_and_points_at_original_message(self):
        task = self.send()
        question = self.call("reply", "--to", "lead", "--reply-to", task["id"],
                             "--type", "question", "--send-key", "question-1",
                             agent="mate", body="Which variant?")["data"]["message"]
        self.assertEqual(question["thread_id"], task["thread_id"])
        self.assertEqual(question["reply_to"], task["id"])
        answer = self.call("reply", "--to", "mate", "--reply-to", question["id"],
                           "--type", "answer", "--send-key", "answer-1",
                           body="Variant A")["data"]["message"]
        self.assertEqual(answer["thread_id"], task["thread_id"])
        self.assertEqual(answer["reply_to"], question["id"])

    def test_foreign_agent_cannot_read_claim_ack_or_reply_to_a_message(self):
        task = self.send()
        self.call("status", "--id", task["id"], agent="other", expected=3)
        self.call("claim", "--id", task["id"], agent="other", expected=3)
        self.call("reply", "--to", "lead", "--reply-to", task["id"], "--type", "result",
                  "--send-key", "foreign", agent="other", body="fake", expected=3)
        claimed = self.claim(task["id"])
        self.call("ack", "--id", task["id"], "--lease-token", claimed["lease_token"],
                  agent="other", expected=3)

    def test_reply_rejects_an_address_other_than_original_sender(self):
        task = self.send()
        self.call("reply", "--to", "other", "--reply-to", task["id"], "--type", "question",
                  "--send-key", "wrong-recipient", agent="mate", body="Hello", expected=4)
        self.assertEqual(self.inbox("other"), [])

    def test_claim_ack_and_repeat_ack_preserve_delivery_state(self):
        task = self.send()
        claimed = self.claim(task["id"])
        self.assertEqual(claimed["id"], task["id"])
        self.assertEqual(claimed["state"], "leased")
        self.assertEqual(claimed["attempts"], 1)
        self.assertTrue(claimed["lease_token"])
        self.assertEqual(self.inbox(), [])
        self.assertIsNone(self.claim())
        ack = ["ack", "--id", task["id"], "--lease-token", claimed["lease_token"]]
        self.call(*ack, agent="mate")
        self.call(*ack, agent="mate")
        state = self.call("status", "--id", task["id"], agent="mate")["data"]["message"]
        self.assertEqual(state["state"], "acked")
        self.call("ack", "--id", task["id"], "--lease-token", "wrong", agent="mate", expected=4)

    def test_concurrent_claims_produce_exactly_one_live_lease(self):
        self.send()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.claim(), range(2)))
        winners = [message for message in results if message is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0]["attempts"], 1)

    def test_expired_lease_is_redelivered_and_old_token_cannot_ack(self):
        task = self.send()
        old = self.call("claim", "--lease-seconds", "1", agent="mate")["data"]["message"]
        time.sleep(1.15)
        self.call("ack", "--id", task["id"], "--lease-token", old["lease_token"],
                  agent="mate", expected=4)
        renewed = self.claim(task["id"])
        self.assertEqual(renewed["attempts"], 2)
        self.assertNotEqual(old["lease_token"], renewed["lease_token"])
        self.call("ack", "--id", task["id"], "--lease-token", old["lease_token"],
                  agent="mate", expected=4)
        self.call("ack", "--id", task["id"], "--lease-token", renewed["lease_token"], agent="mate")

    def test_wait_wakes_without_claiming_or_blocking_writers(self):
        waiting = subprocess.Popen(self.argv("wait", "--timeout", "5", agent="mate"),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: waiting.poll() is None and waiting.kill())
        time.sleep(0.25)
        start = time.monotonic()
        task = self.send()
        stdout, stderr = waiting.communicate(timeout=7)
        self.decode(subprocess.CompletedProcess(waiting.args, waiting.returncode, stdout, stderr))
        self.assertLess(time.monotonic() - start, 3)
        self.assertEqual(self.inbox()[0]["id"], task["id"])
        self.assertEqual(self.claim()["attempts"], 1)

    def test_wait_timeout_is_structured_and_leaves_mailbox_empty(self):
        self.call("wait", "--timeout", "1", agent="mate", expected=5)
        self.assertEqual(self.inbox(), [])

    def test_wait_interrupt_has_documented_exit_code_and_json(self):
        waiting = subprocess.Popen(self.argv("wait", "--timeout", "10", agent="mate"),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: waiting.poll() is None and waiting.kill())
        time.sleep(0.3)
        waiting.send_signal(signal.SIGINT)
        stdout, stderr = waiting.communicate(timeout=3)
        self.decode(subprocess.CompletedProcess(waiting.args, waiting.returncode, stdout, stderr), 130)

    def test_user_content_round_trips_without_shell_expansion(self):
        body = "It's Ukrainian: привіт!\n$(touch SHOULD_NOT_EXIST) `whoami` ; --help\n"
        message = self.send(body=body)
        self.assertEqual(message["body"], body)
        self.assertEqual(self.claim()["body"], body)

    def test_body_file_preserves_exact_text(self):
        body = "Apostrophe ' and\nmultiple lines\n"
        path = Path(self.temp.name) / "body with spaces.txt"
        path.write_text(body, encoding="utf-8")
        message = self.call("send", "--to", "mate", "--type", "message", "--send-key", "file",
                            "--body-file", str(path))["data"]["message"]
        self.assertEqual(message["body"], body)

    def test_invalid_arguments_are_json_errors_and_do_not_deliver(self):
        bad_args = [("inbox", "--limit", "0"), ("inbox", "--limit", "101"),
                    ("claim", "--lease-seconds", "0"), ("claim", "--lease-seconds", "3601"),
                    ("wait", "--timeout", "31"), ("wait", "--timeout", "0"),
                    ("send", "--to", "mate", "--type", "bogus", "--send-key", "bad"),
                    ("send", "--to", "mate", "--type", "task"), ("unknown-command",)]
        for args in bad_args:
            with self.subTest(args=args):
                self.call(*args, expected=2)
        self.assertEqual(self.inbox(), [])

    def test_invalid_creation_does_not_leave_a_database(self):
        path = Path(self.temp.name) / "invalid.sqlite3"
        self.call("run-create", expected=2, db=path)
        self.assertFalse(path.exists())

    def test_unknown_recipient_does_not_consume_send_key(self):
        self.call("send", "--to", "missing", "--type", "task", "--send-key", "future",
                  body="Hello", expected=3)
        self.call("register", "--role", "teammate", "--lead", "lead", agent="missing")
        self.send(key="future", to="missing", body="Hello")
        self.assertEqual(len(self.inbox("missing")), 1)

    def test_missing_database_is_not_created_by_read_commands(self):
        path = Path(self.temp.name) / "missing.sqlite3"
        self.call("inbox", db=path, expected=3)
        self.assertFalse(path.exists())

    def test_newer_database_schema_is_rejected_without_mutation(self):
        connection = sqlite3.connect(self.db)
        connection.execute("PRAGMA user_version=999")
        connection.commit()
        connection.close()
        self.call("send", "--to", "mate", "--type", "task", "--send-key", "future",
                  body="Do not write", expected=1)
        connection = sqlite3.connect(self.db)
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 999)

    def test_status_counts_track_available_leased_and_accepted(self):
        first = self.send()
        self.send(key="second")
        counts = self.call("status", agent="mate")["data"]["counts"]
        self.assertEqual(counts, {"available": 2, "leased": 0, "acked": 0})
        lease = self.claim(first["id"])
        counts = self.call("status", agent="mate")["data"]["counts"]
        self.assertEqual(counts, {"available": 1, "leased": 1, "acked": 0})
        self.call("ack", "--id", first["id"], "--lease-token", lease["lease_token"], agent="mate")
        counts = self.call("status", agent="mate")["data"]["counts"]
        self.assertEqual(counts, {"available": 1, "leased": 0, "acked": 1})
        self.assertEqual(self.call("status")["data"]["counts"],
                         {"available": 0, "leased": 0, "acked": 0})

    def test_explicit_thread_is_checked_and_retry_intent_is_preserved(self):
        first = self.send()
        same_thread = self.call("send", "--to", "mate", "--type", "message",
                                "--send-key", "in-thread", "--thread", first["thread_id"],
                                body="Follow-up")["data"]["message"]
        self.assertEqual(same_thread["thread_id"], first["thread_id"])
        self.call("send", "--to", "mate", "--type", "task", "--send-key", "task-1",
                  "--thread", first["thread_id"], body="Build the thing", expected=4)
        self.call("send", "--to", "mate", "--type", "message", "--send-key", "missing-thread",
                  "--thread", "not-a-thread", body="Follow-up", expected=3)

    def test_invalid_utf8_body_is_rejected_without_consuming_key(self):
        path = Path(self.temp.name) / "invalid-utf8.txt"
        path.write_bytes(b"invalid: \xff")
        self.call("send", "--to", "mate", "--type", "task", "--send-key", "invalid-body",
                  "--body-file", str(path), expected=2)
        self.assertEqual(self.inbox(), [])
        self.send(key="invalid-body", body="Valid replacement")

    def test_existing_run_creation_rejects_conflicting_label_and_lead(self):
        self.call("run-create", "--label", "Acceptance")
        self.call("run-create", "--label", "Different", expected=4)
        self.call("run-create", "--label", "Acceptance", agent="other", expected=4)
        self.send()
        self.assertEqual(len(self.inbox()), 1)

    def test_busy_writer_returns_a_bounded_structured_lock_timeout(self):
        connection = sqlite3.connect(self.db)
        self.addCleanup(connection.close)
        connection.execute("BEGIN IMMEDIATE")
        start = time.monotonic()
        self.call("send", "--to", "mate", "--type", "task", "--send-key", "blocked",
                  body="Blocked", expected=6)
        self.assertLess(time.monotonic() - start, 9)
        connection.rollback()
        self.assertEqual(self.inbox(), [])


if __name__ == "__main__":
    unittest.main()
