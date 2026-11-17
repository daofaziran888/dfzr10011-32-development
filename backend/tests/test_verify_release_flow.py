"""校验规则归属审批与放行链路的服务级回归测试。

覆盖：
1. 越权/只读用户不能创建或修改规则；
2. 校验字段必须指向计划真实输出；
3. 未获批版本不能产生放行结论；
4. 并发审批只保留一个获批版本；
5. 等待审批期间计划交接的两种处理路径；
6. 撤回审批不删除已有不通过明细，撤回后未获批版本仍只读；
7. 结果聚合只放行唯一获批规则产生的明细，放行清单可核对规则版本与计划所属团队；
8. 归属复核清单反映校验放行结果数据。
"""
from __future__ import annotations

import sys
import unittest

from app.services.merge import MergeService
from app.services.verify import VerifyService
from app.store import store

verify = VerifyService()
merge = MergeService()

TEAM_A = "采集一组"
TEAM_B = "采集二组"
TEAM_C = "采集三组"


def op(team: str, role: str = "操作员", name: str = "测试员") -> dict[str, str]:
    return {"操作人": name, "操作团队": team, "操作角色": role}


def rule_by_code(code: str) -> dict:
    for row in store.rows("verify"):
        if row.get("规则编号") == code:
            return row
    raise AssertionError(f"规则 {code} 不存在")


def rule_id(code: str) -> int:
    return int(rule_by_code(code)["id"])


class VerifyFlowTest(unittest.TestCase):
    def test_01_bootstrap_release_aligned(self) -> None:
        """启动补数：VERI-0001 已获批，放行结果与明细状态对齐。"""
        verify.bootstrap_release_results()
        rows = [r for r in store.rows("verify_release") if r.get("规则编号") == "VERI-0001"]
        self.assertEqual(len(rows), 8, "4 条明细 × 2 个校验字段")
        fails = [r for r in rows if r.get("校验结论") == "不通过"]
        self.assertEqual(len(fails), 2)
        self.assertEqual({r["明细编号"] for r in fails}, {"DTL-0002", "DTL-0003"})
        self.assertTrue(all(r.get("审批团队") == TEAM_A for r in rows))
        self.assertTrue(all(r.get("计划所属团队") == TEAM_A for r in rows))
        self.assertTrue(rule_by_code("VERI-0001")["abnormal"])

    def test_02_readonly_and_intruder_cannot_create(self) -> None:
        values = {
            "采集计划": "PLAN-0001", "校验类型": "非空校验", "校验字段": ["标题"],
            "阈值配置": "0", "不通过动作": "隔离明细", **op(TEAM_A, role="只读"),
        }
        entry, missing = verify.create_entry(values)
        self.assertIsNone(entry)
        self.assertIn("只读用户不能改动共享内容", missing[0])
        values = {**values, **op(TEAM_C)}
        entry, missing = verify.create_entry(values)
        self.assertIsNone(entry)
        self.assertIn("规则只能由采集计划所属团队", missing[0])

    def test_03_field_must_match_plan_output(self) -> None:
        values = {
            "采集计划": "PLAN-0001", "校验类型": "非空校验", "校验字段": ["不存在的字段"],
            "阈值配置": "0", "不通过动作": "隔离明细", **op(TEAM_A),
        }
        entry, missing = verify.create_entry(values)
        self.assertIsNone(entry)
        self.assertIn("校验字段必须指向计划真实输出", missing[0])

    def test_04_create_submit_approve_run(self) -> None:
        values = {
            "采集计划": "PLAN-0002", "校验类型": "数值下限", "校验字段": ["库存"],
            "阈值配置": "100", "不通过动作": "告警通知", **op(TEAM_B),
        }
        entry, missing = verify.create_entry(values)
        self.assertIsNotNone(entry, missing)
        code = entry["规则编号"]
        self.assertEqual(entry["归属团队"], TEAM_B)
        # 未获批版本不能产生放行结论
        result, message = verify.run_action(rule_id(code), "执行校验", op(TEAM_B))
        self.assertIsNone(result)
        self.assertIn("未获批版本不能产生放行结论", message)
        # 提交 → 通过 → 执行校验
        result, message = verify.run_action(rule_id(code), "提交审批", op(TEAM_B))
        self.assertIsNotNone(result, message)
        result, message = verify.run_action(rule_id(code), "审批通过", {**op(TEAM_B), "审批意见": "同意"})
        self.assertIsNotNone(result, message)
        result, message = verify.run_action(rule_id(code), "执行校验", op(TEAM_B))
        self.assertIsNotNone(result, message)
        rows = [r for r in store.rows("verify_release") if r.get("规则编号") == code]
        self.assertEqual(len(rows), 3)
        # 阈值 100：DTL-0101 库存 32、DTL-0103 库存 12 不通过，DTL-0102 库存 120 放行
        self.assertEqual(sum(1 for r in rows if r["校验结论"] == "不通过"), 2)
        self.__class__.created_code = code

    def test_05_concurrent_approval_keeps_single_version(self) -> None:
        code = self.created_code
        # 新建版本 2 并提交
        entry, message = verify.create_version(rule_id(code), {
            "校验类型": "数值下限", "校验字段": ["库存"], "阈值配置": "5", "不通过动作": "告警通知", **op(TEAM_B),
        })
        self.assertIsNotNone(entry, message)
        entry, message = verify.run_action(rule_id(code), "提交审批", op(TEAM_B))
        self.assertIsNotNone(entry, message)
        # 版本 1 仍获批，版本 2 审批应被并发规则拦下
        result, message = verify.run_action(rule_id(code), "审批通过", op(TEAM_B))
        self.assertIsNone(result)
        self.assertIn("并发审批只保留一个版本", message)

    def test_06_handover_path_a_submitting_team_approves(self) -> None:
        """VERI-0002 待审批期间 PLAN-0002 从二组交接给三组，提交团队继续批准。"""
        record, message = verify.handover_plan({
            "计划编号": "PLAN-0002", "新团队": TEAM_C, "交接说明": "组织架构调整", **op(TEAM_B),
        })
        self.assertIsNotNone(record, message)
        handover_time = record["交接时间"]
        # 越权团队不能审批
        result, message = verify.run_action(rule_id("VERI-0002"), "审批通过", op(TEAM_A))
        self.assertIsNone(result)
        self.assertIn("越权操作", message)
        # 提交团队（二组）继续批准 → 放行数据显示原审批团队 + 交接时间
        result, message = verify.run_action(rule_id("VERI-0002"), "审批通过", {**op(TEAM_B), "审批意见": "继续批准"})
        self.assertIsNotNone(result, message)
        result, message = verify.run_action(rule_id("VERI-0002"), "执行校验", op(TEAM_C))
        self.assertIsNotNone(result, message)
        rows = [r for r in store.rows("verify_release") if r.get("规则编号") == "VERI-0002"]
        self.assertTrue(rows)
        self.assertTrue(all(r.get("审批团队") == TEAM_B for r in rows))
        self.assertTrue(all(r.get("交接时间") == handover_time for r in rows))
        self.assertTrue(all(r.get("计划所属团队") == TEAM_C for r in rows))

    def test_07_handover_path_b_current_team_reapproves(self) -> None:
        """VERI-0003 提交后 PLAN-0003 交接给二组，由当前团队重新批准。"""
        entry, message = verify.run_action(rule_id("VERI-0003"), "提交审批", op(TEAM_A))
        self.assertIsNotNone(entry, message)
        record, message = verify.handover_plan({"计划编号": "PLAN-0003", "新团队": TEAM_B, **op(TEAM_A)})
        self.assertIsNotNone(record, message)
        result, message = verify.run_action(rule_id("VERI-0003"), "审批通过", {**op(TEAM_B), "审批意见": "当前团队重新批准"})
        self.assertIsNotNone(result, message)
        self.assertIn("原提交审批撤回", message)
        result, message = verify.run_action(rule_id("VERI-0003"), "执行校验", op(TEAM_B))
        self.assertIsNotNone(result, message)
        rows = [r for r in store.rows("verify_release") if r.get("规则编号") == "VERI-0003"]
        self.assertTrue(rows)
        self.assertTrue(all(r.get("审批团队") == TEAM_B for r in rows))
        self.assertTrue(all(r.get("原审批撤回依据") for r in rows))
        self.assertIn("采集一组", rows[0]["原审批撤回依据"])

    def test_08_merge_release_list_only_approved(self) -> None:
        """MERG-0001 生成放行清单：只放行唯一获批规则产生的明细。"""
        task = next(r for r in store.rows("merge") if r.get("任务编号") == "MERG-0001")
        record, message = merge.run_action(int(task["id"]), "生成放行清单", op(TEAM_A))
        self.assertIsNotNone(record, message)
        self.assertEqual(record["规则编号"], "VERI-0001")
        self.assertEqual(record["规则版本"], 1)
        self.assertEqual(record["计划所属团队"], TEAM_A)
        self.assertEqual(record["放行明细"], ["DTL-0001", "DTL-0004"])
        self.assertEqual(record["拦截明细"], ["DTL-0002", "DTL-0003"])
        # 越权团队不能生成
        task2 = next(r for r in store.rows("merge") if r.get("任务编号") == "MERG-0002")
        record2, message2 = merge.run_action(int(task2["id"]), "生成放行清单", op(TEAM_A))
        self.assertIsNone(record2)
        self.assertIn("越权操作", message2)

    def test_09_withdraw_keeps_reject_details(self) -> None:
        before = [r for r in store.rows("verify_release") if r.get("规则编号") == "VERI-0001" and r.get("校验结论") == "不通过"]
        self.assertEqual(len(before), 2)
        # 撤回必须填依据
        result, message = verify.run_action(rule_id("VERI-0001"), "撤回审批", op(TEAM_A))
        self.assertIsNone(result)
        self.assertIn("撤回依据", message)
        result, message = verify.run_action(rule_id("VERI-0001"), "撤回审批", {**op(TEAM_A), "撤回依据": "阈值口径调整"})
        self.assertIsNotNone(result, message)
        self.assertIn("2 条不通过明细保留不删", message)
        after = [r for r in store.rows("verify_release") if r.get("规则编号") == "VERI-0001" and r.get("校验结论") == "不通过"]
        self.assertEqual(len(after), 2, "撤回审批不能删除已有不通过明细")
        # 撤回后版本已撤回、规则回到草稿，未获批版本只读：不能再执行校验
        self.assertEqual(result["审批状态"], "已撤回")
        result2, message2 = verify.run_action(rule_id("VERI-0001"), "执行校验", op(TEAM_A))
        self.assertIsNone(result2)
        self.assertIn("未获批版本不能产生放行结论", message2)
        # 撤回后保存草稿也被拒（当前版本是已撤回，只读）
        result3, message3 = verify.run_action(rule_id("VERI-0001"), "保存草稿", {
            "校验类型": "非空校验", "校验字段": ["标题"], "阈值配置": "0", "不通过动作": "隔离明细", **op(TEAM_A),
        })
        self.assertIsNone(result3)
        self.assertIn("只读", message3)
        # 撤回后放行清单生成失败：没有获批版本
        task1 = next(r for r in store.rows("merge") if r.get("任务编号") == "MERG-0001")
        record, message = merge.run_action(int(task1["id"]), "生成放行清单", op(TEAM_A))
        self.assertIsNone(record)
        self.assertIn("没有已获批的校验版本", message)

    def test_10_reject_approval_aligned(self) -> None:
        """审批不通过：版本驳回、审批记录留痕、规则状态对齐。"""
        values = {
            "采集计划": "PLAN-0001", "校验类型": "非空校验", "校验字段": ["发布时间"],
            "阈值配置": "0", "不通过动作": "拦截入库", **op(TEAM_A),
        }
        entry, missing = verify.create_entry(values)
        self.assertIsNotNone(entry, missing)
        code = entry["规则编号"]
        verify.run_action(rule_id(code), "提交审批", op(TEAM_A))
        result, message = verify.run_action(rule_id(code), "审批不通过", {**op(TEAM_A), "审批意见": "阈值口径不清"})
        self.assertIsNotNone(result, message)
        self.assertEqual(result["审批状态"], "已驳回")
        self.assertEqual(result["status"], "草稿")
        records = [r for r in store.rows("verify_approval") if r.get("规则编号") == code]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["审批结论"], "不通过")
        # 驳回版本只读，不能保存草稿
        result2, message2 = verify.run_action(rule_id(code), "保存草稿", {**values})
        self.assertIsNone(result2)
        self.assertIn("只读", message2)

    def test_11_verify_review_reflects_release(self) -> None:
        items = merge.verify_review()
        self.assertTrue(items)
        by_code = {item["规则编号"]: item for item in items}
        self.assertEqual(by_code["VERI-0001"]["复核状态"], "归属一致")
        self.assertEqual(by_code["VERI-0001"]["不通过明细数"], 2)
        self.assertEqual(by_code["VERI-0002"]["复核状态"], "原团队跨交接批准")
        self.assertEqual(by_code["VERI-0002"]["计划所属团队"], TEAM_C)
        self.assertEqual(by_code["VERI-0003"]["复核状态"], "当前团队重新批准")
        self.assertTrue(by_code["VERI-0003"]["原审批撤回依据"])

    def test_12_readonly_cannot_handover_or_release(self) -> None:
        record, message = verify.handover_plan({"计划编号": "PLAN-0001", "新团队": TEAM_C, **op(TEAM_A, role="只读")})
        self.assertIsNone(record)
        self.assertIn("只读用户不能改动共享内容", message)
        task = next(r for r in store.rows("merge") if r.get("任务编号") == "MERG-0003")
        record2, message2 = merge.run_action(int(task["id"]), "生成放行清单", op(TEAM_B, role="只读"))
        self.assertIsNone(record2)
        self.assertIn("只读用户不能改动共享内容", message2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
