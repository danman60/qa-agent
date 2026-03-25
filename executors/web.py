"""Web executor — Playwright-based browser testing.

Extracted from qa_agent.py's Browser class. Implements BaseExecutor so the
harness can drive it through the platform-agnostic interface.
"""

import os
import re
import time

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.expanduser("~/.cache/ms-playwright"))

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from .base import BaseExecutor


class WebExecutor(BaseExecutor):
    """Playwright-based web executor. Drop-in replacement for the old Browser class."""

    def __init__(self, visual: bool = False):
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.console_errors: list[dict] = []
        self.network_errors: list[dict] = []
        self.visual = visual
        self._viewport = (1280, 720)

    # ── BaseExecutor interface ──

    def setup(self) -> bool:
        self.pw = sync_playwright().start()
        launch_opts = {"headless": not self.visual}
        if self.visual:
            launch_opts["slow_mo"] = 300
        self.browser = self.pw.chromium.launch(**launch_opts)
        self.context = self.browser.new_context(
            viewport={"width": self._viewport[0], "height": self._viewport[1]},
            user_agent="QA-Agent/1.0",
        )
        self.page = self.context.new_page()
        self.page.on("console", self._on_console)
        self.page.on("response", self._on_response)
        return True

    def teardown(self) -> None:
        if self.browser:
            self.browser.close()
        if self.pw:
            self.pw.stop()

    def navigate(self, target: str) -> bool:
        try:
            self.page.goto(target, timeout=15000, wait_until="domcontentloaded")
            self.page.wait_for_load_state("networkidle", timeout=10000)
            return True
        except Exception:
            return False

    def snapshot(self) -> str:
        try:
            return self.page.locator("body").aria_snapshot()
        except Exception as e:
            return f"(snapshot error: {e})"

    def screenshot(self, path: str) -> str:
        self.page.screenshot(path=path, full_page=False)
        return path

    def click(self, role: str, name: str) -> tuple[bool, str]:
        try:
            el = self.page.get_by_role(role, name=name)
            self._highlight(el)
            el.click(timeout=5000)
            time.sleep(0.5)
            return True, "clicked"
        except Exception as e:
            return False, str(e)[:200]

    def click_text(self, text: str) -> tuple[bool, str]:
        try:
            el = self.page.get_by_text(text, exact=True).first
            self._highlight(el)
            el.click(timeout=5000)
            time.sleep(0.5)
            return True, "clicked"
        except Exception as e:
            return False, str(e)[:200]

    def fill(self, role: str, name: str, value: str) -> tuple[bool, str]:
        try:
            el = self.page.get_by_role(role, name=name)
            self._highlight(el)
            el.fill(value, timeout=5000)
            return True, "filled"
        except Exception as e:
            return False, str(e)[:200]

    def type_text(self, text: str) -> tuple[bool, str]:
        self.page.keyboard.type(text)
        return True, "typed"

    def press_key(self, key: str) -> tuple[bool, str]:
        self.page.keyboard.press(key)
        return True, f"pressed {key}"

    def scroll(self, direction: str = "down") -> tuple[bool, str]:
        key = "PageDown" if direction == "down" else "PageUp"
        self.page.keyboard.press(key)
        return True, f"scrolled {direction}"

    def select_option(self, role: str, name: str, value: str) -> tuple[bool, str]:
        try:
            el = self.page.get_by_role(role, name=name)
            self._highlight(el)
            el.select_option(value, timeout=5000)
            return True, f"selected {value}"
        except Exception as e:
            return False, str(e)[:200]

    def back(self) -> tuple[bool, str]:
        try:
            self.page.go_back(timeout=5000)
            return True, "went back"
        except Exception as e:
            return False, str(e)[:200]

    def get_console_errors(self, since_last: bool = True) -> list[dict]:
        errors = self.console_errors[:]
        if since_last:
            self.console_errors.clear()
        return errors

    def get_network_errors(self, since_last: bool = True) -> list[dict]:
        errors = self.network_errors[:]
        if since_last:
            self.network_errors.clear()
        return errors

    def discover_links(self, snapshot_text: str) -> dict:
        links = {}
        for line in snapshot_text.split("\n"):
            m = re.search(r'link\s+"([^"]+)"', line)
            if m:
                name = m.group(1).strip()
                if name and len(name) < 60:
                    links[name] = True
        return links

    def set_viewport(self, width: int, height: int) -> None:
        self._viewport = (width, height)
        self.page.set_viewport_size({"width": width, "height": height})

    @property
    def url(self) -> str:
        return self.page.url

    def get_screen_size(self) -> tuple[int, int]:
        return self._viewport

    # ── Web-specific methods (used by login flow) ──

    @property
    def page_handle(self):
        """Direct access to Playwright page for login and other web-specific code."""
        return self.page

    @property
    def context_handle(self):
        """Direct access to Playwright browser context (for cookies etc)."""
        return self.context

    # ── Internal ──

    def _on_console(self, msg):
        if msg.type in ("error", "warning"):
            self.console_errors.append({
                "type": msg.type, "text": msg.text[:300],
                "url": self.page.url,
            })

    def _on_response(self, response):
        if response.status >= 400:
            self.network_errors.append({
                "status": response.status, "url": response.url[:200],
                "page_url": self.page.url,
            })

    def _highlight(self, locator):
        if not self.visual:
            return
        try:
            locator.scroll_into_view_if_needed(timeout=2000)
            locator.evaluate("""el => {
                el.style.outline = '3px solid #ef4444';
                el.style.outlineOffset = '2px';
                el.style.transition = 'outline 0.2s ease';
                setTimeout(() => { el.style.outline = ''; el.style.outlineOffset = ''; }, 1200);
            }""")
            time.sleep(0.4)
        except Exception:
            pass
