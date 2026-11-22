"""Schema 规则求值引擎：把单条规则对单个字段值的判定抽出来，供首轮校验和复检共用。

规则快照的键固定为：字段名、类型约束、格式约束、空值策略、规则编号、规则版本。
复检时必须使用首轮失败时的规则快照，而不是当前内存里可能已经改过的规则行，
这样同一批数据在字典或规则变化后仍能重放出首轮口径。
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable


def violation_identity(
    source: str,
    row_code: str,
    field: str,
    rule: dict[str, Any],
    dict_version: str,
) -> str:
    """生成稳定违规身份：来源+行标识+字段+规则编号/版本+字典版本。

    页内序号、来源行 id 都不参与，重排、翻页、重新采集都不会改变同一条违规的身份。
    """
    raw = "|".join([
        str(source), str(row_code), str(field),
        str(rule.get("规则编号", "")), str(rule.get("规则版本", "")), str(dict_version),
    ])
    return "V-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def rule_snapshot(rule: dict[str, Any]) -> dict[str, Any]:
    """从规则行中截取与判定有关的字段，作为不可变快照保存。"""
    return {
        "规则编号": rule.get("规则编号", ""),
        "规则版本": rule.get("规则版本", ""),
        "目标数据": rule.get("目标数据", ""),
        "字段名": rule.get("字段名", ""),
        "类型约束": rule.get("类型约束", ""),
        "格式约束": rule.get("格式约束", ""),
        "空值策略": rule.get("空值策略", ""),
        "异常归类": rule.get("异常归类", ""),
        "合法值": list(rule.get("合法值") or []),
    }


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _allowed_literals(format_constraint: str) -> set[str] | None:
    """解析 `枚举:A,B` / `enum:A,B` 形式的字面量枚举。"""
    for prefix in ("枚举:", "enum:"):
        if format_constraint.startswith(prefix):
            return {item.strip() for item in format_constraint[len(prefix):].split(",") if item.strip()}
    return None


def evaluate_rule(
    rule: dict[str, Any],
    value: Any,
    *,
    legal_values: Iterable[str] | None = None,
) -> list[str]:
    """用一条规则快照判定一个值，返回违规原因列表；空列表表示通过。

    legal_values 只对“标准字典”类格式约束生效，由调用方按当前口径或复检固定的
    字典版本传入，保证同一值在不同字典版本下可以分别得到首轮/本轮结论。
    """
    field = str(rule.get("字段名", ""))
    code = rule.get("规则编号", "")
    reasons: list[str] = []

    if _is_blank(value):
        if rule.get("空值策略") == "禁止":
            reasons.append(f"字段{field}不允许为空（规则{code}）")
        return reasons

    expected_type = str(rule.get("类型约束", "") or "")
    if expected_type == "int":
        try:
            int(value)
        except (ValueError, TypeError):
            reasons.append(f"字段{field}期望int类型，实际为{type(value).__name__}")
    elif expected_type == "float":
        try:
            float(value)
        except (ValueError, TypeError):
            reasons.append(f"字段{field}期望float类型")
    elif expected_type == "date":
        if not isinstance(value, str) or len(value) < 8:
            reasons.append(f"字段{field}日期格式不符合{rule.get('格式约束', '')}")

    format_constraint = str(rule.get("格式约束", "") or "")
    if format_constraint in {"标准字典", "字典", "enum", "枚举"}:
        if legal_values is not None:
            allowed = {str(item) for item in legal_values}
        else:
            allowed = {str(item) for item in (rule.get("合法值") or [])}
        if not allowed:
            reasons.append(f"字段{field}缺少可用的标准字典版本（规则{code}）")
        elif str(value) not in allowed:
            reasons.append(f"字段{field}取值{value}不在标准字典范围内（规则{code}）")
    else:
        literals = _allowed_literals(format_constraint)
        if literals is not None and str(value) not in literals:
            reasons.append(f"字段{field}取值{value}不在枚举{format_constraint}内（规则{code}）")

    return reasons
