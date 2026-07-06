#!/usr/bin/env python
"""control_panel.py - the GUI front-end (Mode C) for computer-control.

Launch this and everything happens in ONE window:

* type a task at the bottom and press Run,
* watch live progress in the log,
* answer the agent's questions (free text or radio choices) inline,

...without touching the terminal. Each task is executed by a headless
``copilot -p`` engine spawned in the background, so it uses your Copilot
subscription and needs NO Anthropic API key. The engine talks back to this
window through the file bridge (tools/bridge.py) via the skill's ``cc.py``.

Run it with:  pythonw control_panel.py   (or)   python control_panel.py
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading

_SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
if _SKILL_DIR not in sys.path:
    sys.path.insert(0, _SKILL_DIR)

import tkinter as tk
from tkinter import scrolledtext

from tools import bridge, memory

CC = os.path.join(_SKILL_DIR, "cc.py")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

# Instructions handed to the headless engine for every task. It makes the engine
# use the skill's cc.py for actions AND for all user interaction (so questions
# surface in this GUI rather than the terminal).
PROMPT_TEMPLATE = '''You are operating the user's real Windows computer to complete a task.

PREFER DIRECT METHODS OVER GUI AUTOMATION. You already have file read/write/edit
and shell/terminal tools. Use them directly whenever possible instead of the
screenshot/click/type loop, because that loop is slow and error-prone:
- Reading or editing code/files: read and edit the files directly on disk. Do
  NOT screenshot the editor or retype code.
- Opening a project in VS Code: run `code "<folder>"` (or `code -r`, `code -g file:line`).
- Creating/moving/renaming files, git, installing packages, running builds/tests,
  launching apps with a URL/args: use shell commands.
Use the hands-and-eyes CLI below ONLY when there is no CLI/file/API alternative
(e.g. clicking through a GUI-only desktop app, or visually confirming on-screen state).

WEB TASKS — ask which browser first. When the task involves a website/browser,
ask the user (one `ask --choices`) which they want:
  - "Automated (Playwright) browser" -> use the `browser` commands below. This is
    DOM-level and FAST: no screenshots, no coordinate guessing. Strongly preferred.
  - "Normal browser" -> drive their real Chrome visually with the hands-and-eyes
    CLI (open/screenshot/click/type).
Automated browser commands (run with python; a persistent browser stays open and
logged in across commands — NO screenshots needed):
  python "{cc}" browser goto --url "<url>"          open/navigate
  python "{cc}" browser read                        get the visible page text
  python "{cc}" browser links                       list clickable elements (text)
  python "{cc}" browser search --text "<query>"     find the search box, type & submit
  python "{cc}" browser click --text "Sign in"      click by visible text
  python "{cc}" browser click --role button --name "Sign in"   click by ARIA role+name
  python "{cc}" browser click --selector "#id"      or click by CSS selector
  python "{cc}" browser fill --label "Email" --text "a@b.com"  fill by label/placeholder/selector
  python "{cc}" browser wait --text "Results"       wait until text/selector appears
  python "{cc}" browser get --selector ".price"     text of the first match
  python "{cc}" browser extract --selector ".item"  text of ALL matches (lists/results)
  python "{cc}" browser url                         current title + URL
  python "{cc}" browser scroll --to bottom          page scroll (or --px 800)
  python "{cc}" browser press --key "Enter" | back | eval --js "<expr>" | screenshot [--out p] | close
Typical flow: goto -> read/links (or search) -> click/fill -> wait -> read/extract.
These ready-made commands mean you don't write Playwright yourself. Reserve
`browser screenshot` for the rare case you must SEE something (e.g. a captcha).

Hands-and-eyes CLI (run each with python) — the fallback for genuine GUI interaction:
  python "{cc}" screenshot            capture the screen to an image file, then OPEN/VIEW that path to see it
  python "{cc}" open "<app>"          open an app via Windows search
  python "{cc}" launch "<command>"    launch a command/exe directly
  python "{cc}" focus "<title>"       bring a window to the front
  python "{cc}" windows               list open window titles
  python "{cc}" type "<text>" [--enter]
  python "{cc}" click X Y [--double] [--button right]   (X,Y are in the last screenshot's image coordinates)
  python "{cc}" move X Y | python "{cc}" drag X1 Y1 X2 Y2
  python "{cc}" scroll --dir down|up [--step N] [--times N] [--gap S] [--duration S]
      (step = wheel notches per scroll, times = how many steps, gap = seconds between them,
       or duration = keep scrolling for S seconds; scrolls the content under the screen centre)
  python "{cc}" key "ctrl+s"
When you DO use the GUI, screenshot first and again after each action to verify.

Learn from past runs and record this one:
  python "{cc}" mem-search "<keywords>"   check how similar tasks were done before (reads the index only)
  python "{cc}" mem-note "<decision>"     record a key decision/learning for the future
(The GUI already logs your progress to this task's memory automatically.)

Communicate with the user ONLY through these commands (a GUI window shows them; do NOT expect terminal input):
  python "{cc}" log "<short progress update>"            normal progress note
  python "{cc}" log --alert "<important message>"        important note; pops the GUI to the front
  python "{cc}" ask "<question>" [--choices "A" "B" "C"] [--allow-freeform] [--sensitive]
      -> prints the user's answer as JSON: {{"answer": "..."}}. Read it and continue.
  python "{cc}" confirm "<one-line summary>"
      -> REQUIRED before any payment, booking, purchase, sending a message, accepting terms,
         or destructive/irreversible action. Prints {{"approved": true|false}}; proceed only if true.

Rules:
- Post a `log` message when you start and at each key step; post a `log --alert` with the final result when done.
- If any command output contains "USER INTERRUPTION", STOP your current approach at once and follow the new
  instruction instead (the pending action was not performed); re-screenshot if you need to re-orient.
- Whenever anything is ambiguous (dates, times, budget, which option, which account, login), STOP and use `ask`.
- For a scroll request, if the user hasn't said how far, ask them (one `ask`) for the scroll amount per
  step, the gap between steps, and how long / how many steps; then run `scroll` with --step/--gap/--times
  (or --duration). Scroll in steps with a gap rather than one big jump.
- Never type credentials the user did not give you; request secrets with `ask --sensitive` and never echo them.
- Prefer the automated (Playwright) browser for web tasks; prefer direct file/shell tools for code and file tasks.

TASK: {task}
'''


class ControlPanel:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.proc: subprocess.Popen | None = None
        self.out_q: "queue.Queue[str]" = queue.Queue()
        self._log_offset = 0
        self._pending: list[dict] = []
        self._showing: str | None = None
        self._qwidgets: list = []
        self._q_var: tk.StringVar | None = None
        self._q_entry = None
        self._q_kind = "ask"
        self._q_other = "\x00other"
        self.always_on_top = tk.BooleanVar(value=True)
        self.compact = tk.BooleanVar(value=False)
        self.sound_ask = tk.BooleanVar(value=False)
        self._normal_geom = "700x620"
        self._compact_geom = "380x150"
        self._task_dir: str | None = None
        self._last_summary: str = ""
        self._server_proc = None
        self._stopping = False

        root.title("Computer Control")
        root.geometry(self._normal_geom)
        root.minsize(320, 120)

        header = tk.Label(
            root, text="Computer Control  \u2014  type a task, I'll do it on your PC",
            font=("Segoe UI", 11, "bold"), anchor="w",
        )
        header.pack(fill="x", padx=10, pady=(8, 0))
        opts = tk.Frame(root)
        opts.pack(fill="x", padx=10)
        tk.Checkbutton(
            opts, text="\U0001f514 Sound alert", variable=self.sound_ask,
            font=("Segoe UI", 8), fg="#555",
        ).pack(side="left")
        tk.Checkbutton(
            opts, text="Stay on top", variable=self.always_on_top,
            command=self._apply_topmost, font=("Segoe UI", 8), fg="#555",
        ).pack(side="right")
        tk.Checkbutton(
            opts, text="Compact", variable=self.compact,
            command=self._toggle_compact, font=("Segoe UI", 8), fg="#555",
        ).pack(side="right", padx=(0, 10))

        self.log = scrolledtext.ScrolledText(
            root, wrap="word", font=("Segoe UI", 10), state="disabled", height=18
        )
        self.log.pack(fill="both", expand=True, padx=10, pady=(6, 4))
        self.log.tag_config("you", foreground="#0a58ca", font=("Segoe UI", 10, "bold"))
        self.log.tag_config("agent", foreground="#146c2e")
        self.log.tag_config("ask", foreground="#8a5a00", font=("Segoe UI", 10, "bold"))
        self.log.tag_config("engine", foreground="#8a8a8a")
        self.log.tag_config("sys", foreground="#b02a37", font=("Segoe UI", 9, "italic"))

        # Inline question area (packed only when a question is active).
        self.qframe = tk.Frame(root, bd=1, relief="solid", bg="#fff7e6")
        self.qlabel = tk.Label(
            self.qframe, text="", wraplength=650, justify="left",
            font=("Segoe UI", 10, "bold"), fg="#8a5a00", bg="#fff7e6",
        )
        self.qlabel.pack(anchor="w", padx=8, pady=(6, 4))
        self.qbody = tk.Frame(self.qframe, bg="#fff7e6")
        self.qbody.pack(fill="x", padx=8)
        self.qbtns = tk.Frame(self.qframe, bg="#fff7e6")
        self.qbtns.pack(fill="x", padx=8, pady=6)
        self.qsubmit = tk.Button(self.qbtns, text="Submit", command=lambda: self._submit_answer(True))
        self.qsubmit.pack(side="right")
        self.qcancel = tk.Button(self.qbtns, text="Cancel", command=lambda: self._submit_answer(False))

        bottom = tk.Frame(root)
        bottom.pack(fill="x", padx=10, pady=8)
        self.entry = tk.Entry(bottom, font=("Segoe UI", 11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.entry.bind("<Return>", lambda _e: self._on_enter())
        self.run_btn = tk.Button(bottom, text="Run", width=7, command=self._run_task)
        self.run_btn.pack(side="left", padx=(6, 0))
        self.send_btn = tk.Button(bottom, text="Send", width=7,
                                  command=self._send_interject, state="disabled")
        self.send_btn.pack(side="left", padx=(6, 0))
        self.stop_btn = tk.Button(bottom, text="Stop", width=6,
                                  command=self._stop_task, state="disabled")
        self.stop_btn.pack(side="left", padx=(6, 0))
        tk.Label(
            root, fg="#666", font=("Segoe UI", 8), anchor="w",
            text="Idle: type a task \u2192 Run.   While running: type a correction \u2192 "
                 "Send (redirects the agent), or Stop to abort.",
        ).pack(fill="x", padx=10, pady=(0, 6))

        self._append("Ready. Type a task below and press Run.\n", "sys")
        self.entry.focus_set()

        bridge.ensure_dirs()
        bridge.clear()
        try:
            if bridge.LOG_FILE.exists():
                self._log_offset = bridge.LOG_FILE.stat().st_size
        except OSError:
            pass

        self.root.after(1000, self._heartbeat)
        self.root.after(150, self._poll_bridge)
        self.root.after(120, self._drain_output)
        self._apply_topmost()
        self._start_server()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- persistent action server (speed) ----
    def _start_server(self) -> None:
        """Launch the warm hands-&-eyes server so cc.py actions are fast."""
        try:
            exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(exe):
                exe = sys.executable
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._server_proc = subprocess.Popen(
                [exe, CC, "serve"], cwd=_SKILL_DIR,
                env={**os.environ, "CC_BRIDGE_DIR": str(bridge.BRIDGE_DIR)},
                creationflags=flags,
            )
            self._append("[action server started \u2014 faster screenshots/clicks]\n", "sys")
        except Exception as exc:
            self._server_proc = None
            self._append(f"[action server unavailable: {exc}]\n", "sys")

    # ---- log helper ----
    def _append(self, text: str, tag: str = "engine") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text, tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _raise_window(self, steal_focus: bool = True) -> None:
        """Bring the window to the front so the user notices it.

        Uses a brief -topmost toggle (reliable from a background app on Windows)
        plus a bell and taskbar flash, then releases topmost so it isn't stuck
        permanently above everything.
        """
        try:
            if self.root.state() == "iconic":
                self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.lift()
            if steal_focus:
                self.root.focus_force()
            self.root.bell()
            self.root.after(1500, lambda: self._drop_topmost())
        except Exception:
            pass
        self._flash_taskbar()

    def _toggle_compact(self) -> None:
        """Shrink to a small always-usable panel, or restore the full window."""
        if self.compact.get():
            g = self.root.geometry()
            if "x" in g and not g.startswith("1x1"):
                self._normal_geom = g
            self.root.geometry(self._compact_geom)
        else:
            self.root.geometry(self._normal_geom)

    def _apply_topmost(self) -> None:
        """Apply the always-on-top setting and tell cc.py whether to manage us."""
        on = self.always_on_top.get()
        try:
            self.root.attributes("-topmost", on)
            if on:
                self.root.lift()
        except Exception:
            pass
        try:
            bridge.set_manage(on)
        except Exception:
            pass

    def _drop_topmost(self) -> None:
        # Only drop if the user hasn't asked us to stay on top.
        if self.always_on_top.get():
            return
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass

    def _flash_taskbar(self) -> None:
        """Flash the taskbar button (Windows) for attention if not focused."""
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()

            class FLASHWINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.UINT),
                    ("hwnd", wintypes.HWND),
                    ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT),
                    ("dwTimeout", wintypes.DWORD),
                ]

            FLASHW_ALL, FLASHW_TIMERNOFG = 0x3, 0xC
            info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd,
                              FLASHW_ALL | FLASHW_TIMERNOFG, 5, 0)
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass

    # ---- heartbeat / bridge polling ----
    def _heartbeat(self) -> None:
        bridge.touch_heartbeat()
        self.root.after(1000, self._heartbeat)

    def _poll_bridge(self) -> None:
        try:
            if bridge.LOG_FILE.exists():
                size = bridge.LOG_FILE.stat().st_size
                if size > self._log_offset:
                    with open(bridge.LOG_FILE, "r", encoding="utf-8") as f:
                        f.seek(self._log_offset)
                        data = f.read()
                    self._log_offset += len(data.encode("utf-8"))
                    for line in data.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            msg = rec.get("message", "")
                            alert = bool(rec.get("alert"))
                            prefix = "\u26a0\ufe0f " if alert else "\U0001f916 "
                            self._append(prefix + msg + "\n", "ask" if alert else "agent")
                            self._record(msg, kind="alert" if alert else "progress")
                            if alert:
                                self._last_summary = msg
                                self._raise_window(steal_focus=False)
                        except Exception:
                            self._append(line + "\n", "agent")
        except OSError:
            pass

        for req in bridge.poll_requests():
            self._pending.append(req)
        if self._showing is None and self._pending:
            self._show_question(self._pending.pop(0))

        self.root.after(150, self._poll_bridge)

    # ---- question rendering ----
    def _show_question(self, req: dict) -> None:
        self._showing = req["id"]
        self._q_kind = req.get("kind", "ask")
        for w in self._qwidgets:
            w.destroy()
        self._qwidgets.clear()
        self._q_var = tk.StringVar(value="")
        self._q_entry = None
        self.qcancel.pack_forget()

        if self._q_kind == "confirm":
            summary = req.get("summary", "")
            self.qlabel.config(text="\u26a0 Approval needed:\n" + summary)
            self._append("\u2753 Approval needed: " + summary + "\n", "ask")
            self.qsubmit.config(text="Approve")
            self.qcancel.pack(side="right", padx=(0, 6))
        else:
            q = req.get("question", "")
            self.qlabel.config(text=q)
            self._append("\u2753 " + q + "\n", "ask")
            self.qsubmit.config(text="Submit")
            choices = req.get("choices")
            sensitive = req.get("sensitive")
            allow_freeform = req.get("allow_freeform")
            if choices:
                for c in choices:
                    rb = tk.Radiobutton(
                        self.qbody, text=c, value=c, variable=self._q_var,
                        wraplength=620, justify="left", anchor="w",
                        font=("Segoe UI", 10), bg="#fff7e6",
                    )
                    rb.pack(anchor="w")
                    self._qwidgets.append(rb)
                self._q_var.set(choices[0])
                if allow_freeform:
                    row = tk.Frame(self.qbody, bg="#fff7e6")
                    row.pack(anchor="w", fill="x")
                    rb = tk.Radiobutton(row, text="Other:", value=self._q_other,
                                        variable=self._q_var, bg="#fff7e6", font=("Segoe UI", 10))
                    rb.pack(side="left")
                    ent = tk.Entry(row, font=("Segoe UI", 10))
                    ent.pack(side="left", fill="x", expand=True)
                    ent.bind("<FocusIn>", lambda _e: self._q_var.set(self._q_other))
                    self._q_entry = ent
                    self._qwidgets.extend([row, rb, ent])
            else:
                ent = tk.Entry(self.qbody, font=("Segoe UI", 11),
                               show="*" if sensitive else "")
                ent.pack(fill="x")
                ent.focus_set()
                ent.bind("<Return>", lambda _e: self._submit_answer(True))
                self._q_entry = ent
                self._qwidgets.append(ent)

        self.qframe.pack(fill="x", padx=10, pady=(0, 4), before=self.entry.master)
        self.qsubmit.config(state="normal")
        if self.compact.get():
            # Grow just enough to show and answer the question.
            self.root.geometry("460x360")
        self._raise_window()
        self._alert_sound()

    def _alert_sound(self) -> None:
        """Play a ~2s audible alert (if 'Sound alert' is on) so an away-from-screen
        user knows input is needed. Uses Windows winsound; runs in a thread."""
        if not self.sound_ask.get():
            return

        def beep() -> None:
            try:
                import time as _t
                import winsound

                # System-alias sounds play through the normal audio device, so
                # they're audible locally and over Remote Desktop (unlike Beep()).
                end = _t.time() + 2.0
                while _t.time() < end:
                    winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS)
                    _t.sleep(0.12)
            except Exception:
                try:
                    import winsound

                    for _ in range(4):
                        winsound.MessageBeep()
                except Exception:
                    pass

        threading.Thread(target=beep, daemon=True).start()

    def _record(self, text: str, kind: str = "progress") -> None:
        """Append a note to the current task's memory folder (best-effort)."""
        if not self._task_dir or not text:
            return
        try:
            memory.add_note(text, kind=kind, task_dir=self._task_dir)
        except Exception:
            pass

    def _submit_answer(self, ok: bool) -> None:
        if self._showing is None:
            return
        rid = self._showing
        if self._q_kind == "confirm":
            data = {"approved": bool(ok)}
            self._append("\U0001f9d1 " + ("Approved" if ok else "Cancelled") + "\n", "you")
            self._record("Approval: " + ("APPROVED" if ok else "DENIED"), kind="answer")
        else:
            sel = self._q_var.get() if self._q_var else ""
            if self._q_entry is not None and (sel == self._q_other or sel == ""):
                ans = self._q_entry.get().strip()
            else:
                ans = sel
            data = {"answer": ans}
            self._append("\U0001f9d1 " + ans + "\n", "you")
            self._record("Q: " + self.qlabel.cget("text") + "  A: " + ans, kind="answer")

        bridge.write_response(rid, data)
        self.qframe.pack_forget()
        self._showing = None
        for w in self._qwidgets:
            w.destroy()
        self._qwidgets.clear()
        if self._pending:
            self.root.after(50, lambda: self._show_question(self._pending.pop(0)))
        elif self.compact.get():
            self.root.after(50, lambda: self.root.geometry(self._compact_geom))

    # ---- task engine ----
    def _on_enter(self) -> None:
        if self.proc is not None:
            self._send_interject()
        else:
            self._run_task()

    def _send_interject(self) -> None:
        """Redirect the running agent with a new instruction (keeps context)."""
        text = self.entry.get().strip()
        if not text or self.proc is None:
            return
        self.entry.delete(0, "end")
        bridge.set_interject(text)
        self._append("\U0001f9d1\u27a1 (to agent): " + text + "\n", "you")
        self._record("User redirected mid-task: " + text, kind="answer")

    def _run_task(self) -> None:
        task = self.entry.get().strip()
        if not task or self.proc is not None:
            return
        self.entry.delete(0, "end")
        self._append("\U0001f9d1 You: " + task + "\n", "you")
        self._stopping = False

        # Start recording this task's memory folder.
        self._task_dir = None
        self._last_summary = ""
        try:
            meta = memory.start_task(task)
            self._task_dir = meta["dir"]
            self._append(f"[memory: {meta['id']}  ({', '.join(meta['tags'])})]\n", "sys")
        except Exception as exc:
            self._append(f"[memory unavailable: {exc}]\n", "sys")

        prompt = PROMPT_TEMPLATE.format(cc=CC, task=task)
        env = dict(os.environ)
        env["CC_BRIDGE_DIR"] = str(bridge.BRIDGE_DIR)
        env["CC_MEMORY_DIR"] = str(memory.ROOT)
        if self._task_dir:
            env["CC_TASK_DIR"] = str(self._task_dir)
        try:
            self.proc = subprocess.Popen(
                ["copilot", "-p", prompt, "--allow-all"],
                cwd=_SKILL_DIR, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except FileNotFoundError:
            self._append("[error] 'copilot' CLI not found on PATH. Install GitHub Copilot CLI.\n", "sys")
            self.proc = None
            memory.finish_task("Engine (copilot CLI) not found.", status="failed", task_dir=self._task_dir)
            return
        self.run_btn.config(state="disabled")
        self.send_btn.config(state="normal")
        self.stop_btn.config(state="normal")
        self._append("[engine started]\n", "sys")
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self.out_q.put(_ANSI.sub("", line.rstrip("\n")))
        try:
            proc.stdout.close()
        except Exception:
            pass
        rc = proc.wait()
        self.out_q.put("\x00DONE%d" % rc)

    def _drain_output(self) -> None:
        try:
            while True:
                line = self.out_q.get_nowait()
                if line.startswith("\x00DONE"):
                    rc = line[5:]
                    self._append("[engine finished, exit %s]\n" % rc, "sys")
                    self.proc = None
                    self.run_btn.config(state="normal")
                    self.send_btn.config(state="disabled")
                    self.stop_btn.config(state="disabled")
                    if self._task_dir:
                        if self._stopping:
                            status = "cancelled"
                        else:
                            status = "done" if rc == "0" else "failed"
                        summary = self._last_summary or f"(engine exited {rc})"
                        try:
                            memory.finish_task(summary, status=status, task_dir=self._task_dir)
                            self._append(f"[memory saved: {status}]\n", "sys")
                        except Exception:
                            pass
                        self._task_dir = None
                    self._stopping = False
                    self._raise_window(steal_focus=False)
                elif line.strip():
                    self._append("\u00b7 " + line + "\n", "engine")
        except queue.Empty:
            pass
        self.root.after(120, self._drain_output)

    def _stop_task(self) -> None:
        if self.proc is not None:
            self._stopping = True
            try:
                self.proc.terminate()
            except Exception:
                pass
            self._append("[stopping engine\u2026]\n", "sys")

    def _on_close(self) -> None:
        self._stop_task()
        if self._task_dir:
            try:
                memory.finish_task("Window closed before completion.",
                                   status="cancelled", task_dir=self._task_dir)
            except Exception:
                pass
        if self._server_proc is not None:
            try:
                self._server_proc.terminate()
            except Exception:
                pass
        for f in (bridge.HEARTBEAT, bridge.MANAGE_FLAG):
            try:
                f.unlink()
            except OSError:
                pass
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    ControlPanel(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
