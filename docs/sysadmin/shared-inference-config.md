# Sysadmin — Shared Inference Working Config

**Last verified:** 2026-03-22 03:50 ET
**Model:** Llama 3.1 70B Instruct Q3_K_M (31.9GB)
**Speed:** ~8-10 tok/s (75/80 layers GPU, 5 on CPU RAM)
**Combined VRAM:** 24GB (4090) + 12GB (3060) = 36GB pool

## SPYBALLOON (RPC Server)

```bash
# Start RPC server exposing the RTX 3060
rpc-server --host 0.0.0.0 --port 50052 --device CUDA0 &

# Binary location: /usr/local/bin/rpc-server (also at ~/llama.cpp/build/bin/rpc-server)
# Built: 2026-03-21 from llama.cpp source with CUDA+RPC
# Verify: ss -tlnp | grep 50052
```

## FIRMAMENT (llama-server)

```powershell
# PowerShell — launch llama-server with RPC to SPYBALLOON
Start-Process -FilePath 'C:\Users\danie\Desktop\llama-cpp\llama-server.exe' `
  -ArgumentList @(
    '-m','D:\llama-cpp\models\Meta-Llama-3.1-70B-Instruct-Q3_K_M.gguf',
    '--rpc','192.168.0.197:50052',
    '--host','0.0.0.0',
    '--port','8080',
    '-ngl','75',
    '--ctx-size','2048',
    '-np','1'
  )
```

```bash
# From SPYBALLOON via SSH
ssh -o BatchMode=yes firmament "powershell -c \"Start-Process -FilePath 'C:\\Users\\danie\\Desktop\\llama-cpp\\llama-server.exe' -ArgumentList @('-m','D:\\llama-cpp\\models\\Meta-Llama-3.1-70B-Instruct-Q3_K_M.gguf','--rpc','192.168.0.197:50052','--host','0.0.0.0','--port','8080','-ngl','75','--ctx-size','2048','-np','1')\""
```

## Key Details

| Setting | Value | Why |
|---------|-------|-----|
| Binary path (FIRMAMENT) | `C:\Users\danie\Desktop\llama-cpp\llama-server.exe` | Downloaded b8461 release |
| Model path (FIRMAMENT) | `D:\llama-cpp\models\Meta-Llama-3.1-70B-Instruct-Q3_K_M.gguf` | Standalone GGUF |
| RPC address | `192.168.0.197:50052` | SPYBALLOON LAN IP (NOT Tailscale) |
| ngl | 75 (not 99) | 80 total layers, 5 offload to CPU to avoid fit-check abort |
| ctx-size | 2048 | Minimum to save VRAM |
| np | 1 | Single parallel slot to save KV cache memory |
| -fit | DO NOT USE `-fit off` | b8461 ignores this flag, still aborts on memory check |

## Verify

```bash
# Health check
ssh -o BatchMode=yes firmament "curl -s http://localhost:8080/health"
# Expected: {"status":"ok"}

# Test completion
ssh -o BatchMode=yes firmament "curl -s http://localhost:8080/v1/chat/completions -H 'Content-Type: application/json' -d '{\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}],\"max_tokens\":30}'"
```

## CURRENT STATUS: PARTIALLY WORKING (2026-03-22 04:00 ET)

`-fit off` via cmd.exe DOES bypass the fit check (confirmed in inline SSH output — model starts loading). But:
1. Model load takes 60-90s for 70B
2. SSH commands timeout before load completes
3. PowerShell `Start-Process` doesn't reliably pass `-fit off`
4. ollama auto-restarts and steals VRAM — must `taskkill /F /IM ollama.exe` first
5. Windows desktop processes (dwm, explorer, Discord, Steam) consume ~1.3GB VRAM baseline

### What worked earlier in this session
`ngl=75 ctx=2048` launched and returned `{"status":"ok"}` after ~10s. But subsequent launches crashed because:
- ollama restarted and loaded a model (eating 16GB VRAM)
- A previous llama-server.exe was still running (PID lingered)

### Previous working session (transcript reference)
File: `~/.claude/transcripts/2026-03-21/-home-danman60-projects.md`
Lines: 4870-5080
Key details:
- Session at 12:11-12:42 ET on 2026-03-21
- Binary: `C:\Users\danie\Desktop\llama-cpp\llama-server.exe` (b8461)
- `--flash-attn` needed a value (`on`/`off`/`auto`) — caused silent crash without it
- RPC connection worked: `RPC0 (192.168.0.197:50052) - 11794 MiB free`
- qwen3.5 models FAILED (rope.dimension_sections mismatch) — only standard arch models (Llama, Mistral) work
- The actual successful benchmark run was with Llama 3.1 70B Q3_K_M at 11.5 tok/s

### TODO to fix
- [ ] Stop ollama service before launching: `powershell "Stop-Service OllamaService"`
- [ ] Kill ALL GPU processes: ollama, LM Studio, Steam, Discord
- [ ] Launch via `cmd /c start /b` with `-fit off` flag
- [ ] Wait 90s+ for model load (not 10s)
- [ ] Or: write a .bat file on FIRMAMENT that does all cleanup + launch in one step
- [ ] Or: build ollama from source with RPC backend enabled (eliminates llama-server entirely)

## What Doesn't Work

- **ollama models via shared inference** — qwen3.5, qwen3-coder, glm-4.7-flash all use custom architectures (qwen35, qwen3moe, glm4moelite) that standard llama.cpp doesn't support. Only works with standard arch models (Llama, Mistral, Gemma).
- **`-fit off`** — flag exists but b8461 still runs the fit check and aborts if memory is tight.
- **`-ngl 99`** — projects 1460 MiB over budget, fit check kills the process.
- **Tailscale IP for RPC** — use LAN IP (192.168.0.197), not Tailscale IP (100.122.177.91).

## Startup Sequence

1. `rpc-server --host 0.0.0.0 --port 50052 --device CUDA0 &` on SPYBALLOON
2. Wait 3s, verify: `ss -tlnp | grep 50052`
3. Launch llama-server on FIRMAMENT (see command above)
4. Wait ~10-15s for model load
5. Verify: `curl -s http://localhost:8080/health` → `{"status":"ok"}`
