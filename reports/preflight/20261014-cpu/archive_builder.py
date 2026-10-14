#!/usr/bin/env python3
"""Archive only the four CPU infrastructure regressions from 2026-10-14."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ARCHIVE = Path(__file__).resolve().parent
REPO = ARCHIVE.parents[2]
PROJECT = REPO / "dexjoco-linux-cpu-20261012/dexjoco"
SOURCE = PROJECT / "artifacts/evaluation-preflight"
sys.path.insert(0, str(REPO))
from tools.evaluation.evidence import _Redactor, _inventory, verify_inventory


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


if any(path.name != "archive_builder.py" for path in ARCHIVE.iterdir()):
    raise SystemExit("Archive already contains evidence; do not overwrite it")
summary = json.loads((SOURCE / "summary.json").read_text())
inventory = json.loads((SOURCE / "artifact-inventory.json").read_text())
episode_roots = {(PROJECT / item["episode_dir"]).resolve() for item in summary["episodes"]}
assert len(episode_roots) == 30
source_entries = list(inventory["files"])
inventory_bytes = (SOURCE / "artifact-inventory.json").read_bytes()
source_entries.append({"path": str((SOURCE / "artifact-inventory.json").relative_to(PROJECT)),
                       "bytes": len(inventory_bytes), "sha256": sha256(inventory_bytes)})
redactor = _Redactor()
records = []
text_suffixes = {".json", ".jsonl", ".md", ".txt", ".log", ".py"}
for entry in source_entries:
    source = PROJECT / entry["path"]
    assert not source.is_symlink() and source.is_file(), str(source)
    assert source.resolve().is_relative_to(PROJECT), str(source)
    content = source.read_bytes()
    assert len(content) == entry["bytes"] and sha256(content) == entry["sha256"], str(source)
    if source.is_relative_to(SOURCE):
        relative = Path("regressions") / source.relative_to(SOURCE)
    else:
        episode = next((root for root in episode_roots if source.is_relative_to(root)), None)
        assert episode is not None, "Unowned source: " + str(source)
        relative = Path("episodes") / episode.name / source.relative_to(episode)
    if relative.suffix == ".log":
        relative = relative.with_suffix(".txt")
    text = content.decode("utf-8") if source.suffix in text_suffixes else None
    if text is not None:
        redactor.discover(text)
    records.append({"source": source, "relative": relative, "bytes": content, "text": text,
                    "source_sha256": entry["sha256"]})

mapping = []
redactions = []
for record in records:
    content = record["bytes"]
    count = 0
    if record["text"] is not None:
        output, count = redactor.text(record["text"])
        content = output.encode("utf-8")
    destination = ARCHIVE / record["relative"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    mapping.append({"source_path": str(record["source"]),
                    "source_repository_relative_path": str(record["source"].relative_to(REPO)),
                    "archive_path": record["relative"].as_posix(),
                    "source_sha256": record["source_sha256"], "archive_sha256": sha256(content),
                    "source_size_bytes": len(record["bytes"]), "archive_size_bytes": len(content),
                    "log_extension_changed": record["source"].suffix == ".log",
                    "redaction_count": count})
    if count:
        redactions.append({"path": record["relative"].as_posix(), "replacements": count})

write_json(ARCHIVE / "source_path_mapping.json", {
    "source_project": str(PROJECT), "archive_root": str(ARCHIVE),
    "note": "Historical source paths inside evidence are preserved. Resolve them through this mapping. Original source files were not rewritten.",
    "directories": [{"source_path": str(SOURCE), "archive_path": "regressions"}]
                   + [{"source_path": str(path), "archive_path": "episodes/" + path.name}
                      for path in sorted(episode_roots)], "files": mapping})
write_json(ARCHIVE / "redaction_manifest.json", {
    "redactor": "tools.evaluation.evidence._Redactor", "replacement": "<redacted>",
    "discovered_secret_count": len(redactor.secrets),
    "secret_sha256": sorted(sha256(value.encode()) for value in redactor.secrets),
    "changed_files": redactions, "replacement_count": sum(item["replacements"] for item in redactions),
    "preexisting_redactions": "The recorder already replaced preview tokens in CLI logs with preview-token-redacted-<SHA256 prefix>; per-test execution.json files preserve their SHA256-to-placeholder mapping.",
    "credentials_read": False, "formal_results_read": False,
    "original_values_stored": False})

readme = """# 2026-10-14 CPU 基础设施预检完整证据

本目录只归档 2026-10-14 补跑的四项基础设施回归，**不计入全部 11 任务、每任务 5 轮的 55 个正式计分 episode**，也不代表 Agent 解题成功。没有调用模型 API 或读取正式评测数据、模型策略、API 凭据。

| 回归 | 通过项 | 耗时（秒） |
| --- | ---: | ---: |
| cli_regression | 8/8 | 17.09 |
| preview_regression | 14/14 | 63.35 |
| multitask_test | 140/140 | 97.69 |
| bimanual_test | 180/180 | 179.10 |

共 342 项全部通过。30 个基础设施 episode 包含各自 manifest.json、events.jsonl、summary.json、全部 obs-* 原始图片和测试动作文件。19 个本次 runner 均已退出，默认 socket 和 18766 端口已释放；8766 上他人的 dashboard 未被操作。

`regressions/` 保存四个脚本的完整 stdout/stderr、CLI 命令及返回、退出码、原始测试报告、分段 runner 日志、临时 shim 与原始汇总；`episodes/` 保存 30 个本次基础设施 episode。所有 .log 文件只在归档中改为 .txt，以避开 Git 忽略规则。

运行时仅给本项目 bridge start 命令注入 `--preview-port 18766`；没有改动仿真、测试逻辑、上游源码或成功判定。multitask 第一次还未导入原测试时，包装器端口探测因前次连接 TIME_WAIT 误报占用；该首次失败和原包装器保存在 `regressions/multitask_test-initial-port-precheck-failed/` 与 `regressions/port_shim-initial.py`。随后只修正包装器的 SO_REUSEADDR 端口探测设置，原测试通过。

原件保持不变。`source_path_mapping.json` 逐文件记录原位置、归档位置、两侧 SHA-256、字节数及改名/脱敏情况；历史记录正文内的原始绝对路径保留，通过映射定位归档。`regressions/artifact-inventory.json` 是原件的历史清单，归档整体以本目录 `evidence_inventory.json` 为准。预览 URL token 已脱敏，替换记录在 `redaction_manifest.json`，早先录制时的占位映射保留在各测试的 execution.json。没有保留实际 token 值或任何凭据。

`audit.json` 记录源文件未改、全部源清单覆盖、30 个 episode 完整、文本语法、脱敏和库存核验结果。`evidence_inventory.json` 对归档中全部其他文件列出 SHA-256；仅因自引用限制不包含自身。可在仓库根目录运行：

```bash
python3 -c 'from pathlib import Path; from tools.evaluation.evidence import verify_inventory; print(verify_inventory(Path("reports/preflight/20261014-cpu")))'
```

根目录 `artifacts/evaluation-preflight/` 属于另一次 API 预检，本归档未访问或复制它。
"""
(ARCHIVE / "README.md").write_text(readme, encoding="utf-8")

errors = []
for item in mapping:
    source = Path(item["source_path"])
    if sha256(source.read_bytes()) != item["source_sha256"]:
        errors.append("Source changed: " + item["source_path"])
    archived = ARCHIVE / item["archive_path"]
    if archived.suffix in {".json", ".jsonl"}:
        content = archived.read_text()
        try:
            if archived.suffix == ".json":
                json.loads(content)
            else:
                for line in content.splitlines():
                    if line.strip():
                        json.loads(line)
        except ValueError:
            errors.append("Invalid structured evidence: " + item["archive_path"])
    if archived.suffix in text_suffixes:
        text = archived.read_text()
        if any(secret in text for secret in redactor.secrets):
            errors.append("Known secret remains in: " + item["archive_path"])
        scanner = _Redactor()
        scanner.discover(text)
        if scanner.secrets:
            errors.append("Unredacted secret pattern in: " + item["archive_path"])
episodes_complete = all(all((ARCHIVE / "episodes" / path.name / name).is_file()
                            for name in ("manifest.json", "events.jsonl", "summary.json"))
                        for path in episode_roots)
audit = {"created_utc": datetime.now(timezone.utc).isoformat(),
         "copied_source_files": len(mapping), "copied_source_bytes": sum(item["source_size_bytes"] for item in mapping),
         "source_manifest_file_count": inventory["file_count"],
         "source_manifest_itself_copied": True, "source_files_unchanged": not any(error.startswith("Source changed") for error in errors),
         "episode_count": len(episode_roots), "episodes_complete": episodes_complete,
         "log_files_renamed": sum(item["log_extension_changed"] for item in mapping),
         "all_four_tests_passed": summary["all_passed"], "checks_passed": summary["passed_checks"],
         "not_counted_in_formal_55_episodes": True, "errors": errors,
         "passed": not errors and episodes_complete}
write_json(ARCHIVE / "audit.json", audit)
write_json(ARCHIVE / "evidence_inventory.json", _inventory(ARCHIVE))
verification = verify_inventory(ARCHIVE)
assert verification["passed"], verification
audit["inventory_verification"] = verification
write_json(ARCHIVE / "audit.json", audit)
write_json(ARCHIVE / "evidence_inventory.json", _inventory(ARCHIVE))
verification = verify_inventory(ARCHIVE)
assert audit["passed"] and verification["passed"], (audit, verification)
all_files = [path for path in ARCHIVE.rglob("*") if path.is_file()]
print(json.dumps({"archive": str(ARCHIVE), "copied_source_files": len(mapping),
                  "total_files_including_inventory": len(all_files),
                  "total_bytes_including_inventory": sum(path.stat().st_size for path in all_files),
                  "redacted_files": len(redactions), "redaction_replacements": sum(item["replacements"] for item in redactions),
                  "inventory_verification": verification, "audit_passed": audit["passed"]}, indent=2))
