# Evaluation batch 20261014-astra-medium

固定初始场景、环境 seed=0、每任务五轮、跨轮上下文隔离。五轮为小样本稳定性观察，不代表跨场景泛化或官方 benchmark 成绩。

计分进度：3/55；成功 1 轮；实际尝试 3 次。
评测状态：未完成正式评测。证据状态：已归档并通过核验。

不足五轮的任务不发布正式任务成功率；全部 11 个任务完成前不发布正式总体成功率。

| task | 计划 | 有效完成 | 成功 | 失败 | 中断 | 缺少 | 任务成功率 | 状态 | 证据 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| click_mouse | 5 | 1 | 1 | 0 | 0 | 4 | 未完成 | 未完成正式评测 | [click_mouse](episodes/click_mouse) |
| fold_glasses | 5 | 1 | 0 | 1 | 0 | 4 | 未完成 | 未完成正式评测 | [fold_glasses](episodes/fold_glasses) |
| hammer_nail | 5 | 1 | 0 | 1 | 0 | 4 | 未完成 | 未完成正式评测 | [hammer_nail](episodes/hammer_nail) |
| pick_bucket | 5 | 0 | 0 | 0 | 0 | 5 | 未完成 | 未完成正式评测 | 尚无 |
| pinch_tongs | 5 | 0 | 0 | 0 | 0 | 5 | 未完成 | 未完成正式评测 | 尚无 |
| water_plant | 5 | 0 | 0 | 0 | 0 | 5 | 未完成 | 未完成正式评测 | 尚无 |
| bimanual_assembly | 5 | 0 | 0 | 0 | 0 | 5 | 未完成 | 未完成正式评测 | 尚无 |
| bimanual_hanoi | 5 | 0 | 0 | 0 | 0 | 5 | 未完成 | 未完成正式评测 | 尚无 |
| bimanual_microwave_cook | 5 | 0 | 0 | 0 | 0 | 5 | 未完成 | 未完成正式评测 | 尚无 |
| bimanual_photograph | 5 | 0 | 0 | 0 | 0 | 5 | 未完成 | 未完成正式评测 | 尚无 |
| bimanual_unlock_ipad | 5 | 0 | 0 | 0 | 0 | 5 | 未完成 | 未完成正式评测 | 尚无 |

每次尝试（包括所有中断及补测）：

| task / trial | attempt | 计分 | 状态 | succeed | steps | action_calls | reason | 补测关联 | 核验 |
| --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- |
| click_mouse / 1 | [trial01-attempt01](episodes/click_mouse/trial01-attempt01) | true | completed | True | 694 | 24 | official_success |  | passed |
| hammer_nail / 1 | [trial01-attempt01](episodes/hammer_nail/trial01-attempt01) | true | completed | False | 1000 | 34 | native_step_limit |  | passed |
| fold_glasses / 1 | [trial01-attempt01](episodes/fold_glasses/trial01-attempt01) | true | completed | False | 1200 | 41 | native_step_limit |  | passed |

未完成任务及原因：

- click_mouse：缺少 4 轮；official_success。
- fold_glasses：缺少 4 轮；native_step_limit。
- hammer_nail：缺少 4 轮；native_step_limit。
- pick_bucket：缺少 5 轮；not started。
- pinch_tongs：缺少 5 轮；not started。
- water_plant：缺少 5 轮；not started。
- bimanual_assembly：缺少 5 轮；not started。
- bimanual_hanoi：缺少 5 轮；not started。
- bimanual_microwave_cook：缺少 5 轮；not started。
- bimanual_photograph：缺少 5 轮；not started。
- bimanual_unlock_ipad：缺少 5 轮；not started。

可获取性与核验边界：

- Only exportable public model inputs, outputs and tool records are archived. Private/internal model reasoning is unavailable; this is not a complete model conversation.
- The requested model and settings can be recorded, but the backend model version has not been independently verified.
- The archive can verify recorded commands and declared recorder counts; it cannot independently prove that a command was never omitted by the recorder.

[批次配置](batch_config.json) · [逐任务 CSV](task_results.csv) · [逐尝试 CSV](episode_results.csv) · [全部证据 SHA-256 清单](evidence_inventory.json)

Git 提交与推送由外部调度者逐次执行；本报告生成本身不证明远程仓库已更新。
