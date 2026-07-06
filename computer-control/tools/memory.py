"""Persistent task memory for the computer-control skill.

Every task the skill runs is recorded on disk so future sessions can look back
at what was done and how. Layout (root configurable via ``CC_MEMORY_DIR``,
default ``C:\\memory-copilot``)::

    C:\\memory-copilot\\
        INDEX.md                     <- human "super file": all tasks, grouped by kind
        index.json                   <- machine-readable source of truth
        .current                     <- pointer to the currently-active task folder
        20260706-164500_open-notepad-and-write-a-note\\
            task.md                  <- human timeline (task, notes, result)
            meta.json                <- structured metadata for this task
            log.jsonl                <- append-only event log

The GUI records notes automatically; the engine (and Mode B) can also add notes
and search prior memory through the ``cc.py mem-*`` commands.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("CC_MEMORY_DIR", r"C:\memory-copilot"))
INDEX_JSON = ROOT / "index.json"
INDEX_MD = ROOT / "INDEX.md"
CURRENT = ROOT / ".current"

# Simple keyword -> kind map for auto-tagging a task by its description.
_KIND_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("email", ("email", "outlook", "gmail", "inbox", "mail")),
    ("web", ("chrome", "browser", "website", "web", "google", "facebook", "youtube", "search online")),
    ("booking", ("book", "flight", "ticket", "hotel", "order", "buy", "purchase", "cart")),
    ("notes", ("notepad", "note", "write", "document", "word", "text")),
    ("files", ("file", "folder", "rename", "copy", "move", "delete", "explorer", "download")),
    ("media", ("play", "music", "video", "spotify", "song")),
    ("system", ("settings", "control panel", "wifi", "bluetooth", "volume", "screenshot")),
]


def slugify(text: str, max_len: int = 50) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (s[:max_len].rstrip("-")) or "task"


def classify(task: str) -> list[str]:
    """Best-effort tags describing the *kind* of task, for the super index."""
    low = (task or "").lower()
    tags = [kind for kind, words in _KIND_KEYWORDS if any(w in low for w in words)]
    return tags or ["general"]


def _read_index() -> list[dict]:
    try:
        return json.loads(INDEX_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_index(entries: list[dict]) -> None:
    _atomic_write(INDEX_JSON, json.dumps(entries, indent=2, ensure_ascii=False))
    _render_index_md(entries)


def _render_index_md(entries: list[dict]) -> None:
    lines = [
        "# Computer-Control Task Memory",
        "",
        "This is the master index of every task run by the computer-control skill.",
        "Each task has its own folder containing `task.md` (timeline), `meta.json`",
        "and `log.jsonl`. Browse by kind below, or open a folder directly.",
        "",
        f"_Total tasks: {len(entries)} \u00b7 last updated {datetime.now():%Y-%m-%d %H:%M}_",
        "",
    ]
    by_kind: dict[str, list[dict]] = {}
    for e in entries:
        for tag in e.get("tags", ["general"]) or ["general"]:
            by_kind.setdefault(tag, []).append(e)

    lines.append("## By kind")
    lines.append("")
    for kind in sorted(by_kind):
        lines.append(f"- **{kind}** ({len(by_kind[kind])})")
    lines.append("")

    lines.append("## All tasks (newest first)")
    lines.append("")
    lines.append("| When | Task | Kind | Status | Summary | Folder |")
    lines.append("|------|------|------|--------|---------|--------|")
    for e in sorted(entries, key=lambda x: x.get("created_at", ""), reverse=True):
        when = (e.get("created_at", "") or "")[:16].replace("T", " ")
        task = (e.get("task", "") or "").replace("|", "\\|")
        if len(task) > 50:
            task = task[:47] + "..."
        tags = ", ".join(e.get("tags", []) or [])
        status = e.get("status", "")
        summary = (e.get("summary", "") or "").replace("|", "\\|").replace("\n", " ")
        if len(summary) > 70:
            summary = summary[:67] + "..."
        folder = e.get("id", "")
        lines.append(f"| {when} | {task} | {tags} | {status} | {summary} | `{folder}` |")
    lines.append("")
    _atomic_write(INDEX_MD, "\n".join(lines))


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def start_task(task: str, tags: list[str] | None = None) -> dict:
    """Create a new task-memory folder, seed its files, and index it."""
    ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    task_id = f"{stamp}_{slugify(task)}"
    task_dir = ROOT / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "id": task_id,
        "task": task,
        "tags": tags or classify(task),
        "status": "in_progress",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "finished_at": None,
        "summary": "",
        "dir": str(task_dir),
    }
    _atomic_write(task_dir / "meta.json", json.dumps(meta, indent=2, ensure_ascii=False))
    _atomic_write(
        task_dir / "task.md",
        f"# Task: {task}\n\n"
        f"- **Started:** {meta['created_at']}\n"
        f"- **Kind:** {', '.join(meta['tags'])}\n"
        f"- **Status:** in_progress\n\n"
        f"## Timeline\n\n",
    )
    (task_dir / "log.jsonl").write_text("", encoding="utf-8")

    entries = [e for e in _read_index() if e.get("id") != task_id]
    entries.append({k: meta[k] for k in
                    ("id", "task", "tags", "status", "created_at", "summary", "dir")})
    _write_index(entries)

    try:
        CURRENT.write_text(str(task_dir), encoding="utf-8")
    except OSError:
        pass
    return meta


def _resolve_dir(task_dir: str | os.PathLike | None) -> Path | None:
    if task_dir:
        return Path(task_dir)
    env = os.environ.get("CC_TASK_DIR")
    if env:
        return Path(env)
    try:
        p = CURRENT.read_text(encoding="utf-8").strip()
        return Path(p) if p else None
    except OSError:
        return None


def add_note(note: str, kind: str = "note", task_dir=None, role: str = "agent") -> bool:
    """Append a timeline note / event to the active (or given) task."""
    d = _resolve_dir(task_dir)
    if not d or not d.exists():
        return False
    ts = _now_iso()
    try:
        with open(d / "log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": ts, "kind": kind, "role": role, "text": note}) + "\n")
        icon = {"question": "\u2753", "answer": "\U0001f9d1", "alert": "\u26a0\ufe0f",
                "result": "\u2705", "decision": "\U0001f4a1"}.get(kind, "\u2022")
        with open(d / "task.md", "a", encoding="utf-8") as f:
            f.write(f"- {icon} `{ts[11:16]}` {note}\n")
    except OSError:
        return False
    _touch_meta(d)
    return True


def _touch_meta(d: Path) -> dict | None:
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    meta["updated_at"] = _now_iso()
    _atomic_write(d / "meta.json", json.dumps(meta, indent=2, ensure_ascii=False))
    return meta


def finish_task(summary: str = "", status: str = "done", task_dir=None) -> bool:
    """Finalize a task: record its summary/status in meta, task.md and the index."""
    d = _resolve_dir(task_dir)
    if not d or not d.exists():
        return False
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    meta["status"] = status
    meta["summary"] = summary or meta.get("summary", "")
    meta["finished_at"] = _now_iso()
    meta["updated_at"] = meta["finished_at"]
    _atomic_write(d / "meta.json", json.dumps(meta, indent=2, ensure_ascii=False))
    try:
        with open(d / "task.md", "a", encoding="utf-8") as f:
            f.write(f"\n## Result ({status})\n\n{summary or '(no summary)'}\n")
    except OSError:
        pass

    entries = _read_index()
    for e in entries:
        if e.get("id") == meta["id"]:
            e["status"] = status
            e["summary"] = meta["summary"]
            break
    _write_index(entries)

    try:
        if CURRENT.exists() and CURRENT.read_text(encoding="utf-8").strip() == str(d):
            CURRENT.unlink()
    except OSError:
        pass
    return True


def list_tasks(limit: int = 20) -> list[dict]:
    entries = sorted(_read_index(), key=lambda x: x.get("created_at", ""), reverse=True)
    return entries[:limit]


def search(query: str, limit: int = 10) -> list[dict]:
    """Search prior tasks using ONLY the super index (index.json).

    No per-folder traversal: the index carries task text, kind/tags, status and
    summary, so lookups are fast and don't touch every memory folder. Open a
    match's `dir` only when you actually want its full timeline.
    """
    q = (query or "").lower().strip()
    terms = [t for t in re.split(r"\s+", q) if t]
    if not terms:
        return []
    results: list[tuple[int, dict]] = []
    for e in _read_index():
        hay = " ".join([
            e.get("task", ""), " ".join(e.get("tags", []) or []), e.get("summary", ""),
        ]).lower()
        score = sum(hay.count(t) for t in terms)
        if score:
            results.append((score, e))
    results.sort(key=lambda x: x[0], reverse=True)
    return [e for _s, e in results[:limit]]
