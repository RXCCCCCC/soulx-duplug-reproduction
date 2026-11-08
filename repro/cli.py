"""复现入口：默认只做本地检查，真实推理需单独 P2 授权。"""
import argparse
import collections
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

from repro.catalog import EXPECTED, ROOT, dump, read_json

LABELS = {"complete", "incomplete"}
PREDICTIONS = LABELS | {"undecided", "error"}


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def validate_manifest(rows, expected=None):
    if not rows:
        raise ValueError("空 manifest")
    found = {}
    counts = collections.Counter()
    for row in rows:
        sid = row["sample_id"]
        if not isinstance(sid, str) or not sid or sid in found:
            raise ValueError(f"空或重复 sample_id: {sid}")
        group = (row["language"], row["gold_label"])
        if group not in EXPECTED:
            raise ValueError(f"非法语言/标签: {group}")
        found[sid] = row
        counts[group] += 1
    if set(counts) != set(EXPECTED):
        raise ValueError("必须包含四个子集，禁止空类/缺类")
    if expected is not None and dict(counts) != expected:
        raise ValueError(f"样本计数不符合冻结清单: {dict(counts)}")
    return found


def score(manifest, predictions, expected=None):
    gold = validate_manifest(manifest, expected)
    seen = set()
    correct = collections.Counter()
    total = collections.Counter()
    records = []
    for prediction in predictions:
        sid = prediction["sample_id"]
        if sid in seen or sid not in gold:
            raise ValueError(f"重复/未知预测: {sid}")
        seen.add(sid)
        row = gold[sid]
        if prediction["language"] != row["language"] or prediction["gold_label"] != row["gold_label"]:
            raise ValueError(f"预测与冻结标签不一致: {sid}")
        label = prediction["predicted_label"]
        if label not in PREDICTIONS:
            raise ValueError(f"未知预测类别: {label}")
        group = (row["language"], row["gold_label"])
        hit = label == row["gold_label"]
        total[group] += 1
        correct[group] += hit
        records.append({**prediction, "correct": hit})
    if seen != set(gold):
        raise ValueError(f"缺少 {len(set(gold) - seen)} 条预测；禁止缩小分母")
    metrics = {f"{lang}_{label}": {"correct": correct[(lang, label)],
                                 "total": total[(lang, label)],
                                 "accuracy": correct[(lang, label)] / total[(lang, label)]}
               for lang, label in EXPECTED}
    for lang in ("en", "zh"):
        metrics[lang + "_macro_accuracy"] = sum(
            metrics[f"{lang}_{label}"]["accuracy"] for label in ("complete", "incomplete")
        ) / 2
    metrics["undecided"] = sum(r["predicted_label"] == "undecided" for r in records)
    metrics["errors"] = sum(r["predicted_label"] == "error" for r in records)
    return metrics, records


def check_model_files(model_root):
    lock = read_json(ROOT / "repro/sources.lock.json")
    checked = 0
    for row in lock["model"]["files"]:
        path = Path(model_root) / row["path"]
        if not path.is_file() or path.stat().st_size != row["bytes"]:
            raise ValueError(f"模型文件缺失/大小不符: {row['path']}")
        if row["sha256"]:
            if digest(path) != row["sha256"]:
                raise ValueError(f"模型 SHA256 不符: {row['path']}")
        elif row.get("git_blob_id"):
            data = path.read_bytes()
            blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if blob != row["git_blob_id"]:
                raise ValueError(f"模型 Git blob 不符: {row['path']}")
        checked += 1
    return checked


def local_check(data_root=None, model_root=None):
    manifest = read_json(ROOT / "repro/dataset_manifest.json")
    validate_manifest(manifest, EXPECTED)
    verified = 0
    for source in read_json(ROOT / "evidence/sources.json"):
        if source["status"] != "ok":
            raise ValueError(f"缺少证据: {source['file']}")
        if digest(ROOT / "evidence" / source["file"]) != source["sha256"]:
            raise ValueError(f"证据校验失败: {source['file']}")
        verified += 1
    samples = read_json(ROOT / "evidence/small_sample_audit.json")
    for sample in samples:
        if digest(ROOT / "evidence/samples" / sample["file"]) != sample["sha256"]:
            raise ValueError(f"样本校验失败: {sample['file']}")
    labels = audit_labels(manifest)
    if data_root:
        for row in manifest:
            path = Path(data_root) / row["language"] / row["relative_path"]
            if not path.is_file() or path.stat().st_size != row["bytes"]:
                raise ValueError(f"数据缺失/大小不符: {row['sample_id']}")
            if row.get("sha256") and digest(path) != row["sha256"]:
                raise ValueError(f"数据 SHA256 不符: {row['sample_id']}")
            if row.get("git_blob_id") and not row.get("sha256"):
                data = path.read_bytes()
                blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                if blob != row["git_blob_id"]:
                    raise ValueError(f"数据 Git blob 不符: {row['sample_id']}")
            if row.get("crc32"):
                import zlib
                if f"{zlib.crc32(path.read_bytes()):08x}" != row["crc32"]:
                    raise ValueError(f"数据 CRC 不符: {row['sample_id']}")
    model_files = check_model_files(model_root) if model_root else 0
    return {"status": "LOCAL_STATIC_PASS", "manifest_samples": len(manifest),
            "source_files_verified": verified, "small_files_verified": len(samples),
            "label_audit": labels,
            "full_data_verified": data_root is not None, "model_files_verified": model_files,
            "model_inference": "NOT_RUN"}


def audit_labels(manifest):
    result = {}
    for label in ("complete", "incomplete"):
        base = ROOT / "evidence/samples"
        english = [json.loads(line) for line in (base / f"en_{label}.jsonl").read_text(
            encoding="utf-8").splitlines() if line.strip()]
        english_rows = [r for r in manifest if r["language"] == "en" and r["gold_label"] == label]
        if len(english) != len(english_rows) or any("text" not in row for row in english):
            raise ValueError(f"英文文本数量/结构不符: {label}")
        zh = [json.loads(line) for line in (base / f"zh_{label}.list").read_text(
            encoding="utf-8").splitlines() if line.strip()]
        paths = ["testset/" + row["wav"].removeprefix("./") for row in zh]
        expected_paths = {r["relative_path"] for r in manifest
                          if r["language"] == "zh" and r["gold_label"] == label}
        if len(paths) != len(set(paths)) or set(paths) != expected_paths:
            raise ValueError(f"中文清单与音频路径不一致: {label}")
        if any(not row["txt"].endswith(f"<{label.upper()}>") for row in zh):
            raise ValueError(f"中文目录与转写末尾标签不一致: {label}")
        result[label] = {"en_text_count": len(english), "zh_paths_and_text_labels_verified": len(zh),
                         "zh_state_field_values_not_used_as_labels": sorted({str(r.get("state")) for r in zh})}
    return result


def setup_plan():
    packages = ("torch", "torchaudio", "transformers", "peft", "funasr", "modelscope", "omegaconf", "pytorch-lightning")
    installed = {}
    for name in packages:
        try:
            installed[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed[name] = None
    return {"mode": "PLAN_ONLY_NO_INSTALL", "python": sys.version,
            "installed_metadata_only": installed,
            "upstream_requirements_sha256": digest(ROOT / "requirements.txt"),
            "official_python": "3.10", "cloud_environment": "NOT_PREPARED",
            "next": "P1 授权后核验镜像/安装路径并按 requirements.txt 固定版本；不自动安装"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="只输出环境计划，不安装")
    check = sub.add_parser("check", help="校验证据/manifest/小样本；可指定完整数据目录")
    check.add_argument("--data-root", type=Path)
    check.add_argument("--model-root", type=Path)
    run = sub.add_parser("run", help="默认仅显示门禁；--execute 需明确 P2 授权记录")
    run.add_argument("--execute", action="store_true")
    run.add_argument("--authorization", type=Path)
    run.add_argument("--data-root", type=Path)
    run.add_argument("--out", type=Path)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--limit-per-subset", type=int)
    evaluate = sub.add_parser("evaluate", help="从明确预测标签评分，不猜测状态规则")
    evaluate.add_argument("--manifest", type=Path)
    evaluate.add_argument("--predictions", type=Path)
    evaluate.add_argument("--run-dir", type=Path)
    evaluate.add_argument("--out", type=Path, required=True)
    evaluate.add_argument("--synthetic-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            print(json.dumps(setup_plan(), ensure_ascii=False, indent=2))
        elif args.command == "check":
            print(json.dumps(local_check(args.data_root, args.model_root), ensure_ascii=False, indent=2))
        elif args.command == "run":
            from repro.runner import (check_protocol, load_audio, model_factory, require_p2_authorization,
                                      run_samples, seed_runtime, source_fingerprint)
            protocol = read_json(ROOT / "repro/protocol.json")
            check_protocol(protocol)
            if not args.execute:
                print(json.dumps({"status": "BLOCKED_P2_AUTHORIZATION", "protocol_status": protocol["status"],
                                  "reason": "P0 适配器已准备；P2 未授权，未导入模型依赖",
                                  "model_inference": "NOT_RUN"}, ensure_ascii=False, indent=2))
                return 3
            require_p2_authorization(args.authorization)
            if args.data_root is None or args.out is None:
                raise ValueError("真实推理必须提供 --data-root 和 --out")
            lock = read_json(ROOT / "repro/sources.lock.json")
            if any(not model.get("resolved_commit") for model in lock["teacher_models"].values()):
                raise ValueError("ASR teacher 不可变版本尚未冻结；须先完成 P1")
            # Core weights are loaded by upstream from this fixed relative directory.
            if Path.cwd().resolve() != ROOT:
                raise ValueError("真实推理须从项目根目录执行，避免相对模型路径漂移")
            local_check(args.data_root, ROOT / "pretrained_models")
            rows = read_json(ROOT / "repro/dataset_manifest.json")
            if args.limit_per_subset is not None:
                if args.limit_per_subset < 1:
                    raise ValueError("smoke 样本数必须为正")
                rows = [row for group in EXPECTED for row in
                        sorted((r for r in rows if (r["language"], r["gold_label"]) == group),
                               key=lambda r: r["sample_id"])[:args.limit_per_subset]]
            run_samples(rows, args.data_root, args.out, protocol, model_factory,
                        load_audio, seed_runtime, source_fingerprint(), resume=args.resume)
            print(json.dumps({"status": "INFERENCE_COMPLETE", "samples": len(rows),
                              "out": str(args.out.resolve()), "next": "evaluate --run-dir 此目录"}, ensure_ascii=False))
        else:
            if args.synthetic_test:
                if args.manifest is None or args.predictions is None or args.run_dir is not None:
                    raise ValueError("模拟评分须提供 --manifest/--predictions，不提供 --run-dir")
                manifest = read_json(args.manifest)
                with args.predictions.open(encoding="utf-8", newline="") as stream:
                    predictions = list(csv.DictReader(stream))
                metrics, records = score(manifest, predictions)
                provenance = {"result_kind": "SYNTHETIC_TEST", "paper_comparable": False,
                              "manifest_sha256": digest(args.manifest),
                              "predictions_sha256": digest(args.predictions)}
            else:
                if args.run_dir is None or args.manifest is not None or args.predictions is not None:
                    raise ValueError("真实评分仅接受 --run-dir 的可核验模型轨迹，不接收手工 CSV")
                from repro.runner import check_protocol, source_fingerprint, verify_run_predictions
                protocol = read_json(ROOT / "repro/protocol.json")
                check_protocol(protocol)
                full_manifest = read_json(ROOT / "repro/dataset_manifest.json")
                validate_manifest(full_manifest, EXPECTED)
                manifest, predictions, run = verify_run_predictions(
                    args.run_dir, full_manifest, protocol, source_fingerprint())
                is_full = len(manifest) == len(full_manifest)
                metrics, records = score(manifest, predictions, EXPECTED if is_full else None)
                provenance = {"result_kind": "RECONSTRUCTED_PROTOCOL", "paper_comparable": False,
                              "strict_protocol_match": False, "full_dataset": is_full,
                              "run_fingerprint": run["fingerprint"],
                              "evaluation_scope": "FULL" if is_full else "SMOKE_ONLY"}
            # Refuse overwrite so a rerun cannot silently replace a reviewed output.
            args.out.mkdir(parents=True, exist_ok=False)
            dump(args.out / "metrics.json", {**provenance, "metrics": metrics})
            fields = [key for key in predictions[0] if key != "correct"] + ["correct"]
            for filename, rows in (("predictions.csv", records),
                                   ("failures.csv", [r for r in records if not r["correct"]])):
                with (args.out / filename).open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
            print(json.dumps({**provenance, "output": str(args.out.resolve())}, ensure_ascii=False))
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
