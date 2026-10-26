# GPT-6-Astra xhigh：OneAPI 实验记录

本归档保存 2026-10-22 至 2026-10-26 两个 OneAPI 批次的结果、配置、调度和审计记录。
**覆盖完成 110/110：历史 69 个结果全部保留，续跑补齐 41 个；5 个重复计分结果单列。**
共归档 203 次尝试，其中 115 次计分（含上述重复），88 次未计分。

| 模式 | 完成位置 | 成功 | 失败 | 已有证据审计通过 | 有审计异常 |
| --- | ---: | ---: | ---: | ---: | ---: |
| assisted | 55/55 | 26 | 29 | 51 | 4 |
| bare | 55/55 | 7 | 48 | 54 | 1 |

合计 33 个成功、77 个失败。这是跨配置的结果计数，**不作为同一配置的正式成功率**。
原批次未显式启用 compact，续跑批次设置 `model_auto_compact_token_limit=20000`。两者均请求
`gpt-6-astra / xhigh`、标准 1× 预算、11 个任务、2 种模式、每任务 5 个种子（100000–100004）。
计划初始并发为 16；剩余位置续跑调度记录为 1，并发调整以各批次调度事件为准。
后端实际模型身份未独立验证。早期 baseline / ebill 批次不参与本汇总。

## 结果与去重规则

- [episode_results.csv](episode_results.csv)：110 个保留结果，含来源批次、结果路径、SHA-256 和证据审计标记。
- [task_results.csv](task_results.csv) / [mode_results.csv](mode_results.csv)：任务和模式计数。
- [attempt_results.csv](attempt_results.csv)：全部 203 次尝试及是否计入覆盖；基础设施失败与中断不计分。
- [duplicate_results.csv](duplicate_results.csv)：5 个重复结果及所保留的历史结果路径。
- [summary.json](summary.json)：机器可读汇总，正式成功率保持 `null`。
- [continuation/remaining_selection.json](continuation/remaining_selection.json)：冻结的历史 69 个结果及哈希。

去重键为 `(mode, task, seed)`。对历史成功和失败一视同仁，均保留原成绩；只从续跑批次补入缺失位置。
重复结果不增加完成数，也不替换较差的历史成绩。审计异常单独标注，不在本次整理中改写或重算任务结果。

## 审计限制

两个批次的结果结构校验 `summary.json.audit.ok` 均为 `true`，但更严格的 `evidence_audit.json`
均为 `audit_ok_for_available_evidence=false`。两者检查范围不同；不能把结构校验通过解释为证据审计全部通过。
110 个保留结果中，105 个通过现有证据审计，5 个存在 `script_snapshot_reference_mismatch`：

| 模式 | 任务 | 种子 | 任务结果 | 脚本快照索引行 |
| --- | --- | ---: | --- | ---: |
| assisted | water_plant | 100003 | success | 14 |
| assisted | bimanual_assembly | 100002 | failure | 16 |
| assisted | bimanual_microwave_cook | 100003 | failure | 39 |
| assisted | bimanual_photograph | 100001 | failure | 24 |
| bare | bimanual_microwave_cook | 100001 | failure | 72 |

详情见 [audit_issues.json](audit_issues.json) 与两个批次的 `evidence_audit.json`。
此次归档不修复、不覆盖原始审计，也不宣称重新读取全部原始图片完成了一次新审计。
每个独立批次仍保留完整 110 位置计划且未单独完成，因此原 `report.md` 中的 `pending`、
`formal_complete=false` 以及 `continuation/completion.json` 中较早的 `stopped_incomplete` 状态照原样归档。
跨批次去重后的 110/110 覆盖与这些单批次状态属于不同口径。

## 归档目录

| 批次 | 已计分 | 本汇总采用 | 记录入口 |
| --- | ---: | ---: | --- |
| version2-oneapi16 | 69 | 69 | [原报告](batches/20261022-200600-paired-xhigh-oneapi16/report.md) / [证据审计](batches/20261022-200600-paired-xhigh-oneapi16/evidence_audit.json) |
| version2-oneapi16-compact | 46 | 41 | [原报告](batches/20261023-114334-paired-xhigh-oneapi16-compact/report.md) / [证据审计](batches/20261023-114334-paired-xhigh-oneapi16-compact/evidence_audit.json) |

`batches/` 保留批次计划、原始报告/CSV、环境验收、调度事件和全部尝试的结果、证据清单、
模型会话生命周期、最终判定及容器隔离元数据。`continuation/` 保留去重、调度纠正和覆盖进度。
本次仅提交实验记录；代码、环境、依赖、原始图片、模型事件日志、逐步动作和脚本工作区保留本地。
源文件定位与原始 SHA-256 见 [source_manifest.json](source_manifest.json)。配置中的绝对路径是原机器的历史路径，
其中 `auth_file` 只记录路径，不包含认证文件或密钥。

每轮 `evidence_inventory.json` 列出完整本地证据及其哈希；目录中缺少图片、动作轨迹等是本次按范围导出所致，
不等于实验运行时缺证据。原 `missing_evidence.json` 描述实验当时的完整性。
原报告里的结果目录链接可浏览已导出的元数据，但不能据此在 GitHub 上回放完整视觉/动作轨迹。

## 归档检查

导出文件与来源逐字节核对，历史 69 个结果与冻结选择清单的哈希核对，
并检查 110 个位置唯一、5 个重复结果排除、结果/任务/模式计数一致以及已有审计异常完整保留。
检查记录见 [archive_verification.json](archive_verification.json)。这验证归档过程，不提升实验审计状态。
在本目录可执行 `sha256sum -c SHA256SUMS` 校验此次提交的文件；清单不包含自身。
