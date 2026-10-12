# Warm vLLM throughput measurement

Start the server first and wait until it is warm. From the repository root, run:

```bash
python3 bench/measure.py --url http://localhost:8010 --model qwen3-30b-a3b --max-tokens 256 --single-runs 5 --concurrency 16 --concurrent-rounds 1
```

The default workload has one untimed warmup, five single-stream requests, and one round of 16 concurrent requests. It finishes in a few minutes on a healthy warm server. Each request has a 180-second socket timeout. `--deadline-seconds` defaults to 600 and aborts the whole run, including lookup and warmup, if exceeded. Results go to `bench/results/<UTC timestamp>.json` unless `--out` sets another path. Use `--image` to record the container image; the script cannot infer it reliably.

The script uses streamed chat completions at temperature 0. TTFT starts before HTTP connection and ends on the first content chunk. Inter-chunk latency uses arrival intervals between content chunks; chunks may contain more than one token. The JSON records `content_chunks` for each request. Token rates use server-reported completion-token usage only. When usage is absent, `completion_tokens` and `tokens_per_second` are null, and `token_count_source` is `unavailable`. Aggregate token rate is null if any request in its round lacks usage. The p50 and p95 values use linear interpolation over successful requests with available values. Failed requests remain in the JSON, and the command exits nonzero.

Results are not comparable across different models, prompts, server flags, or container images.
