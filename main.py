"""从本地执行清单运行独立的场景实例并归档报告。"""
import csv
import logging
import os
from importlib import import_module
import sys

from batch_setup import prepare_batch


def run_case(case_id, module_name, case_dir, case_config):
    # 每条用例单独创建实例，并记录本次的输入与检查结果。
    case_record = {"case": case_config, "checks": []}
    # 每条用例拥有独立日志；写完即关闭文件，不修改整个进程的日志设置。
    with (case_dir / "run.log").open("w", encoding="utf-8") as stream:
        log = logging.Logger(case_id, level=logging.INFO)
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
        log.addHandler(handler)
        log.info("开始用例 %s（%s）", case_id, case_config.get("用例名称") or "未填写名称")
        try:
            # 例如导入 cases.test_job_match，就能通过 module.Case 访问该脚本定义的类。
            module = import_module(module_name)
            # 模块可以复用，每条用例仍用自己的数据和目录创建一个新实例。
            test_case = module.Case(case_record, log)
            try:
                result = test_case.run()
            finally:
                # 只要实例创建成功，即使业务调用抛出异常，也执行一次收尾。
                test_case.teardown()
            status, detail = result["status"], result["detail"]
        except Exception as error:
            # 场景返回 FAIL 表示预期不符；执行或收尾异常记为 ERROR 并保留调用栈。
            log.exception("用例执行出错")
            status, detail = "ERROR", str(error) or type(error).__name__
        log.info("用例结束：%s，%s", status, detail)
    write_report(case_dir, case_id, case_record, status, detail)
    # 超限会阻止后续请求，不能仅以普通用例失败处理。
    return status, detail, case_record.get("stop_batch", False)


def display(value):
    # 一个关键词可能命中零项或多项，用可读文字呈现，不显示 Python 列表语法。
    if isinstance(value, list):
        return "、".join(map(str, value)) or "无匹配项"
    return "空值" if value is None else str(value)


def write_report(case_dir, case_id, case_record, status, detail):
    case = case_record["case"]
    title = case.get("用例名称") or case_id
    lines = [f"# {title}（{case_id}）", "", f"**{status}**　{detail}", "",
             f"- 执行环境：{os.environ.get('EVAL_RUN_LABEL', '在线评估')}",
             f"- 岗位 ID：{case.get('岗位ID') or '未填写'}"]
    # 报告只收录定位本次运行和理解判定所需的信息。
    for label, key in [("画像版本", "profile_revision"), ("岗位版本", "job_revision"),
                       ("工作台运行 ID", "run_id"), ("工作台报告 ID", "report_id"), ("通勤路线", "route")]:
        if key in case_record:
            lines.append(f"- {label}：{case_record[key]}")
    lines += ["", "### 验收明细", ""]
    for check in case_record["checks"]:
        state = "通过" if check["通过"] else "不符合"
        lines.append(f"- **{check['字段']}**（{state}）：预期 {display(check['预期'])}，实际 {display(check['实际'])}")
    if not case_record["checks"]:
        lines.append("尚未执行验收检查。")
        for field in ("要求关键词", "预期匹配判断", "预期必备项数", "预期匹配分"):
            if case.get(field) is not None:
                lines.append(f"- {field}：{case[field]}")
    if status == "ERROR":
        lines += ["", "出错步骤和异常堆栈见同目录的 run.log。"]
    (case_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    batch_dir, cases = prepare_batch()
    print(batch_dir, flush=True)
    # 每条结束立即写汇总，后续用例中断时也能看到先前结果。
    pass_count = 0
    fail_count = 0
    stop_batch = False
    # 汇总所有用例结果
    with (batch_dir / "summary.csv").open("w", newline="", encoding="utf-8-sig") as csv_file:
        # writer 负责把一组值写成表格的一行。如果说明里有逗号或换行，它会自动加引号。
        writer = csv.writer(csv_file)
        # writerow 每次写一行，列表里的每个元素占一列。这次写的是表头，整批只写一次。
        writer.writerow(["用例ID", "结果", "执行环境", "说明"])
        for case_id, module_name, case_config in cases:
            # 明确记录因前序停止而未运行的用例，避免误计为失败。
            if stop_batch:
                status, detail = "NOT_RUN", "前序用例要求停止本批"
                write_report(batch_dir / case_id, case_id, {"case": case_config, "checks": []}, status, detail)
            else:
                status, detail, stop_batch = run_case(case_id, module_name, batch_dir / case_id, case_config)
            # 每轮循环写一条用例，四个值依次对应上面的四列表头。执行环境默认填“在线评估”。
            writer.writerow([case_id, status, os.environ.get("EVAL_RUN_LABEL", "在线评估"), detail])
            # flush 把已写的内容从 Python 缓冲区交给操作系统，不必等整批结束才看到这一行。
            csv_file.flush()
            print(case_id, status, flush=True)
            if status == "PASS":
                pass_count += 1
            elif status == "FAIL":
                fail_count += 1
    print(f"本次测试用例{len(cases)}个，PASS用例数{pass_count}，FAIL用例数{fail_count}。")
    return 0 if pass_count == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
