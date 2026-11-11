# 实施计划与验收证据

截至 2026-11-11，本机原型六阶段均已有代码和验证产物。公开仓库为 [ncs2-macos](https://github.com/Nishikori-Yui/ncs2-macos)，内容为实验源码预览；原创许可证仍待选择，没有二进制/固件发行。没有使用 Linux、虚拟机、Rosetta、x86_64 runtime 或摄像头。

| 阶段 | 实现 | 证据与边界 |
| --- | --- | --- |
| 1 原生工具链 | 固定 OpenVINO 2022.3.2 源码、子模块和固件；原生编译 MYRIAD | 9 个非系统 Mach-O 均为 arm64；本机通过，新机器未复现 |
| 2 算子/IR | 12 个 IR v11 往返；独立标量参考 | 11 个实机通过，standalone FakeQuantize 编译拒绝；默认套件保留非零退出 |
| 3 性能/协议 | USB 生命周期、XLink runtime 事件、3 种卷积、并发和预处理对照 | 小卷积 3 轮，大卷积 1 轮；逐元素校验；XLink 日志不是 USB 包抓取 |
| 4 现代 API | 常驻 worker、严格版本化 JSON/张量、Python/Swift 客户端 | 超时/进程回收；内置模型及外部静态 f32 IR；单输入输出，16,384 元素上限 |
| 5 无摄像头样例 | WAV 读回→声谱→未训练小 CNN；合成时序异常分数 | 各 3 次实机核对；异常恰为样本 42/43；音频不是分类器 |
| 6 repo 准备 | 可搬移本地 runtime、来源/许可记录、允许清单源码导出 | 新路径实机与 IR 加载通过；原创许可/第二台 Mac/发行签名仍待决定或验证 |

## 本轮证据

- 恢复观测更新后 123 项非硬件整合回归全部通过（22.249 秒）；包括客户端、源码导出及 8 项 soak 数值/RSS/监督器/有限重试测试，后者包含真实 SIGTERM 忽略进程的强制回收。
- 性能：results/perf-20261111-165442-c6ec393c/report.json（3 轮×5 模式，每项 5 warmup+40 measured），529,200 输出校验。
- 大卷积：results/perf-20261111-173115-db71dab7/report.json，1,656,000 输出校验。
- 最终性能故意扰动：results/perf-20261111-173144-21e08c60/report.json，4 模式 validation 失败，均 hardware_executed=true。
- Worker：results/worker-20261111-165650-7fd4b6a5/report.json；后续外部 relu_4 IR 和 CLI 流式交互见 notebook。Swift 784 输出独立校验且退出 0，见合并开发记录。
- 应用：results/apps-hardware-20261111-1702/report.json；零容差负向 results/apps-hardware-negative-20261111-1704/report.json。
- XLink：results/xlink-20261111-173035-7b39a10e/report.json，保留原始日志和哈希。
- 搬移：results/relocation-20261111-1734/report.json，动态库来自副本；IR frontend 搬移也通过。
- 长跑：results/worker-soak-20261111-180642-c04d3699/report.json，同一 worker 120.002 秒、71,703 次推理，65 组输入，所有输出正确；RSS 12 次采样为 44,957,696～45,039,616 bytes，3 次正常重启退出 0。随后 SIGKILL 后 5 次启动均未 ready，整项实验 failed；没有声称进程强杀后可立即自动恢复。首次同类失败也保留在 results/worker-soak-20261111-180011-73a4672d/report.json。详见 WORKER_STABILITY.md。
- 恢复扩展：独立 recovery-only 与完整默认流程均在第 6 次尝试成功，ready 分别为强杀后 16.575/16.663 秒；前 5 次 USB 为 booted f63b，第 6 次为 bootloader 2485。默认预算改为 10 次，每次失败间隔 3 秒。完整新报告 results/worker-soak-20261111-183417-b6f48cce/report.json：120.001 秒、73,569 次 soak 推理，全阶段 73,574 次、2,648,664 输出核对，正常重启与强杀恢复均通过。仅证明本机两次有界恢复观测，不等于物理拔插恢复。
- 完整命令/失败/修复写入 ncs2_read_attempts.md；公开文档保留摘要，results/dist/build/vendor 不进源码候选。

## 发行和扩展边界

原创代码许可证未选择；公开 repo 已获用户授权，正式开源发行尚未完成。固件/本地包再分发未确认，包仅本地用。第二台 Mac 从零复现、Developer ID 签名/公证、小时或天级稳定性、物理断连恢复、功耗仍未验证。短时 soak、正常重启与扩大有限预算后的进程强杀恢复已验证；立即恢复及原 5 次预算测试仍失败。没有实现 ONNX 转换器、自定义 Myriad 内核编译器或现代大模型支持。现代上层接口不等于把 MYRIAD 插件移植进 2026 年 OpenVINO。

本地实现阶段已有验收证据，完整长期目标保留 active，后续继续许可选择、跨机器复现和更广模型测试。
