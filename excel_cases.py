from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config" / "cases"


def read_cases(excel_path):
    # 按首行列名读取配置，保留文本岗位 ID 的前导零以及数字形式的预期分数。
    table = pd.read_excel(excel_path, dtype=object, keep_default_na=False)
    # 测试人员留空的可选列表示跳过该项验收；填数字 0 则要校验零分。
    table = table.replace("", None)
    # 返回“用例 ID → 整行配置”，让入口按清单顺序找到每条用例的数据。
    return table.set_index("用例ID", drop=False).to_dict(orient="index")
