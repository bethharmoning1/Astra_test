# 失败原因诊断（完成后只读统计）

本批次全部 55 个有效计分 episode 已完成，成功 5/55 = 9.09%。固定初始场景、环境 seed=0、每任务五轮、跨轮上下文隔离。以下分析由外部汇总程序执行，未提供给任何后续执行模型；未修改冻结配置或策略。

## 可以从证据直接确定的原因

- 40 轮耗尽任务原生步数但未达到官方成功条件；这表示动作过程未及时完成任务，不能仅由超时标签推断具体抓取失败原因。
- 8 轮在上限前由环境终止且 succeed=false：hanoi 3 轮、unlock_ipad 5 轮。iPad 第 2/3 轮分别仅 89/104 步（3/4 次动作）即结束，增加总步数无法修复这种提前终止。公开日志没有更细终止原因，不能据此确定为碰撞、掉落或具体谓词。
- 2 轮由模型主动结束：pick_bucket 第 4 轮 930 步、water_plant 第 3 轮 810 步，均按失败计分。
- 已完成计分轮共记录 7 次 API 错误，均恢复后继续，没有 api_error 结局。仿真在模型推理/等待期间暂停，API 等待耗时不会消耗环境步数。
- 动作校验拒绝共 4 次（fold_glasses 第 5 轮 2 次，pick_bucket 第 2 轮及 pinch_tongs 第 1 轮各 1 次），不支持“多数动作因接口校验而未执行”的解释。每轮启动前的无 runner status 记录共 55 条，是预期检查结果。

## 值得单独验证的控制问题

- 已接受动作请求中 1803/1961 段使用 30 步上限。1000 步任务通常仅进行约 34 次动作决策，接触过程中的修正机会有限。这是日志支持的控制特征，尚未通过对照实验确认因果；30 步是模型选择的上限，并非工具强制的固定长度。
- 双臂任务全部使用逐臂 move/hold，没有使用可同时控制双臂的 act。装配等操作需要协调；逐臂决策可能限制同步能力，但这一点仍需新批次对照验证。
- 当前控制依赖允许的 RGB 拼图与机器人本体状态，没有物体真值、成功示范或预制抓取动作。空间定位、手指接触、稳定抓取和多阶段操作之间，现有统计不足以唯一定位哪一项失效。需要对官方图像与动作时间线做任务级复核，不能把 0/5 直接解释成模型完全没有该能力。

## 结果与证据边界

- 55 轮计分记录均通过 summary/events、初始状态、命令、模型图片与文件哈希审计。原有 3 条进程消失中断保留全部能取得的证据，缺失官方 summary，标为 incomplete；补测分别对应 trial03-attempt02（pick_bucket、pinch_tongs）及 trial02-attempt02（water_plant）。不能把这三条中断的证据称为完整。
- API 请求模型名和推理设置可核验为 gpt-6-astra/medium；后端具体版本无法独立确认。隐藏内部推理不可解读或独立导出。
- 不改变本批次规则或重挑成绩。若要检验更短接触动作、双臂同步控制或改进提示的效果，应冻结新方案后新建批次；本次未运行这些对照。

| task | 成功/计分 | 成功率 | 结束原因分布 |
| --- | ---: | ---: | --- |
| click_mouse | 4/5 | 80% | official_success: 4, native_step_limit: 1 |
| fold_glasses | 1/5 | 20% | native_step_limit: 4, official_success: 1 |
| hammer_nail | 0/5 | 0% | native_step_limit: 5 |
| pick_bucket | 0/5 | 0% | native_step_limit: 4, agent_gave_up: 1 |
| pinch_tongs | 0/5 | 0% | native_step_limit: 5 |
| water_plant | 0/5 | 0% | native_step_limit: 4, agent_gave_up: 1 |
| bimanual_assembly | 0/5 | 0% | native_step_limit: 5 |
| bimanual_hanoi | 0/5 | 0% | official_failure: 3, native_step_limit: 2 |
| bimanual_microwave_cook | 0/5 | 0% | native_step_limit: 5 |
| bimanual_photograph | 0/5 | 0% | native_step_limit: 5 |
| bimanual_unlock_ipad | 0/5 | 0% | official_failure: 5 |

[统计数据及逐轮来源](diagnosis.json) · [正式报告](report.md) · [逐轮结果](episode_results.csv)
