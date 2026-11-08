"""从已冻结的元数据生成清单，并仅获取标签文本与每类一个真实样本。"""
import concurrent.futures
import hashlib
import io
import json
from pathlib import Path
import struct
import urllib.request
import wave
import zlib

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
EXPECTED = {("en", "complete"): 318, ("en", "incomplete"): 299,
            ("zh", "complete"): 300, ("zh", "incomplete"): 300}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def range_bytes(url, start, end, size):
    # Distinct query avoids CDN cache collisions between different byte ranges.
    request = urllib.request.Request(
        url + f"?p0range={start}-{end}", headers={"Range": f"bytes={start}-{end}"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 206 or response.headers.get("Content-Range") != (
            f"bytes {start}-{end}/{size}"
        ):
            raise ValueError("服务器未返回所请求字节范围；拒绝完整下载")
        data = response.read(end - start + 2)
    if len(data) != end - start + 1:
        raise ValueError("范围长度不匹配")
    return data


def zip_member(entry, url, archive_size):
    start = entry["header_offset"]
    header = range_bytes(url, start, start + 29, archive_size)
    fields = struct.unpack("<4s5H3L2H", header)
    if fields[0] != b"PK\x03\x04" or fields[2] & 1:
        raise ValueError("不支持的 ZIP 本地头或加密条目")
    data_start = start + 30 + fields[-2] + fields[-1]
    data = range_bytes(url, data_start,
                       data_start + entry["compressed_bytes"] - 1, archive_size)
    if fields[3] == 8:
        data = zlib.decompress(data, -15)
    elif fields[3] != 0:
        raise ValueError("不支持的 ZIP 压缩算法")
    if len(data) != entry["bytes"] or f"{zlib.crc32(data):08x}" != entry["crc32"]:
        raise ValueError("ZIP 条目长度/CRC 不匹配")
    return data


def build_catalog():
    rows = []
    index = read_json(EVIDENCE / "en_zip_index.json")
    for entry in index["entries"]:
        path = entry["path"]
        parts = path.split("/")
        if len(parts) == 3 and parts[1] in {"complete", "incomplete"} and path.endswith(".wav"):
            rows.append({"sample_id": "en/" + "/".join(parts[1:]), "language": "en",
                         "gold_label": parts[1], "relative_path": path,
                         "bytes": entry["bytes"], "crc32": entry["crc32"],
                         "revision": index["revision"], "sha256": None})
    zh = read_json(EVIDENCE / "zh_info.json")
    for entry in zh["siblings"]:
        path = entry["rfilename"]
        parts = path.split("/")
        if len(parts) == 4 and parts[1] in {"complete", "incomplete"} and path.endswith(".wav"):
            rows.append({"sample_id": "zh/" + "/".join(parts[1:]), "language": "zh",
                         "gold_label": parts[1], "relative_path": path,
                         "bytes": entry["size"], "git_blob_id": entry.get("blobId"),
                         "sha256": entry.get("lfs", {}).get("sha256"), "revision": zh["sha"]})
    rows.sort(key=lambda r: r["sample_id"])
    for group, expected in EXPECTED.items():
        count = sum((r["language"], r["gold_label"]) == group for r in rows)
        if count != expected:
            raise ValueError(f"{group}: {count} != {expected}")
    if len({r["sample_id"] for r in rows}) != len(rows):
        raise ValueError("重复 sample_id")
    dump(ROOT / "repro" / "dataset_manifest.json", rows)
    models = read_json(EVIDENCE / "model_info.json")
    en = read_json(EVIDENCE / "en_info.json")
    model_files = [{"path": f["rfilename"], "bytes": f["size"],
                    "sha256": f.get("lfs", {}).get("sha256"), "git_blob_id": f.get("blobId")}
                   for f in models["siblings"]]
    dump(ROOT / "repro" / "sources.lock.json", {
        "code": {"repository": "Soul-AILab/SoulX-Duplug",
                 "commit": "a0b9063843df69619b087b95b74597b2176910b8"},
        "model": {"repository": models["id"], "revision": models["sha"], "files": model_files},
        "en": {"repository": en["id"], "revision": en["sha"],
               "archive": next(f for f in en["siblings"] if f["rfilename"] == "Easy-Turn-Testset-en.zip")},
        "zh": {"repository": zh["id"], "revision": zh["sha"]},
        "teacher_models": {"zh": {"id": "iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
                                   "requested_revision": "v2.0.4", "resolved_commit": None},
                           "en": {"id": "iic/SenseVoiceSmall", "resolved_commit": None}},
        "teacher_status": "P1 前必须解析不可变版本；官方代码存在 fallback/自动下载，当前未宣称已冻结",
    })
    return rows, index


def fetch_small_samples(rows, index):
    samples = EVIDENCE / "samples"
    samples.mkdir(exist_ok=True)
    archive_url = ("https://huggingface.co/datasets/Soul-AILab/SoulX-Duplug-Eval/resolve/"
                   + index["revision"] + "/Easy-Turn-Testset-en.zip")
    jobs = []
    for label in ("complete", "incomplete"):
        entries = index["entries"]
        meta = next(x for x in entries if x["path"] == f"Easy-Turn-Testset-en/{label}.jsonl")
        audio = min((x for x in entries if x["path"].startswith(f"Easy-Turn-Testset-en/{label}/")
                     and x["path"].endswith(".wav")), key=lambda x: x["path"])
        jobs += [("en_" + label + ".jsonl", meta, archive_url),
                 ("en_" + label + ".wav", audio, archive_url)]
    def task(job):
        name, entry, url = job
        data = zip_member(entry, url, index["archive_bytes"])
        (samples / name).write_bytes(data)
        return {"file": name, "source_path": entry["path"], "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "zip_crc_verified": True}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        downloaded = list(pool.map(task, jobs))
    for label in ("complete", "incomplete"):
        row = next(r for r in rows if r["language"] == "zh" and r["gold_label"] == label)
        listing = [f["rfilename"] for f in read_json(EVIDENCE / "zh_info.json")["siblings"]
                   if f["rfilename"].startswith(f"testset/{label}/")
                   and f["rfilename"].endswith(".list")]
        if len(listing) != 1:
            raise ValueError(f"中文标签清单不唯一: {label}: {listing}")
        for name, path in [("zh_" + label + ".wav", row["relative_path"]),
                           ("zh_" + label + ".list", listing[0])]:
            url = ("https://huggingface.co/datasets/ASLP-lab/Easy-Turn-Testset/resolve/"
                   + row["revision"] + "/" + path)
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise ValueError("样本超出 P0 单文件 2MB 上限")
            sha = hashlib.sha256(data).hexdigest()
            if name.endswith(".wav") and (len(data) != row["bytes"] or (
                row["sha256"] and sha != row["sha256"]
            )):
                raise ValueError("中文样本长度/hash 校验失败")
            if name.endswith(".wav") and not row["sha256"]:
                git_blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                if git_blob != row["git_blob_id"]:
                    raise ValueError("中文 Git blob 校验失败")
            (samples / name).write_bytes(data)
            downloaded.append({"file": name, "source_path": path, "bytes": len(data), "sha256": sha})
    for item in downloaded:
        if not item["file"].endswith(".wav"):
            continue
        with wave.open(io.BytesIO((samples / item["file"]).read_bytes()), "rb") as wav:
            item["audio"] = {"sample_rate": wav.getframerate(), "channels": wav.getnchannels(),
                             "sample_width": wav.getsampwidth(), "frames": wav.getnframes(),
                             "duration_seconds": wav.getnframes() / wav.getframerate()}
    dump(EVIDENCE / "small_sample_audit.json", downloaded)
    print("样本与标签下载/校验完成：", len(downloaded))


if __name__ == "__main__":
    rows, index = build_catalog()
    print("冻结 manifest：", len(rows), "条；总字节：", sum(r["bytes"] for r in rows))
    fetch_small_samples(rows, index)
