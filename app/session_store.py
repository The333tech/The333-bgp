"""Restart-safe sessions. Only hashes of authentication cookies are stored."""

import json
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def load_or_create_salt(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        value = path.read_bytes()
    except FileNotFoundError:
        fd, name = tempfile.mkstemp(prefix=".credential-salt-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(os.urandom(32))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(name, path)
            except FileExistsError:
                pass
        finally:
            Path(name).unlink(missing_ok=True)
        value = path.read_bytes()
    if path.is_symlink() or len(value) != 32:
        raise RuntimeError("invalid session credential salt")
    return value


class SessionStore:
    def __init__(self, path: Path, credential_id: str):
        self.path = path
        self.credential_id = credential_id

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                connection.execute("CREATE TABLE IF NOT EXISTS sessions "
                                   "(token_hash TEXT PRIMARY KEY, credential_id TEXT NOT NULL, "
                                   "expires REAL NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL)")
                yield connection
        finally:
            connection.close()

    def get(self, token_hash: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM sessions WHERE token_hash=? "
                             "AND credential_id=? AND expires>?",
                             (token_hash, self.credential_id, time.time())).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, token_hash: str, session: dict, limit: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires<=? OR credential_id!=?",
                       (time.time(), self.credential_id))
            db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?)",
                       (token_hash, self.credential_id, session["expires_at"],
                        session["created_at"], json.dumps(session)))
            if limit > 0:
                db.execute("DELETE FROM sessions WHERE token_hash IN "
                           "(SELECT token_hash FROM sessions ORDER BY created DESC LIMIT -1 OFFSET ?)",
                           (limit,))

    def delete(self, token_hash: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
