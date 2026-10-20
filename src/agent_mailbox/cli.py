"""Command-line parsing, JSON envelopes, and bounded polling."""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from . import __version__
from .store import Failure, Store, public


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure(2, "invalid_input", message)


def nonempty(value):
    if not value.strip():
        raise argparse.ArgumentTypeError("value must not be empty")
    return value


def bounded(low, high):
    def parse(value):
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("value must be an integer") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"value must be between {low} and {high}")
        return number
    return parse


def parser():
    root = Parser(description="Durable local messaging between scoped agent sessions.", allow_abbrev=False)
    root.add_argument("--version", action="version", version=f"agent-mailbox {__version__}")
    root.add_argument("--db", required=True, type=nonempty, help="absolute shared SQLite database path")
    root.add_argument("--run", required=True, type=nonempty, help="isolated run ID")
    root.add_argument("--as", required=True, dest="agent", type=nonempty, help="session agent ID")
    commands = root.add_subparsers(dest="command", required=True, parser_class=Parser)
    create = commands.add_parser("run-create", help="create a run and register its lead", allow_abbrev=False)
    create.add_argument("--label", required=True, type=nonempty)
    create.add_argument("--client", type=nonempty)
    register = commands.add_parser("register", help="join an existing run", allow_abbrev=False)
    register.add_argument("--role", required=True, choices=("lead", "teammate"))
    register.add_argument("--lead", type=nonempty)
    register.add_argument("--client", type=nonempty)
    for name in ("send", "reply"):
        send = commands.add_parser(name, help="send an idempotent addressed message", allow_abbrev=False)
        send.add_argument("--to", required=True, type=nonempty)
        send.add_argument("--type", required=True, choices=("task", "message", "question", "answer", "result"))
        send.add_argument("--send-key", required=True, type=nonempty)
        send.add_argument("--body-file", default="-", help="UTF-8 text file; default: stdin")
        if name == "send":
            send.add_argument("--thread", type=nonempty)
        else:
            send.add_argument("--reply-to", required=True, type=nonempty)
    inbox = commands.add_parser("inbox", help="list available incoming messages", allow_abbrev=False)
    inbox.add_argument("--limit", type=bounded(1, 100), default=20, metavar="N",
                       help="maximum messages, integer 1..100 (default: 20)")
    claim = commands.add_parser("claim", help="lease one incoming message", allow_abbrev=False)
    claim.add_argument("--id", type=nonempty)
    claim.add_argument("--lease-seconds", type=bounded(1, 3600), default=120, metavar="SECONDS",
                       help="integer 1..3600 (default: 120)")
    ack = commands.add_parser("ack", help="acknowledge delivery using its current lease token", allow_abbrev=False)
    ack.add_argument("--id", required=True, type=nonempty)
    ack.add_argument("--lease-token", required=True, type=nonempty)
    status = commands.add_parser("status", help="read an addressed message or incoming counts", allow_abbrev=False)
    status.add_argument("--id", type=nonempty)
    wait = commands.add_parser("wait", help="poll availability without claiming", allow_abbrev=False)
    wait.add_argument("--timeout", type=bounded(1, 30), required=True, metavar="SECONDS",
                      help="integer 1..30; for a longer budget, repeat wait")
    return root


def read_body(path):
    try:
        raw = sys.stdin.buffer.read() if path == "-" else Path(path).read_bytes()
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Failure(2, "invalid_input", "Message body must be valid UTF-8.") from None
    except OSError:
        raise Failure(1, "storage_error", "Cannot read message body file.") from None


def is_busy(error):
    # Extended result codes retain the primary SQLite result in the low byte.
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xff in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    return "locked" in str(error).lower() or "busy" in str(error).lower()


def waiting(args):
    deadline = time.monotonic() + args.timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Failure(5, "timeout", "No incoming message became available before the deadline.")
        store = None
        try:
            store = Store(args.db, args.run, args.agent, timeout=min(5.0, remaining))
            store.require_run()
            rows = store.inbox(1)
            if rows:
                return {"message": public(rows[0])}
        except sqlite3.OperationalError as error:
            if not is_busy(error):
                raise
            # Lock waiting belongs to the same overall timeout budget.
        finally:
            if store is not None:
                store.close()
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(1.0, remaining))


def execute(args):
    if args.command == "wait":
        return waiting(args)
    body = read_body(args.body_file) if args.command in ("send", "reply") else None
    if args.command == "register" and args.role == "teammate" and args.lead is None:
        raise Failure(2, "invalid_input", "Teammate registration requires --lead.")
    store = Store(args.db, args.run, args.agent, create=args.command == "run-create")
    try:
        if args.command == "run-create":
            return store.create_run(args.label, args.client)
        store.require_run()
        if args.command == "register":
            return store.register(args.role, args.lead, args.client)
        if args.command in ("send", "reply"):
            return store.send(args.to, args.type, args.send_key, body,
                              getattr(args, "thread", None), getattr(args, "reply_to", None))
        if args.command == "inbox":
            return {"messages": [public(row) for row in store.inbox(args.limit)]}
        if args.command == "claim":
            return store.claim(args.id, args.lease_seconds)
        if args.command == "ack":
            return store.ack(args.id, args.lease_token)
        return store.status(args.id)
    finally:
        store.close()


def emit(payload):
    # Output is UTF-8 regardless of locale; backslashreplace keeps lone surrogates as JSON escapes.
    text = json.dumps({"schema_version": 1, **payload}, ensure_ascii=False) + "\n"
    sys.stdout.flush()
    sys.stdout.buffer.write(text.encode("utf-8", "backslashreplace"))
    sys.stdout.buffer.flush()


def main(argv=None):
    try:
        data = execute(parser().parse_args(argv))
        emit({"ok": True, "data": data})
        return 0
    except Failure as error:
        failure = error
    except KeyboardInterrupt:
        failure = Failure(130, "interrupted", "Operation interrupted.")
    except sqlite3.Error as error:
        if is_busy(error):
            failure = Failure(6, "busy", "Database busy timeout exceeded.")
        else:
            failure = Failure(1, "storage_error", "Database operation failed.")
    except OSError:
        failure = Failure(1, "storage_error", "Storage operation failed.")
    except Exception:
        failure = Failure(1, "storage_error", "Unexpected operation failure.")
    emit({"ok": False, "error": {"code": failure.code, "message": str(failure)}})
    return failure.status
