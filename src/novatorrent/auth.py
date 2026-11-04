"""Users (argon2), API keys (sha256 of high-entropy token), sessions and brute-force protection."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .settings import atomic_write

ROLES = ("admin", "readonly")
SESSION_TTL = 3600
MAX_FAILS = 5
LOCKOUT_BASE = 60  # seconds, doubles per extra failure round, capped


class AuthStore:
    def __init__(self, config_dir: Path):
        self.path = config_dir / "auth.json"
        self.config_dir = config_dir
        self.lock = threading.RLock()
        self.ph = PasswordHasher()
        self.sessions: dict[str, tuple[str, str, float, str]] = {}  # sid -> user, role, exp, csrf
        self.fails: dict[str, tuple[int, float]] = {}  # ip -> (count, locked_until)
        self.data: dict[str, Any] = {"users": {}, "api_keys": {}}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self._bootstrap()

    def _save(self) -> None:
        atomic_write(self.path, json.dumps(self.data, indent=1).encode(), mode=0o600)

    def _bootstrap(self) -> None:
        pw = os.environ.get("NOVA_ADMIN_PASSWORD")
        user = os.environ.get("NOVA_ADMIN_USER", "admin")
        if not pw:
            pw = secrets.token_urlsafe(14)
            atomic_write(self.config_dir / "initial_password.txt",
                         f"user: {user}\npassword: {pw}\n".encode(), mode=0o600)
        self.data["users"][user] = {"hash": self.ph.hash(pw), "role": "admin"}
        self._save()

    # ---- users -----------------------------------------------------------------------------
    def verify(self, user: str, password: str) -> str | None:
        """Return the role on success. Always does one hash verification to limit timing leaks."""
        with self.lock:
            rec = self.data["users"].get(user)
        dummy = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHRzb21lc2FsdA$" + "A" * 43
        try:
            self.ph.verify(rec["hash"] if rec else dummy, password)
        except (VerifyMismatchError, InvalidHashError, Exception):
            return None
        return rec["role"] if rec else None

    def set_user(self, user: str, password: str, role: str = "admin") -> None:
        if role not in ROLES or not user or len(password) < 8:
            raise ValueError("invalid user, role or password (min 8 chars)")
        with self.lock:
            self.data["users"][user] = {"hash": self.ph.hash(password), "role": role}
            self._save()

    def delete_user(self, user: str) -> bool:
        with self.lock:
            admins = [u for u, r in self.data["users"].items() if r["role"] == "admin"]
            if user not in self.data["users"] or (user in admins and len(admins) == 1):
                return False
            del self.data["users"][user]
            self._save()
            return True

    def list_users(self) -> list[dict[str, str]]:
        with self.lock:
            return [{"user": u, "role": r["role"]} for u, r in self.data["users"].items()]

    # ---- api keys --------------------------------------------------------------------------
    def create_api_key(self, name: str, role: str = "readonly") -> str:
        if role not in ROLES:
            raise ValueError("invalid role")
        token = "nt_" + secrets.token_urlsafe(32)
        with self.lock:
            self.data["api_keys"][hashlib.sha256(token.encode()).hexdigest()] = {
                "name": name, "role": role, "created": int(time.time())}
            self._save()
        return token  # shown once, only the hash is stored

    def check_api_key(self, token: str) -> str | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            for stored, rec in self.data["api_keys"].items():
                if hmac.compare_digest(stored, digest):
                    return rec["role"]
        return None

    def list_api_keys(self) -> list[dict[str, Any]]:
        with self.lock:
            return [{"id": k[:8], **v} for k, v in self.data["api_keys"].items()]

    def delete_api_key(self, key_id: str) -> bool:
        with self.lock:
            for k in list(self.data["api_keys"]):
                if k.startswith(key_id) and len(key_id) >= 8:
                    del self.data["api_keys"][k]
                    self._save()
                    return True
        return False

    # ---- sessions --------------------------------------------------------------------------
    def new_session(self, user: str, role: str) -> tuple[str, str]:
        sid, csrf = secrets.token_hex(16), secrets.token_urlsafe(24)
        with self.lock:
            self._expire()
            self.sessions[sid] = (user, role, time.time() + SESSION_TTL, csrf)
        return sid, csrf

    def get_session(self, sid: str | None) -> tuple[str, str, str] | None:
        if not sid:
            return None
        with self.lock:
            rec = self.sessions.get(sid)
            if not rec or rec[2] < time.time():
                self.sessions.pop(sid, None)
                return None
            self.sessions[sid] = (rec[0], rec[1], time.time() + SESSION_TTL, rec[3])
            return rec[0], rec[1], rec[3]

    def end_session(self, sid: str | None) -> None:
        with self.lock:
            self.sessions.pop(sid or "", None)

    def _expire(self) -> None:
        now = time.time()
        for k in [k for k, v in self.sessions.items() if v[2] < now]:
            del self.sessions[k]

    # ---- brute force -----------------------------------------------------------------------
    def locked_for(self, ip: str) -> int:
        with self.lock:
            _, until = self.fails.get(ip, (0, 0.0))
            return max(0, int(until - time.time()))

    def record_fail(self, ip: str) -> None:
        with self.lock:
            if len(self.fails) > 10000:
                self.fails.clear()
            count, _ = self.fails.get(ip, (0, 0.0))
            count += 1
            until = 0.0
            if count >= MAX_FAILS:
                until = time.time() + min(LOCKOUT_BASE * 2 ** (count - MAX_FAILS), 3600)
            self.fails[ip] = (count, until)

    def record_ok(self, ip: str) -> None:
        with self.lock:
            self.fails.pop(ip, None)
