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
MAX_TURNS = 50
MAX_TOOL_ERRORS = 5

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
    path = args["path"]
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
    path = args["path"]
    content = args["content"]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"OK: wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"ERROR: {e}"


def exec_edit_file(args):
    path = args["path"]
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

def ollama_chat(host, model, messages, tools=None):
    """Call ollama /api/chat with tool support."""
    url = f"{host}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_ctx": 8192},
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
        with urllib.request.urlopen(req, timeout=120) as resp:
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
        with urllib.request.urlopen(req, timeout=120) as resp:
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


def openai_chat(host, model, messages, tools=None):
    """Call any OpenAI-compatible endpoint (llama-server, etc) with tool support."""
    url = f"{host}/v1/chat/completions"
    payload = {
        "model": model or "local",
        "messages": messages,
        "max_tokens": 4096,
    }
    if tools:
        payload["tools"] = tools

    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
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


def llm_chat(provider, model, host, messages, tools=None):
    """Route to the right chat API based on provider."""
    if provider == "nim":
        return nim_chat(model, messages, tools)
    elif provider == "llama-server":
        return openai_chat(host, model, messages, tools)
    else:
        return ollama_chat(host, model, messages, tools)


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
        self.log_file.write_text(f"# Local Runner Log\nStarted: {datetime.now():%H:%M:%S}\n\n")

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        with open(self.log_file, "a") as f:
            f.write(line + "\n")

    def update(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
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

def run(task_file, model, host, provider="ollama"):
    task = Path(task_file).read_text()
    status = StatusWriter(task_file)

    status.log(f"Provider: {provider}")
    status.log(f"Model: {model}")
    if provider != "nim":
        status.log(f"Host: {host}")
    status.log(f"Task: {task_file}")

    system = """You are a coding assistant executing a task. You have tools to read files, write files, edit files, run bash commands, and grep for patterns.

Rules:
- Read files before editing them
- Use edit_file for targeted changes (old_string must be unique)
- Use write_file only for new files or full rewrites
- Run builds/tests via bash after making changes
- Call 'done' when the task is complete
- Call 'blocked' if you're stuck and need help
- Be precise with file paths (use absolute paths)
- Don't ask questions — just execute the task"""

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

        # Call LLM (ollama or NIM)
        resp = llm_chat(provider, model, host, messages, tools=TOOLS)

        if "error" in resp:
            status.log(f"OLLAMA ERROR: {resp['error']}")
            tool_errors += 1
            if tool_errors >= MAX_TOOL_ERRORS:
                status.update(state="error", errors=tool_errors)
                status.log("Circuit breaker: too many errors")
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

        # If no tool calls, the model is done talking
        if not tool_calls:
            if turn > 0:
                status.log("No tool calls — assuming done")
                status.update(state="done")
                return
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

        # Trim message history if too long
        if len(messages) > 40:
            messages = [messages[0]] + messages[-30:]

    status.update(state="timeout")
    status.log(f"Hit max turns ({MAX_TURNS})")


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Local LLM Task Runner")
    parser.add_argument("task", help="Path to task .md file")
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", choices=["ollama", "nim", "llama-server"], default="ollama")
    parser.add_argument("--host", default=DEFAULT_HOST)
    args = parser.parse_args()

    if not os.path.isfile(args.task):
        print(f"ERROR: task file not found: {args.task}")
        sys.exit(1)

    # Set defaults based on provider
    if args.provider == "nim":
        model = args.model or NIM_MODEL
        host = NIM_URL
    elif args.provider == "llama-server":
        model = args.model or "llama-3.1-70b"
        host = args.host
        if not host.startswith("http"):
            host = f"http://{host}"
    else:
        model = args.model or DEFAULT_MODEL
        host = args.host
        if not host.startswith("http"):
            host = f"http://{host}"

    print(f"\n{'='*50}")
    print(f"  ollama-runner — LLM Task Executor")
    print(f"{'='*50}")
    print(f"  Provider: {args.provider}")
    print(f"  Model: {model}")
    if args.provider != "nim":
        print(f"  Host:  {host}")
    print(f"  Task:  {args.task}")
    print(f"{'='*50}\n")

    run(args.task, model, host, provider=args.provider)


if __name__ == "__main__":
    main()
