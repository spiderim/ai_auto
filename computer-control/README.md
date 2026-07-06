# computer-control

Let an AI **operate your real Windows PC** — mouse, keyboard and screen — to carry
out everyday GUI tasks from a single natural‑language instruction: open and drive
desktop apps, browse the web, fill forms, take notes, compare options, and (only
with your on‑screen approval) complete bookings or purchases.

It ships as an **agent skill** (a `SKILL.md` + Python scripts). You drive it from
a small **GUI control panel** whose "brain" is the **GitHub Copilot CLI** you
already have — so it needs **no separate API key**.

> **Platform:** Windows only (uses Windows DPI/Win32 APIs, `pyautogui`, `mss`).
> Run it on the machine you want to control (not over Remote Desktop).

---

## Quick start

```powershell
# 1. Clone
git clone https://github.com/<you>/computer-control.git
cd computer-control

# 2. Install Python deps (Python 3.10+)
pip install -r requirements.txt

# 3. Launch the GUI (uses your GitHub Copilot CLI — no API key)
pythonw control_panel.py           # or double-click launch-gui.cmd
```

A window opens. Type a task, press **Run**, and watch it work. It asks you
questions inline when unsure, and requires your approval before anything risky.

**Prerequisite:** the [GitHub Copilot CLI](https://docs.github.com/copilot/how-tos/set-up/install-copilot-cli)
installed and logged in (`copilot` on your PATH). That's the engine.

---

## Install as a Copilot skill (optional)

So Copilot loads it automatically when a task matches:

```powershell
# Personal skill (available in all your projects):
copilot skill add https://github.com/<you>/computer-control.git
#   ...or clone it straight into your skills folder:
git clone https://github.com/<you>/computer-control.git "$HOME\.copilot\skills\computer-control"
```

Then in a Copilot CLI session run `/skills reload`, check with
`/skills info computer-control`, and invoke with e.g.
*"Use the /computer-control skill to open Notepad and write my todo list."*

---

## Using the GUI

Type a task → **Run**. While it runs you can:

- **Send** — type a correction to redirect it mid‑task (it re‑plans without
  losing context, via a `USER INTERRUPTION`).
- **Stop** — abort the current task.
- **Compact** — shrink to a tiny always‑usable panel.
- **Stay on top** — keep the window visible; it auto‑steps‑aside for the instant
  it clicks so it never blocks the app.
- **Sound alert** — play a ~2‑second sound when input is needed (handy if you've
  stepped away).

It asks questions (free text or radio choices) and approvals **inline** in the
window, and requires approval before payments, bookings, messages, or anything
destructive.

---

## How it works

```
control_panel.py (GUI) ──spawns──▶ copilot -p "<task>" --allow-all   (background engine)
    ▲   │ task in / progress + questions out          │ uses this skill's cc.py
    │   ▼                                              ▼
  you ◀─ tools/bridge.py (files) ◀─ cc.py log/ask/confirm   cc.py screenshot/click/type/… ─▶ your PC
```

The engine prefers **direct methods** (file edits, shell, `code <folder>`) and
falls back to screenshot→click→type only when a GUI interaction is unavoidable.
A persistent `cc.py serve` process (auto‑started by the GUI) keeps the heavy
libraries loaded so each action is a fast local socket call.

The `cc.py` "hands & eyes" commands the engine uses (also runnable manually):

```powershell
python cc.py screenshot            # capture -> prints image path; view it to "see"
python cc.py open "notepad"
python cc.py type "hello" --enter
python cc.py click 640 400         # X,Y are in the last screenshot's coordinates
python cc.py scroll --dir down     # stepped, controllable scroll
python cc.py key "ctrl+s"
```

### Automated browser (fast, no screenshots)

For web tasks the engine asks whether you want the **automated (Playwright)
browser** or your **normal** Chrome. The automated browser drives the **DOM**
directly — navigate, read text, click/fill by text or selector — so it's much
faster and more reliable than the screenshot→click loop, and it stays logged in
across runs. One-time: `playwright install chromium`.

```powershell
python cc.py browser goto --url "wikipedia.org"
python cc.py browser read                         # visible page text
python cc.py browser links                        # clickable elements
python cc.py browser click --text "English"
python cc.py browser fill --selector "input[name=search]" --text "Alan Turing"
python cc.py browser press --key "Enter"
```

---

## Task memory

Every task run from the GUI is recorded under **`C:\memory-copilot\`**:

```
C:\memory-copilot\
├── INDEX.md      ← human "super index": every task, grouped by kind, with summaries
├── index.json    ← machine-readable index (fast search, no folder scanning)
└── <timestamp>_<slug>\   task.md · meta.json · log.jsonl
```

Look things up without opening each folder:
```powershell
python cc.py mem-search "outlook email"
python cc.py mem-list
```
Change the location with the `CC_MEMORY_DIR` environment variable.

---

## Safety

- **Mandatory approval** before payments, bookings, sending messages, accepting
  terms, or destructive actions.
- Passwords are only ever entered through a masked prompt and never echoed.
- **Failsafe:** slam the mouse into any screen corner to abort instantly.
- The agent ignores instructions embedded in web pages/screens that conflict with
  your request (prompt‑injection resistance).
- The engine runs with `--allow-all`, so only run tasks you trust; prefer a
  dedicated Windows account or VM for risky web automation.

---

## Configuration (environment variables)

| Variable | Default | Meaning |
|----------|---------|---------|
| `CC_MEMORY_DIR` | `C:\memory-copilot` | Where task memory is stored. |
| `CC_SCROLL_STEP` | `3` | Default wheel notches per scroll step. |
| `CC_SCROLL_TIMES` | `5` | Default number of scroll steps. |
| `CC_SCROLL_GAP` | `0.3` | Default seconds between scroll steps. |
| `CC_BROWSER_PROFILE` | `~/.computer-control/browser-profile` | Automated-browser profile (keeps logins). |
| `CC_BROWSER_CHANNEL` | `chrome` | Automated-browser engine; empty = bundled Chromium. |
| `CC_BROWSER_HEADLESS` | `0` | `1` runs the automated browser headless. |
| `CC_BRIDGE_DIR` | `%TEMP%\computer_control_bridge` | GUI ↔ engine bridge folder (advanced). |

---

## Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Skill manifest read by the host agent. |
| `control_panel.py` | The GUI (task input, live log, inline questions, memory). |
| `cc.py` | Hands‑&‑eyes CLI + action server + `browser` + `ask`/`confirm`/`log`/`mem-*`. |
| `tools/computer.py` | Windows input helpers (key translation, clipboard, DPI awareness). |
| `tools/browser.py` | Automated Playwright browser (persistent, DOM‑level, fast). |
| `tools/bridge.py` | GUI ↔ engine file bridge (questions, logs, interjections). |
| `tools/memory.py` | Task memory store + super index. |
| `tools/gui.py` | Fallback `ask_user` / `confirm_action` popup dialogs. |
| `launch-gui.cmd` | Convenience launcher for the GUI. |

---

## Limitations

- **Windows only**, and controls the **local** machine — Remote Desktop input
  redirection is not reliable for automation.
- Multi‑monitor targeting beyond the primary display isn't handled.
- Sites with heavy bot‑detection/captcha may block automation; the agent will ask
  you to step in.
