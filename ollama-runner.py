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
# How long ollama pins the model in VRAM after a request. Override per-run with
# OLLAMA_KEEP_ALIVE=... . See the comment in ollama_chat(): omitting this costs a
# full model reload (~123 s for a 19.3 GB model) on EVERY turn when VRAM is tight.
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")
# Long runs must not be killed by the harness. The bash tool default was 60s,
# which silently failed ANY build or test suite the model tried to run — the
# exact loop we want it closing (write -> tsc/test -> fix). 600s covers a
# Next build; raise per-run with OLLAMA_BASH_TIMEOUT.
BASH_TIMEOUT = int(os.environ.get("OLLAMA_BASH_TIMEOUT", "600"))
# Per-request HTTP ceiling. Generation on a big local model can run minutes.
OLLAMA_HTTP_TIMEOUT = int(os.environ.get("OLLAMA_HTTP_TIMEOUT", "900"))
MAX_TURNS = 150

# Populated by --contract. Drives both the injected API surface and the
# post-write auto-repair. See extract_contract_surface().
CONTRACT_MODULE = None      # e.g. "@/lib/contract"
CONTRACT_SYMBOLS = set()    # every real exported name
CONTRACT_SURFACE = None     # the generated block appended to the task prompt
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
            "description": "Run a bash command. Returns stdout+stderr. Timeout 600s (builds and test suites are fine).",
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

def _normalize_path_key(args):
    """Models sometimes use file_path, filepath, or filename instead of path."""
    if "path" not in args:
        for alt in ("file_path", "filepath", "filename", "file"):
            if alt in args:
                args["path"] = args[alt]
                return
        raise KeyError(f"No path key found in args: {list(args.keys())}")

def exec_read_file(args):
    _normalize_path_key(args)
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


# Phrases that only appear in unfinished code. Deliberately NOT bare "placeholder":
# it matches the HTML `placeholder=` attribute on every form input, which flagged two
# complete components (RecordPaymentForm, RosterFilterBar) as stubs on 2026-08-22.
# A marker here must not be able to match ordinary working code.
STUB_MARKERS = ("not implemented", "todo:", "fixme", "coming soon",
                "placeholder implementation", "stub implementation")


def extract_target_paths(task_text, worktree=None):
    """Every file path a task file tells the model to write.

    Matches the two shapes the task templates use — "Write ONE file: `<path>`"
    and "Create the file `<path>` with EXACTLY this content" — plus any
    backticked absolute-looking source path in the RULES footer.
    """
    paths = []
    for m in re.finditer(r"`([^`\n]+\.(?:tsx|ts|jsx|js|py|sql))`", task_text):
        p = m.group(1).strip()
        if worktree:
            p = p.replace("__WT__", worktree.rstrip("/"))
        if "__WT__" in p:            # unresolved template — cannot check it
            continue
        if p not in paths:
            paths.append(p)
    return paths


def preflight_targets(task_text, worktree=None, tsc_cmd=None):
    """Refuse to dispatch work whose artefact already exists.

    A task file in a queue directory is NOT evidence the work is missing, and
    neither is the absence of a builder commit. Proven 2026-08-22 on classact:
    7 specs with no builder commit, matching the driver's own
    `DRAINED passed=9 failed=7` — and all 7 components already existed, real and
    compiling, from an earlier bootstrap build. Dispatching would have
    overwritten seven working components. Both signals agreed and both were
    wrong; only the target file knew.

    Returns (ok_to_dispatch, [notes]).
    """
    notes = []
    targets = extract_target_paths(task_text, worktree)
    if not targets:
        return True, ["preflight: no target path found in task file — dispatching"]

    existing = []
    for p in targets:
        if not os.path.isfile(p):
            notes.append(f"preflight: MISSING {p} — real work")
            continue
        try:
            body = open(p, errors="ignore").read()
        except Exception:
            continue
        low = body.lower()
        if not body.strip():
            notes.append(f"preflight: {p} exists but is EMPTY — real work")
            continue
        if any(s in low for s in STUB_MARKERS):
            notes.append(f"preflight: {p} exists but looks like a stub — real work")
            continue
        existing.append(p)
        notes.append(f"preflight: {p} ALREADY EXISTS ({len(body)} bytes, no stub markers)")

    if existing and len(existing) == len(targets):
        notes.append("preflight: REFUSING to dispatch — every target already exists. "
                     "Verify with the project's own tsc, then delete the task or pass --force.")
        return False, notes
    return True, notes


def extract_contract_surface(contract_path, module="@/lib/contract"):
    """Build the API-surface block appended to every task prompt.

    Measured 2026-08-22, 92 ladder runs across gemma4:12b and qwen3-coder:30b:
    31 of 56 failures were the model inventing a key on the shared style object,
    and 4 more were invented exports. Pasting the real surface took prose specs
    from 0/6 to 5/6 and invented-API failures to zero, on BOTH models. It costs
    ~1.4 KB, is identical for every task in a batch, and contains no component
    code — so unlike a literal spec it amortises to nothing.

    Scan line by line. A regex spanning the file is what produced the 2026-08-21
    bug where a greedy match ran past an object's closing brace and reported 60
    style keys where there were 11.

    Returns (text, symbols).
    """
    lines = Path(contract_path).read_text().splitlines()
    fns, consts, types, ifaces, objects = [], [], [], [], {}

    for i, line in enumerate(lines):
        m = re.match(r"^export function (\w+)\(([^)]*)\)(?:\s*:\s*([^{;]+))?", line)
        if m:
            sig = " ".join(m.group(2).split())
            ret = (m.group(3) or "").strip()
            fns.append((m.group(1), sig, ret))
            continue
        m = re.match(r"^export const (\w+)", line)
        if m:
            name = m.group(1)
            consts.append(name)
            if line.rstrip().endswith("{"):        # object literal -> collect its keys
                keys = []
                for nxt in lines[i + 1:]:
                    if nxt.startswith("}"):
                        break
                    k = re.match(r"^  ([A-Za-z_]\w*)\s*:", nxt)
                    if k:
                        keys.append(k.group(1))
                if keys:
                    objects[name] = keys
            continue
        m = re.match(r"^export type (\w+)", line)
        if m:
            types.append(m.group(1))
            continue
        m = re.match(r"^export interface (\w+)", line)
        if m:
            ifaces.append(m.group(1))

    def uniq(xs):
        return list(dict.fromkeys(xs))

    fns = [f for i, f in enumerate(fns) if f[0] not in {g[0] for g in fns[:i]}]
    consts, types, ifaces = uniq(consts), uniq(types), uniq(ifaces)

    out = [f"CONTRACT API SURFACE — `{module}` exports EXACTLY these. Nothing else exists.",
           "Do NOT reference any symbol or object key that is not on this list.", ""]
    if fns:
        out.append("functions:")
        out += [f"  {n}({a})" + (f": {r}" if r else "") for n, a, r in fns]
        out.append("")
    if consts:
        out.append("consts: " + ", ".join(consts))
    if types:
        out.append("types: " + ", ".join(types))
    if ifaces:
        out.append("interfaces: " + ", ".join(ifaces))
    for name, keys in objects.items():
        out.append("")
        out.append(f"{name} has EXACTLY these {len(keys)} keys: " + ", ".join(keys))

    symbols = set(consts) | set(types) | set(ifaces) | {n for n, _, _ in fns}
    return "\n".join(out), symbols


def auto_repair(content, path):
    """Fix the two mechanical failures that otherwise burn a whole retry.

    Measured 2026-08-22 over 92 ladder runs: of 56 failures, 11 were React/JSX
    import boilerplate and 6 were a REAL contract symbol used without being
    imported. Both are pure text edits — no model round trip needed. The other
    35 were invented API, which no repair can fix (hence --contract injection).

    Returns (content, [notes]). Never raises; a repair that cannot be made
    cleanly is skipped so the gate still sees the model's real output.
    """
    notes = []
    if not path.endswith((".tsx", ".ts", ".jsx")):
        return content, notes

    # 1. Automatic JSX runtime: `import React` is never needed and errors.
    out = []
    for line in content.split("\n"):
        s = line.strip()
        if re.match(r"^import\s+React\s*(,|from)", s) or re.match(r"^import\s+\*\s+as\s+React\s+from", s):
            notes.append("removed `import React` (automatic JSX runtime)")
            continue
        m = re.match(r"^(import\s*\{)([^}]*)(\}\s*from\s*['\"]react['\"];?)$", s)
        if m and re.search(r"\bReact\b", m.group(2)):
            names = [n.strip() for n in m.group(2).split(",")
                     if n.strip() and n.strip() != "React"]
            if not names:
                notes.append("removed bare `{ React }` react import")
                continue
            line = f"{m.group(1)} {', '.join(names)} {m.group(3)}"
            notes.append("stripped React from the react named-import")
        out.append(line)
    content = "\n".join(out)

    # 2. Contract symbol referenced but never imported.
    if CONTRACT_MODULE and CONTRACT_SYMBOLS:
        # `import type { X }` counts as importing X. The original pattern here was
        # `^import\s*\{`, which does not match the `type` form -- so when a model
        # wrote `import type { Claim } from '@bask/core'`, the repair could not see
        # Claim, added a second `import { Claim, ... }`, and the file failed to
        # compile with TS2300 Duplicate identifier. The repair caused the error it
        # exists to prevent, and the task was blamed for it.
        pat = re.compile(r"^import\s+(?:type\s+)?\{([^}]*)\}\s*from\s*['\"]"
                         + re.escape(CONTRACT_MODULE) + r"['\"];?", re.M)
        # A file may import from the module more than once (a type import AND a
        # value import). Count every one of them, not just the first.
        imported = set()
        value_import = None
        for mm in pat.finditer(content):
            for n in mm.group(1).split(","):
                n = n.strip()
                if n.startswith("type "):
                    n = n[5:].strip()
                if n:
                    imported.add(n)
            if value_import is None and not re.match(r"^import\s+type\b", mm.group(0)):
                value_import = mm
        m = value_import
        body = pat.sub("", content)
        used = {s for s in CONTRACT_SYMBOLS
                if re.search(r"\b" + re.escape(s) + r"\b", body)}
        missing = sorted(used - imported)
        if missing:
            if m:
                # Extend the existing VALUE import in place. Merging every known
                # name into it instead would duplicate whatever a sibling
                # `import type { ... }` line already brings in, and rewriting the
                # first match blindly could clobber that type import outright.
                value_names = sorted({n.strip() for n in m.group(1).split(",") if n.strip()}
                                     | set(missing))
                content = (content[:m.start()]
                           + "import { " + ", ".join(value_names) + " } from \""
                           + CONTRACT_MODULE + "\";"
                           + content[m.end():])
            else:
                content = ("import { " + ", ".join(missing) + " } from \""
                           + CONTRACT_MODULE + "\";\n" + content)
            notes.append(f"imported missing contract symbol(s): {', '.join(missing)}")

    return content, notes


def exec_write_file(args):
    _normalize_path_key(args)
    path = os.path.expanduser(args["path"])
    content = args["content"]
    try:
        content, notes = auto_repair(content, path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        msg = f"OK: wrote {len(content)} bytes to {path}"
        if notes:
            msg += "\nAUTO-REPAIRED before write: " + "; ".join(notes)
        return msg
    except Exception as e:
        return f"ERROR: {e}"


def exec_edit_file(args):
    _normalize_path_key(args)
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
        # Inject NVM/node paths so npx/tsc/node work in subprocess
        env = os.environ.copy()
        home = os.path.expanduser("~")
        nvm_bin = os.path.join(home, ".nvm", "versions", "node")
        if os.path.isdir(nvm_bin):
            for entry in sorted(Path(nvm_bin).iterdir(), reverse=True):
                bin_dir = entry / "bin"
                if bin_dir.is_dir():
                    env["PATH"] = str(bin_dir) + ":" + env.get("PATH", "")
                    break  # Use newest node version
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                timeout=BASH_TIMEOUT, env=env)
        output = result.stdout + result.stderr
        return output[:5000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return (f"ERROR: command timed out ({BASH_TIMEOUT}s). If this was a build or test "
                f"suite, it needs longer — rerun with OLLAMA_BASH_TIMEOUT=<seconds>.")
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
    # Suppress thinking mode. MUST be top-level, never inside options — proven
    # 2026-08-19: options.think=False still emitted 360 chars of thinking on
    # gemma4:12b; top-level think=False emitted 0. Thinking models otherwise
    # write to message.thinking, leave message.content EMPTY, and burn the
    # whole context budget (cost the vault run 24 failures / 22.6% pass rate).
    # Applied to all models: ollama ignores think=False for non-thinking models.
    payload["think"] = False
    # Pin the model in VRAM between turns. MUST be top-level, like think.
    # Proven 2026-08-21 on FIRMAMENT: without it, a 19.3 GB model on a 24.5 GB
    # card (~3 GB held by the Windows desktop) is evicted after EVERY request,
    # so every turn pays a full reload — measured load_duration ~123 s on three
    # consecutive identical calls. With keep_alive it drops to 0.6 s then 0.2 s.
    # api/ps LIES here: it reports the model loaded with a future expires_at
    # the whole time. Diagnose with load_duration in the response, not api/ps.
    payload["keep_alive"] = OLLAMA_KEEP_ALIVE

    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_HTTP_TIMEOUT) as resp:
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
        with urllib.request.urlopen(req, timeout=OLLAMA_HTTP_TIMEOUT) as resp:
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
        with urllib.request.urlopen(req, timeout=OLLAMA_HTTP_TIMEOUT) as resp:
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
    if CONTRACT_SURFACE:
        # Costs nothing: local inference is free, so a longer prompt is a pure
        # win over supervisor-authored literal code. See extract_contract_surface().
        task = task.rstrip() + "\n\n" + CONTRACT_SURFACE + "\n"
    status = StatusWriter(task_file)

    # Track original provider for fallback logic
    original_provider = provider
    original_model = model
    has_switched = False

    # Detect circular fallback (same provider+model) and auto-fix
    if fallback == provider and (fallback_model == model or fallback_model is None):
        status.log(f"WARNING: Circular fallback detected ({provider}/{model} → {fallback}/{fallback_model}), overriding to NIM")
        fallback = "nim"
        fallback_model = None

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
            # Fast-fail on HTTP 500s: if 5+ errors in under 60s, skip to fallback immediately
            error_threshold = 5 if "500" in str(resp.get("error", "")) else MAX_TOOL_ERRORS
            if tool_errors >= error_threshold:
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
    parser.add_argument("--contract", default=None, metavar="PATH",
                        help="Shared contract/design-system file. Its real exports and object keys "
                             "are appended to the task prompt, and referenced-but-unimported "
                             "symbols are auto-repaired before write. Kills the single largest "
                             "local-model failure class (invented API).")
    parser.add_argument("--contract-module", default="@/lib/contract", metavar="SPEC",
                        help="Import specifier for --contract (default: @/lib/contract)")
    parser.add_argument("--print-contract-surface", default=None, metavar="PATH",
                        help="Print the generated API surface for a contract file and exit")
    parser.add_argument("--worktree", default=None, metavar="DIR",
                        help="Substituted for __WT__ in task file paths (also used by preflight)")
    parser.add_argument("--force", action="store_true",
                        help="Dispatch even when every target file already exists. Off by default: "
                             "a task file in a queue is NOT evidence the work is missing.")
    args = parser.parse_args()

    if args.list_models:
        list_models()
        sys.exit(0)

    if args.print_contract_surface:
        text, syms = extract_contract_surface(args.print_contract_surface, args.contract_module)
        print(text)
        print(f"\n# {len(syms)} exported symbols, {len(text)} bytes", file=sys.stderr)
        sys.exit(0)

    if args.contract:
        if not os.path.isfile(args.contract):
            print(f"ERROR: contract file not found: {args.contract}")
            sys.exit(1)
        CONTRACT_SURFACE, syms = extract_contract_surface(args.contract, args.contract_module)
        globals()["CONTRACT_MODULE"] = args.contract_module
        globals()["CONTRACT_SYMBOLS"] = syms
        globals()["CONTRACT_SURFACE"] = CONTRACT_SURFACE
        print(f"  Contract: {args.contract} ({len(syms)} symbols, "
              f"{len(CONTRACT_SURFACE)} bytes appended to prompt)")

    if not args.task:
        parser.error("task file is required (use --list-models to see available models)")

    if not os.path.isfile(args.task):
        print(f"ERROR: task file not found: {args.task}")
        sys.exit(1)

    ok, notes = preflight_targets(Path(args.task).read_text(), args.worktree)
    for n in notes:
        print("  " + n)
    if not ok and not args.force:
        print("SKIPPED: nothing to build. (--force overrides)")
        sys.exit(3)          # distinct from 1 so a driver can count skips separately
    if not ok and args.force:
        print("  preflight: --force given, dispatching over existing files anyway")

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
