"""File-based bridge between the running GUI (control_panel.py) and the agent's
hands-and-eyes CLI (cc.py).

The GUI is the *server*: it polls for requests, shows them to the user, and
writes back responses. The agent side (cc.py `ask` / `confirm` / `log`) is the
*client*. A heartbeat file lets the client detect whether a GUI is actually
running; if not, cc.py falls back to standalone popups.

Transport is plain files under a shared directory (no sockets, so no firewall
prompts). Writes are atomic (write-tmp-then-rename).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path

BRIDGE_DIR = Path(
    os.environ.get(
        "CC_BRIDGE_DIR", str(Path(tempfile.gettempdir()) / "computer_control_bridge")
    )
)
REQ_DIR = BRIDGE_DIR / "requests"
RES_DIR = BRIDGE_DIR / "responses"
LOG_FILE = BRIDGE_DIR / "log.jsonl"
HEARTBEAT = BRIDGE_DIR / "gui.alive"
MANAGE_FLAG = BRIDGE_DIR / "manage.flag"
INTERJECT = BRIDGE_DIR / "interject.jsonl"
HEARTBEAT_TTL = 5.0  # seconds


def ensure_dirs() -> None:
    REQ_DIR.mkdir(parents=True, exist_ok=True)
    RES_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


# --- heartbeat -------------------------------------------------------------
def touch_heartbeat() -> None:
    ensure_dirs()
    try:
        HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def gui_alive() -> bool:
    """True if a GUI updated the heartbeat within the TTL."""
    try:
        return (time.time() - HEARTBEAT.stat().st_mtime) < HEARTBEAT_TTL
    except OSError:
        return False


# --- "manage the GUI window" flag ------------------------------------------
# When the GUI is in always-on-top mode it sets this flag so cc.py knows to
# briefly step the window aside during screenshots and mouse actions (so the
# always-on-top panel never covers the target or appears in screenshots).
def set_manage(on: bool) -> None:
    ensure_dirs()
    try:
        if on:
            MANAGE_FLAG.write_text("1", encoding="utf-8")
        elif MANAGE_FLAG.exists():
            MANAGE_FLAG.unlink()
    except OSError:
        pass


def should_manage() -> bool:
    """cc.py should manage the GUI window only if it's flagged AND alive."""
    try:
        return MANAGE_FLAG.exists() and gui_alive()
    except OSError:
        return False


# --- interjection channel (user redirects the running agent) ---------------
def set_interject(text: str) -> None:
    """Queue a mid-task instruction for the running agent (from the GUI)."""
    ensure_dirs()
    try:
        with open(INTERJECT, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "text": text}) + "\n")
    except OSError:
        pass


def take_interject() -> list[str]:
    """Consume any pending interjections (cc.py calls this before each action)."""
    try:
        lines = INTERJECT.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    try:
        INTERJECT.unlink()
    except OSError:
        pass
    out = []
    for ln in lines:
        try:
            t = json.loads(ln).get("text", "")
            if t:
                out.append(t)
        except Exception:
            pass
    return out


# --- client side (cc.py) ---------------------------------------------------
def send_request(kind: str, payload: dict, timeout: float = 600.0) -> dict | None:
    """Send a request to the GUI and block until it responds or times out.

    Returns the response dict, or None on timeout.
    """
    ensure_dirs()
    rid = uuid.uuid4().hex
    _atomic_write(REQ_DIR / f"{rid}.json", {"id": rid, "kind": kind, "ts": time.time(), **payload})
    res_path = RES_DIR / f"{rid}.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if res_path.exists():
            try:
                data = json.loads(res_path.read_text(encoding="utf-8"))
            except Exception:
                time.sleep(0.1)
                continue
            try:
                res_path.unlink()
            except OSError:
                pass
            return data
        # If the GUI vanished while we were waiting, give up early.
        if not gui_alive():
            break
        time.sleep(0.15)
    try:
        (REQ_DIR / f"{rid}.json").unlink()
    except OSError:
        pass
    return None


def log(message: str, role: str = "agent", alert: bool = False) -> None:
    """Append a message to the shared log the GUI tails and displays."""
    ensure_dirs()
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "role": role,
                                "message": message, "alert": bool(alert)}) + "\n")
    except OSError:
        pass


# --- server side (GUI) -----------------------------------------------------
def poll_requests() -> list[dict]:
    """Return any pending requests (consuming them). Oldest first."""
    ensure_dirs()
    out: list[dict] = []
    for p in sorted(REQ_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
        try:
            p.unlink()
        except OSError:
            pass
    return out


def write_response(rid: str, data: dict) -> None:
    ensure_dirs()
    _atomic_write(RES_DIR / f"{rid}.json", data)


def clear() -> None:
    """Remove stale requests/responses (called by the GUI at startup)."""
    ensure_dirs()
    for p in list(REQ_DIR.glob("*.json")) + list(RES_DIR.glob("*.json")):
        try:
            p.unlink()
        except OSError:
            pass
    for f in (INTERJECT,):
        try:
            f.unlink()
        except OSError:
            pass
