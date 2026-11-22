"""违规复检接口：首轮失败明细、可查询选集、复检轮次、重放与字典版本管理。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas import ActionResult
from app.services import region_dictionary
from app.services import violation_recheck as recheck

router = APIRouter(prefix="/api/recheck", tags=["违规复检"])


class ChecklistPayload(BaseModel):
    name: str = ""
    violation_ids: list[str] = Field(default_factory=list)
    remark: str = ""


class SubmitPayload(BaseModel):
    violation_ids: list[str] | None = None
    mode: str = "current"
    remark: str = ""
    run_id: int | None = None


class DictionaryPayload(BaseModel):
    field: str = recheck.DICT_FIELD
    version: str
    mapping: dict[str, str] = Field(default_factory=dict)
    note: str = ""
    make_active: bool = True


@router.get("/state", response_model=dict)
def get_state() -> dict[str, Any]:
    return recheck.ViolationRecheckService().state()


@router.get("/violations", response_model=dict)
def list_violations(
    source: str | None = Query(default=None),
    field: str | None = Query(default=None),
    result: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    sort: str = Query(default="来源,行标识"),
) -> dict[str, Any]:
    items, total = recheck.list_violations(
        source=source, field=field, result=result, keyword=keyword,
        page=page, size=size, sort=sort)
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/sources", response_model=dict)
def list_sources() -> dict[str, Any]:
    return {"items": recheck.ViolationRecheckService().source_rows()}


@router.get("/checklists", response_model=dict)
def list_checklists() -> dict[str, Any]:
    return {"items": recheck.list_checklists()}


@router.post("/checklists", response_model=ActionResult)
def create_checklist(payload: ChecklistPayload) -> ActionResult:
    try:
        checklist = recheck.create_checklist(
            payload.name, payload.violation_ids, remark=payload.remark)
    except ValueError as error:
        return ActionResult(ok=False, message=str(error))
    return ActionResult(
        ok=True, message=f"复检清单{checklist['清单编号']}已创建", entry=checklist)


@router.get("/checklists/{checklist_id}", response_model=dict)
def get_checklist(checklist_id: int) -> dict[str, Any]:
    try:
        return recheck.get_checklist(checklist_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/checklists/{checklist_id}/items", response_model=dict)
def list_checklist_items(
    checklist_id: int,
    source: str | None = Query(default=None),
    field: str | None = Query(default=None),
    result: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    sort: str = Query(default="来源,行标识"),
) -> dict[str, Any]:
    try:
        checklist, items, total = recheck.list_checklist_items(
            checklist_id, source=source, field=field, result=result,
            keyword=keyword, page=page, size=size, sort=sort)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"checklist": checklist, "items": items, "total": total, "page": page, "size": size}


@router.post("/checklists/{checklist_id}/submit", response_model=ActionResult)
def submit_checklist(checklist_id: int, payload: SubmitPayload) -> ActionResult:
    try:
        round_record = recheck.submit_checklist(
            checklist_id,
            violation_ids=payload.violation_ids,
            mode=payload.mode,
            remark=payload.remark,
            run_id=payload.run_id,
        )
    except ValueError as error:
        return ActionResult(ok=False, message=str(error))
    stats = round_record["统计"]
    return ActionResult(
        ok=True,
        message=(
            f"复检{round_record['轮次编号']}完成：{stats['行数']}行，"
            f"通过{stats['通过数']}，仍违规{stats['仍违规数']}，"
            f"字典版本{round_record['实际修复字典版本']}"
        ),
        entry=round_record,
    )


@router.get("/rounds", response_model=dict)
def list_rounds() -> dict[str, Any]:
    return {"items": recheck.list_rounds()}


@router.get("/rounds/{round_id}", response_model=dict)
def get_round(round_id: int) -> dict[str, Any]:
    try:
        return recheck.get_round(round_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/rounds/{round_id}/replay", response_model=ActionResult)
def replay_round(round_id: int) -> ActionResult:
    try:
        replayed = recheck.replay_round(round_id)
    except ValueError as error:
        return ActionResult(ok=False, message=str(error))
    stats = replayed["统计"]
    verdict = "与上次一致" if replayed.get("与上次一致") else "与上次不一致"
    return ActionResult(
        ok=True,
        message=(
            f"重放{replayed['轮次编号']}（源轮次{replayed['重放自']}）："
            f"{stats['行数']}行，通过{stats['通过数']}，仍违规{stats['仍违规数']}，{verdict}"
        ),
        entry=replayed,
    )


@router.get("/dictionary", response_model=dict)
def list_dictionary(field: str = Query(default=recheck.DICT_FIELD)) -> dict[str, Any]:
    return {
        "field": field,
        "active": region_dictionary.active_version(field),
        "versions": region_dictionary.list_versions(field),
    }


@router.post("/dictionary", response_model=ActionResult)
def install_dictionary(payload: DictionaryPayload) -> ActionResult:
    try:
        item = region_dictionary.install_version(
            payload.field, payload.version, payload.mapping,
            note=payload.note, make_active=payload.make_active)
    except ValueError as error:
        return ActionResult(ok=False, message=str(error))
    return ActionResult(
        ok=True,
        message=f"地区字典版本{item['版本']}已{'生效' if item.get('生效') else '登记'}",
        entry=item,
    )
