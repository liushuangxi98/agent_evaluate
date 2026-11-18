from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config" / "cases"


def scenario_cases(script_path):
    # 脚本与配置同名，配置表统一放在 config/cases。
    return read_cases(CONFIG / Path(script_path).with_suffix(".xlsx").name)


def read_cases(excel_path):
    # 首行是表头；原样读取岗位 ID 等文本，数字仍保留为数值。
    table = pd.read_excel(excel_path, dtype=object, keep_default_na=False)
    # 可选验收列留空表示跳过检查；数字 0 仍是有效的预期值。
    table = table.replace("", None)
    # 以用例 ID 为键，保留表中行序和每行的完整配置（包括 ID 本身）。
    return table.set_index("用例ID", drop=False).to_dict(orient="index")
