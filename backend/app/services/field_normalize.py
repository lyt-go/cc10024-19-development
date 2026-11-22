"""字段归一化领域服务：将清洗后数据做标准化映射，消费clean_pipeline和transform_rule输出。"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.base import BaseModuleService
from app import persistence as persist


def normalize_by_mapping(raw: Any, mapping: dict[str, str]) -> tuple[str, bool, str]:
    """按给定字典映射归一单个原码。

    返回 (归一值, 是否命中, 原码)。未命中时保留原值，便于复检继续按原口径判定。
    """
    text = "" if raw is None else str(raw)
    if text in mapping:
        return str(mapping[text]), True, text
    return text, False, text


class FieldNormalizeService(BaseModuleService):
    MODULE = "field_normalize"
    ENTITY = "归一规则"
    OPERATION_TERM = "标准化"
    LIST_FIELDS = ["规则编号", "关联字段", "归一类型", "标准字典", "映射表", "未知策略", "区间边界", "规则版本"]
    REQUIRED_FIELDS = ["规则编号", "关联字段", "归一类型"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = ["停用规则"]

    def normalize_value(self, field: str, value: str) -> tuple[str, bool]:
        """对单个字段值执行归一化。返回归一化后的值和是否成功。"""
        rules = [r for r in store.rows(self.MODULE) if r.get("关联字段") == field and r.get("status") == "已生效"]
        if not rules:
            return value, True
        rule = rules[0]
        norm_type = rule.get("归一类型", "")
        if norm_type == "lowercase":
            return value.lower(), True
        elif norm_type == "trim":
            return value.strip(), True
        elif norm_type == "标准字典":
            mapping = rule.get("映射表", "")
            if mapping and value in mapping:
                return value, True
            unknown = rule.get("未知策略", "keep")
            return value if unknown == "keep" else "", unknown == "keep"
        return value, True

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        # 关联的清洗管道
        pipelines = store.rows("clean_pipeline")
        base["upstream_pipelines"] = sum(1 for p in pipelines if p.get("status") == "已启用")
        # 关联的质量防护
        guards = store.rows("quality_guard")
        base["downstream_quality_rules"] = sum(1 for g in guards if g.get("status") == "已生效")
        return base