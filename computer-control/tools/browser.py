"""Playwright-backed browser control for fast, DOM-level web automation.

Runs inside cc.py's persistent action server, so a single browser stays open
across commands (and a persistent profile keeps the user logged in). This lets
the agent navigate, read page text, and click/fill by selector or visible text
directly — far faster and more reliable than the screenshot -> click loop.

Import-guarded: if Playwright isn't installed, ``available()`` reports why.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_PROFILE = os.environ.get(
    "CC_BROWSER_PROFILE", str(Path.home() / ".computer-control" / "browser-profile")
)
_CHANNEL = os.environ.get("CC_BROWSER_CHANNEL", "chrome")  # "" -> bundled chromium
_HEADLESS = os.environ.get("CC_BROWSER_HEADLESS", "0") == "1"
_READ_LIMIT = int(os.environ.get("CC_BROWSER_READ_LIMIT", "6000"))

_pw = None
_ctx = None
_page = None


def available() -> tuple[bool, str]:
    try:
        import playwright  # noqa: F401

        return True, "ok"
    except Exception as exc:  # pragma: no cover
        return False, f"playwright not installed ({exc}). Run: pip install playwright && playwright install chromium"


def _ensure():
    """Start Playwright + a persistent browser context on first use."""
    global _pw, _ctx, _page
    if _page is not None:
        return _page
    from playwright.sync_api import sync_playwright

    _pw = sync_playwright().start()
    kwargs = dict(user_data_dir=_PROFILE, headless=_HEADLESS,
                  args=["--start-maximized"], no_viewport=True)
    try:
        if _CHANNEL:
            _ctx = _pw.chromium.launch_persistent_context(channel=_CHANNEL, **kwargs)
        else:
            raise RuntimeError("no channel configured")
    except Exception:
        # Fall back to Playwright's bundled Chromium.
        _ctx = _pw.chromium.launch_persistent_context(**kwargs)
    _page = _ctx.pages[0] if _ctx.pages else _ctx.new_page()
    return _page


def goto(url: str) -> str:
    page = _ensure()
    if url and "://" not in url:
        url = "https://" + url
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    return f"opened: {page.title()} — {page.url}"


def read() -> str:
    page = _ensure()
    text = page.inner_text("body")
    truncated = text[:_READ_LIMIT]
    note = "" if len(text) <= _READ_LIMIT else f"\n...[truncated {len(text) - _READ_LIMIT} chars]"
    return f"URL: {page.url}\nTITLE: {page.title()}\n\n{truncated}{note}"


def links() -> str:
    page = _ensure()
    items = page.eval_on_selector_all(
        "a, button, [role=button], input[type=submit]",
        "els => els.slice(0,80).map(e => (e.innerText||e.value||e.getAttribute('aria-label')||'').trim()).filter(Boolean)",
    )
    seen, out = set(), []
    for t in items:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return "Clickable elements:\n- " + "\n- ".join(out[:60])


def click(text: str | None = None, selector: str | None = None,
          role: str | None = None, name: str | None = None) -> str:
    page = _ensure()
    if role:
        loc = page.get_by_role(role, name=name) if name else page.get_by_role(role)
        loc.first.click(timeout=10000)
        what = f"role={role}" + (f" name={name!r}" if name else "")
    elif selector:
        page.click(selector, timeout=10000)
        what = selector
    elif text:
        page.get_by_text(text, exact=False).first.click(timeout=10000)
        what = f"text {text!r}"
    else:
        return "click needs --text, --selector, or --role [--name]"
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    return f"clicked {what}. now at: {page.url}"


def fill(selector: str | None = None, text: str = "",
         label: str | None = None, placeholder: str | None = None) -> str:
    page = _ensure()
    if label:
        page.get_by_label(label).first.fill(text, timeout=10000)
        return f"filled label {label!r}"
    if placeholder:
        page.get_by_placeholder(placeholder).first.fill(text, timeout=10000)
        return f"filled placeholder {placeholder!r}"
    if selector:
        page.fill(selector, text, timeout=10000)
        return f"filled {selector}"
    return "fill needs --selector, --label, or --placeholder (plus --text)"


# --- common ready-made helpers (so the agent needn't write Playwright itself) --
_SEARCH_SELECTORS = [
    "input[type=search]",
    "#searchInput", "#search", "#searchform input",
    "input[name=q]", "input[name=query]", "input[name=search]", "input[name=s]",
    "textarea[name=q]",
    "input[aria-label*='Search' i]", "input[placeholder*='Search' i]",
    "input[title*='Search' i]",
]


def search(query: str) -> str:
    """Find the page's main search box, type the query and submit it."""
    page = _ensure()

    def _try(loc, how):
        try:
            if loc.count() > 0:
                loc.first.fill(query, timeout=4000)
                loc.first.press("Enter")
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                return f"searched {query!r} via {how} -> {page.url}"
        except Exception:
            return None
        return None

    # ARIA searchbox role first (most robust), then common selectors.
    res = _try(page.get_by_role("searchbox"), "role=searchbox")
    if res:
        return res
    for sel in _SEARCH_SELECTORS:
        res = _try(page.locator(sel), sel)
        if res:
            return res
    return "no obvious search box found (use `fill` then `press --key Enter`)"


def wait(text: str | None = None, selector: str | None = None, timeout: float = 15.0) -> str:
    """Wait until an element / text appears (deterministic, no polling by screenshot)."""
    page = _ensure()
    ms = int(timeout * 1000)
    if selector:
        page.wait_for_selector(selector, timeout=ms)
        return f"found selector: {selector}"
    if text:
        page.get_by_text(text, exact=False).first.wait_for(timeout=ms)
        return f"found text: {text}"
    page.wait_for_load_state("networkidle", timeout=ms)
    return "page settled (network idle)"


def get(selector: str) -> str:
    """Inner text of the first element matching a selector."""
    page = _ensure()
    return page.locator(selector).first.inner_text(timeout=8000)


def extract(selector: str, limit: int = 50) -> str:
    """Text of ALL elements matching a selector (for lists/results/tables)."""
    page = _ensure()
    texts = page.eval_on_selector_all(
        selector,
        "els => els.map(e => (e.innerText||e.value||'').trim()).filter(Boolean)",
    )
    texts = texts[: max(1, limit)]
    return "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts)) or "(no matches)"


def url() -> str:
    page = _ensure()
    return f"{page.title()} — {page.url}"


def scroll(to: str | None = None, px: int = 0) -> str:
    """Scroll the page: --to top|bottom, or by --px pixels (default 800)."""
    page = _ensure()
    if to == "bottom":
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        where = "bottom"
    elif to == "top":
        page.evaluate("window.scrollTo(0, 0)")
        where = "top"
    else:
        n = int(px) or 800
        page.evaluate(f"window.scrollBy(0, {n})")
        where = f"{n}px"
    return f"scrolled {where}"


def eval_js(js: str) -> str:
    """Run JavaScript in the page and return the result (advanced/extraction)."""
    page = _ensure()
    res = page.evaluate(js)
    if isinstance(res, str):
        return res[:_READ_LIMIT]
    return json.dumps(res, ensure_ascii=False)[:_READ_LIMIT]


def press(key: str) -> str:
    page = _ensure()
    page.keyboard.press(key or "Enter")
    return f"pressed {key or 'Enter'}"


def back() -> str:
    page = _ensure()
    page.go_back(wait_until="domcontentloaded")
    return f"back to: {page.url}"


def screenshot(out: str | None = None) -> str:
    page = _ensure()
    path = os.path.abspath(out or os.path.join(tempfile.gettempdir(), "cc_browser.png"))
    page.screenshot(path=path)
    return path


def close() -> str:
    global _pw, _ctx, _page
    try:
        if _ctx:
            _ctx.close()
        if _pw:
            _pw.stop()
    finally:
        _pw = _ctx = _page = None
    return "browser closed"
