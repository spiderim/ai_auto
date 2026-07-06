#!/usr/bin/env python
"""cc.py — the "hands & eyes" CLI for Mode B.

Mode B lets the agent you're ALREADY running in a terminal (Copilot / Claude /
etc.) drive this computer — no separate Anthropic API key needed. The agent is
the brain; this CLI is its hands and eyes.

The loop the driving agent follows:

    python cc.py screenshot          # -> saves an image + prints its path/size
    (open/view that image to SEE the screen)
    python cc.py focus "Notepad"     # act...
    python cc.py type "hello world"
    python cc.py click 640 400
    python cc.py key "ctrl+s"
    python cc.py screenshot          # verify, repeat

Coordinates for click/move/drag are in the space of the LAST screenshot's
`image_size`; this CLI scales them to real screen pixels automatically.

Everything is path-independent, so it can be launched from any directory.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

# Make the skill importable regardless of the current working directory.
_SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
if _SKILL_DIR not in sys.path:
    sys.path.insert(0, _SKILL_DIR)

# Print unicode window titles safely on cp1252 consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import contextlib  # noqa: E402
import ctypes  # noqa: E402

from tools import bridge  # noqa: E402  (light: stdlib only)

# Heavy libraries (pyautogui ~0.5s, mss, PIL, tkinter) are imported LAZILY so
# that light commands (log/ask/mem-*) and the thin forwarding client never pay
# for them. A persistent `serve` process imports them once and executes actions
# over a loopback socket, cutting per-action latency from ~750ms to ~150ms.
_PG = None


def _pg():
    """Import + configure pyautogui once per process; return the module."""
    global _PG
    if _PG is None:
        import pyautogui as p
        from tools.computer import _set_dpi_awareness

        _set_dpi_awareness()
        p.FAILSAFE = True
        p.PAUSE = 0.1
        _PG = p
    return _PG


_STATE = os.path.join(tempfile.gettempdir(), "cc_state.json")
_DEFAULT_SHOT = os.path.join(tempfile.gettempdir(), "cc_screen.jpg")
# Scroll defaults (all overridable per-call and via env): notches per step,
# number of steps, and the pause between steps for smooth, controllable scrolling.
SCROLL_STEP_NOTCHES = int(os.environ.get("CC_SCROLL_STEP", "3"))
SCROLL_TIMES = int(os.environ.get("CC_SCROLL_TIMES", "5"))
SCROLL_GAP = float(os.environ.get("CC_SCROLL_GAP", "0.3"))

# --- persistent action server (speed) --------------------------------------
SERVER_INFO = bridge.BRIDGE_DIR / "server.json"
SERVER_TTL = 5.0
# True only inside the long-lived `serve` process (where the browser persists).
SERVER_MODE = False
# Commands worth forwarding to the warm server (they need heavy libs / state).
_FORWARD_CMDS = {
    "screenshot", "click", "move", "drag", "scroll", "type", "key",
    "open", "focus", "windows", "launch", "browser",
}

# --- GUI "step aside" (Win32) ----------------------------------------------
# When the control panel is always-on-top, hide it for the instant we capture
# the screen or move the mouse, so it neither appears in screenshots nor
# intercepts clicks. Restored (still on top, without stealing focus) after.
_GUI_TITLE = "Computer Control"
_SW_HIDE, _SW_SHOWNA = 0, 8
_HWND_TOPMOST = -1
_SWP_NOMOVE, _SWP_NOSIZE, _SWP_NOACTIVATE = 0x2, 0x1, 0x10
# Commands during which the always-on-top GUI must step aside.
_ASIDE_CMDS = {"screenshot", "click", "move", "drag", "scroll", "open"}


def _find_gui_hwnd() -> int:
    try:
        return ctypes.windll.user32.FindWindowW(None, _GUI_TITLE) or 0
    except Exception:
        return 0


@contextlib.contextmanager
def _gui_aside():
    hwnd = 0
    try:
        manage = bridge.should_manage()
    except Exception:
        manage = False
    if manage:
        hwnd = _find_gui_hwnd()
        if hwnd:
            try:
                ctypes.windll.user32.ShowWindow(hwnd, _SW_HIDE)
                time.sleep(0.05)  # let Windows repaint what was underneath
            except Exception:
                hwnd = 0
    try:
        yield
    finally:
        if hwnd:
            try:
                ctypes.windll.user32.ShowWindow(hwnd, _SW_SHOWNA)
                ctypes.windll.user32.SetWindowPos(
                    hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                    _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
                )
            except Exception:
                pass



def _save_state(d: dict) -> None:
    try:
        with open(_STATE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def _load_state() -> dict:
    try:
        with open(_STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _to_screen(x: float, y: float, space: str) -> tuple[int, int]:
    """Scale coords from last-screenshot image space to real screen pixels."""
    st = _load_state()
    if space == "screen" or not st:
        return int(round(x)), int(round(y))
    iw, ih = st.get("image_size", [0, 0])
    rw, rh = st.get("screen_size", [0, 0])
    if not iw or not ih:
        return int(round(x)), int(round(y))
    return int(round(x * rw / iw)), int(round(y * rh / ih))


# --- commands --------------------------------------------------------------
def cmd_screenshot(args) -> int:
    from PIL import Image
    import mss

    with mss.MSS() as sct:
        raw = sct.grab(sct.monitors[1])
        img = Image.frombytes("RGB", raw.size, raw.rgb)
    rw, rh = img.width, img.height
    mw = args.max_width
    if mw and rw > mw:
        img = img.resize((mw, round(rh * mw / rw)), Image.LANCZOS)
    out = os.path.abspath(args.out or _DEFAULT_SHOT)
    img.save(out, "JPEG", quality=70)
    info = {"path": out, "image_size": [img.width, img.height], "screen_size": [rw, rh]}
    _save_state(info)
    print(json.dumps(info))
    return 0


def cmd_click(args) -> int:
    pg = _pg()
    x, y = _to_screen(args.x, args.y, args.space)
    pg.moveTo(x, y)
    clicks = 3 if args.triple else 2 if args.double else 1
    pg.click(x, y, clicks=clicks, interval=0.05, button=args.button)
    print(f"clicked {args.button} x{clicks} at screen ({x},{y})")
    return 0


def cmd_move(args) -> int:
    pg = _pg()
    x, y = _to_screen(args.x, args.y, args.space)
    pg.moveTo(x, y)
    print(f"moved to screen ({x},{y})")
    return 0


def cmd_drag(args) -> int:
    pg = _pg()
    x1, y1 = _to_screen(args.x1, args.y1, args.space)
    x2, y2 = _to_screen(args.x2, args.y2, args.space)
    pg.moveTo(x1, y1)
    pg.dragTo(x2, y2, duration=0.4, button="left")
    print(f"dragged ({x1},{y1}) -> ({x2},{y2})")
    return 0


def cmd_scroll(args) -> int:
    pg = _pg()
    # Put the cursor over the content so the wheel scrolls the right thing.
    if args.at:
        pg.moveTo(*_to_screen(args.at[0], args.at[1], args.space))
    else:
        w, h = pg.size()
        pg.moveTo(w // 2, h // 2)

    step = args.step if args.step and args.step > 0 else SCROLL_STEP_NOTCHES
    delta = step if args.dir == "up" else -step
    gap = max(0.0, args.gap)
    done = 0
    if args.duration and args.duration > 0:
        end = time.time() + min(args.duration, 60.0)
        while time.time() < end:
            pg.scroll(delta)
            done += 1
            if time.time() < end:
                time.sleep(gap)
    else:
        times = max(1, min(args.times, 300))
        for i in range(times):
            pg.scroll(delta)
            done += 1
            if i < times - 1:
                time.sleep(gap)
    print(f"scrolled {args.dir}: {done} step(s) x {step} notches, gap {gap}s")
    return 0


def cmd_type(args) -> int:
    from tools.computer import set_clipboard

    pg = _pg()
    text = args.text
    if not args.raw and text and set_clipboard(text):
        time.sleep(0.05)
        pg.hotkey("ctrl", "v")
        how = "pasted"
    else:
        pg.write(text, interval=0.02)
        how = "typed"
    if args.enter:
        pg.press("enter")
    print(f"{how} {len(text)} chars" + (" + enter" if args.enter else ""))
    return 0


def cmd_key(args) -> int:
    from tools.computer import parse_key_combo

    pg = _pg()
    keys = parse_key_combo(args.combo)
    if len(keys) == 1:
        pg.press(keys[0])
    elif keys:
        pg.hotkey(*keys)
    print("pressed", "+".join(keys))
    return 0


def cmd_open(args) -> int:
    from tools.computer import set_clipboard

    pg = _pg()
    pg.press("win")
    time.sleep(0.7)
    if set_clipboard(args.query):
        time.sleep(0.05)
        pg.hotkey("ctrl", "v")
    else:
        pg.write(args.query, interval=0.03)
    time.sleep(0.9)
    pg.press("enter")
    time.sleep(1.5)
    print(f"opened via search: {args.query}")
    return 0


def cmd_launch(args) -> int:
    try:
        subprocess.Popen(args.command, shell=True)
        time.sleep(1.5)
        print(f"launched: {args.command}")
        return 0
    except Exception as exc:
        print(f"launch failed: {exc}", file=sys.stderr)
        return 1


def cmd_focus(args) -> int:
    import pygetwindow as gw

    wins = gw.getWindowsWithTitle(args.title)
    if not wins:
        print(f"no window matching: {args.title}", file=sys.stderr)
        return 1
    w = wins[0]
    try:
        if w.isMinimized:
            w.restore()
        w.activate()
        if not args.no_maximize:
            w.maximize()
    except Exception as exc:
        print(f"activate warning: {exc!r}")
    time.sleep(0.6)
    print(f"focused: {w.title}")
    return 0


def cmd_windows(args) -> int:
    import pygetwindow as gw

    titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
    for t in titles:
        print("-", t)
    return 0


def cmd_wait(args) -> int:
    time.sleep(max(0.0, args.seconds))
    print(f"waited {args.seconds}s")
    return 0


# --- user interaction (routes to the GUI if running, else a popup) ---------
def cmd_ask(args) -> int:
    choices = args.choices or None
    if bridge.gui_alive():
        res = bridge.send_request(
            "ask",
            {
                "question": args.question,
                "choices": choices,
                "sensitive": bool(args.sensitive),
                "allow_freeform": bool(args.allow_freeform),
            },
            timeout=args.timeout,
        )
        if res is not None:
            print(json.dumps({"answer": res.get("answer", "")}))
            return 0
    # Fallback: no GUI (or it went away) -> standalone popup / console.
    from tools import gui

    answer = gui.ask_user(
        args.question,
        sensitive=bool(args.sensitive),
        choices=choices,
        allow_freeform=bool(args.allow_freeform),
    )
    print(json.dumps({"answer": answer}))
    return 0


def cmd_confirm(args) -> int:
    if bridge.gui_alive():
        res = bridge.send_request("confirm", {"summary": args.summary}, timeout=args.timeout)
        if res is not None:
            print(json.dumps({"approved": bool(res.get("approved"))}))
            return 0
    from tools import gui

    approved = gui.confirm_action(args.summary)
    print(json.dumps({"approved": bool(approved)}))
    return 0


def cmd_log(args) -> int:
    if bridge.gui_alive():
        bridge.log(args.message, role=args.role, alert=bool(args.alert))
    else:
        print(args.message)
    return 0


# --- task memory (persisted under C:\memory-copilot) -----------------------
def cmd_mem_start(args) -> int:
    from tools import memory

    meta = memory.start_task(args.task, tags=args.tags or None)
    print(json.dumps({"id": meta["id"], "dir": meta["dir"], "tags": meta["tags"]}))
    return 0


def cmd_mem_note(args) -> int:
    from tools import memory

    ok = memory.add_note(args.note, kind=args.kind, task_dir=args.dir)
    print(json.dumps({"recorded": bool(ok)}))
    return 0


def cmd_mem_finish(args) -> int:
    from tools import memory

    ok = memory.finish_task(summary=args.summary, status=args.status, task_dir=args.dir)
    print(json.dumps({"finished": bool(ok)}))
    return 0


def cmd_mem_list(args) -> int:
    from tools import memory

    print(json.dumps(memory.list_tasks(limit=args.limit), ensure_ascii=False))
    return 0


def cmd_mem_search(args) -> int:
    from tools import memory

    hits = memory.search(args.query, limit=args.limit)
    print(json.dumps(hits, ensure_ascii=False))
    return 0


# --- automated (Playwright) browser ----------------------------------------
def cmd_browser(args) -> int:
    """DOM-level web automation — no screenshots needed. Runs in the persistent
    server so one browser stays open (and logged in) across commands."""
    if not SERVER_MODE:
        print("The automated browser needs the persistent action server, which the "
              "GUI starts automatically. (Or run `python cc.py serve` in another "
              "terminal.)", file=sys.stderr)
        return 2
    from tools import browser as B

    ok, msg = B.available()
    if not ok:
        print(msg, file=sys.stderr)
        return 2
    a = args.action
    try:
        if a == "goto":
            print(B.goto(args.url or ""))
        elif a == "read":
            print(B.read())
        elif a == "links":
            print(B.links())
        elif a == "click":
            print(B.click(text=args.text, selector=args.selector, role=args.role, name=args.name))
        elif a == "fill":
            print(B.fill(selector=args.selector, text=args.text or "",
                         label=args.label, placeholder=args.placeholder))
        elif a == "search":
            print(B.search(args.text or ""))
        elif a == "wait":
            print(B.wait(text=args.text, selector=args.selector, timeout=args.timeout or 15.0))
        elif a == "get":
            print(B.get(args.selector or ""))
        elif a == "extract":
            print(B.extract(args.selector or "", limit=args.limit or 50))
        elif a == "url":
            print(B.url())
        elif a == "scroll":
            print(B.scroll(to=args.to, px=args.px or 0))
        elif a == "eval":
            print(B.eval_js(args.js or ""))
        elif a == "press":
            print(B.press(args.key or "Enter"))
        elif a == "back":
            print(B.back())
        elif a == "screenshot":
            print(B.screenshot(args.out))
        elif a == "close":
            print(B.close())
        else:
            print(f"unknown browser action: {a}", file=sys.stderr)
            return 2
    except Exception as exc:
        print(f"browser {a} failed: {exc}", file=sys.stderr)
        return 1
    return 0


# --- CLI wiring ------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cc", description="Hands & eyes CLI (Mode B).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("screenshot", help="Capture the screen to an image file.")
    s.add_argument("--out", default=None, help=f"Output path (default: {_DEFAULT_SHOT})")
    s.add_argument("--max-width", type=int, default=1536, help="Downscale width (0 = native).")
    s.set_defaults(func=cmd_screenshot)

    def _coord_opts(sp):
        sp.add_argument("--space", choices=["image", "screen"], default="image",
                        help="Coordinate space (default: last screenshot's image space).")

    c = sub.add_parser("click", help="Click at coordinates.")
    c.add_argument("x", type=float); c.add_argument("y", type=float)
    c.add_argument("--button", choices=["left", "right", "middle"], default="left")
    c.add_argument("--double", action="store_true")
    c.add_argument("--triple", action="store_true")
    _coord_opts(c); c.set_defaults(func=cmd_click)

    m = sub.add_parser("move", help="Move the cursor.")
    m.add_argument("x", type=float); m.add_argument("y", type=float)
    _coord_opts(m); m.set_defaults(func=cmd_move)

    d = sub.add_parser("drag", help="Drag from (x1,y1) to (x2,y2).")
    for a in ("x1", "y1", "x2", "y2"):
        d.add_argument(a, type=float)
    _coord_opts(d); d.set_defaults(func=cmd_drag)

    sc = sub.add_parser("scroll", help="Scroll the content under the cursor in controllable steps.")
    sc.add_argument("--dir", choices=["up", "down"], default="down")
    sc.add_argument("--step", type=int, default=0,
                    help=f"Wheel notches per scroll step / 'length in one go' (default {SCROLL_STEP_NOTCHES}).")
    sc.add_argument("--times", type=int, default=SCROLL_TIMES,
                    help=f"How many scroll steps to do (default {SCROLL_TIMES}).")
    sc.add_argument("--gap", type=float, default=SCROLL_GAP,
                    help=f"Seconds to wait between steps (default {SCROLL_GAP}).")
    sc.add_argument("--duration", type=float, default=0.0,
                    help="Keep scrolling for this many seconds (overrides --times).")
    sc.add_argument("--at", type=float, nargs=2, metavar=("X", "Y"),
                    help="Scroll at these image coords (default: screen centre).")
    _coord_opts(sc); sc.set_defaults(func=cmd_scroll)

    t = sub.add_parser("type", help="Type text (clipboard paste by default).")
    t.add_argument("text")
    t.add_argument("--enter", action="store_true", help="Press Enter afterwards.")
    t.add_argument("--raw", action="store_true", help="Force char-by-char typing.")
    t.set_defaults(func=cmd_type)

    k = sub.add_parser("key", help='Press a key/combo, e.g. "ctrl+s".')
    k.add_argument("combo"); k.set_defaults(func=cmd_key)

    o = sub.add_parser("open", help="Open an app via Windows search.")
    o.add_argument("query"); o.set_defaults(func=cmd_open)

    la = sub.add_parser("launch", help="Launch a command/exe directly.")
    la.add_argument("command"); la.set_defaults(func=cmd_launch)

    f = sub.add_parser("focus", help="Bring a window to the front (title match).")
    f.add_argument("title")
    f.add_argument("--no-maximize", action="store_true")
    f.set_defaults(func=cmd_focus)

    sub.add_parser("windows", help="List open window titles.").set_defaults(func=cmd_windows)

    w = sub.add_parser("wait", help="Sleep N seconds.")
    w.add_argument("seconds", type=float); w.set_defaults(func=cmd_wait)

    ask = sub.add_parser("ask", help="Ask the user a question (GUI popup).")
    ask.add_argument("question")
    ask.add_argument("--choices", nargs="*", default=None,
                     help="Offer radio-button choices instead of free text.")
    ask.add_argument("--allow-freeform", action="store_true",
                     help="With --choices, also allow an 'Other' typed answer.")
    ask.add_argument("--sensitive", action="store_true", help="Mask input (password).")
    ask.add_argument("--timeout", type=float, default=600.0)
    ask.set_defaults(func=cmd_ask)

    cf = sub.add_parser("confirm", help="Ask the user to approve a high-stakes action.")
    cf.add_argument("summary")
    cf.add_argument("--timeout", type=float, default=600.0)
    cf.set_defaults(func=cmd_confirm)

    lg = sub.add_parser("log", help="Post a progress/result message to the GUI.")
    lg.add_argument("message")
    lg.add_argument("--role", default="agent")
    lg.add_argument("--alert", action="store_true",
                    help="Mark as important: pops the GUI to the front.")
    lg.set_defaults(func=cmd_log)

    ms = sub.add_parser("mem-start", help="Begin recording a task's memory folder.")
    ms.add_argument("task")
    ms.add_argument("--tags", nargs="*", default=None, help="Override auto-detected kind tags.")
    ms.set_defaults(func=cmd_mem_start)

    mn = sub.add_parser("mem-note", help="Append a note to the active task's memory.")
    mn.add_argument("note")
    mn.add_argument("--kind", default="decision",
                    help="note kind: decision/progress/result/... (default: decision)")
    mn.add_argument("--dir", default=None, help="Task folder (else CC_TASK_DIR / .current).")
    mn.set_defaults(func=cmd_mem_note)

    mf = sub.add_parser("mem-finish", help="Finalize the active task's memory.")
    mf.add_argument("summary")
    mf.add_argument("--status", default="done", choices=["done", "failed", "cancelled"])
    mf.add_argument("--dir", default=None)
    mf.set_defaults(func=cmd_mem_finish)

    ml = sub.add_parser("mem-list", help="List recent tasks from the super index.")
    ml.add_argument("--limit", type=int, default=20)
    ml.set_defaults(func=cmd_mem_list)

    msr = sub.add_parser("mem-search", help="Search prior tasks (super index only).")
    msr.add_argument("query")
    msr.add_argument("--limit", type=int, default=10)
    msr.set_defaults(func=cmd_mem_search)

    br = sub.add_parser("browser", help="Automated (Playwright) web control — DOM-level, no screenshots.")
    br.add_argument("action", choices=["goto", "read", "links", "click", "fill", "search",
                                       "wait", "get", "extract", "url", "scroll", "eval",
                                       "press", "back", "screenshot", "close"])
    br.add_argument("--url", help="URL for goto.")
    br.add_argument("--text", help="Text to click / fill / search query.")
    br.add_argument("--selector", help="CSS selector for click/fill/get/extract/wait.")
    br.add_argument("--role", help="ARIA role for click (e.g. button, link).")
    br.add_argument("--name", help="Accessible name for a --role click.")
    br.add_argument("--label", help="Fill the field with this visible label.")
    br.add_argument("--placeholder", help="Fill the field with this placeholder.")
    br.add_argument("--key", help="Key to press (default Enter).")
    br.add_argument("--to", choices=["top", "bottom"], help="Page scroll target.")
    br.add_argument("--px", type=int, default=0, help="Page scroll pixels (default 800).")
    br.add_argument("--js", help="JavaScript to eval in the page.")
    br.add_argument("--timeout", type=float, default=0.0, help="Seconds for wait (default 15).")
    br.add_argument("--limit", type=int, default=0, help="Max items for extract (default 50).")
    br.add_argument("--out", help="Path for screenshot.")
    br.set_defaults(func=cmd_browser)

    sv = sub.add_parser("serve", help="Run the persistent action server (speed).")
    sv.set_defaults(func=lambda a: 0)  # handled specially in main()

    return p


# --- persistent server + thin client (speed) -------------------------------
def _write_server_info(port: int) -> None:
    bridge.ensure_dirs()
    try:
        SERVER_INFO.write_text(
            json.dumps({"port": port, "pid": os.getpid(), "ts": time.time()}),
            encoding="utf-8",
        )
    except OSError:
        pass


def _server_info() -> dict | None:
    try:
        info = json.loads(SERVER_INFO.read_text(encoding="utf-8"))
        if (time.time() - float(info.get("ts", 0))) < SERVER_TTL:
            return info
    except Exception:
        pass
    return None


def _dispatch(args) -> int:
    """Run one parsed command, hiding the always-on-top GUI when needed.

    Before doing anything, honour a pending user interjection: surface it to the
    agent and skip the physical action so a wrong move isn't carried out.
    """
    if args.cmd in _FORWARD_CMDS or args.cmd in ("ask", "confirm"):
        try:
            msgs = bridge.take_interject()
        except Exception:
            msgs = []
        if msgs:
            for m in msgs:
                print(f"USER INTERRUPTION: {m}")
            print("(Stop your current approach and follow the instruction above. "
                  "The pending action was NOT performed. Re-check the screen if needed.)")
            return 0
    aside = _gui_aside() if args.cmd in _ASIDE_CMDS else contextlib.nullcontext()
    with aside:
        return args.func(args)


def _run_argv_capture(parser, argv) -> tuple[str, int]:
    import io

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return ("bad arguments\n", 2)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            code = _dispatch(args)
        except Exception as exc:
            if type(exc).__name__ == "FailSafeException":
                return (buf.getvalue() + "Aborted by failsafe.\n", 130)
            return (buf.getvalue() + f"error: {exc}\n", 1)
    return (buf.getvalue(), code or 0)


def cmd_serve(args) -> int:
    global SERVER_MODE
    SERVER_MODE = True
    import socket

    _pg()  # warm pyautogui now
    try:
        import mss  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:
        pass

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    srv.settimeout(1.0)
    port = srv.getsockname()[1]
    _write_server_info(port)
    parser = build_parser()
    print(f"cc action server on 127.0.0.1:{port} (pid {os.getpid()})", flush=True)
    try:
        while True:
            _write_server_info(port)  # heartbeat
            try:
                conn, _addr = srv.accept()
            except socket.timeout:
                continue
            with conn:
                try:
                    conn.settimeout(300)
                    data = b""
                    while not data.endswith(b"\n"):
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                    req = json.loads(data.decode("utf-8"))
                    out, code = _run_argv_capture(parser, req.get("argv", []))
                    conn.sendall((json.dumps({"out": out, "code": code}) + "\n").encode("utf-8"))
                except Exception as exc:
                    with contextlib.suppress(Exception):
                        conn.sendall((json.dumps({"out": f"server error: {exc}\n", "code": 1}) + "\n").encode("utf-8"))
    except KeyboardInterrupt:
        pass
    finally:
        with contextlib.suppress(Exception):
            from tools import browser as _B

            _B.close()
        with contextlib.suppress(OSError):
            SERVER_INFO.unlink()
    return 0


def _forward_to_server(argv) -> tuple[bool, int]:
    info = _server_info()
    if not info:
        return (False, 0)
    try:
        import socket

        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=0.4) as s:
            s.sendall((json.dumps({"argv": argv}) + "\n").encode("utf-8"))
            s.settimeout(300)
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        resp = json.loads(data.decode("utf-8", "replace"))
        sys.stdout.write(resp.get("out", ""))
        return (True, int(resp.get("code", 0)))
    except Exception:
        return (False, 0)


def main(argv=None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw)

    if args.cmd == "serve":
        return cmd_serve(args)

    # Route heavy actions through the warm server when it's available.
    if args.cmd in _FORWARD_CMDS:
        handled, code = _forward_to_server(raw)
        if handled:
            return code

    try:
        return _dispatch(args)
    except Exception as exc:
        if type(exc).__name__ == "FailSafeException":
            print("Aborted by failsafe (mouse in a screen corner).", file=sys.stderr)
            return 130
        raise


if __name__ == "__main__":
    raise SystemExit(main())
