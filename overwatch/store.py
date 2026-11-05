"""SQLite durable records and a tamper-evident event chain.

One database belongs to exactly one customer/environment. This is not WORM storage:
production must anchor/export the chain to independently retained immutable storage.
"""
from contextlib import contextmanager, closing
import hashlib
import json
from pathlib import Path
import sqlite3


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Transaction:
    def __init__(self, conn):
        self.conn = conn

    def get(self, kind, identifier):
        row = self.conn.execute("SELECT payload FROM records WHERE kind=? AND id=?",
                                (kind, identifier)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, kind):
        return [json.loads(row[0]) for row in self.conn.execute(
            "SELECT payload FROM records WHERE kind=? ORDER BY rowid", (kind,))]

    def put(self, kind, value):
        self.conn.execute("INSERT INTO records(kind,id,payload) VALUES(?,?,?) "
                          "ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload",
                          (kind, value["id"], canonical(value)))

    def event(self, kind, data, now):
        last = self.conn.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        seq, previous = (last[0] + 1, last[1]) if last else (1, "0" * 64)
        envelope = dict(seq=seq, kind=kind, payload=data, at=now, previous=previous)
        self.conn.execute("INSERT INTO events(seq,kind,payload,at,previous,hash) VALUES(?,?,?,?,?,?)",
                          (seq, kind, canonical(data), now, previous, digest(envelope)))

    def receipt_anchor(self, identifier):
        for row in self.conn.execute("SELECT payload FROM events WHERE kind='observation' ORDER BY seq"):
            payload = json.loads(row[0])
            if payload.get("receipt_id") == identifier:
                return payload.get("hash")
        return None


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    at REAL NOT NULL, previous TEXT NOT NULL, hash TEXT NOT NULL);
            """)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield Transaction(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def read(self, kind, identifier):
        with self.transaction() as tx:
            return tx.get(kind, identifier)

    def list(self, kind):
        with self.transaction() as tx:
            return tx.all(kind)

    def events(self):
        with closing(self.connect()) as conn:
            return [dict(seq=r[0], kind=r[1], payload=json.loads(r[2]), at=r[3],
                         previous=r[4], hash=r[5]) for r in conn.execute(
                "SELECT seq,kind,payload,at,previous,hash FROM events ORDER BY seq")]

    def verify_ledger(self):
        previous = "0" * 64
        try:
            for seq, event in enumerate(self.events(), 1):
                claimed = event.pop("hash")
                if event["seq"] != seq or event["previous"] != previous or digest(event) != claimed:
                    return False
                previous = claimed
        except (ValueError, TypeError):
            return False
        return True
