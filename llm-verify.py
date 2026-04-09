#!/usr/bin/env python3
"""
llm-verify.py — Post-run task completion verifier.

Given a task .md file and the status JSON written by ollama-runner, decide
whether the task actually completed its deliverables. Runs in two phases:

  1. Structural check (free, deterministic)
     Parse "## Files to Create" / "## Files to Modify" sections from the task
     .md, resolve paths against the project dir, and confirm every declared
     file exists on disk with size > 0 AND is in the status `files_changed`.

  2. LLM verification (fallback when structural check is inconclusive)
     Call a cheap cloud model (glm-5.1:cloud → minimax-m2.5:cloud fallback)
     via Ollama's /api/chat with format: "json", returning a structured
     {"verdict": "done|incomplete|error", "reason": "...", "missing": [...]}.

Writes the verdict JSON to --output and also prints it to stdout.

Designed to be called by pipeline-orchestrator.py when an ollama-runner task
reports state="timeout" — so the orchestrator can distinguish "LLM forgot to
call done but the work is finished" from "LLM genuinely didn't complete".

Exit code: 0 on success (verdict written), 1 on unrecoverable error.

Usage:
    python3 llm-verify.py \\
      --task-file /tmp/task-foo.md \\
      --status-file /tmp/task-foo-status.json \\
      --project-dir /home/danman60/projects/constellation-dashboard \\
      --output /tmp/verify-foo.json
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "glm-5.1:cloud"
DEFAULT_FALLBACK_MODEL = "minimax-m2.5:cloud"
DEFAULT_HOST = "http://100.75.112.14:11434"  # FIRMAMENT Ollama
DEFAULT_TIMEOUT_SEC = 30
MAX_FILE_LINES_IN_PROMPT = 80
MAX_TOTAL_PROMPT_CHARS = 120_000  # well under glm-5.1:cloud's 200K context


# ---------------------------------------------------------------------------
# Task .md parsing — extract declared deliverable files
# ---------------------------------------------------------------------------

# Matches "## Files to Create" or "## Files to Modify" through the next ## or EOF.
_SECTION_RE = re.compile(
    r"^##\s+Files\s+to\s+(?:Create|Modify)\s*$(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
# Anchored to start of line so we don't grab paths from prose.
_PATH_RE = re.compile(r"^\s*[-*]\s*`([^`]+)`")


def extract_declared_files(md_text: str) -> list[str]:
    """Return list of declared file paths (verbatim from task .md, relative)."""
    out = []
    for section in _SECTION_RE.finditer(md_text):
        for line in section.group(1).splitlines():
            m = _PATH_RE.match(line)
            if m:
                path = m.group(1).strip()
                if path and path not in out:
                    out.append(path)
    return out


# ---------------------------------------------------------------------------
# Phase 1 — Structural check
# ---------------------------------------------------------------------------


def structural_check(declared: list[str], files_changed: list[str],
                     project_dir: Path) -> dict:
    """
    Returns dict with keys: verdict, phase, reason, missing.
    verdict is one of: done, incomplete, inconclusive.
    """
    if not declared:
        return {
            "verdict": "inconclusive",
            "phase": "structural",
            "reason": "no declared files in task .md",
            "missing": [],
        }

    # Normalize files_changed to absolute resolved paths for comparison.
    changed_norm: set[str] = set()
    for p in files_changed or []:
        try:
            changed_norm.add(str(Path(p).resolve()))
        except (OSError, ValueError):
            continue

    missing: list[str] = []
    present: list[str] = []
    for rel in declared:
        # Allow absolute paths in task files (rare); otherwise resolve against project_dir.
        rel_path = Path(rel)
        abs_path = rel_path if rel_path.is_absolute() else (project_dir / rel).resolve()

        if not abs_path.exists():
            missing.append(f"{rel} (not on disk)")
            continue
        try:
            if abs_path.stat().st_size == 0:
                missing.append(f"{rel} (empty file)")
                continue
        except OSError:
            missing.append(f"{rel} (stat failed)")
            continue

        # Soft check — file exists and has content, but wasn't in files_changed.
        # This is possible if another agent created it. We still count it as
        # present because the deliverable exists; just flag it in reason.
        resolved = str(abs_path)
        if resolved not in changed_norm:
            present.append(f"{rel} (on disk, not in files_changed)")
        else:
            present.append(rel)

    if missing:
        return {
            "verdict": "incomplete",
            "phase": "structural",
            "reason": f"missing {len(missing)}/{len(declared)}: {missing[:3]}",
            "missing": missing,
        }

    # Every declared file is on disk with content. Good enough.
    reason = f"{len(present)}/{len(declared)} deliverables present"
    soft_count = sum(1 for p in present if "not in files_changed" in p)
    if soft_count:
        reason += f" ({soft_count} not in files_changed but exist)"
    return {
        "verdict": "done",
        "phase": "structural",
        "reason": reason,
        "missing": [],
    }


# ---------------------------------------------------------------------------
# Phase 2 — LLM verification via Ollama JSON mode
# ---------------------------------------------------------------------------


def _read_clipped(path: Path, max_lines: int = MAX_FILE_LINES_IN_PROMPT) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    lines.append(f"... [clipped at {max_lines} lines]\n")
                    break
                lines.append(line)
            return "".join(lines)
    except OSError as e:
        return f"[unreadable: {e}]\n"


def _build_diffs_block(files_changed: list[str], project_dir: Path) -> str:
    """Concatenate first 80 lines of each changed file into a single block."""
    blocks = []
    total = 0
    for p in files_changed or []:
        path = Path(p)
        if not path.is_absolute():
            path = (project_dir / p).resolve()
        if not path.exists():
            blocks.append(f"\n### {p}\n[missing]\n")
            continue
        body = _read_clipped(path)
        block = f"\n### {p}\n```\n{body}```\n"
        if total + len(block) > MAX_TOTAL_PROMPT_CHARS:
            blocks.append(f"\n### [remaining {len(files_changed) - len(blocks)} files clipped]\n")
            break
        blocks.append(block)
        total += len(block)
    return "".join(blocks)


_SYS_PROMPT = (
    "You are a senior code reviewer acting as a completion verifier. "
    "You will be given a task description (what an agent was asked to do), "
    "a list of files the agent changed, and the first 80 lines of each file. "
    "Your job: decide whether the task is actually COMPLETE based on what is "
    "in the files, NOT based on whether the agent said it was done.\n\n"
    "Rules:\n"
    "1. If all declared deliverables exist in the changed files and the code "
    "looks like it implements the requested behavior, verdict = \"done\".\n"
    "2. If major pieces are missing, unimplemented, or stubbed with TODO, "
    "verdict = \"incomplete\".\n"
    "3. If the files contain syntax errors, crashes, or contradict the task, "
    "verdict = \"error\".\n"
    "4. Be LENIENT about style, polish, and whether tests exist — only judge "
    "whether the core ask is present in the code.\n\n"
    "Output format (JSON, no markdown, no preamble):\n"
    '{"verdict": "done"|"incomplete"|"error", '
    '"reason": "<one sentence, <=120 chars>", '
    '"missing": ["<feature or file>", ...]}'
)


def _ollama_verify_call(model: str, host: str, system: str, user: str,
                         timeout: int) -> dict:
    """One-shot Ollama /api/chat call with format: json. Raises on HTTP error."""
    url = f"{host}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": "json",
        "options": {"num_ctx": 32768, "temperature": 0.1, "think": False},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())
    content = (body.get("message") or {}).get("content", "")
    if not content:
        raise ValueError(f"empty response from {model}: {body}")
    # Ollama format=json guarantees valid JSON in content field.
    return json.loads(content)


def llm_check(task_text: str, files_changed: list[str], project_dir: Path,
              model: str, fallback_model: str, host: str, timeout: int) -> dict:
    """
    Returns verdict dict. Tries primary model, falls back on any failure.
    On both failing, returns verdict="error" with reason describing the fault.
    """
    diffs_block = _build_diffs_block(files_changed, project_dir)
    user_prompt = (
        f"# TASK\n{task_text}\n\n"
        f"# FILES CHANGED\n{json.dumps(files_changed, indent=2)}\n\n"
        f"# FILE CONTENTS (first {MAX_FILE_LINES_IN_PROMPT} lines each)\n"
        f"{diffs_block}"
    )
    # Truncate just in case a task description is enormous.
    if len(user_prompt) > MAX_TOTAL_PROMPT_CHARS:
        user_prompt = user_prompt[:MAX_TOTAL_PROMPT_CHARS] + "\n[prompt clipped]"

    last_error = None
    for attempt_model in (model, fallback_model):
        if not attempt_model:
            continue
        try:
            result = _ollama_verify_call(
                attempt_model, host, _SYS_PROMPT, user_prompt, timeout
            )
            verdict = result.get("verdict", "").lower()
            if verdict not in ("done", "incomplete", "error"):
                raise ValueError(f"invalid verdict: {verdict!r}")
            return {
                "verdict": verdict,
                "phase": "llm",
                "reason": str(result.get("reason", ""))[:150],
                "missing": result.get("missing", []) or [],
                "llm_model": attempt_model,
            }
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, ValueError, TimeoutError, OSError) as e:
            last_error = f"{attempt_model}: {type(e).__name__}: {e}"
            continue

    return {
        "verdict": "error",
        "phase": "llm",
        "reason": f"all models failed: {last_error}",
        "missing": [],
        "llm_model": None,
    }


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify ollama-runner task completion.")
    ap.add_argument("--task-file", required=True, help="Path to task .md")
    ap.add_argument("--status-file", required=True, help="Path to ollama-runner status JSON")
    ap.add_argument("--project-dir", required=True, help="Base directory for relative paths in task .md")
    ap.add_argument("--output", required=True, help="Where to write verdict JSON")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Primary cloud model")
    ap.add_argument("--fallback-model", default=DEFAULT_FALLBACK_MODEL, help="Fallback cloud model")
    ap.add_argument("--host", default=DEFAULT_HOST, help="Ollama host URL")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC, help="LLM call timeout seconds")
    ap.add_argument("--no-llm", action="store_true", help="Skip LLM phase, return structural result only")
    args = ap.parse_args()

    start = time.time()

    task_path = Path(args.task_file)
    status_path = Path(args.status_file)
    project_dir = Path(args.project_dir).resolve()

    if not task_path.exists():
        verdict = {"verdict": "error", "phase": "setup",
                   "reason": f"task file missing: {task_path}",
                   "missing": [], "elapsed_s": 0.0}
        Path(args.output).write_text(json.dumps(verdict, indent=2))
        print(json.dumps(verdict))
        return 1

    if not status_path.exists():
        verdict = {"verdict": "error", "phase": "setup",
                   "reason": f"status file missing: {status_path}",
                   "missing": [], "elapsed_s": 0.0}
        Path(args.output).write_text(json.dumps(verdict, indent=2))
        print(json.dumps(verdict))
        return 1

    try:
        task_text = task_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        verdict = {"verdict": "error", "phase": "setup",
                   "reason": f"cannot read task file: {e}",
                   "missing": [], "elapsed_s": 0.0}
        Path(args.output).write_text(json.dumps(verdict, indent=2))
        print(json.dumps(verdict))
        return 1

    try:
        status_data = json.loads(status_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        verdict = {"verdict": "error", "phase": "setup",
                   "reason": f"cannot parse status file: {e}",
                   "missing": [], "elapsed_s": 0.0}
        Path(args.output).write_text(json.dumps(verdict, indent=2))
        print(json.dumps(verdict))
        return 1

    files_changed = status_data.get("files_changed", []) or []
    declared = extract_declared_files(task_text)

    # ----- Phase 1: structural -----
    struct = structural_check(declared, files_changed, project_dir)
    if struct["verdict"] == "done":
        struct["elapsed_s"] = round(time.time() - start, 3)
        Path(args.output).write_text(json.dumps(struct, indent=2))
        print(json.dumps(struct))
        return 0

    if struct["verdict"] == "incomplete":
        # Declared files are genuinely missing — no point asking the LLM.
        struct["elapsed_s"] = round(time.time() - start, 3)
        Path(args.output).write_text(json.dumps(struct, indent=2))
        print(json.dumps(struct))
        return 0

    # verdict == "inconclusive" → escalate to LLM unless --no-llm set
    if args.no_llm:
        struct["elapsed_s"] = round(time.time() - start, 3)
        Path(args.output).write_text(json.dumps(struct, indent=2))
        print(json.dumps(struct))
        return 0

    # ----- Phase 2: LLM -----
    if not files_changed:
        # Nothing was written — LLM can't judge empty work positively.
        verdict = {
            "verdict": "incomplete",
            "phase": "structural",
            "reason": "no files_changed and no declared deliverables",
            "missing": [],
            "elapsed_s": round(time.time() - start, 3),
        }
        Path(args.output).write_text(json.dumps(verdict, indent=2))
        print(json.dumps(verdict))
        return 0

    llm_result = llm_check(
        task_text=task_text,
        files_changed=files_changed,
        project_dir=project_dir,
        model=args.model,
        fallback_model=args.fallback_model,
        host=args.host,
        timeout=args.timeout,
    )
    llm_result["elapsed_s"] = round(time.time() - start, 3)
    Path(args.output).write_text(json.dumps(llm_result, indent=2))
    print(json.dumps(llm_result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
