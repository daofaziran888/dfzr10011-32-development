"""结果聚合归并接口：维护聚合任务，覆盖启动归并、重试归并、归档任务与生成放行清单等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.merge import MergeService

router = APIRouter(prefix="/api/merge", tags=["结果聚合归并"])

service = MergeService()

LIST_FIELDS = ["任务编号", "采集计划", "分片总数", "已归并数", "归并策略", "输出路径", "数据总量", "任务状态"]
STATUSES = ["待归并", "归并中", "已完成", "归并失败"]


@router.get("/release-list", response_model=PageResult[dict])
def release_list(
    plan: str | None = Query(default=None, description="按采集计划过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """结果聚合放行清单：可核对规则版本、计划所属团队、审批团队、交接时间与原审批撤回依据。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_release_list(plan=plan, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/verify-review")
def verify_review() -> dict[str, Any]:
    """校验规则归属复核清单：由校验放行结果数据归并而成，供归并下游与其他业务入口核对。"""
    items = service.verify_review()
    return {"items": items, "total": len(items)}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出结果聚合归并清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "merge", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="待归并、归并中、已完成、归并失败"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按任务编号与状态过滤结果聚合归并列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条聚合任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"聚合任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条聚合任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="聚合任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条聚合任务执行启动归并、重试归并、归档任务、生成放行清单；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
