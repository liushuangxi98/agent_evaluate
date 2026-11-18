"""岗位匹配场景：每条用例创建一个 Case 实例。"""

import os
import time

import httpx


def choose_place(place_names, place_name, is_start=False):
    # 接口用从 1 开始的字符串编号，日志用地点名称，选中时一并返回。
    if place_names and is_start and place_name == "Default":
        return "1", place_names[0]
    if len(place_names) == 1 and not place_name:
        return "1", place_names[0]
    if place_names.count(place_name) == 1:
        return str(place_names.index(place_name) + 1), place_name
    raise ValueError(f"无法唯一选择地点 {place_name!r}，实际候选 {place_names}")


class Case:
    def __init__(self, case_record, log):
        self.case_record = case_record
        self.case_config = case_record["case"]
        self.log = log
        # 一条用例共用一个 HTTP 客户端；main 会在 finally 中调用 teardown。
        base_url = os.environ.get("WORKBENCH_URL", "http://127.0.0.1:8000")
        self.api = httpx.Client(base_url=base_url, timeout=20, trust_env=False)

    def teardown(self):
        self.api.close()

    def check(self, name, actual, expected):
        # 每个检查项只记录比较结果，整条用例执行完再汇总失败字段。
        self.case_record["checks"].append({
            "字段": name, "实际": actual, "预期": expected, "通过": actual == expected,
        })

    def run(self):
        report = self.evaluate()
        self.check_report(report)
        # 完成全部检查后一次返回，报告中能看到所有不符合的字段。
        failed_fields = [item["字段"] for item in self.case_record["checks"] if not item["通过"]]
        if failed_fields:
            return {"status": "FAIL", "detail": "预期不符：" + "、".join(failed_fields)}
        return {"status": "PASS", "detail": "所有配置的验收条件均满足"}

    def check_report(self, report):
        case_config = self.case_config
        report_jobs = report["data"]["jobs"]
        self.check("报告岗位", [job["job_id"] for job in report_jobs], [case_config["岗位ID"]])
        # 先验证报告归属，再比较本用例设置的岗位事实。
        self.check("报告关联运行", report["run_id"], self.case_record["run_id"])
        self.check("报告类型", report["kind"], "evaluation")
        if len(report_jobs) == 1:
            job = report_jobs[0]
            matches = job["matches"]
            keyword = case_config["要求关键词"]
            expected_score = case_config["预期匹配分"]
            # 只验配置中的事实；空白跳过，文字 null 代表明确期待空值。
            if case_config["预期必备项数"] is not None:
                self.check("必备项数", len(matches), case_config["预期必备项数"])
            # 数字零必须参加断言，不能用真假值过滤。
            if expected_score is not None:
                self.check("匹配分", job["match_score"], None if expected_score == "null" else expected_score)
            if keyword:
                # 唯一命中才能验该项，避免另一条无关的“符合”导致误通过。
                actual_labels = [match["assessment"] for match in matches if keyword in match["requirement"]]
                self.check(keyword, actual_labels, [case_config["预期匹配判断"]])

    def request(self, method, api_path, body=None):
        response = self.api.request(method, api_path, json=body)
        # 413 停止整批，其他错误由入口记为 ERROR。
        if response.is_error:
            if response.status_code == 413:
                self.case_record["stop_batch"] = True
            self.log.error("工作台接口 %s %s 返回 HTTP %s：%s", method, api_path, response.status_code, response.text[:1000])
        response.raise_for_status()
        return response.json()

    def evaluate(self):
        """用当前已确认的画像评估一个已有岗位，返回本次运行保存的报告。"""
        case_record = self.case_record
        job_id = self.case_config["岗位ID"]
        self.log.info("检查工作台环境和已有资料：%s", self.api.base_url)
        # 先只读确认环境、画像、岗位。测试不会上传资料或覆盖画像。
        if self.request("GET", "/api/health")["mode"] != "real":
            raise ValueError("请使用真实模式工作台")
        profile = self.request("GET", "/api/profile")
        if not profile["confirmed"]:
            raise ValueError("当前画像尚未确认")
        # 读取已有岗位的版本；之后用最终快照核对其身份。
        job = self.request("GET", f"/api/jobs/{job_id}")
        case_record.update(profile_revision=profile["revision"], job_revision=job["revision"])
        # POST 仅发一次；之后轮询只读的状态接口。
        run = self.request("POST", "/api/runs", {"action": "evaluate", "input": {"job_ids": [job_id]}})
        run_path = "/api/runs/" + run["id"]
        case_record["run_id"] = run["id"]
        self.log.info("评估已启动：岗位 %s，运行 ID %s", job_id, run["id"])
        deadline = time.monotonic() + 900
        while run["status"] in {"running", "needs_input"}:
            # 本地超时后保留 run_id，停止批次，避免创建第二个活动。
            if time.monotonic() >= deadline:
                case_record["stop_batch"] = True
                raise TimeoutError(f"运行仍在执行，请查看 {run_path}")
            if run["status"] == "running":
                time.sleep(1)
                run = self.request("GET", run_path)
                continue
            run = self.confirm_route(run_path, run)

        # 运行失败也会返回 HTTP 200，必须检查业务状态和实际错误。
        if run["status"] != "completed" or not run["report_id"]:
            detail = str(run["result"])
            if any(marker in detail for marker in ("413", "TOO_LARGE", "超过大小限制")):
                case_record["stop_batch"] = True
            error_data = (run["result"] or {}).get("data", {})
            raise RuntimeError(f"评估未完成（{run['status']}）：{error_data.get('message') or detail}")
        # 必须能读回持久化的报告，单独看到运行完成还不足以评分。
        report = self.request("GET", "/api/reports/" + run["report_id"])
        # 完整报告仍在工作台，评测报告保留 ID 供核对。
        case_record["report_id"] = run["report_id"]
        self.log.info("已读取工作台报告 %s，开始核对预期值", run["report_id"])
        # 校验最终报告对应启动时的画像版本；运行环境变动应与模型断言失败区分。
        if report["snapshot"]["profile"]["revision"] != profile["revision"]:
            raise ValueError("画像版本在启动期间发生变化")
        if [(job_snapshot["id"], job_snapshot["revision"]) for job_snapshot in report["snapshot"]["jobs"]] != [(job_id, job["revision"])]:
            raise ValueError("报告中的岗位或版本与本用例不一致")
        return report

    def confirm_route(self, run_path, run):
        # 地址确认是续接原运行，选择失败时只结束本用例自己的等待。
        # 选择编号从 1 开始；用本轮响应中的候选名称核对 Excel。
        place_choices = run["result"]["data"]["place_choices"]
        input_id = run["pending_input"]["id"]
        try:
            start_number, start_name = choose_place(place_choices["start"], self.case_config["通勤起点"], is_start=True)
            end_number, end_name = choose_place(place_choices["end"], self.case_config["通勤终点"])
            self.case_record["route"] = f"{start_name} → {end_name}"
            self.log.info("确认通勤：%s", self.case_record["route"])
            # 页面按文本编号提交地点。数字类型会被工作台的候选校验拒绝。
            answer = {"start": start_number, "end": end_number}
            return self.request("POST", run_path + "/input", {"input_id": input_id, "decision": "answer", "data": answer})
        except (ValueError, httpx.HTTPError):
            # 只结束仍在等待同一 input_id 的本场运行。超限时不再发出请求。
            if not self.case_record.get("stop_batch"):
                current_run = self.request("GET", run_path)
                if current_run["status"] == "needs_input" and current_run["pending_input"]["id"] == input_id:
                    self.request("POST", run_path + "/input", {"input_id": input_id, "decision": "finish"})
            raise
