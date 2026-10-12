"""Offline tests for streamed benchmark timing."""

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("measure", Path(__file__).resolve().parents[1] / "bench" / "measure.py")
measure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measure)


class StreamTest(unittest.TestCase):
    def test_usage_and_arrival_math(self):
        lines = [
            b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"hello"}}]}\n',
            b'data: {"choices":[{"delta":{"content":" world"}}]}\n',
            b'data: {"choices":[],"usage":{"completion_tokens":3}}\n',
            b'data: [DONE]\n',
        ]
        times = iter([10.5, 11.0, 12.0])
        result = measure.parse_stream(lines, 10.0, lambda: next(times))
        self.assertEqual(result["completion_tokens"], 3)
        self.assertEqual(result["token_count_source"], "usage")
        self.assertEqual(result["content_chunks"], 2)
        self.assertAlmostEqual(result["wall_seconds"], 2.0)
        self.assertAlmostEqual(result["tokens_per_second"], 1.5)
        self.assertAlmostEqual(result["ttft_seconds"], 0.5)
        self.assertAlmostEqual(result["mean_inter_chunk_latency_seconds"], 0.5)

    def test_missing_usage_does_not_count_chunks_as_tokens(self):
        lines = [
            b'data: {"choices":[{"delta":{"content":"a"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"b"}}]}\n',
            b'data: [DONE]\n',
        ]
        times = iter([2.0, 3.0, 4.0])
        result = measure.parse_stream(lines, 1.0, lambda: next(times))
        self.assertIsNone(result["completion_tokens"])
        self.assertEqual(result["token_count_source"], "unavailable")
        self.assertEqual(result["content_chunks"], 2)
        self.assertIsNone(result["tokens_per_second"])
        self.assertAlmostEqual(result["mean_inter_chunk_latency_seconds"], 1.0)

    def test_deadline_aborts_model_lookup(self):
        script = (
            "import importlib.util, time; "
            f"spec = importlib.util.spec_from_file_location('measure', {str(Path(__file__).resolve().parents[1] / 'bench' / 'measure.py')!r}); "
            "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
            "module.get_json = lambda url: time.sleep(1); "
            "module.main(['--deadline-seconds', '0.05'])"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 124)
        self.assertIn("overall 0.05-second deadline exceeded", result.stderr)


if __name__ == "__main__":
    unittest.main()
