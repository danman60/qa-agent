# Skill → Model Routing Proposal

## The Tiers

| Tier | Engine | Speed | Quality | Cost | When |
|------|--------|-------|---------|------|------|
| **Claude Opus** | Anthropic API | ~40 tok/s + latency | Best | $$$ | Complex reasoning, multi-file refactors, architectural decisions |
| **Claude Sonnet** | Anthropic API | ~70 tok/s + latency | Great | $$ | Standard coding, code review, most skills today |
| **Claude Haiku** | Anthropic API | ~90 tok/s + latency | Good | $ | Simple orchestration, summaries, routing |
| **Local Tier 1** | qwen3.5:27b (FIRMAMENT) | 41 tok/s, 0 latency | 65/65 | Free | Code gen that doesn't need Claude-level reasoning |
| **Local Tier 2** | qwen3-coder:30b (FIRMAMENT) | 193 tok/s, 0 latency | 64/65 | Free | Fast code gen, batch work |
| **Local Tier 3** | glm-4.7-flash (FIRMAMENT) | 140 tok/s, 0 latency | 61/65 | Free | Analysis, summarization, instruction following |
| **Local Tier 4** | gemma3:12b (SPYBALLOON) | 40 tok/s, 0 latency | 59/65 | Free | Simple tasks, frees FIRMAMENT |
| **Shared 70B** | Llama 3.1 70B (FIRM+SPY RPC) | 11.5 tok/s, 0 latency | ~53/65+ | Free | Deep reasoning without API cost (locks both GPUs) |

## Proposed Routing

### Always Claude (reasoning quality critical)
- bootstrap → Opus (architectural decisions)
- gitnexus-refactoring → Sonnet (precision refactoring)
- write-tests → Sonnet (needs to understand app logic deeply)
- verify → Sonnet (catch subtle bugs)
- security-audit → Sonnet (security reasoning)

### Claude OR Local (user's choice, config flag)
- design-pass → Sonnet default, Local T2 if "fast mode"
- fix-list → Sonnet default, Local T2 for batch speed
- hotfix → Sonnet default (prod is down, quality matters)
- test-fix-loop → Local T2 default (speed matters in loops)
- loop-debug → Local T2 default
- update-tests → Local T2 default
- gitnexus-debugging → Sonnet default, Local T1 fallback
- gitnexus-exploring → Local T3 default (just reading/summarizing)
- gitnexus-impact-analysis → Local T3 default

### Always Local (no Claude needed)
- report → Local T4 (gemma3:12b on SPYBALLOON)
- reflect → Local T4
- watchdog → Local T4
- nudge → Local T4
- wrap-up → Local T4
- fresh → Local T4
- read-crash-transcript → Local T4
- cost-reducer → Local T4
- find-auth → Local T4
- qa-report → Local T4
- fleet → Local T4
- collab → Local T4
- spinoff → Local T4
- gmail/gcal/gdocs/gdrive/gsheets → Local T4
- seo → Local T3
- marketing-audit → Local T3
- domain-names → Local T3
- sync-apps → Local T3
- cross-browser → Local T3
- overnight-mode → Local T3
- playwright-cli → Local T3
- android-apk-testing → Local T3
- test-electron → Local T3
- test-webapp → Local T3

### Not LLM
- transcribe → Whisper
- gitnexus-cli → graph DB
- gitnexus-guide → static docs

## Implementation

### Option A: Config in ~/.claude/skill-routing.json
```json
{
  "default": "sonnet",
  "overrides": {
    "report": {"engine": "ollama", "model": "gemma3:12b", "host": "localhost:11434"},
    "fix-list": {"engine": "ollama", "model": "qwen3-coder:30b", "host": "100.75.112.14:11434"},
    "bootstrap": {"engine": "claude", "model": "opus"}
  }
}
```

### Option B: Per-skill SKILL.md frontmatter
```markdown
---
model: ollama:qwen3-coder:30b
fallback: sonnet
host: 100.75.112.14:11434
---
```

### Option C: Wrapper script that intercepts skill activation
A hook that reads the routing config and either:
- Lets Claude Code handle it normally (Anthropic API)
- Spawns a local ollama chat session instead
- Requires building an ollama-based "skill runner" that mimics Claude Code's tool use

## What You'd Notice

### Local models (T2-T4) vs Claude Sonnet:
- **Faster response** for simple tasks (no network round-trip)
- **Worse at ambiguity** — local models need more explicit instructions
- **No tool use** — ollama models can't call Read/Write/Bash/etc natively
- **No git awareness** — can't browse your repo without explicit context
- **No MCP** — can't query Supabase, GitNexus, etc.

### The tool-use gap is the real blocker
Claude Code skills work because Claude can call tools (Read, Write, Bash, Grep).
Local ollama models can't do that — they just generate text.
To route skills to local models, we'd need to build a tool-use layer
on top of ollama (function calling → parse → execute → feed back).
qwen3.5 and qwen3-coder support tool calls (5/5 in our benchmark),
so this IS possible but requires building the harness.

## Recommendation

### Phase 1 (now): Tag skills, don't route yet
Add `# Model: local-t4` comments to SKILL.md files.
When spawning subagents manually (delegate, collab, spinoff),
use the tag to decide: spawn Claude or spawn ollama.

### Phase 2: Build ollama skill runner
A Python script that loads a skill's SKILL.md, sends it to ollama
with tool-call support, and executes tools locally.
Test with Tier 4 skills first (report, reflect, wrap-up).

### Phase 3: Hook into Claude Code
A PreToolUse hook that intercepts skill activation,
checks skill-routing.json, and optionally redirects
to the local ollama runner instead of Anthropic API.
