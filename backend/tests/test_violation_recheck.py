"""违规复检领域测试：稳定身份、选集边界、字典版本口径冲突与重放稳定性。

不依赖 pytest 的网络客户端，直接调用领域服务，保证干净环境也能跑。
"""
from __future__ import annotations

import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.services import region_dictionary as region_dict
from app.services import violation_recheck as recheck
from app.services.run_history import RunHistoryService
from app.services.schema_engine import evaluate_rule, violation_identity


def _reset_checked_state() -> None:
    """每个用例前把复检相关内存表清回种子状态。"""
    from app.store import store
    for module in (recheck.SOURCE_MODULE, recheck.FIRST_ROUND_MODULE,
                   recheck.CHECKLIST_MODULE, recheck.ROUND_MODULE):
        store.rows(module).clear()
    recheck._seeded = False
    region_dict._seeded = False
    region_dict._versions.clear()
    recheck.ensure_seeded()


def _first_picked(checklist_id: int, source: str = "甲站") -> str:
    _, page, _ = recheck.list_checklist_items(
        checklist_id, source=source, page=1, size=1, sort="来源,行标识")
    return page[0]["violation_id"]


def test_violation_identity_is_stable():
    rule = {"规则编号": "SCHE-REGION-001", "规则版本": "r1"}
    first = violation_identity("甲站", "J-1002", "地区", rule, "v1")
    second = violation_identity("甲站", "J-1002", "地区", rule, "v1")
    other_row = violation_identity("甲站", "J-1003", "地区", rule, "v1")
    other_site = violation_identity("乙站", "J-1002", "地区", rule, "v1")
    other_dict = violation_identity("甲站", "J-1002", "地区", rule, "v2")
    assert first == second
    assert len({first, other_row, other_site, other_dict}) == 4
    assert first.startswith("V-")


def test_first_round_has_field_level_details():
    _reset_checked_state()
    first = recheck.ensure_first_round()
    assert first["违规数"] == 3
    assert set(first["来源集合"]) == {"甲站", "乙站"}
    for item in first["violations"]:
        assert item["字段名"] == "地区"
        assert item["原值"]
        assert item["原违规规则"]["规则编号"] == "SCHE-REGION-001"
        assert item["违规原因"]
        assert item["violation_id"].startswith("V-")
    # 乙站同名字段也进入首轮明细，但默认清单不选它
    assert any(item["来源"] == "乙站" for item in first["violations"])


def test_checklist_filters_and_pagination_use_stable_identity():
    _reset_checked_state()
    checklist = recheck.list_checklists()[0]
    assert checklist["选中站点"] == ["甲站"]
    page1, page2 = [], []
    for page in (1, 2):
        _, items, total = recheck.list_checklist_items(
            checklist["id"], source="甲站", page=page, size=1, sort="来源,行标识")
        assert total == 2
        assert len(items) == 1
        (page1 if page == 1 else page2).extend(items)
    assert page1[0]["violation_id"] != page2[0]["violation_id"]
    assert {page1[0]["来源"], page2[0]["来源"]} == {"甲站"}
    # 换页结果稳定
    _, again, _ = recheck.list_checklist_items(
        checklist["id"], source="甲站", page=1, size=1, sort="来源,行标识")
    assert again[0]["violation_id"] == page1[0]["violation_id"]


def test_submit_rejects_out_of_scope_and_never_touches_other_sites():
    _reset_checked_state()
    checklist = recheck.list_checklists()[0]
    try:
        recheck.submit_checklist(checklist["id"], violation_ids=["V-not-exist"])
    except ValueError as error:
        assert "超出清单选集" in str(error)
    else:  # pragma: no cover - 失败路径
        raise AssertionError("越界选集必须被拒绝")
    for row in recheck.ViolationRecheckService().source_rows():
        assert row["修复后地区"] == ""
        assert row["复检结果"] == ""


def test_submit_fixes_only_selected_row_and_records_versions():
    _reset_checked_state()
    checklist = recheck.list_checklists()[0]
    selected = _first_picked(checklist["id"], "甲站")
    region_dict.install_version(
        "地区", "v2", {"310000": "华东", "330000": "华北", "340000": "西南"},
        note="旧字典被替换")
    round_record = recheck.submit_checklist(
        checklist["id"], violation_ids=[selected], mode="current")
    assert round_record["统计"] == {"行数": 1, "通过数": 1, "仍违规数": 0}
    assert round_record["实际修复字典版本"] == "v2"
    (chosen,) = round_record["选择"]
    assert chosen["violation_id"] == selected
    assert chosen["原值"] == "340000"
    assert chosen["归一值"] == "西南"
    assert chosen["首轮字典版本"] == "v1"
    assert chosen["实际修复字典版本"] == "v2"
    assert chosen["规则快照"]["规则版本"] == "r1"
    # 旧字典把 340000 解释为旧标签，新字典改判为西南：必须显式记录口径冲突
    conflicts = round_record["口径冲突"]
    assert conflicts and conflicts[0]["原码"] == "340000"
    assert conflicts[0]["旧值"] != conflicts[0]["新值"]

    by_row = {row["行标识"]: row for row in recheck.ViolationRecheckService().source_rows()}
    assert by_row["J-1002"]["修复后地区"] == "西南"
    assert by_row["J-1003"]["修复后地区"] == ""          # 清单内未选行保持原结果
    assert by_row["J-1001"]["修复后地区"] == ""          # 清单外甲站正常行不动
    assert by_row["Y-2001"]["修复后地区"] == ""          # 乙站同名字段不动
    assert by_row["Y-2002"]["修复后地区"] == ""


def test_replay_is_immutable_and_stable_after_dictionary_change_again():
    _reset_checked_state()
    checklist = recheck.list_checklists()[0]
    selected = _first_picked(checklist["id"], "甲站")
    region_dict.install_version(
        "地区", "v2", {"310000": "华东", "330000": "华北", "340000": "西南"},
        note="第一次换字典")
    first_round = recheck.submit_checklist(
        checklist["id"], violation_ids=[selected], mode="current")
    first_values = [(item["行标识"], item["原值"], item["归一值"]) for item in first_round["选择"]]
    first_versions = {
        item["行标识"]: (item["首轮字典版本"], item["实际修复字典版本"])
        for item in first_round["选择"]
    }

    replay1 = recheck.replay_round(first_round["id"])
    assert replay1["与上次一致"] is True
    assert replay1["重放自"] == first_round["轮次编号"]
    # 重放不回写来源行，也不改变历史轮次
    assert len(recheck.list_rounds()) == 2

    # 再次更换字典：v3 对同一原码给出不同值
    region_dict.install_version(
        "地区", "v3",
        {"310000": "华东", "330000": "华北", "340000": "西南", "350000": "华南"},
        note="第二次换字典")
    assert region_dict.active_version("地区")["版本"] == "v3"
    replay2 = recheck.replay_round(first_round["id"])
    assert replay2["实际修复字典版本"] == "v2"  # 跟随轮次固定版本，不跟当前生效版本
    assert replay2["与上次一致"] is True
    assert [(item["行标识"], item["原值"], item["归一值"]) for item in replay2["选择"]] == first_values
    assert len(replay2["选择"]) == len(first_round["选择"])
    assert {
        item["行标识"]: (item["首轮字典版本"], item["实际修复字典版本"])
        for item in replay2["选择"]
    } == first_versions
    assert len(recheck.list_rounds()) == 3  # 每次重放单独留档，但首轮记录不变


def test_run_history_executes_selected_range_and_keeps_untouched_rows():
    _reset_checked_state()
    checklist = recheck.list_checklists()[0]
    region_dict.install_version(
        "地区", "v2", {"310000": "华东", "330000": "华北", "340000": "西南"},
        note="替换旧字典")
    service = RunHistoryService()
    entry, missing = service.create_entry({
        "记录编号": "TEST-RCK-RUN-001",
        "关联计划": "SCHE-REGION",
        "开始时间": "2026-11-22",
        "复检清单id": checklist["id"],
    })
    assert not missing and entry is not None
    assert entry["复检清单"] == checklist["清单编号"]

    result, message = service.run_action(entry["id"], "确认完成")
    assert result is not None, message
    assert result["复检清单"] == checklist["清单编号"]
    assert result["复检轮次"]
    assert result["复检统计"]["行数"] == 2  # 只跑清单选集（甲站两行）
    assert result["复检字典版本"] == "v2"

    by_row = {row["行标识"]: row for row in recheck.ViolationRecheckService().source_rows()}
    assert by_row["J-1002"]["修复后地区"] == "西南"
    assert by_row["J-1003"]["修复后地区"] == "350000"  # v2 缺 350000，保留原值
    assert by_row["J-1003"]["复检结果"] == "仍违规"
    assert by_row["Y-2002"]["修复后地区"] == ""
    assert by_row["J-1001"]["修复后地区"] == ""

    persisted = recheck.get_round(int(result["复检轮次"].split("-")[-1]))
    assert persisted["运行记录id"] == entry["id"]
    assert persisted["统计"]["仍违规数"] == 1


def test_rule_engine_uses_frozen_legal_values_not_current_mapping():
    rule = {
        "规则编号": "SCHE-REGION-001",
        "规则版本": "r1",
        "字段名": "地区",
        "格式约束": "标准字典",
        "空值策略": "禁止",
        "合法值": ["华东", "华北", "华南", "西南"],
    }
    assert evaluate_rule(rule, "华南(旧)", legal_values=rule["合法值"]) != []
    assert evaluate_rule(rule, "西南", legal_values=rule["合法值"]) == []
    # 合法值域为空时不能“因为字典里没这个键”就误判通过
    assert evaluate_rule({**rule, "合法值": []}, "西南", legal_values=[]) != []


if __name__ == "__main__":
    tests = [
        test_violation_identity_is_stable,
        test_first_round_has_field_level_details,
        test_checklist_filters_and_pagination_use_stable_identity,
        test_submit_rejects_out_of_scope_and_never_touches_other_sites,
        test_submit_fixes_only_selected_row_and_records_versions,
        test_replay_is_immutable_and_stable_after_dictionary_change_again,
        test_run_history_executes_selected_range_and_keeps_untouched_rows,
        test_rule_engine_uses_frozen_legal_values_not_current_mapping,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\nALL {len(tests)} RECHECK TESTS PASSED")
