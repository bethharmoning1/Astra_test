# Codex Agent 操作约定：Linux CPU 本机版（2026-10-12）

本文件需要显式读取，不会像 AGENTS.md 一样自动加载。适用于 Linux 纯 CPU 本机运行：先按 MIGRATION.md 安装，再由可访问本机文件系统的 Codex 做视觉闭环。仿真不需要另部署模型 API，但本包不安装 Codex、不提供账号登录或任何模型凭据。

## 项目目录（可移植）

本文用 `DEXJOCO_ROOT` 指代包含 `agent_bridge/`、`configs/` 和 `dexjoco/` 的项目根目录，不绑定特定用户名或服务器。默认是当前 Linux 用户的 `$HOME/dexjoco`。完成部署后，先在执行任务的 shell 中设置：

~~~bash
export DEXJOCO_ROOT="${DEXJOCO_ROOT:-$HOME/dexjoco}"
~~~

如果项目不在默认位置，先用 `export DEXJOCO_ROOT="/absolute/path/to/dexjoco"` 指定实际 Linux 绝对路径。下文的 `${DEXJOCO_ROOT}` 是此目录的占位写法，读取文件时应展开为实际路径。该变量只用于命令定位；启动脚本和 bridge 仍按自身文件位置识别项目根目录。

## 已适配任务（2026-10-11）

| task | 目标 |
| --- | --- |
| click_mouse | 将鼠标移到紫色鼠标垫并点击左键 |
| fold_glasses | 折叠眼镜并放入眼镜盒 |
| hammer_nail | 用锤子将钉子钉入木板 |
| pick_bucket | 将盒装食物放入桶中，然后提起桶 |
| pinch_tongs | 抓住夹钳并连续完成三次张开—闭合 |
| water_plant | 抓住浇水器并给植物浇水 |
| bimanual_assembly | 左手托盘、右手插销，插入孔 |
| bimanual_hanoi | 右手移动中盘、左手移动小盘 |
| bimanual_microwave_cook | 开微波炉、放食物、关门、启动 |
| bimanual_photograph | 左手持相机对准标志、右手按快门 |
| bimanual_unlock_ipad | 拿起 iPad，输入 123 解锁 |

现支持全部11个任务。6个单臂原生23D，只允许官方逐任务配置的2路相机：click_mouse为ego_right/wrist，其他单臂为front/wrist。5个双臂原生46D，允许ego/wrist_left/wrist_right三路。相机由configs/rand_obj/<task>.yaml的camera_mapping读取，人类只读预览与Agent选择相同，不开放其余原生视角。tasks全部bridge_supported=true。不要把环境配置或小动作验证当成Agent已完成任务。

## 开始前与任务选择

1. 读取 ${DEXJOCO_ROOT}/agent_bridge/MIGRATION.md 和 README.md，完成 CPU 环境与真实渲染验收。
2. 使用 `bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh"`；它使用本项目 .venv 并设置 OSMesa，不裸调用旧 EGL 默认环境。
3. 不修改/重启其他项目、模型服务或其他用户进程；本次 CPU bridge 不安装 OpenPI 模型。
4. 先检查 bridge.py status。已有 runner 时确认 task、label、episode_dir 和归属，不擅自 reset/stop 别人的 episode。
5. CPU/OSMesa 软件渲染，无需 GPU/桌面；仿真仅在显式动作调用时前进。CPU 可能较慢，超时先检查进程归属和日志，不反复启动。

在运行仿真的 Linux 机器上直接执行，不需要旧连接别名或 Windows 脚本：

~~~bash
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" tasks
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" start --task pinch_tongs --seed 0 --label codex-agent
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" contract
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" observe
~~~

把 pinch_tongs 替换为用户选择的已适配任务。以 contract 的任务提示、camera_mapping/camera_keys、机器人动作维度、关节范围和工作空间为准，不沿用 click_mouse 的策略或物体位置。

- start 不带 --task：复用任何已有任务；没有 runner 时默认 click_mouse。
- start 带 --task：已有同任务时只复用，不重新设置 seed/label；若任务不同则报错，绝不偷偷重置。
- reset：只重置当前任务，可用 --task 检查任务是否一致，不会切换任务。
- 切任务：确认当前 episode 归属，finish/stop 后再 start --task 新任务。
- 保留一个默认 runner，多个任务不要同时争用它。不要为了切任务删除 runner.sock 或宽泛杀进程。

## 视觉闭环

读取 result.contact_sheet 或 result.images 本机绝对路径，通过能访问本 Linux 文件系统的图像工具查看，不必下载到另一台电脑。Agent 无图片工具时先配置，不盲目执行动作。根据新图像和机器人本体状态规划下一段短动作；不要在未看新图的情况下盲目执行大段预先固定轨迹。

~~~bash
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" move --xyz X Y Z --steps 30 --note safe_label
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" hold --steps 10
~~~

单臂原生动作采用绝对世界 XYZ、wxyz 四元数和 16 维手指目标，合计 23D；单臂 act 也接受 22D rotvec。双臂使用下文的 46D/44D 排布。move 的插值只是低层执行，不是现成抓取/点击策略。单次动作请求最多 30 步；一次试验的累计最大环境步数由当前任务的上游环境设置决定，见下节，不使用统一的 Agent 总步数预算替代它。

## 每次试验的最大步数：遵循上游任务环境

一次试验指从一次 reset 开始，到官方成功、官方失败、环境步数上限耗尽或明确的人工结束为止的一个 episode。默认测试应按当前任务的上游原生步数上限执行。

以下是 2026-10-11 从旧部署逐项核验的原生上限，与固定上游提交 8d23b0fab23b17a58c4b55f3942e17013aaf8267 对应：

| 类型 | task | 最大环境步数 |
| --- | --- | ---: |
| 单臂 | click_mouse | 1200 |
| 单臂 | fold_glasses | 1200 |
| 单臂 | hammer_nail | 1000 |
| 单臂 | pick_bucket | 1200 |
| 单臂 | pinch_tongs | 1000 |
| 单臂 | water_plant | 1000 |
| 双臂 | bimanual_assembly | 1500 |
| 双臂 | bimanual_hanoi | 1500 |
| 双臂 | bimanual_microwave_cook | 1100 |
| 双臂 | bimanual_photograph | 1000 |
| 双臂 | bimanual_unlock_ipad | 1200 |

来源是上游源码 ${DEXJOCO_ROOT}/dexjoco/dexjoco/sim/envs/panda_<task>_env.py 中 step() 的终止阈值；water_plant 使用同文件的 _MAX_EPISODE_STEPS 常量。旧核验报告不在本迁移包中，需要时按上游源码重新核查。此表只记录已部署上游版本的设置，不是另加一套限步配置；若部署版本变化，应重新核验，以实际任务环境为准。

- 环境步数按实际 env.step() 调用累计，move、hold、act 执行的每一步均计入；不是 Agent 决策次数、动作段数量或模型推理耗时。observe 不推进仿真。
- 开始或复用 episode 时先确认当前 task 和已执行步数。规划下一动作段时计算“剩余步数 = 当前任务原生上限 - 已执行步数”，请求不超过剩余步数，且仍遵守单次请求最多 30 步的 bridge 限制。
- 每次动作返回后检查 succeed、terminated 和 truncated。官方成功或官方失败可能提前结束；环境达到上限也会结束。收到 terminated/truncated 后不再发送动作，也不 reset 后把下一轮接算到本轮。只有官方 info.succeed=true 才算成功。
- 若用户明确要求短测、人工结束或出现必须停止的安全/基础设施问题，可提前结束，但必须记录实际步数和具体早停原因；不能把 Agent 自定预算耗尽伪装成官方成功、失败或超时。
- contract 的 episode_step_limit=null 仅表示 bridge 没有另加统一的 episode 上限，不代表上游环境没有上限；recommended_agent_step_budget 是历史建议字段，不作为当前任务的原生上限或默认早停条件。若 README 或旧记录出现统一总步数预算的提示，以本节和实际上游任务设置为准。
- 不修改上游终止逻辑、控制频率、奖励或成功判定。单次动作段最多 30 步与一次试验的累计最大步数是两种不同限制。
- 30 步是本项目 bridge 的动作段上限，不是 DexJoCo 官方统一规定；move、hold 的 --steps 必须在 1..30，act 的动作序列必须满足 1<=T<=30（单臂 23D/22D、双臂 46D/44D 均适用）。超长请求整体拒绝，不自动截断或拆分。完成动作段后观察新图像，再规划下一段；不要在脚本循环里预先拆长轨迹以绕过闭环观察。

## 重复测试协议：同场景完成稳定性（2026-10-12）

当用户要求按本协议进行重复评测时，每个被测任务执行 5 个独立 episode。用户明确指定的接口验收、调试、短测或单次尝试不自动扩展为五轮，也不混入本协议成绩。此协议不表示已复现论文或官方 benchmark；既有调试记录不得追溯改标为隔离评测。

| 测试能力 | 初始条件 | 重复次数 | 跨轮经验 |
| --- | --- | --- | --- |
| 同场景完成稳定性 | 同一任务、同一初始状态，环境 seed=0 | 5 个独立 episode | 隔离，不学习上一轮经验 |

### 1. 评测范围与固定配置

- 测试开始前确定任务列表、批次标识和 5 轮计划。每个选定任务均测 5 轮；只有用户选择全部 11 个任务时才是 55 个计分 episode。
- 五轮使用同一部署版本、任务配置、相机配置、控制模式、模型及可确认的版本/设置、指导文件、任务起始提示、工具权限、观察策略和终止规则。记录这些配置及可获取的哈希；无法确认后端模型版本时如实注明，不能声称已独立验证。
- 先冻结公共指导文件和提示，再开始计分。不得根据前几轮结果修改指南、增加抓取技巧、提供成功轨迹或调整策略后继续计入同一组五轮；确需修改时结束原批次，另建批次并完整重测。
- 环境 seed 与模型生成随机性是两回事。固定环境 seed=0 不意味着每轮生成相同动作；保持模型采样设置一致，不通过复用上一轮输出制造重复轨迹。

### 2. 每轮重新初始化环境

- 每轮开始前检查 status，确认 runner 的 task、label、episode_dir 和归属；只操作本次评测拥有的 runner。不得重置或停止其他人的 episode，不得争用同一个默认 runner。
- 本协议优先使用“本轮 finish → stop → 确认 runner 已退出 → 下一轮 start --task <task> --seed 0 --label <本轮标签>”。首轮没有 runner 时直接 start。也可对自己拥有的当前同任务 runner 明确执行 reset --task <task> --seed 0 --label <本轮标签>；reset 不能切换任务。
- 不得把反复执行 start 当成重新开始：已有同任务 runner 时 start 只复用当前 episode，不重置 seed、label 或状态。
- 每轮使用不同标签，例如同一批次的 eval-BATCH-trial01 至 eval-BATCH-trial05。确认新返回的 episode_dir 与前轮不同，step_count 为 0，task 和 seed 正确；这些生命周期核验由外部调度者完成，不向执行 Agent 提供前轮解题经验。
- 在首个动作前保存本轮初始观察。由外部核验者使用允许的初始 RGB、机器人本体状态和配置记录比较初始条件；不得让执行 Agent 为此读取前轮历史，也不得绕过 bridge 读取物体真值或内部状态。比较方法及数值容差应预先确定，不能只凭 seed 相同认定初始状态一致。若不一致，先排查并记录原因，不计作同场景重复评测。

### 3. 同轮保留历史，跨轮隔离上下文

**同一个 episode 内保留历史；开始下一个 episode 时，重新建立不包含此前 episode 内容的空白历史，不传入上一轮的任何经验。**

- “空白历史”不是不给任务说明：每轮仍加载相同的固定指南、工具说明、任务提示和本轮初始观察。本轮后续观察、动作和反馈在本轮内保留，以维持视觉闭环；不要每次动作后清空历史。
- 不传入此前 episode 的对话、摘要、复盘、成功或失败结果、轨迹、动作文件或策略笔记；不得经由共享文件、记忆工具、历史报告或他人转述间接学习这些经验。此限制同时适用于其他已测试任务的经验。
- **隔离必须由外部调度程序或独立会话机制实现。仅在同一聊天中要求 Agent“忽略上一轮”“忘记经验”，不视为已完成隔离。** reset 或 stop/start 只重置仿真，不清空模型聊天上下文。
- 当前无 API 的 Codex 工作方式应每轮使用不含前轮对话/摘要的新聊天，并控制其历史文件访问权限；单个执行聊天只负责当前一个 episode，五轮调度和最终汇总由外部程序或人工负责。本文件不会自动创建新聊天、清空上下文或限制文件权限。
- 若将来经用户授权改用 API，同样须由外层程序为每轮重新建立输入历史或独立会话，不把新 episode 接入前轮上下文链；当前协议不授权改造 API 流程或索要 API Key。
- 在同一轮内可以根据本轮反馈修正动作，但不能把修正经验带入下一轮。无法保证上下文或文件访问隔离时，明确报告阻碍，不在现有共享聊天中继续并宣称已完成隔离评测。

### 4. 单轮执行、终止与异常处理

- 继续遵守本指南的视觉闭环、官方观察范围、单次动作最多 30 步和当前任务上游原生累计环境步数上限，不额外用统一 Agent 总步数预算替代。
- 官方成功、官方失败或原生环境步数耗尽后结束本轮，保存结束原因并 finish/stop；不得 reset 后把下一轮接算到本轮。成功仅认本轮官方 info.succeed=true；summary.succeed 保留本轮是否曾达到官方成功。
- 任务失败、超时或 Agent 自行放弃均是一次未成功尝试，不能删除后补跑以提高成绩；动作策略导致的失败不能包装成基础设施异常。
- 测试前约定基础设施故障和人工中断的处理规则。可证实的 服务/文件访问/硬件故障等未完成 episode 单独标记为 interrupted，保留全部记录，不默认为成功或任务失败；只按预先约定或用户确认的规则补测，补测仍须从同一初始状态、独立上下文开始，并记录替换关系。若需改动冻结配置，另建批次。
- 缺少 summary.json 时先核实是否仍在运行或异常中断，不从缺失文件推断任务结果。未取得 5 个有效计分 episode 时报告“评测未完成”和当前进度，不提前发布正式五轮成功率。

### 5. 逐轮存档与经验访问隔离

- 每轮保留独立 episode_dir 下的 manifest.json、events.jsonl、summary.json、obs-* 观察目录和原始相机图片，不覆盖、不删除失败轮次。
- 本机结果目录为 ${DEXJOCO_ROOT}/artifacts/<task>-<label>-seed<seed>-<日期时间>-<六位时间后缀>/。同一 task、label 和 seed 的新 episode 仍会创建独立目录；本轮内 events.jsonl 追加写入，每次保存的观察使用新的 obs-* 子目录。
- 外部评测记录至少保存 batch_id、task、trial_index、seed、label、episode_dir、配置/指南版本或哈希、隔离方式及核验结果、初始状态核验结果、succeed、terminated、truncated、steps、action_calls、reason 和异常/补测关系。bridge 尚未记录的字段由外部记录补充，不宣称仅修改本文就已自动落盘。
- bridge 的存档是仿真执行与观察记录，不等于模型完整聊天记录，也不保证每个仿真步都有存档图片。如需额外保存模型请求/响应，由外部程序保存且不供后续执行 Agent 读取。
- 存档与经验隔离不冲突：结果可以全部保留，但后续执行 Agent 仅能访问固定公共材料和本轮所需文件。历史目录、汇总结果和复盘材料留给外部调度者/汇总者，不反馈给后续轮次。

### 6. 最终结果与任务成功率

- 五轮结束后由外部程序或独立汇总会话读取各轮 summary.json，并与外部评测记录对应；不能让汇总者携带历史经验再充当后续轮次的执行 Agent。
- 每个任务逐轮列出 trial、seed、succeed、steps、action_calls、reason 和 episode_dir，另列所有 interrupted 及补测记录。计分轮次的缺失和替换必须透明，不挑选最好五轮，不把旧调试成功混入。
- 每个任务的任务成功率（不是分类准确率）为：成功 episode 数 / 5 × 100%。例如成功 3 轮则为 3/5=60%。不是“五轮中成功一次就记为该任务 100%”，也不是挑选最佳一轮。
- 多任务汇总同时报告各任务成功率；所有选定任务均取得 5 个有效计分 episode 后，总体成功率 = 成功 episode 总数 /（被测任务数 × 5）× 100%。每个任务等权的平均成功率在此条件下与总体成功率一致。
- 报告注明“固定初始场景、环境 seed=0、每任务五轮、跨轮上下文隔离”。五轮是小样本稳定性观察，不代表不同场景泛化能力、经验学习后的能力或官方 benchmark 成绩。

## 实验约束

- Agent 观察仅限当前任务官方 rand_obj/camera_mapping 选中的 RGB（所有单臂两路，双臂三路）和单臂state[:23]/双臂state[:46]的机器人本体信息；不要绕过 bridge 读取物体真值位置、物体 qpos 或任务内部状态，也不要读取成功任务的动作日志作为盲测策略。
- 不修改物体位置、奖励、上游成功判定，不把 smoke 的特权 predicate fixture 当作 Agent 成功。
- 只有官方 info.succeed=true 才算成功；termination 或屏幕看起来变色不足以替代判定。summary 的 succeed 会保留本轮是否曾达到官方成功。
- 记录 task、seed、提示、控制模式、动作调用数、步数、早停原因和模型设置。
- 旧探索与调试不属于正式盲测/零样本成绩，迁移不保留旧结果，不把旧成功记录混入新测试。
- 若需要正式 GPT 6 Astra 成绩，由用户在 Codex UI 确认模型选择；bridge 无法独立验证后端模型名称。
- 不下载 π₀.₅ 权重，不索要 API Key；本方案由当前 Codex Agent 使用命令工具操作。

结束时：

~~~bash
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" finish --reason agent_done
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" stop
~~~

保留 manifest.json、events.jsonl、summary.json 和原始相机图片。结果在 ${DEXJOCO_ROOT}/artifacts/<task>-<label>-seed<seed>-<timestamp>/。如果出现崩溃先检查 agent_bridge/runner.log，不用 pkill python 一类宽泛命令排错。

## 人类只读预览（Linux）

- 预览随 runner 启动；CPU 可用 `start --preview-fps 2` 降低编码/发布开销。再次 start 不会改已有 runner 的设置。
- 用 `bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" status` 读取 result.preview 中实际带 token 的 URL；服务器只绑定 loopback，勿公开 token。
- 无桌面服务器无需浏览器，直接查看 observe 图片即可。浏览器在其他机器时需要安全访问服务器 loopback 的链路，不能将浏览器的 localhost 当作服务器。
- 页面可查看允许相机、动作中间帧、目标、步数和 succeed，不能控制/推进仿真。
- Agent 使用 observe/move/hold；思考时保持末帧。只停止自己拥有的 runner，用户观看时先说明。

## 双臂 Agent 约定与接口来源（2026-10-11）

机器人适配来自现有 agent_bridge，不修改上游任务、资源、动力学和成功判定。部署阶段按 MIGRATION.md 重建环境；执行任务时不擅自改依赖/重装环境。

- 上游 dexjoco/tasks/policy_wrappers.py 的 SingleArmPolicyWrapper 原生23D：[xyz3, wxyz4, hand16]。
- 同文件 DualArmPolicyWrapper 原生46D：[right_pose7, left_pose7, right_hand16, left_hand16]，raw env 接收 right/left 两个23D数组的字典；这由上游 wrapper 转换。
- bridge 的 rotvec22/44 输入便利转换采用 dexjoco_openpi_client/dexjoco_openpi_env.py 的格式。44D是 [right_xyz3, right_rotvec3, right_hand16, left_xyz3, left_rotvec3, left_hand16]，不是两个pose6后再两个hand16。这里只使用格式，不调用OpenPI模型。
- 左手顺序 RF/MF/FF/TH，右手 FF/MF/RF/TH，每指4关节；以 contract.hand_order 与模型物理限位为准。
- move 是 bridge 插值封装，双臂必须 --arm right 或 --arm left；另一臂保持上次合法目标（不是重新跟随测量漂移）。hold 保持两臂目标。同步双臂动作通过 act --file 发原生46D序列。
- position_bounds_m 是 bridge 自定验证范围，不是官方统一工作空间规定；四元数均用wxyz，世界系绝对位置，rotvec单位弧度。
- 相机是上游env返回的原生RGB；拼图、PNG、本机 Unix socket bridge 和认证只读网页是本项目封装。

~~~bash
# 已有其他任务时先确认归属，再 finish/stop，不偷偷切换或重置
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" start --task bimanual_assembly --seed 0 --label codex-agent --preview-fps 2
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" contract
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" observe
# 接口小动作，不是解题策略
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" move --arm right --xyz 0.003 0 0 --delta --steps 8
bash "$DEXJOCO_ROOT/agent_bridge/run_cpu.sh" hold --steps 10
~~~

旧部署双臂报告不包含在迁移包中，接口验收不是五任务已由 GPT 完成。Agent 成绩只认本轮 summary 中上游 info.succeed，不能将 terminated 等同成功。

## 相机输入对齐（2026-10-11）

本 bridge 采用官方常规逐任务 `configs/rand_obj/<task>.yaml` 的 `camera_mapping`。

| task | policy role → 原生图像键 | 独立视角数 |
| --- | --- | --- |
| click_mouse | base → ego_right；wrist → wrist | 2 |
| fold_glasses / hammer_nail / pick_bucket / pinch_tongs / water_plant | base → front；wrist → wrist | 2 |
| 全部5个双臂任务 | base → ego；wrist_left → wrist_left；wrist_right → wrist_right | 3 |

`task_registry.py`直接读取部署根目录的官方 YAML；本机副本读取 `upstream/configs`。缺少配置、机器人类型不符、相机映射非法时拒绝启动，不回退成更多相机。为兼容现有工具，`images`、`camera_keys`、PNG文件名和网页按钮保留原生图像键；`camera_mapping`明确标注官方policy角色到原生图像键的对应关系。

Agent的单图、观察拼图、存档、实时预览帧和按钮使用同一组相机。未选中视角即使仍由上游环境渲染，也不进入上述输出；预览的frame/stream接口拒绝请求这些视角。预览会显示动作中间帧，Agent在每段动作之后获取观察，因此“相机一致”不代表两者的采样时间和JPEG压缩完全相同。

- `rand_full` 中 base 为随机相机；当前部署仍是 `randomize=False`，没有切换至该评测模式。
- `multi_task` 单臂的 wrist_left 和 wrist_right 指向同一张 wrist 图像，三键仅代表两个独立视角；当前 bridge 未启用 Multi-Task 输入复制。
- `contract`、`status`、`observe`、`manifest.json`、`summary.json`记录camera_profile、camera_mapping_source、camera_mapping、camera_config_sha256；新episode可追溯到所读配置。迁移包不带旧 episode/报告；新实验生成新结果，不追溯改写旧成绩。
- 此修改仅对齐相机选择；控制时钟、图像分辨率、动作接口及其他评测差异仍存在，不应宣称已完全复现官方评测协议。
