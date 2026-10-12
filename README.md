# LLM serving on NVIDIA DGX Spark (GB10) — field notes, August–September 2026

This repo is for people running local LLM inference on DGX Spark GB10 as the
engine behind unattended batch and agent workloads.

The Qwen3 MoE vLLM server, generation guard, and ConnectX-7 ring worked on my
three-Spark fleet. These are field notes from one fleet. Single-stream results
are directional, and external benchmark rows are marked. There is no own
measurement for 12 to 16 concurrent streams.

## What this is, in one verifiable line

This repo documents DGX Spark LLM serving and provides a guard that restarts vLLM after two consecutive probe failures. <!-- Quantified claim source: README.md -->

Generation probe with restart guard.

Verify it in `scripts/engine-guard.sh`.

The canonical source is https://github.com/jasonphillip/dgx-spark-llm-serving

Extracted from a private working tree on 2026-08-26.

## Quickstart

Run this vLLM container on a DGX Spark with the model available in the Hugging
Face cache:

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

Copy `systemd/engine-guard.service` and `systemd/engine-guard.timer` to
`~/.config/systemd/user/`. Set `ExecStart` in the service to the absolute path
of `scripts/engine-guard.sh` in your checkout. Then enable the user timer:

```bash
systemctl --user daemon-reload && systemctl --user enable --now engine-guard.timer
```

The timer runs only while a user session exists unless user lingering is enabled.
Check the host before changing lingering.

## Measured result and engineering choice

Four single-stream Qwen3 FP8 requests produced 50.4 to 53.1 output tokens per
second on a warm server. The [throughput record](evidence/throughput-2026-09-04.md)
gives the request and limits. In one wedge, the guard restarted the container at
07:02 and a later probe was healthy at 07:12. The [case study](docs/case-study-wedge-2026-08-26.md)
records the workload recovery separately.

MoE keeps active parameters small, which reduces decode work on GB10. A real
completion probe detects generation failure that a metadata endpoint can miss.
The guard restarts after two consecutive failed probes, so recovery waits for
the next scheduled run.

## How this was built

I wrote these notes and scripts with LLM coding agents, Claude Code and Codex,
under my direction. I ran the fleet, chose the models and flags, diagnosed the
wedge, reviewed every change, and set the acceptance criteria. The agents wrote
most of the scripts and tests.

## The one law that decides everything

GB10 has 128 GB unified memory at **~273 GB/s**. Decode is memory-bandwidth-bound,
so decode speed ≈ bandwidth / bytes-touched-per-token:

| Model class | Decode speed | Verdict |
|---|---|---|
| Dense ~32B (Q4/FP8) | ~10 tok/s (public GB10 benchmarks: 10.7 on llama.cpp, 9.4–10.1 on Ollama) | unusable for agentic loops |
| MoE, 3B active (Qwen3-30B-A3B) | up to ~89 tok/s in public llama.cpp benchmarks, 50–53 tok/s single stream in my vLLM FP8 runs (2026-09-04, [evidence/throughput-2026-09-04.md](evidence/throughput-2026-09-04.md)) | the workable class |
| MoE, 3B active, 16 concurrent streams | ~350 tok/s aggregate, ~22 per stream (public vLLM benchmark, Qwen3-Coder-Next-80B-A3B int4) | batch workhorse |

Public figures come from [DandinPower's llama.cpp report](https://github.com/DandinPower/llama.cpp_bench/blob/main/dgx_spark/report.md), the [G3nadh Ollama dataset](https://huggingface.co/datasets/G3nadh/dgx-spark-benchmarks) and [NathanMaine's vLLM notes](https://github.com/NathanMaine/NVIDIA-DGX-Spark-with-vLLM), all measured on GB10. An aggregate is a capacity figure, not a per-request speed.

Prefill is compute-bound and fast. Rule of thumb: **on GB10, pick MoE models with
small active parameter counts.** Total parameters are nearly free (unified memory);
active parameters are what you pay per token.

## vLLM on GB10 (sm_121) — flags that mattered

The quickstart uses `nvcr.io/nvidia/vllm:26.08-py3`. The throughput record also
includes two runs on a previous community sparkrun vLLM build.

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

The [2026-08-26 wedge case study](docs/case-study-wedge-2026-08-26.md) records the probe timeline and workload effect.

Use the timer installation in the quickstart above.

### Requirements and troubleshooting

- `python3` and `curl` must be on PATH.
- The user must be allowed to run `docker restart` (usually via the docker group).
- User timers run only while a user session exists unless `loginctl enable-linger <user>` is set.
  Check the host first; never enable linger from a script.
- Inspect timers with `systemctl --user list-timers`.
- Inspect failures with `journalctl --user -u engine-guard.service`.
- State: `$HOME/.cache/engine-guard.fails` (`STATE` overrides).
- Log: `$HOME/.cache/engine-guard.log` (`LOG` overrides).

Client side, for batch pipelines hitting a local engine:
- Treat timeouts/connection-refused as **transient**: never record them as
  document-level failures; re-queue at the next pass.
- Add a **consecutive-transient circuit breaker** (mine trips at 30) so a dead
  engine can't burn a 29k-document queue as spurious failures.
- Pre-flight each pass with the generation probe, not `/v1/models`.

## Multi-node (ConnectX-7 ring)

Three Sparks in a ring over CX7: each edge is one cable carrying two planes
(~100 Gb/s each). The [multi-node evidence](evidence/multinode-2026-07-and-08.md)
records the test conditions and commands. Findings:

- **87.2 Gb/s** on an untuned IPv6 link-local QSFP link at MTU 1500 with 9321
  retransmits, and **105 Gb/s** on a tuned static-address link at MTU 9000.
- NCCL all-reduce measured about 20.6 GB/s bus bandwidth on two nodes with
  stock NCCL 2.28.9 and about 18–21 GB/s on the three-node ring with NCCL
  fork 2.29.7. The tests reported `#wrong=0` at every size.
- `NCCL_NET_PLUGIN=none` was set for the recorded NCCL tests. The three-node
  ring needed the 2.29.7 fork because stock 2.28.9 paired asymmetric rails by
  device index and failed.
- NVIDIA's guidance is that a second cable between the same two nodes does not add bandwidth and the second port belongs to the ring. I did not test it.
- DeepSeek V4 Flash ran in production with tensor parallelism TP=2 across two Sparks, with an OpenAI-compatible API on one node and a served context of 458,752 tokens. It fed the companion Terminal-Bench runs.

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
