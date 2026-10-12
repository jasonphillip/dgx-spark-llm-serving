# LLM serving on NVIDIA DGX Spark (GB10) — field notes, August–September 2026

Working notes from running local LLM inference on a 3-node DGX Spark fleet as the
engine behind unattended agent/batch workloads. The fleet, link and reliability
numbers are from my units. The decode table cites public GB10 benchmarks where
marked. Treat single-machine numbers as directional for yours.

## What this is, in one verifiable line

This repo documents DGX Spark LLM serving and provides a guard that restarts vLLM after two consecutive probe failures. <!-- Quantified claim source: README.md -->

Generation probe with restart guard.

Verify it in `scripts/engine-guard.sh`.

The canonical source is https://github.com/jasonphillip/dgx-spark-llm-serving

Extracted from a private working tree on 2026-08-26.

## The one law that decides everything

GB10 has 128 GB unified memory at **~273 GB/s**. Decode is memory-bandwidth-bound,
so decode speed ≈ bandwidth / bytes-touched-per-token:

| Model class | Decode speed | Verdict |
|---|---|---|
| Dense ~32B (Q4/FP8) | ~10 tok/s (public GB10 benchmarks: 10.7 on llama.cpp, 9.4–10.1 on Ollama) | unusable for agentic loops |
| MoE, 3B active (Qwen3-30B-A3B) | up to ~89 tok/s in public llama.cpp benchmarks, ~46 tok/s in my vLLM FP8 logs | the workable class |
| MoE, 3B active, 16 concurrent streams | ~350 tok/s aggregate, ~22 per stream (public vLLM benchmark, Qwen3-Coder-Next-80B-A3B int4) | batch workhorse |

Public figures come from [DandinPower's llama.cpp report](https://github.com/DandinPower/llama.cpp_bench/blob/main/dgx_spark/report.md), the [G3nadh Ollama dataset](https://huggingface.co/datasets/G3nadh/dgx-spark-benchmarks) and [NathanMaine's vLLM notes](https://github.com/NathanMaine/NVIDIA-DGX-Spark-with-vLLM), all measured on GB10. An aggregate is a capacity figure, not a per-request speed.

Prefill is compute-bound and fast. Rule of thumb: **on GB10, pick MoE models with
small active parameter counts.** Total parameters are nearly free (unified memory);
active parameters are what you pay per token.

## vLLM on GB10 (sm_121) — flags that mattered

```bash
docker run -d --name llm --gpus all --network host --ipc host \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  --entrypoint vllm nvcr.io/nvidia/vllm:26.08-py3 \
  serve Qwen/Qwen3-30B-A3B-Instruct-2507-FP8 \
  --served-model-name qwen3-30b-a3b --port 8010 \
  --max-model-len 32768 --max-num-seqs 16 \
  --gpu-memory-utilization 0.35 \
  --attention-backend TRITON_ATTN \
  --no-enable-log-requests
```

Image: `nvcr.io/nvidia/vllm:26.08-py3` (NGC), the build these notes were taken on.

- `--gpu-memory-utilization` well below 1.0 lets the engine co-tenant with other
  services on the same box (unified memory makes this practical).
- `--attention-backend TRITON_ATTN` was the stable choice on this stack.

## The wedge failure mode (and the fix)

Under sustained batch load with client-abort waves, vLLM can enter a state where
**the API answers but generation is dead**: `/v1/models` returns 200 while every
completion hangs forever. Two consequences:

Liveness has to be a real completion, never a metadata endpoint. The probe inside
`scripts/engine-guard.sh` sends a 5-token completion with a hard timeout. A dumb
10-minute guard beats a clever monitor: two consecutive probe failures trigger
`docker restart`. A failed restart is logged and retried on the next run.

Copy `systemd/engine-guard.service` and `systemd/engine-guard.timer` to
`~/.config/systemd/user/`, then edit `ExecStart` to point at your checkout:

```bash
systemctl --user daemon-reload && systemctl --user enable --now engine-guard.timer
```

Client side, for batch pipelines hitting a local engine:
- Treat timeouts/connection-refused as **transient**: never record them as
  document-level failures; re-queue at the next pass.
- Add a **consecutive-transient circuit breaker** (mine trips at 30) so a dead
  engine can't burn a 29k-document queue as spurious failures.
- Pre-flight each pass with the generation probe, not `/v1/models`.

## Multi-node (ConnectX-7 ring)

Three Sparks in a ring over CX7: each edge is one cable carrying two planes
(~100 Gb/s each). Findings:

- **87 Gb/s** on an untuned link-local QSFP link at MTU 1500, and **105 Gb/s** after
  tuning (MTU 9000): line rate for one plane.
- Plain `scp`/`rsync` uses one plane (~100 Gb/s ceiling). Only NCCL spans both
  planes: measured ~165 Gb/s all-reduce bus bandwidth against a ~200 Gb/s two-plane ceiling.
- `NCCL_NET_PLUGIN=none` was required for clean 20+ GB/s NCCL inter-node
  bandwidth on this stack (the bundled plugin underperformed).
- Doubling cables between two nodes **reduces** bandwidth. The topology assumptions
  matter more than raw links.
- Pipeline parallelism across the ring runs very large MoE models that no single
  node fits (e.g. a ~397B-total MoE at PP=3), at batch-usable speeds.

## Reliability pattern for unattended multi-day runs

The stack that survived a multi-day autonomous run on this fleet:

1. Idempotent workers keyed by content hash (kill/restart-safe by construction).
2. 10-min engine guard (generation probe → 2-strike container restart).
3. 4-hour program watchdog: stalled-pipeline detection, engine probe + restart +
   re-kick, auto-revive of dead timers, load-shedding (halve client concurrency
   when the engine flaps), disk/peer checks, heartbeat log.
4. systemd user timers everywhere; oneshot services started with `--no-block`
   (a `restart` on a long oneshot blocks for the whole pass — learned the hard way).

## License

MIT.
