# Sysadmin — Kimi/ollama-runner Follow-up Tasks

**Status:** First successful spinoff completed 2026-03-22. Needs hardening.

## What Worked
- Kimi K2.5 via NIM built the flow file executor (156 lines, 31 tool calls, 294s)
- Tool call round-trip: read_file, bash, edit_file all worked
- Code passed syntax check before rate limit hit
- Zero GPU usage (NIM cloud)

## What Broke
1. **Wrong paths** — Kimi guessed `/home/user/` instead of `/home/danman60/`. Fix: task files must use absolute paths, or inject CWD into system prompt.
2. **NIM 429 rate limit** — hit after 31 tool calls in 5 min. Fix: add exponential backoff on 429 (currently circuit-breaks after 5 errors).
3. **NIM 400 on tool result** — first run failed because tool role message format was wrong. Fixed by storing `_raw_message` for conversation history.
4. **No retry on transient errors** — runner treats all errors the same. Should distinguish 429 (retry with backoff) from 400 (format error, fix and retry) from 500 (server error, wait).

## TODO
- [ ] Add exponential backoff on 429 (start 5s, max 60s, reset on success)
- [ ] Inject absolute CWD path into system prompt: "You are working in /home/danman60/projects/qa-agent"
- [ ] Add `--cwd` flag to set working directory context
- [ ] Increase MAX_TOOL_ERRORS for NIM (rate limits aren't real errors)
- [ ] Add `done` tool call detection even when rate-limited mid-session (check if files were changed)
- [ ] Test with qwen3-coder:30b on FIRMAMENT (ollama provider) — should be much faster
- [ ] Add `--timeout` flag (default 10 min)
- [ ] Context injection test — write to inject file mid-run, verify runner picks it up
- [ ] Babysit pattern test — Opus monitors status file, injects context when runner gets stuck

## Benchmark: Kimi vs Local for Task Execution
Not yet tested. When GPU is free, run the same task file with:
```bash
python3 ollama-runner.py /tmp/task-flow-executor.md --provider nim          # Kimi cloud
python3 ollama-runner.py /tmp/task-flow-executor.md --model qwen3-coder:30b  # Local 193 tok/s
python3 ollama-runner.py /tmp/task-flow-executor.md --model qwen3.5:27b      # Local 41 tok/s
```
Compare: time to complete, tool call accuracy, code quality, error rate.
