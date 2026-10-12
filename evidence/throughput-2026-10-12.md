# Warm Qwen3 FP8 throughput, 2026-10-12

The preserved result is [bench/results/20261012T201254Z.json](../bench/results/20261012T201254Z.json). It was produced by the previous version of `bench/measure.py` on one DGX Spark unit. The hostname in its `uname` field was replaced before publication. Every timed request reports `token_count_source: usage` and 256 server-reported completion tokens. The script sent one untimed warmup before the timed requests.

## Conditions

- Server: `nvcr.io/nvidia/vllm:26.08-py3`, vLLM 0.27.1 (`/version` reported `0.27.1+93523f72.dev`).
- Model: `Qwen/Qwen3-30B-A3B-Instruct-2507-FP8`, served as `qwen3-30b-a3b`.
- GPU: NVIDIA GB10. The server was warm, with no other model loaded. KV cache capacity was 134,416 tokens.
- README serving flags: `--max-model-len 32768 --max-num-seqs 16 --gpu-memory-utilization 0.35 --attention-backend TRITON_ATTN`.
- Request: `max_tokens: 256`, `temperature: 0`, prompt: “Write a 250-word plain description of how a ring network of three computers passes data.”

The command corresponding to the arguments recorded in the JSON was:

```bash
python3 bench/measure.py --url http://localhost:8010 --model qwen3-30b-a3b --max-tokens 256 --single-runs 5 --concurrency 16 --concurrent-rounds 1 --image nvcr.io/nvidia/vllm:26.08-py3 --out bench/results/20261012T201254Z.json
```

The result records the arguments, not the original shell history. The command above reproduces those arguments with the script defaults for prompt and temperature.

## Result

| Mode | Timed requests | Per-request output rate | Aggregate output rate | Time to first content chunk |
|---|---:|---:|---:|---:|
| Single stream | 5 × 256 tokens | 52.8–53.0 tok/s; p50 52.9 tok/s | — | 0.094–0.099 s |
| 16 concurrent streams | 1 round × 16 × 256 tokens | p50 27.6 tok/s | 441.6 tok/s over a 9.3 s round | p50 0.716 s |

Output rates divide server-reported completion tokens by measured wall time. The concurrent aggregate divides 4,096 completion tokens by the round wall time of 9.2756 seconds. Time to first content chunk includes connection time.

## Limits

The 16-stream result covers one round only. The test used one prompt, one model, and one warm server state. It does not establish sustained capacity or prompt-independent performance. The saved JSON calls its interval field `mean_inter_token_latency_seconds`; that value measures arrival intervals between content chunks, not between tokens. The script now calls this inter-chunk latency. The saved JSON remains unchanged.
