#!/usr/bin/env python3
"""
QA Agent — Checklist-driven, LLM-powered webapp tester.

Architecture:
  HARNESS (deterministic state machine)
    - Owns: snapshots, verdicts, console, network, screenshots, navigation, login
    - Enforces: checklist progression, pass/fail per step, error logging
  LLM (the brain)
    - Only job: given a snapshot + task, pick which element to interact with
    - Returns: {"action": "CLICK", "role": "button", "name": "Sign In"}

Uses Playwright Python API directly — no CLI wrapper, no shell escaping,
no snapshot file I/O, no ref misalignment. Persistent browser context with cookies.

Usage:
    python3 qa_agent.py                                      # Interactive setup
    python3 qa_agent.py https://example.com                  # Auto-discover
    python3 qa_agent.py https://example.com --checklist tests/checklist.md
    python3 qa_agent.py https://example.com --email user@test.com --password pass123
"""

import json
import sys
import os
import re
import time
import hashlib
import argparse
import socket
import threading
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.expanduser("~/.cache/ms-playwright"))

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ═══════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://100.75.112.14:11434")
OLLAMA_LOCAL_URL = os.environ.get("OLLAMA_LOCAL_URL", "http://127.0.0.1:11434")
NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_KEY = os.environ.get("NIM_API_KEY", "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")
NIM_MODEL = "moonshotai/kimi-k2.5"

MODEL_MENU = [
    {"label": "qwen3-coder:30b", "host": "FIRMAMENT 4090", "desc": "fast, 193 tok/s", "provider": "ollama", "url": OLLAMA_URL},
    {"label": "qwen3.5:27b", "host": "FIRMAMENT 4090", "desc": "precise, 41 tok/s", "provider": "ollama", "url": OLLAMA_URL},
    {"label": "glm-4.7-flash", "host": "FIRMAMENT 4090", "desc": "marathon, 140 tok/s", "provider": "ollama", "url": OLLAMA_URL},
    {"label": "gemma3:12b", "host": "SPYBALLOON 3060", "desc": "lightweight, 39.5 tok/s", "provider": "ollama-local", "url": OLLAMA_LOCAL_URL},
    {"label": "Kimi K2.5", "host": "NVIDIA NIM cloud", "desc": "free cloud, 40 req/min", "provider": "nim", "url": None},
]

SYSTEM_PROMPT = """You are a browser testing agent. You receive a page snapshot (accessibility tree) and a task.

## How to respond
```json
{"action": "CLICK", "role": "button", "name": "Sign In", "value": null, "reasoning": "clicking sign in"}
```

## Available actions
- CLICK — role + name of element to click (e.g. role="link", name="Dashboard")
- FILL — role="textbox", name = field label, value = text to enter
- TYPE — type into focused element. value = text (no role/name needed)
- PRESS — value = key name ("Enter", "Tab", "Escape")
- SCROLL — value = "down" or "up"
- SELECT — role="combobox", name = label, value = option text
- VERIFY — confirm the task is complete. reasoning MUST describe what you see (e.g. "page shows schedule table with 5 rows of data"). Use ONLY after navigating/interacting and confirming the expected content is visible.
- NONE — can't determine what to do

## How the snapshot works
The snapshot shows elements like:
  - textbox "Email"
  - button "Sign In"
  - link "Dashboard"
  - heading "Welcome" [level=1]

Use the role and name EXACTLY as shown. Example:
  Snapshot shows: button "Sign In"
  Response: {"action": "CLICK", "role": "button", "name": "Sign In"}

  Snapshot shows: textbox "Email"
  Response: {"action": "FILL", "role": "textbox", "name": "Email", "value": "user@test.com"}

## Rules
- Output ONLY JSON, no extra text
- Use role and name exactly from the snapshot
- If the element isn't in the snapshot, use action=NONE
- Keep reasoning under 30 words"""


# ═══════════════════════════════════════════════════════════════════
# Checklist
# ═══════════════════════════════════════════════════════════════════

class ChecklistItem:
    def __init__(self, section, step_id, description, how=""):
        self.section = section
        self.step_id = step_id
        self.description = description
        self.how = how
        self.status = "pending"  # pending, running, pass, fail, skip, error
        self.result_detail = ""
        self.screenshot = ""
        self.console_errors = []
        self.network_errors = []
        self.snapshot_excerpt = ""
        self.attempts = 0
        self.max_attempts = 5
        self.model_time = 0.0
        self.action_time = 0.0
        self.comment = ""
        self.url = ""

    def to_dict(self):
        return {k: v for k, v in {
            "section": self.section, "step_id": self.step_id,
            "description": self.description, "how": self.how,
            "status": self.status, "result_detail": self.result_detail,
            "screenshot": os.path.basename(self.screenshot) if self.screenshot else "",
            "attempts": self.attempts,
            "console_errors": len(self.console_errors),
            "network_errors": len(self.network_errors),
            "model_time": round(self.model_time, 1),
            "url": self.url, "comment": self.comment,
        }.items()}


def parse_checklist(text):
    items = []
    section = "General"
    n = 0
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("##"):
            section = line.lstrip("#").strip()
            continue
        if line.startswith("#"):
            continue
        m = re.match(r"^[-*]\s+(.+)$", line) or re.match(r"^\d+[.)]\s+(.+)$", line)
        if m:
            n += 1
            content = m.group(1).strip()
            desc, how = content, ""
            for sep in [" — ", " - ", ": ", " -- "]:
                if sep in content:
                    desc, how = content.split(sep, 1)
                    break
            items.append(ChecklistItem(section, str(n), desc.strip(), how.strip()))
    return items


def generate_auto_checklist(url):
    return parse_checklist(f"""## Page Discovery
- Home page loads — Navigate to {url} and check it loads
- Find all navigation links — Identify every link in the nav/sidebar
## Navigation
- Test each nav link — Click every navigation link, verify each page loads with content
- Check for error pages — Look for 404s, 500s, blank pages, error messages
## Interaction
- Test a form — Find and fill out any form (search, filter, create)
- Test buttons — Click interactive buttons and verify response
- Test dropdowns — Open any dropdown or select menu
## Verification
- Check page content — Verify pages show real data, not empty states
- Check console for errors — Look for JavaScript errors
- Check responsive layout — Verify at mobile viewport (375px wide)
""")


# ═══════════════════════════════════════════════════════════════════
# Agent State
# ═══════════════════════════════════════════════════════════════════

class AgentState:
    def __init__(self, report_dir, url="", model="", provider=""):
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        (self.report_dir / "screenshots").mkdir(exist_ok=True)

        self.url = url
        self.model = model
        self.provider = provider
        self.checklist = []
        self.current_item_idx = 0
        self.start_time = time.time()
        self.total_model_time = 0.0
        self.consecutive_llm_errors = 0
        self.pages_visited = set()
        self.all_console_errors = []
        self.all_network_errors = []
        self.nav_links = {}  # name -> href
        self.log_file = self.report_dir / "agent-log.md"

        # Dashboard
        self.paused = False
        self.stopped = False
        self.running = True
        self.commands = []
        self.cmd_lock = threading.Lock()

        with open(self.log_file, "w") as f:
            f.write(f"# QA Agent Log\nStarted: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")

    def log(self, msg, level="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        pfx = {"info": "  ", "action": "> ", "result": "  < ", "error": "X ", "success": "* ", "warn": "! "}.get(level, "  ")
        line = f"[{ts}] {pfx}{msg}"
        print(line, flush=True)
        try:
            with open(self.log_file, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def elapsed(self):
        e = time.time() - self.start_time
        return f"{int(e // 60)}m {int(e % 60)}s"

    def counts(self):
        c = {"pass": 0, "fail": 0, "error": 0, "skip": 0, "pending": 0}
        for i in self.checklist:
            c[i.status] = c.get(i.status, 0) + 1
        c["total"] = len(self.checklist)
        return c

    def push_cmd(self, cmd):
        with self.cmd_lock:
            self.commands.append(cmd)

    def pop_cmds(self):
        with self.cmd_lock:
            out = self.commands[:]
            self.commands.clear()
            return out

    def get_status_dict(self):
        return {
            "running": self.running, "paused": self.paused,
            "current": self.current_item_idx, "elapsed": self.elapsed(),
            "url": self.url, "model": self.model, "provider": self.provider,
            "counts": self.counts(), "pages": len(self.pages_visited),
            "console_errors": len(self.all_console_errors),
            "network_errors": len(self.all_network_errors),
            "checklist": [i.to_dict() for i in self.checklist],
        }

    def write_reports(self):
        c = self.counts()
        elapsed = time.time() - self.start_time

        # Markdown
        md = self.report_dir / "report.md"
        with open(md, "w") as f:
            f.write(f"# QA Agent Report\n\n")
            f.write(f"**URL:** {self.url}\n**Model:** {self.model} ({self.provider})\n")
            f.write(f"**Duration:** {int(elapsed//60)}m {int(elapsed%60)}s\n")
            f.write(f"**Results:** {c['pass']} PASS | {c['fail']} FAIL | {c['error']} ERROR | {c['skip']} SKIP / {c['total']} total\n\n")

            f.write("## Results\n\n| # | Section | Step | Status | Detail |\n|---|---------|------|--------|--------|\n")
            for item in self.checklist:
                st = item.status.upper()
                detail = (item.result_detail or "")[:80].replace("|", "/")
                f.write(f"| {item.step_id} | {item.section} | {item.description[:50]} | {st} | {detail} |\n")

            if self.pages_visited:
                f.write(f"\n## Pages Visited ({len(self.pages_visited)})\n\n")
                for u in sorted(self.pages_visited):
                    f.write(f"- {u}\n")

            if self.all_console_errors:
                f.write(f"\n## Console Errors ({len(self.all_console_errors)})\n\n")
                for ce in self.all_console_errors[:20]:
                    f.write(f"- [{ce['url']}] {ce['text'][:150]}\n")

            if self.all_network_errors:
                f.write(f"\n## Network Errors ({len(self.all_network_errors)})\n\n")
                for ne in self.all_network_errors[:20]:
                    f.write(f"- [{ne['status']}] {ne['url'][:120]}\n")

            failures = [i for i in self.checklist if i.status in ("fail", "error")]
            if failures:
                f.write(f"\n## Failure Details\n\n")
                for item in failures:
                    f.write(f"### {item.step_id}. {item.description}\n")
                    f.write(f"- **{item.status.upper()}**: {item.result_detail}\n")
                    if item.screenshot:
                        f.write(f"- Screenshot: {item.screenshot}\n")
                    f.write("\n")

        # JSON
        with open(self.report_dir / "report.json", "w") as f:
            json.dump(self.get_status_dict(), f, indent=2)

        # Fix list
        failures = [i for i in self.checklist if i.status in ("fail", "error")]
        if failures:
            with open(self.report_dir / "fix-list.md", "w") as f:
                f.write(f"# Fix List — QA Agent Results\n\n{len(failures)} issues found.\n\n")
                for idx, item in enumerate(failures, 1):
                    f.write(f"{idx}. **[{item.status.upper()}] {item.description}** — {item.result_detail[:120]}\n")
            self.log(f"Fix list: {self.report_dir / 'fix-list.md'}", "success")

        # Signal file
        sig_dir = Path("/tmp/test-fix-signals")
        sig_dir.mkdir(exist_ok=True)
        with open(sig_dir / "qa-agent-test.json", "w") as f:
            json.dump({"status": "done", "timestamp": datetime.now().isoformat(),
                        "report": str(md), "summary": c,
                        "bugs": [{"description": i.description, "detail": i.result_detail}
                                 for i in failures]}, f, indent=2)

        self.log(f"Report: {md}", "success")
        self.running = False


# ═══════════════════════════════════════════════════════════════════
# LLM Providers (unchanged)
# ═══════════════════════════════════════════════════════════════════

def chat_ollama(model, messages, max_tokens=300, url=None):
    payload = json.dumps({
        "model": model, "messages": messages, "stream": False, "think": False,
        "options": {"num_predict": max_tokens, "num_ctx": 16384},
    }).encode()
    req = urllib.request.Request(f"{url or OLLAMA_URL}/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read()).get("message", {}).get("content", "")


def chat_nim(messages, max_tokens=300):
    payload = json.dumps({
        "model": NIM_MODEL, "messages": messages, "max_tokens": max_tokens,
        "temperature": 0.7, "stream": False,
    }).encode()
    req = urllib.request.Request(NIM_URL, data=payload,
                                 headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {NIM_KEY}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read()).get("choices", [{}])[0].get("message", {}).get("content", "")


def llm_chat(provider, model, messages, max_tokens=300):
    for attempt in range(3):
        try:
            if provider == "nim":
                return chat_nim(messages, max_tokens)
            elif provider == "ollama-local":
                return chat_ollama(model, messages, max_tokens, url=OLLAMA_LOCAL_URL)
            else:
                return chat_ollama(model, messages, max_tokens)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep((attempt + 1) * 5)
                continue
            return f"LLM_ERROR: {e}"
        except Exception as e:
            return f"LLM_ERROR: {e}"
    return "LLM_ERROR: max retries"


# ═══════════════════════════════════════════════════════════════════
# Browser Layer (Playwright Python API — the whole point of the rewrite)
# ═══════════════════════════════════════════════════════════════════

class Browser:
    """Wraps Playwright Python API. All browser interaction goes through here."""

    def __init__(self):
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.console_errors = []
        self.network_errors = []

    def launch(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.context = self.browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="QA-Agent/1.0",
        )
        self.page = self.context.new_page()

        # Monitor console errors
        self.page.on("console", self._on_console)
        # Monitor network failures
        self.page.on("response", self._on_response)

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

    def goto(self, url, timeout=15000):
        self.page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        self.page.wait_for_load_state("networkidle", timeout=10000)

    def snapshot(self):
        """Get accessibility tree as text. Direct API — no files, no refs to align."""
        try:
            return self.page.locator("body").aria_snapshot()
        except Exception as e:
            return f"(snapshot error: {e})"

    def screenshot(self, path):
        """Take and auto-resize screenshot."""
        self.page.screenshot(path=path, full_page=False)

    def fill(self, role, name, value):
        """Fill a form field by role and name."""
        try:
            self.page.get_by_role(role, name=name).fill(value, timeout=5000)
            return True, "filled"
        except Exception as e:
            return False, str(e)[:200]

    def click(self, role, name):
        """Click an element by role and name."""
        try:
            self.page.get_by_role(role, name=name).click(timeout=5000)
            time.sleep(0.5)
            return True, "clicked"
        except Exception as e:
            return False, str(e)[:200]

    def click_text(self, text):
        """Click by visible text (fallback)."""
        try:
            self.page.get_by_text(text, exact=True).first.click(timeout=5000)
            time.sleep(0.5)
            return True, "clicked"
        except Exception as e:
            return False, str(e)[:200]

    def type_text(self, text):
        """Type into currently focused element."""
        self.page.keyboard.type(text)
        return True, "typed"

    def press_key(self, key):
        """Press a keyboard key."""
        self.page.keyboard.press(key)
        return True, f"pressed {key}"

    def scroll(self, direction="down"):
        key = "PageDown" if direction == "down" else "PageUp"
        self.page.keyboard.press(key)
        return True, f"scrolled {direction}"

    def select_option(self, role, name, value):
        """Select from a dropdown."""
        try:
            self.page.get_by_role(role, name=name).select_option(value, timeout=5000)
            return True, f"selected {value}"
        except Exception as e:
            return False, str(e)[:200]

    def set_viewport(self, width, height):
        self.page.set_viewport_size({"width": width, "height": height})

    def get_console_errors(self, since_last=True):
        """Get console errors. Optionally clear after reading."""
        errors = self.console_errors[:]
        if since_last:
            self.console_errors.clear()
        return errors

    def get_network_errors(self, since_last=True):
        errors = self.network_errors[:]
        if since_last:
            self.network_errors.clear()
        return errors

    def discover_links(self, snapshot_text):
        """Extract navigation links from snapshot."""
        links = {}
        for line in snapshot_text.split("\n"):
            m = re.search(r'link\s+"([^"]+)"', line)
            if m:
                name = m.group(1).strip()
                if name and len(name) < 60:
                    links[name] = True
        return links

    @property
    def url(self):
        return self.page.url

    def close(self):
        if self.browser:
            self.browser.close()
        if self.pw:
            self.pw.stop()


# ═══════════════════════════════════════════════════════════════════
# LLM Response Parser
# ═══════════════════════════════════════════════════════════════════

def parse_response(response):
    """Parse LLM response into action dict."""
    # Try JSON
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", response, re.DOTALL)
    if not json_match:
        json_match = re.search(r"\{[^{}]*\"action\"[^{}]*\}", response, re.DOTALL)
    if json_match:
        try:
            raw = json_match.group(1) if "```" in json_match.group(0) else json_match.group(0)
            obj = json.loads(raw)
            return {
                "action": str(obj.get("action", "NONE")).upper(),
                "role": str(obj.get("role", "")),
                "name": str(obj.get("name", "")),
                "value": str(obj.get("value", "")) if obj.get("value") else "",
                "reasoning": str(obj.get("reasoning", ""))[:80],
            }
        except (json.JSONDecodeError, AttributeError):
            pass
    return {"action": "NONE", "role": "", "name": "", "value": "", "reasoning": response[:80]}


def execute_action(browser, action):
    """Execute a parsed action on the browser. Returns (success, detail)."""
    cmd = action["action"]
    role = action.get("role", "")
    name = action.get("name", "")
    value = action.get("value", "")

    if cmd == "CLICK":
        if role and name:
            return browser.click(role, name)
        elif name:
            return browser.click_text(name)
        return False, "CLICK needs role+name"

    elif cmd == "FILL":
        if not value:
            return False, "FILL needs a value"
        return browser.fill(role or "textbox", name, value)

    elif cmd == "TYPE":
        return browser.type_text(value or name)

    elif cmd == "PRESS":
        return browser.press_key(value or name)

    elif cmd == "SCROLL":
        return browser.scroll(value or "down")

    elif cmd == "SELECT":
        return browser.select_option(role or "combobox", name, value)

    elif cmd == "VERIFY":
        return True, action.get("reasoning", "verified")

    elif cmd == "NONE":
        return False, "LLM returned NONE"

    return False, f"Unknown action: {cmd}"


# ═══════════════════════════════════════════════════════════════════
# Core: Harness-Enforced Test Loop
# ═══════════════════════════════════════════════════════════════════

def ask_llm(provider, model, snapshot, task, messages):
    """Ask LLM what to do. Returns (action_dict, model_time, raw_response)."""
    user_msg = f"TASK: {task}\n\nPage snapshot:\n{snapshot[:3000]}"
    messages.append({"role": "user", "content": user_msg})

    t0 = time.time()
    response = llm_chat(provider, model, messages, max_tokens=300)
    mt = time.time() - t0

    if "LLM_ERROR" in response:
        return None, mt, response

    messages.append({"role": "assistant", "content": response})
    if len(messages) > 20:
        messages[:] = [messages[0]] + messages[-14:]

    return parse_response(response), mt, response


def do_login(browser, state, credentials):
    """Deterministic harness-enforced login. No LLM needed."""
    email = credentials.get("email", "")
    password = credentials.get("password", "")
    login_url = credentials.get("login_url", state.url.rstrip("/") + "/login")

    if not email or not password:
        return True  # No creds, skip login

    state.log(f"Logging in as {email}...", "action")
    browser.goto(login_url)

    # Fill email — try multiple selectors
    for selector in ['input[type="email"]', 'input[name="email"]', '[placeholder*="email" i]', 'input[type="text"]']:
        try:
            el = browser.page.locator(selector).first
            if el.is_visible(timeout=2000):
                el.fill(email)
                state.log(f"  Email filled via {selector}", "result")
                break
        except Exception:
            continue

    # Fill password
    for selector in ['input[type="password"]', 'input[name="password"]']:
        try:
            el = browser.page.locator(selector).first
            if el.is_visible(timeout=2000):
                el.fill(password)
                state.log(f"  Password filled via {selector}", "result")
                break
        except Exception:
            continue

    # Click submit
    for text in ["Sign In", "Log In", "Login", "Sign in", "Submit", "Log in"]:
        try:
            btn = browser.page.get_by_role("button", name=text)
            if btn.is_visible(timeout=1000):
                btn.click()
                state.log(f"  Clicked '{text}'", "result")
                break
        except Exception:
            continue

    # Wait for redirect
    try:
        browser.page.wait_for_url(lambda u: "/login" not in u, timeout=10000)
    except PWTimeout:
        pass

    # Verify
    if "/login" not in browser.url:
        state.log(f"Login succeeded: {browser.url}", "success")
        state.pages_visited.add(browser.url)
        return True
    else:
        state.log(f"Login failed — still at {browser.url}", "error")
        return False


def execute_checklist_item(browser, item, state, provider, model, messages):
    """Execute one checklist item. Harness enforces: snapshot → LLM → act → verdict."""
    item.status = "running"
    state.log(f"\n{'='*50}", "info")
    state.log(f"[{item.step_id}] {item.section}: {item.description}", "action")
    if item.how:
        state.log(f"  How: {item.how}", "info")

    task = item.description
    if item.how:
        task += f". Instructions: {item.how}"

    # Harness: clear console/network errors for this step
    browser.get_console_errors(since_last=True)
    browser.get_network_errors(since_last=True)

    success = False
    last_result = ""

    for attempt in range(1, item.max_attempts + 1):
        # Dashboard commands
        for cmd in state.pop_cmds():
            if cmd.get("cmd") == "pause":
                state.paused = True
            elif cmd.get("cmd") == "resume":
                state.paused = False
            elif cmd.get("cmd") == "stop":
                state.stopped = True
            elif cmd.get("cmd") == "skip":
                item.status = "skip"
                item.result_detail = "Skipped by user"
                return
            elif cmd.get("cmd") == "comment":
                item.comment = cmd.get("text", "")

        while state.paused and not state.stopped:
            time.sleep(1)
            for cmd in state.pop_cmds():
                if cmd.get("cmd") == "resume":
                    state.paused = False
                elif cmd.get("cmd") == "stop":
                    state.stopped = True

        if state.stopped:
            item.status = "skip"
            return

        # 1. HARNESS: Snapshot
        snapshot = browser.snapshot()
        item.snapshot_excerpt = snapshot[:300]
        item.url = browser.url
        state.pages_visited.add(browser.url)

        # Discover nav links
        state.nav_links.update(browser.discover_links(snapshot))

        # 2. LLM: What to do?
        full_task = f"Attempt {attempt}/{item.max_attempts}. {task}"
        if last_result:
            full_task += f"\nPrevious: {last_result[:200]}"

        action, mt, raw = ask_llm(provider, model, snapshot, full_task, messages)
        item.model_time += mt
        state.total_model_time += mt

        if action is None:
            state.consecutive_llm_errors += 1
            state.log(f"  LLM error ({state.consecutive_llm_errors}/5): {raw[:100]}", "error")
            if state.consecutive_llm_errors >= 5:
                item.status = "error"
                item.result_detail = "Circuit breaker: 5 LLM errors"
                return
            time.sleep(3)
            continue
        else:
            state.consecutive_llm_errors = 0

        # 3. Execute
        action_str = f"{action['action']} {action.get('role','')} \"{action.get('name','')}\" {action.get('value','')}"
        state.log(f"  [{attempt}] {action_str.strip()} ({action.get('reasoning','')[:50]})", "action")

        if action["action"] == "NONE":
            reasoning = action.get("reasoning", "").lower()
            # Tight verdict: fail words checked FIRST, then require positive evidence
            FAIL_PHRASES = ["not visible", "not found", "no data", "not present",
                            "may still", "couldn't", "can't find", "cannot find",
                            "empty", "missing", "error", "failed", "unable",
                            "not loaded", "not showing", "no table", "no content",
                            "doesn't show", "does not show", "not displayed"]
            PASS_PHRASES = ["shows", "displays", "contains", "loaded with",
                            "visible with", "confirmed", "verified", "present",
                            "working correctly", "page shows", "table shows",
                            "data is displayed", "successfully", "content loaded",
                            "rendered", "appears correctly"]
            if any(p in reasoning for p in FAIL_PHRASES):
                last_result = f"Observation FAIL: {action.get('reasoning', '')}"
                continue
            elif any(p in reasoning for p in PASS_PHRASES):
                success = True
                last_result = action.get("reasoning", "Task verified by observation")
                break
            else:
                # No positive evidence = not verified
                last_result = f"No positive evidence: {action.get('reasoning', '')}"
                continue

        t1 = time.time()
        ok, detail = execute_action(browser, action)
        item.action_time += time.time() - t1
        item.attempts = attempt

        if ok:
            state.log(f"  OK: {detail[:100]}", "result")
            last_result = detail[:200]
            # VERIFY action = task confirmed, apply tight verdict
            if action["action"] == "VERIFY":
                reasoning = action.get("reasoning", "").lower()
                FAIL_PHRASES = ["not visible", "not found", "no data", "not present",
                                "may still", "couldn't", "can't find", "cannot find",
                                "empty", "missing", "error", "failed", "unable",
                                "not loaded", "not showing", "no table", "no content"]
                if any(p in reasoning for p in FAIL_PHRASES):
                    last_result = f"VERIFY negative: {detail[:150]}"
                    continue
                success = True
                break
            # Non-VERIFY success (CLICK, FILL, etc) — track URL, continue to verify
            new_url = browser.url
            if new_url != item.url:
                state.pages_visited.add(new_url)
                state.log(f"  Nav: {new_url}", "info")
                item.url = new_url
            continue
        else:
            state.log(f"  ERROR: {detail[:100]}", "error")
            last_result = f"ERROR: {detail[:200]}"

    # 4. HARNESS ENFORCES VERDICT

    # Collect console/network errors for this step
    ce = browser.get_console_errors()
    ne = browser.get_network_errors()
    if ce:
        item.console_errors = ce
        state.all_console_errors.extend(ce)
    if ne:
        item.network_errors = ne
        state.all_network_errors.extend(ne)

    # Screenshot
    sc_name = f"step-{item.step_id}-{'pass' if success else 'fail'}.png"
    sc_path = str(state.report_dir / "screenshots" / sc_name)
    try:
        browser.screenshot(sc_path)
        item.screenshot = sc_path
    except Exception:
        pass

    # Verdict
    if success:
        item.status = "pass"
        item.result_detail = last_result[:200]
        if ce:
            item.result_detail += f" ({len(ce)} console error(s))"
        state.log(f"  PASS: {item.description}", "success")
    else:
        item.status = "fail"
        item.result_detail = last_result[:200] if last_result else "Failed after max attempts"
        state.log(f"  FAIL: {item.description} — {item.result_detail[:80]}", "error")


def run_harness_checks(browser, state):
    """Run harness-level checks that don't need the LLM. Appended to checklist results."""
    # Console error summary
    if state.all_console_errors:
        state.log(f"\nConsole errors total: {len(state.all_console_errors)}", "warn")
        for ce in state.all_console_errors[:5]:
            state.log(f"  [{ce['url']}] {ce['text'][:100]}", "error")

    # Network error summary
    if state.all_network_errors:
        state.log(f"Network errors total: {len(state.all_network_errors)}", "warn")
        for ne in state.all_network_errors[:5]:
            state.log(f"  [{ne['status']}] {ne['url'][:100]}", "error")

    # Mobile viewport check
    try:
        state.log("Testing mobile viewport (375x812)...", "action")
        browser.set_viewport(375, 812)
        time.sleep(1)
        snap = browser.snapshot()
        sc_path = str(state.report_dir / "screenshots" / "mobile-viewport.png")
        browser.screenshot(sc_path)
        state.log(f"  Mobile screenshot saved: {sc_path}", "success")
        browser.set_viewport(1280, 720)  # Reset
    except Exception as e:
        state.log(f"  Mobile viewport check failed: {e}", "error")


# ═══════════════════════════════════════════════════════════════════
# Main Entry
# ═══════════════════════════════════════════════════════════════════

def run_agent(url, provider, model, checklist_items, report_dir,
              credentials=None, dashboard_port=9876, no_dashboard=False, ollama_url=None):
    if ollama_url:
        global OLLAMA_URL
        OLLAMA_URL = ollama_url

    state = AgentState(report_dir, url=url, model=model, provider=provider)
    state.checklist = checklist_items

    state.log("QA Agent — Checklist-Driven Webapp Tester")
    state.log(f"URL: {url}")
    state.log(f"Model: {model or NIM_MODEL} ({provider})")
    state.log(f"Checklist: {len(checklist_items)} items")

    # Dashboard
    dashboard = None
    if not no_dashboard:
        try:
            dashboard = start_dashboard(state, dashboard_port)
        except Exception as e:
            state.log(f"Dashboard failed: {e}", "warn")

    # Launch browser
    browser = Browser()
    browser.launch()
    state.log("Browser launched", "success")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Login
    if credentials and credentials.get("email"):
        if not do_login(browser, state, credentials):
            state.log("Login failed — continuing anyway", "warn")
    else:
        browser.goto(url)
        state.pages_visited.add(url)

    # Execute checklist
    for idx, item in enumerate(checklist_items):
        state.current_item_idx = idx
        if state.stopped:
            item.status = "skip"
            continue
        execute_checklist_item(browser, item, state, provider, model, messages)

    # Harness-level checks
    run_harness_checks(browser, state)

    # Reports
    state.write_reports()
    c = state.counts()
    state.log(f"\nDONE: {c['pass']} PASS, {c['fail']} FAIL, {c['error']} ERROR, {c['skip']} SKIP / {c['total']} total", "success")

    browser.close()

    if dashboard:
        state.log("Dashboard running. Ctrl+C to exit.", "info")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            dashboard.shutdown()

    return state


# ═══════════════════════════════════════════════════════════════════
# Dashboard (ported from v3 — identical functionality)
# ═══════════════════════════════════════════════════════════════════

DASH_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap');
:root{
  --blue:#3b82f6;--blue-dark:#1d4ed8;--blue-light:#eff6ff;--blue-50:#f0f7ff;
  --pass:#22c55e;--pass-bg:#f0fdf4;--fail:#ef4444;--fail-bg:#fef2f2;
  --error:#f59e0b;--error-bg:#fef3c7;
  --bg:#f1f5f9;--card:#fff;--text:#0f172a;--muted:#64748b;--border:#e2e8f0;
  --shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
  --shadow-md:0 4px 6px rgba(0,0,0,.05),0 2px 4px rgba(0,0,0,.04);
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);font-size:14px;line-height:1.5}

/* Header */
.hdr{background:linear-gradient(135deg,var(--blue) 0%,var(--blue-dark) 50%,#1e3a8a 100%);color:#fff;padding:1.25rem 1.5rem 1rem;position:sticky;top:0;z-index:100;box-shadow:0 4px 20px rgba(59,130,246,.25)}
.hdr h1{font-size:1.3rem;font-weight:700;letter-spacing:-.01em;display:flex;align-items:center;gap:.4rem}
.hdr .sub{font-size:.78rem;opacity:.85;margin-top:.2rem;font-family:'JetBrains Mono',monospace;letter-spacing:-.02em}
.pbar{margin-top:.6rem;background:rgba(255,255,255,.15);border-radius:99px;height:6px;overflow:hidden;max-width:500px;backdrop-filter:blur(4px)}
.pbar-fill{height:100%;border-radius:99px;background:linear-gradient(90deg,#86efac,#fff);transition:width .6s cubic-bezier(.4,0,.2,1)}
.pbar-label{font-size:.7rem;opacity:.8;margin-top:.2rem;font-family:'JetBrains Mono',monospace}
.stats{display:flex;gap:.6rem;margin-top:.5rem;font-size:.72rem;flex-wrap:wrap}
.stat{background:rgba(255,255,255,.12);padding:.25rem .6rem;border-radius:6px;backdrop-filter:blur(4px);display:flex;align-items:center;gap:.25rem}
.stat b{font-weight:700;font-size:.82rem;font-family:'JetBrains Mono',monospace}

/* Controls */
.ctl{display:flex;align-items:center;gap:.5rem;padding:.6rem 1.5rem;background:var(--card);border-bottom:1px solid var(--border);box-shadow:var(--shadow)}
.ctl button{padding:.4rem .85rem;border-radius:8px;border:1px solid var(--border);background:var(--card);font-size:.78rem;font-weight:500;cursor:pointer;font-family:inherit;transition:all .15s ease}
.ctl button:hover{background:var(--blue-light);border-color:var(--blue);transform:translateY(-1px);box-shadow:var(--shadow)}
.ctl button:active{transform:translateY(0)}
.ctl .danger{color:var(--fail);border-color:#fecaca}
.ctl .danger:hover{background:var(--fail-bg);border-color:var(--fail)}
.ctl-sep{width:1px;height:20px;background:var(--border);margin:0 .2rem}

/* Content */
.wrap{max-width:920px;margin:0 auto;padding:1.25rem 1rem 3rem}

/* Checklist items */
.it{background:var(--card);border:1px solid var(--border);border-radius:10px;margin-bottom:.5rem;padding:.75rem 1rem;box-shadow:var(--shadow);transition:all .2s ease;animation:fadeSlideIn .3s ease both}
.it:hover{box-shadow:var(--shadow-md);transform:translateY(-1px)}
.it.running{border-color:var(--blue);border-width:2px;background:var(--blue-50);box-shadow:0 0 0 3px rgba(59,130,246,.1),var(--shadow-md);animation:fadeSlideIn .3s ease both,glowPulse 2s ease-in-out infinite}
.it.pass{border-left:4px solid var(--pass);background:linear-gradient(90deg,var(--pass-bg) 0%,var(--card) 8%)}
.it.fail,.it.error{border-left:4px solid var(--fail);background:linear-gradient(90deg,var(--fail-bg) 0%,var(--card) 8%)}
.it.skip{opacity:.5}
.it-h{display:flex;align-items:center;gap:.6rem;font-size:.85rem}
.it-n{width:28px;height:28px;border-radius:8px;background:var(--blue-light);color:var(--blue);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:.75rem;flex-shrink:0;font-family:'JetBrains Mono',monospace}
.it.pass .it-n{background:var(--pass-bg);color:var(--pass)}
.it.fail .it-n,.it.error .it-n{background:var(--fail-bg);color:var(--fail)}
.it-d{font-weight:600;flex:1;line-height:1.3}
.badge{padding:.2rem .55rem;border-radius:99px;font-size:.62rem;font-weight:700;text-transform:uppercase;letter-spacing:.03em;white-space:nowrap}
.b-pass{background:#dcfce7;color:#16a34a}
.b-fail{background:#fef2f2;color:#dc2626}
.b-err{background:#fef3c7;color:#92400e}
.b-run{background:var(--blue-light);color:var(--blue);animation:pulse 1.5s infinite}
.b-pend{background:#f1f5f9;color:#cbd5e1;font-size:.55rem}
.b-skip{background:#f1f5f9;color:var(--muted)}

/* Animations */
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
@keyframes fadeSlideIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
@keyframes glowPulse{0%,100%{box-shadow:0 0 0 3px rgba(59,130,246,.1),var(--shadow-md)}50%{box-shadow:0 0 0 6px rgba(59,130,246,.15),var(--shadow-md)}}
@keyframes checkIn{from{transform:scale(0);opacity:0}to{transform:scale(1);opacity:1}}
@keyframes confetti{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}

/* Details */
.detail{font-size:.78rem;color:#475569;margin-top:.3rem;line-height:1.4}
.detail-how{font-size:.75rem;color:var(--muted);margin-top:.15rem;font-style:italic}
.thumb{margin-top:.4rem}
.thumb img{width:120px;height:auto;border-radius:6px;border:1px solid var(--border);cursor:pointer;transition:transform .2s ease,box-shadow .2s ease;box-shadow:var(--shadow)}
.thumb img:hover{transform:scale(1.05);box-shadow:var(--shadow-md)}

/* Section headers */
.sec-h{font-weight:700;font-size:.82rem;margin:1.25rem 0 .5rem;color:var(--blue-dark);padding:.4rem .75rem;background:var(--blue-light);border-radius:8px;display:flex;align-items:center;gap:.4rem;letter-spacing:-.01em}
.sec-h::before{content:'';width:3px;height:16px;background:var(--blue);border-radius:2px}

/* Done banner */
.done{background:linear-gradient(135deg,#f0fdf4 0%,#dcfce7 100%);border:2px solid #86efac;border-radius:12px;padding:1.2rem;text-align:center;margin-bottom:1.25rem;font-weight:700;color:#15803d;font-size:1rem;box-shadow:0 4px 12px rgba(34,197,94,.15);animation:fadeSlideIn .5s ease}
.done .done-stats{display:flex;justify-content:center;gap:1.5rem;margin-top:.5rem;font-size:.85rem;font-weight:500}
.done .done-stat{display:flex;align-items:center;gap:.3rem}
.done .done-stat b{font-family:'JetBrains Mono',monospace;font-size:1.1rem}

/* Responsive */
@media(max-width:640px){
  .hdr{padding:.8rem 1rem}
  .hdr h1{font-size:1.1rem}
  .stats{gap:.4rem}
  .stat{padding:.2rem .4rem;font-size:.65rem}
  .ctl{padding:.5rem .75rem;gap:.3rem}
  .ctl button{padding:.3rem .5rem;font-size:.7rem}
  .wrap{padding:.75rem .5rem}
  .it{padding:.6rem .75rem;border-radius:8px}
  .it-d{font-size:.8rem}
}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

class DashHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        s = self.server.state
        if self.path == "/api/state":
            self._json(s.get_status_dict())
        elif self.path.startswith("/screenshots/"):
            self._serve_screenshot(s, self.path[len("/screenshots/"):])
        else:
            self._html(self._render(s))

    def _serve_screenshot(self, s, filename):
        # Sanitize filename to prevent path traversal
        filename = os.path.basename(filename)
        filepath = s.report_dir / "screenshots" / filename
        if filepath.is_file():
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            with open(filepath, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/command":
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n)) if n else {}
            self.server.state.push_cmd(body)
            self._json({"ok": True})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _json(self, obj):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def _html(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def _render(self, s):
        c = s.counts()
        done_pct = int((c["pass"]+c["fail"]+c["error"]+c["skip"]) / max(c["total"],1) * 100)
        status = "PAUSED" if s.paused else ("DONE" if not s.running else "TESTING")
        live = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#22c55e;margin-right:4px;animation:pulse 1.5s infinite"></span>' if s.running else ""

        items = ""
        sec = ""
        for i, it in enumerate(s.checklist):
            if it.section != sec:
                sec = it.section
                items += f'<div class="sec-h">{sec}</div>'
            css = f"it {it.status}" if it.status != "pending" else "it"
            if i == s.current_item_idx and s.running:
                css = "it running"
            bc = {"pass":"b-pass","fail":"b-fail","error":"b-err","skip":"b-skip","running":"b-run"}.get(it.status, "b-pend")
            bt = it.status.upper() if it.status != "pending" else "..."
            det = f'<div class="detail">{it.result_detail[:150]}</div>' if it.result_detail else ""
            if it.how:
                det += f'<div class="detail-how">{it.how[:120]}</div>'
            if it.screenshot:
                sc_name = os.path.basename(it.screenshot)
                det += f'<div class="thumb"><a href="/screenshots/{sc_name}" target="_blank"><img src="/screenshots/{sc_name}" alt="screenshot"></a></div>'
            items += f'<div class="{css}" style="animation-delay:{i*0.03}s"><div class="it-h"><div class="it-n">{it.step_id}</div><div class="it-d">{it.description}</div><span class="badge {bc}">{bt}</span></div>{det}</div>'

        # Done banner with stats breakdown
        done_b = ""
        if not s.running:
            pass_icon = "&#10003;" if c["fail"] == 0 else "&#9888;"
            done_b = f'''<div class="done">{pass_icon} Testing Complete
<div class="done-stats">
<span class="done-stat"><b>{c["pass"]}</b> passed</span>
<span class="done-stat"><b>{c["fail"]}</b> failed</span>
<span class="done-stat"><b>{s.elapsed()}</b></span>
</div></div>'''

        # Stats with pill styling
        stat = lambda label, val: f'<span class="stat"><b>{val}</b> {label}</span>'
        stats_html = (
            stat("pass", c["pass"]) + stat("fail", c["fail"]) + stat("err", c["error"]) +
            stat("total", c["total"]) + stat("pages", len(s.pages_visited)) +
            stat("console", len(s.all_console_errors)) + stat("network", len(s.all_network_errors))
        )

        return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>QA Agent — {status}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#9889;</text></svg>">
<meta http-equiv="refresh" content="2"><style>{DASH_CSS}</style></head><body>
<div class="hdr">
<h1>{live}QA Agent <span style="font-weight:400;opacity:.85">{status}</span></h1>
<div class="sub">{s.url} &middot; {s.model} ({s.provider}) &middot; {s.elapsed()}</div>
<div class="pbar"><div class="pbar-fill" style="width:{done_pct}%"></div></div>
<div class="pbar-label">{done_pct}% complete</div>
<div class="stats">{stats_html}</div>
</div>
<div class="ctl">
<button onclick="fetch('/api/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{cmd:'pause'}})}})">&#9208; Pause</button>
<button onclick="fetch('/api/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{cmd:'resume'}})}})">&#9654; Resume</button>
<span class="ctl-sep"></span>
<button onclick="fetch('/api/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{cmd:'skip'}})}})">&#9197; Skip</button>
<button class="danger" onclick="fetch('/api/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{cmd:'stop'}})}})">&#9632; Stop</button>
</div>
<div class="wrap">{done_b}{items}</div>
<script>window.addEventListener('load',function(){{var e=document.querySelector('.it.running');if(e)e.scrollIntoView({{behavior:'smooth',block:'center'}});}});</script></body></html>"""


def start_dashboard(state, port=9876):
    srv = HTTPServer(("0.0.0.0", port), DashHandler)
    srv.state = state
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    ip = socket.gethostbyname(socket.gethostname()) if socket.gethostname() != "localhost" else "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    state.log(f"Dashboard: http://{ip}:{port}", "success")
    return srv


# ═══════════════════════════════════════════════════════════════════
# Interactive Setup
# ═══════════════════════════════════════════════════════════════════

def select_model():
    print("\n  Select model:")
    for i, m in enumerate(MODEL_MENU, 1):
        print(f"  [{i}] {m['label']:20s} ({m['host']}) — {m['desc']}")
    print(f"  [{len(MODEL_MENU)+1}] Custom...")
    try:
        c = input("\n  Choice [1]: ").strip()
        idx = int(c) - 1 if c else 0
    except (ValueError, EOFError):
        idx = 0
    if idx < 0 or idx >= len(MODEL_MENU):
        model = input("  Model name: ").strip() or "qwen3-coder:30b"
        url = input(f"  Ollama URL [{OLLAMA_URL}]: ").strip() or OLLAMA_URL
        return "ollama", model, url
    m = MODEL_MENU[idx]
    return m["provider"], m["label"], m.get("url", OLLAMA_URL)


def interactive_setup():
    print("\n" + "=" * 42)
    print("       QA Agent — Webapp Tester")
    print("=" * 42)

    url = input("\n  Webapp URL: ").strip()
    if not url:
        sys.exit(1)
    if not url.startswith("http"):
        url = "https://" + url

    print("\n  Login credentials (Enter to skip):")
    email = input("  Email: ").strip()
    password = input("  Password: ").strip() if email else ""
    login_url = ""
    if email:
        login_url = input(f"  Login URL [{url.rstrip('/')}/login]: ").strip() or url.rstrip("/") + "/login"
    creds = {"email": email, "password": password, "login_url": login_url} if email else {}

    provider, model, ollama_url = select_model()

    print("\n  Checklist:")
    print("  [1] Auto-discover")
    print("  [2] Load from file")
    print("  [3] Type now")
    ch = input("  Choice [1]: ").strip() or "1"

    items = []
    if ch == "2":
        path = input("  File: ").strip()
        if os.path.isfile(path):
            with open(path) as f:
                items = parse_checklist(f.read())
    elif ch == "3":
        print("  Type checklist (empty line to finish):")
        lines = []
        while True:
            line = input("  > ")
            if not line:
                break
            lines.append(line)
        if lines:
            items = parse_checklist("\n".join(lines))

    if not items:
        items = generate_auto_checklist(url)
    print(f"  {len(items)} checklist items")

    return url, provider, model, ollama_url, creds, items


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="QA Agent — Webapp Tester")
    parser.add_argument("url", nargs="?")
    parser.add_argument("--model", default="qwen3-coder:30b")
    parser.add_argument("--provider", choices=["ollama", "ollama-local", "nim"], default="ollama")
    parser.add_argument("--ollama-url", default=None)
    parser.add_argument("--report-dir", default=None)
    parser.add_argument("--checklist", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--login-url", default=None)
    parser.add_argument("--dashboard-port", type=int, default=9876)
    parser.add_argument("--no-dashboard", action="store_true")
    parser.add_argument("--compare", default=None,
                        help="Compare models: 'ollama:qwen3-coder:30b,nim:kimi-k2.5'")
    args = parser.parse_args()

    if not args.url:
        url, provider, model, ollama_url, creds, items = interactive_setup()
        if args.compare:
            _run_comparison(args, url, creds, items, ollama_url)
            return
    else:
        url = args.url
        provider = args.provider
        model = args.model
        ollama_url = args.ollama_url
        creds = {}
        if args.email:
            creds = {"email": args.email, "password": args.password or "",
                     "login_url": args.login_url or url.rstrip("/") + "/login"}
        items = []
        if args.checklist and os.path.isfile(args.checklist):
            with open(args.checklist) as f:
                items = parse_checklist(f.read())
        if not items:
            items = generate_auto_checklist(url)

        if args.compare:
            _run_comparison(args, url, creds, items, ollama_url)
            return

    if not args.report_dir:
        args.report_dir = f"tests/reports/qa-{datetime.now():%Y%m%d-%H%M%S}"

    run_agent(url, provider, model, items, args.report_dir,
              credentials=creds, dashboard_port=args.dashboard_port,
              no_dashboard=args.no_dashboard, ollama_url=ollama_url)


def _run_comparison(args, url, creds, items, ollama_url):
    """Run the same checklist with multiple models and generate a comparison report."""
    models = []
    for spec in args.compare.split(","):
        spec = spec.strip()
        if ":" in spec:
            parts = spec.split(":", 1)
            models.append((parts[0], parts[1]))
        else:
            models.append(("ollama", spec))

    base_dir = args.report_dir or f"tests/reports/compare-{datetime.now():%Y%m%d-%H%M%S}"
    Path(base_dir).mkdir(parents=True, exist_ok=True)

    results = []  # list of (provider, model, state)
    for prov, mod in models:
        print(f"\n{'='*60}")
        print(f"  COMPARISON RUN: {mod} ({prov})")
        print(f"{'='*60}")
        run_dir = f"{base_dir}/{prov}-{mod.replace('/', '-').replace(':', '-')}"
        # Re-parse checklist for fresh items each run
        fresh_items = []
        if args.checklist and os.path.isfile(args.checklist):
            with open(args.checklist) as f:
                fresh_items = parse_checklist(f.read())
        if not fresh_items:
            fresh_items = generate_auto_checklist(url)

        state = run_agent(url, prov, mod, fresh_items, run_dir,
                          credentials=creds, no_dashboard=True,
                          ollama_url=ollama_url)
        results.append((prov, mod, state))

    # Generate comparison report
    _write_comparison_report(base_dir, results)


def _write_comparison_report(base_dir, results):
    """Generate a markdown comparison table from multiple runs."""
    report_path = Path(base_dir) / "comparison.md"

    # Build header
    model_names = [f"{mod} ({prov})" for prov, mod, _ in results]
    header = "| # | Step |" + "|".join(f" {m[:25]} " for m in model_names) + "|"
    sep = "|---|------|" + "|".join("------" for _ in results) + "|"

    # Build rows from first result's checklist (all have same items)
    rows = []
    first_checklist = results[0][2].checklist
    for i, item in enumerate(first_checklist):
        cells = []
        for _, _, state in results:
            st = state.checklist[i].status.upper() if i < len(state.checklist) else "?"
            cells.append(st)
        row = f"| {item.step_id} | {item.description[:40]} |" + "|".join(f" {c} " for c in cells) + "|"
        rows.append(row)

    # Summary row
    summary_cells = []
    for prov, mod, state in results:
        c = state.counts()
        elapsed = time.time() - state.start_time
        summary_cells.append(f"{c['pass']}/{c['total']} ({int(elapsed)}s)")
    summary = "| | **TOTAL** |" + "|".join(f" **{c}** " for c in summary_cells) + "|"

    with open(report_path, "w") as f:
        f.write(f"# Model Comparison Report\n\n")
        f.write(f"**URL:** {results[0][2].url}\n")
        f.write(f"**Date:** {datetime.now():%Y-%m-%d %H:%M}\n")
        f.write(f"**Models:** {', '.join(model_names)}\n\n")
        f.write(header + "\n")
        f.write(sep + "\n")
        for row in rows:
            f.write(row + "\n")
        f.write(summary + "\n")

    print(f"\n{'='*60}")
    print(f"  COMPARISON REPORT: {report_path}")
    print(f"{'='*60}")
    for prov, mod, state in results:
        c = state.counts()
        print(f"  {mod} ({prov}): {c['pass']}/{c['total']} PASS, {state.elapsed()}")


if __name__ == "__main__":
    main()
