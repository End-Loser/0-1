"""Source-bound Writer permissions and conservative offline prose diagnostics."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

PHASES = {"清晨": 0, "早晨": 0, "上午": 1, "午前": 1, "中午": 2, "正午": 2,
          "午后": 3, "下午": 3, "黄昏": 4, "傍晚": 4, "夜晚": 5, "夜间": 5, "深夜": 6, "午夜": 6}
ANCHOR = re.compile(
    r"(?:^|(?<=[\n。！？，,]))[ \t]*(?:到了|时至|此时(?:已是)?|现在(?:已是)?|已是)?"
    r"(?P<phase>清晨|早晨|上午|午前|中午|正午|午后|下午|黄昏|傍晚|夜晚|夜间|深夜|午夜)"
    r"(?=时|的|[，,。；\s])")
QUOTE = re.compile(r"“[^”]*”|「[^」]*」|『[^』]*』|\"[^\"\n]*\"", re.S)
FACT_PATTERNS = [
    ("RECORDING_NEW_TIMING", r"[一二三四五六七八九十\d]+息(?:之后|以后|前|后)",
     "录音内具体声音位置须有批准事件来源，不能由十二息总时长推得。"),
    ("SPEAKER_RECOGNITION", r"那声音他认得|(?:认出|认得|辨认出)了?[^。！？\n]{0,12}(?:说话者|声音)",
     "辨认背景声不等于获知说话者身份；须核对获知事件。"),
    ("LEDGER_NEW_HISTORY", r"(?:底账|封存记录|这一页)[^。！？\n]{0,100}(?:签押|签记|隔门应|没有列?见证人|另一名见证人|第[一二三四五六七八九十\d]+页)|承认[^。！？\n]{0,50}(?:口述交接|未见签押)",
     "未批准的底账内容、签记和见证历史不能成为新证据。"),
    ("PROCEDURE_NEW_RULE", r"(?:本院|封证院|复核)[^。！？\n]{0,40}(?:程序|规程|制度)[^。！？\n]{0,50}(?:只写|规定|必须|不得)",
     "未决复核程序不能在正文中补成稳定制度事实。"),
]


def payload(value):
    return value.get("content", value.get("payload", value))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_rhythms(work, chapters):
    rows = []
    for chapter in chapters:
        for item in chapter["dependencies"]["items"]:
            if item["source_id"].startswith("P08-CH"):
                path = work / chapter["source_files"][item["source_id"]]
                if sha(path) != item["sha256"]:
                    raise ValueError("RHYTHM_SOURCE_HASH_CHANGED")
                rows.append({"chapter": chapter["chapter"], "source_ref": item["source_id"],
                             "sha256": item["sha256"],
                             "plan": payload(json.loads(path.read_text(encoding="utf-8")))})
    return rows


def rhythm_findings(rhythms):
    findings, previous = [], None
    phases = "|".join(PHASES)
    for row in sorted(rhythms, key=lambda item: item["chapter"]):
        anchor = row["plan"].get("target_anchor", "")
        structured = isinstance(anchor, dict) and ("start_phase" in anchor or "end_phase" in anchor)
        start, end = None, None
        if structured:
            values = [anchor.get("start_phase"), anchor.get("end_phase")]
            if any(value is not None and (not isinstance(value, str) or value not in PHASES)
                   for value in values):
                findings.append({"code": "INVALID_STRUCTURED_TIME_PHASE", "decision": "BLOCKED",
                                 "chapter": row["chapter"], "source_ref": row["source_ref"],
                                 "sha256": row["sha256"], "evidence": anchor})
                continue
            start, end = [PHASES[value] if value is not None else None for value in values]
        if isinstance(anchor, dict):
            anchor = anchor.get("description", "")
        start_match = re.search(rf"^(?:第1天)?({phases})|从[^。]{{0,12}}?({phases})[^。]{{0,4}}起", anchor)
        first_clause = re.split(r"[，,。]", anchor, maxsplit=1)[0]
        legacy_start = (PHASES[next(part for part in start_match.groups() if part)]
                 if start_match and (anchor.startswith("从") or
                                     not re.search(r"本章末|章内终点|落点", first_clause)) else None)
        end_match = re.search(rf"(?:本章末|章内终点|落点|落在)[^。；]{{0,30}}?({phases})|"
                              rf"({phases})[^，,。]{{0,15}}(?:本章末|章内终点|落点)", anchor)
        if not structured:
            start = legacy_start
            end = PHASES[next(part for part in end_match.groups() if part)] if end_match else None
        if start is not None and end is not None and start > end:
            findings.append({"code": "FROZEN_TIME_WITHIN_CHAPTER_CONFLICT", "decision": "BLOCKED",
                             "chapter": row["chapter"], "source_ref": row["source_ref"],
                             "sha256": row["sha256"], "evidence": anchor})
        first_observed = start if start is not None else end if structured else None
        if previous and first_observed is not None and first_observed < previous["end_rank"]:
            findings.append({"code": "FROZEN_TIME_INTERFACE_CONFLICT", "decision": "BLOCKED",
                             "chapter": row["chapter"], "source_ref": row["source_ref"],
                             "sha256": row["sha256"], "evidence": anchor, "previous": previous,
                             "requires": "Revise and reapprove timing artifacts; do not rewrite frozen history."})
        carried = end if end is not None else start if structured else None
        if carried is not None and (previous is None or carried >= previous["end_rank"]):
            previous = {"chapter": row["chapter"], "source_ref": row["source_ref"],
                        "sha256": row["sha256"], "end_rank": carried, "evidence": anchor,
                        "anchor_kind": "END" if end is not None else "START"}
    return findings


def make_contract(clock, facts, chapters, sources, rhythms=None):
    numbers = [row["chapter"] for row in chapters]
    if len(numbers) != len(set(numbers)):
        raise ValueError("DUPLICATE_CHAPTER_BOUNDARY")
    same_day = any(word in clock for word in ("同一日", "同一天"))
    bounded = same_day and "清晨" in clock and "黄昏" in clock
    return {
        "version": "writer-boundaries-v1", "source_bindings": sources,
        "temporal": {
            "source_clock": clock, "same_day": same_day,
            "global_phase_bounds": [0, 4] if bounded else None,
            "chapter_order": sorted(numbers),
            "interfaces": [{"chapter": row["chapter"],
                            "first_event_ref": row["events"][0]["id"],
                            "last_event_ref": row["events"][-1]["id"]}
                           for row in sorted(chapters, key=lambda row: row["chapter"])],
            "exact_chapter_times": "UNSPECIFIED_NOT_HOST_INVENTED",
            "approved_rhythm_sources": [{key: row[key] for key in ("chapter", "source_ref", "sha256", "plan")}
                                       for row in rhythms or []],
            "design_findings": rhythm_findings(rhythms or []) if same_day else [],
            "instruction": "主线按冻结事件的章节顺序推进；同日时间不得回退。"
                           "未给出本章精确时段时勿自行加入强时间锚点。"
                           "回忆、假设和角色话语须可识别；不读取相邻Writer正文来补时间。",
        },
        "facts": {
            "known_fact_ids": [row["id"] for row in facts.get("facts", [])],
            "unresolved_fact_ids": [row["id"] for row in facts.get("unknowns", [])],
            "sources_in_pack": ["WorldConstants", "approved_events", "state_at_start",
                                "complete_frozen_base_lines", "explicit_conditional_constraints"],
            "instruction": "以上事实与本章批准事件是事实权限来源，未来proposed节点不视为已发生。"
                           "未知事项只有本章获批事件明确揭示后才能确定，不从缺字段推导世界不存在该事实。",
            "free_expression": "可写不改变承重事实的感知、情绪、比喻、动作质感。"
                               "角色猜测、质疑、谎言可以出现，但不能升级为叙述者确认的真相。",
            "source_required": "新增录音内容/精确声音位置、底账历史、复核制度、说话者身份、"
                               "定罪结果或机制能力须有获批来源；发现设计缺口交回设计，不能让Writer补定。",
            "unknown_resolution": "UNKNOWN_UNLESS_CURRENT_APPROVED_EVENT_RESOLVES",
        },
        "diagnostic_limits": "时间检查只覆盖明确当前时段；事实模式只产生待语义审读线索，"
                             "不是全量事实抽取，空结果不证明正文忠实。",
    }


def current_anchors(text):
    masked = QUOTE.sub(lambda match: " " * len(match.group()), text)
    result = []
    for match in ANCHOR.finditer(masked):
        prefix = re.split(r"[。！？\n]", masked[:match.start()])[-1]
        if re.search(r"回忆|想起|记得|曾经|梦里|昨日|昨夜|明天|计划|如果|假如|倘若", prefix):
            continue
        end = re.search(r"[。！？\n]", masked[match.end():])
        clause = masked[match.end():match.end() + end.start()] if end else masked[match.end():]
        if re.search(r"如果|假如|倘若|将会|明天|等到|待到", clause):
            continue
        phase = match.group("phase")
        result.append({"phase": phase, "rank": PHASES[phase],
                       "start": match.start("phase"), "end": match.end("phase")})
    return result


def current_day_shifts(text):
    masked = QUOTE.sub(lambda match: " " * len(match.group()), text)
    pattern = r"(?:^|(?<=[\n。！？]))[ \t]*(?P<shift>次日|翌日|第二天|隔日)(?=清晨|早晨|上午|午后|黄昏|[，,])"
    return [{"evidence": match.group("shift"), "start": match.start("shift"), "end": match.end("shift")}
            for match in re.finditer(pattern, masked)]


def fact_candidates(text):
    result = []
    quoted = [(match.start(), match.end()) for match in QUOTE.finditer(text)]
    for code, pattern, reason in FACT_PATTERNS:
        for match in re.finditer(pattern, text):
            claim = match.group()
            if re.search(r"如果|假如|也许|可能|猜测|不知|未明|是否|有没有|会不会|能否", claim):
                continue
            sentence_start = max(text.rfind(stop, 0, match.start()) for stop in "。！？\n") + 1
            sentence_end = re.search(r"[。！？\n]", text[match.end():])
            end = match.end() + sentence_end.start() if sentence_end else len(text)
            context = text[sentence_start:end]
            if text[end:end + 1] == "？":
                continue
            if code == "RECORDING_NEW_TIMING" and not re.search(r"录音|背景|重播|留声|回放", context):
                continue
            if code == "SPEAKER_RECOGNITION" and re.search(r"背景|磨刀|水声|脚步", context):
                continue
            dialogue = any(start <= match.start() < end for start, end in quoted)
            result.append({"code": code, "decision": "REVIEW_REQUIRED",
                           "scope": "CHARACTER_CLAIM" if dialogue else "NARRATOR_OR_CONTEXT",
                           "start": match.start(), "end": match.end(), "evidence": claim,
                           "reason": reason, "automatic_fact_violation": False})
    return result


def audit(chapters, contract):
    order = contract["temporal"]["chapter_order"]
    by_chapter = {row["chapter"]: row["text"] for row in chapters}
    if len(by_chapter) != len(chapters) or set(by_chapter) != set(order):
        raise ValueError("BOUNDARY_CHAPTER_SET_MISMATCH")
    findings, rows, previous = list(contract["temporal"]["design_findings"]), [], None
    for number in order:
        text = by_chapter[number]
        anchors = current_anchors(text)
        bounds = contract["temporal"]["global_phase_bounds"]
        if contract["temporal"]["same_day"]:
            findings.extend({"code": "SAME_DAY_BOUNDARY", "decision": "BLOCKED",
                             "chapter": number, **shift} for shift in current_day_shifts(text))
            for anchor in anchors:
                if bounds and not bounds[0] <= anchor["rank"] <= bounds[1]:
                    findings.append({"code": "GLOBAL_TIME_WINDOW", "decision": "BLOCKED",
                                     "chapter": number, "anchor": anchor})
                if previous and anchor["rank"] < previous["anchor"]["rank"]:
                    findings.append({"code": "MAINLINE_TIME_REGRESSION", "decision": "BLOCKED",
                                     "chapter": number, "anchor": anchor,
                                     "previous": previous,
                                     "scope": "Explicit mainline anchors; verify any approved flashback before repair."})
                else:
                    previous = {"chapter": number, "anchor": anchor}
        candidates = fact_candidates(text)
        findings.extend({"chapter": number, **candidate} for candidate in candidates)
        rows.append({"chapter": number, "anchors": anchors,
                     "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                     "time_anchor_status": "EXPLICIT" if anchors else "UNSPECIFIED",
                     "fact_candidate_count": len(candidates)})
    return {"version": contract["version"],
            "decision": "BLOCKED" if any(row["decision"] == "BLOCKED" for row in findings)
            else "REVIEW_REQUIRED" if findings else "NO_DETECTED_FINDINGS_NOT_SEMANTIC_PASS",
            "source_bindings": contract["source_bindings"], "chapters": rows, "findings": findings,
            "limits": contract["diagnostic_limits"],
            "writer_neighbor_text_consumed": False, "post_generation_all_chapter_audit": True}


def audit_run(work):
    setup = json.loads((work / "setup.json").read_text(encoding="utf-8"))
    plans_path, writers_path = work / "chapters-r2.json", work / "writers-r2.json"
    plans = json.loads(plans_path.read_text(encoding="utf-8"))["chapters"]
    generated = json.loads(writers_path.read_text(encoding="utf-8"))["writers"]
    sources = [{"path": path.name, "sha256": sha(path)}
               for path in (work / "setup.json", plans_path, writers_path)]
    contract = make_contract(setup["seed"]["clock"], payload(setup["facts"]), plans, sources,
                             load_rhythms(work, plans))
    rows = [{"chapter": row["chapter"], "text": payload(row["content"])["text"]} for row in generated]
    return {"mode": "OFFLINE_NO_API", "contract": contract, "audit": audit(rows, contract),
            "source_artifacts_modified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = audit_run(args.work_dir)
    with args.out.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"decision": report["audit"]["decision"],
                      "findings": len(report["audit"]["findings"]), "mode": report["mode"]}))


if __name__ == "__main__":
    main()
