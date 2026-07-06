"""Human-in-the-loop GUI, exposed to Claude as the ``ask_user`` and
``confirm_action`` tools.

* ``ask_user``       - pops a question box with a text field and returns the
                       typed answer. Set ``sensitive=True`` to mask input
                       (passwords) with asterisks.
* ``confirm_action`` - pops an Approve / Cancel dialog and returns a bool. Used
                       as the mandatory gate before payments, credential entry
                       and other high-stakes actions.

Both fall back to console prompts if Tkinter is unavailable, so the skill still
works headlessly.
"""
from __future__ import annotations

try:
    import tkinter as tk

    _TK_OK = True
    _TK_ERR: Exception | None = None
except Exception as exc:  # pragma: no cover - depends on runtime environment
    _TK_OK = False
    _TK_ERR = exc


_PAD = 12


def _center(win: "tk.Tk", width: int, height: int) -> None:
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    x, y = (sw - width) // 2, (sh - height) // 3
    win.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")


def _make_root(title: str) -> "tk.Tk":
    root = tk.Tk()
    root.title(title)
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    return root


_OTHER = "\x00other"


def ask_user(
    question: str,
    sensitive: bool = False,
    choices: list[str] | None = None,
    allow_freeform: bool = False,
    title: str = "Claude needs your input",
    timeout: float | None = None,
) -> str:
    """Ask the user a question and return their answer.

    * ``choices``       -> show radio buttons.
    * ``allow_freeform``-> add an "Other:" entry (with choices), or use a text
                           box (without choices).
    * ``sensitive``     -> mask the input (passwords).
    * ``timeout``       -> auto-close after N seconds returning "" (tests/safety).
    """
    if not _TK_OK:
        prompt = f"\n[Claude asks] {question}\n"
        if choices:
            for i, c in enumerate(choices, 1):
                prompt += f"  {i}. {c}\n"
        prompt += "> "
        try:
            import getpass

            raw = getpass.getpass(prompt) if sensitive else input(prompt)
        except Exception:
            raw = input(prompt)
        if choices and raw.strip().isdigit():
            idx = int(raw.strip()) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        return raw

    result: dict[str, str] = {"value": ""}
    root = _make_root(title)

    tk.Label(
        root, text=question, wraplength=460, justify="left", font=("Segoe UI", 11)
    ).pack(anchor="w", padx=_PAD, pady=(_PAD, 8))

    var = tk.StringVar(value="")
    entry = None  # tk.Entry or tk.Text

    def submit(_event=None) -> None:
        if choices:
            sel = var.get()
            if entry is not None and (sel == _OTHER or sel == ""):
                result["value"] = entry.get().strip()
            else:
                result["value"] = sel
        elif sensitive:
            result["value"] = entry.get()
        else:
            result["value"] = entry.get("1.0", "end").strip()
        root.destroy()

    if choices:
        for c in choices:
            tk.Radiobutton(
                root, text=c, value=c, variable=var, wraplength=430,
                justify="left", anchor="w", font=("Segoe UI", 10),
            ).pack(anchor="w", padx=_PAD)
        var.set(choices[0])
        if allow_freeform:
            row = tk.Frame(root)
            row.pack(anchor="w", fill="x", padx=_PAD)
            tk.Radiobutton(row, text="Other:", value=_OTHER, variable=var,
                           font=("Segoe UI", 10)).pack(side="left")
            entry = tk.Entry(row, font=("Segoe UI", 10))
            entry.pack(side="left", fill="x", expand=True)
            entry.bind("<FocusIn>", lambda _e: var.set(_OTHER))
    elif sensitive:
        entry = tk.Entry(root, show="*", font=("Segoe UI", 11))
        entry.pack(fill="x", padx=_PAD)
        entry.focus_set()
    else:
        entry = tk.Text(root, height=4, font=("Segoe UI", 11), wrap="word")
        entry.pack(fill="both", expand=True, padx=_PAD)
        entry.focus_set()

    single_line = bool(choices) or sensitive
    hint = "Press Enter to submit" if single_line else "Press Ctrl+Enter to submit"
    tk.Label(root, text=hint, fg="#666", font=("Segoe UI", 8)).pack(anchor="w", padx=_PAD)
    tk.Button(root, text="Submit", width=12, command=submit).pack(
        anchor="e", padx=_PAD, pady=_PAD
    )

    root.bind("<Return>" if single_line else "<Control-Return>", submit)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    if timeout:
        root.after(int(timeout * 1000), root.destroy)
    _center(root, 500, 280)
    root.mainloop()
    return result["value"]


def confirm_action(summary: str, title: str = "Confirm action") -> bool:
    """Ask the user to approve a high-stakes action. Returns True if approved."""
    if not _TK_OK:
        ans = input(f"\n[Claude wants to] {summary}\nApprove? [y/N] ")
        return ans.strip().lower() in ("y", "yes")

    result: dict[str, bool] = {"ok": False}
    root = _make_root(title)

    tk.Label(
        root,
        text="Claude wants to perform this action:",
        font=("Segoe UI", 10, "bold"),
    ).pack(anchor="w", padx=_PAD, pady=(_PAD, 4))
    tk.Label(
        root, text=summary, wraplength=440, justify="left", font=("Segoe UI", 11)
    ).pack(anchor="w", padx=_PAD)

    def approve() -> None:
        result["ok"] = True
        root.destroy()

    def cancel() -> None:
        result["ok"] = False
        root.destroy()

    btns = tk.Frame(root)
    btns.pack(anchor="e", padx=_PAD, pady=_PAD)
    tk.Button(btns, text="Cancel", width=12, command=cancel).pack(side="left", padx=6)
    tk.Button(btns, text="Approve", width=12, command=approve, default="active").pack(
        side="left"
    )

    root.bind("<Escape>", lambda _e: cancel())
    root.protocol("WM_DELETE_WINDOW", cancel)
    _center(root, 480, 180)
    root.mainloop()
    return result["ok"]


if __name__ == "__main__":  # Manual smoke test: `python tools/gui.py`
    print("ask_user ->", ask_user("What city are you flying from?"))
    print("choices  ->", ask_user("Which flight?", choices=["Cheapest", "Fastest", "Non-stop"], allow_freeform=True))
    print("confirm_action ->", confirm_action("Book a flight for 4,500 INR"))
