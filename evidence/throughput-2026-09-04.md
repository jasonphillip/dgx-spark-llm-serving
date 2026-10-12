# Single-stream Qwen3 FP8 throughput, 2026-09-04

These are my four runs on a Spark host with GB10. The model was
`Qwen/Qwen3-30B-A3B-Instruct-2507-FP8` served by vLLM. The server used the
README flags: port 8010, `--max-model-len 32768`, `--max-num-seqs 16`,
`--gpu-memory-utilization 0.35`, and `--attention-backend TRITON_ATTN`.
Two runs used a previous community sparkrun vLLM image. Two used
`nvcr.io/nvidia/vllm:26.08-py3`.

## Request and calculation

A Python `urllib` script posted one chat completion to
`http://localhost:8010/v1/chat/completions`. The request used
`max_tokens: 256`, `temperature: 0`, and this user prompt:

> Write a 250-word plain description of how a ring network of three computers passes data.

The script measured wall-clock time around the request and calculated
`completion_tokens / elapsed_seconds`. This is the request shape, not a
preserved copy of the original script:

```text
POST /v1/chat/completions
model: Qwen/Qwen3-30B-A3B-Instruct-2507-FP8
messages: [{role: user, content: "Write a 250-word plain description of how a ring network of three computers passes data."}]
max_tokens: 256
temperature: 0
timing: wall-clock around one urllib request
rate: completion_tokens / elapsed_seconds
```

| Image | Completion tokens | Elapsed | Recorded output rate |
|---|---:|---:|---:|
| Community sparkrun vLLM build | 256 | 4.9 s | 51.8 tok/s |
| Community sparkrun vLLM build | 256 | 4.8 s | 53.1 tok/s |
| NVIDIA vLLM 26.08-py3 | 256 | 5.1 s | 50.4 tok/s |
| NVIDIA vLLM 26.08-py3 | 256 | 4.8 s | 53.0 tok/s |

Each run had one stream. The server was warm. The cache state was not recorded.
There is no TTFT or inter-token latency measurement. No raw output file was
kept beyond the session transcript. Four runs do not establish a distribution
or sustained throughput. I did not measure 12 to 16 concurrent streams.
