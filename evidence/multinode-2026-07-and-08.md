# Multi-node network evidence, 2026-07 and 2026-08

These results are from my Spark fleet. The network test used a direct
ConnectX-7 QSFP cable between two Sparks. The collective test used the
three-Spark ring.

## iperf3, 2026-07-04

The tuned link had MTU 9000 and static addresses. Its recorded client command
and result were:

```bash
iperf3 -c 10.0.0.2 -p 5299 -t 5 -P 4
```

`[SUM]` was 61.4 GBytes in 5 seconds, or 105 Gbit/s.

The untuned link had MTU 1500 and an IPv6 link-local address. Its recorded
command shape and result were:

```bash
iperf3 -6 -c fe80::...%enp1s0f0np0 -p 5299 -t 5 -P 4
```

The ellipsis stands for the address omitted from the retained record. `[SUM]`
was 50.7 GBytes in 5 seconds, or 87.2 Gbit/s, with 9321 retransmits.

## nccl-tests all_reduce_perf, 2026-08-11

The recorded command shape was `all_reduce_perf` with these environment
settings. The four exact HCA names and complete command arguments were not
retained in the facts used here:

```text
NCCL_NET_PLUGIN=none
NCCL_IB_HCA=<four RoCE devices>
NCCL_IB_SUBNET_AWARE_ROUTING=1
NCCL_IB_MERGE_NICS=0
all_reduce_perf
```

| Topology and NCCL version | Large-message bus bandwidth | Correctness |
|---|---|---|
| Two nodes, stock NCCL 2.28.9 | About 20.6 GB/s, about 165 Gb/s | `#wrong=0` at every size |
| Three-node ring, NCCL fork 2.29.7 | About 18–21 GB/s, about 150–165 Gb/s | `#wrong=0` at every size |

The stated NVIDIA floor was 21.875 GB/s for the two-node result. Stock NCCL
2.28.9 paired rails by device index and failed on the asymmetric three-node
pairing. The 2.29.7 fork was needed for that ring. The three-node result is
not a stock-2.28.9 result.

DeepSeek V4 Flash later served production requests with TP=2 across two
Sparks. One node exposed an OpenAI-compatible API with a served context of
458,752 tokens. That service fed the companion Terminal-Bench runs. A
~397B-total MoE at PP=3 was a planned model choice, not an own run.
