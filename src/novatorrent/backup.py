"""Password-encrypted export/import of the config (settings, state, RSS, resume data).

Format: b"NTBAK1" + salt(16) + nonce(12) + AES-256-GCM(tar.gz). Key = scrypt(password, salt).
auth.json (password hashes, API keys) is deliberately NOT part of a backup.
"""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"NTBAK1"
MAX_BACKUP = 200 * 1024 * 1024
ALLOWED_TOP = {"settings.json", "state.json"}


class BackupError(Exception):
    pass


def _key(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode())


def export_config(config_dir: Path, password: str) -> bytes:
    if len(password) < 8:
        raise BackupError("password must be at least 8 characters")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ALLOWED_TOP:
            f = config_dir / name
            if f.exists():
                tar.add(f, arcname=name)
        for f in sorted((config_dir / "resume").glob("*.fastresume")):
            tar.add(f, arcname=f"resume/{f.name}")
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_key(password, salt)).encrypt(nonce, buf.getvalue(), MAGIC)


def import_config(config_dir: Path, password: str, blob: bytes) -> list[str]:
    """Restore files into config_dir. Returns the restored names. Needs a restart to take effect."""
    if len(blob) > MAX_BACKUP or not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 28:
        raise BackupError("not a NovaTorrent backup")
    salt = blob[6:22]
    nonce = blob[22:34]
    try:
        raw = AESGCM(_key(password, salt)).decrypt(nonce, blob[34:], MAGIC)
    except InvalidTag:
        raise BackupError("wrong password or corrupted backup") from None
    restored = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for m in tar.getmembers():
            name = m.name
            ok = name in ALLOWED_TOP or (
                name.startswith("resume/") and name.count("/") == 1 and name.endswith(".fastresume")
            )
            if not (ok and m.isfile()) or m.size > MAX_BACKUP:
                continue  # skip anything unexpected: no paths, links or devices
            data = tar.extractfile(m).read()
            dest = config_dir / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
            restored.append(name)
    return restored
