"""定义岗位评估等场景共用的验收步骤和用例结论。"""
from datetime import datetime


class StopCase(AssertionError):
    """当前用例缺少继续执行的必要条件，交由批次入口记录失败。"""


class BaseCase:
    def __init__(self, case_config, log, report_path):
        # 把执行清单所选的那一行配置、这条用例的日志和报告位置交给场景脚本。
        self.case_config = case_config
        self.log = log
        self.report_path = report_path
        # 每条用例从 PASS 开始；任一步不符后，即使后续步骤通过也保留 FAIL。
        self.status = "PASS"
        # 按执行顺序积累步骤明细，供测试人员在单条报告中逐项核对。
        self.steps = []
        # 用例从实例创建时开始计时，收尾完成后由入口补上结束时间。
        self.start_time = datetime.now()
        self.end_time = None
        # 请求超限或等待超时时，通知入口跳过本批剩余用例。
        self.stop_batch = False

    def assert_info(self, step, expected, actual):
        # 用 Excel 预期对比本步实际结果，保留原始值，方便报告区分空值、零和文本。
        passed = expected == actual
        self.steps.append({"步骤": step, "预期": expected, "实际": actual, "通过": passed})
        message = f"assert_info | {step} | 预期：{expected!r} | 实际：{actual!r}"
        if passed:
            self.log.debug(f"{message} | 结果：PASS")
        else:
            # 普通验收项不符时标记整条用例失败，接着检查其余配置项。
            self.status = "FAIL"
            self.log.warning(f"{message} | 结果：FAIL")

    def assert_err(self, step, expected, actual):
        passed = expected == actual
        # 把必需步骤的结果记入报告，失败时也能看到中断前最后核对了什么。
        self.steps.append({"步骤": step, "预期": expected, "实际": actual, "通过": passed})
        if passed:
            self.log.info(f"assert_err | {step} | 预期：{expected!r} | 实际：{actual!r} | 结果：PASS")
        else:
            self.status = "FAIL"
            # 必需条件未满足，继续用这份数据做后续验收没有意义。
            self.log.error(f"assert_err | {step} | 预期：{expected!r} | 实际：{actual!r} | 结果：FAIL")
            raise StopCase(step)
