import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from repro.cli import check_model_files, main, score, validate_manifest


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.manifest = []
        self.predictions = []
        # Unequal class sizes detect a pooled-accuracy/macroscopic-mean mixup.
        for language, label, count in (("en", "complete", 2), ("en", "incomplete", 1),
                                        ("zh", "complete", 1), ("zh", "incomplete", 1)):
            for i in range(count):
                row = {"sample_id": f"{language}/{label}/{i}", "language": language, "gold_label": label}
                self.manifest.append(row)
                self.predictions.append({**row, "predicted_label": label})

    def test_macro_uses_class_means_and_keeps_errors_in_denominator(self):
        self.predictions[0]["predicted_label"] = "error"
        self.predictions[2]["predicted_label"] = "undecided"
        metrics, rows = score(self.manifest, self.predictions)
        self.assertEqual(metrics["en_complete"], {"correct": 1, "total": 2, "accuracy": 0.5})
        self.assertEqual(metrics["en_macro_accuracy"], 0.25)
        self.assertEqual(metrics["zh_macro_accuracy"], 1)
        self.assertEqual(metrics["errors"], 1)
        self.assertEqual(metrics["undecided"], 1)
        self.assertEqual(sum(not r["correct"] for r in rows), 2)

    def test_missing_prediction_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "禁止缩小分母"):
            score(self.manifest, self.predictions[:-1])

    def test_duplicate_prediction_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "重复/未知"):
            score(self.manifest, self.predictions + self.predictions[:1])

    def test_unknown_prediction_is_rejected(self):
        self.predictions[0]["sample_id"] = "unknown"
        with self.assertRaisesRegex(ValueError, "重复/未知"):
            score(self.manifest, self.predictions)

    def test_gold_cannot_be_relabelled(self):
        self.predictions[0]["gold_label"] = "incomplete"
        with self.assertRaisesRegex(ValueError, "冻结标签"):
            score(self.manifest, self.predictions)

    def test_service_idle_speak_cannot_be_scored_as_binary(self):
        for state in ("idle", "speak", "<|user_complete|>"):
            with self.subTest(state=state):
                self.predictions[0]["predicted_label"] = state
                with self.assertRaisesRegex(ValueError, "未知预测类别"):
                    score(self.manifest, self.predictions)

    def test_empty_or_missing_class_is_rejected(self):
        for rows in ([], self.manifest[:-1]):
            with self.assertRaises(ValueError):
                validate_manifest(rows)

    def test_duplicate_manifest_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "重复"):
            validate_manifest(self.manifest + self.manifest[:1])

    def test_frozen_counts_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "计数"):
            validate_manifest(self.manifest, {("en", "complete"): 318})

    def test_model_size_sha256_and_missing_file_are_checked(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "repro").mkdir()
            (root / "weights").mkdir()
            model = root / "weights/model.bin"
            model.write_bytes(b"abc")
            lock = {"model": {"files": [{"path": "model.bin", "bytes": 3,
                                        "sha256": hashlib.sha256(b"abc").hexdigest(), "git_blob_id": None}]}}
            (root / "repro/sources.lock.json").write_text(json.dumps(lock))
            with mock.patch("repro.cli.ROOT", root):
                self.assertEqual(check_model_files(root / "weights"), 1)
                model.write_bytes(b"xyz")
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    check_model_files(root / "weights")
                model.write_bytes(b"longer")
                with self.assertRaisesRegex(ValueError, "大小不符"):
                    check_model_files(root / "weights")

    def test_small_model_metadata_git_blob_is_checked(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "repro").mkdir()
            (root / "weights").mkdir()
            (root / "weights/config.json").write_bytes(b"{}")
            lock = {"model": {"files": [{"path": "config.json", "bytes": 2, "sha256": None,
                                        "git_blob_id": hashlib.sha1(b"blob 2\0{}").hexdigest()}]}}
            (root / "repro/sources.lock.json").write_text(json.dumps(lock))
            with mock.patch("repro.cli.ROOT", root):
                self.assertEqual(check_model_files(root / "weights"), 1)

    def test_run_is_blocked_before_importing_model(self):
        import sys
        self.assertNotIn("service.model", sys.modules)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["run"])
        self.assertEqual(result, 3)
        self.assertEqual(json.loads(output.getvalue())["model_inference"], "NOT_RUN")
        self.assertNotIn("service.model", sys.modules)

    def test_real_evaluate_is_blocked(self):
        with contextlib.redirect_stderr(io.StringIO()):
            result = main(["evaluate", "--manifest", "missing", "--predictions", "missing", "--out", "missing"])
        self.assertEqual(result, 2)

    def test_csv_export_and_overwrite_protection(self):
        self.predictions[0]["predicted_label"] = "incomplete"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
            with (path / "predictions.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(self.predictions[0]))
                writer.writeheader()
                writer.writerows(self.predictions)
            argv = ["evaluate", "--manifest", str(path / "manifest.json"),
                    "--predictions", str(path / "predictions.csv"), "--out", str(path / "output"), "--synthetic-test"]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv), 0)
            result = json.loads((path / "output/metrics.json").read_text())
            self.assertEqual(result["result_kind"], "SYNTHETIC_TEST")
            self.assertFalse(result["paper_comparable"])
            with (path / "output/failures.csv").open(encoding="utf-8", newline="") as stream:
                failures = list(csv.DictReader(stream))
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["sample_id"], self.predictions[0]["sample_id"])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(argv), 2)


if __name__ == "__main__":
    unittest.main()
