"""用当前画像评估 Excel 中指定的岗位，并核对工作台返回的匹配结果。"""

import time

import httpx

from base_case import BaseCase, StopCase


class Case(BaseCase):
    def __init__(self, case_config, log, report_path):
        super().__init__(case_config, log, report_path)
        # 为当前用例连接本机工作台；本次运行的所有岗位、通勤和报告请求共用此客户端。
        self.api = httpx.Client(base_url="http://127.0.0.1:8000", timeout=20, trust_env=False)

    def teardown(self):
        # 无论断言是否中断，入口都会在用例结束时释放本次连接。
        self.api.close()

    def run(self):
        # 先用已保存的画像实际评估一个已有岗位，再把生成的报告交给验收步骤。
        report = self.evaluate()
        self.check_report(report)

    def check_report(self, report):
        case_config = self.case_config
        # 报告类型、运行 ID 和岗位都必须对应本次请求，避免拿旧报告或其他岗位的报告打分。
        self.assert_err("核对报告类型", "evaluation", report["kind"])
        self.assert_err("核对报告是否关联本次运行", self.run_id, report["run_id"])
        report_jobs = report["data"]["jobs"]
        self.assert_err("核对报告是否仅包含配置的岗位", [case_config["岗位ID"]], [job["job_id"] for job in report_jobs])
        job = report_jobs[0]
        matches = job["matches"]
        # Excel 填了哪一项预期就检查哪一项。以下三项独立，一项失败仍继续核对其余项。
        count = case_config["预期必备项数"]
        if count is not None:
            self.assert_info("核对岗位必备项数", count, len(matches))
        score = case_config["预期匹配分"]
        # 留空表示跳过，数字 0 表示预期零分，文本 null 表示工作台应返回空分数。
        if score is not None:
            self.assert_info("核对岗位匹配分", None if score == "null" else score, job["match_score"])
        keyword = case_config["要求关键词"]
        if keyword:
            # 收集所有包含关键词的判断，与只含一个预期判断的列表比较；漏项和重复项都能发现。
            labels = [match["assessment"] for match in matches if keyword in match["requirement"]]
            self.assert_info(f"核对「{keyword}」要求的匹配判断", [case_config["预期匹配判断"]], labels)

    def request(self, method, api_path, body=None):
        # 用当前步骤的路径和参数调用工作台公开接口，和页面走同一套业务流程。
        response = self.api.request(method, api_path, json=body)
        if response.is_error:
            # 收到 413 就通知入口停止整批；日志保留失败接口、状态码和服务端返回的原因。
            if response.status_code == 413:
                self.stop_batch = True
            self.log.error(f"工作台接口 {method} {api_path} 返回 HTTP {response.status_code}：{response.text[:1000]}")
        response.raise_for_status()
        return response.json()

    def evaluate(self):
        """从读取现有画像和岗位开始，跟踪一次工作台评估，直到取回对应的正式报告。"""
        job_id = self.case_config["岗位ID"]
        self.log.info(f"检查工作台环境和已有资料：{self.api.base_url}")
        # 读取当前画像，先核对是否已有保存记录；空表单也可能以 HTTP 200 返回。
        profile = self.request("GET", "/api/profile")
        self.assert_err("读取当前已保存的画像", True, profile["id"] is not None)
        self.assert_err("核对画像必填项完整", [], profile["missing"])
        # 按 Excel 的岗位 ID 读取已有岗位，确认工作台返回的就是本条用例要测的岗位。
        job = self.request("GET", f"/api/jobs/{job_id}")
        self.assert_err("读取 Excel 指定的岗位", job_id, job.get("id"))
        # 提交一次岗位评估，取回运行 ID；后续查询和选择通勤地点都用这个 ID。
        run = self.request("POST", "/api/runs", {"action": "evaluate", "input": {"job_ids": [job_id]}})
        self.assert_err("发起评估后取得运行 ID", True, bool(run.get("id")))
        self.run_id = run["id"]
        run_path = "/api/runs/" + self.run_id
        self.log.info(f"评估已启动：岗位 {job_id}，运行 ID {self.run_id}")
        # 从发起评估开始计算 15 分钟等待时间，超时后停止本批，保留运行 ID 供排查。
        deadline = time.time() + 900
        while run["status"] in {"running", "needs_input"}:
            if time.time() >= deadline:
                self.stop_batch = True
                raise TimeoutError(f"运行仍在执行，请查看 {run_path}")
            # 工作台仍在生成结果时，每秒查询进度，避免重复提交评估。
            if run["status"] == "running":
                time.sleep(1)
                run = self.request("GET", run_path)
                continue
            # 工作台转为等待输入时，按本用例配置选择通勤起终点，续接原运行。
            run = self.confirm_route(run_path, run)

        # 查询进度成功也可能读到失败的业务结果；向报告返回具体原因，并识别上游请求超限。
        if run["status"] == "failed":
            detail = str(run["result"])
            if any(marker in detail for marker in ("413", "TOO_LARGE", "超过大小限制")):
                self.stop_batch = True
            error_data = (run["result"] or {}).get("data", {})
            raise RuntimeError(f"评估未完成（{run['status']}）：{error_data.get('message') or detail}")
        self.assert_err("评估运行已完成", "completed", run.get("status"))
        self.assert_err("完成评估后取得报告 ID", True, bool(run.get("report_id")))
        # 用运行结果提供的报告 ID 读回正式报告，核对报告 ID 后记入本地日志。
        report = self.request("GET", "/api/reports/" + run["report_id"])
        self.assert_err("读取本次生成的报告", run["report_id"], report.get("id"))
        self.report_id = run["report_id"]
        self.log.info(f"已读取工作台报告 {self.report_id}，开始核对预期值")
        # 对照报告快照检查画像和岗位版本，避免用例运行期间的资料修改影响验收结论。
        snapshot = report["snapshot"]
        self.assert_err("核对报告使用的画像 ID", profile["id"], snapshot["profile"].get("id"))
        self.assert_err("核对画像版本", profile["revision"], snapshot["profile"].get("revision"))
        self.assert_err("核对岗位快照", [(job_id, job["revision"])],
                        [(item.get("id"), item.get("revision")) for item in snapshot["jobs"]])
        return report

    def choose_place(self, field, names):
        configured = self.case_config[field]
        # 起点填 Default 时按工作台的候选顺序取第一项。
        if field == "通勤起点" and configured == "Default":
            options = names[:1]
        # 留空时保留全部候选，只有候选恰好一个才允许自动确认。
        elif not configured:
            options = names
        else:
            # 填写具体地点时必须按名称唯一匹配，重名时交给测试人员检查配置。
            options = [name for name in names if name == configured]
        self.assert_err(f"按配置「{configured or '留空'}」选出唯一{field}", 1, len(options))
        name = options[0]
        # 工作台要求以从 1 开始的文本编号提交选项，报告中则展示地点名称。
        return str(names.index(name) + 1), name

    def confirm_route(self, run_path, run):
        # 确认等待输入的是本场评估，再取得本轮输入 ID，防止把地址提交到别的运行。
        self.assert_err("通勤确认属于当前运行", self.run_id, run.get("id"))
        input_id = (run.get("pending_input") or {}).get("id")
        self.assert_err("通勤确认提供了本轮输入 ID", True, bool(input_id))
        try:
            place_choices = run["result"]["data"].get("place_choices", {})
            start_number, start_name = self.choose_place("通勤起点", place_choices.get("start", []))
            end_number, end_name = self.choose_place("通勤终点", place_choices.get("end", []))
            # 在报告中记录实际选择的路线，便于复核 Default 这次选中了哪里。
            self.log.info(f"确认通勤：{start_name} → {end_name}")
            answer = {"start": start_number, "end": end_number}
            # 带上本轮输入 ID 和起终点编号，提交后核对工作台是否继续处理同一场评估。
            updated = self.request("POST", run_path + "/input", {"input_id": input_id, "decision": "answer", "data": answer})
            self.assert_err("提交通勤选择后续接原运行", self.run_id, updated.get("id"))
            return updated
        except (StopCase, ValueError, httpx.HTTPError):
            # 选址或续接失败后，只结束仍在等同一条输入的运行；请求超限时停止发送请求。
            if not self.stop_batch:
                current_run = self.request("GET", run_path)
                if current_run["status"] == "needs_input" and current_run["pending_input"]["id"] == input_id:
                    self.request("POST", run_path + "/input", {"input_id": input_id, "decision": "finish"})
            raise
