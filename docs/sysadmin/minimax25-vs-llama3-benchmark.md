# Sysadmin — MiniMax 2.5 vs Llama 3 for Coding: 2026 Local Model Benchmark

**Source:** https://www.sitepoint.com/benchmarking-local-models-minimax25-vs-llama-3-vs-mistral/
**Saved:** 2026-03-22

## TL;DR Verdict

| Use Case | Best Model | Why |
|----------|-----------|-----|
| **Coding (JS-heavy)** | MiniMax 2.5 | 73% JS Pass@1, best React components |
| **Coding (Python-heavy)** | Llama 3 70B | 74% Python Pass@1, fewer hallucinations |
| **Chat/General** | Mistral Large 2 | Best coherence (4.5), instruction following (4.5), tone (4.3) |
| **24GB+ GPU** | Mistral Large 2 | Most balanced across all tasks |
| **12GB GPU (RTX 3060)** | Gemma 2 9B | Fits in 12GB, 28 t/s, adequate for straightforward coding |
| **Speed on consumer HW** | Llama 3 8B | 31 t/s on 3060, slightly faster than Gemma 2 |

## Test Setup

- **Hardware:** RTX 4090 (24GB) + RTX 3060 (12GB)
- **Software:** Ollama 0.6.2, llama.cpp, Python 3.12, Node.js 22
- **Quantization:** Q4_K_M (most common for local deployment)
- **Prompts:** 30 total — 10 coding (5 Python, 5 JS), 10 reasoning, 10 creative

## Key Results

### Coding Accuracy (Pass@1)

| Model | Python | JavaScript | Combined | Hallucination Rate |
|-------|--------|-----------|----------|-------------------|
| MiniMax 2.5 (80B MoE, ~20B active) | 71% | 73% | 72% | 9% fabricated methods |
| Llama 3 70B | 74% | 70% | 72% | Lowest |
| Mistral Large 2 (123B) | Good | Best Express/Zod | Clean | Missed edge cases in file I/O |
| Gemma 2 27B | Weak | Weak | Low | Omits imports, breaks async/await |

### Speed (Tokens/Second)

| Model | RTX 4090 | RTX 3060 |
|-------|----------|----------|
| Llama 3 8B | Fast | 31 t/s |
| Gemma 2 9B | Fast | 28 t/s |
| MiniMax 2.5 | Mid-tier | 6 t/s (partial offload) |
| Llama 3 70B | 18 t/s | <2 t/s (unusable) |
| Mistral Large 2 | 12 t/s | <2 t/s (unusable) |

### VRAM Usage

| Model | VRAM @ 4K context |
|-------|-------------------|
| Gemma 2 9B | 6.9 GB |
| Llama 3 8B | ~6 GB |
| MiniMax 2.5 | ~24 GB (MoE, tolerates offloading) |
| Llama 3 70B | 24+ GB (dense, offloading kills perf) |
| Mistral Large 2 | 24+ GB |

## Relevance to QA Agent

The QA agent currently uses:
- **qwen3-coder:30b** on FIRMAMENT (4090) — 193 tok/s
- **Gemma 3 12B** on SPYBALLOON (3060) — 39.5 tok/s
- **Kimi K2.5** on NIM cloud

### What this benchmark suggests:
1. **MiniMax 2.5 could be a good QA agent model** — strong JS awareness, modern idioms, but 9% hallucination rate is concerning for verdict accuracy
2. **Llama 3 70B at 18 t/s on 4090** is viable for QA but much slower than qwen3-coder at 193 t/s
3. **For the 3060 (SPYBALLOON):** only sub-10B models are usable interactively. Gemma 2 9B (28 t/s) or Llama 3 8B (31 t/s) are the options
4. **MoE models tolerate offloading** better than dense models — relevant for SPYBALLOON's 12GB RTX 3060
5. **Q4_K_M quantization** drops real-world accuracy significantly vs full precision (79% → 55% in their tests)

## Key Insight

> "The gap between a model's reported MMLU score and its usefulness generating a working Express.js endpoint on an RTX 3060 can invalidate procurement decisions made on leaderboard data alone."

Leaderboard scores ≠ local performance. Always benchmark on YOUR hardware with YOUR prompts.
