"""读取执行清单和用例配置，准备本批报告目录。"""
from datetime import datetime

from excel_cases import ROOT, read_cases, scenario_cases
from GlobalVar import CASE_SCRIPTS


def load_cases():
    # 保留执行清单的顺序；先检查整批配置，再创建目录或发起评估。
    selected_cases = read_cases(ROOT / "执行清单.xlsx")
    if not selected_cases:
        raise ValueError("执行清单没有填写用例 ID")

    cases = []
    # 同一个脚本可以对应多条用例，它的 Excel 配置只需读一次。
    cases_by_script = {}
    for case_id in selected_cases:
        if case_id not in CASE_SCRIPTS:
            raise ValueError(f"{case_id} 未在 GlobalVar.py 登记脚本")
        module_name = CASE_SCRIPTS[case_id]
        # 把 cases.test_job_match 定位到本项目中的 cases/test_job_match.py，供脚本和 Excel 预检。
        script_path = ROOT.joinpath(*module_name.split(".")).with_suffix(".py")
        if not script_path.is_file():
            raise ValueError(f"{case_id} 找不到脚本 {script_path.name}")
        if script_path not in cases_by_script:
            cases_by_script[script_path] = scenario_cases(script_path)
        # 登记过脚本的 ID 也必须在同名配置表里有对应的一行。
        if case_id not in cases_by_script[script_path]:
            raise ValueError(f"{case_id} 找不到场景配置行")
        # 返回已定位的脚本和配置，执行阶段可以直接使用。
        cases.append((case_id, module_name, cases_by_script[script_path][case_id]))
    return cases


def prepare_batch():
    cases = load_cases()
    # 同一秒已有批次时，mkdir 会报错，防止覆盖原报告。
    batch_dir = ROOT / "reports" / datetime.now().strftime("%Y-%m/%Y-%m-%d/%H-%M-%S")
    batch_dir.mkdir(parents=True)
    # 所有用例共享批次开始时间，每条的日志和数据写入自己的目录。
    for case_id, _, _ in cases:
        (batch_dir / case_id).mkdir()
    return batch_dir, cases
