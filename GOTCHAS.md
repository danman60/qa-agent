# Gotchas

Auto-extracted + manually seeded patterns. Read on every session start.

- QA dashboard must bind to 0.0.0.0:9876 (not localhost) for FIRMAMENT access — add UFW rule | recurring IP issue | 2026-03-22
- ollama-runner "no tool calls" auto-exit was a critical bug — now requires 3 consecutive no-tool turns before exit | premature termination | 2026-03-22
- ollama-runner "done" validation added — rejects done if no files changed and < half turns used | premature done calls | 2026-03-22
- Sysadmin docs sometimes get committed here by mistake — check file paths before committing | cross-project contamination | 2026-03-22
- MAX_TURNS=150 but local models burn 30-50 turns just reading — leave budget for writing by keeping task scope small | context waste | 2026-03-22
- ollama-runner write_file creates literal ~/  directories — Python open() doesn't expand tilde. ALWAYS use /home/danman60/ in task files AND in system prompt examples | 3 overnight failures from this | 2026-03-23
