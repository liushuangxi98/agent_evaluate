"""按 Excel 清单逐条运行用例，生成单条验收报告和整批结果汇总。"""
import csv
from datetime import datetime
import logging
import os
from importlib import import_module
import sys

from base_case import StopCase
from batch_setup import prepare_batch


def run_case(case_id, module_name, case_dir, case_config):
    test_case = None
    report_path = case_dir / "report.md"
    # 每条用例单独写 run.log；从普通步骤到异常堆栈都只进本用例的文件夹。
    with (case_dir / "run.log").open("w", encoding="utf-8") as stream:
        log = logging.Logger(case_id, level=logging.DEBUG)
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
        log.addHandler(handler)
        log.info(f"开始用例 {case_id}（{case_config.get('用例名称') or '未填写名称'}）")
        try:
            # 按登记的模块名加载场景，例如 cases.test_job_match；下面的 module.Case 就是该脚本里的类。
            module = import_module(module_name)
            test_case = module.Case(case_config, log, report_path)
            try:
                # 运行本条用例的业务步骤。断言在执行时更新实例状态，结束后直接读取。
                test_case.run()
            except StopCase as error:
                # 硬断言已把用例记为 FAIL，这里结束当前场景，把控制权交回批次入口。
                log.warning(f"必需步骤未通过，结束当前用例：{error}")
            finally:
                # 即使中途断言失败，也让场景关闭本次运行占用的资源。
                test_case.teardown()
            status = test_case.status
            # 提取不符合预期的步骤名称，作为汇总表中这条用例的失败说明。
            failed = [step["步骤"] for step in test_case.steps if not step["通过"]]
            detail = "预期不符：" + "、".join(failed) if failed else "所有配置的验收条件均满足"
        except Exception as error:
            # 接口不可用、数据读取或收尾出错都记为 ERROR，异常调用栈写进本条日志。
            log.exception("用例执行出错")
            if test_case is not None:
                test_case.status = "ERROR"
            status, detail = "ERROR", str(error) or type(error).__name__
        if test_case is not None:
            test_case.end_time = datetime.now()
        log.info(f"用例结束：{status}，{detail}")
    # 把已执行步骤写入 report.md，测试人员可以逐项对照预期值和实际值。
    write_report(report_path, case_id, case_config, status, detail, test_case)
    # 把是否停止整批的决定带回入口，例如用例遇到请求超限时，后续用例标成 NOT_RUN。
    return status, detail, test_case is not None and test_case.stop_batch


def display(value):
    # 关键词未命中时在报告中显示“无匹配项”，命中多条则列出全部结果。
    if isinstance(value, list):
        return "、".join(map(str, value)) or "无匹配项"
    return "空值" if value is None else str(value)


def write_report(report_path, case_id, case_config, status, detail, test_case=None):
    steps = test_case.steps if test_case is not None else ()
    title = case_config.get("用例名称") or case_id
    lines = [f"# {title}（{case_id}）", "", f"**{status}**　{detail}", "",
             f"- 执行环境：{os.environ.get('EVAL_RUN_LABEL', '在线评估')}",
             f"- 岗位 ID：{case_config.get('岗位ID') or '未填写'}"]
    # 实例正常开始且结束时，用两个时间点算出本条用例实际耗时。
    if test_case is not None and test_case.end_time is not None:
        start, end = test_case.start_time, test_case.end_time
        lines += [f"- 开始时间：{start:%Y-%m-%d %H:%M:%S}", f"- 结束时间：{end:%Y-%m-%d %H:%M:%S}",
                  f"- 用时：{(end - start).total_seconds():.2f} 秒"]
    # 用运行 ID 和报告 ID 回查工作台记录，路线显示本次实际选择的地点。
    for label, key in [("工作台运行 ID", "run_id"), ("工作台报告 ID", "report_id"), ("通勤路线", "route")]:
        value = getattr(test_case, key, None)
        if value is not None:
            lines.append(f"- {label}：{value}")
    lines += ["", "### 验收明细", ""]
    # 按真实执行顺序列出软、硬断言，测试人员能看到最后执行到哪一步。
    for step in steps:
        state = "通过" if step["通过"] else "不符合"
        lines.append(f"- **{step['步骤']}**（{state}）：预期 {display(step['预期'])}，实际 {display(step['实际'])}")
    # 如果一个验收步骤都没执行，仍列出这条用例原本准备检查的预期。
    if not steps:
        lines.append("尚未执行验收检查。")
        for field in ("要求关键词", "预期匹配判断", "预期必备项数", "预期匹配分"):
            if case_config.get(field) is not None:
                lines.append(f"- {field}：{case_config[field]}")
    if status == "ERROR":
        lines += ["", "出错步骤和异常堆栈见同目录的 run.log。"]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    # 读取要跑的用例并建好报告目录，控制台会打印本批结果所在的位置。
    batch_dir, cases = prepare_batch()
    print(batch_dir, flush=True)
    status_counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "NOT_RUN": 0}
    stop_batch = False
    # 创建本批的 CSV 汇总：utf-8-sig 让 Excel 识别中文，newline='' 避免 Windows 多出空行。
    with (batch_dir / "summary.csv").open("w", newline="", encoding="utf-8-sig") as csv_file:
        # csv.writer 负责分列，即使错误说明带逗号或换行，也会放在同一个单元格。
        writer = csv.writer(csv_file)
        # writerow 每次写一行；循环前只写一次表头，循环内按相同的四列写用例结果。
        writer.writerow(["用例ID", "结果", "执行环境", "说明"])
        for case_id, module_name, case_config in cases:
            # 前序用例要求停批时，不发起新的评估，但清单中剩余每条仍要记录为未执行。
            if stop_batch:
                status, detail = "NOT_RUN", "前序用例要求停止本批"
                write_report(batch_dir / case_id / "report.md", case_id, case_config, status, detail)
            else:
                status, detail, stop_batch = run_case(case_id, module_name, batch_dir / case_id, case_config)
            # 把当前用例的 ID、结论、执行环境和原因写到汇总，及时刷新以便运行中查看。
            writer.writerow([case_id, status, os.environ.get("EVAL_RUN_LABEL", "在线评估"), detail])
            csv_file.flush()
            print(case_id, status, flush=True)
            # 每个清单项只计入一个状态，批次结束时也能看到出错和未执行的数量。
            status_counts[status] += 1
    summary = "，".join(f"{status}用例数{count}" for status, count in status_counts.items())
    print(f"本次测试用例{len(cases)}个，{summary}。")
    # 只有清单中每一条都通过才返回成功退出码，供命令行或自动化任务判断。
    return 0 if status_counts["PASS"] == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
