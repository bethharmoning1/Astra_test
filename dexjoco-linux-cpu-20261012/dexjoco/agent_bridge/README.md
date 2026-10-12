# DexJoCo agent_bridge：Linux CPU 本机部署

先读 `MIGRATION.md` 完成环境安装，再读 `CODEX_AGENT.md` 做视觉闭环。

```bash
export DEXJOCO_ROOT="${DEXJOCO_ROOT:-$HOME/dexjoco}"
cd "$DEXJOCO_ROOT"
bash agent_bridge/run_cpu.sh tasks
```

项目根目录默认是当前 Linux 用户的 `$HOME/dexjoco`；其他部署位置可先设置 `DEXJOCO_ROOT`。启动脚本按自身文件位置识别项目根目录。

使用 run_cpu.sh，不沿用原 bridge 的 GPU/EGL 默认环境；测试前 source env_cpu.sh。
核心 bridge/任务/动力学/奖励代码未改，只新增 CPU 入口、安装检查和适配文档。
支持 6 单臂 + 5 双臂，动作和相机以 contract 为准。Unix socket 传动作，loopback/token 网页只读。
历史 artifacts 不在包里，接口验收不等于 Agent 成功成绩；目标 CPU 渲染、全任务和性能须实测。
