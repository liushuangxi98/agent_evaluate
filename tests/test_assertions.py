"""验证两种断言的中断范围，以及测试人员最终看到的日志与报告。"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cases.test_job_match import Case
from main import run_case


class CaseAssertionsTest(unittest.TestCase):
    def test_info_continues_and_err_stops_only_this_case(self):
        cleanup = []
        # 安排一次普通失败、一次普通通过，再触发必需步骤失败，以验证两种断言怎样配合。
        def steps(case):
            case.assert_info("核对列表", [1], [2])
            # 检查后续字典比较通过后，整条用例仍保留之前的失败状态。
            self.assertEqual(case.status, "FAIL")
            case.assert_info("核对字典", {"分": 0}, {"分": 0})
            self.assertEqual(case.status, "FAIL")
            self.assertEqual(len(case.steps), 2)
            self.assertEqual(case.report_path.name, "report.md")
            case.assert_err("核对报告归属", "本场", "其他场")
            # 如果硬断言仍让执行走到这里，就说明它没有按约定结束场景。
            self.fail("硬断言失败后不应执行到这里")

        original_teardown = Case.teardown
        def teardown(case):
            # 保留场景真正的关闭连接动作，随后检查中断时客户端是否也已关闭。
            original_teardown(case)
            cleanup.append(case.api.is_closed)

        with TemporaryDirectory() as temp, patch.object(Case, "run", steps), patch.object(Case, "teardown", teardown):
            folder = Path(temp)
            # 通过批次入口运行，核对硬断言只结束当前用例，失败原因同时包含先前的软断言。
            status, detail, stop = run_case("断言示例", "cases.test_job_match", folder, {})
            self.assertEqual((status, stop, cleanup), ("FAIL", False, [True]))
            self.assertIn("核对列表、核对报告归属", detail)
            log = (folder / "run.log").read_text(encoding="utf8")
            # 逐条核对日志等级和原始值，让软失败、后续通过和硬失败能在日志里区分开。
            for line in ("WARNING assert_info | 核对列表 | 预期：[1] | 实际：[2] | 结果：FAIL",
                         "DEBUG assert_info | 核对字典 | 预期：{'分': 0} | 实际：{'分': 0} | 结果：PASS",
                         "ERROR assert_err | 核对报告归属 | 预期：'本场' | 实际：'其他场' | 结果：FAIL"):
                self.assertIn(line, log)
            # 从实际生成的 Markdown 检查，失败的用例仍有时间记录和逐步验收结果。
            report = (folder / "report.md").read_text(encoding="utf8")
            for label in ("开始时间", "结束时间", "用时", "核对字典**（通过）"):
                self.assertIn(label, report)

    def test_soft_failure_returns_fail_after_last_step(self):
        def steps(case):
            # 以合法的零值通过必需步骤，再让空值预期失败，用最后一个通过步骤验证用例状态。
            case.assert_err("核对零分", 0, 0)
            case.assert_info("核对可选项", None, "其他")
            case.assert_info("核对末项", True, True)

        with TemporaryDirectory() as temp, patch.object(Case, "run", steps):
            result = run_case("软断言", "cases.test_job_match", Path(temp), {})
            # 场景返回后核对 FAIL、失败步骤名称和停批标志，软断言不应阻止下一条用例。
            self.assertEqual(result, ("FAIL", "预期不符：核对可选项", False))
            log = (Path(temp) / "run.log").read_text(encoding="utf8")
            # 硬断言成功要作为正常步骤写入日志，保留数字零的预期和实际值。
            self.assertIn("INFO assert_err | 核对零分 | 预期：0 | 实际：0 | 结果：PASS", log)
