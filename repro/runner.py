"""已批准重建协议的非侵入式适配与样本级恢复；模块导入不依赖 CUDA。"""
import hashlib
import json
import os
from pathlib import Path
import time

from repro.catalog import ROOT, dump, read_json
from repro.cli import digest

TOKENS = {"<|user_complete|>": "complete", "<|user_incomplete|>": "incomplete"}


def protocol_hash(protocol):
    return hashlib.sha256(json.dumps(protocol, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source_fingerprint():
    paths = ["requirements.txt", "repro/sources.lock.json", "repro/dataset_manifest.json",
             "repro/runner.py", "repro/cli.py", "repro/catalog.py"]
    for directory in ("config", "model", "service", "utils"):
        paths.extend(str(p.relative_to(ROOT)) for p in (ROOT / directory).rglob("*.py"))
    paths.append("config/config.yaml")
    return protocol_hash({path: digest(ROOT / path) for path in sorted(paths)})


def verify_run_predictions(run_dir, manifest, protocol, sources):
    """评分前验证运行契约及每条轨迹；拒绝直接把手工 CSV 当真实模型结果。"""
    run_dir = Path(run_dir)
    saved = read_json(run_dir / "run.json")
    if saved["result_kind"] != "RECONSTRUCTED_PROTOCOL":
        raise ValueError("不是可用于真实评测的运行记录")
    if saved["protocol"] != protocol or saved["source_fingerprint"] != sources:
        raise ValueError("运行记录协议/源码指纹不一致")
    ids = saved["sample_ids"]
    if len(ids) != len(set(ids)):
        raise ValueError("运行样本列表重复")
    selected = [r for r in manifest if r["sample_id"] in set(ids)]
    if len(selected) != len(ids):
        raise ValueError("运行包含不在 manifest 中的样本")
    selected.sort(key=lambda r: r["sample_id"])
    fingerprint = protocol_hash({"protocol": protocol, "sources": sources,
                                 "rows": selected, "synthetic": False})
    if fingerprint != saved["fingerprint"]:
        raise ValueError("运行契约指纹不一致")
    raw = (run_dir / "records.jsonl").read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError("记录末尾不完整")
    records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    verified = verify_resume_records(records, selected, fingerprint)
    if set(verified) != set(ids):
        raise ValueError("运行结果不完整，不能评分")
    for record in records:
        path = run_dir / record["raw_state_trace"]
        if not path.is_file() or digest(path) != record["trace_sha256"]:
            raise ValueError("运行轨迹缺失/损坏")
        trace = read_json(path)
        if trace["sample_id"] != record["sample_id"] or trace["protocol_sha256"] != protocol_hash(protocol):
            raise ValueError("轨迹 ID/协议不一致")
        label = "error" if trace["error"] else first_endpoint(trace["trace"])
        if record["predicted_label"] != label:
            raise ValueError("预测类别与冻结轨迹判定规则不一致")
    return selected, records, saved


def first_endpoint(trace):
    """只读取模型修正后状态；忽略服务 speak/idle 和未出现 nonidle 的终止事件。"""
    seen_nonidle = False
    for item in trace:
        state = item.get("model_state")
        if state == "<|user_nonidle|>":
            seen_nonidle = True
        elif seen_nonidle and state in TOKENS:
            return TOKENS[state]
    return "undecided"


def chunk_plan(resampled_frames, protocol):
    if resampled_frames < 1:
        raise ValueError("空音频不能作为有效样本")
    frames = resampled_frames + protocol["tail_zero_samples"]
    padding = (-frames) % protocol["chunk_size"]
    return {"resampled_frames": resampled_frames,
            "tail_zero_samples": protocol["tail_zero_samples"],
            "final_zero_padding": padding,
            "chunks": (frames + padding) // protocol["chunk_size"]}


def check_protocol(protocol):
    if protocol.get("status") != "USER_APPROVED_P0":
        raise ValueError("主协议尚未确认")
    if protocol.get("candidate_id") != "easy-turn-first-endpoint-v1":
        raise ValueError("未知协议，不能复用此实现")
    required = {"classification": "RECONSTRUCTED_PROTOCOL", "sample_rate": 16000,
                "chunk_size": 2560, "tail_zero_samples": 32000, "per_sample_seed": 42,
                "label_rule": "first_complete_or_incomplete_after_nonidle"}
    if any(protocol.get(k) != v for k, v in required.items()):
        raise ValueError("协议参数与已实现逻辑不一致")


def verify_resume_records(records, rows, fingerprint):
    expected = {row["sample_id"]: row for row in rows}
    done = {}
    for record in records:
        sid = record["sample_id"]
        if sid in done or sid not in expected:
            raise ValueError("恢复记录有重复/未知样本")
        if record["fingerprint"] != fingerprint:
            raise ValueError("恢复记录的配置指纹不一致")
        for field in ("language", "gold_label"):
            if record[field] != expected[sid][field]:
                raise ValueError("恢复记录标签不一致")
        done[sid] = record
    return done


def run_samples(rows, data_root, output, protocol, model_factory, audio_loader, seed_fn,
                source_fingerprint, *, resume=False, synthetic=False):
    """依赖注入允许 P0 使用 mock，生产依赖仅在显式执行函数中导入。"""
    check_protocol(protocol)
    if not rows or len({r["sample_id"] for r in rows}) != len(rows):
        raise ValueError("空或重复样本清单")
    rows = sorted(rows, key=lambda r: r["sample_id"])
    fingerprint = protocol_hash({"protocol": protocol, "sources": source_fingerprint,
                                 "rows": rows, "synthetic": synthetic})
    output = Path(output)
    ledger = output / "records.jsonl"
    if output.exists():
        if not resume:
            raise ValueError("输出目录已存在；需显式 --resume")
        saved = read_json(output / "run.json")
        if saved["fingerprint"] != fingerprint:
            raise ValueError("不能以不同协议/数据/代码恢复")
        records = []
        if ledger.exists():
            raw = ledger.read_bytes()
            if raw and not raw.endswith(b"\n"):
                # Do not guess whether an interrupted final record is complete.
                raise ValueError("记录末尾不完整；保留原文件并上报，不自动截断")
            records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        completed = verify_resume_records(records, rows, fingerprint)
    else:
        output.mkdir(parents=True)
        completed = {}
        dump(output / "run.json", {"fingerprint": fingerprint, "protocol": protocol,
                                  "source_fingerprint": source_fingerprint,
                                  "result_kind": "SYNTHETIC_TEST" if synthetic else "RECONSTRUCTED_PROTOCOL",
                                  "sample_ids": [r["sample_id"] for r in rows]})
    (output / "traces").mkdir(exist_ok=True)
    adapter = None
    current_language = None
    for row in rows:
        sid = row["sample_id"]
        trace_name = hashlib.sha256(sid.encode()).hexdigest() + ".json"
        trace_path = output / "traces" / trace_name
        if sid in completed:
            saved = completed[sid]
            if not trace_path.is_file() or digest(trace_path) != saved["trace_sha256"]:
                raise ValueError("恢复样本轨迹缺失/损坏")
            continue
        # Model loading/configuration failures abort the run rather than making a class-wide error result.
        if current_language != row["language"]:
            adapter = None
            adapter = model_factory(row["language"], output)
            current_language = row["language"]
        seed_fn(protocol["per_sample_seed"])
        adapter.reset()
        trace = []
        started = time.perf_counter()
        audio_meta = None
        error = None
        try:
            path = Path(data_root) / row["language"] / row["relative_path"]
            chunks, audio_meta = audio_loader(path, protocol)
            for i, chunk in enumerate(chunks):
                result = adapter.process(chunk)
                trace.append({"chunk_index": i, "model_time_seconds": i * 0.16,
                              "audio_chunk_samples": len(chunk), **result})
            label = first_endpoint(trace)
        except Exception as exc:
            # Preserve error as an explicit row, never silently exclude it from the denominator.
            label = "error"
            error = {"type": type(exc).__name__, "message": str(exc)}
        dump(trace_path, {"sample_id": sid, "trace": trace, "audio": audio_meta,
                          "error": error, "runtime": adapter.metadata(),
                          "protocol_sha256": protocol_hash(protocol)})
        record = {"sample_id": sid, "language": row["language"], "gold_label": row["gold_label"],
                  "predicted_label": label, "correct": label == row["gold_label"],
                  "raw_state_trace": str(Path("traces") / trace_name),
                  "trace_sha256": digest(trace_path), "fingerprint": fingerprint,
                  "protocol_sha256": protocol_hash(protocol), "elapsed_seconds": time.perf_counter() - started,
                  "error": error, "result_kind": "SYNTHETIC_TEST" if synthetic else "RECONSTRUCTED_PROTOCOL"}
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        completed[sid] = record
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"sample_id": sid, "prediction": label, "error": error}, ensure_ascii=False) + "\n")
        if error:
            # Stop after persisting the affected row; diagnose before spending more GPU time.
            raise RuntimeError(f"样本 {sid} 失败，错误已保存；停止后续推理")
    return [completed[row["sample_id"]] for row in rows]


class OfficialAdapter:
    """仅包装 infer/recognize 做观测；保持返回值和服务算法原样。"""
    def __init__(self, model):
        self.model = model
        self.observed = []
        self.asr_errors = []

    def reset(self):
        self.observed = []
        self.asr_errors = []
        self.model.reset()

    def process(self, chunk):
        original = self.model.infer
        def observe(*args, **kwargs):
            result = original(*args, **kwargs)
            self.observed.append({"model_state": result[0], "asr_segment": result[1],
                                  "asr_buffer": result[2]})
            return result
        self.observed = []
        # The official ASR catches exceptions. Capture its explicit failure log without suppressing it.
        import contextlib
        import sys
        target_stdout = sys.stdout
        class Tee:
            def __init__(self): self.buffer = ""
            def write(self, text):
                self.buffer += text
                return target_stdout.write(text)
            def flush(self): target_stdout.flush()
        tee = Tee()
        self.model.infer = observe
        try:
            with contextlib.redirect_stdout(tee):
                response = self.model.process(chunk)
        finally:
            self.model.infer = original
        asr_warning = None
        if "ASR recognition failed:" in tee.buffer:
            # Official ASR catches the exception and returns an empty transcription.
            # Preserve that upstream behavior; record the warning without turning it
            # into a synthetic sample-level failure or changing state prediction.
            asr_warning = "official ASR recognition failed; official fallback returned empty text"
        if len(self.observed) > 1:
            raise ValueError("每个输入块出现多次 infer，需重新审计观测接口")
        return {**(self.observed[0] if self.observed else {"model_state": None}),
                "argmax_state": None, "service_state": response,
                "argmax_note": "未插桩 logits；model_state 为官方修正后状态",
                "asr_warning": asr_warning}

    def metadata(self):
        import torch
        from collections import Counter
        return {"parameter_dtypes": dict(Counter(str(p.dtype) for p in self.model.model.parameters())),
                "gpu": torch.cuda.get_device_name(), "torch": torch.__version__,
                "cuda": torch.version.cuda, "max_memory_allocated": torch.cuda.max_memory_allocated(),
                "config": str(self.model.config)}


def load_audio(path, protocol):
    import numpy as np
    import soundfile as sf
    import soxr
    # Match main/test.py: soundfile's float64 decode, soxr, then float32 cast.
    wav, rate = sf.read(path, always_2d=True)
    channels = wav.shape[1]
    wav = wav.mean(axis=1)
    original_frames = len(wav)
    if rate != 16000:
        wav = soxr.resample(wav, rate, 16000)
    wav = wav.astype(np.float32)
    plan = chunk_plan(len(wav), protocol)
    wav = np.concatenate([wav, np.zeros(protocol["tail_zero_samples"], dtype=np.float32)])
    padding = plan["final_zero_padding"]
    wav = np.pad(wav, (0, padding)).astype(np.float32)
    chunks = (wav[i:i + protocol["chunk_size"]] for i in range(0, len(wav), protocol["chunk_size"]))
    return chunks, {"source_rate": rate, "channels": channels, "source_frames": original_frames,
                    **plan, "sha256": digest(path)}


def model_factory(language, output):
    import gc
    import torch
    from omegaconf import OmegaConf
    from service.model import TurnModel
    gc.collect()
    torch.cuda.empty_cache()
    config = OmegaConf.load(ROOT / "config/config.yaml")
    protocol = read_json(ROOT / "repro/protocol.json")
    for key in ("max_wait_num", "max_mistake_num", "far_field_threshold"):
        if config.infer_config[key] != protocol[key]:
            raise ValueError(f"官方配置与冻结协议不一致: {key}")
    for key in ("chunk_size", "audio_back_size", "audio_ahead_size", "sample_rate"):
        if config.infer_config.input[key] != protocol[key]:
            raise ValueError(f"官方音频配置与冻结协议不一致: {key}")
    config.infer_config.asr.model_name = "sensevoice" if language == "en" else "paraformer"
    config.infer_config.asr.language = "en" if language == "en" else "auto"
    directory = output / "configs"
    directory.mkdir(exist_ok=True)
    filename = directory / f"{language}.yaml"
    OmegaConf.save(config, filename)
    return OfficialAdapter(TurnModel(str(filename)))


def seed_runtime(seed):
    import random
    import numpy as np
    import torch
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def require_p2_authorization(path):
    # Workflow guard, not a security boundary. This file is created only after explicit future approval.
    if path is None or not Path(path).is_file():
        raise ValueError("P2 未授权；缺少明确的阶段授权记录")
    record = read_json(path)
    if record.get("scope") != "P2" or record.get("approved") is not True or not record.get("user_instruction"):
        raise ValueError("P2 授权记录无效")
    return record
