"""Windows input helpers used by ``cc.py``.

Small, dependency-light utilities:

* key-name translation (xdotool-style names -> pyautogui names),
* clipboard set (for reliable paste-based typing), and
* per-monitor DPI awareness (so click coordinates map to real pixels).

Kept free of heavy top-level imports so importing these helpers is cheap.
"""
from __future__ import annotations

# Key symbols an agent may emit (xdotool-style) mapped to pyautogui names.
_XDOTOOL_TO_PYAUTOGUI: dict[str, str] = {
    "return": "enter",
    "kp_enter": "enter",
    "enter": "enter",
    "tab": "tab",
    "space": "space",
    "backspace": "backspace",
    "delete": "delete",
    "kp_delete": "delete",
    "escape": "esc",
    "esc": "esc",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "home": "home",
    "end": "end",
    "page_up": "pageup",
    "prior": "pageup",
    "pageup": "pageup",
    "page_down": "pagedown",
    "next": "pagedown",
    "pagedown": "pagedown",
    "insert": "insert",
    "menu": "apps",
    "print": "printscreen",
    "sys_req": "printscreen",
    "caps_lock": "capslock",
    "num_lock": "numlock",
    "scroll_lock": "scrolllock",
    # Modifiers
    "control": "ctrl",
    "control_l": "ctrl",
    "control_r": "ctrl",
    "ctrl": "ctrl",
    "alt": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "meta": "alt",
    "shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    "super": "win",
    "super_l": "win",
    "super_r": "win",
    "win": "win",
    "cmd": "win",
    # Named punctuation -> literal character (pyautogui accepts the char).
    "minus": "-",
    "plus": "+",
    "equal": "=",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "backslash": "\\",
    "semicolon": ";",
    "apostrophe": "'",
    "grave": "`",
    "bracketleft": "[",
    "bracketright": "]",
    "exclam": "!",
    "at": "@",
    "numbersign": "#",
    "dollar": "$",
    "percent": "%",
    "asciicircum": "^",
    "ampersand": "&",
    "asterisk": "*",
    "parenleft": "(",
    "parenright": ")",
    "underscore": "_",
    "question": "?",
    "colon": ":",
    "less": "<",
    "greater": ">",
}


def translate_key(token: str) -> str:
    """Translate a single xdotool key token to a pyautogui key name."""
    t = token.strip()
    if not t:
        return t
    lowered = t.lower()
    if lowered in _XDOTOOL_TO_PYAUTOGUI:
        return _XDOTOOL_TO_PYAUTOGUI[lowered]
    # Function keys F1..F24
    if lowered.startswith("f") and lowered[1:].isdigit():
        return lowered
    # Single alphabetic char -> lowercase (modifiers carry the case).
    if len(t) == 1 and t.isalpha():
        return t.lower()
    # Otherwise assume it is already a valid pyautogui key / literal char.
    return lowered if len(t) > 1 else t


def parse_key_combo(text: str) -> list[str]:
    """Turn "ctrl+s" / "alt+Tab" into ["ctrl", "s"] pyautogui names."""
    if text is None:
        return []
    # Split on '+' but keep a lone '+' (e.g. the plus key) intact.
    parts = [p for p in text.replace("++", "+plus").split("+")]
    return [translate_key(p) for p in parts if p != ""]


def _set_dpi_awareness() -> None:
    """Opt into per-monitor DPI awareness so coordinates map to real pixels."""
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass  # Non-Windows or already set; harmless.


def set_clipboard(text: str) -> bool:
    """Put text on the Windows clipboard. Returns True on success.

    Used for reliable paste-based typing (per-character typing drops characters
    on fast/latency-heavy targets like the Windows 11 Notepad).
    """
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:
        pass
    try:
        import tkinter as tk

        r = tk.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update()  # flush to the OS clipboard
        r.destroy()
        return True
    except Exception:
        return False
