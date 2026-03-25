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
from executors.web import WebExecutor

# ═══════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════

GOTCHAS_FILE = Path(__file__).parent / "gotchas.md"

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

# ═══════════════════════════════════════════════════════════════════
# Server mode globals
# ═══════════════════════════════════════════════════════════════════
_server_mode = False           # True when --server is active
_current_run_state = None      # AgentState of active run (or None)
_current_run_thread = None     # Thread running the test
PROJECTS_FILE = Path(__file__).parent / "projects.json"


def load_gotchas():
    """Load learned fail phrases from gotchas.md. These are phrases that slipped through
    verdict checks in previous runs and were caught post-hoc."""
    phrases = []
    if GOTCHAS_FILE.is_file():
        for line in GOTCHAS_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("- ") and "|" in line:
                # Format: - phrase | reason | date
                phrase = line[2:].split("|")[0].strip().lower()
                if phrase:
                    phrases.append(phrase)
    return phrases


def save_gotcha(phrase, reason):
    """Append a new gotcha phrase learned from this run."""
    date = datetime.now().strftime("%Y-%m-%d")
    entry = f"- {phrase} | {reason} | {date}\n"
    if not GOTCHAS_FILE.is_file():
        GOTCHAS_FILE.write_text("# QA Agent Gotchas — Learned Fail Phrases\n"
                                "# Format: - phrase | why it's a false pass | date learned\n"
                                "# These get loaded into FAIL_PHRASES on every run.\n\n")
    # Don't add duplicates
    existing = GOTCHAS_FILE.read_text()
    if phrase.lower() not in existing.lower():
        with open(GOTCHAS_FILE, "a") as f:
            f.write(entry)


# Base fail phrases + learned gotchas
BASE_FAIL_PHRASES = ["not visible", "not found", "no data", "not present",
                     "may still", "couldn't", "can't find", "cannot find",
                     "cannot ", "empty", "missing", "error", "failed", "unable",
                     "not loaded", "not showing", "no table", "no content",
                     "doesn't show", "does not show", "not displayed",
                     "still shows login", "has not loaded", "not logged in",
                     "login screen", "login page"]
LEARNED_GOTCHAS = load_gotchas()
FAIL_PHRASES = BASE_FAIL_PHRASES + LEARNED_GOTCHAS

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
    # Check if this is a flow file format
    if "**Action:**" in text or "**Actions:**" in text:
        # Delegate to flow file parser
        steps = parse_flow_file(text)
        return flow_to_checklist(steps)

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


def parse_flow_file(text):
    """Parse flow format test files into structured steps.

    Extracts steps with: step_number, title, action, expected, verify.
    Handles variations like Action/Actions, Expected/Expect, Verify/Check/Assert.
    """
    steps = []
    # Split by step headers
    step_pattern = r'^##\s*Step\s+(\d+)\s*:\s*(.+)$'
    lines = text.split('\n')

    current_step = None
    current_field = None
    field_content = []

    # Field patterns to detect (normalized to standard names)
    field_patterns = {
        'action': r'^\*\*(?:Action|Actions):\*\*\s*(.*)$',
        'expected': r'^\*\*(?:Expected|Expect):\*\*\s*(.*)$',
        'verify': r'^\*\*(?:Verify|Check|Assert):\*\*\s*(.*)$',
        'console': r'^\*\*Console:\*\*\s*(.*)$',
        'screenshot': r'^\*\*Screenshot:\*\*\s*(.*)$',
        'depends_on': r'^\*\*DEPENDS_ON:\*\*\s*(.*)$',
    }

    def finalize_field():
        nonlocal current_step, current_field, field_content
        if current_step and current_field and field_content:
            content = '\n'.join(field_content).strip()
            current_step[current_field] = content
            current_field = None
            field_content = []

    def finalize_step():
        finalize_field()
        nonlocal current_step
        if current_step and 'step_number' in current_step:
            steps.append(current_step)
        current_step = None

    for line in lines:
        stripped = line.strip()

        # Check for step header
        step_match = re.match(step_pattern, line, re.IGNORECASE)
        if step_match:
            finalize_step()
            step_num = int(step_match.group(1))
            title = step_match.group(2).strip()
            current_step = {
                'step_number': step_num,
                'title': title,
                'action': '',
                'expected': '',
                'verify': '',
            }
            current_field = None
            field_content = []
            continue

        if not current_step:
            continue

        # Check for field headers
        field_matched = False
        for field_name, pattern in field_patterns.items():
            match = re.match(pattern, stripped, re.IGNORECASE)
            if match:
                finalize_field()
                current_field = field_name
                content = match.group(1) if match.groups() else ''
                if content:
                    field_content.append(content)
                field_matched = True
                break

        if field_matched:
            continue

        # Check for continuation lines (indented or part of multi-line content)
        if current_field and stripped:
            # Skip lines that are just separators (---)
            if stripped == '---':
                continue
            # Skip empty field indicators from other patterns
            if stripped.startswith('- ') or re.match(r'^\d+[.)]', stripped):
                # This might be a numbered list item, include it
                field_content.append(stripped)
            else:
                field_content.append(stripped)

    finalize_step()

    # Sort by step number
    steps.sort(key=lambda s: s.get('step_number', 0))
    return steps


def flow_to_checklist(steps):
    """Convert parsed flow steps to ChecklistItem objects."""
    items = []
    for step in steps:
        step_num = step.get('step_number', 0)
        title = step.get('title', '')
        action = step.get('action', '')
        expected = step.get('expected', '')
        verify = step.get('verify', '')

        # Build description: title + action (truncated to 100 chars)
        action_preview = action.split('\n')[0] if action else ''
        desc = f"{title}: {action_preview}" if action_preview else title
        if len(desc) > 100:
            desc = desc[:97] + '...'

        # Build how: Expected + Verify criteria
        how_parts = []
        if expected:
            how_parts.append(f"Expected: {expected}")
        if verify:
            how_parts.append(f"Verify: {verify}")
        how = " | ".join(how_parts)

        items.append(ChecklistItem("General", str(step_num), desc, how))
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

    def __init__(self, visual=False):
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.console_errors = []
        self.network_errors = []
        self.visual = visual

    def launch(self):
        self.pw = sync_playwright().start()
        launch_opts = {"headless": not self.visual}
        if self.visual:
            launch_opts["slow_mo"] = 300  # 300ms between actions for visibility
        self.browser = self.pw.chromium.launch(**launch_opts)
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

    def _highlight(self, locator):
        """Flash a red border around element before interacting (visual mode only)."""
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

    def fill(self, role, name, value):
        """Fill a form field by role and name."""
        try:
            el = self.page.get_by_role(role, name=name)
            self._highlight(el)
            el.fill(value, timeout=5000)
            return True, "filled"
        except Exception as e:
            return False, str(e)[:200]

    def click(self, role, name):
        """Click an element by role and name."""
        try:
            el = self.page.get_by_role(role, name=name)
            self._highlight(el)
            el.click(timeout=5000)
            time.sleep(0.5)
            return True, "clicked"
        except Exception as e:
            return False, str(e)[:200]

    def click_text(self, text):
        """Click by visible text (fallback)."""
        try:
            el = self.page.get_by_text(text, exact=True).first
            self._highlight(el)
            el.click(timeout=5000)
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
            el = self.page.get_by_role(role, name=name)
            self._highlight(el)
            el.select_option(value, timeout=5000)
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


def execute_action(executor, action):
    """Execute a parsed action on the executor. Returns (success, detail)."""
    cmd = action["action"]
    role = action.get("role", "")
    name = action.get("name", "")
    value = action.get("value", "")

    if cmd == "CLICK":
        if role and name:
            return executor.click(role, name)
        elif name:
            return executor.click_text(name)
        return False, "CLICK needs role+name"

    elif cmd == "FILL":
        if not value:
            return False, "FILL needs a value"
        return executor.fill(role or "textbox", name, value)

    elif cmd == "TYPE":
        return executor.type_text(value or name)

    elif cmd == "PRESS":
        return executor.press_key(value or name)

    elif cmd == "SCROLL":
        return executor.scroll(value or "down")

    elif cmd == "SELECT":
        return executor.select_option(role or "combobox", name, value)

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


def _try_token_injection(executor, state, credentials, login_url):
    """Get a Supabase session token via API and inject into browser localStorage.
    Returns True if injection succeeded and browser is now at dashboard.
    Requires a WebExecutor (uses page_handle/context_handle for Playwright-specific APIs)."""
    import urllib.request, json as _json
    supabase_url = credentials.get("supabase_url", "")
    service_key = credentials.get("supabase_service_key", "")
    anon_key = credentials.get("supabase_anon_key", "")
    session_file = credentials.get("supabase_session_file", "")
    email = credentials.get("email", "")
    password = credentials.get("password", "")
    api_key = service_key or anon_key
    if not supabase_url:
        return False
    # Token injection only works with WebExecutor
    if not hasattr(executor, 'page_handle'):
        return False
    state.log("  Token-injection login...", "action")
    page = executor.page_handle
    ctx = executor.context_handle
    try:
        # Option A: load pre-generated session from file
        if session_file and os.path.isfile(session_file):
            with open(session_file) as f:
                token_data = _json.load(f)
            state.log("  Using pre-generated session token", "result")
        elif api_key and email and password:
            # Option B: fetch token via API
            req_data = _json.dumps({"email": email, "password": password}).encode()
            headers = {"apikey": api_key, "Content-Type": "application/json"}
            if service_key:
                headers["Authorization"] = f"Bearer {service_key}"
            req = urllib.request.Request(
                f"{supabase_url}/auth/v1/token?grant_type=password",
                data=req_data, headers=headers,
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                token_data = _json.loads(resp.read())
        else:
            return False
        access_token = token_data.get("access_token", "")
        if not access_token:
            return False
        session_payload = _json.dumps({
            "access_token": access_token,
            "refresh_token": token_data.get("refresh_token", ""),
            "token_type": "bearer",
            "expires_in": token_data.get("expires_in", 3600),
            "expires_at": token_data.get("expires_at", 0),
            "user": token_data.get("user", {}),
        })
        base_url = login_url.split("/login")[0]
        domain = base_url.replace("https://", "").replace("http://", "")
        ref = supabase_url.replace("https://", "").split(".")[0]
        cookie_name = f"sb-{ref}-auth-token"
        import urllib.parse as _urlparse
        cookie_value = _urlparse.quote(session_payload)
        page.goto(login_url, wait_until="domcontentloaded")
        ctx.add_cookies([{
            "name": cookie_name,
            "value": cookie_value,
            "domain": domain,
            "path": "/",
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        }])
        page.evaluate(f"localStorage.setItem('sb-{ref}-auth-token', {_json.dumps(session_payload)});")
        page.goto(base_url + "/dashboard", wait_until="domcontentloaded")
        time.sleep(3)
        if "/login" not in executor.url:
            state.log(f"  Token-injection succeeded: {executor.url}", "result")
            return True
        try:
            page.wait_for_url(lambda u: "/login" not in u, timeout=8000)
            state.log(f"  Token-injection succeeded after wait: {executor.url}", "result")
            return True
        except Exception:
            pass
    except Exception as e:
        import urllib.error as _ue
        detail = ""
        if isinstance(e, _ue.HTTPError):
            try: detail = e.read().decode()[:150]
            except Exception: pass
        state.log(f"  Token-injection failed: {e} {detail}", "error")
    return False


def do_login(executor, state, credentials):
    """Deterministic harness-enforced login. No LLM needed.
    Works with WebExecutor (uses page_handle for Playwright-specific selectors)."""
    email = credentials.get("email", "")
    password = credentials.get("password", "")
    login_url = credentials.get("login_url", state.url.rstrip("/") + "/login")

    if not email or not password:
        return True  # No creds, skip login

    state.log(f"Logging in as {email}...", "action")

    # Try token injection first if Supabase credentials provided (avoids form rate limits)
    if credentials.get("supabase_url") and (credentials.get("supabase_service_key") or credentials.get("supabase_anon_key") or credentials.get("supabase_session_file")):
        if _try_token_injection(executor, state, credentials, login_url):
            state.log(f"Login succeeded (token injection): {executor.url}", "success")
            state.pages_visited.add(executor.url)
            return True
        state.log("  Token injection failed, falling back to form login...", "warn")

    executor.navigate(login_url)

    # Web-specific form login using Playwright selectors
    page = executor.page_handle

    # Fill email — try multiple selectors
    for selector in ['input[type="email"]', 'input[name="email"]', '[placeholder*="email" i]', 'input[type="text"]']:
        try:
            el = page.locator(selector).first
            if el.is_visible(timeout=2000):
                el.fill(email)
                state.log(f"  Email filled via {selector}", "result")
                break
        except Exception:
            continue

    # Fill password
    for selector in ['input[type="password"]', 'input[name="password"]']:
        try:
            el = page.locator(selector).first
            if el.is_visible(timeout=2000):
                el.fill(password)
                state.log(f"  Password filled via {selector}", "result")
                break
        except Exception:
            continue

    # Click submit
    for text in ["Sign In", "Log In", "Login", "Sign in", "Submit", "Log in"]:
        try:
            btn = page.get_by_role("button", name=text)
            if btn.is_visible(timeout=1000):
                btn.click()
                state.log(f"  Clicked '{text}'", "result")
                break
        except Exception:
            continue

    # Wait for redirect OR page content change (some apps don't redirect)
    try:
        page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
    except PWTimeout:
        time.sleep(5)

    state.log(f"  Post-login URL: {executor.url}", "result")

    if "/login" not in executor.url:
        state.log(f"Login succeeded: {executor.url}", "success")
        state.pages_visited.add(executor.url)
        return True

    # SPA fallback
    try:
        snap = executor.snapshot()
        has_sidebar = bool(page.locator('nav').count()) or 'navigation' in snap.lower()
        has_dashboard_link = 'link "Dashboard"' in snap or 'link "Home"' in snap
        if has_sidebar and has_dashboard_link:
            try:
                page.get_by_role("link", name="Dashboard").first.click()
                time.sleep(2)
                state.log(f"Login succeeded (SPA → navigated to dashboard): {executor.url}", "success")
                state.pages_visited.add(executor.url)
                return True
            except Exception:
                state.log(f"Login succeeded (SPA, URL unchanged): {executor.url}", "success")
                state.pages_visited.add(executor.url)
                return True
    except Exception:
        pass

    state.log(f"Login failed — still at {executor.url}", "error")
    return False


def execute_checklist_item(executor, item, state, provider, model, messages):
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
    executor.get_console_errors(since_last=True)
    executor.get_network_errors(since_last=True)

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
        snapshot = executor.snapshot()
        item.snapshot_excerpt = snapshot[:300]
        item.url = executor.url
        state.pages_visited.add(executor.url)

        # Discover nav links
        state.nav_links.update(executor.discover_links(snapshot))

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
            # Uses module-level FAIL_PHRASES (base + learned gotchas)
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
        ok, detail = execute_action(executor, action)
        item.action_time += time.time() - t1
        item.attempts = attempt

        if ok:
            state.log(f"  OK: {detail[:100]}", "result")
            last_result = detail[:200]
            # VERIFY action = task confirmed, apply tight verdict
            if action["action"] == "VERIFY":
                reasoning = action.get("reasoning", "").lower()
                # Uses module-level FAIL_PHRASES (base + learned gotchas)
                if any(p in reasoning for p in FAIL_PHRASES):
                    last_result = f"VERIFY negative: {detail[:150]}"
                    continue
                success = True
                break
            # Non-VERIFY success (CLICK, FILL, etc) — track URL, continue to verify
            new_url = executor.url
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
    ce = executor.get_console_errors()
    ne = executor.get_network_errors()
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
        executor.screenshot(sc_path)
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


def run_harness_checks(executor, state):
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
        executor.set_viewport(375, 812)
        time.sleep(1)
        snap = executor.snapshot()
        sc_path = str(state.report_dir / "screenshots" / "mobile-viewport.png")
        executor.screenshot(sc_path)
        state.log(f"  Mobile screenshot saved: {sc_path}", "success")
        executor.set_viewport(1280, 720)  # Reset
    except Exception as e:
        state.log(f"  Mobile viewport check failed: {e}", "error")


# ═══════════════════════════════════════════════════════════════════
# Main Entry
# ═══════════════════════════════════════════════════════════════════

def _learn_gotchas(state):
    """Post-run: detect suspicious PASS verdicts and save new gotcha phrases."""
    learned = 0
    for item in state.checklist:
        if item.status != "pass" or not item.result_detail:
            continue
        detail = item.result_detail.lower()
        # Suspicious: passed but reasoning contains negative language
        suspicious_words = ["but", "however", "although", "not able", "couldn't",
                            "still on", "unable", "no ", "didn't", "wasn't"]
        if any(w in detail for w in suspicious_words):
            # Extract the suspicious phrase (first 4 words after the negative word)
            for w in suspicious_words:
                idx = detail.find(w)
                if idx >= 0:
                    snippet = detail[idx:idx+40].split(".")[0].strip()
                    if len(snippet) > 5:
                        save_gotcha(snippet, f"false pass on: {item.description[:50]}")
                        learned += 1
                        break
    if learned:
        state.log(f"Learned {learned} new gotcha(s) → {GOTCHAS_FILE.name}", "info")


def run_agent(url, provider, model, checklist_items, report_dir,
              credentials=None, dashboard_port=9876, no_dashboard=False, ollama_url=None,
              visual=False, _state=None):
    if ollama_url:
        global OLLAMA_URL
        OLLAMA_URL = ollama_url

    if _state is not None:
        state = _state
        state.report_dir = Path(report_dir)
        state.report_dir.mkdir(parents=True, exist_ok=True)
        (state.report_dir / "screenshots").mkdir(exist_ok=True)
        state.log_file = state.report_dir / "agent-log.md"
        with open(state.log_file, "w") as f:
            f.write(f"# QA Agent Log\nStarted: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
    else:
        state = AgentState(report_dir, url=url, model=model, provider=provider)
        state.checklist = checklist_items

    state.log("QA Agent — Checklist-Driven Webapp Tester")
    state.log(f"URL: {url}")
    state.log(f"Model: {model or NIM_MODEL} ({provider})")
    state.log(f"Checklist: {len(checklist_items)} items")
    if LEARNED_GOTCHAS:
        state.log(f"Loaded {len(LEARNED_GOTCHAS)} learned gotchas from {GOTCHAS_FILE.name}", "info")

    # Dashboard
    dashboard = None
    if not no_dashboard:
        try:
            dashboard = start_dashboard(state, dashboard_port)
        except Exception as e:
            state.log(f"Dashboard failed: {e}", "warn")

    # Launch executor
    executor = WebExecutor(visual=visual)
    executor.setup()
    state.log("Browser launched", "success")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Login
    login_ok = True
    if credentials and credentials.get("email"):
        if not do_login(executor, state, credentials):
            state.log("Login FAILED — skipping all items that require auth", "error")
            login_ok = False
    else:
        executor.navigate(url)
        state.pages_visited.add(url)

    # Execute checklist
    for idx, item in enumerate(checklist_items):
        state.current_item_idx = idx
        if state.stopped:
            item.status = "skip"
            continue
        # Skip all items after login failure (except first item which tests login itself)
        if not login_ok and idx > 0:
            item.status = "skip"
            item.result_detail = "Skipped: login prerequisite failed"
            state.log(f"  SKIP: {item.description} (login failed)", "warn")
            continue
        execute_checklist_item(executor, item, state, provider, model, messages)

    # Harness-level checks
    run_harness_checks(executor, state)

    # Reports
    state.write_reports()
    c = state.counts()
    state.log(f"\nDONE: {c['pass']} PASS, {c['fail']} FAIL, {c['error']} ERROR, {c['skip']} SKIP / {c['total']} total", "success")

    # Learn gotchas: scan passed items for suspicious verdicts
    _learn_gotchas(state)

    executor.teardown()

    if dashboard:
        state.log(f"Dashboard running at port {dashboard_port}. Ctrl+C to exit.", "info")
        state.log(f"View: http://100.122.177.91:{dashboard_port} (Tailscale)", "info")
        try:
            while True:
                time.sleep(1)
        except (KeyboardInterrupt, EOFError):
            dashboard.shutdown()

    return state


# ═══════════════════════════════════════════════════════════════════
# Dashboard (ported from v3 — identical functionality)
# ═══════════════════════════════════════════════════════════════════

DASH_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');
:root{
  --blue:#3b82f6;--blue-dark:#1d4ed8;--blue-light:#eff6ff;--blue-50:#f0f7ff;
  --pass:#10b981;--pass-bg:#ecfdf5;--pass-border:#6ee7b7;
  --fail:#ef4444;--fail-bg:#fef2f2;--fail-border:#fca5a5;
  --error:#f59e0b;--error-bg:#fef3c7;
  --skip-text:#94a3b8;
  --bg:#0f172a;--bg-card:#1e293b;--bg-surface:#334155;
  --text:#f1f5f9;--text-secondary:#94a3b8;--muted:#64748b;
  --border:#334155;--border-light:#475569;
  --shadow:0 1px 3px rgba(0,0,0,.3),0 1px 2px rgba(0,0,0,.2);
  --shadow-md:0 4px 12px rgba(0,0,0,.3);
  --shadow-lg:0 8px 30px rgba(0,0,0,.4);
  --glass:rgba(30,41,59,.7);
  --mono:'JetBrains Mono',monospace;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);font-size:14px;line-height:1.5;min-height:100vh}
body::before{content:'';position:fixed;top:0;left:0;right:0;height:400px;background:linear-gradient(180deg,rgba(59,130,246,.08) 0%,transparent 100%);pointer-events:none;z-index:0}

/* Header */
.hdr{background:var(--glass);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-bottom:1px solid rgba(255,255,255,.06);padding:1.5rem 2rem 1.25rem;position:sticky;top:0;z-index:100}
.hdr-top{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem}
.hdr h1{font-size:1.5rem;font-weight:800;letter-spacing:-.02em;display:flex;align-items:center;gap:.5rem;background:linear-gradient(135deg,#fff 0%,#94a3b8 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hdr .status-pill{font-size:.7rem;font-weight:600;padding:.25rem .6rem;border-radius:99px;letter-spacing:.04em;-webkit-text-fill-color:initial}
.status-testing{background:rgba(59,130,246,.2);color:#60a5fa;border:1px solid rgba(59,130,246,.3)}
.status-done{background:rgba(16,185,129,.15);color:#6ee7b7;border:1px solid rgba(16,185,129,.3)}
.status-paused{background:rgba(245,158,11,.15);color:#fbbf24;border:1px solid rgba(245,158,11,.3)}
.hdr .sub{font-size:.75rem;color:var(--text-secondary);margin-top:.35rem;font-family:var(--mono);letter-spacing:-.01em}
.pbar{margin-top:.75rem;background:rgba(255,255,255,.06);border-radius:99px;height:4px;overflow:hidden;max-width:100%}
.pbar-fill{height:100%;border-radius:99px;background:linear-gradient(90deg,#3b82f6,#8b5cf6,#ec4899);background-size:200% 100%;animation:shimmer 3s ease infinite;transition:width .8s cubic-bezier(.4,0,.2,1)}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
.pbar-label{font-size:.65rem;color:var(--muted);margin-top:.3rem;font-family:var(--mono)}

/* Stats grid */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(80px,1fr));gap:.5rem;margin-top:.75rem}
.stat{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06);padding:.5rem .6rem;border-radius:10px;text-align:center}
.stat b{display:block;font-weight:700;font-size:1.1rem;font-family:var(--mono);background:linear-gradient(135deg,#fff,#94a3b8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.stat span{font-size:.6rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;font-weight:500}
.stat.s-pass b{-webkit-text-fill-color:var(--pass)}
.stat.s-fail b{-webkit-text-fill-color:var(--fail)}

/* Controls */
.ctl{display:flex;align-items:center;gap:.5rem;padding:.5rem 2rem;background:var(--glass);backdrop-filter:blur(12px);border-bottom:1px solid rgba(255,255,255,.04)}
.ctl button{padding:.4rem .75rem;border-radius:8px;border:1px solid var(--border);background:rgba(255,255,255,.04);color:var(--text-secondary);font-size:.75rem;font-weight:500;cursor:pointer;font-family:inherit;transition:all .15s ease}
.ctl button:hover{background:rgba(255,255,255,.1);border-color:var(--border-light);color:#fff;transform:translateY(-1px)}
.ctl button:active{transform:translateY(0)}
.ctl .danger{color:#f87171;border-color:rgba(248,113,113,.3)}
.ctl .danger:hover{background:rgba(239,68,68,.15);border-color:rgba(248,113,113,.5);color:#fca5a5}
.ctl-sep{width:1px;height:18px;background:var(--border);margin:0 .15rem}

/* Content */
.wrap{max-width:860px;margin:0 auto;padding:1.25rem 1.25rem 4rem;position:relative;z-index:1}

/* Checklist items */
.it{background:var(--bg-card);border:1px solid var(--border);border-radius:12px;margin-bottom:.5rem;padding:.85rem 1rem;transition:all .25s ease;animation:fadeSlideIn .35s ease both}
.it:hover{border-color:var(--border-light);transform:translateY(-1px);box-shadow:var(--shadow-md)}
.it.running{border-color:rgba(59,130,246,.5);background:linear-gradient(135deg,rgba(59,130,246,.08) 0%,var(--bg-card) 100%);box-shadow:0 0 0 1px rgba(59,130,246,.2),0 0 20px rgba(59,130,246,.1);animation:fadeSlideIn .35s ease both,glowPulse 2.5s ease-in-out infinite}
.it.pass{border-left:3px solid var(--pass)}
.it.fail,.it.error{border-left:3px solid var(--fail)}
.it.skip{opacity:.35}
.it-h{display:flex;align-items:center;gap:.6rem;font-size:.85rem}
.it-n{width:30px;height:30px;border-radius:10px;background:rgba(255,255,255,.05);color:var(--muted);display:flex;align-items:center;justify-content:center;font-weight:600;font-size:.75rem;flex-shrink:0;font-family:var(--mono);border:1px solid rgba(255,255,255,.06)}
.it.pass .it-n{background:rgba(16,185,129,.12);color:var(--pass);border-color:rgba(16,185,129,.2)}
.it.fail .it-n,.it.error .it-n{background:rgba(239,68,68,.12);color:var(--fail);border-color:rgba(239,68,68,.2)}
.it-d{font-weight:600;flex:1;line-height:1.35;font-size:.84rem}
.badge{padding:.2rem .6rem;border-radius:99px;font-size:.6rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;white-space:nowrap}
.b-pass{background:rgba(16,185,129,.12);color:#6ee7b7;border:1px solid rgba(16,185,129,.2)}
.b-fail{background:rgba(239,68,68,.12);color:#fca5a5;border:1px solid rgba(239,68,68,.2)}
.b-err{background:rgba(245,158,11,.12);color:#fbbf24;border:1px solid rgba(245,158,11,.2)}
.b-run{background:rgba(59,130,246,.12);color:#60a5fa;border:1px solid rgba(59,130,246,.2);animation:pulse 1.5s infinite}
.b-pend{background:rgba(255,255,255,.03);color:var(--muted);border:1px solid rgba(255,255,255,.06);font-size:.55rem}
.b-skip{background:rgba(255,255,255,.03);color:var(--muted);border:1px solid rgba(255,255,255,.06)}

/* Item meta row */
.it-meta{display:flex;gap:.75rem;margin-top:.3rem;font-size:.65rem;color:var(--muted);font-family:var(--mono)}
.it-meta span{display:flex;align-items:center;gap:.2rem}

/* Animations */
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
@keyframes fadeSlideIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
@keyframes glowPulse{0%,100%{box-shadow:0 0 0 1px rgba(59,130,246,.2),0 0 20px rgba(59,130,246,.1)}50%{box-shadow:0 0 0 2px rgba(59,130,246,.3),0 0 30px rgba(59,130,246,.15)}}

/* Details */
.detail{font-size:.76rem;color:var(--text-secondary);margin-top:.35rem;line-height:1.45;padding-left:36px}
.detail-how{font-size:.72rem;color:var(--muted);margin-top:.15rem;font-style:italic;padding-left:36px}
.thumb{margin-top:.4rem;padding-left:36px}
.thumb img{width:140px;height:auto;border-radius:8px;border:1px solid var(--border);cursor:pointer;transition:all .2s ease;opacity:.85}
.thumb img:hover{opacity:1;transform:scale(1.03);box-shadow:var(--shadow-md)}

/* Section headers */
.sec-h{font-weight:700;font-size:.72rem;margin:1.5rem 0 .6rem;color:var(--text-secondary);padding:.5rem .85rem;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:10px;display:flex;align-items:center;gap:.5rem;letter-spacing:.04em;text-transform:uppercase}
.sec-h::before{content:'';width:3px;height:14px;background:linear-gradient(180deg,var(--blue),#8b5cf6);border-radius:2px}
.sec-count{font-family:var(--mono);font-weight:400;color:var(--muted);font-size:.65rem;margin-left:auto}

/* Done banner */
.done{background:linear-gradient(135deg,rgba(16,185,129,.08) 0%,rgba(59,130,246,.06) 100%);border:1px solid rgba(16,185,129,.2);border-radius:14px;padding:1.5rem;text-align:center;margin-bottom:1.5rem;animation:fadeSlideIn .5s ease}
.done-title{font-size:1.1rem;font-weight:800;background:linear-gradient(135deg,#6ee7b7,#60a5fa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.done-warn .done-title{background:linear-gradient(135deg,#fbbf24,#f87171);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.done .done-stats{display:flex;justify-content:center;gap:2rem;margin-top:.75rem;font-size:.8rem;font-weight:500;color:var(--text-secondary)}
.done .done-stat{display:flex;align-items:center;gap:.3rem}
.done .done-stat b{font-family:var(--mono);font-size:1.2rem;color:#fff}

/* Score ring */
.score-ring{width:72px;height:72px;margin:0 auto .6rem;position:relative}
.score-ring svg{transform:rotate(-90deg)}
.score-ring .ring-bg{fill:none;stroke:rgba(255,255,255,.06);stroke-width:5}
.score-ring .ring-fg{fill:none;stroke-width:5;stroke-linecap:round;transition:stroke-dashoffset .8s ease}
.score-ring .ring-pass{stroke:var(--pass)}
.score-ring .ring-fail{stroke:var(--fail)}
.score-pct{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-weight:700;font-size:1rem;color:#fff}

/* Responsive */
@media(max-width:640px){
  .hdr{padding:1rem}
  .hdr h1{font-size:1.2rem}
  .stats{grid-template-columns:repeat(4,1fr);gap:.35rem}
  .stat{padding:.35rem .4rem}
  .stat b{font-size:.9rem}
  .ctl{padding:.4rem .75rem;gap:.3rem}
  .ctl button{padding:.3rem .5rem;font-size:.68rem}
  .wrap{padding:.75rem .5rem}
  .it{padding:.65rem .75rem;border-radius:10px}
  .it-d{font-size:.8rem}
  .detail,.detail-how,.thumb{padding-left:0}
}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

class DashHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        global _current_run_state
        # Server mode routing
        if _server_mode:
            if self.path == "/":
                if _current_run_state and _current_run_state.running:
                    self._html(self._render(_current_run_state))
                else:
                    self._html(self._render_landing())
                return
            elif self.path == "/history":
                self._html(self._render_history())
                return
            elif self.path == "/api/state":
                if _current_run_state:
                    self._json(_current_run_state.get_status_dict())
                else:
                    self._json({"running": False})
                return
            elif self.path.startswith("/screenshots/") and _current_run_state:
                self._serve_screenshot(_current_run_state, self.path[len("/screenshots/"):])
                return
            elif self.path.startswith("/reports/"):
                self._serve_report_file(self.path[len("/reports/"):])
                return
            # Fallback: landing
            self._html(self._render_landing())
            return

        # Non-server mode (original behavior)
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

    def _serve_report_file(self, path):
        """Serve a report.md from tests/reports/<run_name>/report.md"""
        # Sanitize: only allow alphanum, dash, underscore, dot, slash
        safe = re.sub(r'[^a-zA-Z0-9\-_./]', '', path)
        filepath = Path(__file__).parent / "tests" / "reports" / safe
        if filepath.is_file() and filepath.suffix in ('.md', '.json', '.txt'):
            self.send_response(200)
            ct = "text/plain; charset=utf-8" if filepath.suffix != '.json' else "application/json"
            self.send_header("Content-Type", ct)
            self.end_headers()
            self.wfile.write(filepath.read_bytes())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/run" and _server_mode:
            self._handle_run()
            return
        if self.path == "/api/command":
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n)) if n else {}
            if _server_mode and _current_run_state:
                _current_run_state.push_cmd(body)
            elif hasattr(self.server, 'state'):
                self.server.state.push_cmd(body)
            self._json({"ok": True})

    def _handle_run(self):
        """POST /api/run — launch a test from the project picker."""
        global _current_run_state, _current_run_thread
        if _current_run_state and _current_run_state.running:
            self._json({"ok": False, "error": "A test is already running"})
            return
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n)) if n else {}
        project_id = body.get("project_id")
        provider = body.get("provider", "ollama")
        model = body.get("model", "qwen3-coder:30b")

        # Load project
        projects = json.loads(PROJECTS_FILE.read_text()) if PROJECTS_FILE.is_file() else []
        proj = next((p for p in projects if p["id"] == project_id), None)
        if not proj:
            self._json({"ok": False, "error": f"Unknown project: {project_id}"})
            return

        url = proj.get("url", "")
        if not url:
            self._json({"ok": False, "error": f"Project {project_id} has no URL"})
            return

        # Credentials
        creds = {}
        if proj.get("email"):
            creds = {
                "email": proj["email"],
                "password": proj.get("password", ""),
                "login_url": proj.get("login_url", url.rstrip("/") + "/login"),
            }

        # Checklist — prefer request override, then project default
        items = []
        cl_path = body.get("checklist", "") or proj.get("checklist", "")
        if cl_path and os.path.isfile(cl_path):
            with open(cl_path) as f:
                items = parse_checklist(f.read())
        elif cl_path and os.path.isfile(Path(__file__).parent / cl_path):
            with open(Path(__file__).parent / cl_path) as f:
                items = parse_checklist(f.read())
        if not items and cl_path and cl_path.endswith('.md'):
            # Try flow file format as fallback
            if os.path.isfile(cl_path):
                with open(cl_path) as f:
                    steps = parse_flow_file(f.read())
                    if steps:
                        items = flow_to_checklist(steps)
            elif os.path.isfile(Path(__file__).parent / cl_path):
                with open(Path(__file__).parent / cl_path) as f:
                    steps = parse_flow_file(f.read())
                    if steps:
                        items = flow_to_checklist(steps)
        if not items:
            items = generate_auto_checklist(url)

        # Resolve ollama URL
        ollama_url = OLLAMA_URL
        for m in MODEL_MENU:
            if m["label"] == model:
                if m.get("url"):
                    ollama_url = m["url"]
                provider = m["provider"]
                break

        report_dir = f"tests/reports/qa-{datetime.now():%Y%m%d-%H%M%S}"
        run_id = f"run-{datetime.now():%Y%m%d-%H%M%S}"

        # Create state early so dashboard can show progress during run
        _current_run_state = AgentState(report_dir, url=url, model=model, provider=provider)
        _current_run_state.checklist = items
        pre_state = _current_run_state

        def _run():
            global _current_run_state
            try:
                run_agent(url, provider, model, items, report_dir,
                          credentials=creds, no_dashboard=True,
                          ollama_url=ollama_url, _state=pre_state)
            except Exception as e:
                print(f"[SERVER] Run failed: {e}", flush=True)
            finally:
                if _current_run_state:
                    _current_run_state.running = False

        _current_run_thread = threading.Thread(target=_run, daemon=True)
        _current_run_thread.start()

        self._json({"ok": True, "run_id": run_id, "project": proj["name"]})

    def _render_landing(self):
        """Render the project picker page."""
        projects = json.loads(PROJECTS_FILE.read_text()) if PROJECTS_FILE.is_file() else []

        # Model options
        model_opts = ""
        for m in MODEL_MENU:
            model_opts += f'<option value="{m["label"]}" data-provider="{m["provider"]}">{m["label"]} ({m["host"]} — {m["desc"]})</option>'

        # Project cards with test discovery
        cards = ""
        for p in projects:
            has_url = bool(p.get("url"))
            disabled = "" if has_url else 'disabled title="No URL configured"'
            url_display = p.get("url", "No URL")[:50] if has_url else '<em style="color:var(--muted)">No URL configured</em>'

            # Discover test files
            test_files = []
            # Explicit checklist
            cl = p.get("checklist", "")
            if cl and os.path.isfile(cl):
                test_files.append(("checklist", cl, os.path.basename(cl)))
            elif cl and os.path.isfile(Path(__file__).parent / cl):
                test_files.append(("checklist", str(Path(__file__).parent / cl), os.path.basename(cl)))
            # Scan test_dir for flow files
            test_dir = p.get("test_dir", "")
            if test_dir and os.path.isdir(test_dir):
                for f in sorted(Path(test_dir).glob("flow-*.md")):
                    test_files.append(("flow", str(f), f.name))
                for f in sorted(Path(test_dir).glob("*.spec.ts")):
                    test_files.append(("spec", str(f), f.name))
                # Check for TEST_PLAN.md
                tp = Path(test_dir) / "TEST_PLAN.md"
                if tp.is_file():
                    test_files.append(("plan", str(tp), "TEST_PLAN.md"))
            # Scan repo tests/ dir as fallback
            repo = p.get("repo", "")
            if repo and not test_files:
                for pattern in ["tests/**/*.md", "scripts/test-*.md"]:
                    for f in sorted(Path(repo).glob(pattern)):
                        if "node_modules" not in str(f) and ".next" not in str(f):
                            test_files.append(("md", str(f), f.name))
                            if len(test_files) >= 10:
                                break

            # Build test selector dropdown
            if test_files:
                opts = '<option value="">Auto-discover</option>'
                for ttype, tpath, tname in test_files:
                    label = {"flow": "Flow", "spec": "Spec", "checklist": "Checklist", "plan": "Plan", "md": "Test"}.get(ttype, "")
                    opts += f'<option value="{tpath}">[{label}] {tname}</option>'
                test_select = f'<select class="test-select" id="tests-{p["id"]}" style="width:100%;margin-bottom:.5rem;padding:.4rem .6rem;border-radius:8px;border:1px solid var(--border);background:var(--bg-card);color:var(--text);font-size:.72rem;font-family:var(--mono)">{opts}</select>'
                test_badge = f'<span class="badge b-pass">{len(test_files)} test{"s" if len(test_files)!=1 else ""}</span>'
            else:
                test_select = ''
                test_badge = '<span class="badge b-pend">Auto-discover</span>'

            cards += f'''<div class="proj-card" data-id="{p["id"]}">
<div class="proj-name">{p["name"]}</div>
<div class="proj-url">{url_display}</div>
<div class="proj-badges">{test_badge}</div>
{test_select}
<button class="proj-run" {disabled} onclick="launchRun('{p["id"]}')">Run Test</button>
</div>'''

        return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>QA Agent — Server</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#9889;</text></svg>">
<meta http-equiv="refresh" content="5">
<style>{DASH_CSS}
.landing{{max-width:860px;margin:0 auto;padding:2rem 1.25rem}}
.landing-title{{font-size:1.8rem;font-weight:800;margin-bottom:.3rem;background:linear-gradient(135deg,#fff 0%,#94a3b8 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.landing-sub{{color:var(--muted);font-size:.85rem;margin-bottom:1.5rem}}
.config-row{{display:flex;gap:.75rem;margin-bottom:1.5rem;flex-wrap:wrap}}
.config-row select{{flex:1;min-width:200px;padding:.55rem .75rem;border-radius:10px;border:1px solid var(--border);background:var(--bg-card);color:var(--text);font-size:.8rem;font-family:inherit}}
.config-row select:focus{{outline:none;border-color:var(--blue)}}
.proj-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:.75rem}}
.proj-card{{background:var(--bg-card);border:1px solid var(--border);border-radius:14px;padding:1.1rem;transition:all .2s ease}}
.proj-card:hover{{border-color:var(--border-light);transform:translateY(-2px);box-shadow:var(--shadow-md)}}
.proj-name{{font-weight:700;font-size:.95rem;margin-bottom:.3rem}}
.proj-url{{font-size:.72rem;color:var(--muted);font-family:var(--mono);margin-bottom:.6rem;word-break:break-all}}
.proj-badges{{margin-bottom:.7rem}}
.proj-run{{width:100%;padding:.55rem;border-radius:10px;border:1px solid rgba(59,130,246,.4);background:rgba(59,130,246,.12);color:#60a5fa;font-size:.8rem;font-weight:600;cursor:pointer;font-family:inherit;transition:all .15s ease}}
.proj-run:hover:not(:disabled){{background:rgba(59,130,246,.25);border-color:rgba(59,130,246,.6);transform:translateY(-1px)}}
.proj-run:disabled{{opacity:.3;cursor:not-allowed}}
.nav-bar{{display:flex;gap:1rem;margin-bottom:1.5rem}}
.nav-bar a{{color:var(--text-secondary);text-decoration:none;font-size:.8rem;font-weight:500;padding:.4rem .8rem;border-radius:8px;border:1px solid var(--border);transition:all .15s ease}}
.nav-bar a:hover,.nav-bar a.active{{color:#fff;background:rgba(255,255,255,.06);border-color:var(--border-light)}}
</style></head><body>
<div class="landing">
<div class="landing-title">QA Agent</div>
<div class="landing-sub">Select a project and model, then hit Run Test.</div>
<div class="nav-bar">
<a href="/" class="active">Projects</a>
<a href="/history">History</a>
</div>
<div class="config-row">
<select id="model-select">{model_opts}</select>
</div>
<div class="proj-grid">{cards}</div>
</div>
<script>
function launchRun(projectId) {{
  var sel = document.getElementById('model-select');
  var opt = sel.options[sel.selectedIndex];
  var model = sel.value;
  var provider = opt.getAttribute('data-provider');
  var testSel = document.getElementById('tests-' + projectId);
  var checklist = testSel ? testSel.value : '';
  fetch('/api/run', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{project_id: projectId, model: model, provider: provider, checklist: checklist}})
  }}).then(r => r.json()).then(d => {{
    if (d.ok) {{ window.location.href = '/'; }}
    else {{ alert('Error: ' + d.error); }}
  }}).catch(e => alert('Request failed: ' + e));
}}
</script></body></html>"""

    def _render_history(self):
        """Render the past runs history page."""
        reports_dir = Path(__file__).parent / "tests" / "reports"
        runs = []
        if reports_dir.is_dir():
            for d in sorted(reports_dir.iterdir(), reverse=True):
                if not d.is_dir():
                    continue
                report_json = d / "report.json"
                report_md = d / "report.md"
                if report_json.is_file():
                    try:
                        data = json.loads(report_json.read_text())
                        c = data.get("counts", {})
                        runs.append({
                            "name": d.name,
                            "url": data.get("url", ""),
                            "model": data.get("model", ""),
                            "provider": data.get("provider", ""),
                            "pass": c.get("pass", 0),
                            "fail": c.get("fail", 0),
                            "error": c.get("error", 0),
                            "total": c.get("total", 0),
                            "elapsed": data.get("elapsed", ""),
                            "has_md": report_md.is_file(),
                        })
                    except Exception:
                        runs.append({"name": d.name, "url": "", "model": "", "provider": "",
                                     "pass": 0, "fail": 0, "error": 0, "total": 0,
                                     "elapsed": "", "has_md": report_md.is_file()})
                elif report_md.is_file():
                    runs.append({"name": d.name, "url": "", "model": "", "provider": "",
                                 "pass": 0, "fail": 0, "error": 0, "total": 0,
                                 "elapsed": "", "has_md": True})

        rows = ""
        for r in runs[:50]:
            score = int(r["pass"] / max(r["total"], 1) * 100) if r["total"] else 0
            score_color = "var(--pass)" if score >= 80 else ("var(--error)" if score >= 50 else "var(--fail)")
            link = f'<a href="/reports/{r["name"]}/report.md" target="_blank" style="color:var(--blue);text-decoration:none">View</a>' if r["has_md"] else ''
            rows += f'''<tr>
<td style="font-family:var(--mono);font-size:.75rem">{r["name"]}</td>
<td style="font-size:.75rem">{r["url"][:40]}</td>
<td style="font-size:.75rem">{r["model"][:20]}</td>
<td style="color:{score_color};font-weight:700;font-family:var(--mono)">{score}%</td>
<td style="font-family:var(--mono);font-size:.75rem"><span style="color:var(--pass)">{r["pass"]}</span>/<span style="color:var(--fail)">{r["fail"]}</span>/{r["total"]}</td>
<td style="font-size:.75rem">{r["elapsed"]}</td>
<td>{link}</td>
</tr>'''

        return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>QA Agent — History</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#9889;</text></svg>">
<style>{DASH_CSS}
.landing{{max-width:960px;margin:0 auto;padding:2rem 1.25rem}}
.landing-title{{font-size:1.8rem;font-weight:800;margin-bottom:.3rem;background:linear-gradient(135deg,#fff 0%,#94a3b8 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.landing-sub{{color:var(--muted);font-size:.85rem;margin-bottom:1.5rem}}
.nav-bar{{display:flex;gap:1rem;margin-bottom:1.5rem}}
.nav-bar a{{color:var(--text-secondary);text-decoration:none;font-size:.8rem;font-weight:500;padding:.4rem .8rem;border-radius:8px;border:1px solid var(--border);transition:all .15s ease}}
.nav-bar a:hover,.nav-bar a.active{{color:#fff;background:rgba(255,255,255,.06);border-color:var(--border-light)}}
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;font-size:.65rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);padding:.6rem .5rem;border-bottom:1px solid var(--border)}}
td{{padding:.65rem .5rem;border-bottom:1px solid rgba(255,255,255,.04);vertical-align:middle}}
tr:hover td{{background:rgba(255,255,255,.02)}}
</style></head><body>
<div class="landing">
<div class="landing-title">Test History</div>
<div class="landing-sub">{len(runs)} past runs found</div>
<div class="nav-bar">
<a href="/">Projects</a>
<a href="/history" class="active">History</a>
</div>
<table>
<thead><tr><th>Run</th><th>URL</th><th>Model</th><th>Score</th><th>P/F/T</th><th>Time</th><th></th></tr></thead>
<tbody>{rows}</tbody>
</table>
</div></body></html>"""

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
        completed = c["pass"]+c["fail"]+c["error"]+c["skip"]
        done_pct = int(completed / max(c["total"],1) * 100)
        pass_pct = int(c["pass"] / max(completed,1) * 100) if completed else 0
        status = "PAUSED" if s.paused else ("DONE" if not s.running else "TESTING")
        status_css = {"TESTING":"status-testing","DONE":"status-done","PAUSED":"status-paused"}[status]

        # Section counts
        sec_counts = {}
        for it in s.checklist:
            sec_counts.setdefault(it.section, {"total":0,"pass":0,"fail":0})
            sec_counts[it.section]["total"] += 1
            if it.status == "pass": sec_counts[it.section]["pass"] += 1
            elif it.status in ("fail","error"): sec_counts[it.section]["fail"] += 1

        items = ""
        sec = ""
        for i, it in enumerate(s.checklist):
            if it.section != sec:
                sec = it.section
                sc = sec_counts.get(sec, {})
                sc_label = f'{sc.get("pass",0)}/{sc.get("total",0)}'
                items += f'<div class="sec-h">{sec}<span class="sec-count">{sc_label}</span></div>'
            css = f"it {it.status}" if it.status != "pending" else "it"
            if i == s.current_item_idx and s.running:
                css = "it running"
            bc = {"pass":"b-pass","fail":"b-fail","error":"b-err","skip":"b-skip","running":"b-run"}.get(it.status, "b-pend")
            bt = it.status.upper() if it.status != "pending" else "..."
            det = f'<div class="detail">{it.result_detail[:150]}</div>' if it.result_detail else ""
            if it.how:
                det += f'<div class="detail-how">{it.how[:120]}</div>'
            # Meta row: attempts, model time, URL
            meta_parts = []
            if it.attempts:
                meta_parts.append(f'{it.attempts} attempt{"s" if it.attempts != 1 else ""}')
            if it.model_time > 0:
                meta_parts.append(f'{it.model_time:.1f}s model')
            if it.url:
                path = it.url.split("//",1)[-1].split("/",1)[-1] if "//" in it.url else it.url
                meta_parts.append(f'/{path[:40]}')
            if meta_parts:
                det += '<div class="it-meta">' + ''.join(f'<span>{p}</span>' for p in meta_parts) + '</div>'
            if it.screenshot:
                sc_name = os.path.basename(it.screenshot)
                det += f'<div class="thumb"><a href="/screenshots/{sc_name}" target="_blank"><img src="/screenshots/{sc_name}" alt="screenshot" loading="lazy"></a></div>'
            items += f'<div class="{css}" style="animation-delay:{i*0.02}s"><div class="it-h"><div class="it-n">{it.step_id}</div><div class="it-d">{it.description}</div><span class="badge {bc}">{bt}</span></div>{det}</div>'

        # Score ring SVG
        circ = 2 * 3.14159 * 28
        pass_offset = circ - (pass_pct / 100 * circ)
        fail_offset = circ - ((100 - pass_pct) / 100 * circ) if completed else circ

        # Done banner
        done_b = ""
        if not s.running:
            warn = ' done-warn' if c["fail"] > 0 else ''
            done_b = f'''<div class="done{warn}">
<div class="score-ring"><svg viewBox="0 0 64 64" width="72" height="72">
<circle class="ring-bg" cx="32" cy="32" r="28"/><circle class="ring-fg ring-pass" cx="32" cy="32" r="28"
style="stroke-dasharray:{circ};stroke-dashoffset:{pass_offset}"/></svg>
<div class="score-pct">{pass_pct}%</div></div>
<div class="done-title">{"All Tests Passed" if c["fail"]==0 else f'{c["fail"]} Test{"s" if c["fail"]!=1 else ""} Failed'}</div>
<div class="done-stats">
<span class="done-stat"><b>{c["pass"]}</b> passed</span>
<span class="done-stat"><b>{c["fail"]}</b> failed</span>
<span class="done-stat"><b>{s.elapsed()}</b></span>
</div></div>'''

        # Stats
        stats_html = f'''
<div class="stat s-pass"><b>{c["pass"]}</b><span>Pass</span></div>
<div class="stat s-fail"><b>{c["fail"]}</b><span>Fail</span></div>
<div class="stat"><b>{c["error"]}</b><span>Error</span></div>
<div class="stat"><b>{c["total"]}</b><span>Total</span></div>
<div class="stat"><b>{len(s.pages_visited)}</b><span>Pages</span></div>
<div class="stat"><b>{len(s.all_console_errors)}</b><span>Console</span></div>
<div class="stat"><b>{len(s.all_network_errors)}</b><span>Network</span></div>'''

        return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>QA Agent — {status}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#9889;</text></svg>">
<meta http-equiv="refresh" content="2"><style>{DASH_CSS}</style></head><body>
<div class="hdr">
<div class="hdr-top"><div><h1>QA Agent</h1>
<div class="sub">{s.url} &middot; {s.model} ({s.provider}) &middot; {s.elapsed()}</div></div>
<span class="status-pill {status_css}">{status}</span></div>
<div class="pbar"><div class="pbar-fill" style="width:{done_pct}%"></div></div>
<div class="pbar-label">{done_pct}% &middot; {completed}/{c["total"]} items</div>
<div class="stats">{stats_html}</div>
</div>
<div class="ctl">
<button onclick="fetch('/api/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{cmd:'pause'}})}})">Pause</button>
<button onclick="fetch('/api/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{cmd:'resume'}})}})">Resume</button>
<span class="ctl-sep"></span>
<button onclick="fetch('/api/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{cmd:'skip'}})}})">Skip</button>
<button class="danger" onclick="fetch('/api/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{cmd:'stop'}})}})">Stop</button>
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
    parser.add_argument("--server", action="store_true",
                        help="Persistent dashboard server mode (project picker, run history)")
    parser.add_argument("--model", default="qwen3-coder:30b")
    parser.add_argument("--provider", choices=["ollama", "ollama-local", "nim"], default="ollama")
    parser.add_argument("--ollama-url", default=None)
    parser.add_argument("--report-dir", default=None)
    parser.add_argument("--checklist", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--login-url", default=None)
    parser.add_argument("--supabase-url", default=None, help="Supabase project URL for token-injection fallback")
    parser.add_argument("--supabase-anon-key", default=None, help="Supabase anon key for token-injection fallback")
    parser.add_argument("--supabase-service-key", default=None, help="Supabase service role key (bypasses rate limits)")
    parser.add_argument("--supabase-session-file", default=None, help="Path to pre-generated session JSON file (skips login form entirely)")
    parser.add_argument("--dashboard-port", type=int, default=9876)
    parser.add_argument("--no-dashboard", action="store_true")
    parser.add_argument("--visual", action="store_true",
                        help="Headed browser with element highlights and slow_mo (watch it work)")
    parser.add_argument("--compare", default=None,
                        help="Compare models: 'ollama:qwen3-coder:30b,nim:kimi-k2.5'")
    args = parser.parse_args()

    # ── Server mode ──────────────────────────────────────────────
    if args.server:
        global _server_mode
        _server_mode = True
        port = args.dashboard_port
        srv = HTTPServer(("0.0.0.0", port), DashHandler)
        ip = "0.0.0.0"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
        print(f"\n  QA Agent Server")
        print(f"  http://{ip}:{port}")
        print(f"  http://100.122.177.91:{port} (Tailscale)")
        print(f"  Ctrl+C to stop\n", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n  Server stopped.")
            srv.shutdown()
        return

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
            if args.supabase_url:
                creds["supabase_url"] = args.supabase_url
            if args.supabase_anon_key:
                creds["supabase_anon_key"] = args.supabase_anon_key
            if args.supabase_service_key:
                creds["supabase_service_key"] = args.supabase_service_key
            if args.supabase_session_file:
                creds["supabase_session_file"] = args.supabase_session_file
        items = []
        if args.checklist and os.path.isfile(args.checklist):
            with open(args.checklist) as f:
                items = parse_checklist(f.read())
        elif args.checklist and args.checklist.endswith('.md'):
            # Try flow file format as fallback
            with open(args.checklist) as f:
                steps = parse_flow_file(f.read())
                if steps:
                    items = flow_to_checklist(steps)
        if not items:
            items = generate_auto_checklist(url)

        if args.compare:
            _run_comparison(args, url, creds, items, ollama_url)
            return

    if not args.report_dir:
        args.report_dir = f"tests/reports/qa-{datetime.now():%Y%m%d-%H%M%S}"

    run_agent(url, provider, model, items, args.report_dir,
              credentials=creds, dashboard_port=args.dashboard_port,
              no_dashboard=args.no_dashboard, ollama_url=ollama_url,
              visual=args.visual)


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
        elif args.checklist and args.checklist.endswith('.md'):
            # Try flow file format as fallback
            with open(args.checklist) as f:
                steps = parse_flow_file(f.read())
                if steps:
                    fresh_items = flow_to_checklist(steps)
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
