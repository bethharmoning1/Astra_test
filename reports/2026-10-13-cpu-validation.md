# 2026-10-13 Linux CPU 安装、验收与任务执行

本次按根目录 README、CPU 迁移说明及操作指南执行。先写入 `PLAN.md`，再由子代理完成安装与验收；推理任务使用另一个全新上下文的执行子代理。原始运行文件保存在项目的忽略目录 `artifacts/`，不随本报告提交。

构建与下列基础设施验收通过；GPT-6-Astra medium 的 `click_mouse` 单次尝试在 518 步、18 次动作调用后达到官方成功。四项可选回归未执行，详见下表。本次没有修改仿真源码、任务资源或成功判定。

## 环境与来源

| 项目 | 实际值 |
| --- | --- |
| 仓库起始提交 | `5d1e3ef` |
| 上游固定提交 | `8d23b0fab23b17a58c4b55f3942e17013aaf8267` |
| 操作系统 | Ubuntu 24.04.4 LTS，Linux 6.8.0-124-generic，x86_64 |
| CPU | Intel Xeon Platinum 8563C |
| Python / uv | 3.11.17 / 0.13.0 |
| 渲染后端 | MuJoCo OSMesa，Mesa llvmpipe 软件渲染 |
| MuJoCo / Gymnasium / NumPy | 3.4.0 / 1.0.0 / 1.26.4 |
| Python 环境 | `dexjoco-linux-cpu-20261012/dexjoco/.venv` |
| 依赖安装 | `agent_bridge/requirements.lock.txt`，另执行 `--no-deps -e ./dexjoco` |

根目录与 `agent_bridge/` 的 `CODEX_AGENT.md` 内容一致，SHA-256 为 `9303cd43314a7056269b1c455b1adefdd84177e6379ae972e9565c2c6daa9b05`。依赖锁定文件 SHA-256 为 `493c1c260abc7508d44c291c67ee1b7d44cf1c0b6670bb862ea87cbefa4c6987`。

## 安装与接口验收

| 检查 | 结果 |
| --- | --- |
| 系统 OSMesa / GL / GLFW / GLib 库 | 已安装并可用 |
| 锁定依赖、editable 安装、`uv pip check` | 通过，36 个包兼容 |
| `verify_cpu_install.py` | 通过，真实生成 640 × 640 非常量 RGB 图，并由 Agent 查看 |
| 文档列出的 unittest | 37 / 37 通过，5.204 秒 |
| `tasks` | 通过 |
| `click_mouse` 启动、契约、观察、1 步 hold、finish、stop | 均返回 `ok=true` |

默认预览端口 `8766` 已被另一服务占用。首次启动因此报 `OSError: [Errno 98] Address already in use`，子进程自行退出且未建立 runner socket。确认这一原因后，使用现有 CLI 参数 `--preview-port 18766 --preview-fps 2` 成功启动；没有停止或修改已有服务。

最小验收 episode：

```text
artifacts/click_mouse-cpu-migration-check-20261013-seed0-20261013-152313-993137/
```

其 `summary.json` 记录 `steps=1`、`action_calls=1`、`succeed=false`、`terminated=false`、`truncated=false`、`reason=cpu_migration_check`。这是接口验收，未记作解题成功。允许的相机为 `ego_right`、`wrist`，相机配置 SHA-256 为 `fa58df1acac695aec496b78d2349f8c41c5ccea365e968e5ed26d9c54aac3df3`。结束后原 runner 进程退出，socket 消失，端口 `18766` 释放。

安装图、脱敏命令日志和逐条命令 JSON 位于 `artifacts/install-check/`；完整 episode 保留 `manifest.json`、`events.jsonl`、`summary.json` 及 `obs-*` 图片。

## 扩展回归

| 回归 | 结果 | 耗时 |
| --- | --- | ---: |
| `smoke_test.py` | 9 / 9 通过 | 48.09 秒 |
| `action_chunk_30_integration.py` | 2 个任务、16 / 16 通过；上游源码哈希前后一致 | 105.51 秒 |
| `camera_alignment_test.py`（短 socket 变体） | 11 个任务、380 / 380 通过 | 72.40 秒 |
| `cli_regression.py`、`preview_regression.py`、`multitask_test.py`、`bimanual_test.py` | 未执行：原脚本使用已被占用的默认预览端口 `8766` | — |

相机回归首次执行失败，原因是当前仓库的嵌套路径使专用 Unix socket 绝对路径达到 108 字节，超过 Linux 可用上限 107 字节。保留失败报告与日志后，在本次独有、权限为 0700 的 `/tmp/astra-camera-*/` 目录中选用短 socket 路径，通过临时导入脚本并覆盖模块 `SOCKET` 再调用 `main()` 完成重跑；没有修改测试源码、动作或相机检查。此变体全部通过。测试结束后，专用 runner、socket、端口及临时目录已清理。

在确认没有测试 runner、端口 `8767` 空闲后，可以这样复现短 socket 变体：

```bash
cd "$DEXJOCO_ROOT/agent_bridge"
source env_cpu.sh
../.venv/bin/python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
import camera_alignment_test as regression

with TemporaryDirectory(prefix="astra-camera-") as directory:
    regression.SOCKET = Path(directory) / "runner.sock"
    regression.main()
PY
```

基础设施 smoke 包含特权成功判定 fixture，其结果只用于验证逻辑，未计入 Agent 任务成绩。

主要原始证据（相对于 `DEXJOCO_ROOT`）：

- `artifacts/install-check/validation-summary.json`：安装、版本、检查结果、环境问题和清理状态。
- `artifacts/install-check/minimal-command-log.jsonl`：最小接口验收命令及返回，预览 token 已脱敏。
- `artifacts/smoke_report.json`、`artifacts/action_chunk_30_integration_report.json`、`artifacts/camera_alignment_report.json`：三项集成回归结果。
- `artifacts/install-check/camera_alignment_report-initial-failed.json` 及同目录的 `runner-after-initial-camera-failure.log`：相机首次失败的原始报告和错误日志。
- `artifacts/install-check/camera_alignment_short_socket-execution.json` 及同目录的 `camera-short-socket-launch.json`：短 socket 重跑记录和参数。
- `artifacts/install-check/artifact-inventory.json`：截至基础设施验收结束的 314 个产物路径、大小与 SHA-256（不包含随后执行的模型 episode）。

## GPT-6-Astra 视觉闭环尝试

环境验收完成后，由独立子代理按 `gpt-6-astra`、`medium` 配置执行默认 `click_mouse` 单次尝试，seed=0，label=`astra-medium-single-20261013`。使用 `fork_turns=none` 创建全新上下文，不传入基础设施验收的任务轨迹或策略。启动提示原文为：

```text
请你读取CODEX_AGENT.md 这个教程 进行任务的测试
```

调度消息还提供本机路径、已安装环境、任务/seed/label、可用预览端口、观察边界、原生步数上限及清理要求，没有提供解题策略。调度记录保存在 `artifacts/astra-single-20261013-dispatch.json`。

| task | seed | steps | action_calls | succeed | terminated | truncated | reason |
| --- | ---: | ---: | ---: | --- | --- | --- | --- |
| `click_mouse` | 0 | 518 | 18 | true | true | false | `official_success` |

模型 episode：

```text
artifacts/click_mouse-astra-medium-single-20261013-seed0-20261013-154116-562538/
```

外部核验者（父代理）将 `summary.json` 与 `events.jsonl` 逐项对应：只有一次 reset，环境步号连续为 1..518，18 次动作调用的实际步数为前 17 次各 30 步、最后一次 8 步。第 518 步记录官方 `succeed=true` 和 `terminated=true`，之后没有继续执行动作。初始观察及每段动作后的观察均已存档，共 21 个观察目录；两路相机与官方配置一致。核验记录在本轮 `external-audit.json`，全部 15 项核验通过。执行者按要求使用图像工具查看观察，结束后外部核验者也查看了最终图像。

`finish` 和 `stop` 已成功执行，外部复核确认本次 runner 进程已退出、默认 socket 不存在。本轮无基础设施异常，未再次 reset、重试或替换 episode。

调度参数只能证明请求的模型配置，无法独立验证服务后端模型版本。此单次尝试不作为五轮稳定性成绩，也不代表其他任务的 Agent 成功率。现有子代理能力已提供所请求模型，没有读取用户提供的 API 凭据文件或另行构建 API 调用流程。

## 本机复用

```bash
export DEXJOCO_ROOT="$PWD/dexjoco-linux-cpu-20261012/dexjoco"
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" tasks
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" status
# 确认没有他人 runner 后，使用未占用的预览端口：
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" start \
  --task click_mouse --seed 0 --label manual-check \
  --preview-port 18766 --preview-fps 2
```

构建只需 CPU；后续仿真操作应继续使用 `run_cpu.sh`，遵循 `CODEX_AGENT.md` 的观察、动作与归属要求。
