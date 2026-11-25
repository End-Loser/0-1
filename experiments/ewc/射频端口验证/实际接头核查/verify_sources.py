"""Offline evidence verification, not a geometry or field solver."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent.parent
ANCHOR_MANIFEST = PROJECT / "原始资料" / "manifest.json"
EXPECTED = {
    "S1": ("132139", "https://www.amphenolrf.com/132139.html"),
    "S2": ("901-9211", "https://www.amphenolrf.com/en-us/part/901-9211/4281/"),
    "S3": ("132139", "https://www.amphenolrf.com/en-us/assets/file/4065786252/"),
    "S4": ("901-9211", "https://www.amphenolrf.com/en-us/assets/file/4065788912/"),
    "S5": ("901-9211", "https://www.amphenolrf.com/en-us/assets/file/4068628677/"),
}
ITEMS = {"内导体", "外导体", "介质支撑", "镀层和材料参数",
         "安装尺寸", "公差", "参考面", "频响"}


def ensure(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_file(record):
    path = (ROOT / record["file"]).resolve()
    ensure(path.parent == ROOT, "证据路径越出指定目录")
    ensure(path.is_file(), "证据文件缺失")
    ensure(digest(path) == record["sha256"], "归档证据哈希不符")
    return path


def registered_digests():
    """Digests the anchor manifest already records as unrecoverable."""
    if not ANCHOR_MANIFEST.is_file():
        return set()
    data = json.loads(ANCHOR_MANIFEST.read_text(encoding="utf-8"))
    return {row["sha256"] for row in data.get("unrecoverable", []) if row.get("sha256")}


def verify(manifest, require_temp=False):
    ensure(manifest["schema"] == 1, "schema不符")
    ensure(manifest["source_limit"] == 5 and manifest["candidate_limit"] == 2,
           "来源或候选上限被改变")
    ensure(manifest["source_budget_closed"] is True, "来源预算未关闭")
    ensure(manifest["complete_physical_port_pass"] is False and
           manifest["fixture_created"] is False and
           manifest["geometry_parameter_fixture"] is None, "错误宣称物理模型通过")
    for key in ("field_runs", "s_parameter_runs", "successful_original_files"):
        ensure(type(manifest[key]) is int and manifest[key] == 0, "虚报原件或求解")
    for key in ("ideal_air_coax_substitution", "tools_downloaded", "purchases",
                "vendor_request_sent", "band_is_frozen"):
        ensure(manifest[key] is False, "执行边界不符")
    collection = json.loads(local_file(manifest["collection"]).read_text(encoding="utf-8"))
    local_file(manifest["report"])
    local_file(manifest["collector"])
    sources = collection["sources"]
    ensure(len(sources) == 5 and {s["id"] for s in sources} == set(EXPECTED),
           "必须保留五个固定来源及失败记录")
    temp_checked = 0
    temp_missing = []
    registered_missing = []
    registered = registered_digests()
    for source in sources:
        ensure((source["part"], source["url"]) == EXPECTED[source["id"]],
               "来源不是固定厂家资源")
        ensure(source["acquired"] is False and source["status_code"] == 403,
               "错误将失败响应标为原件")
        ensure(source["content_type"] == "text/html", "失败响应类型不符")
        raw = Path(source["raw_path"]).resolve()
        ensure(raw.is_relative_to(Path(os.environ["TEMP"]).resolve()),
               "原始响应不在TEMP")
        if raw.is_file():
            ensure(digest(raw) == source["raw_sha256"], "TEMP响应哈希不符")
            ensure(raw.stat().st_size == source["raw_bytes"], "TEMP响应长度不符")
            temp_checked += 1
        elif source["raw_sha256"] in registered:
            registered_missing.append(source["id"])
        else:
            temp_missing.append(source["id"])
    ensure(not require_temp or not temp_missing,
           "严格复核缺少TEMP响应（存在未登记为不可复原的缺失）")
    evidence = manifest["evidence"]
    ensure(len(evidence) == 5 and {e["source"] for e in evidence} == set(EXPECTED),
           "文本来源覆盖不全")
    texts = {}
    for record in evidence:
        text = local_file(record).read_text(encoding="utf-8")
        texts[record["source"]] = text
        ensure(EXPECTED[record["source"]][1] in text, "文本缺少对应URL")
        ensure(bool(record["provenance"]), "文本缺少抽取来源性质")
    ensure("12.4" in texts["S1"] and "PTFE" in texts["S1"], "S1规格锚点缺失")
    ensure("Brass" in texts["S2"] and "18" in texts["S2"], "S2规格锚点缺失")
    ensure("empty content" in texts["S3"], "S3空内容证据缺失")
    ensure("STAINLESS STEEL" in texts["S4"] and
           "BERYLLIUM COPPER" in texts["S4"] and
           "INTERNAL USE ONLY" in texts["S4"], "S4材料冲突或内部页限制缺失")
    ensure("Login" in texts["S5"], "STEP登录阻塞证据缺失")
    candidates = manifest["candidates"]
    ensure(len(candidates) == 2 and
           {c["part"] for c in candidates} == {"132139", "901-9211"},
           "候选集合不符")
    ensure(set(manifest["required_items"]) == ITEMS, "必要项目缺失")
    for candidate in candidates:
        ensure(candidate["manufacturer"] == "Amphenol RF", "厂家不符")
        ensure(candidate["fixture_allowed"] is False, "缺失资料却准入fixture")
        rows = candidate["full_wave_requirements"]
        ensure(len(rows) == 8 and {r["item"] for r in rows} == ITEMS, "逐项检查不全")
        ensure(all(r["status"] == "缺失" and r["missing"] for r in rows),
               "将部分信息冒充完整全波输入")
        expected_refs = {sid for sid, pair in EXPECTED.items()
                         if pair[0] == candidate["part"]}
        ensure(set(candidate["sources"]) == expected_refs, "候选证据归属错误")
    conflicts = next(c for c in candidates if c["part"] == "901-9211")["conflicts"]
    ensure(len(conflicts) == 3 and all(c["resolved"] is False for c in conflicts),
           "冲突被删除或未经证据宣称解决")
    baseline = collection["baseline"]
    ensure(len(baseline) > 300, "旧证据基线覆盖不足")
    immutable_checked = 0
    context_documents = []
    for record in baseline:
        path = (PROJECT / record["path"]).resolve()
        ensure(path.is_relative_to(PROJECT) and not path.is_relative_to(ROOT),
               "旧证据基线路径不符")
        if record["path"] == "EWC真实材料与器件统一规划.md":
            ensure(path.is_file(), "主规划背景文件缺失")
            context_documents.append({"path": record["path"],
                                      "captured_sha256": record["sha256"],
                                      "current_sha256": digest(path),
                                      "role": "versioned planning context, not measurement evidence"})
            continue
        ensure(path.is_file() and digest(path) == record["sha256"],
               "旧射频证据或主规划已变化：" + record["path"])
        immutable_checked += 1
    extra_models = [p.name for p in ROOT.iterdir()
                    if p.suffix.lower() in {".step", ".stp", ".pdf", ".npz", ".s2p"}]
    ensure(not extra_models, "工作区出现未声明原件或场模型")
    return {"status": "通过：证据一致性，非物理验收",
            "official_resources_checked": 5, "candidates_checked": 2,
            "full_wave_missing_items": 16, "baseline_files_checked": immutable_checked,
            "context_documents": context_documents,
            "temp_response_hashes_checked": temp_checked,
            "temp_responses_absent": temp_missing,
            "temp_responses_registered_unrecoverable": registered_missing,
            "environment_missing": bool(registered_missing),
            "successful_original_files": 0, "field_runs": 0,
            "complete_physical_port_pass": False,
            "fixture_created": False, "require_temp": require_temp}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-temp", action="store_true")
    args = parser.parse_args()
    manifest_path = ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result = verify(manifest, args.require_temp)
    result.update(verified_utc=datetime.now(timezone.utc).isoformat(),
                  manifest_sha256=digest(manifest_path),
                  verifier_sha256=digest(Path(__file__)),
                  offline=True)
    (ROOT / "verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
