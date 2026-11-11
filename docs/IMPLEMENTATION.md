# 实施计划与验收证据

截至 2026-11-11，本机原型六阶段均已有代码和验证产物。公开仓库为 [ncs2-macos](https://github.com/Nishikori-Yui/ncs2-macos)，内容为实验源码预览；原创许可证仍待选择，没有二进制/固件发行。没有使用 Linux、虚拟机、Rosetta、x86_64 runtime 或摄像头。

| 阶段 | 实现 | 证据与边界 |
| --- | --- | --- |
| 1 原生工具链 | 固定 OpenVINO 2022.3.2 源码、子模块和固件；原生编译 MYRIAD | 9 个非系统 Mach-O 均为 arm64；隔离的新源码/依赖在本机完整复现，第二台 Mac 未验收 |
| 2 算子/IR | 20 个 IR v11 往返；独立标量参考及序列化属性断言 | 完整 20 项实机运行中 19 项通过、FakeQuantize 编译拒绝 |
| 3 性能/协议 | USB 生命周期、XLink runtime 事件、3 种卷积、并发和预处理对照 | 小卷积 3 轮，大卷积 1 轮；逐元素校验；XLink 日志不是 USB 包抓取 |
| 4 现代 API | 常驻 worker、严格版本化 JSON/张量、Python/Swift 客户端 | 超时/进程回收；内置模型及外部静态 f32 IR；单输入输出，16,384 元素上限 |
| 5 无摄像头样例 | WAV 读回→声谱→未训练小 CNN；合成时序异常分数 | 各 3 次实机核对；异常恰为样本 42/43；音频不是分类器 |
| 6 repo 准备 | 可搬移本地 runtime、来源/许可记录、允许清单源码导出 | 新路径实机与 IR 加载通过；原创许可/第二台 Mac/发行签名仍待决定或验证 |

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
- 恢复扩展：独立 recovery-only 与完整默认流程均在第 6 次尝试成功，ready 分别为强杀后 16.575/16.663 秒；前 5 次 USB 为 booted f63b，第 6 次为 bootloader 2485。默认预算改为 10 次，每次失败间隔 3 秒。完整新报告 results/worker-soak-20261111-183417-b6f48cce/report.json：120.001 秒、73,569 次 soak 推理，全阶段 73,574 次、2,648,664 输出核对，正常重启与强杀恢复均通过。仅证明本机两次有界恢复观测，不等于物理拔插恢复。
- 全新依赖复现：最终源码快照报告 `results/reproduce-physical-verified-20261111/report.json` 的 8 个阶段全部退出码为 0；全新源码快照和依赖、离线锁定校验、原生 arm64 构建、架构审计、12 项 IR export/reload、141 项非硬件回归与源码导出均通过。报告标记 `fresh-source-and-dependencies-on-current-host`、`hardware_executed=false`。此前的 `curl 18` / early EOF 和 DNS 解析失败均保留；有界重试后完成复现。
- 新增 `./ncs2 check --json`：本机实测 9 个非系统 Mach-O 全为 arm64，逐项输出依赖边和 SHA-256；外部绝对路径以 basename 表示。三项非硬件回归覆盖递归依赖清单、x86_64/universal 架构拒绝及真实 launcher 的单一 JSON 输出和 notebook 写入；动态库自己的安装名不作为依赖边。
- 清单扩展后的全新联网复现未完成：`results/reproduce-architecture-inventory-20261111/report.json` 在 fetch-online 遇到 DNS 失败；联网重试 `results/reproduce-architecture-inventory-retry-20261111/report.json` 遇到 early EOF，后续两次 fetch 因 shallow.lock 存在失败。两项均保留 failed，不声称此版本通过全新联网重建；上面的 141 项完整复现属于较早源码快照。
- 算子扩展：新增 `avgpool_2x2`、`maxpool_2x2`、`concat_channels`、`broadcast_channels`，C++/Python 独立参考和序列化 op 类型断言通过；16 项 IR export/reload 全部通过，见 `results/suite-20261111-213348-c28a15aa/report.json`。正常 USB 权限下完整 16 项实机重跑，15 项通过，standalone FakeQuantize 仍 compile 拒绝；每项 3 次预热 + 20 次计时，15 项全部输出最大绝对误差 0。新增四项共核对 736 元素；硬件报告为 `results/suite-20261111-213649-db869fd8/report.json`，细表见 OPERATOR_SUITE.md。首次受限进程的 device_enumeration 失败及空 USB 列表如实保留，并经正常权限下 USB 读取和全套运行纠正，未记为设备断连或算子不支持。本轮使用已有锁定 runtime，未重复全新联网构建。
- 扩展后完整非硬件回归再次运行：144 项，23.252 秒全部通过。两份完整算子报告及全部 XML/BIN 的 SHA-256、op 类型、源码/二进制版本和 765,624 个设备元素的累计检查数另外核对通过；本机记录见 `results/operator-expansion-development.md`、`results/operator-expansion-verification.json`。
- 算子边界扩展：20 项 IR 导出及重新读取通过，报告 `results/suite-20261111-220041-5779c35b/report.json`；四项新增案例单独在 MYRIAD 通过，报告 `results/suite-20261111-220107-4635809b/report.json`，每项 3 次预热、20 次计时及逐元素核对。平均池化的 `exclude_pad` 初版参数和 oracle 曾不一致；核对 API 后改为 `false` 并增加 IR 属性断言，详见本地记录。
- 完整 20 项硬件复跑：`results/suite-20261111-220616-682f3359/report.json`，19 项通过，FakeQuantize 编译拒绝，套件按设计退出 1。成功项共核对 766,222 个输出元素，最大绝对误差均为 0；独立脚本核对了案例、运行状态、IR 哈希和构建来源。
- 多输入/输出前置实验：`./ncs2 multi-io` 在 MYRIAD 编译并运行两个 [1,4] f32 输入、加法和减法两个输出的固定图，23 次推理、184 个元素全部通过标量校验，日志见 `results/multi-io-development.md`。这是底层能力证据；worker schema 1 和现有客户端仍为单输入/输出。新增工具后完整非硬件回归 144 项通过，依赖审计覆盖 10 个 arm64 非系统 Mach-O。
- 独立硬件 smoke：从最终全新构建单独运行 `MYRIAD` ReLU，输出 `[0, 0.5, 0, 3]`，最大绝对误差 0。记录在 `results/reproduce-physical-verified-20261111/separate-hardware-smoke.json`；它与不访问 USB 的复现报告分开统计。受限执行上下文曾报告 `device_count=0`；正常 macOS USB 执行发现设备并通过。
- 手动 macOS arm64 workflow 已静态解析并限制为自托管物理机标签；当前公开仓库的自托管 runner 数量为 0，未触发 workflow，也未做第二台 Mac 验收。
- 完整命令/失败/修复写入 ncs2_read_attempts.md；公开文档保留摘要，results/dist/build/vendor 不进源码候选。

## 发行和扩展边界

原创代码许可证未选择；公开 repo 已获用户授权，正式开源发行尚未完成。固件/本地包再分发未确认，包仅本地用。同机隔离复现和从全新构建执行的独立 smoke 已通过，但第二台 Mac 从零复现、Developer ID 签名/公证、小时或天级稳定性、物理断连恢复、功耗仍未验证。短时 soak、正常重启与扩大有限预算后的进程强杀恢复已验证；立即恢复及原 5 次预算测试仍失败。没有实现 ONNX 转换器、自定义 Myriad 内核编译器或现代大模型支持。现代上层接口不等于把 MYRIAD 插件移植进 2026 年 OpenVINO。

本地实现阶段已有验收证据，完整长期目标保留 active，后续继续许可选择、跨机器复现和更广模型测试。
