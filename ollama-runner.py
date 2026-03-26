#!/usr/bin/env python3
"""
ollama-runner.py — Local LLM task runner with tool-call support.

Reads a task file, executes it using an ollama model with tool calls
(read/write/bash), writes status and result files for Opus to monitor.

Usage:
    python3 ollama-runner.py /tmp/task-fixlist.md
    python3 ollama-runner.py /tmp/task-fixlist.md --model qwen3-coder:30b
    python3 ollama-runner.py /tmp/task-fixlist.md --host 100.75.112.14:11434
"""

import json
import sys
import os
import re
import time
import hashlib
import subprocess
import argparse
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

DEFAULT_MODEL = "qwen3-coder:30b"
DEFAULT_HOST = "http://100.75.112.14:11434"  # FIRMAMENT
NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_KEY = os.environ.get("NIM_API_KEY", "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")
NIM_MODEL = "moonshotai/kimi-k2.5"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-6c421e24b1e644749a81d2666fd7239b")
DEEPSEEK_MODEL = "deepseek-chat"
MODEL_REGISTRY_PATH = Path.home() / "projects" / "sysadmin" / "model-registry.json"
MAX_TURNS = 150
MAX_TOOL_ERRORS = 10


def load_model_registry():
    """Load model registry and build lookup tables."""
    try:
        with open(MODEL_REGISTRY_PATH) as f:
            registry = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, {}

    models = {}  # id -> model entry
    aliases = {}  # short name -> id
    for m in registry.get("models", []):
        if m.get("type") != "llm":
            continue
        mid = m["id"]
        models[mid] = m
        # Auto-generate aliases: first word, name without tag
        short = mid.split(":")[0].split("/")[-1]
        if short not in aliases:
            aliases[short] = mid
    return models, aliases


def resolve_model(name):
    """Resolve a model name/alias to (model_id, provider, host).

    Returns (model_id, provider, host) or (name, None, None) if not found in registry.
    """
    models, aliases = load_model_registry()

    # Direct match
    if name in models:
        m = models[name]
        provider = m["provider"]
        if provider == "ollama-cloud":
            provider = "ollama"  # same API, just no GPU
        host = m.get("endpoint", DEFAULT_HOST)
        return name, provider, host

    # Alias match
    if name in aliases:
        return resolve_model(aliases[name])

    # Fuzzy: check if name is a substring of any model id
    for mid in models:
        if name.lower() in mid.lower():
            return resolve_model(mid)

    return name, None, None


def list_models():
    """Print available models from registry."""
    models, aliases = load_model_registry()
    print(f"\n{'='*65}")
    print(f"  Available Models (from {MODEL_REGISTRY_PATH.name})")
    print(f"{'='*65}")
    print(f"  {'Model':<25} {'Provider':<15} {'Host':<12} {'Cost':<6} {'Notes'}")
    print(f"  {'-'*25} {'-'*15} {'-'*12} {'-'*6} {'-'*20}")
    for mid, m in sorted(models.items(), key=lambda x: (x[1].get("cost", ""), x[0])):
        provider = m.get("provider", "?")
        host = m.get("host", "?")
        cost = m.get("cost", "?")
        notes = (m.get("notes", "") or "")[:30]
        print(f"  {mid:<25} {provider:<15} {host:<12} {cost:<6} {notes}")
    print(f"\n  Aliases: {', '.join(f'{a}={v}' for a, v in sorted(aliases.items()))}")
    print()

# ═══════════════════════════════════════════════════════════════════
# Tool Definitions (what the LLM can call)
# ═══════════════════════════════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from disk. Returns the file contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"},
                    "offset": {"type": "integer", "description": "Start line (1-based, optional)"},
                    "limit": {"type": "integer", "description": "Max lines to read (optional)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file. Creates parent dirs. Overwrites existing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"},
                    "content": {"type": "string", "description": "Full file content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace a string in a file. old_string must be unique in the file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"},
                    "old_string": {"type": "string", "description": "Exact string to find"},
                    "new_string": {"type": "string", "description": "Replacement string"}
                },
                "required": ["path", "old_string", "new_string"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a bash command. Returns stdout+stderr. Timeout 60s.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents using ripgrep. Returns matching lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "path": {"type": "string", "description": "File or directory to search"},
                    "glob": {"type": "string", "description": "File glob filter (e.g. '*.py')"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Signal that the task is complete. Call this when finished.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Brief summary of what was done"},
                    "files_changed": {"type": "array", "items": {"type": "string"}, "description": "List of files modified"}
                },
                "required": ["summary"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "blocked",
            "description": "Signal that you're stuck and need help from the supervising session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "What you're stuck on"},
                    "context": {"type": "string", "description": "Relevant context for the supervisor"}
                },
                "required": ["reason"]
            }
        }
    }
]

# ═══════════════════════════════════════════════════════════════════
# Tool Execution
# ═══════════════════════════════════════════════════════════════════

def exec_read_file(args):
    path = os.path.expanduser(args["path"])
    if not os.path.isfile(path):
        return f"ERROR: File not found: {path}"
    try:
        with open(path, "r") as f:
            lines = f.readlines()
        offset = args.get("offset", 1) - 1
        limit = args.get("limit", len(lines))
        selected = lines[max(0, offset):offset + limit]
        return "".join(f"{offset + i + 1}\t{line}" for i, line in enumerate(selected))
    except Exception as e:
        return f"ERROR: {e}"


def exec_write_file(args):
    path = os.path.expanduser(args["path"])
    content = args["content"]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"OK: wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"ERROR: {e}"


def exec_edit_file(args):
    path = os.path.expanduser(args["path"])
    old = args["old_string"]
    new = args["new_string"]
    if not os.path.isfile(path):
        return f"ERROR: File not found: {path}"
    try:
        content = open(path).read()
        count = content.count(old)
        if count == 0:
            return f"ERROR: old_string not found in {path}"
        if count > 1:
            return f"ERROR: old_string found {count} times — must be unique"
        content = content.replace(old, new, 1)
        with open(path, "w") as f:
            f.write(content)
        return f"OK: replaced in {path}"
    except Exception as e:
        return f"ERROR: {e}"


def exec_bash(args):
    cmd = args["command"]
    # Safety: block destructive commands
    blocked = ["rm -rf /", "mkfs", "dd if=", "> /dev/sd", "shutdown", "reboot"]
    if any(b in cmd for b in blocked):
        return "ERROR: blocked destructive command"
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        output = result.stdout + result.stderr
        return output[:5000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out (60s)"
    except Exception as e:
        return f"ERROR: {e}"


def exec_grep(args):
    pattern = args["pattern"]
    path = args.get("path", ".")
    glob_filter = args.get("glob", "")
    cmd = f"rg --no-heading -n '{pattern}'"
    if glob_filter:
        cmd += f" --glob '{glob_filter}'"
    cmd += f" {path} 2>&1 | head -50"
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return result.stdout[:5000] if result.stdout else "(no matches)"
    except Exception as e:
        return f"ERROR: {e}"


TOOL_HANDLERS = {
    "read_file": exec_read_file,
    "write_file": exec_write_file,
    "edit_file": exec_edit_file,
    "bash": exec_bash,
    "grep": exec_grep,
}

# ═══════════════════════════════════════════════════════════════════
# Ollama Chat API
# ═══════════════════════════════════════════════════════════════════

def ollama_chat(host, model, messages, tools=None, ctx_size=32768):
    """Call ollama /api/chat with tool support."""
    url = f"{host}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_ctx": ctx_size},
    }
    if tools:
        payload["tools"] = tools
    # Suppress thinking mode for qwen models
    if "qwen" in model.lower():
        payload["options"]["think"] = False

    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}

def nim_chat(model, messages, tools=None):
    """Call NIM (OpenAI-compatible) /v1/chat/completions with tool support."""
    payload = {
        "model": model or NIM_MODEL,
        "messages": messages,
        "max_tokens": 4096,
    }
    if tools:
        payload["tools"] = tools

    data = json.dumps(payload).encode()
    req = urllib.request.Request(NIM_URL, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {NIM_KEY}",
    })
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        return {"error": f"HTTP {e.code}: {body}"}
    except urllib.error.URLError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}

    if "error" in result:
        return result
    choice = result.get("choices", [{}])[0]
    msg = choice.get("message", {})
    # Parse tool_calls — keep raw for history, normalize for execution
    tool_calls = msg.get("tool_calls", []) or []
    normalized_calls = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        normalized_calls.append({
            "id": tc.get("id", ""),
            "function": {"name": fn.get("name", ""), "arguments": args}
        })
    return {
        "message": {
            "role": "assistant",
            "content": msg.get("content", "") or "",
            "tool_calls": normalized_calls if normalized_calls else []
        },
        # Keep raw message for appending to history (NIM expects its own format back)
        "_raw_message": msg
    }


def parse_tool_calls_from_text(text):
    """Extract tool calls from raw LLM text output.

    Uses a greedy approach: find {"name": "<tool>" and then progressively
    try json.loads on longer substrings until it parses. Handles nested
    braces in string values (like Python code in write_file content).
    """
    known_tools = {"read_file", "write_file", "edit_file", "bash", "grep", "done", "blocked"}
    calls = []

    # Remove markdown code fences
    clean = re.sub(r'```json\s*', '', text)
    clean = re.sub(r'```\s*', '', clean)

    for tool_name in known_tools:
        # Find all occurrences of {"name": "<tool_name>"
        pattern = r'\{"name":\s*"' + re.escape(tool_name) + r'"'
        for match in re.finditer(pattern, clean):
            start = match.start()
            # Try progressively longer substrings until JSON parses
            # Start from a reasonable minimum and work outward
            for end in range(start + 20, min(start + 50000, len(clean) + 1)):
                candidate = clean[start:end]
                # Quick check: must end with }
                if not candidate.rstrip().endswith('}'):
                    continue
                try:
                    obj = json.loads(candidate)
                    args = obj.get("parameters", obj.get("arguments", {}))
                    calls.append({
                        "id": f"call_{hashlib.md5(candidate.encode()).hexdigest()[:8]}",
                        "function": {"name": tool_name, "arguments": args}
                    })
                    break  # Found valid JSON for this match
                except (json.JSONDecodeError, ValueError):
                    continue

    return calls


def openai_chat(host, model, messages, tools=None):
    """Call any OpenAI-compatible endpoint (llama-server, etc) with tool support.

    Does NOT send tools in the API payload (llama-server's Jinja parser is broken
    for multi-line JSON arguments). Instead, tool descriptions are in the system
    prompt and we parse tool calls from the raw text response.
    """
    url = f"{host}/v1/chat/completions"
    payload = {
        "model": model or "local",
        "messages": messages,
        "max_tokens": 4096,
        # Do NOT send tools — llama-server's parser chokes on multi-line content
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=1200) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        return {"error": f"HTTP {e.code}: {body}"}
    except urllib.error.URLError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}

    if "error" in result:
        return result
    choice = result.get("choices", [{}])[0]
    msg = choice.get("message", {})
    content = msg.get("content", "") or ""

    # First try native tool_calls from the API response
    tool_calls = msg.get("tool_calls", []) or []
    normalized_calls = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        normalized_calls.append({
            "id": tc.get("id", ""),
            "function": {"name": fn.get("name", ""), "arguments": args}
        })

    # If no native tool calls, parse from text
    if not normalized_calls and content:
        normalized_calls = parse_tool_calls_from_text(content)

    return {
        "message": {
            "role": "assistant",
            "content": content,
            "tool_calls": normalized_calls
        },
        "_raw_message": msg
    }


def deepseek_chat(model, messages, tools=None):
    """Call DeepSeek API (OpenAI-compatible)."""
    payload = {
        "model": model or DEEPSEEK_MODEL,
        "messages": messages,
        "max_tokens": 4096,
    }
    if tools:
        payload["tools"] = tools

    data = json.dumps(payload).encode()
    req = urllib.request.Request(DEEPSEEK_URL, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_KEY}",
    })
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        return {"error": f"HTTP {e.code}: {body}"}
    except urllib.error.URLError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}

    if "error" in result:
        return result
    choice = result.get("choices", [{}])[0]
    msg = choice.get("message", {})

    tool_calls = msg.get("tool_calls", []) or []
    normalized_calls = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        normalized_calls.append({
            "id": tc.get("id", ""),
            "function": {"name": fn.get("name", ""), "arguments": args}
        })
    return {
        "message": {
            "role": "assistant",
            "content": msg.get("content", "") or "",
            "tool_calls": normalized_calls if normalized_calls else []
        },
        "_raw_message": msg
    }


def llm_chat(provider, model, host, messages, tools=None, ctx_size=32768):
    """Route to the right chat API based on provider."""
    if provider == "nim":
        return nim_chat(model, messages, tools)
    elif provider == "deepseek":
        return deepseek_chat(model, messages, tools)
    elif provider == "llama-server":
        return openai_chat(host, model, messages, tools)
    else:
        return ollama_chat(host, model, messages, tools, ctx_size=ctx_size)


# ═══════════════════════════════════════════════════════════════════
# Status File (for Opus to monitor)
# ═══════════════════════════════════════════════════════════════════

class StatusWriter:
    def __init__(self, task_file):
        self.status_file = Path(str(task_file).replace(".md", "-status.json"))
        self.log_file = Path(str(task_file).replace(".md", "-log.md"))
        self.turns = 0
        self.tool_calls = 0
        self.errors = 0
        self.files_changed = []
        self.state = "running"
        self.start_time = time.time()
        self.session_id = hashlib.md5(task_file.encode()).hexdigest()[:12]
        self.provider_history = []
        self.provider_switched = False
        self.log_file.write_text(f"# Local Runner Log\nStarted: {datetime.now():%H:%M:%S}\n\n")

        # Write JSONL transcript for CCBot Telegram forwarding
        # CCBot reads ~/.claude/projects/<project-path>/<session-id>.jsonl
        self._init_transcript()

    def _init_transcript(self):
        """Create a JSONL transcript file that CCBot can discover and forward to Telegram."""
        # Determine project path from CWD
        cwd = os.getcwd()
        # CCBot project dirs use dashes for path separators
        project_key = cwd.replace("/", "-").lstrip("-")
        transcript_dir = Path.home() / ".claude" / "projects" / project_key
        transcript_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_file = transcript_dir / f"{self.session_id}.jsonl"
        # Write initial session start entry
        self._write_transcript({
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": f"Local LLM runner started (session {self.session_id})"}]},
            "sessionId": self.session_id,
            "cwd": cwd,
        })

    def _write_transcript(self, entry):
        """Append a JSONL line that CCBot's session monitor can parse."""
        entry["timestamp"] = datetime.now().isoformat()
        try:
            with open(self.transcript_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def transcript_message(self, role, text):
        """Write a message to the transcript for CCBot to forward to Telegram."""
        self._write_transcript({
            "type": role,
            "message": {"role": role, "content": [{"type": "text", "text": text}]},
            "sessionId": self.session_id,
            "cwd": os.getcwd(),
        })

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        with open(self.log_file, "a") as f:
            f.write(line + "\n")
        # Forward key messages to CCBot transcript for Telegram topics
        if any(msg.startswith(p) for p in ["LLM:", "TOOL:", "DONE:", "BLOCKED:", "INJECTED",
                                            "OLLAMA ERROR", "Circuit breaker", "Provider:", "Model:", "Task:"]):
            self.transcript_message("assistant", msg)

    def update(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        self._write()

    def switch_provider(self, old_provider, new_provider, new_model=None):
        """Record a provider tier switch."""
        self.provider_switched = True
        entry = {
            "from": old_provider,
            "to": new_provider,
            "model": new_model,
            "turn": self.turns,
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        self.provider_history.append(entry)
        self.log(f"TIER SWITCH: {old_provider} → {new_provider}" +
                 (f" (model: {new_model})" if new_model else ""))
        self._write()

    def _write(self):
        elapsed = time.time() - self.start_time
        data = {
            "state": self.state,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "errors": self.errors,
            "files_changed": self.files_changed,
            "elapsed": f"{int(elapsed)}s",
            "updated": datetime.now().strftime("%H:%M:%S"),
            "provider_history": self.provider_history,
            "provider_switched": self.provider_switched,
        }
        self.status_file.write_text(json.dumps(data, indent=2))

# ═══════════════════════════════════════════════════════════════════
# Context Injection (Opus can write to this file mid-run)
# ═══════════════════════════════════════════════════════════════════

def check_context_injection(task_file):
    """Check if Opus has injected additional context."""
    inject_file = Path(str(task_file).replace(".md", "-inject.md"))
    if inject_file.is_file():
        content = inject_file.read_text().strip()
        if content:
            inject_file.write_text("")  # Clear after reading
            return content
    return None

# ═══════════════════════════════════════════════════════════════════
# Main Runner Loop
# ═══════════════════════════════════════════════════════════════════

def run(task_file, model, host, provider="ollama", ctx_size=None, fallback="none", fallback_model=None):
    task = Path(task_file).read_text()
    status = StatusWriter(task_file)

    # Track original provider for fallback logic
    original_provider = provider
    has_switched = False

    status.log(f"Provider: {provider}")
    status.log(f"Model: {model}")
    if fallback != "none":
        status.log(f"Fallback: {fallback}" + (f" ({fallback_model})" if fallback_model else ""))
    if provider != "nim":
        status.log(f"Host: {host}")
    status.log(f"Task: {task_file}")

    home_dir = os.path.expanduser("~")
    base_system = f"""You are a coding assistant executing a task. You have tools to read files, write files, edit files, run bash commands, and grep for patterns.

Environment:
- HOME directory: {home_dir}
- ALWAYS use absolute paths starting with {home_dir} — NEVER use ~/ or /home/user/
- Current working directory: {os.getcwd()}

Rules:
- Read files before editing them
- Use edit_file for targeted changes (old_string must be unique)
- Use write_file only for new files or full rewrites
- Run builds/tests via bash after making changes
- Be precise with file paths (use absolute paths starting with {home_dir})
- Don't ask questions — just execute the task

CRITICAL — COMPLETION RULES:
- Call 'done' ONLY when ALL parts of the task are complete
- If the task has multiple phases/sections, complete ALL of them before calling done
- You MUST have written or edited at least one file before calling done
- DO NOT fix existing bugs unless the task explicitly asks you to
- DO NOT call done after just reading code — you must have made changes
- If you're unsure whether you're done, re-read the original task and check each requirement
- Call 'blocked' if you're stuck and need help"""

    # For llama-server provider: include tool descriptions in the system prompt
    # because we don't send tools in the API payload (Jinja parser is broken)
    if provider == "llama-server":
        system = base_system + """

## Available Tools

To use a tool, respond with a JSON object on its own line. One tool call per JSON block.

### read_file
Read a file from disk. Example:
{"name": "read_file", "parameters": {"path": "/absolute/path/to/file"}}
Optional: "offset" (start line, 1-based), "limit" (max lines)

### write_file
Write content to a file (creates parent dirs, overwrites existing). Example:
{"name": "write_file", "parameters": {"path": "/absolute/path", "content": "file content here"}}

### edit_file
Replace a unique string in a file. Example:
{"name": "edit_file", "parameters": {"path": "/absolute/path", "old_string": "exact text to find", "new_string": "replacement text"}}

### bash
Run a shell command (timeout 60s). Example:
{"name": "bash", "parameters": {"command": "python3 test.py"}}

### grep
Search file contents with regex. Example:
{"name": "grep", "parameters": {"pattern": "def main", "path": "/dir", "glob": "*.py"}}

### done
Signal task complete. Example:
{"name": "done", "parameters": {"summary": "Built X with Y", "files_changed": ["/path/a.py", "/path/b.py"]}}

### blocked
Signal you're stuck. Example:
{"name": "blocked", "parameters": {"reason": "Cannot access database"}}

IMPORTANT: Respond with ONE tool call JSON per message. After each tool call, you'll receive the result and can make another call. Do NOT put multiple tool calls in one response."""
    else:
        system = base_system

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task}
    ]

    tool_errors = 0

    for turn in range(MAX_TURNS):
        status.update(turns=turn + 1)

        # Check for context injection from Opus
        injected = check_context_injection(task_file)
        if injected:
            status.log(f"INJECTED CONTEXT from supervisor")
            messages.append({"role": "user", "content": f"[SUPERVISOR CONTEXT]: {injected}"})

        # Periodic progress check every 25 turns
        if turn > 0 and turn % 25 == 0:
            files_so_far = ", ".join(status.files_changed[-5:]) if status.files_changed else "none yet"
            pct = int((turn / MAX_TURNS) * 100)
            messages.append({
                "role": "user",
                "content": f"[SYSTEM]: Progress check — turn {turn}/{MAX_TURNS} ({pct}% budget used). Files changed: {files_so_far}. If you haven't started writing code yet, start now. Focus on the highest-priority deliverable first."
            })
            status.log(f"Progress check: turn {turn}/{MAX_TURNS}, files: {files_so_far}")

        # Call LLM (ollama or NIM)
        _ctx = ctx_size or 32768
        resp = llm_chat(provider, model, host, messages, tools=TOOLS, ctx_size=_ctx)

        if "error" in resp:
            status.log(f"OLLAMA ERROR: {resp['error']}")
            tool_errors += 1
            if tool_errors >= MAX_TOOL_ERRORS:
                # Fallback: switch provider instead of dying
                if fallback != "none" and not has_switched:
                    old_provider = provider
                    old_model = model
                    provider = fallback
                    if fallback_model:
                        model = fallback_model
                    elif fallback == "nim":
                        model = NIM_MODEL
                    elif fallback == "ollama":
                        model = DEFAULT_MODEL
                    # Update host for provider switch
                    if fallback == "nim":
                        host = NIM_URL
                    elif fallback == "ollama":
                        host = DEFAULT_HOST
                    has_switched = True
                    tool_errors = 0
                    status.switch_provider(old_provider, provider, model)
                    status.log(f"Switching to fallback provider: {provider} (model: {model})")
                    # Continue the loop with new provider
                else:
                    status.update(state="error", errors=tool_errors)
                    status.log("Circuit breaker: too many errors" +
                               (" (fallback also failed)" if has_switched else ""))
                    return
            time.sleep(3)
            continue

        msg = resp.get("message", {})
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])

        # Add assistant response to history (use raw format for NIM compatibility)
        raw_msg = resp.get("_raw_message", msg)
        messages.append(raw_msg)

        # If there's text content, log it
        if content:
            # Strip thinking tags if present
            clean = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            if clean:
                status.log(f"LLM: {clean[:200]}")

        # If no tool calls, nudge the model to continue (don't auto-exit)
        if not tool_calls:
            no_tool_turns = getattr(status, '_no_tool_turns', 0) + 1
            status._no_tool_turns = no_tool_turns
            if no_tool_turns >= 3:
                # 3 consecutive turns with no tools — model is stuck
                status.log(f"No tool calls for {no_tool_turns} consecutive turns — forcing exit")
                status.update(state="done")
                return
            # Nudge model to use tools
            messages.append({"role": "user", "content": "[SYSTEM]: You responded with text but no tool calls. You must use tools to make progress. Re-read the task and use a tool to continue working. If you are truly done, call the 'done' tool explicitly."})
            status.log(f"No tool calls — nudging model (attempt {no_tool_turns}/3)")
            continue

        # Execute tool calls
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            call_id = tc.get("id", "")

            status.update(tool_calls=status.tool_calls + 1)

            # Handle terminal tools
            if name == "done":
                summary = args.get("summary", "Task complete")
                files = args.get("files_changed", [])

                # Validate: reject premature done calls
                writes_made = sum(1 for m in messages if m.get("role") == "tool" and
                                 any(kw in str(m.get("content", "")) for kw in ["wrote", "Written", "edited", "Created"]))

                # Check for recent errors in tool results (last 10 messages)
                recent_errors = sum(1 for m in messages[-10:] if m.get("role") == "tool" and
                                   any(kw in str(m.get("content", "")) for kw in ["ERROR", "Traceback", "ModuleNotFound", "ImportError", "FAILED"]))

                if recent_errors > 0 and turn < MAX_TURNS - 5:
                    status.log(f"REJECTED done — {recent_errors} recent errors in tool output. Fix them first.")
                    messages.append({
                        "role": "tool",
                        "content": f"ERROR: You called 'done' but there are {recent_errors} recent errors in your tool results (Traceback, ERROR, etc). Fix these issues before calling done. Re-read the error messages and correct the problems.",
                        "tool_call_id": call_id,
                    })
                    status._no_tool_turns = 0
                    continue

                if not files and writes_made == 0 and turn < MAX_TURNS // 2:
                    status.log(f"REJECTED done — no files changed after {turn} turns. Continuing.")
                    messages.append({
                        "role": "tool",
                        "content": "ERROR: You called 'done' but haven't written or edited any files yet. Re-read the task and continue working. You must make the requested changes before calling done.",
                        "tool_call_id": call_id,
                    })
                    # Reset no-tool counter
                    status._no_tool_turns = 0
                    continue

                status.update(state="done", files_changed=files)
                status.log(f"DONE: {summary}")
                # Write result file for Opus
                result_file = Path(str(task_file).replace(".md", "-result.md"))
                result_file.write_text(f"# Result\n\n{summary}\n\n## Files Changed\n" +
                                       "\n".join(f"- {f}" for f in files))
                return

            if name == "blocked":
                reason = args.get("reason", "Unknown")
                status.update(state="blocked")
                status.log(f"BLOCKED: {reason}")
                # Write blocker for Opus to see
                result_file = Path(str(task_file).replace(".md", "-result.md"))
                result_file.write_text(f"# BLOCKED\n\n{reason}\n\n{args.get('context', '')}")
                return

            # Execute tool
            handler = TOOL_HANDLERS.get(name)
            if handler:
                status.log(f"TOOL: {name}({json.dumps(args)[:120]})")
                result = handler(args)
                status.log(f"  → {result[:150]}")

                # Track file changes
                if name in ("write_file", "edit_file") and "OK" in result:
                    path = args.get("path", "")
                    if path and path not in status.files_changed:
                        status.files_changed.append(path)

                if "ERROR" in result:
                    tool_errors += 1
                    status.update(errors=tool_errors)

                # Feed result back to LLM
                messages.append({
                    "role": "tool",
                    "content": result[:3000],
                    "tool_call_id": call_id,
                })
            else:
                messages.append({
                    "role": "tool",
                    "content": f"ERROR: unknown tool '{name}'",
                    "tool_call_id": call_id,
                })

        # Read-without-write guardrail: if 15+ turns of only reading, nudge to start writing
        if turn >= 15 and not status.files_changed:
            read_only_nudge = getattr(status, '_read_nudge_sent', False)
            if not read_only_nudge:
                messages.append({
                    "role": "user",
                    "content": f"[SYSTEM]: You've spent {turn} turns reading without writing any files. You have {MAX_TURNS - turn} turns left. Start implementing NOW. Write the most important file first."
                })
                status._read_nudge_sent = True
                status.log(f"Read-only guardrail: {turn} turns without writing")

        # Trim message history if too long — keep system prompt, original task, and recent messages
        if len(messages) > 50:
            # messages[0] = system prompt, messages[1] = original task
            # Keep both + last 35 messages for continuity
            messages = messages[:2] + messages[-35:]
            # Inject progress reminder
            files_so_far = ", ".join(status.files_changed[-5:]) if status.files_changed else "none yet"
            messages.append({
                "role": "user",
                "content": f"[SYSTEM]: Context trimmed. Turn {turn+1}/{MAX_TURNS}. Files changed so far: {files_so_far}. Re-read the task above and continue working on unfinished parts."
            })

    status.update(state="timeout")
    status.log(f"Hit max turns ({MAX_TURNS})")


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    global MAX_TURNS

    parser = argparse.ArgumentParser(description="Local LLM Task Runner")
    parser.add_argument("task", nargs="?", help="Path to task .md file")
    parser.add_argument("--model", default=None, help="Model name or alias (e.g. minimax, qwen, deepseek)")
    parser.add_argument("--provider", choices=["ollama", "nim", "deepseek", "llama-server"], default=None,
                        help="Provider (auto-detected from model registry if --model is set)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--ctx", type=int, default=None, help="Context window size (default: 32768 for 4090, 16384 for smaller GPUs)")
    parser.add_argument("--turns", type=int, default=None, help=f"Max turns (default: {MAX_TURNS})")
    parser.add_argument("--fallback", choices=["nim", "ollama", "deepseek", "llama-server", "none"], default="none",
                        help="Fallback provider if primary errors out (default: none)")
    parser.add_argument("--fallback-model", default=None,
                        help="Model to use with fallback provider")
    parser.add_argument("--list-models", action="store_true", help="List available models and exit")
    args = parser.parse_args()

    if args.list_models:
        list_models()
        sys.exit(0)

    if not args.task:
        parser.error("task file is required (use --list-models to see available models)")

    if not os.path.isfile(args.task):
        print(f"ERROR: task file not found: {args.task}")
        sys.exit(1)

    if args.turns:
        MAX_TURNS = args.turns

    # Resolve model from registry (auto-detects provider and host)
    if args.model:
        resolved_model, resolved_provider, resolved_host = resolve_model(args.model)
        model = resolved_model
        provider = args.provider or resolved_provider or "ollama"
        host = args.host or resolved_host or DEFAULT_HOST
    elif args.provider == "nim":
        model = NIM_MODEL
        provider = "nim"
        host = NIM_URL
    elif args.provider == "deepseek":
        model = DEEPSEEK_MODEL
        provider = "deepseek"
        host = DEEPSEEK_URL
    elif args.provider == "llama-server":
        model = "llama-3.1-70b"
        provider = "llama-server"
        host = args.host or DEFAULT_HOST
    else:
        model = DEFAULT_MODEL
        provider = args.provider or "ollama"
        host = args.host or DEFAULT_HOST

    if provider not in ("nim", "deepseek") and not host.startswith("http"):
        host = f"http://{host}"

    print(f"\n{'='*50}")
    print(f"  ollama-runner — LLM Task Executor")
    print(f"{'='*50}")
    print(f"  Provider: {provider}")
    print(f"  Model: {model}")
    if provider not in ("nim", "deepseek"):
        print(f"  Host:  {host}")
    print(f"  Task:  {args.task}")
    print(f"{'='*50}\n")

    run(args.task, model, host, provider=provider, ctx_size=args.ctx,
        fallback=args.fallback, fallback_model=getattr(args, 'fallback_model', None))


if __name__ == "__main__":
    main()
