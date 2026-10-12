# DexJoCo：新 Linux 纯 CPU 服务器部署与迁移说明

更新日期：2026-10-12（Asia/Shanghai）

## 0. 适用范围

**目标是一台只有 CPU 的 Linux 服务器。仿真、动作接口和结果保存在新机器上运行，Codex 按本机文件/终端方式调用。**

- 不需要 NVIDIA 显卡、NVIDIA 驱动、CUDA Toolkit、cuDNN 或 EGL GPU 环境。
- 使用 MuJoCo 的 Linux **OSMesa 软件渲染**，无显示器、无桌面也可以生成观察图片。
- 不依赖旧服务器连接别名，不需要 Windows 客户端包或 SSH 控制链路。
- 范围是 DexJoCo + agent_bridge + 11 个任务接口 + 可选只读预览，不是安装 OpenPI 模型、训练策略或本地大模型。包里保留的上游其他工具不表示其额外依赖已经配置。
- 不迁移历史 `artifacts`；新机器重新生成结果。
- **本文和 ZIP 不是已在目标机器测试过的系统镜像。CPU 全任务和性能仍需验收，不承诺实时流畅。**

本文件用于安装和验收；`agent_bridge/CODEX_AGENT.md` 用于部署后的 Agent 操作。不要先照根目录原上游 README 的模型/GPU 教程安装另一套环境。

## 1. 包里有什么、没有什么

文件名：`dexjoco-linux-cpu-20261012.zip`，顶层是 `dexjoco/`。

```text
dexjoco/
├── dexjoco/                  # 可安装项目，完整源码和 data/ 模型、网格、贴图等资源
├── configs/                  # 官方任务与相机 YAML，保持层级
├── agent_bridge/             # 当前适配代码、预览、固定依赖和测试
│   ├── MIGRATION.md          # 本说明
│   ├── CODEX_AGENT.md        # Linux CPU 本机操作指南
│   ├── README.md             # CPU 入口说明
│   ├── env_cpu.sh            # 当前 shell 的 CPU 后端配置
│   ├── run_cpu.sh            # 每次调用强制设置 CPU 后端
│   └── verify_cpu_install.py # 安装检查和真实 OSMesa 测试图
├── LICENSE
├── UPSTREAM_COMMIT.txt
├── MIGRATION_SOURCE_MANIFEST.json  # 下载时源文件哈希与排除项
├── MIGRATION_PACKAGE_MANIFEST.json # 最终交付哈希与适配标记
└── READ_ME_FIRST.md           # 指向 CPU 指南
```

其他现有上游目录和文档一并保留，避免遗漏资源；它们不是本次需要额外启动的服务。

不带：`.venv`、`.uv-cache`、历史 `artifacts`、`__pycache__`、日志、PID、socket、重复源码压缩包，以及 bridge 的历史备份、旧报告、临时 stage 和凭据。

**模型贴图、网格、上游 docs 图片属于源码资源，不能因扩展名是 PNG/JPG 就当历史截图删除。**

## 2. 系统、权限和网络

优先按 Linux x86_64 + Ubuntu/Debian 路线复现。apt 命令用于该发行版系列；其他系统需要等价库，ARM64 还须确认固定版本 wheel 可获取。

- 管理员安装系统动态库，普通用户管理项目和 Python 环境。
- 需要访问 Python 安装资源和包仓库；离线情况见第 10 节。
- 不必复刻旧 Ubuntu 20.04，也不必安装桌面/X server。
- CPU 核数、内存、可用空间影响多相机速度，目前没有实测过能保证性能的最低硬件规格；还须为虚拟环境和新结果留空间。

本文用 `DEXJOCO_ROOT` 指代包含 `agent_bridge/`、`configs/` 和 `dexjoco/` 的项目根目录，默认使用当前 Linux 用户的 `$HOME/dexjoco`，不要求固定用户名、旧服务器或专用数据目录。

```bash
export DEXJOCO_ROOT="${DEXJOCO_ROOT:-$HOME/dexjoco}"
```

需要其他位置时，先用 `export DEXJOCO_ROOT="/absolute/path/to/dexjoco"` 指定实际 Linux 绝对路径。下文的 `${DEXJOCO_ROOT}` 是目录占位写法；命令块用 `${DEXJOCO_ROOT:-$HOME/dexjoco}` 兼容新 shell 未设置变量的情况。解压时目录末级应为 `dexjoco`，与 ZIP 顶层一致。该变量只用于命令定位，CPU 启动脚本与 bridge 主路径仍按自身位置计算；若使用其他上游工具，还应检查其各自配置和依赖。

## 3. 校验与解压 ZIP

将 ZIP 和 `.zip.sha256` 放在新机器同一个目录，运行：

```bash
# 示例传输目录，替换为实际位置
cd "$HOME/dexjoco-transfer"
sha256sum -c dexjoco-linux-cpu-20261012.zip.sha256
```

必须输出 `OK`。不要在损坏或来源不明的包上继续安装。

使用当前用户可写的目录即可，无需管理员创建专用项目路径。目标项目目录必须尚不存在，避免覆盖：

```bash
sudo apt-get update
sudo apt-get install -y unzip ca-certificates curl

export DEXJOCO_ROOT="${DEXJOCO_ROOT:-$HOME/dexjoco}"
case "$DEXJOCO_ROOT" in
  /*/dexjoco) ;;
  *) echo '项目根目录必须是以 /dexjoco 结尾的 Linux 绝对路径'; exit 1 ;;
esac
DEST_PARENT="$(dirname -- "$DEXJOCO_ROOT")"
mkdir -p -- "$DEST_PARENT"
test -w "$DEST_PARENT" || {
  echo '项目父目录不可写，请选择当前用户有权限的目录'; exit 1;
}
test ! -e "$DEXJOCO_ROOT" && test ! -L "$DEXJOCO_ROOT" || {
  echo '目标项目已存在，停止解压，避免覆盖'; exit 1;
}
unzip "$HOME/dexjoco-transfer/dexjoco-linux-cpu-20261012.zip" -d "$DEST_PARENT"
cd "$DEXJOCO_ROOT"
mkdir -p artifacts
```

空 `artifacts` 正常，旧结果不是启动条件。ZIP 记录 Linux 文件模式；传输工具若丢失执行位，仍可以用 `bash agent_bridge/run_cpu.sh ...` 调用。

## 4. 安装 CPU 渲染系统库

以下都在目标服务器执行：

```bash
sudo apt-get update
sudo apt-get install -y \
  libosmesa6 libgl1 libegl1 libopengl0 libglfw3 libgomp1

# OpenCV 等依赖的 GLib 名称随发行版变化
if apt-cache show libglib2.0-0t64 >/dev/null 2>&1; then
  sudo apt-get install -y libglib2.0-0t64
else
  sudo apt-get install -y libglib2.0-0
fi

ldconfig -p | grep -i osmesa
```

最后应列出 `libOSMesa.so`。`libegl1` 等是通用图形动态库兼容依赖，**本方案不使用 EGL 后端，也不会安装 NVIDIA 驱动**。OSMesa 是关键 CPU 离屏渲染库，Python requirements 不能替代。

无 sudo 权限请管理员安装，不要搬旧 `.venv` 绕过系统库。

## 5. 重建 Python 环境

### 5.1 版本基线

| 项目 | 版本 |
| --- | --- |
| Python | 原部署 3.11.17，优先复现；项目要求 `>=3.10,<3.12` |
| uv | 原部署 0.12.22，可用兼容版本即可，不强制升级 |
| MuJoCo / Gymnasium | 3.4.0 / 1.0.0 |
| NumPy / SciPy | 1.26.4 / 1.17.1 |
| Pillow / PyOpenGL / PyYAML | 12.3.0 / 3.1.10 / 6.0.3 |

完整版本见 `agent_bridge/requirements.lock.txt`。它是固定清单，不是离线包或完整系统/构建哈希锁。

旧 `.venv` 引用旧机器绝对路径，不能移植。不要使用系统默认 3.12/3.13 绕过版本限制。

### 5.2 安装 uv（已有则跳过）

下面从官方站点下载并执行脚本，请遵守组织来源/网络策略：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

### 5.3 安装锁定依赖和项目

```bash
cd "${DEXJOCO_ROOT:-$HOME/dexjoco}"

test ! -e .venv || { echo '.venv 已存在，先确认归属，不覆盖'; exit 1; }
uv python install 3.11.17
uv venv --python 3.11.17 .venv
uv pip install --python .venv/bin/python -r agent_bridge/requirements.lock.txt
uv pip install --python .venv/bin/python --no-deps -e ./dexjoco
uv pip check --python .venv/bin/python
.venv/bin/python --version
```

- 下载渠道没有 3.11.17 时可用可获取的 3.11.x，但必须重新验收并记录版本。
- `-e ./dexjoco` 必须执行，清单里的 editable 注释不会安装项目。
- `--no-deps` 避免 editable 改写依赖，构建后端仍可能在线下载。
- 不执行根目录 `environment-dexjoco.yaml`，它是另一套 Conda/OpenPI 路线。
- 不额外安装 GPU 版 JAX、PyTorch、CUDA、OpenPI 模型或权重。

## 6. CPU 环境变量与启动入口

```bash
export MUJOCO_GL=osmesa
export PYOPENGL_PLATFORM=osmesa
export LIBGL_ALWAYS_SOFTWARE=1
export JAX_PLATFORMS=cpu
export CUDA_VISIBLE_DEVICES=""
unset MUJOCO_EGL_DEVICE_ID
```

必须在首次导入 MuJoCo/PyOpenGL 前设置，不与 `PYOPENGL_PLATFORM=egl` 混用。

原 bridge 保留旧 EGL 默认值，未改其任务、奖励、动力学和动作语义。以后优先使用 CPU 入口，不裸调用旧默认环境：

```bash
cd "${DEXJOCO_ROOT:-$HOME/dexjoco}"
bash agent_bridge/run_cpu.sh tasks
```

`run_cpu.sh` 每次设置 CPU 变量、定位本项目 `.venv`、调用 bridge；子 runner 继承配置，不依赖 `.bashrc` 或上一次终端。

检查和回归测试前可在当前 shell 加载：

```bash
cd "${DEXJOCO_ROOT:-$HOME/dexjoco}"
source agent_bridge/env_cpu.sh
```

另一个终端须重新 source，已存在的 runner 不会自动切换后端。bridge 可能重新填入 EGL_DEVICE_ID 默认值，但 OSMesa 不使用该 EGL 变量。

## 7. 验证安装和真正的 OSMesa 渲染

### 7.1 安装检查和测试图

```bash
cd "${DEXJOCO_ROOT:-$HOME/dexjoco}"
source agent_bridge/env_cpu.sh
.venv/bin/python agent_bridge/verify_cpu_install.py
```

不启动默认 runner：核查 Python/库版本、OSMesa 动态库、DexJoCo 源路径，使用独立简单场景执行物理步，生成 640×640 测试图：

```text
${DEXJOCO_ROOT}/artifacts/install-check/osmesa-smoke.png
```

应输出成功标记、实际 OpenGL renderer 和图片路径。检查图像不是全黑/常量图。仅 import 成功不能证明离屏渲染可用。

### 7.2 37 项非真实仿真单元测试

```bash
cd "${DEXJOCO_ROOT:-$HOME/dexjoco}"
source agent_bridge/env_cpu.sh
cd agent_bridge
../.venv/bin/python -B -m unittest -v \
  test_robot_actions test_task_selection test_preview \
  test_camera_selection test_action_chunk
```

它们验证接口、配置和逻辑，不替代真实任务、多相机或 CPU 性能验收。

## 8. 单个真实任务最小验收

先确认 socket/runner 是否存在。下面仅在没有既有 runner、并明确由你创建本次测试时执行：

```bash
cd "${DEXJOCO_ROOT:-$HOME/dexjoco}"
if [ -e agent_bridge/runner.sock ] || [ -S agent_bridge/runner.sock ]; then
  bash agent_bridge/run_cpu.sh status
  echo '已有 socket/runner，确认 task、label、episode_dir 和归属；停止本次自动验收。'
  exit 1
fi

bash agent_bridge/run_cpu.sh tasks
bash agent_bridge/run_cpu.sh start --task click_mouse --seed 0 --label cpu-migration-check --preview-fps 2
bash agent_bridge/run_cpu.sh contract
bash agent_bridge/run_cpu.sh observe
bash agent_bridge/run_cpu.sh hold --steps 1
bash agent_bridge/run_cpu.sh finish --reason cpu_migration_check
bash agent_bridge/run_cpu.sh stop
```

**逐条执行并检查返回 JSON 的 `ok`，失败后不继续动作/清理。** hold 是接口验收，不是解题策略；只结束自己新创建的测试。

原 bridge 启动等待 120 秒、socket 请求超时 180 秒。CPU 可能慢：start 超时后子进程未必退出，先检查 runner.log、runner.pid 和 status；不要反复 start、删除 socket 或宽泛杀进程。

若一开始就确认没有 runner，可用两终端观察较慢的首次初始化：

```bash
# 终端 A：前台启动，不经过 start 的 120 秒轮询，等待输出 ready
cd "${DEXJOCO_ROOT:-$HOME/dexjoco}"
bash agent_bridge/run_cpu.sh serve --task click_mouse --seed 0 --label cpu-foreground-check --preview-fps 2
```

终端 B 等 A 输出 ready 后执行 status/contract/observe/hold，结束自己的 runner 时 finish、stop。不在上一轮后台进程归属不明时直接 serve。

降低 preview-fps 主要减少预览编码/发布开销，不会自动降低任务图像分辨率或上游相机渲染数量。不得为了提速改官方任务/奖励/成功规则。

## 9. 查看图片、预览和其他任务

### 9.1 Codex 在同一台 Linux 上

observe 的 result.contact_sheet / result.images 是本机绝对路径，让可访问该文件系统的 Agent 直接打开图像，不需要 Windows/SCP。

Agent 可用性与 MuJoCo 环境是两件事：本包不安装 Codex、不提供登录凭据或模型服务，使用你已配置的 Agent 工具。

### 9.2 可选浏览器只读预览

运行中的 runner 默认在服务器 loopback `127.0.0.1:8766` 提供带 token 的预览：

```bash
cd "${DEXJOCO_ROOT:-$HOME/dexjoco}"
bash agent_bridge/run_cpu.sh status
```

看返回的 result.preview 地址字段，以实际带 token 的 URL 为准，不公开 token。网页只查看，不执行动作/推进仿真。

无桌面服务器不必安装浏览器。如果浏览器在另一台机器，其 localhost 不是服务器，需经组织允许的安全端口转发访问服务器 loopback。这是可选观看方式，不是部署依赖，也不要求旧连接别名。不要将 8766 对公网无认证开放。

### 9.3 其他任务和完整回归

11 个任务与动作/相机约定见 CODEX_AGENT.md，简单场景通过不等于全部任务通过。需要的单臂/双臂任务应逐项验收。

回归会启动仿真并生成新结果。先确认默认 runner 空闲、测试归属，再逐个执行，不并发跑这组脚本：

```bash
cd "${DEXJOCO_ROOT:-$HOME/dexjoco}"
source agent_bridge/env_cpu.sh
cd agent_bridge
../.venv/bin/python smoke_test.py
../.venv/bin/python cli_regression.py
../.venv/bin/python preview_regression.py
../.venv/bin/python multitask_test.py
../.venv/bin/python bimanual_test.py
../.venv/bin/python camera_alignment_test.py
../.venv/bin/python action_chunk_30_integration.py
```

旧回归有 GPU 环境下使用过的超时/性能阈值，CPU 路线须区分库/渲染错误、任务错误和性能超时并记录结果。smoke_test.py 含成功判定逻辑 fixture，不是 Agent 成功成绩。

## 10. 完全离线时还要准备什么

代码 ZIP + requirements 不是离线运行环境。需另备：

1. 对应目标架构的 Python 3.11、uv 安装资源。
2. 固定依赖 wheel/源码包和 editable 构建依赖（包括 hatchling）。
3. 目标发行版 apt 离线包和依赖，尤其 OSMesa/GLib 动态库。
4. 管理员安装权限或预装环境。

本包不含这些安装资源或已验收的容器/系统镜像。

## 11. 常见问题

| 现象 | 排查 |
| --- | --- |
| libOSMesa 找不到 / OpenGL 平台报错 | 安装 libosmesa6、source env_cpu.sh，在导入前设置两个 osmesa 变量 |
| EGL/设备编号错误 | 裸 bridge 仍用旧默认环境，改用 run_cpu.sh |
| libGL/GLib 动态库缺失 | 系统库未安装，不能用复制 Python 环境代替 |
| No module named dexjoco | 检查 .venv 和 --no-deps -e ./dexjoco，不是 -e . |
| Python 版本不符合 | 使用 3.11，不用系统 3.12/3.13 |
| YAML/模型/网格/贴图丢失 | 完整保留 configs 与 dexjoco/data，未把资源图片删掉 |
| 已有不同 task | 正常保护，先确认归属，再 finish/stop，不偷偷 reset |
| socket 存在但无响应 | 检查归属、进程和日志，不自动删 socket/杀进程 |
| 120/180 秒超时 | CPU 需实测，检查是否仍初始化，必要时使用归属明确的前台 serve |
| 网页打不开 | 预览是否启用、token 是否正确、浏览器访问链路是否可用 |
| 无桌面/浏览器 | 不影响 CPU 仿真和图片输出，网页是可选功能 |

## 12. 给 Codex 的起始指令

> 先确认实际项目根目录并设置 `DEXJOCO_ROOT`（默认 `$HOME/dexjoco`），再读取 `${DEXJOCO_ROOT}/agent_bridge/MIGRATION.md` 和 `${DEXJOCO_ROOT}/agent_bridge/CODEX_AGENT.md`。这是纯 CPU Linux 本机部署，使用 `bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh"`，不使用旧远端别名/Windows 脚本。先确认环境和 runner 归属，不重置/停止他人的任务，再按指南做视觉闭环。

CODEX_AGENT.md 需显式读取，不会自动创建聊天、安装 Agent 或隔离评测上下文。

## 13. 交付检查与目标验收边界

制作包已做：

- 读取旧项目源码、配置和完整资源，逐个核验下载 SHA256，未改动旧项目或进程。
- 仅新增/适配文档、CPU 入口和独立安装检查，未改 bridge 核心与上游任务。
- 记录源清单/交付清单并提供 ZIP SHA256，排除旧结果、缓存、旧环境和运行文件。
- 之前原服务器现有 Python 3.11.17 环境下的 37 项非真实仿真单元测试全部通过，不等于新机器验收通过。

尚未做：新机器安装、目标 OSMesa 真实渲染、CPU 全任务回归/性能验收。旧服务器未检测到 OSMesa 系统库，本次没有为测试改装旧服务器。

目标机器验收：

- [ ] ZIP 校验、无覆盖解压、完整源码资源。
- [ ] OSMesa/GLib 系统库可用。
- [ ] Python 3.11、固定依赖和项目安装，uv pip check 通过。
- [ ] verify_cpu_install.py 真正生成测试图。
- [ ] 37 项单元测试通过。
- [ ] 自己创建的任务通过 tasks/contract/observe/hold/finish/stop。
- [ ] 需要的单臂/双臂任务和 CPU 性能逐项验收。
- [ ] Agent 显式读取指南，可查看图片，不把旧结果当新成绩。

### 官方参考

```text
https://github.com/google-deepmind/mujoco/blob/3.4.0/python/mujoco/gl_context.py
https://github.com/google-deepmind/mujoco/blob/3.4.0/python/mujoco/osmesa/__init__.py
https://docs.astral.sh/uv/pip/environments/
https://docs.astral.sh/uv/getting-started/installation/
https://docs.python.org/3.11/library/venv.html
```
