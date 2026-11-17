"""结果聚合归并业务规则：归并状态流转、校验放行清单与归属复核口径都收在这里。

下游联动约定：
- 生成放行清单时读取采集结果校验的放行结果数据，只放行唯一获批规则版本产生的明细；
- 放行清单保留规则版本、计划所属团队、审批团队、交接时间与原审批撤回依据，方便核对；
- 校验规则归属复核清单由校验放行结果数据归并而成，供其他业务入口查阅。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "merge"
PLAN_TABLE = "crawlplan"
VERIFY_TABLE = "verify"
VERSION_TABLE = "verify_version"
VERIFY_RELEASE_TABLE = "verify_release"
RELEASE_LIST_TABLE = "merge_release"

REQUIRED_FIELDS = ["任务编号", "采集计划", "分片总数"]
STATUS_ORDER = ["待归并", "归并中", "已完成", "归并失败"]
ACTION_RULES = {"启动归并": "待归并", "重试归并": "归并中", "归档任务": "归并失败"}
RELEASE_ACTION = "生成放行清单"
NEGATIVE_ACTIONS: list[str] = []
READONLY_ROLE = "只读"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(table: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1


def _next_code(table: str, field: str, prefix: str) -> str:
    top = 0
    for row in store.rows(table):
        text = str(row.get(field) or "")
        if text.startswith(f"{prefix}-"):
            try:
                top = max(top, int(text.split("-", 1)[1]))
            except ValueError:
                continue
    return f"{prefix}-{top + 1:04d}"


def _operator_of(values: dict[str, Any]) -> dict[str, str]:
    return {
        "name": str(values.get("操作人") or "值班管理员"),
        "team": str(values.get("操作团队") or "").strip(),
        "role": str(values.get("操作角色") or "操作员").strip() or "操作员",
    }


class MergeService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"聚合任务 {entry_id} 不存在或已归档"
        if action == RELEASE_ACTION:
            return self._generate_release_list(entry, values)
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于结果聚合归并可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"聚合任务已{action}"

    # ---------- 校验放行清单 ----------
    def _approved_rule_version(self, plan_code: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """找到该采集计划下唯一获批的校验规则版本；没有或不止一个都返回 None。"""
        approved: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for rule in store.rows(VERIFY_TABLE):
            if rule.get("采集计划") != plan_code:
                continue
            for version in store.rows(VERSION_TABLE):
                if version.get("规则编号") == rule.get("规则编号") and version.get("审批状态") == "已获批":
                    approved.append((rule, version))
        if len(approved) != 1:
            return None
        return approved[0]

    def _generate_release_list(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        operator = _operator_of(values)
        if operator["role"] == READONLY_ROLE:
            return None, "只读用户不能改动共享内容"
        plan_code = str(entry.get("采集计划") or "").strip()
        plan = next((row for row in store.rows(PLAN_TABLE) if row.get("计划编号") == plan_code), None)
        if plan is None:
            return None, f"采集计划 {plan_code} 不存在，无法核对计划所属团队"
        if operator["team"] != plan.get("所属团队"):
            return None, f"越权操作：放行清单只能由计划所属团队 {plan.get('所属团队')} 生成，当前仅可查看"
        approved_count = sum(
            1
            for rule in store.rows(VERIFY_TABLE)
            if rule.get("采集计划") == plan_code
            for version in store.rows(VERSION_TABLE)
            if version.get("规则编号") == rule.get("规则编号") and version.get("审批状态") == "已获批"
        )
        if approved_count == 0:
            return None, f"采集计划 {plan_code} 没有已获批的校验版本，未获批版本不能产生放行结论"
        if approved_count > 1:
            return None, "存在多个获批校验版本，并发审批只保留一个版本，请先撤回多余审批"
        found = self._approved_rule_version(plan_code)
        if found is None:
            return None, "未找到唯一获批的校验版本"
        rule, version = found
        results = [
            row
            for row in store.rows(VERIFY_RELEASE_TABLE)
            if row.get("规则编号") == rule.get("规则编号")
            and int(row.get("版本号", 0)) == int(version.get("版本号", 0))
        ]
        if not results:
            return None, f"规则 {rule.get('规则编号')} 版本 {version.get('版本号')} 还没有校验放行结果，请先在采集结果校验执行校验"
        detail_pass: dict[str, bool] = {}
        for row in results:
            key = str(row.get("明细编号"))
            detail_pass[key] = detail_pass.get(key, True) and row.get("校验结论") == "放行"
        released = sorted(key for key, ok in detail_pass.items() if ok)
        blocked = sorted(key for key, ok in detail_pass.items() if not ok)
        first = results[0]
        record = {
            "id": _next_id(RELEASE_LIST_TABLE),
            "清单编号": _next_code(RELEASE_LIST_TABLE, "清单编号", "MREL"),
            "任务编号": entry.get("任务编号"),
            "采集计划": plan_code,
            "规则编号": rule.get("规则编号"),
            "规则版本": version.get("版本号"),
            "计划所属团队": plan.get("所属团队"),
            "审批团队": first.get("审批团队"),
            "交接时间": first.get("交接时间"),
            "原审批撤回依据": first.get("原审批撤回依据"),
            "放行明细": released,
            "拦截明细": blocked,
            "放行明细数": len(released),
            "拦截明细数": len(blocked),
            "生成时间": _now(),
        }
        store.rows(RELEASE_LIST_TABLE).append(record)
        entry["已归并数"] = str(len(released))
        entry["status"] = "已完成"
        entry["任务状态"] = "已完成"
        entry["pending"] = False
        message = (
            f"放行清单 {record['清单编号']} 已生成：放行 {len(released)} 条，拦截 {len(blocked)} 条"
            f"（规则 {rule.get('规则编号')} 版本 {version.get('版本号')}，计划所属团队 {plan.get('所属团队')}）"
        )
        return record, message

    def list_release_list(
        self,
        *,
        plan: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(RELEASE_LIST_TABLE)
        if plan:
            rows = [row for row in rows if row.get("采集计划") == plan]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [dict(row) for row in rows[start:start + size]], total

    # ---------- 校验规则归属复核清单 ----------
    def verify_review(self) -> list[dict[str, Any]]:
        """把校验放行结果按规则版本归并成复核清单，供归并下游核对规则归属。"""
        grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for row in store.rows(VERIFY_RELEASE_TABLE):
            key = (str(row.get("规则编号")), int(row.get("版本号", 0)))
            grouped.setdefault(key, []).append(row)
        items: list[dict[str, Any]] = []
        for (rule_code, version_no), rows in sorted(grouped.items()):
            rule = next((item for item in store.rows(VERIFY_TABLE) if item.get("规则编号") == rule_code), None)
            version = next(
                (
                    item
                    for item in store.rows(VERSION_TABLE)
                    if item.get("规则编号") == rule_code and int(item.get("版本号", 0)) == version_no
                ),
                None,
            )
            plan = next((item for item in store.rows(PLAN_TABLE) if item.get("计划编号") == rows[0].get("采集计划")), None)
            first = rows[0]
            approve_team = first.get("审批团队")
            plan_team = plan.get("所属团队") if plan else first.get("计划所属团队")
            if first.get("原审批撤回依据"):
                review_status = "当前团队重新批准"
            elif first.get("交接时间") and approve_team != plan_team:
                review_status = "原团队跨交接批准"
            elif approve_team == plan_team:
                review_status = "归属一致"
            else:
                review_status = "待复核"
            items.append({
                "规则编号": rule_code,
                "版本号": version_no,
                "采集计划": first.get("采集计划"),
                "计划所属团队": plan_team,
                "归属团队": rule.get("归属团队") if rule else None,
                "提交团队": version.get("提交团队") if version else None,
                "审批团队": approve_team,
                "审批状态": version.get("审批状态") if version else None,
                "交接时间": first.get("交接时间"),
                "原审批撤回依据": first.get("原审批撤回依据"),
                "放行明细数": len({row.get("明细编号") for row in rows if row.get("校验结论") == "放行"}),
                "不通过明细数": len({row.get("明细编号") for row in rows if row.get("校验结论") == "不通过"}),
                "最近生成时间": max(str(row.get("生成时间") or "") for row in rows),
                "复核状态": review_status,
            })
        return items
