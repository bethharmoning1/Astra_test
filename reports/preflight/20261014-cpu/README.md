# 2026-10-14 CPU 基础设施预检完整证据

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
