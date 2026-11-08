import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from repro.catalog import ROOT, read_json
from repro.cli import main
from repro.runner import (OfficialAdapter, chunk_plan, first_endpoint, require_p2_authorization,
                          run_samples, verify_run_predictions)


class FakeAdapter:
    def __init__(self, fail=False):
        self.resets = 0
        self.index = 0
        self.fail = fail

    def reset(self):
        self.index = 0
        self.resets += 1

    def process(self, chunk):
        if self.fail:
            raise RuntimeError("fixture ASR failure")
        self.index += 1
        return {"model_state": "<|user_nonidle|>" if self.index == 1 else "<|user_complete|>",
                "service_state": {"state": "speak"}}

    def metadata(self):
        return {"test_double": True}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.protocol = read_json(ROOT / "repro/protocol.json")
        self.rows = [{"sample_id": f"{lang}/{label}/1", "language": lang, "gold_label": label,
                      "relative_path": f"{label}/1.wav"}
                     for lang in ("en", "zh") for label in ("complete", "incomplete")]
        self.seeds = []
        self.adapters = []

    def factory(self, language, output):
        adapter = FakeAdapter()
        self.adapters.append(adapter)
        return adapter

    def loader(self, path, protocol):
        return [[0.0] * 2560, [0.0] * 2560], {"test_double": True}

    def run_fixture(self, out, **kwargs):
        return run_samples(self.rows, "/unused", out, self.protocol, self.factory,
                           self.loader, self.seeds.append, "fixed-sources", **kwargs)

    def test_first_endpoint_ignores_service_state_and_later_conflict(self):
        trace = [{"model_state": "<|user_incomplete|>"},
                 {"model_state": "<|user_nonidle|>"},
                 {"model_state": None, "service_state": "speak"},
                 {"model_state": "<|user_complete|>"},
                 {"model_state": "<|user_incomplete|>"}]
        self.assertEqual(first_endpoint(trace), "complete")
        self.assertEqual(first_endpoint(trace[:3]), "undecided")
        self.assertEqual(first_endpoint([{"service_state": "speak"}]), "undecided")

    def test_exact_tail_then_partial_chunk_padding(self):
        plan = chunk_plan(16001, self.protocol)
        self.assertEqual(plan["tail_zero_samples"], 32000)
        self.assertEqual(plan["final_zero_padding"], 639)
        self.assertEqual(plan["chunks"], 19)
        with self.assertRaises(ValueError):
            chunk_plan(0, self.protocol)

    def test_per_sample_reset_seed_and_full_resume_no_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "run"
            records = self.run_fixture(out, synthetic=True)
            self.assertEqual(len(records), 4)
            self.assertEqual(self.seeds, [42] * 4)
            self.assertEqual([m.resets for m in self.adapters], [2, 2])
            self.assertTrue(all(r["predicted_label"] == "complete" for r in records))
            self.assertTrue(all(r["result_kind"] == "SYNTHETIC_TEST" for r in records))
            before = (out / "records.jsonl").read_bytes()
            again = self.run_fixture(out, synthetic=True, resume=True)
            self.assertEqual(again, records)
            self.assertEqual(before, (out / "records.jsonl").read_bytes())
            self.assertEqual(len(self.adapters), 2)

    def test_changed_protocol_cannot_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "run"
            self.run_fixture(out, synthetic=True)
            self.protocol = {**self.protocol, "far_field_threshold": 0.0}
            with self.assertRaisesRegex(ValueError, "不同协议"):
                self.run_fixture(out, synthetic=True, resume=True)

    def test_torn_ledger_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "run"
            self.run_fixture(out, synthetic=True)
            ledger = out / "records.jsonl"
            ledger.write_bytes(ledger.read_bytes() + b'{"sample_id":')
            before = ledger.read_bytes()
            with self.assertRaisesRegex(ValueError, "末尾不完整"):
                self.run_fixture(out, synthetic=True, resume=True)
            self.assertEqual(ledger.read_bytes(), before)

    def test_corrupt_trace_prevents_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "run"
            records = self.run_fixture(out, synthetic=True)
            (out / records[0]["raw_state_trace"]).write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "轨迹缺失/损坏"):
                self.run_fixture(out, synthetic=True, resume=True)

    def test_sample_error_is_saved_then_run_stops(self):
        self.factory = lambda language, output: FakeAdapter(fail=True)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "run"
            with self.assertRaisesRegex(RuntimeError, "停止后续推理"):
                self.run_fixture(out, synthetic=True)
            records = (out / "records.jsonl").read_text().splitlines()
            self.assertEqual(len(records), 1)
            record = json.loads(records[0])
            self.assertEqual(record["predicted_label"], "error")
            self.assertEqual(record["error"]["message"], "fixture ASR failure")

    def test_real_verification_rejects_mock_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "run"
            self.run_fixture(out, synthetic=True)
            with self.assertRaisesRegex(ValueError, "不是可用于真实"):
                verify_run_predictions(out, self.rows, self.protocol, "fixed-sources")

    def test_contract_and_trace_recheck_without_gpu(self):
        # Fixture stays in TemporaryDirectory; does not become a project model result.
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "run"
            records = self.run_fixture(out, synthetic=False)
            rows, verified, _ = verify_run_predictions(out, self.rows, self.protocol, "fixed-sources")
            self.assertEqual(len(rows), 4)
            self.assertEqual(verified, records)
            with self.assertRaisesRegex(ValueError, "源码指纹"):
                verify_run_predictions(out, self.rows, self.protocol, "changed-sources")

    def test_p2_gate_runs_before_gpu_imports(self):
        import sys
        with self.assertRaisesRegex(ValueError, "P2 未授权"):
            require_p2_authorization(None)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["run", "--execute"]), 2)
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("service.model", sys.modules)

    def test_observation_preserves_model_return_and_restores_method(self):
        class Model:
            def infer(self, chunk):
                return "<|user_incomplete|>", "words", "buffer"
            def process(self, chunk):
                self.infer(chunk)
                return {"state": "idle"}
        model = Model()
        original = model.infer
        result = OfficialAdapter(model).process([0])
        self.assertEqual(result["model_state"], "<|user_incomplete|>")
        self.assertEqual(result["service_state"], {"state": "idle"})
        self.assertEqual(model.infer, original)


if __name__ == "__main__":
    unittest.main()
