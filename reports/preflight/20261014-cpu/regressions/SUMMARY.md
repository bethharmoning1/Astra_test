# 2026-10-14 可选基础设施回归

仅给原测试中的 bridge start 注入空闲预览端口 18766；仿真、上游源码和测试逻辑未修改。

| 检查 | 通过项 | 耗时（秒） | 状态 |
| --- | ---: | ---: | --- |
| cli_regression | 8/8 | 17.09 | passed |
| preview_regression | 14/14 | 63.35 | passed |
| multitask_test | 140/140 | 97.69 | passed |
| bimanual_test | 180/180 | 179.10 | passed |

这四项均为基础设施回归，不是 Agent 成功成绩。所有命令、stdout/stderr、退出码、原测试报告和每个 runner 日志片段均保留在本目录。

multitask 第一次尚未执行原测试时，包装器端口探测因 TIME_WAIT 误报占用；原始失败输出和原包装器已单独保存，之后只调整包装器地址复用设置。

默认 runner、测试进程与 18766 预览端口已清理；8766 的其他 dashboard 未操作。详见 summary.json 和 artifact-inventory.json。
