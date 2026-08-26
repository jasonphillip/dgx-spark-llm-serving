# LLM serving on NVIDIA DGX Spark (GB10) — field notes

Working notes from running local LLM inference on a 3-node DGX Spark fleet as the
engine behind unattended agent/batch workloads. Everything here was measured on my
units; treat single-machine numbers as directional for yours.

## The one law that decides everything

GB10 has 128 GB unified memory at **~273 GB/s**. Decode is memory-bandwidth-bound,
so decode speed ≈ bandwidth / bytes-touched-per-token:

| Model class | Decode (single stream) | Verdict |
|---|---|---|
| Dense ~32B (FP8/INT4) | ~10 tok/s | unusable for agentic loops |
| MoE, small active params (e.g. Qwen3-30B-A3B, 3B active, FP8) | ~44–89 tok/s | the workable class |
| Same MoE under concurrency (12–16 streams) | ~290–370 tok/s aggregate | batch workhorse |

Prefill is compute-bound and fast. Rule of thumb: **on GB10, pick MoE models with
small active parameter counts.** Total parameters are nearly free (unified memory);
active parameters are what you pay per token.

## vLLM on GB10 (sm_121) — flags that mattered

```bash
docker run -d --name llm --gpus all --network host --ipc host \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  --entrypoint vllm <sm_121-capable vllm image> \
  serve Qwen/Qwen3-30B-A3B-Instruct-2507-FP8 \
  --served-model-name qwen3-30b-a3b --port 8010 \
  --max-model-len 32768 --max-num-seqs 16 \
  --gpu-memory-utilization 0.35 \
  --attention-backend TRITON_ATTN \
  --no-enable-log-requests
```

- `--gpu-memory-utilization` well below 1.0 lets the engine co-tenant with other
  services on the same box (unified memory makes this practical).
- `--attention-backend TRITON_ATTN` was the stable choice on this stack.

## The wedge failure mode (and the fix)

Under sustained batch load with client-abort waves, vLLM can enter a state where
**the API answers but generation is dead**: `/v1/models` returns 200 while every
completion hangs forever. Two consequences:

1. **Liveness = a real completion, never a metadata endpoint.**
   `scripts/generation-probe.sh` sends a 5-token completion with a hard timeout.
2. **A dumb 10-minute guard beats a clever monitor.** `scripts/engine-guard.sh`:
   two consecutive probe failures → `docker restart`. Wire it to a systemd user
   timer and forget it.

Client side, for batch pipelines hitting a local engine:
- Treat timeouts/connection-refused as **transient**: never record them as
  document-level failures; re-queue at the next pass.
- Add a **consecutive-transient circuit breaker** (mine trips at 30) so a dead
  engine can't burn a 20k-item queue as spurious failures.
- Pre-flight each pass with the generation probe, not `/v1/models`.

## Multi-node (ConnectX-7 ring)

Three Sparks in a ring over CX7: each edge is one cable carrying two planes
(~100 Gb/s each). Findings:

- **87 Gb/s sustained** on an untuned, link-local QSFP link — near line rate for
  one plane with almost no tuning beyond MTU 9000.
- Plain `scp`/`rsync` uses one plane (~100 Gb/s ceiling); only NCCL spans both
  planes (~200 Gb/s per edge).
- `NCCL_NET_PLUGIN=none` was required for clean 20+ GB/s NCCL inter-node
  bandwidth on this stack (the bundled plugin underperformed).
- Doubling cables between two nodes **reduced** bandwidth in my tests — the
  topology assumptions matter more than raw links.
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
