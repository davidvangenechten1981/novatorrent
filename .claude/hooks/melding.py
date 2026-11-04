"""Notification hook: play a short sound when Claude needs you (permission or idle)."""

from __future__ import annotations


def main() -> int:
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except (ImportError, RuntimeError):
        pass  # not on Windows, or no sound device: stay silent
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
