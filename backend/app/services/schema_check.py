"""Schema校验领域服务：数据结构、字段类型、空值策略校验，在run_history确认完成和导出前触发。

除了返回可读的违规文案，还把命中违规的字段级明细（字段名、原值、规则快照）交给
违规复检清单使用，避免复检阶段只能看到一句摘要、拿不到可纠正的原值。
"""
from __future__ import annotations

from typing import Any, Iterable

from app.store import store
from app.services.base import BaseModuleService
from app.services.schema_engine import evaluate_rule, rule_snapshot
from app import persistence as persist


class SchemaCheckService(BaseModuleService):
    MODULE = "schema_check"
    ENTITY = "Schema规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则编号", "目标数据", "字段名", "类型约束", "格式约束", "空值策略", "异常归类", "规则版本"]
    REQUIRED_FIELDS = ["规则编号", "目标数据", "字段名"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = ["停用规则"]

    def active_rule_snapshots(self, target_module: str) -> list[dict[str, Any]]:
        """取某目标数据上所有已生效规则的不可变快照。"""
        return [
            rule_snapshot(rule)
            for rule in store.rows(self.MODULE)
            if rule.get("目标数据") == target_module and rule.get("status") == "已生效"
        ]

    def validate_detail(
        self,
        record: dict[str, Any],
        target_module: str,
        *,
        rules: list[dict[str, Any]] | None = None,
        legal_values: dict[str, Iterable[str]] | None = None,
    ) -> list[dict[str, Any]]:
        """按规则快照逐字段校验，返回字段级违规明细。

        每条形如：字段名、原值（采集值）、校验值、规则快照、违规原因。
        legal_values 以字段名索引，供“标准字典”约束使用指定字典版本的合法值。
        """
        snapshots = rules if rules is not None else self.active_rule_snapshots(target_module)
        details: list[dict[str, Any]] = []
        for rule in snapshots:
            field = str(rule.get("字段名", ""))
            raw = record.get(field)
            check_value = record.get(f"{field}__归一值", raw)
            allowed = (legal_values or {}).get(field)
            reasons = evaluate_rule(rule, check_value, legal_values=allowed)
            if not reasons:
                continue
            details.append({
                "字段名": field,
                "原值": raw,
                "校验值": check_value,
                "规则快照": rule_snapshot(rule),
                "违规原因": reasons,
            })
        return details

    def validate_record(self, record: dict[str, Any], target_module: str) -> list[str]:
        """对单条记录执行所有已生效的Schema校验规则，返回违规列表。"""
        details = self.validate_detail(record, target_module)
        violations = [
            reason for detail in details for reason in detail["违规原因"]
        ]
        if violations:
            persist.append_jsonl("reports", "schema_violations.jsonl", {
                "target_module": target_module,
                "record_summary": str(record)[:100],
                "violations": violations,
                "details": details,
                "timestamp": persist.now_iso(),
            })
        return violations

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        # Schema违规统计
        violations = persist.read_jsonl("reports", "schema_violations.jsonl")
        base["total_violations"] = len(violations)
        # 关联的质量防护规则
        guards = store.rows("quality_guard")
        base["linked_quality_rules"] = sum(1 for g in guards if g.get("status") == "已生效")
        # 关联的导出通道
        channels = store.rows("export_channel")
        base["linked_export_channels"] = len(channels)
        return base
