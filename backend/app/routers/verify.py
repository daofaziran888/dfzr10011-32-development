"""采集结果校验接口：维护校验规则归属、版本审批与放行结果，覆盖提交审批、审批通过、审批不通过、撤回审批、执行校验等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.verify import VerifyService

router = APIRouter(prefix="/api/verify", tags=["采集结果校验"])

service = VerifyService()

LIST_FIELDS = ["规则编号", "采集计划", "归属团队", "校验类型", "校验字段", "阈值配置", "不通过动作", "当前版本", "审批状态", "最近校验", "规则状态"]
STATUSES = ["草稿", "待审批", "已启用", "已暂停", "已关闭"]


@router.get("/plans")
def list_plans() -> dict[str, Any]:
    """采集计划归属清单：所属团队、真实输出字段与交接记录，登记规则前先确认归属。"""
    items = service.list_plans()
    return {"items": items, "total": len(items)}


@router.post("/plans/handover", response_model=ActionResult)
def handover_plan(payload: EntryPayload) -> ActionResult:
    """采集计划跨团队交接；等待审批中的规则保留两种审批路径，由审批动作各自落账。"""
    record, message = service.handover_plan(payload.values)
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)


@router.get("/release", response_model=PageResult[dict])
def list_release(
    plan: str | None = Query(default=None, description="按采集计划过滤"),
    conclusion: str | None = Query(default=None, description="放行、不通过"),
    rule: str | None = Query(default=None, description="按规则编号检索"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """校验放行结果数据：只有已获批版本执行校验后才会产生，供归并下游复用。"""
    if size > 500:
        raise HTTPException(status_code=400, detail="每页最多 500 条，请缩小分页范围")
    items, total = service.list_release(plan=plan, conclusion=conclusion, rule=rule, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集结果校验清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "verify", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、待审批、已启用、已暂停、已关闭"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按规则编号与状态过滤采集结果校验列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条校验规则明细：版本列表、审批记录与计划交接记录一并返回。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"校验规则 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条校验规则并生成草稿版本；越权团队与指向非真实输出的校验字段会被拦下。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message="；".join(missing))
    return ActionResult(ok=True, message="校验规则已登记（草稿版本 1）", entry=entry)


@router.post("/{entry_id}/versions", response_model=ActionResult)
def create_version(entry_id: int, payload: EntryPayload) -> ActionResult:
    """为校验规则登记新版本；存在在途版本或越权团队时会被拦下并说明原因。"""
    entry, message = service.create_version(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条校验规则执行提交审批、审批通过、审批不通过、撤回审批、执行校验等动作；越权与只读用户只能查看。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
