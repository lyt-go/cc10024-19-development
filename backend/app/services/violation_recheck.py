"""违规复检领域服务：首轮 Schema 违规明细 → 可查询选集 → 本轮复检记录。

核心约束：
1. 每条违规用稳定身份 violation_id（来源+行标识+字段+规则版本+字典版本）标识，
   列表筛选、排序、翻页、提交都只认这个身份，不依赖页内序号，也不会顺带带上未选站点。
2. 复检轮次保存被选中的原值、原违规规则快照和实际使用的字典版本；
   重放同一轮只读这些快照与固定版本，不受之后字典再次更换的影响。
3. 运行历史触发复检时只重算清单选集内的来源行，未入清单的记录保持原结果。
"""
from __future__ import annotations

import copy
from typing import Any

from app import persistence as persist
from app.services import region_dictionary
from app.services.base import BaseModuleService
from app.services.field_normalize import normalize_by_mapping
from app.services.schema_engine import evaluate_rule, violation_identity
from app.store import store

SOURCE_MODULE = "collection_source"
FIRST_ROUND_MODULE = "recheck_first_round"
CHECKLIST_MODULE = "recheck_checklist"
ROUND_MODULE = "recheck_round"

DICT_FIELD = region_dictionary.DEFAULT_FIELD
FIRST_DICT_VERSION = "v1"

# 首轮失败所用的规则快照。之后即使规则行被改，复检仍按这份快照判定。
# 合法值在首轮生成明细时固化进去，复检不再回读当前规则或当前字典。
# 注意：合法值集合与字典映射是两件事——旧字典可能把原码解释成已不在标准值域内的旧标签，
# 这正是首轮失败、以及换字典后同一原码得到不同合法值的原因。
STANDARD_LEGAL_VALUES = ["华东", "华北", "华南", "西南"]

_FIRST_RULE: dict[str, Any] = {
    "规则编号": "SCHE-REGION-001",
    "规则版本": "r1",
    "目标数据": SOURCE_MODULE,
    "字段名": DICT_FIELD,
    "类型约束": "",
    "格式约束": "标准字典",
    "空值策略": "禁止",
    "异常归类": "字典外码",
    "合法值": [],
    "合法值来源": f"{DICT_FIELD}标准值域",
}

_SOURCE_ROWS: list[dict[str, Any]] = [
    {
        "id": 1,
        "行标识": "J-1001",
        "来源": "甲站",
        "站点": "甲站",
        "标题": "甲站价格页第1行",
        "采集时间": "2026-11-20T09:10:00",
        "地区": "310000",
    },
    {
        "id": 2,
        "行标识": "J-1002",
        "来源": "甲站",
        "站点": "甲站",
        "标题": "甲站价格页第2行",
        "采集时间": "2026-11-20T09:11:00",
        "地区": "340000",
    },
    {
        "id": 3,
        "行标识": "J-1003",
        "来源": "甲站",
        "站点": "甲站",
        "标题": "甲站价格页第3行",
        "采集时间": "2026-11-20T09:12:00",
        "地区": "350000",
    },
    {
        "id": 4,
        "行标识": "Y-2001",
        "来源": "乙站",
        "站点": "乙站",
        "标题": "乙站列表页第1行",
        "采集时间": "2026-11-20T10:20:00",
        "地区": "310000",
    },
    {
        "id": 5,
        "行标识": "Y-2002",
        "来源": "乙站",
        "站点": "乙站",
        "标题": "乙站列表页第2行",
        "采集时间": "2026-11-20T10:21:00",
        "地区": "340000",
    },
]

_seeded = False


# --------------------------------------------------------------------------- #
# 种子与稳定身份
# --------------------------------------------------------------------------- #
def _now() -> str:
    return persist.now_iso()


def _seed_sources() -> None:
    rows = store.rows(SOURCE_MODULE)
    if rows:
        return
    for item in _SOURCE_ROWS:
        rows.append({
            **copy.deepcopy(item),
            "修复后地区": "",
            "首轮结果": "",
            "首轮违规原因": [],
            "复检结果": "",
            "复检轮次": "",
            "复检字典版本": "",
            "version": _now(),
        })


def _ensure_rule_row() -> None:
    rules = store.rows("schema_check")
    existing = next((r for r in rules if r.get("规则编号") == _FIRST_RULE["规则编号"]), None)
    payload = {
        "规则编号": _FIRST_RULE["规则编号"],
        "目标数据": _FIRST_RULE["目标数据"],
        "字段名": _FIRST_RULE["字段名"],
        "类型约束": _FIRST_RULE["类型约束"],
        "格式约束": _FIRST_RULE["格式约束"],
        "空值策略": _FIRST_RULE["空值策略"],
        "异常归类": _FIRST_RULE["异常归类"],
        "规则版本": _FIRST_RULE["规则版本"],
        "合法值": list(STANDARD_LEGAL_VALUES),
        "dict_version": FIRST_DICT_VERSION,
    }
    if existing is None:
        new_id = max((int(r.get("id", 0)) for r in rules), default=0) + 1
        rules.append({
            "id": new_id, **payload, "status": "已生效", "pending": False,
            "abnormal": False, "version": _FIRST_RULE["规则版本"], "updated_at": _now(),
        })
    else:
        existing.update(payload)


def ensure_seeded() -> None:
    global _seeded
    if _seeded:
        return
    _seeded = True
    region_dictionary.list_versions(DICT_FIELD)  # 初始化字典版本
    _seed_sources()
    _ensure_rule_row()
    ensure_first_round()
    ensure_default_checklist()


def ensure_default_checklist() -> dict[str, Any] | None:
    """预置一张“甲站两行”清单，方便打开页面直接看到可复检的选集。"""
    checklists = store.rows(CHECKLIST_MODULE)
    if checklists:
        return checklists[0]
    first = ensure_first_round()
    picked = [
        item["violation_id"] for item in first["violations"]
        if item["来源"] == "甲站"
    ]
    if not picked:
        return None
    return create_checklist("甲站两行复检", picked, remark="首轮失败结果中甲站的两行")


# --------------------------------------------------------------------------- #
# 首轮失败结果
# --------------------------------------------------------------------------- #
def _evaluate_source_row(row: dict[str, Any], mapping: dict[str, str], rule: dict[str, Any]) -> dict[str, Any]:
    raw = row.get(DICT_FIELD)
    normalized, hit, _ = normalize_by_mapping(raw, mapping)
    # 合法值域来自规则快照，不来自字典映射：旧字典把原码映射成旧标签时，
    # 归一结果仍可能落在标准值域之外，这正是首轮失败的来源。
    reasons = evaluate_rule(rule, normalized, legal_values=rule.get("合法值"))
    return {
        "归一值": normalized,
        "字典命中": hit,
        "违规原因": reasons,
        "结果": "通过" if not reasons else "仍违规",
    }


def ensure_first_round(*, force: bool = False) -> dict[str, Any]:
    """生成/读取首轮失败明细；固定使用首轮字典版本，保证口径可重放。"""
    ensure_sources = store.rows(SOURCE_MODULE)
    if not ensure_sources:
        _seed_sources()
    rows = store.rows(FIRST_ROUND_MODULE)
    if rows and not force:
        return copy.deepcopy(rows[-1])

    version = region_dictionary.get_version(DICT_FIELD, FIRST_DICT_VERSION)
    if version is None:
        raise ValueError(f"首轮字典版本 {FIRST_DICT_VERSION} 不存在")
    mapping = version["映射"]
    rule = copy.deepcopy(_FIRST_RULE)
    rule["合法值"] = list(STANDARD_LEGAL_VALUES)

    violations: list[dict[str, Any]] = []
    for row in store.rows(SOURCE_MODULE):
        outcome = _evaluate_source_row(row, mapping, rule)
        row["首轮结果"] = "违规" if outcome["违规原因"] else "通过"
        row["首轮违规原因"] = outcome["违规原因"]
        if not outcome["违规原因"]:
            continue
        vid = violation_identity(row["来源"], row["行标识"], DICT_FIELD, rule, FIRST_DICT_VERSION)
        violations.append({
            "violation_id": vid,
            "来源": row["来源"],
            "站点": row.get("站点", row["来源"]),
            "行标识": row["行标识"],
            "source_row_id": row["id"],
            "字段名": DICT_FIELD,
            "原值": row.get(DICT_FIELD),
            "归一值": outcome["归一值"],
            "原违规规则": copy.deepcopy(rule),
            "规则快照": copy.deepcopy(rule),
            "首轮字典版本": FIRST_DICT_VERSION,
            "字典版本": FIRST_DICT_VERSION,
            "违规原因": outcome["违规原因"],
            "状态": "待复检",
            "首次结果": "失败",
        })

    record = {
        "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
        "轮次编号": "",
        "名称": "首轮 Schema 失败明细",
        "规则快照": copy.deepcopy(rule),
        "字典字段": DICT_FIELD,
        "首轮字典版本": FIRST_DICT_VERSION,
        "违规数": len(violations),
        "来源集合": sorted({v["来源"] for v in violations}),
        "violations": violations,
        "创建时间": _now(),
    }
    record["轮次编号"] = f"FIR-{record['id']:04d}"
    rows.append(record)
    persist.append_jsonl("reports", "recheck_first_round.jsonl", {
        "first_round": record["轮次编号"],
        "规则编号": rule["规则编号"],
        "规则版本": rule["规则版本"],
        "字典版本": FIRST_DICT_VERSION,
        "violation_ids": [v["violation_id"] for v in violations],
        "timestamp": _now(),
    })
    return copy.deepcopy(record)


def list_violations(
    *,
    source: str | None = None,
    field: str | None = None,
    result: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    size: int = 20,
    sort: str = "来源,行标识",
) -> tuple[list[dict[str, Any]], int]:
    first = ensure_first_round()
    items = copy.deepcopy(first["violations"])
    if source:
        items = [i for i in items if i["来源"] == source]
    if field:
        items = [i for i in items if i["字段名"] == field]
    if result:
        items = [i for i in items if i["状态"] == result]
    if keyword:
        needle = keyword.casefold()
        items = [
            i for i in items
            if needle in str(i.get("行标识", "")).casefold()
            or needle in str(i.get("原值", "")).casefold()
        ]
    items = _sort_items(items, sort)
    total = len(items)
    start = (page - 1) * size
    return items[start:start + size], total


# --------------------------------------------------------------------------- #
# 清单
# --------------------------------------------------------------------------- #
def _sort_items(items: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    """按稳定字段排序；`-` 前缀表示降序，violation_id 只作为兜底次序。

    先按最不重要的 violation_id 排序，再依次按请求字段稳定排序，
    因此请求字段是主序、violation_id 只在完全并列时决定先后。
    """
    keys = [part.strip() for part in (sort or "").split(",") if part.strip()]
    ordered = list(items)
    ordered.sort(key=lambda item: str(item.get("violation_id", "")))
    for key in reversed(keys):
        descending = key.startswith("-")
        name = key[1:] if descending else key
        ordered.sort(
            key=lambda item, n=name: str(item.get(n, "")),
            reverse=descending,
        )
    return ordered


def _checklist_index(checklist: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["violation_id"]: item for item in checklist.get("选中明细", [])}


def create_checklist(
    name: str,
    violation_ids: list[str],
    *,
    remark: str = "",
) -> dict[str, Any]:
    ensure_seeded()
    first = ensure_first_round()
    index = {item["violation_id"]: item for item in first["violations"]}
    clean_ids = [str(vid) for vid in violation_ids]
    unknown = [vid for vid in clean_ids if vid not in index]
    if unknown:
        raise ValueError(f"违规身份不在首轮失败结果中：{'、'.join(unknown)}")
    if not clean_ids:
        raise ValueError("清单至少需要选择一条违规明细")
    deduped: list[str] = []
    for vid in clean_ids:
        if vid not in deduped:
            deduped.append(vid)
    selected = [copy.deepcopy(index[vid]) for vid in deduped]
    rows = store.rows(CHECKLIST_MODULE)
    new_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
    checklist = {
        "id": new_id,
        "清单编号": f"VCL-{new_id:04d}",
        "名称": name.strip() or f"违规复检清单{new_id}",
        "备注": remark,
        "首轮轮次": first["轮次编号"],
        "规则快照": copy.deepcopy(first["规则快照"]),
        "字典字段": DICT_FIELD,
        "首轮字典版本": first["首轮字典版本"],
        "选中": deduped,
        "选中明细": selected,
        "选中站点": sorted({item["来源"] for item in selected}),
        "创建时间": _now(),
    }
    rows.append(checklist)
    persist.append_jsonl("reports", "recheck_checklists.jsonl", {
        "清单编号": checklist["清单编号"],
        "首轮轮次": checklist["首轮轮次"],
        "选中": deduped,
        "选中站点": checklist["选中站点"],
        "timestamp": _now(),
    })
    return copy.deepcopy(checklist)


def get_checklist(checklist_id: int) -> dict[str, Any]:
    ensure_seeded()
    for row in store.rows(CHECKLIST_MODULE):
        if int(row.get("id", 0)) == checklist_id:
            return copy.deepcopy(row)
    raise ValueError(f"复检清单 {checklist_id} 不存在")


def list_checklists() -> list[dict[str, Any]]:
    ensure_seeded()
    return copy.deepcopy(store.rows(CHECKLIST_MODULE))


def _dict_conflict(item: dict[str, Any], current_version: str | None) -> dict[str, Any] | None:
    """判断同一原码在新旧字典下是否被解释成不同的合法值。

    这正是“提交时旧字典已经替换”的场景：首轮按旧字典归一，提交时若新字典
    对同一原码给出另一个值，就不能悄悄按新值覆盖，必须显式提示口径冲突。
    """
    if not current_version:
        return None
    first_version = item.get("首轮字典版本") or FIRST_DICT_VERSION
    if current_version == first_version:
        return None
    raw = item.get("原值")
    old = region_dictionary.get_version(DICT_FIELD, first_version) or {}
    new = region_dictionary.get_version(DICT_FIELD, current_version) or {}
    old_value = (old.get("映射", {}) or {}).get(raw)
    new_value = (new.get("映射", {}) or {}).get(raw)
    if old_value == new_value:
        return None
    return {
        "原码": raw,
        "旧字典版本": first_version,
        "旧值": old_value,
        "新字典版本": current_version,
        "新值": new_value,
    }


def _latest_execution(checklist_id: int | None, violation_id: str) -> dict[str, Any] | None:
    """取该违规在本清单内最近一次真实执行的结论（跳过纯重放记录）。"""
    latest: dict[str, Any] | None = None
    for round_record in store.rows(ROUND_MODULE):
        if round_record.get("执行方式") == "replay":
            continue
        if checklist_id is not None and round_record.get("清单id") not in (None, checklist_id):
            continue
        for item in round_record.get("选择", []):
            if item.get("violation_id") == violation_id:
                latest = {
                    "复检结果": item.get("复检结果", ""),
                    "当前值": item.get("归一值", ""),
                    "复检轮次": round_record.get("轮次编号", ""),
                    "复检字典版本": item.get("实际修复字典版本", ""),
                }
    return latest


def _enrich_checklist_item(item: dict[str, Any], checklist_id: int | None = None) -> dict[str, Any]:
    enriched = copy.deepcopy(item)
    row = None
    for candidate in store.rows(SOURCE_MODULE):
        if candidate.get("id") == item.get("source_row_id") \
                and candidate.get("来源") == item.get("来源") \
                and candidate.get("行标识") == item.get("行标识"):
            row = candidate
            break
    latest = _latest_execution(checklist_id, item["violation_id"])
    enriched["当前值"] = (latest or {}).get("当前值") or (row or {}).get("修复后地区") or ""
    enriched["复检结果"] = (latest or {}).get("复检结果") or (row or {}).get("复检结果") or "待复检"
    enriched["复检轮次"] = (latest or {}).get("复检轮次") or (row or {}).get("复检轮次") or ""
    enriched["复检字典版本"] = (latest or {}).get("复检字典版本") or (row or {}).get("复检字典版本") or ""
    enriched["来源行当前地区"] = (row or {}).get(DICT_FIELD)
    active = region_dictionary.active_version(DICT_FIELD)
    current_version = active["版本"] if active else None
    enriched["当前字典版本"] = current_version
    conflict = _dict_conflict(item, current_version)
    enriched["口径冲突"] = bool(conflict)
    enriched["字典冲突明细"] = conflict
    return enriched


def list_checklist_items(
    checklist_id: int,
    *,
    source: str | None = None,
    field: str | None = None,
    result: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    size: int = 20,
    sort: str = "来源,行标识",
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    checklist = get_checklist(checklist_id)
    items = [_enrich_checklist_item(item, checklist["id"]) for item in checklist["选中明细"]]
    if source:
        items = [i for i in items if i["来源"] == source]
    if field:
        items = [i for i in items if i["字段名"] == field]
    if result:
        items = [i for i in items if i["复检结果"] == result]
    if keyword:
        needle = keyword.casefold()
        items = [
            i for i in items
            if needle in str(i.get("行标识", "")).casefold()
            or needle in str(i.get("原值", "")).casefold()
        ]
    items = _sort_items(items, sort)
    total = len(items)
    start = (page - 1) * size
    return checklist, items[start:start + size], total


# --------------------------------------------------------------------------- #
# 复检轮次
# --------------------------------------------------------------------------- #
def _pick_items(checklist: dict[str, Any], violation_ids: list[str] | None) -> list[dict[str, Any]]:
    index = _checklist_index(checklist)
    if violation_ids is None:
        # 不传选集时沿用清单全部选中项，但绝不允许借此扩到清单外的站点。
        selected = list(checklist.get("选中", []))
    else:
        selected = [str(vid) for vid in violation_ids]
        extra = [vid for vid in selected if vid not in index]
        if extra:
            raise ValueError(
                f"提交范围超出清单选集，拒绝执行：{'、'.join(extra)}")
    if not selected:
        raise ValueError("没有选中任何违规明细")
    deduped: list[str] = []
    for vid in selected:
        if vid not in deduped:
            deduped.append(vid)
    return [copy.deepcopy(index[vid]) for vid in deduped]


def _resolve_dict_version(mode: str, checklist: dict[str, Any]) -> tuple[str, dict[str, dict[str, str]]]:
    if mode == "first":
        version = checklist.get("首轮字典版本") or FIRST_DICT_VERSION
    elif mode == "current":
        active = region_dictionary.active_version(DICT_FIELD)
        if active is None:
            raise ValueError("当前没有生效的地区字典版本")
        version = active["版本"]
    else:
        raise ValueError(f"不支持的复检口径：{mode}")
    payload = region_dictionary.get_version(DICT_FIELD, version)
    if payload is None:
        raise ValueError(f"字典版本 {version} 不存在，无法复检")
    return version, payload["映射"]


def _execute_items(
    items: list[dict[str, Any]],
    *,
    dict_version: str,
    mapping: dict[str, str],
    rule: dict[str, Any],
    mode: str,
    checklist: dict[str, Any] | None,
    run_id: int | None,
    write_back: bool = True,
) -> dict[str, Any]:
    rounds = store.rows(ROUND_MODULE)
    new_id = max((int(r.get("id", 0)) for r in rounds), default=0) + 1
    round_code = f"RCK-{new_id:04d}"

    executed: list[dict[str, Any]] = []
    passed = 0
    still = 0
    for item in items:
        raw = item.get("原值")
        normalized, hit, _ = normalize_by_mapping(raw, mapping)
        # 每条违规沿用首轮固化的规则快照（含合法值域），再按本次选定的字典版本归一。
        item_rule = copy.deepcopy(item.get("规则快照") or item.get("原违规规则") or rule)
        reasons = evaluate_rule(item_rule, normalized, legal_values=item_rule.get("合法值"))
        result = "通过" if not reasons else "仍违规"
        if result == "通过":
            passed += 1
        else:
            still += 1
        executed.append({
            "violation_id": item["violation_id"],
            "来源": item["来源"],
            "行标识": item["行标识"],
            "source_row_id": item.get("source_row_id"),
            "字段名": item.get("字段名", DICT_FIELD),
            "原值": raw,
            "原违规规则": copy.deepcopy(item.get("原违规规则") or item.get("规则快照") or rule),
            "规则快照": copy.deepcopy(item.get("规则快照") or item.get("原违规规则") or rule),
            "首轮字典版本": item.get("首轮字典版本", checklist and checklist.get("首轮字典版本")),
            "实际修复字典版本": dict_version,
            "归一值": normalized,
            "字典命中": hit,
            "复检结果": result,
            "复检违规原因": reasons,
        })

    # 回写：只写本次真正选中的来源行，且用来源+行标识+行号三重校验，避免串站。
    # 重放只核对历史，不回写，避免把后续轮次的结论覆盖到来源行上。
    if write_back:
        for result_item in executed:
            target = None
            for row in store.rows(SOURCE_MODULE):
                if row.get("id") == result_item["source_row_id"] \
                        and row.get("来源") == result_item["来源"] \
                        and row.get("行标识") == result_item["行标识"]:
                    target = row
                    break
            if target is None:
                continue
            target["修复后地区"] = result_item["归一值"]
            target["复检结果"] = result_item["复检结果"]
            target["复检轮次"] = round_code
            target["复检字典版本"] = dict_version

    first_version = (checklist or {}).get("首轮字典版本") or FIRST_DICT_VERSION
    conflicts = [
        conflict for item in executed
        if (conflict := _dict_conflict(item, dict_version)) is not None
    ]
    round_record = {
        "id": new_id,
        "轮次编号": round_code,
        "清单编号": (checklist or {}).get("清单编号", ""),
        "清单id": (checklist or {}).get("id"),
        "运行记录id": run_id,
        "执行方式": mode,
        "字典字段": DICT_FIELD,
        "首轮字典版本": first_version,
        "实际修复字典版本": dict_version,
        "规则快照": copy.deepcopy(rule),
        "选择": executed,
        "统计": {
            "行数": len(executed),
            "通过数": passed,
            "仍违规数": still,
        },
        "口径冲突": conflicts,
        "口径变化": region_dictionary.change_summary(DICT_FIELD, first_version, dict_version),
        "创建时间": _now(),
        "重放自": "",
    }
    rounds.append(round_record)
    persist.append_jsonl("reports", "recheck_rounds.jsonl", {
        "轮次编号": round_code,
        "清单编号": round_record["清单编号"],
        "运行记录id": run_id,
        "执行方式": mode,
        "首轮字典版本": first_version,
        "实际修复字典版本": dict_version,
        "统计": round_record["统计"],
        "口径冲突": round_record["口径冲突"],
        "选择": [
            {
                "violation_id": item["violation_id"],
                "来源": item["来源"],
                "行标识": item["行标识"],
                "原值": item["原值"],
                "归一值": item["归一值"],
                "复检结果": item["复检结果"],
            } for item in executed
        ],
        "timestamp": _now(),
    })
    return copy.deepcopy(round_record)


def submit_checklist(
    checklist_id: int,
    *,
    violation_ids: list[str] | None = None,
    mode: str = "current",
    remark: str = "",
    run_id: int | None = None,
) -> dict[str, Any]:
    ensure_seeded()
    checklist = get_checklist(checklist_id)
    items = _pick_items(checklist, violation_ids)
    dict_version, mapping = _resolve_dict_version(mode, checklist)
    rule = copy.deepcopy(checklist.get("规则快照") or _FIRST_RULE)
    round_record = _execute_items(
        items,
        dict_version=dict_version,
        mapping=mapping,
        rule=rule,
        mode=mode,
        checklist=checklist,
        run_id=run_id,
    )
    if remark:
        round_record["备注"] = remark
    return round_record


def get_round(round_id: int) -> dict[str, Any]:
    ensure_seeded()
    for row in store.rows(ROUND_MODULE):
        if int(row.get("id", 0)) == round_id:
            return copy.deepcopy(row)
    raise ValueError(f"复检轮次 {round_id} 不存在")


def list_rounds() -> list[dict[str, Any]]:
    ensure_seeded()
    return copy.deepcopy(store.rows(ROUND_MODULE))


def replay_round(round_id: int) -> dict[str, Any]:
    """按轮次固定的原值/规则/字典版本重放，不读取当前字典。"""
    ensure_seeded()
    original = get_round(round_id)
    dict_version = original.get("实际修复字典版本")
    payload = region_dictionary.get_version(DICT_FIELD, dict_version or "")
    if payload is None:
        raise ValueError(f"轮次记录的字典版本 {dict_version} 不存在，无法重放")
    checklist = None
    if original.get("清单id"):
        try:
            checklist = get_checklist(int(original["清单id"]))
        except ValueError:
            checklist = None
    replayed = _execute_items(
        original.get("选择", []),
        dict_version=dict_version,
        mapping=payload["映射"],
        rule=copy.deepcopy(original.get("规则快照") or _FIRST_RULE),
        mode="replay",
        checklist=checklist,
        run_id=original.get("运行记录id"),
        write_back=False,
    )
    previous = [
        (item["violation_id"], item["原值"], item["归一值"], item["复检结果"])
        for item in original.get("选择", [])
    ]
    current = [
        (item["violation_id"], item["原值"], item["归一值"], item["复检结果"])
        for item in replayed.get("选择", [])
    ]
    unchanged = previous == current and len(previous) == len(current)
    replayed["重放自"] = original.get("轮次编号", "")
    replayed["与上次一致"] = unchanged
    replayed["上一次统计"] = copy.deepcopy(original.get("统计", {}))
    # 重放记录写回内存轮次，便于“可查的修复记录”里直接看到一致性结论。
    for row in store.rows(ROUND_MODULE):
        if int(row.get("id", 0)) == int(replayed["id"]):
            row["重放自"] = replayed["重放自"]
            row["与上次一致"] = unchanged
            row["上一次统计"] = replayed["上一次统计"]
            break
    return replayed


# --------------------------------------------------------------------------- #
# 运行历史联动与总览
# --------------------------------------------------------------------------- #
def execute_for_run(
    run_id: int,
    checklist_id: int,
    *,
    violation_ids: list[str] | None = None,
    mode: str = "current",
    remark: str = "",
) -> dict[str, Any]:
    run = store.find("run_history", run_id)
    if run is None:
        raise ValueError(f"运行记录 {run_id} 不存在")
    checklist = get_checklist(checklist_id)
    round_record = submit_checklist(
        checklist_id, violation_ids=violation_ids, mode=mode, remark=remark, run_id=run_id)
    stats = round_record["统计"]
    log = (
        f"复检清单{checklist['清单编号']}执行{stats['行数']}行："
        f"通过{stats['通过数']}，仍违规{stats['仍违规数']}；"
        f"字典版本{round_record['实际修复字典版本']}（轮次{round_record['轮次编号']}）"
    )
    run["复检清单"] = checklist["清单编号"]
    run["复检轮次"] = round_record["轮次编号"]
    run["复检统计"] = stats
    run["复检字典版本"] = round_record["实际修复字典版本"]
    run["执行日志"] = f"{run.get('执行日志', '')}；{log}" if run.get("执行日志") else log
    run["异常摘要"] = (
        f"复检后仍有{stats['仍违规数']}行违规" if stats["仍违规数"] else ""
    )
    run["status"] = "部分成功" if stats["仍违规数"] else "已完成"
    run["pending"] = False
    run["abnormal"] = bool(stats["仍违规数"])
    run["updated_at"] = _now()
    persist.append_jsonl("runs", "run_history.jsonl", {
        "module": "run_history", "entity": "运行记录", "entry_id": run_id,
        "status": run.get("status"), "version": run.get("version", ""),
        "action": "auto_recheck_from_checklist", "timestamp": _now(),
        "abnormal": run.get("abnormal", False),
        "checklist": checklist["清单编号"], "round": round_record["轮次编号"],
    })
    return round_record


class ViolationRecheckService(BaseModuleService):
    MODULE = "violation_recheck"
    ENTITY = "违规复检"
    OPERATION_TERM = "复检"
    LIST_FIELDS: list[str] = []

    def state(self) -> dict[str, Any]:
        ensure_seeded()
        first = ensure_first_round()
        rounds = list_rounds()
        passed = sum(int(r.get("统计", {}).get("通过数", 0)) for r in rounds)
        still = sum(int(r.get("统计", {}).get("仍违规数", 0)) for r in rounds)
        return {
            "字典字段": DICT_FIELD,
            "首轮字典版本": first["首轮字典版本"],
            "当前字典": region_dictionary.active_version(DICT_FIELD),
            "字典版本": region_dictionary.list_versions(DICT_FIELD),
            "首轮违规数": first["违规数"],
            "首轮来源": first["来源集合"],
            "清单数": len(list_checklists()),
            "轮次数": len(rounds),
            "累计通过": passed,
            "累计仍违规": still,
        }

    def source_rows(self) -> list[dict[str, Any]]:
        ensure_seeded()
        return copy.deepcopy(store.rows(SOURCE_MODULE))

    def domain_snapshot(self) -> dict[str, Any]:
        """违规复检的运营快照，含首轮、清单、轮次与字典版本统计。"""
        base = super().domain_snapshot()
        first = ensure_first_round()
        rounds = list_rounds()
        base.update({
            "首轮违规数": first["违规数"],
            "首轮字典版本": first["首轮字典版本"],
            "清单数": len(list_checklists()),
            "轮次数": len(rounds),
            "累计通过": sum(int(r.get("统计", {}).get("通过数", 0)) for r in rounds),
            "累计仍违规": sum(int(r.get("统计", {}).get("仍违规数", 0)) for r in rounds),
        })
        return base
