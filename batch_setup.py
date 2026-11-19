"""把执行清单中的 ID 配上场景脚本、用例数据和本批报告目录。"""
from datetime import datetime

from excel_cases import CONFIG, ROOT, read_cases
from GlobalVar import CASE_SCRIPTS


def load_cases():
    # 执行清单决定这次跑哪些用例，Excel 的行序就是本批的运行顺序。
    selected_cases = read_cases(ROOT / "执行清单.xlsx")
    if not selected_cases:
        raise ValueError("执行清单没有填写用例 ID")

    cases = []
    cases_by_script = {}
    for case_id in selected_cases:
        # 用完整 ID 查登记表；新增用例忘记登记时，在发起评估前指出是哪一条。
        if case_id not in CASE_SCRIPTS:
            raise ValueError(f"{case_id} 未在 GlobalVar.py 登记脚本")
        module_name = CASE_SCRIPTS[case_id]
        # 把登记的模块名还原成脚本路径，核对场景脚本是否在本项目中。
        script_path = ROOT.joinpath(*module_name.split(".")).with_suffix(".py")
        if not script_path.is_file():
            raise ValueError(f"{case_id} 找不到脚本 {script_path.name}")
        # 多个 ID 共用同一场景时，整批只读取一次该场景的 Excel 配置。
        if script_path not in cases_by_script:
            cases_by_script[script_path] = read_cases(CONFIG / script_path.with_suffix(".xlsx").name)
        # 脚本存在还不够，配置表里必须有本条 ID 对应的岗位和预期。
        if case_id not in cases_by_script[script_path]:
            raise ValueError(f"{case_id} 找不到场景配置行")
        # 把用例 ID、加载入口和这一行配置一起交给批次执行器。
        cases.append((case_id, module_name, cases_by_script[script_path][case_id]))
    return cases


def prepare_batch():
    cases = load_cases()
    # 按本批启动时间建目录；同一秒已存在的批次不能被新报告覆盖。
    batch_dir = ROOT / "reports" / datetime.now().strftime("%Y-%m/%Y-%m-%d/%H-%M-%S")
    batch_dir.mkdir(parents=True)
    # 在发出第一个业务请求前，先给清单中的每条用例分好报告目录。
    for case_id, _, _ in cases:
        (batch_dir / case_id).mkdir()
    return batch_dir, cases
