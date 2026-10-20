"""SQLite storage. Transactions cover delivery operations, never agent work."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Failure(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


def missing(message="Object not found in the selected run."):
    raise Failure(3, "not_found", message)


def conflict(message="Request conflicts with existing state."):
    raise Failure(4, "conflict", message)


def now():
    return time.time_ns() // 1_000_000


def identifier():
    return uuid.uuid4().hex


SCHEMA = [
    "CREATE TABLE runs(id TEXT PRIMARY KEY, label TEXT NOT NULL, created_at INTEGER NOT NULL)",
    "CREATE TABLE agents(id TEXT PRIMARY KEY, client TEXT, created_at INTEGER NOT NULL)",
    """CREATE TABLE memberships(
        run_id TEXT NOT NULL REFERENCES runs(id), agent_id TEXT NOT NULL REFERENCES agents(id),
        role TEXT NOT NULL CHECK(role IN ('lead','teammate')), lead_agent_id TEXT,
        PRIMARY KEY(run_id,agent_id),
        CHECK((role='lead' AND lead_agent_id IS NULL) OR
              (role='teammate' AND lead_agent_id IS NOT NULL)),
        FOREIGN KEY(run_id,lead_agent_id) REFERENCES memberships(run_id,agent_id))""",
    "CREATE UNIQUE INDEX one_lead ON memberships(run_id) WHERE role='lead'",
    """CREATE TABLE threads(run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL,
        created_at INTEGER NOT NULL, PRIMARY KEY(run_id,id))""",
    """CREATE TABLE messages(
        run_id TEXT NOT NULL, id TEXT NOT NULL, thread_id TEXT NOT NULL,
        sender_id TEXT NOT NULL, recipient_id TEXT NOT NULL, reply_to TEXT,
        type TEXT NOT NULL CHECK(type IN ('task','message','question','answer','result')),
        body TEXT NOT NULL, send_key TEXT NOT NULL, created_at INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','leased','acked')),
        lease_token TEXT, lease_until INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
        acked_at INTEGER, request_intent TEXT NOT NULL,
        PRIMARY KEY(run_id,id), UNIQUE(run_id,thread_id,id),
        UNIQUE(run_id,sender_id,send_key),
        FOREIGN KEY(run_id,thread_id) REFERENCES threads(run_id,id),
        FOREIGN KEY(run_id,sender_id) REFERENCES memberships(run_id,agent_id),
        FOREIGN KEY(run_id,recipient_id) REFERENCES memberships(run_id,agent_id),
        FOREIGN KEY(run_id,thread_id,reply_to) REFERENCES messages(run_id,thread_id,id))""",
    "CREATE INDEX inbox ON messages(run_id,recipient_id,state,created_at,id)",
    "CREATE INDEX expired_leases ON messages(run_id,recipient_id,state,lease_until)",
]


def public(row):
    if row is None:
        return None
    return {key: row[key] for key in row.keys() if key != "request_intent"}


class Store:
    def __init__(self, path, run, agent, create=False, timeout=5.0):
        self.run = run
        self.agent = agent
        self.db = None
        p = Path(path)
        if not p.is_absolute():
            raise Failure(2, "invalid_input", "Database path must be absolute.")
        if not p.parent.is_dir():
            raise Failure(1, "storage_error", "Database parent directory does not exist.")
        if not create and not p.exists():
            missing("Database does not exist.")
        try:
            self.db = sqlite3.connect(p.as_uri() + ("?mode=rwc" if create else "?mode=rw"),
                                      uri=True, timeout=timeout, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            # Inspect before even journal-mode setup: unknown schemas stay untouched.
            if version not in (0, 1) or (version == 0 and not create):
                raise Failure(1, "storage_error", "Unsupported database schema version.")
            if version == 0 and self.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone():
                raise Failure(1, "storage_error", "Unrecognized database schema.")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA synchronous=FULL")
            if create:
                self.db.execute("PRAGMA journal_mode=WAL")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    @contextmanager
    def write(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def membership(self, agent=None):
        row = self.db.execute("SELECT * FROM memberships WHERE run_id=? AND agent_id=?",
                              (self.run, self.agent if agent is None else agent)).fetchone()
        if row is None:
            missing("Agent is not a member of the selected run.")
        return row

    def require_run(self):
        if self.db.execute("SELECT 1 FROM runs WHERE id=?", (self.run,)).fetchone() is None:
            missing("Run does not exist.")

    def agent_record(self, client):
        existing = self.db.execute("SELECT * FROM agents WHERE id=?", (self.agent,)).fetchone()
        if existing is None:
            self.db.execute("INSERT INTO agents VALUES(?,?,?)", (self.agent, client, now()))
        elif client is not None and existing["client"] != client:
            conflict("Agent client does not match its registration.")

    def create_run(self, label, client):
        with self.write():
            # Another process may have initialized while we awaited the writer lock.
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                for statement in SCHEMA:
                    self.db.execute(statement)
                self.db.execute("PRAGMA user_version=1")
            elif version != 1:
                raise Failure(1, "storage_error", "Unsupported database schema version.")
            existing = self.db.execute("SELECT * FROM runs WHERE id=?", (self.run,)).fetchone()
            if existing is None:
                self.agent_record(client)
                self.db.execute("INSERT INTO runs VALUES(?,?,?)", (self.run, label, now()))
                self.db.execute("INSERT INTO memberships VALUES(?,?,'lead',NULL)",
                                (self.run, self.agent))
            else:
                lead = self.db.execute("SELECT agent_id FROM memberships WHERE run_id=? AND role='lead'",
                                       (self.run,)).fetchone()
                if existing["label"] != label or lead is None or lead[0] != self.agent:
                    conflict("Run label or lead differs from its creation request.")
                self.agent_record(client)
            return {"run": public(self.db.execute("SELECT * FROM runs WHERE id=?", (self.run,)).fetchone()),
                    "membership": public(self.membership())}

    def register(self, role, lead, client):
        with self.write():
            self.require_run()
            creator = self.db.execute("SELECT agent_id FROM memberships WHERE run_id=? AND role='lead'",
                                      (self.run,)).fetchone()
            if role == "lead":
                if lead is not None or creator is None or creator[0] != self.agent:
                    conflict("Lead registration must match the run creator.")
            else:
                if lead is None:
                    raise Failure(2, "invalid_input", "Teammate registration requires --lead.")
                self.membership(lead)
                if creator is None or creator[0] != lead:
                    conflict("Teammates must reference the run lead.")
            existing = self.db.execute("SELECT * FROM memberships WHERE run_id=? AND agent_id=?",
                                       (self.run, self.agent)).fetchone()
            if existing and (existing["role"] != role or existing["lead_agent_id"] != lead):
                conflict("Membership differs from its registration.")
            self.agent_record(client)
            if existing is None:
                self.db.execute("INSERT INTO memberships VALUES(?,?,?,?)", (self.run, self.agent, role, lead))
            return {"membership": public(self.membership())}

    def send(self, recipient, kind, key, body, thread=None, reply=None):
        intent = json.dumps([recipient, kind, body, thread, reply], ensure_ascii=True)
        with self.write():
            self.membership()
            self.membership(recipient)
            existing = self.db.execute("SELECT * FROM messages WHERE run_id=? AND sender_id=? AND send_key=?",
                                       (self.run, self.agent, key)).fetchone()
            if existing:
                if existing["request_intent"] != intent:
                    conflict("Send key has already been used with different input.")
                return {"message": public(existing)}
            if reply is not None:
                parent = self.incoming(reply)
                if parent["sender_id"] != recipient:
                    conflict("Reply recipient must be the original sender.")
                thread = parent["thread_id"]
            elif thread is None:
                thread = identifier()
                self.db.execute("INSERT INTO threads VALUES(?,?,?)", (self.run, thread, now()))
            elif self.db.execute("SELECT 1 FROM threads WHERE run_id=? AND id=?", (self.run, thread)).fetchone() is None:
                missing("Thread does not exist in the selected run.")
            message_id = identifier()
            self.db.execute("""INSERT INTO messages(run_id,id,thread_id,sender_id,recipient_id,reply_to,
                            type,body,send_key,created_at,state,request_intent)
                            VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?)""",
                            (self.run, message_id, thread, self.agent, recipient, reply, kind, body, key, now(), intent))
            return {"message": public(self.incoming_or_outgoing(message_id))}

    def incoming(self, message_id):
        row = self.db.execute("SELECT * FROM messages WHERE run_id=? AND id=? AND recipient_id=?",
                              (self.run, message_id, self.agent)).fetchone()
        if row is None:
            missing()
        return row

    def incoming_or_outgoing(self, message_id):
        row = self.db.execute("SELECT * FROM messages WHERE run_id=? AND id=? AND (recipient_id=? OR sender_id=?)",
                              (self.run, message_id, self.agent, self.agent)).fetchone()
        if row is None:
            missing()
        return row

    @staticmethod
    def available(row, timestamp):
        return row["state"] == "pending" or (row["state"] == "leased" and row["lease_until"] <= timestamp)

    def inbox(self, limit=20):
        self.membership()
        return self.db.execute("""SELECT * FROM messages WHERE run_id=? AND recipient_id=? AND
            (state='pending' OR (state='leased' AND lease_until<=?)) ORDER BY created_at,id LIMIT ?""",
                               (self.run, self.agent, now(), limit)).fetchall()

    def claim(self, message_id, seconds):
        with self.write():
            self.membership()
            timestamp = now()
            if message_id is not None:
                row = self.incoming(message_id)
                if not self.available(row, timestamp):
                    conflict("Message is not available for claiming.")
            else:
                rows = self.inbox(1)
                row = rows[0] if rows else None
            if row is None:
                return {"message": None}
            self.db.execute("""UPDATE messages SET state='leased',lease_token=?,lease_until=?,attempts=attempts+1
                WHERE run_id=? AND id=?""", (identifier(), timestamp + seconds * 1000, self.run, row["id"]))
            return {"message": public(self.incoming(row["id"]))}

    def ack(self, message_id, token):
        with self.write():
            self.membership()
            row = self.incoming(message_id)
            if row["lease_token"] != token:
                conflict("Lease token is not current.")
            if row["state"] == "acked":
                return {"message": public(row)}
            if row["state"] != "leased" or row["lease_until"] <= now():
                conflict("Lease is no longer active.")
            self.db.execute("UPDATE messages SET state='acked',lease_until=NULL,acked_at=? WHERE run_id=? AND id=?",
                            (now(), self.run, message_id))
            return {"message": public(self.incoming(message_id))}

    def status(self, message_id):
        self.membership()
        if message_id is not None:
            return {"message": public(self.incoming_or_outgoing(message_id))}
        timestamp = now()
        row = self.db.execute("""SELECT
            COALESCE(SUM(state='pending' OR (state='leased' AND lease_until<=?)),0) AS available,
            COALESCE(SUM(state='leased' AND lease_until>?),0) AS leased,
            COALESCE(SUM(state='acked'),0) AS acked FROM messages WHERE run_id=? AND recipient_id=?""",
                              (timestamp, timestamp, self.run, self.agent)).fetchone()
        return {"counts": dict(row)}
