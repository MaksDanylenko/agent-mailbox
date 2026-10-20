#!/usr/bin/env python3
"""Exercise a full addressed conversation without invoking any AI provider."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "agent-mailbox"


def run_demo():
    with tempfile.TemporaryDirectory(prefix="agent-mailbox-demo-") as temporary:
        database = Path(temporary) / "mailbox.db"

        def call(actor, command, *arguments, body=""):
            result = subprocess.run(
                [sys.executable, str(CLI), "--db", str(database), "--run", "demo",
                 "--as", actor, command, *arguments],
                input=body, text=True, encoding="utf-8", capture_output=True, timeout=10,
            )
            if result.returncode:
                raise RuntimeError(f"{command} failed: {result.stdout.strip()}")
            envelope = json.loads(result.stdout)
            if not envelope.get("ok"):
                raise RuntimeError(f"{command} did not return success")
            return envelope["data"]

        def receive(actor, message):
            leased = call(actor, "claim", "--id", message["id"])["message"]
            if leased["state"] != "leased" or not leased["lease_token"]:
                raise RuntimeError("Claim did not reserve delivery")
            return leased

        def acknowledge(actor, leased):
            accepted = call(actor, "ack", "--id", leased["id"],
                            "--lease-token", leased["lease_token"])["message"]
            if accepted["state"] != "acked":
                raise RuntimeError("Delivery was not acknowledged")

        def reply(actor, recipient, parent, kind, body):
            message = call(actor, "reply", "--to", recipient, "--reply-to", parent["id"],
                           "--type", kind, "--send-key", f"{parent['id']}-{kind}", body=body)["message"]
            if message["thread_id"] != parent["thread_id"] or message["reply_to"] != parent["id"]:
                raise RuntimeError("Reply left its parent's conversation")
            return message

        call("lead-session", "run-create", "--label", "Demo review", "--client", "codex")
        call("teammate-session", "register", "--role", "teammate", "--lead", "lead-session",
             "--client", "claude")
        task = call("lead-session", "send", "--to", "teammate-session", "--type", "task",
                    "--send-key", "demo-task", body="Review the proposed changes.")["message"]
        leased = receive("teammate-session", task)
        receipt = reply("teammate-session", "lead-session", task, "message", "Task accepted.")
        acknowledge("teammate-session", leased)
        acknowledge("lead-session", receive("lead-session", receipt))
        question = reply("teammate-session", "lead-session", task, "question", "Include mobile layouts?")
        acknowledge("lead-session", receive("lead-session", question))
        answer = reply("lead-session", "teammate-session", question, "answer", "Simulated human answer: yes.")
        acknowledge("teammate-session", receive("teammate-session", answer))
        result = reply("teammate-session", "lead-session", task, "result", "Review complete; mobile included.")
        acknowledge("lead-session", receive("lead-session", result))
        for actor in ("lead-session", "teammate-session"):
            counts = call(actor, "status")["counts"]
            if counts["available"] or counts["leased"]:
                raise RuntimeError("Demo left an unprocessed message")
        print("PASS: task -> receipt -> question -> simulated human answer -> result; shared thread and acked delivery.")
        print("This demo uses CLI subprocesses, not live Codex or Claude agents.")


if __name__ == "__main__":
    try:
        run_demo()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Demo failed: {error}", file=sys.stderr)
        sys.exit(1)
