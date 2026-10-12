# Generation wedge and recovery, 2026-08-26

This incident affected a local vLLM container named `llm` on
2026-08-26, local time. The API still answered a metadata request, but text
generation stopped completing. A `/v1/models` response could therefore say
the process was alive while the batch client could not get work done. The
client was a document-extraction client. This case records one outage and its
observed recovery.

## Detection and timeline

The guard sent a real five-token completion instead of checking only an API
metadata endpoint. It ran from a systemd user timer set to a 10-minute
interval after each run. Each request had a separate 90-second timeout. The
guard restarted the container after two consecutive failed probes. A healthy
probe between failures reset the count.

The `engine-guard.log` lines show `05:32 probe fail #1`, `05:54 probe fail
#1`, and `06:50 probe fail #1`. Each of those first failures was followed by
a healthy probe, so none caused a restart. The next recorded line was `07:02
probe fail #2`, followed by `07:02 RESTARTING llm (wedged)`.
The two-strike policy used two failed probes, at 06:50 and 07:02. The log
timestamps have minute precision, so they do not give
the precise start of the wedge.

The systemd journal shows guard runs finishing at 06:50:32, 07:02:13,
07:12:14, and 07:22:14. The 07:02 run issued the restart command. The first
healthy run after restart was at 07:12. The container restart and model load
took under 10 minutes, as bounded by that healthy probe. A container restart
alone would not prove model readiness. The completed generation probe is the
readiness evidence here.

## Workload effect

Before the restart, the client completed five to eight documents per minute
through 07:02. It completed no documents from 07:03 through 07:14. It then
completed one to four per minute from 07:15 through 07:19. It was back to
five to eight per minute from 07:20. Workload throughput therefore returned
to its earlier range about 18 minutes after the 07:02 restart command. That
is a different interval from the restart-to-healthy-probe interval.

The hourly completed-document totals were 258 at 05h, 434 at 06h, 300 at
07h, and 385 at 08h. These counts show a dip during the affected hour, but
they do not isolate every cause of variation between hours. The client's
transient handling kept timeouts out of document-level failures. Its breaker
stopped after 30 consecutive transient errors. No documents were marked
failed by this outage.

## Recovery decision and limit

I chose a simple two-strike restart because this failure had a clear
generation-level symptom and a reversible local recovery action. A smarter
monitor could have added more state and another decision path while still
needing a real completion to detect the wedge. Two failed probes avoided a
restart for each isolated failure shown earlier in the log. The cost was
detection latency tied to the timer interval, plus time for the model to
load and the client to resume work.

This case does not establish an uptime percentage or a general reliability
rate. It shows one recovery on this fleet. The 07:12 healthy probe supports
engine readiness then. The 07:20 workload rate shows when the batch client
was fully back in its earlier observed range.
