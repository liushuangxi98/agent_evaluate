"""登记每个用例 ID 应交给哪个场景执行。"""
# 执行清单里的两个 ID 对应同一个岗位评估流程，各自的输入和预期在 Excel 的不同行。
CASE_SCRIPTS = {
    "JOB-MATCH-001": "cases.test_job_match",
    "JOB-MATCH-002": "cases.test_job_match",
}
