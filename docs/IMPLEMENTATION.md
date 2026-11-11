# 实施计划与验收证据

截至 2026-11-12，本机原型六阶段均已有代码和验证产物。公开仓库为 [ncs2-macos](https://github.com/Nishikori-Yui/ncs2-macos)，内容为实验源码预览；原创许可证仍待选择，没有二进制/固件发行。没有使用 Linux、虚拟机、Rosetta、x86_64 runtime 或摄像头。

| 阶段 | 实现 | 证据与边界 |
| --- | --- | --- |
| 1 原生工具链 | 固定 OpenVINO 2022.3.2 源码、子模块和固件；原生编译 MYRIAD | 最新干净复建含两个 Swift 客户端，共审计 13 个非系统 Mach-O，均为 arm64；第二台 Mac 未验收 |
| 2 算子/IR | 20 个 IR v11 往返；独立标量参考及序列化属性断言 | 完整 20 项实机运行中 19 项通过、FakeQuantize 编译拒绝 |
| 3 性能/协议 | USB 生命周期、XLink runtime 事件、3 种卷积、并发和预处理对照 | 小卷积 3 轮，大卷积 1 轮；逐元素校验；XLink 日志不是 USB 包抓取 |
| 4 现代 API | schema 1/2 均有 Python/Swift 客户端和 native worker | 静态 f32 IR；最多 32 个端口、每方向总计 16,384 元素；两种语言实测 2→2、异形 2→1、1→2 |
| 5 无摄像头样例 | WAV 读回→声谱→未训练小 CNN；合成时序异常分数 | 各 3 次实机核对；异常恰为样本 42/43；音频不是分类器 |
| 6 repo 准备 | 可搬移本地 runtime、来源/许可记录、允许清单源码导出 | 新路径实机与 IR 加载通过；第三方来源盘点、许可选项和第二台 Mac 操作清单已发布；原创许可决定、跨机复现和发行签名仍待确认或验证 |

## 本轮证据

- USB 描述符离线解析器加入后 141 项非硬件整合回归通过；随后加入纯 arm64 依赖清单、x86_64/universal 拒绝及 CLI JSON 输出测试，当前完整回归为 144 项通过。覆盖客户端、源码导出、严格 USB descriptor 解析及 soak 数值/RSS/监督器/有限重试测试，后者包含真实 SIGTERM 忽略进程的强制回收。
- 性能：results/perf-20261111-165442-c6ec393c/report.json（3 轮×5 模式，每项 5 warmup+40 measured），529,200 输出校验。
- 大卷积：results/perf-20261111-173115-db71dab7/report.json，1,656,000 输出校验。
- 最终性能故意扰动：results/perf-20261111-173144-21e08c60/report.json，4 模式 validation 失败，均 hardware_executed=true。
- Worker：results/worker-20261111-165650-7fd4b6a5/report.json；后续外部 relu_4 IR 和 CLI 流式交互见 notebook。Swift 784 输出独立校验且退出 0，见合并开发记录。
- 应用：results/apps-hardware-20261111-1702/report.json；零容差负向 results/apps-hardware-negative-20261111-1704/report.json。
- XLink：results/xlink-20261111-173035-7b39a10e/report.json，保留原始日志和哈希。
- 搬移：results/relocation-20261111-1734/report.json，动态库来自副本；IR frontend 搬移也通过。
- 长跑：results/worker-soak-20261111-180642-c04d3699/report.json，同一 worker 120.002 秒、71,703 次推理，65 组输入，所有输出正确；RSS 12 次采样为 44,957,696～45,039,616 bytes，3 次正常重启退出 0。随后 SIGKILL 后 5 次启动均未 ready，整项实验 failed；没有声称进程强杀后可立即自动恢复。首次同类失败也保留在 results/worker-soak-20261111-180011-73a4672d/report.json。详见 WORKER_STABILITY.md。
- 25 分钟稳定性：正常 macOS USB 权限下，同一 worker 连跑 1,500.002 秒、855,067 次 soak 推理；所有阶段累计 855,072 次推理、30,782,592 个输出元素最大误差 0，RSS 50 次采样 27,574,272～45,105,152 bytes。三次正常重启通过；强杀后前五次未 ready，第六次在 17.139 秒恢复。没有物理拔插，不能外推小时/天级或其他 Mac。报告：`results/worker-soak-20261112-012354-68fd1fcb/report.json`；无 USB 权限下那次零推理失败保留独立报告。
- 恢复扩展：独立 recovery-only 与完整默认流程均在第 6 次尝试成功，ready 分别为强杀后 16.575/16.663 秒；前 5 次 USB 为 booted f63b，第 6 次为 bootloader 2485。默认预算改为 10 次，每次失败间隔 3 秒。完整新报告 results/worker-soak-20261111-183417-b6f48cce/report.json：120.001 秒、73,569 次 soak 推理，全阶段 73,574 次、2,648,664 输出核对，正常重启与强杀恢复均通过。仅证明本机两次有界恢复观测，不等于物理拔插恢复。
- 全新依赖复现：最终源码快照报告 `results/reproduce-physical-verified-20261111/report.json` 的 8 个阶段全部退出码为 0；全新源码快照和依赖、离线锁定校验、原生 arm64 构建、架构审计、12 项 IR export/reload、141 项非硬件回归与源码导出均通过。报告标记 `fresh-source-and-dependencies-on-current-host`、`hardware_executed=false`。此前的 `curl 18` / early EOF 和 DNS 解析失败均保留；有界重试后完成复现。
- 2026-11-12 最新公开快照干净复建：`results/reproduce-20261112-003435-3155a19e/report.json` 的 8 阶段全部通过，联网拉取与离线锁定校验成功，原生构建、arm64 审计、20 项 IR 导出、166 项非硬件回归和源码导出通过。该批次的首次运行验证了 C++ 工具链，但尚未编译两个 Swift 客户端；为让复建审计覆盖完整最新工具链，随后把 `build-swift` 纳入复建流程并将其放在架构审计之前，见下一轮证据。没有执行 USB 或硬件推理。
- 新增 `./ncs2 check --json`：本机实测 9 个非系统 Mach-O 全为 arm64，逐项输出依赖边和 SHA-256；外部绝对路径以 basename 表示。三项非硬件回归覆盖递归依赖清单、x86_64/universal 架构拒绝及真实 launcher 的单一 JSON 输出和 notebook 写入；动态库自己的安装名不作为依赖边。
- 历史记录：清单扩展后的首次联网复现曾因 DNS、early EOF 及保留的 `shallow.lock` 失败，见 `results/reproduce-architecture-inventory-20261111/report.json` 和 retry 报告。最新公开快照已在 2026-11-12 通过联网干净复建；历史失败仍保留，不再代表当前版本状态。
- 算子扩展：新增 `avgpool_2x2`、`maxpool_2x2`、`concat_channels`、`broadcast_channels`，C++/Python 独立参考和序列化 op 类型断言通过；16 项 IR export/reload 全部通过，见 `results/suite-20261111-213348-c28a15aa/report.json`。正常 USB 权限下完整 16 项实机重跑，15 项通过，standalone FakeQuantize 仍 compile 拒绝；每项 3 次预热 + 20 次计时，15 项全部输出最大绝对误差 0。新增四项共核对 736 元素；硬件报告为 `results/suite-20261111-213649-db869fd8/report.json`，细表见 OPERATOR_SUITE.md。首次受限进程的 device_enumeration 失败及空 USB 列表如实保留，并经正常权限下 USB 读取和全套运行纠正，未记为设备断连或算子不支持。本轮使用已有锁定 runtime，未重复全新联网构建。
- 扩展后完整非硬件回归再次运行：144 项，23.252 秒全部通过。两份完整算子报告及全部 XML/BIN 的 SHA-256、op 类型、源码/二进制版本和 765,624 个设备元素的累计检查数另外核对通过；本机记录见 `results/operator-expansion-development.md`、`results/operator-expansion-verification.json`。
- 算子边界扩展：20 项 IR 导出及重新读取通过，报告 `results/suite-20261111-220041-5779c35b/report.json`；四项新增案例单独在 MYRIAD 通过，报告 `results/suite-20261111-220107-4635809b/report.json`，每项 3 次预热、20 次计时及逐元素核对。平均池化的 `exclude_pad` 初版参数和 oracle 曾不一致；核对 API 后改为 `false` 并增加 IR 属性断言，详见本地记录。
- 完整 20 项硬件复跑：`results/suite-20261111-220616-682f3359/report.json`，19 项通过，FakeQuantize 编译拒绝，套件按设计退出 1。成功项共核对 766,222 个输出元素，最大绝对误差均为 0；独立脚本核对了案例、运行状态、IR 哈希和构建来源。
- 多输入/输出前置实验：`./ncs2 multi-io` 在 MYRIAD 编译并运行两个 [1,4] f32 输入、加法和减法两个输出的固定图，23 次推理、184 个元素全部通过标量校验，日志见 `results/multi-io-development.md`。这是底层能力证据；worker schema 1 和现有客户端仍为单输入/输出。新增工具后完整非硬件回归 144 项通过，依赖审计覆盖 10 个 arm64 非系统 Mach-O。
- schema 2 多张量 worker 初版：新增 `ncs2_multi_worker` 与 `ncs2_multi_client.py`，支持 1..32 个静态 f32 输入/输出，端口按 OpenVINO 顺序编号，每个方向合计不超过 16,384 元素。当轮完整非硬件回归 149 项通过，11 个非系统 Mach-O 全为 arm64。导出目录拒绝覆盖，重复导出失败后 XML/BIN 哈希不变。开发树及打包 runtime 均在 MYRIAD 验证 23 次、184 个元素通过，模型 SHA-256 `e42a842b3a274585b211202e3674484c49e821775922ae00c5f264988cbd0f72`；详细尝试、失败与输出在 `results/multi-worker-development.md` 和本地完整 notebook。当时只覆盖 2×2 图，非对称端口的后续验证见下文；schema 2 仍仅有 Python 客户端，Swift 与任意模型兼容性未验证。
- 独立硬件 smoke：从最终全新构建单独运行 `MYRIAD` ReLU，输出 `[0, 0.5, 0, 3]`，最大绝对误差 0。记录在 `results/reproduce-physical-verified-20261111/separate-hardware-smoke.json`；它与不访问 USB 的复现报告分开统计。受限执行上下文曾报告 `device_count=0`；正常 macOS USB 执行发现设备并通过。
- 手动 macOS arm64 workflow 已静态解析并限制为自托管物理机标签；GitHub Actions API 当前没有该仓库的 workflow runs，也没有第二台 Mac 验收证据。
- 第二台实体 Mac 验收步骤现有公开清单 `docs/SECOND_MAC_CHECKLIST.md`，包括全新来源复现、报告验收字段、可选硬件 smoke/soak 和只回传脱敏摘要的边界。GitHub Actions API 当前返回该仓库 `workflow_runs.total_count=0`；没有第二台机器的执行证据。
- 项目许可证选择材料见 `docs/LICENSE_OPTIONS.md`：Apache-2.0 是工程建议、MIT 是备选；没有替所有者选定或创建根 `LICENSE`。
- 完整命令/失败/修复写入 ncs2_read_attempts.md；公开文档保留摘要，results/dist/build/vendor 不进源码候选。

## 发行和扩展边界

原创代码许可证未选择；公开 repo 已获用户授权，正式开源发行尚未完成。固件/本地包再分发未确认，包仅本地用。来源级第三方盘点已完成，但二进制/固件再分发审查仍未放行。同机隔离复现、从全新构建执行的独立 smoke 和 25 分钟稳定性观察已通过；第二台 Mac 从零复现、Developer ID 签名/公证、小时或天级稳定性、物理断连恢复、功耗仍未验证。没有实现 ONNX 转换器、自定义 Myriad 内核编译器或现代大模型支持。现代上层接口不等于把 MYRIAD 插件移植进 2026 年 OpenVINO。

本地实现阶段已有验收证据，完整长期目标保留 active。后续优先完成第二台实体 Mac 复现、原创许可证选择和二进制/固件再分发审查；模型兼容性扩展保留为后续实验。

### schema 2 非对称端口补充

- 新增 IR v10 fixtures：`concat-unequal` 将 `[1,3]` 与 `[1,5]` 拼为 `[1,8]`；`split-halves` 将 `[1,8]` 拆成两个 `[1,4]` 输出。每次导出后立即重新读回 XML，并断言参数/result 数量、顺序、shape 和 f32 类型；重复导出保护保留。
- 两个 Python worker case 各在 MYRIAD 运行 31 次、检查 248 个元素，最大绝对误差 0。直接 C++ 路径和既有 2→2 加减默认路径也各运行 23 次、检查 184 个元素。完整原始命令和输出：`results/asymmetric-ports-development.md`，合并入本机 `ncs2_read_attempts.md`。
- 完整非硬件回归为 153 项全部通过，11 个非系统 Mach-O 纯 arm64。默认 add-sub 的 worker 路径也复跑 31 次，三个 worker case 合计检查 744 个元素；三个直接 C++ case 合计 552 个元素，本轮实机累计 162 次推理、1296 个输出元素核对。模型哈希、源码和构建哈希的独立核对报告保存在 `results/asymmetric-ports-verification.json`。
- 这证明选定静态 concat/split 图及 schema 的异形端口编排；不是任意 Concat/Split 属性或任意模型兼容性的承诺。试验排除了摄像头、Linux、虚拟机、Rosetta 与 x86_64 runtime。

### Swift schema 2 补充（2026-11-12）

- 新增独立 `NCS2MultiWorkerClient`，复用既有同步 JSON-lines 管道、绝对 deadline 和严格 JSON 解码检查；单输入输出 schema 1 保持原行为。`./ncs2 build-swift` 构建两个 Swift 6 原生 arm64 示例。
- 三种现有 fixture 在 Swift 客户端中各复测 31 次、248 元素，共 93 次推理、744 个输出元素，最大绝对误差 0；各 worker shutdown 退出 0。完整日志在 `results/swift-multi-development.md`，核对报告在 `results/swift-multi-verification.json`。
- 12 项无 USB 测试覆盖三种端口布局、无效 ready/输出、重复键、超长帧、数值故意扰动、局部输入拒绝、远端非致命恢复、超时、SIGTERM 忽略后的回收、正常退出和 deinit。模拟 worker 声称 hardware_executed 仅用于协议验证，不能计入实机证据。
- 本轮完整非硬件回归 166 项、36.762 秒全部通过，包含已有 schema 1 测试和一个可选 Swift 架构清单测试。13 个非系统 Mach-O 纯 arm64；两个 Swift 程序的直接依赖均为 Apple 系统库，没有加载旧 OpenVINO ABI。

### 最新提交干净复建闭环（2026-11-12）

- 复建脚本新增独立 `build-swift` 阶段，要求 Swift 6，并在全量 arm64 审计前构建 schema 1/2 两个示例；因此 `check_arm64` 将检查 7 个 C++ 工具、2 个 Swift 客户端和被打包依赖。
- 当前发布源码快照通过联网依赖获取、离线锁校验、OpenVINO/C++ 构建、Swift 构建、递归架构审计、20 项 IR 导出/回读、167 项非硬件回归及源码导出。`3b2591a` 对应的完整复建报告固定保存在本地 `results/reproduce-3b2591a-20261112/report.json`；该文件及构建产物不纳入公开仓库。

### 当前进度口径与剩余项

按六阶段交付项粗估，本机可用原型约 95%；包含第二台 Mac 与正式开源准备约 85%。这是规划估算，不是测试覆盖率或已测量工时。

当前 Swift 批次已通过整合回归和实机验证；后续按以下项收尾：

1. 在第二台实体 arm64 Mac 上按 `docs/SECOND_MAC_CHECKLIST.md` 复现锁定工具链；当前机器上的最新源码干净复建已经完成。
2. 从 `docs/LICENSE_OPTIONS.md` 的选项中确认并提交项目原创代码许可证；来源盘点已完成，但二进制/固件再分发仍需单独审查。
3. 如果决定发布编译产物，再做 Developer ID 签名、公证和搬移 runtime 的发行审查。

本机可执行的收尾预计分 2–3 批推进。跨设备和发布决策依赖额外条件，不估算总工时；不把新增任意模型或算子作为无限延长首版收尾的前提。
