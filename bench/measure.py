#!/usr/bin/env python3
"""Measure bounded chat-completion throughput on a warm vLLM server."""

import argparse
import concurrent.futures
import datetime as dt
import json
import math
import os
import platform
import signal
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path


PROMPT = "Write a 250-word plain description of how a ring network of three computers passes data."
TIMEOUT_SECONDS = 180


def percentile(values, percent):
    """Linear interpolation between sorted observations."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    low = int(position)
    return ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (position - low)


def parse_stream(lines, started_at, now=time.perf_counter):
    """Parse OpenAI SSE data lines; timestamps mark arrival of content chunks."""
    arrivals = []
    usage_tokens = None
    done = False
    for raw_line in lines:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            done = True
            break
        event = json.loads(data)
        if "error" in event:
            raise ValueError(f"server stream error: {event['error']}")
        usage = event.get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int):
            usage_tokens = usage["completion_tokens"]
        for choice in event.get("choices", []):
            content = choice.get("delta", {}).get("content")
            if content:
                arrivals.append(now())
    ended_at = now()
    if not done:
        raise ValueError("stream ended without [DONE]")
    if not arrivals:
        raise ValueError("stream contained no content chunks")
    elapsed = ended_at - started_at
    intervals = [later - earlier for earlier, later in zip(arrivals, arrivals[1:])]
    return {
        "completion_tokens": usage_tokens,
        "token_count_source": "usage" if usage_tokens is not None else "unavailable",
        "content_chunks": len(arrivals),
        "wall_seconds": elapsed,
        "tokens_per_second": usage_tokens / elapsed if usage_tokens is not None and elapsed > 0 else None,
        "ttft_seconds": arrivals[0] - started_at,
        "mean_inter_chunk_latency_seconds": statistics.mean(intervals) if intervals else None,
    }


def get_json(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def request_completion(args):
    body = json.dumps({
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "max_tokens": args.max_tokens,
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }).encode("utf-8")
    request = urllib.request.Request(
        args.url.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started_at = time.perf_counter()
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return parse_stream(response, started_at)


def observed_request(args, kind, index, round_index=None):
    record = {"kind": kind, "index": index}
    if round_index is not None:
        record["round"] = round_index
    try:
        record.update(request_completion(args))
        record["status"] = "ok"
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        record.update(status="error", error=str(exc))
    return record


def gated_request(gate, args, index, round_index):
    gate.wait()
    return observed_request(args, "concurrent", index, round_index)


def summary(records, field):
    values = [row[field] for row in records if row["status"] == "ok" and row[field] is not None]
    return {"p50": percentile(values, 50), "p95": percentile(values, 95)}


def gpu_name():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return result.stdout.strip().splitlines()[0] or None
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8010")
    parser.add_argument("--model", default="qwen3-30b-a3b")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--single-runs", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--concurrent-rounds", type=int, default=1)
    parser.add_argument("--deadline-seconds", type=float, default=600)
    parser.add_argument("--prompt", default=PROMPT)
    parser.add_argument("--image")
    parser.add_argument("--out", type=Path, default=Path("bench/results") / (dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"))
    args = parser.parse_args(argv)
    if not 1 <= args.max_tokens <= 4096 or not 1 <= args.single_runs <= 100 or not 1 <= args.concurrency <= 128 or not 1 <= args.concurrent_rounds <= 100:
        parser.error("max-tokens must be 1..4096, single-runs 1..100, concurrency 1..128, concurrent-rounds 1..100")
    if args.single_runs + args.concurrency * args.concurrent_rounds > 1000:
        parser.error("at most 1000 measured requests are allowed")
    if not math.isfinite(args.deadline_seconds) or args.deadline_seconds <= 0:
        parser.error("deadline-seconds must be a positive finite number")
    return args


def main(argv=None):
    args = arguments(argv)
    def deadline_abort(_signum, _frame):
        os.write(2, f"measurement aborted: overall {args.deadline_seconds:g}-second deadline exceeded\n".encode())
        os._exit(124)

    signal.signal(signal.SIGALRM, deadline_abort)
    signal.setitimer(signal.ITIMER_REAL, args.deadline_seconds)
    started_utc = dt.datetime.now(dt.timezone.utc).isoformat()
    base = args.url.rstrip("/")
    try:
        models = get_json(base + "/v1/models")
        model_ids = [item["id"] for item in models["data"]]
        if not model_ids:
            raise ValueError("/v1/models returned no model ids")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"model lookup failed: {exc}", file=sys.stderr)
        return 1
    try:
        version = get_json(base + "/version")
    except (OSError, ValueError):
        version = None

    print("Warmup: 1 request (untimed in results)", flush=True)
    warmup = observed_request(args, "warmup", 1)
    if warmup["status"] != "ok":
        print(f"warmup failed: {warmup['error']}", file=sys.stderr)
        return 1

    singles = [observed_request(args, "single", i + 1) for i in range(args.single_runs)]
    rounds = []
    for number in range(1, args.concurrent_rounds + 1):
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            gate = threading.Event()
            futures = [executor.submit(gated_request, gate, args, i, number) for i in range(1, args.concurrency + 1)]
            round_started = time.perf_counter()
            gate.set()
            requests = [future.result() for future in futures]
            round_wall = time.perf_counter() - round_started
        rounds.append({
            "round": number,
            "wall_seconds": round_wall,
            "aggregate_tokens_per_second": (
                sum(r["completion_tokens"] for r in requests) / round_wall
                if round_wall > 0 and all(r["status"] == "ok" and r["completion_tokens"] is not None for r in requests) else None
            ),
            "requests": requests,
            "ttft_seconds": summary(requests, "ttft_seconds"),
            "per_request_tokens_per_second": summary(requests, "tokens_per_second"),
            "mean_inter_chunk_latency_seconds": summary(requests, "mean_inter_chunk_latency_seconds"),
        })

    result = {
        "arguments": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
        "utc_start_time": started_utc,
        "temperature": 0,
        "model_id": args.model if args.model in model_ids else model_ids[0],
        "model_ids": model_ids,
        "server_version": version,
        "container_image": args.image,
        "platform": platform.platform(),
        "uname": list(platform.uname()),
        "nvidia_smi_name": gpu_name(),
        "single_requests": singles,
        "single_tokens_per_second": summary(singles, "tokens_per_second"),
        "single_ttft_seconds": summary(singles, "ttft_seconds"),
        "single_mean_inter_chunk_latency_seconds": summary(singles, "mean_inter_chunk_latency_seconds"),
        "concurrent_rounds": rounds,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    def fmt(value):
        return "n/a" if value is None else f"{value:.3f}"
    print("Mode       Requests  tok/s p50/p95  TTFT s p50/p95  Inter-chunk s p50/p95  Aggregate tok/s")
    print(f"Single     {args.single_runs:8d}  {fmt(result['single_tokens_per_second']['p50'])}/{fmt(result['single_tokens_per_second']['p95'])}       {fmt(result['single_ttft_seconds']['p50'])}/{fmt(result['single_ttft_seconds']['p95'])}       {fmt(result['single_mean_inter_chunk_latency_seconds']['p50'])}/{fmt(result['single_mean_inter_chunk_latency_seconds']['p95'])}        -")
    for row in rounds:
        print(f"Round {row['round']:<4d} {args.concurrency:8d}  {fmt(row['per_request_tokens_per_second']['p50'])}/{fmt(row['per_request_tokens_per_second']['p95'])}       {fmt(row['ttft_seconds']['p50'])}/{fmt(row['ttft_seconds']['p95'])}       {fmt(row['mean_inter_chunk_latency_seconds']['p50'])}/{fmt(row['mean_inter_chunk_latency_seconds']['p95'])}        {fmt(row['aggregate_tokens_per_second'])}")
    print(f"JSON: {args.out}")
    return int(any(r["status"] != "ok" for r in singles + [request for row in rounds for request in row["requests"]]))


if __name__ == "__main__":
    sys.exit(main())
