# Astra_test：DexJoCo Linux CPU-only 测试项目

本仓库保存 DexJoCo 源码、任务资源和 Codex 操作指南，面向 **Linux CPU-only、本机执行仿真和视觉闭环**。它不是已安装的虚拟环境或容器镜像，不包含原始运行 artifacts。

[2026-10-13 本机验收记录](reports/2026-10-13-cpu-validation.md) 记录了 CPU 构建、37 项单元测试、11 个任务相机验收，以及 GPT-6-Astra medium 在 `click_mouse` 单次尝试中的官方成功（518 步、18 次动作调用）。记录也列出了环境适配和未执行的可选回归；单次尝试不是五轮评测。

## 目录与阅读顺序

- `CODEX_AGENT.md`：可移植的 Agent 操作与评测指南；与项目内同名指南一致。
- `dexjoco-linux-cpu-20261012/dexjoco/`：实际项目根目录，包含 `agent_bridge/`、`configs/` 和可安装的 `dexjoco/` 源码。
- [安装与验收说明](dexjoco-linux-cpu-20261012/dexjoco/agent_bridge/MIGRATION.md)：系统库、Python 环境、OSMesa 渲染和接口验收。
- [Agent 操作指南](CODEX_AGENT.md)：视觉闭环、11 个任务的动作/相机约定，以及固定场景五轮评测规则。

先安装并验收环境，再让可访问目标 Linux 文件系统、终端及观察图片的 Agent 显式读取操作指南。`CODEX_AGENT.md` 不会像 `AGENTS.md` 一样自动加载。

## 在新 Linux 机器上获取项目

```bash
git clone https://github.com/bethharmoning1/Astra_test.git "$HOME/Astra_test"
export DEXJOCO_ROOT="$HOME/Astra_test/dexjoco-linux-cpu-20261012/dexjoco"
cd "$DEXJOCO_ROOT"
```

此时项目已经通过 Git 获取，**跳过 `MIGRATION.md` 第 3 节的 ZIP 校验/解压步骤**，从第 4 节安装 CPU 渲染系统库，继续重建 Python 环境并验收。每个新 shell 执行命令前，应重新设置以上 `DEXJOCO_ROOT`；不要误用文档的默认 `$HOME/dexjoco` 指向另一个目录。

安装完成后的 CPU 入口示例：

```bash
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" tasks
```

- CPU 后端为 MuJoCo OSMesa 软件渲染，不要求 NVIDIA 驱动或 GPU。
- 不含 `.venv`、依赖缓存、离线安装资源、Codex 安装包、模型账号/凭据或历史 `artifacts`。
- 新 Linux 机器上的真实渲染、完整任务和性能仍需验收；语法/文件校验不等于目标环境实测。
- 包含上游其他工具是为了保留源码与资源，不表示本方案安装了 OpenPI、大模型训练或遥操作环境。
- 模型网格、贴图等资源属于源码，已经纳入版本管理；新的实验结果应保存在被忽略的 `artifacts/` 中。

## 推理测试执行方式

完成上述环境安装与验收后，在能够访问目标 Linux 机器文件系统、终端和观察图片的 Codex 会话中执行测试：

1. 打开克隆后的仓库根目录（例如 `$HOME/Astra_test`），确保可以读取根目录的 `CODEX_AGENT.md`；执行命令时仍使用上文设置的 `DEXJOCO_ROOT`。
2. 将测试会话的模型设为 **GPT-6-Astra**（`gpt-6-astra`）。
3. 将推理强度设为 **中等（Medium / `medium`）**。
4. 发送以下 prompt（保持原文）：

```text
请你读取CODEX_AGENT.md 这个教程 进行任务的测试
```

Agent 应先读取 [CODEX_AGENT.md](CODEX_AGENT.md)，再按教程中的任务执行、视觉闭环、正式评测和结果存档规则开展测试。正式五轮评测须遵循教程中的跨轮上下文隔离要求，各轮使用相同的模型、推理强度和上述初始 prompt。

**注意：** 模型与推理强度需要在实际执行测试的 Codex 会话中明确选择；README 和 prompt 本身不会自动切换模型或推理强度。测试配置本身不代表已经完成测试；已执行的范围和结果见上方本机验收记录。

## 上游来源与许可证

DexJoCo 上游为 `brave-eai/dexjoco`，固定提交为 `8d23b0fab23b17a58c4b55f3942e17013aaf8267`。

保留原有 [DexJoCo LICENSE](dexjoco-linux-cpu-20261012/dexjoco/LICENSE) 以及各第三方组件许可证。`MIGRATION_SOURCE_MANIFEST.json` 记录适配前下载源码的哈希，来源目录已去除；`MIGRATION_PACKAGE_MANIFEST.json` 记录当前交付文件的哈希（不含该清单自身）。

为保持文件哈希可复核，本仓库不进行自动换行转换。Git 上的源码版本不包含之前生成的 ZIP 文件。
