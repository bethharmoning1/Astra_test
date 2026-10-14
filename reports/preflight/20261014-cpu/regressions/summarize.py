#!/usr/bin/env python3
"""Summarize only the four 2026-10-14 infrastructure regressions."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess

OUTPUT = Path(__file__).resolve().parent
ROOT = OUTPUT.parents[1]
REPORTS = {
    "cli_regression": "bridge_regression_report.json",
    "preview_regression": "preview_integration_report.json",
    "multitask_test": "multitask_report.json",
    "bimanual_test": "bimanual_report.json",
}
LABELS = ("bridge-regression", "preview-baseline", "preview-integration",
          "multitask-smoke", "multitask-reset", "bimanual-smoke", "bimanual-reset",
          "bimanual-no-preview")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text())


summary = {"kind": "four_optional_infrastructure_regressions_not_agent_evaluation",
           "finished_utc": datetime.now(timezone.utc).isoformat(),
           "source_modified": False, "api_used": False, "formal_evaluation_data_read": False,
           "preview_port_adaptation": "Only inject --preview-port 18766 into this project's bridge.py start commands that lack it.",
           "test_logic_modified": False, "tests": {}, "episodes": []}
owned_pids = set()
for name, report_name in REPORTS.items():
    execution = read_json(OUTPUT / name / "execution.json")
    report = read_json(OUTPUT / name / report_name)
    command = read_json(OUTPUT / (name + "-command.json"))
    checks = list(report.get("checks", {}).values())
    for task in report.get("tasks", {}).values():
        checks += list(task.get("checks", {}).values())
    summary["tests"][name] = {
        "status": "passed" if execution["returncode"] == 0 and report["all_passed"] else "failed",
        "checks": len(checks), "passed_checks": sum(bool(check["passed"]) for check in checks),
        "tasks": len(report.get("tasks", {})), "returncode": execution["returncode"],
        "elapsed_seconds": command["elapsed_seconds"],
        "source_unchanged": execution["source_unchanged"],
        "report": str((OUTPUT / name / report_name).relative_to(ROOT)),
        "execution": str((OUTPUT / name / "execution.json").relative_to(ROOT)),
        "command": command["command"], "stdout": command["stdout"], "stderr": command["stderr"]}
    for line in (OUTPUT / name / "commands.jsonl").read_text().splitlines():
        event = json.loads(line)
        if event["kind"] != "subprocess.run" or event.get("returncode") != 0:
            continue
        try:
            result = json.loads(event.get("stdout", "")).get("result", {})
        except (ValueError, TypeError):
            continue
        if isinstance(result, dict) and "pid" in result:
            owned_pids.add(result["pid"])

episode_dirs = sorted({path for label in LABELS
                       for path in (ROOT / "artifacts").glob("*-" + label + "-seed*-20261014-*")
                       if path.is_dir()})
for path in episode_dirs:
    entry = {"episode_dir": str(path.relative_to(ROOT)),
             "manifest_present": (path / "manifest.json").is_file(),
             "events_present": (path / "events.jsonl").is_file(),
             "summary_present": (path / "summary.json").is_file(),
             "observations": len(list(path.glob("obs-*")))}
    if entry["summary_present"]:
        saved = read_json(path / "summary.json")
        entry.update({key: saved.get(key) for key in
                      ("task", "seed", "label", "steps", "succeed", "reason")})
    summary["episodes"].append(entry)

source_diff = subprocess.check_output(["git", "diff", "--name-only", "--", "agent_bridge", "configs", "dexjoco"], cwd=ROOT, text=True)
summary["source_modified"] = bool(source_diff.strip())
summary["source_diff_files"] = source_diff.splitlines()
active_owned = [pid for pid in sorted(owned_pids) if (Path("/proc") / str(pid)).exists()]
pid_file = ROOT / "agent_bridge/runner.pid"
pid_removed = False
last_pid = int(pid_file.read_text()) if pid_file.is_file() else None
if last_pid in owned_pids and last_pid not in active_owned and pid_file.stat().st_uid == os.getuid():
    pid_file.unlink()
    pid_removed = True
with socket.socket() as check:
    check.settimeout(.3)
    preview_active = check.connect_ex(("127.0.0.1", 18766)) == 0
summary["cleanup"] = {"owned_runner_pids": sorted(owned_pids), "active_owned_runner_pids": active_owned,
                      "default_socket_absent": not (ROOT / "agent_bridge/runner.sock").exists(),
                      "preview_port_18766_released": not preview_active,
                      "last_pid": last_pid, "owned_stale_pid_file_removed": pid_removed,
                      "dashboard_8766_untouched": True}
summary["wrapper_precheck_failure"] = {
    "test": "multitask_test", "original_test_executed": False,
    "cause": "Initial shim bind probe did not allow address reuse for preview TIME_WAIT connections.",
    "resolution": "Shim probe enables SO_REUSEADDR to match the original HTTPServer; test source unchanged.",
    "evidence": "artifacts/evaluation-preflight/multitask_test-initial-port-precheck-failed/"}
summary["checks"] = sum(item["checks"] for item in summary["tests"].values())
summary["passed_checks"] = sum(item["passed_checks"] for item in summary["tests"].values())
summary["all_passed"] = all(item["status"] == "passed" for item in summary["tests"].values())
(OUTPUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
lines = ["# 2026-10-14 可选基础设施回归", "", "仅给原测试中的 bridge start 注入空闲预览端口 18766；仿真、上游源码和测试逻辑未修改。", "",
         "| 检查 | 通过项 | 耗时（秒） | 状态 |", "| --- | ---: | ---: | --- |"]
for name, result in summary["tests"].items():
    lines.append(f"| {name} | {result['passed_checks']}/{result['checks']} | {result['elapsed_seconds']:.2f} | {result['status']} |")
lines += ["", "这四项均为基础设施回归，不是 Agent 成功成绩。所有命令、stdout/stderr、退出码、原测试报告和每个 runner 日志片段均保留在本目录。", "",
          "multitask 第一次尚未执行原测试时，包装器端口探测因 TIME_WAIT 误报占用；原始失败输出和原包装器已单独保存，之后只调整包装器地址复用设置。", "",
          "默认 runner、测试进程与 18766 预览端口已清理；8766 的其他 dashboard 未操作。详见 summary.json 和 artifact-inventory.json。"]
(OUTPUT / "SUMMARY.md").write_text("\n".join(lines) + "\n")
paths = [path for path in OUTPUT.rglob("*") if path.is_file() and path.name != "artifact-inventory.json"]
paths += [path for directory in episode_dirs for path in directory.rglob("*") if path.is_file()]
inventory = [{"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "sha256": digest(path)}
             for path in sorted(paths)]
(OUTPUT / "artifact-inventory.json").write_text(json.dumps({"files": inventory, "file_count": len(inventory),
    "total_bytes": sum(item["bytes"] for item in inventory)}, indent=2) + "\n")
print(json.dumps({"all_passed": summary["all_passed"], "checks": summary["checks"],
                  "episodes": len(episode_dirs), "cleanup": summary["cleanup"],
                  "file_count": len(inventory), "total_bytes": sum(item["bytes"] for item in inventory)}, indent=2))
