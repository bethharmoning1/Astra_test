# DexJoCo CPU 构建与执行计划

## 目标

按照根目录 `README.md`、`agent_bridge/MIGRATION.md` 和 `CODEX_AGENT.md`，在当前 Linux 工作区完成 DexJoCo 的 CPU-only 构建、安装验收、真实任务接口执行及视觉闭环尝试，并保留可复核的结果。执行前先写计划，实际构建与任务操作交给子代理。

## 执行步骤

1. **确认环境与项目归属**
   - 将 `DEXJOCO_ROOT` 指向本仓库中的 `dexjoco-linux-cpu-20261012/dexjoco`。
   - 检查 Python/uv、OSMesa/GL 动态库、已有 `.venv`、`runner.sock`/runner 进程和 Git 工作区。
   - 如果存在 runner，只读取状态并确认归属；不重置、停止或覆盖其他任务。

2. **准备 CPU 运行环境**
   - 依据迁移文档安装或确认 `libosmesa6`、GL/GLFW/GLib 等系统库。
   - 准备 Python 3.11（项目要求 `>=3.10,<3.12`）和 uv。
   - 在项目根目录创建 `.venv`，按 `agent_bridge/requirements.lock.txt` 安装锁定依赖，并以 `--no-deps -e ./dexjoco` 安装项目。
   - 使用 `uv pip check` 和 Python 版本检查安装一致性；不安装 OpenPI、GPU/CUDA 或模型权重。

3. **验收 OSMesa 与软件渲染**
   - 通过 `agent_bridge/env_cpu.sh`/`run_cpu.sh` 设置 OSMesa、软件 GL、CPU JAX 环境。
   - 运行 `verify_cpu_install.py`，确认真实 MuJoCo 物理步和非黑测试图写入 `artifacts/install-check/osmesa-smoke.png`。

4. **运行非仿真回归**
   - 在 `agent_bridge` 目录运行文档列出的 37 项 unittest：机器人动作、任务选择、预览、相机选择和动作分块测试。
   - 记录通过/失败及首个可定位错误；失败时先按迁移文档排查，不跳过依赖问题伪造成功。

5. **运行最小真实 CPU 任务验收**
   - 确认没有不属于本次工作的 runner 后，使用 `run_cpu.sh` 依次执行 `tasks`、`start --task click_mouse --seed 0`、`contract`、`observe`、`hold --steps 1`、`finish`、`stop`。
   - 每步检查返回 JSON 的 `ok`、任务/相机契约和终止状态；只清理本次创建的 runner。
   - 保留该 episode 的 `manifest.json`、`events.jsonl`、`summary.json` 与观察图片；不将接口验收描述为 Agent 任务成功。

6. **可选扩大回归**
   - 若前述步骤通过且 CPU 时间/资源允许，按文档顺序运行 smoke、CLI、preview、多任务、双臂、相机和 30 步动作分块集成回归。
   - 将 CPU 慢导致的超时与代码/渲染错误分开记录；不并发启动多个 runner。

7. **README 推理测试（环境验收后）**
   - 使用独立子代理，明确指定 `gpt-6-astra` 和 `medium`，每个 episode 使用新的上下文；记录调度设置，不声称独立验证后端模型版本。
   - 固定初始提示为 `请你读取CODEX_AGENT.md 这个教程 进行任务的测试`，让执行者显式阅读指南并查看允许的相机图片。
   - 默认执行 `click_mouse`、seed=0 的一次完整视觉闭环尝试；任务选择已向用户征询，若用户指定范围则按其选择执行。单次尝试不扩展或宣称为五轮评测。
   - 每段动作最多 30 步，查看新图后再规划下一段，累计遵循任务原生上限；成功仅以官方 `info.succeed=true` 判定。
   - 若用户选择五轮协议，则在第一轮前固定配置、初始图像/本体状态比较方法和异常处理规则，隔离跨轮上下文与历史文件访问，保留每轮及中断记录。

8. **记录结果并推送新分支**
   - 复核执行日志、安装图、真实任务观察和 summary；提交不含凭据的计划与验收报告。
   - `.venv`、原始 artifacts 和运行时状态留在忽略目录。除非验收发现必须修复的问题，否则不修改仿真源码或成功判定。
   - 按用户指定使用 `test` 分支，提交后推送到 `origin`，核验远程分支提交与本地一致。

## 交付与判定

- 子代理执行上述构建和运行步骤并返回命令、结果路径、通过项和阻塞项。
- 我复核工作区变更、测试输出和 artifacts，再向用户报告实际完成范围。
- 若系统库或 Python 3.11 无法安装，停止依赖其环境的后续步骤，保留具体错误和可继续执行的命令。

## 执行记录

- CPU 构建、真实渲染、37 项单测和最小真实接口验收完成。
- smoke（9 项）、30 步分块（16 项）及 11 任务相机回归（380 项）通过；相机回归通过临时短 socket 路径解决深目录限制，保留首次失败记录。
- 四个使用默认预览端口 8766 的可选回归未执行，该端口属于其他服务。
- 独立 GPT-6-Astra medium 子代理完成 `click_mouse` 单次尝试：518 步、18 次动作调用，官方成功，runner 已清理。
- 完整结果与本机证据位置见 `reports/2026-10-13-cpu-validation.md`。
