"""验证岗位评估在资料、通勤和报告各环节发生偏差时会给出哪种结论。"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from main import run_case


class JobMatchStepsTest(unittest.TestCase):
    def test_steps(self):
        # 用同一条岗位配置分别模拟正常、业务结果不符、资料错配、选址失败和请求超限。
        for fault, expected in [("", "PASS"), ("soft", "FAIL"), ("profile", "FAIL"), ("job", "FAIL"),
                                ("incomplete", "FAIL"), ("route", "FAIL"), ("snapshot", "FAIL"), ("limit", "ERROR")]:
            with self.subTest(fault=fault), TemporaryDirectory() as temp:
                requests = []
                def respond(req):
                    path = req.url.path
                    # 保留请求方法和路径，后续按调用顺序核对失败之后有没有继续发起评估。
                    requests.append((req.method, path))
                    if path == "/api/profile":
                        # 在第一次读取画像时模拟 413，检查这个用例报错并要求整批停止。
                        if fault == "limit":
                            return httpx.Response(413)
                        data = {"id": None if fault == "profile" else 7, "revision": 3,
                                "profile": {"education": "本科"},
                                "missing": [{"field": "profile.city", "label": "目标城市"}] if fault == "incomplete" else []}
                    elif path == "/api/jobs/j1":
                        # 返回另一个岗位的 ID，验证脚本在创建评估之前就能发现测错了对象。
                        data = {"id": "other" if fault == "job" else "j1", "revision": 2}
                    elif path in ("/api/runs", "/api/runs/r1"):
                        # 模拟工作台待选址状态；在选址失败样本中，只清空终点候选。
                        data = {"id": "r1", "status": "needs_input", "pending_input": {"id": "p1"},
                                "result": {"data": {"place_choices": {"start": ["甲"], "end": [] if fault == "route" else ["乙"]}}}}
                    elif path == "/api/runs/r1/input":
                        # 正常时检查提交的是选项编号；没有终点时检查脚本结束的是本轮输入。
                        body = json.loads(req.content)
                        self.assertEqual(body["input_id"], "p1")
                        self.assertEqual(body["decision"], "finish" if fault == "route" else "answer")
                        if fault != "route":
                            self.assertEqual(body["data"], {"start": "1", "end": "1"})
                        data = {"id": "r1", "status": "completed", "report_id": "rep"}
                    else:
                        self.assertEqual(path, "/api/reports/rep")
                        # 用相同的岗位匹配结果构造正式报告，快照故障样本只替换画像修订号。
                        data = {"id": "rep", "kind": "evaluation", "run_id": "r1",
                                "snapshot": {"profile": {"id": 7, "revision": 1 if fault == "snapshot" else 3},
                                             "jobs": [{"id": "j1", "revision": 2}]},
                                "data": {"jobs": [{"job_id": "j1", "match_score": 100,
                                                   "matches": [{"requirement": "本科", "assessment": "符合"}]}]}}
                    return httpx.Response(200, json=data)

                # 软断言场景只改必备项数的预期，便于确认其余匹配分和学历检查仍然执行。
                config = {"岗位ID": "j1", "通勤起点": "Default", "通勤终点": "乙", "要求关键词": "本科",
                          "预期匹配判断": "符合", "预期匹配分": 100, "预期必备项数": 0 if fault == "soft" else 1}
                # 运行真实用例入口，将业务请求接到上面的测试响应，避免调用模型和地图。
                client = httpx.Client(base_url="http://local.test", transport=httpx.MockTransport(respond))
                with patch("cases.test_job_match.httpx.Client", return_value=client):
                    status, detail, stop = run_case("示例", "cases.test_job_match", Path(temp), config)
                self.assertEqual((status, stop), (expected, fault == "limit"))
                log = (Path(temp) / "run.log").read_text(encoding="utf8")
                # 缺画像、岗位错误或请求超限都应停在发起评估前，不能再创建运行。
                if fault in ("profile", "incomplete", "job", "limit"):
                    self.assertNotIn(("POST", "/api/runs"), requests)
                elif fault == "route":
                    # 检查选址失败后，先读取原运行，再把结束指令提交给它。
                    self.assertIn("唯一通勤终点", detail)
                    self.assertEqual(requests[-2:], [("GET", "/api/runs/r1"), ("POST", "/api/runs/r1/input")])
                elif fault == "snapshot":
                    # 检查报告引用了错误版本的画像后，脚本立即停止，避免继续评价匹配分。
                    self.assertIn("核对画像版本", detail)
                    self.assertNotIn("核对岗位匹配分", log)
                else:
                    # 必备项数不符时，日志里仍应有匹配分和学历两项的检查结果。
                    self.assertIn("核对岗位匹配分 | 预期：100 | 实际：100", log)
                    self.assertIn("核对「本科」要求的匹配判断", log)
                    self.assertIn("提交通勤选择后续接原运行", log)
                # 逐个检查成功、断言失败及接口异常三个出口是否都释放了连接。
                self.assertTrue(client.is_closed)
