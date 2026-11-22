"""地区标准字典版本库：同一原码在不同字典版本下可以有不同合法值。

首轮失败和复检都必须能说清“用的是哪个字典版本”。这里把版本化字典独立出来，
既供字段归一化读取当前生效版本，也供复检轮次固定历史版本做重放。
"""
from __future__ import annotations

import copy
import json
from typing import Any

from app.store import store
from app import persistence as persist

DEFAULT_FIELD = "地区"

# 首轮失败时记录的原始字典版本，以及随后被替换进来的新版本。
# v1/v2 对同一个原码给出不同版本的合法值；v2 还刻意漏掉 320000，
# 用来保留“复检后仍违规”的真实分支。
_SEED_VERSIONS: dict[str, list[dict[str, Any]]] = {
    DEFAULT_FIELD: [
        {
            "版本": "v1",
            "映射": {
                "310000": "华东",
                "330000": "华北",
                "340000": "华南(旧)",
                "350000": "华南(旧)",
            },
            "生效": True,
            "备注": "首轮失败时使用的旧字典：西南/华南仍用旧标签",
        },
        {
            "版本": "v2",
            "映射": {
                "310000": "华东",
                "330000": "华北",
                "340000": "西南",
            },
            "生效": False,
            "备注": "旧字典被替换后的新版：340000 改判为西南，350000 暂缺",
        },
        {
            "版本": "v3",
            "映射": {
                "310000": "华东",
                "330000": "华北",
                "340000": "西南",
                "350000": "华南",
            },
            "生效": False,
            "备注": "第二次换字典用的完整版本：值域与标准一致，用于验证重放不跟随最新字典漂移",
        },
    ],
}

_versions: dict[str, list[dict[str, Any]]] = {}
_seeded = False


def _ensure_seed() -> None:
    global _seeded
    if _seeded:
        return
    _seeded = True
    for field, items in _SEED_VERSIONS.items():
        _versions[field] = [copy.deepcopy(item) for item in items]
    _sync_field_normalize(DEFAULT_FIELD)


def _sync_field_normalize(field: str) -> None:
    """把字典版本镜像到字段归一化模块，方便在既有模块里看到版本流转。"""
    rows = store.rows("field_normalize")
    versions = _versions.get(field, [])
    active = next((v for v in versions if v.get("生效")), None)
    for row in rows:
        if row.get("关联字段") == field and row.get("归一类型") == "标准字典":
            row["status"] = "已生效" if active and row.get("规则版本") == active["版本"] else "已停用"
            row["pending"] = False
    for version in versions:
        rule_code = f"NORM-REGION-{version['版本']}"
        existing = next((r for r in rows if r.get("规则编号") == rule_code), None)
        payload = {
            "规则编号": rule_code,
            "关联字段": field,
            "归一类型": "标准字典",
            "标准字典": f"{field}标准字典",
            "映射表": json.dumps(version.get("映射", {}), ensure_ascii=False, sort_keys=True),
            "未知策略": "keep",
            "区间边界": "",
            "规则版本": version["版本"],
        }
        if existing is None:
            row_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            rows.append({
                "id": row_id,
                **payload,
                "status": "已生效" if version.get("生效") else "已停用",
                "pending": False,
                "abnormal": False,
                "version": version["版本"],
                "updated_at": persist.now_iso(),
            })
        else:
            existing.update(payload)
            existing["status"] = "已生效" if version.get("生效") else "已停用"
            existing["pending"] = False
            existing["version"] = version["版本"]
            existing["updated_at"] = persist.now_iso()


def list_versions(field: str = DEFAULT_FIELD) -> list[dict[str, Any]]:
    _ensure_seed()
    return copy.deepcopy(_versions.get(field, []))


def get_version(field: str, version: str) -> dict[str, Any] | None:
    _ensure_seed()
    for item in _versions.get(field, []):
        if item.get("版本") == version:
            return copy.deepcopy(item)
    return None


def active_version(field: str = DEFAULT_FIELD) -> dict[str, Any] | None:
    _ensure_seed()
    for item in _versions.get(field, []):
        if item.get("生效"):
            return copy.deepcopy(item)
    return None


def install_version(
    field: str,
    version: str,
    mapping: dict[str, str],
    *,
    note: str = "",
    make_active: bool = True,
) -> dict[str, Any]:
    """新增/覆盖一个字典版本；make_active 时把同字段其它版本置为失效。"""
    _ensure_seed()
    version = str(version or "").strip()
    if not version:
        raise ValueError("字典版本不能为空")
    clean_mapping = {str(key): str(value) for key, value in mapping.items()}
    versions = _versions.setdefault(field, [])
    item = next((v for v in versions if v.get("版本") == version), None)
    if item is None:
        item = {"版本": version, "映射": clean_mapping, "生效": False, "备注": note}
        versions.append(item)
    else:
        item["映射"] = clean_mapping
        if note:
            item["备注"] = note
    if make_active:
        for other in versions:
            other["生效"] = other is item
    _sync_field_normalize(field)
    persist.append_jsonl("reports", "dictionary_changes.jsonl", {
        "字段名": field,
        "版本": version,
        "生效": bool(item.get("生效")),
        "映射": clean_mapping,
        "备注": note,
        "timestamp": persist.now_iso(),
    })
    return copy.deepcopy(item)


def change_summary(field: str, from_version: str, to_version: str) -> list[dict[str, Any]]:
    """对比两个版本对同一原码的解释差异，供复检时提示口径冲突。"""
    old = get_version(field, from_version) or {}
    new = get_version(field, to_version) or {}
    old_map = old.get("映射", {}) or {}
    new_map = new.get("映射", {}) or {}
    changes: list[dict[str, Any]] = []
    for code in sorted(set(old_map) | set(new_map)):
        before = old_map.get(code)
        after = new_map.get(code)
        if before != after:
            changes.append({"原码": code, "旧值": before, "新值": after})
    return changes
