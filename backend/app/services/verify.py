"""采集结果校验业务规则：规则归属、版本审批、放行结果与计划交接都收在这里。

约定：
- 规则只能由采集计划所属团队创建或修改，越权用户只能查看；
- 校验字段必须指向计划真实输出，未获批版本不能产生放行结论；
- 并发审批只保留一个获批版本；撤回审批不删除已有不通过明细；
- 等待审批期间计划跨团队交接时保留两种处理：提交团队继续批准
  （放行清单显示原审批团队和交接时间），或当前团队重新批准
  （放行清单显示当前团队审批和原审批撤回依据）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "verify"
PLAN_TABLE = "crawlplan"
HANDOVER_TABLE = "plan_handover"
DETAIL_TABLE = "plan_detail"
VERSION_TABLE = "verify_version"
APPROVAL_TABLE = "verify_approval"
RELEASE_TABLE = "verify_release"

REQUIRED_FIELDS = ["采集计划", "校验类型"]
STATUS_ORDER = ["草稿", "待审批", "已启用", "已暂停", "已关闭"]
CHECK_TYPES = ["非空校验", "数值下限", "长度上限"]
REJECT_ACTIONS = ["隔离明细", "告警通知", "拦截入库"]
READONLY_ROLE = "只读"
ADMIN_ROLE = "管理员"
ACTION_NAMES = [
    "提交审批",
    "审批通过",
    "审批不通过",
    "撤回审批",
    "执行校验",
    "暂停校验",
    "恢复校验",
    "关闭校验",
    "保存草稿",
]
INFLIGHT_STATUSES = {"草稿", "待审批"}


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


def _as_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").replace("，", ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def _check_value(check_type: str, value: Any, threshold: str) -> bool:
    """按校验类型判断单条明细的单个字段是否放行。"""
    if check_type == "非空校验":
        return value is not None and str(value).strip() != ""
    if check_type == "数值下限":
        try:
            return float(str(value)) >= float(str(threshold))
        except (TypeError, ValueError):
            return False
    if check_type == "长度上限":
        try:
            return len(str(value or "")) <= int(float(str(threshold)))
        except (TypeError, ValueError):
            return False
    return False


class VerifyService:
    # ---------- 基础查询 ----------
    def _plan(self, plan_code: Any) -> dict[str, Any] | None:
        code = str(plan_code or "").strip()
        for plan in store.rows(PLAN_TABLE):
            if str(plan.get("计划编号")) == code:
                return plan
        return None

    def _versions_of(self, rule_code: str) -> list[dict[str, Any]]:
        rows = [row for row in store.rows(VERSION_TABLE) if row.get("规则编号") == rule_code]
        return sorted(rows, key=lambda row: int(row.get("版本号", 0)))

    def _current_version(self, rule: dict[str, Any]) -> dict[str, Any] | None:
        for version in self._versions_of(str(rule.get("规则编号"))):
            if int(version.get("版本号", 0)) == int(rule.get("当前版本", 0)):
                return version
        return None

    def _approved_version(self, rule: dict[str, Any]) -> dict[str, Any] | None:
        for version in self._versions_of(str(rule.get("规则编号"))):
            if version.get("审批状态") == "已获批":
                return version
        return None

    def _active_approval(self, rule_code: str, version_no: Any) -> dict[str, Any] | None:
        for record in reversed(store.rows(APPROVAL_TABLE)):
            if (
                record.get("规则编号") == rule_code
                and int(record.get("版本号", 0)) == int(version_no or 0)
                and record.get("审批结论") == "通过"
                and not record.get("已撤回")
            ):
                return record
        return None

    def _latest_handover(self, plan_code: Any, since: Any = None) -> dict[str, Any] | None:
        code = str(plan_code or "").strip()
        records = [row for row in store.rows(HANDOVER_TABLE) if row.get("计划编号") == code]
        if since:
            records = [row for row in records if str(row.get("交接时间") or "") >= str(since)]
        if not records:
            return None
        return max(records, key=lambda row: str(row.get("交接时间") or ""))

    def _decorate(self, rule: dict[str, Any]) -> dict[str, Any]:
        row = dict(rule)
        plan = self._plan(row.get("采集计划"))
        row["计划所属团队"] = plan.get("所属团队") if plan else "未知"
        row["校验字段"] = list(row.get("校验字段") or [])
        approved = self._approved_version(rule)
        row["已获批版本"] = approved.get("版本号") if approved else None
        inflight = [v for v in self._versions_of(str(rule.get("规则编号"))) if v.get("审批状态") in INFLIGHT_STATUSES]
        row["在途版本"] = inflight[0].get("版本号") if inflight else None
        return row

    def _sync_rule(self, rule: dict[str, Any], version: dict[str, Any]) -> None:
        """把当前版本的配置镜像到规则行，保证列表明细与版本状态可对齐。"""
        rule["校验类型"] = version.get("校验类型")
        rule["校验字段"] = list(version.get("校验字段") or [])
        rule["阈值配置"] = version.get("阈值配置")
        rule["不通过动作"] = version.get("不通过动作")
        rule["当前版本"] = version.get("版本号")
        rule["审批状态"] = version.get("审批状态")
        self._refresh_status(rule)

    def _refresh_status(self, rule: dict[str, Any]) -> None:
        if rule.get("status") in ("已暂停", "已关闭"):
            rule["规则状态"] = rule.get("status")
            rule["pending"] = rule.get("status") != "已关闭"
            return
        versions = self._versions_of(str(rule.get("规则编号")))
        if any(v.get("审批状态") == "已获批" for v in versions):
            rule["status"] = "已启用"
        elif any(v.get("审批状态") == "待审批" for v in versions):
            rule["status"] = "待审批"
        else:
            rule["status"] = "草稿"
        rule["规则状态"] = rule["status"]
        rule["pending"] = True

    # ---------- 列表与明细 ----------
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
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._decorate(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        rule = store.find(MODULE, entry_id)
        if rule is None:
            return None
        detail = self._decorate(rule)
        rule_code = str(rule.get("规则编号"))
        versions = []
        for version in self._versions_of(rule_code):
            item = dict(version)
            item["只读"] = version.get("审批状态") != "草稿"
            versions.append(item)
        detail["版本列表"] = versions
        detail["审批记录"] = [
            dict(record) for record in store.rows(APPROVAL_TABLE) if record.get("规则编号") == rule_code
        ]
        plan = self._plan(rule.get("采集计划"))
        detail["计划输出字段"] = list(plan.get("输出字段") or []) if plan else []
        detail["交接记录"] = [
            dict(record) for record in store.rows(HANDOVER_TABLE) if record.get("计划编号") == rule.get("采集计划")
        ]
        return detail

    def list_release(
        self,
        *,
        plan: str | None = None,
        conclusion: str | None = None,
        rule: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(RELEASE_TABLE)
        if plan:
            rows = [row for row in rows if row.get("采集计划") == plan]
        if conclusion:
            rows = [row for row in rows if row.get("校验结论") == conclusion]
        if rule:
            rows = [row for row in rows if rule in str(row.get("规则编号", ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [dict(row) for row in rows[start:start + size]], total

    # ---------- 采集计划归属与交接 ----------
    def list_plans(self) -> list[dict[str, Any]]:
        plans = []
        for plan in store.rows(PLAN_TABLE):
            row = dict(plan)
            row["交接记录"] = [
                dict(record) for record in store.rows(HANDOVER_TABLE) if record.get("计划编号") == plan.get("计划编号")
            ]
            plans.append(row)
        return plans

    def handover_plan(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        operator = _operator_of(values)
        if operator["role"] == READONLY_ROLE:
            return None, "只读用户不能改动共享内容"
        code = str(values.get("计划编号") or "").strip()
        new_team = str(values.get("新团队") or "").strip()
        note = str(values.get("交接说明") or "").strip()
        plan = self._plan(code)
        if plan is None:
            return None, f"采集计划 {code or '空'} 不存在，无法交接"
        old_team = str(plan.get("所属团队") or "")
        if not new_team:
            return None, "新团队不能为空"
        if new_team == old_team:
            return None, f"采集计划 {code} 已归属 {old_team}，无需交接"
        if operator["team"] != old_team and operator["role"] != ADMIN_ROLE:
            return None, f"越权操作：只能由计划所属团队 {old_team} 或管理员发起交接，当前仅可查看"
        record = {
            "id": _next_id(HANDOVER_TABLE),
            "计划编号": code,
            "原团队": old_team,
            "新团队": new_team,
            "交接时间": _now(),
            "交接说明": note or f"{operator['name']} 发起计划交接",
        }
        store.rows(HANDOVER_TABLE).append(record)
        plan["所属团队"] = new_team
        return record, f"采集计划 {code} 已由 {old_team} 交接至 {new_team}"

    # ---------- 规则与版本登记 ----------
    def _check_fields(self, plan: dict[str, Any], values: dict[str, Any]) -> tuple[list[str], str]:
        check_type = str(values.get("校验类型") or "").strip()
        if check_type not in CHECK_TYPES:
            return [], f"校验类型「{check_type}」不支持，可选：{'、'.join(CHECK_TYPES)}"
        action = str(values.get("不通过动作") or "").strip()
        if action not in REJECT_ACTIONS:
            return [], f"不通过动作「{action}」不支持，可选：{'、'.join(REJECT_ACTIONS)}"
        fields = _as_list(values.get("校验字段"))
        if not fields:
            return [], "校验字段不能为空"
        outputs = [str(field) for field in plan.get("输出字段") or []]
        invalid = [field for field in fields if field not in outputs]
        if invalid:
            return [], f"校验字段必须指向计划真实输出：{'、'.join(invalid)} 不在 {plan.get('计划编号')} 的输出字段里"
        threshold = str(values.get("阈值配置") or "").strip()
        if check_type in ("数值下限", "长度上限"):
            if not threshold:
                return [], f"{check_type}必须填写阈值配置"
            try:
                float(threshold)
            except ValueError:
                return [], f"阈值配置「{threshold}」不是有效数值"
        return fields, ""

    def _add_version(
        self,
        rule: dict[str, Any],
        values: dict[str, Any],
        fields: list[str],
        operator: dict[str, str],
    ) -> dict[str, Any]:
        versions = self._versions_of(str(rule.get("规则编号")))
        version = {
            "id": _next_id(VERSION_TABLE),
            "规则编号": rule.get("规则编号"),
            "版本号": max((int(v.get("版本号", 0)) for v in versions), default=0) + 1,
            "校验类型": values.get("校验类型"),
            "校验字段": fields,
            "阈值配置": str(values.get("阈值配置") or "").strip(),
            "不通过动作": values.get("不通过动作"),
            "提交团队": operator["team"],
            "提交时间": None,
            "审批状态": "草稿",
            "审批团队": None,
            "审批时间": None,
            "审批意见": None,
        }
        store.rows(VERSION_TABLE).append(version)
        self._sync_rule(rule, version)
        return version

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        operator = _operator_of(values)
        if operator["role"] == READONLY_ROLE:
            return None, ["只读用户不能改动共享内容"]
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        plan = self._plan(values.get("采集计划"))
        if plan is None:
            return None, [f"采集计划 {values.get('采集计划')} 不存在，无法确认归属团队"]
        if operator["team"] != plan.get("所属团队"):
            return None, [f"规则只能由采集计划所属团队 {plan.get('所属团队')} 创建或修改"]
        fields, error = self._check_fields(plan, values)
        if error:
            return None, [error]
        code = str(values.get("规则编号") or "").strip() or _next_code(MODULE, "规则编号", "VERI")
        if any(row.get("规则编号") == code for row in store.rows(MODULE)):
            return None, [f"规则编号 {code} 已存在"]
        rule = {
            "id": _next_id(MODULE),
            "规则编号": code,
            "采集计划": plan.get("计划编号"),
            "归属团队": plan.get("所属团队"),
            "当前版本": 0,
            "审批状态": "草稿",
            "最近校验": None,
            "status": STATUS_ORDER[0],
            "规则状态": STATUS_ORDER[0],
            "pending": True,
            "abnormal": False,
        }
        store.rows(MODULE).append(rule)
        self._add_version(rule, values, fields, operator)
        return self._decorate(rule), []

    def create_version(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        operator = _operator_of(values)
        if operator["role"] == READONLY_ROLE:
            return None, "只读用户不能改动共享内容"
        rule = store.find(MODULE, entry_id)
        if rule is None:
            return None, f"校验规则 {entry_id} 不存在或已归档"
        if rule.get("status") == "已关闭":
            return None, "规则已关闭，不能新建版本"
        plan = self._plan(rule.get("采集计划"))
        if plan is not None and operator["team"] != plan.get("所属团队"):
            return None, f"规则只能由采集计划所属团队 {plan.get('所属团队')} 创建或修改"
        inflight = [
            v for v in self._versions_of(str(rule.get("规则编号"))) if v.get("审批状态") in INFLIGHT_STATUSES
        ]
        if inflight:
            return None, f"版本 {inflight[0].get('版本号')} 仍在{inflight[0].get('审批状态')}，请先处理再新建版本"
        if plan is None:
            return None, f"采集计划 {rule.get('采集计划')} 不存在，无法确认归属团队"
        fields, error = self._check_fields(plan, values)
        if error:
            return None, error
        version = self._add_version(rule, values, fields, operator)
        return self._decorate(rule), f"已登记版本 {version.get('版本号')}（草稿），可提交审批"

    # ---------- 动作流转 ----------
    def run_action(self, entry_id: int, action: str, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        rule = store.find(MODULE, entry_id)
        if rule is None:
            return None, f"校验规则 {entry_id} 不存在或已归档"
        operator = _operator_of(values)
        if operator["role"] == READONLY_ROLE:
            return None, "只读用户不能改动共享内容"
        handlers = {
            "提交审批": self._submit,
            "审批通过": self._approve,
            "审批不通过": self._reject,
            "撤回审批": self._withdraw,
            "执行校验": self._run_validation,
            "暂停校验": self._pause,
            "恢复校验": self._resume,
            "关闭校验": self._close,
            "保存草稿": self._save_draft,
        }
        handler = handlers.get(action)
        if handler is None:
            return None, f"动作「{action}」不属于采集结果校验可执行范围"
        return handler(rule, values, operator)

    def _team_guard(self, rule: dict[str, Any], operator: dict[str, str]) -> str:
        plan = self._plan(rule.get("采集计划"))
        if plan is not None and operator["team"] != plan.get("所属团队"):
            return f"规则只能由采集计划所属团队 {plan.get('所属团队')} 创建或修改"
        return ""

    def _submit(self, rule: dict[str, Any], values: dict[str, Any], operator: dict[str, str]):
        version = self._current_version(rule)
        if version is None or version.get("审批状态") != "草稿":
            return None, "只有草稿版本可以提交审批"
        guard = self._team_guard(rule, operator)
        if guard:
            return None, guard
        version["提交团队"] = operator["team"]
        version["提交时间"] = _now()
        version["审批状态"] = "待审批"
        self._sync_rule(rule, version)
        return self._decorate(rule), f"版本 {version.get('版本号')} 已提交审批，等待 {version.get('提交团队')} 审批"

    def _approve(self, rule: dict[str, Any], values: dict[str, Any], operator: dict[str, str]):
        version = self._current_version(rule)
        if version is None or version.get("审批状态") != "待审批":
            return None, "只有待审批版本可以审批通过"
        plan = self._plan(rule.get("采集计划"))
        plan_team = plan.get("所属团队") if plan else None
        submit_team = version.get("提交团队")
        if operator["team"] not in {submit_team, plan_team}:
            return None, f"越权操作：只能由提交团队 {submit_team} 或计划所属团队 {plan_team} 审批，当前仅可查看"
        approved = [v for v in self._versions_of(str(rule.get("规则编号"))) if v.get("审批状态") == "已获批"]
        if approved:
            return None, f"版本 {approved[0].get('版本号')} 已获批，并发审批只保留一个版本，请先撤回原审批"
        handover = self._latest_handover(rule.get("采集计划"), version.get("提交时间"))
        handover_time = handover.get("交接时间") if handover else None
        withdraw_basis = None
        if operator["team"] != submit_team:
            withdraw_basis = (
                f"采集计划已由 {submit_team} 交接至 {operator['team']}"
                f"（{handover_time or '交接时间未知'}），原提交审批撤回，由当前团队重新批准"
            )
        now = _now()
        version["审批状态"] = "已获批"
        version["审批团队"] = operator["team"]
        version["审批时间"] = now
        version["审批意见"] = str(values.get("审批意见") or "").strip() or "同意"
        store.rows(APPROVAL_TABLE).append({
            "id": _next_id(APPROVAL_TABLE),
            "规则编号": rule.get("规则编号"),
            "版本号": version.get("版本号"),
            "审批团队": operator["team"],
            "审批结论": "通过",
            "审批时间": now,
            "审批意见": version.get("审批意见"),
            "已撤回": False,
            "撤回依据": None,
            "撤回时间": None,
            "交接时间": handover_time,
            "原审批撤回依据": withdraw_basis,
        })
        self._sync_rule(rule, version)
        message = f"版本 {version.get('版本号')} 审批通过"
        if withdraw_basis:
            message += f"（{withdraw_basis}）"
        elif handover_time:
            message += f"（原审批团队 {operator['team']}，交接时间 {handover_time}）"
        return self._decorate(rule), message

    def _reject(self, rule: dict[str, Any], values: dict[str, Any], operator: dict[str, str]):
        version = self._current_version(rule)
        if version is None or version.get("审批状态") != "待审批":
            return None, "只有待审批版本可以审批不通过"
        plan = self._plan(rule.get("采集计划"))
        plan_team = plan.get("所属团队") if plan else None
        submit_team = version.get("提交团队")
        if operator["team"] not in {submit_team, plan_team}:
            return None, f"越权操作：只能由提交团队 {submit_team} 或计划所属团队 {plan_team} 审批，当前仅可查看"
        now = _now()
        opinion = str(values.get("审批意见") or "").strip() or "未填写不通过原因"
        version["审批状态"] = "已驳回"
        version["审批团队"] = operator["team"]
        version["审批时间"] = now
        version["审批意见"] = opinion
        store.rows(APPROVAL_TABLE).append({
            "id": _next_id(APPROVAL_TABLE),
            "规则编号": rule.get("规则编号"),
            "版本号": version.get("版本号"),
            "审批团队": operator["team"],
            "审批结论": "不通过",
            "审批时间": now,
            "审批意见": opinion,
            "已撤回": False,
            "撤回依据": None,
            "撤回时间": None,
            "交接时间": None,
            "原审批撤回依据": None,
        })
        self._sync_rule(rule, version)
        return self._decorate(rule), f"版本 {version.get('版本号')} 审批不通过：{opinion}"

    def _withdraw(self, rule: dict[str, Any], values: dict[str, Any], operator: dict[str, str]):
        version = self._approved_version(rule)
        if version is None:
            return None, "没有已获批版本，无法撤回审批"
        plan = self._plan(rule.get("采集计划"))
        plan_team = plan.get("所属团队") if plan else None
        approve_team = version.get("审批团队")
        if operator["team"] not in {approve_team, plan_team}:
            return None, f"越权操作：只能由审批团队 {approve_team} 或计划所属团队 {plan_team} 撤回审批"
        basis = str(values.get("撤回依据") or "").strip()
        if not basis:
            return None, "撤回审批必须填写撤回依据"
        now = _now()
        for record in store.rows(APPROVAL_TABLE):
            if (
                record.get("规则编号") == rule.get("规则编号")
                and int(record.get("版本号", 0)) == int(version.get("版本号", 0))
                and record.get("审批结论") == "通过"
                and not record.get("已撤回")
            ):
                record["已撤回"] = True
                record["撤回依据"] = basis
                record["撤回时间"] = now
        version["审批状态"] = "已撤回"
        self._sync_rule(rule, version)
        kept = sum(
            1
            for row in store.rows(RELEASE_TABLE)
            if row.get("规则编号") == rule.get("规则编号")
            and int(row.get("版本号", 0)) == int(version.get("版本号", 0))
            and row.get("校验结论") == "不通过"
        )
        return self._decorate(rule), f"版本 {version.get('版本号')} 审批已撤回（{basis}），已有 {kept} 条不通过明细保留不删"

    def _run_validation(self, rule: dict[str, Any], values: dict[str, Any], operator: dict[str, str]):
        version = self._approved_version(rule)
        if version is None:
            return None, "未获批版本不能产生放行结论，请先完成审批"
        guard = self._team_guard(rule, operator)
        if guard:
            return None, guard
        plan = self._plan(rule.get("采集计划"))
        created = self._generate_release(rule, version, plan)
        rule["最近校验"] = _now()
        fails = sum(1 for row in created if row.get("校验结论") == "不通过")
        rule["abnormal"] = fails > 0
        return self._decorate(rule), f"版本 {version.get('版本号')} 校验完成：放行 {len(created) - fails} 条，不通过 {fails} 条"

    def _pause(self, rule: dict[str, Any], values: dict[str, Any], operator: dict[str, str]):
        if rule.get("status") != "已启用":
            return None, "只有已启用规则可以暂停校验"
        guard = self._team_guard(rule, operator)
        if guard:
            return None, guard
        rule["status"] = "已暂停"
        rule["规则状态"] = "已暂停"
        return self._decorate(rule), "校验规则已暂停校验"

    def _resume(self, rule: dict[str, Any], values: dict[str, Any], operator: dict[str, str]):
        if rule.get("status") != "已暂停":
            return None, "只有已暂停规则可以恢复校验"
        guard = self._team_guard(rule, operator)
        if guard:
            return None, guard
        if self._approved_version(rule) is None:
            return None, "没有已获批版本，不能恢复启用"
        rule["status"] = "已启用"
        rule["规则状态"] = "已启用"
        return self._decorate(rule), "校验规则已恢复校验"

    def _close(self, rule: dict[str, Any], values: dict[str, Any], operator: dict[str, str]):
        if rule.get("status") != "已暂停":
            return None, "只有已暂停规则可以关闭校验"
        guard = self._team_guard(rule, operator)
        if guard:
            return None, guard
        rule["status"] = "已关闭"
        rule["规则状态"] = "已关闭"
        rule["pending"] = False
        return self._decorate(rule), "校验规则已关闭校验"

    def _save_draft(self, rule: dict[str, Any], values: dict[str, Any], operator: dict[str, str]):
        version = self._current_version(rule)
        if version is None or version.get("审批状态") != "草稿":
            return None, "未获批版本仍只读，只有草稿版本可以保存修改"
        guard = self._team_guard(rule, operator)
        if guard:
            return None, guard
        plan = self._plan(rule.get("采集计划"))
        if plan is None:
            return None, f"采集计划 {rule.get('采集计划')} 不存在，无法确认归属团队"
        merged = {
            "校验类型": values.get("校验类型", version.get("校验类型")),
            "校验字段": values.get("校验字段", version.get("校验字段")),
            "阈值配置": values.get("阈值配置", version.get("阈值配置")),
            "不通过动作": values.get("不通过动作", version.get("不通过动作")),
        }
        fields, error = self._check_fields(plan, merged)
        if error:
            return None, error
        version["校验类型"] = merged["校验类型"]
        version["校验字段"] = fields
        version["阈值配置"] = str(merged.get("阈值配置") or "").strip()
        version["不通过动作"] = merged["不通过动作"]
        self._sync_rule(rule, version)
        return self._decorate(rule), f"草稿版本 {version.get('版本号')} 已保存"

    # ---------- 放行结果 ----------
    def _generate_release(
        self,
        rule: dict[str, Any],
        version: dict[str, Any],
        plan: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        rows = store.rows(RELEASE_TABLE)
        # 同一规则版本重跑时先清掉旧结果，保证明细与状态对齐
        rows[:] = [
            row
            for row in rows
            if not (
                row.get("规则编号") == rule.get("规则编号")
                and int(row.get("版本号", 0)) == int(version.get("版本号", 0))
            )
        ]
        approval = self._active_approval(str(rule.get("规则编号")), version.get("版本号")) or {}
        details = [row for row in store.rows(DETAIL_TABLE) if row.get("采集计划") == rule.get("采集计划")]
        created: list[dict[str, Any]] = []
        for detail in details:
            content = detail.get("输出内容") or {}
            for field in version.get("校验字段") or []:
                passed = _check_value(str(version.get("校验类型")), content.get(field), str(version.get("阈值配置") or ""))
                row = {
                    "id": _next_id(RELEASE_TABLE),
                    "结果编号": _next_code(RELEASE_TABLE, "结果编号", "VREL"),
                    "规则编号": rule.get("规则编号"),
                    "版本号": version.get("版本号"),
                    "采集计划": rule.get("采集计划"),
                    "计划所属团队": plan.get("所属团队") if plan else rule.get("归属团队"),
                    "审批团队": version.get("审批团队"),
                    "交接时间": approval.get("交接时间"),
                    "原审批撤回依据": approval.get("原审批撤回依据"),
                    "明细编号": detail.get("明细编号"),
                    "校验字段": field,
                    "校验类型": version.get("校验类型"),
                    "阈值配置": version.get("阈值配置"),
                    "校验结论": "放行" if passed else "不通过",
                    "不通过动作": None if passed else version.get("不通过动作"),
                    "生成时间": _now(),
                }
                rows.append(row)
                created.append(row)
        return created

    def bootstrap_release_results(self) -> None:
        """服务启动时为已获批但还没有放行结果的版本补一份，保证示例数据明细与状态对齐。"""
        for rule in store.rows(MODULE):
            version = self._approved_version(rule)
            if version is None:
                continue
            exists = any(
                row.get("规则编号") == rule.get("规则编号")
                and int(row.get("版本号", 0)) == int(version.get("版本号", 0))
                for row in store.rows(RELEASE_TABLE)
            )
            if exists:
                continue
            plan = self._plan(rule.get("采集计划"))
            created = self._generate_release(rule, version, plan)
            fails = sum(1 for row in created if row.get("校验结论") == "不通过")
            rule["最近校验"] = rule.get("最近校验") or _now()
            rule["abnormal"] = fails > 0
