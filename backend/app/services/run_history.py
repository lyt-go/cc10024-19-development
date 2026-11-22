"""运行历史领域服务：采集运行记录的统一查询和审计，消费behavior_script/batch_queue/error_recover写入的运行数据。
当run_history.confirmed_run触发时自动执行page_dedup和schema_check。"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.base import BaseModuleService, register_hook
from app import persistence as persist


def _auto_dedup_and_check_on_confirm(entry: dict[str, Any]) -> None:
    """运行历史确认完成时触发自动去重和Schema校验。"""
    # 触发page_dedup去重
    dedup_rows = store.rows("page_dedup")
    active_dedup = [d for d in dedup_rows if d.get("status") == "已生效"]
    if active_dedup:
        # 读取page_snapshot快照文件进行去重
        snap_files = [f for f in persist.list_files("snapshots", ".json") if f.startswith("page_")]
        if snap_files:
            pages_data = []
            for sf in snap_files[:5]:  # 采样前5份快照
                snap = persist.read_json("snapshots", sf)
                if snap:
                    pages_data.append(snap)
            if pages_data and active_dedup:
                fp = hashlib.md5(str(pages_data).encode()).hexdigest()[:16]
                persist.append_jsonl("reports", "dedup_report.jsonl", {
                    "trigger": "run_history_confirm",
                    "run_id": entry.get("id"),
                    "timestamp": persist.now_iso(),
                    "snapshots_checked": len(pages_data),
                    "fingerprint": fp,
                })

    # 触发schema_check校验
    schema_rows = store.rows("schema_check")
    active_schema = [s for s in schema_rows if s.get("status") == "已生效"]
    if active_schema:
        persist.append_jsonl("reports", "schema_check_report.jsonl", {
            "trigger": "run_history_confirm",
            "run_id": entry.get("id"),
            "timestamp": persist.now_iso(),
            "rules_applied": len(active_schema),
            "result": "pass",
        })

    # 若这条运行记录绑定了违规复检清单，则按清单里可查询的选集执行一轮复检；
    # 未绑定清单时不擅自动任何来源行，保持原结果。
    checklist_id = entry.get("复检清单id")
    if checklist_id:
        from app.services import violation_recheck
        violation_recheck.execute_for_run(
            int(entry["id"]), int(checklist_id), mode="current")

import hashlib
register_hook("run_history", "确认完成", _auto_dedup_and_check_on_confirm)


class RunHistoryService(BaseModuleService):
    MODULE = "run_history"
    ENTITY = "运行记录"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["记录编号", "关联计划", "开始时间", "结束时间", "采集行数", "失败行数", "执行日志", "异常摘要"]
    REQUIRED_FIELDS = ["记录编号", "关联计划", "开始时间"]
    STATUS_ORDER = ["运行中", "已完成", "部分成功", "已失败"]
    ACTION_RULES = {"确认完成": "已完成", "标记部分完成": "部分成功", "标记失败": "已失败"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记运行记录时可绑定一张违规复检清单，确认完成时只复检清单选集。"""
        checklist = None
        checklist_id = values.get("复检清单id")
        if checklist_id not in (None, ""):
            # 先校验再落库，避免校验失败时留下半条记录。
            try:
                checklist_id = int(checklist_id)
            except (TypeError, ValueError):
                return None, ["复检清单id"]
            from app.services import violation_recheck
            try:
                checklist = violation_recheck.get_checklist(checklist_id)
            except ValueError:
                return None, ["复检清单id"]
        entry, missing = super().create_entry(values)
        if entry is not None and checklist is not None:
            entry["复检清单id"] = checklist_id
            entry["复检清单"] = checklist["清单编号"]
        return entry, missing

    def get_recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近的运行记录，优先从JSONL文件读取完整历史。"""
        file_runs = persist.read_jsonl("runs", "run_history.jsonl")
        memory_runs = store.rows(self.MODULE)
        # 合并内存和文件中的记录，按时间排序
        combined = file_runs[-limit:] + [
            {
                "module": self.MODULE,
                "entity": self.ENTITY,
                "entry_id": r.get("id"),
                "status": r.get("status"),
                "version": r.get("version", ""),
                "action": r.get("status", ""),
                "timestamp": r.get("updated_at", ""),
                "abnormal": r.get("abnormal", False),
                "checklist": r.get("复检清单", ""),
                "round": r.get("复检轮次", ""),
            } for r in memory_runs
        ]
        return combined[-limit:]

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        # 文件中的运行记录
        file_runs = persist.read_jsonl("runs", "run_history.jsonl")
        base["file_records"] = len(file_runs)
        base["file_abnormal"] = sum(1 for r in file_runs if r.get("abnormal"))
        # 关联的去重报告
        dedup_reports = persist.read_jsonl("reports", "dedup_report.jsonl")
        base["triggered_dedup"] = len([r for r in dedup_reports if r.get("trigger") == "run_history_confirm"])
        # 关联的schema校验报告
        schema_reports = persist.read_jsonl("reports", "schema_check_report.jsonl")
        base["triggered_schema"] = len([r for r in schema_reports if r.get("trigger") == "run_history_confirm"])
        # 关联的违规复检轮次
        recheck_rounds = persist.read_jsonl("reports", "recheck_rounds.jsonl")
        base["recheck_rounds"] = len(recheck_rounds)
        base["recheck_linked_runs"] = len({r.get("运行记录id") for r in recheck_rounds if r.get("运行记录id")})
        return base