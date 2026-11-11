# 不涉及摄像头的实验方向

已跑通的基础是：macOS 原生 arm64 主机 → OpenVINO 2022.3.2 MYRIAD → NCS2。现在已实现算子/shape IR 测试集和 USB 生命周期采样；其余计划状态见 IMPLEMENTATION.md。

| 方向 | 已验证 / 可以做的东西 | 下一项可验证实验 |
| --- | --- | --- |
| USB 设备考古 | 已完成描述符读取、层级端点解析、bootloader/runtime PID 和速度变化观察；保存的配置描述符可离线解析 | 在可控硬件链路条件下做 USB 2/3 A/B；继续保持只读观察，不向任意端点盲发数据 |
| XLink 协议观察 | 已完成结构化 stream、FIFO、trigger、read/write 和 reset 生命周期日志 | 对不同图、请求深度和重启路径比较事件计数；日志仍不等于 USB 包抓取 |
| 性能实验室 | 已完成冷启动、预热、p50/p95、张量尺寸、重复请求、并发深度和主机/设备预处理对照 | 增加更大模型和更长 soak；功耗与能效必须用独立仪器测量 |
| 数值实验 | 20 个 IR v11 fixture 全部往返；此前完整 16 项中 15 项实机通过，standalone FakeQuantize 编译拒绝；另有 4 项边界案例实机通过 | 继续扩大 shape、属性与组合图的边界，并记录任意用户模型的兼容限制 |
| 离线音频 | 已完成 WAV 读回、声谱预处理和未训练小 CNN 的数值验证；它没有分类语义 | 测试更多合法/非法 WAV 输入；训练分类器属于另一个数据和许可项目 |
| 时间序列 | 已完成固定合成序列的异常分数和已知 spike 核对 | 扰动窗口、阈值和事件位置，检查误报/漏报边界 |
| 现代 macOS API | 已完成独立 arm64 worker、版本化 JSON、Python/Swift 客户端和超时/恢复边界 | 稳定多输入/多输出与更多模型元数据，再评估面向 macOS 的发布接口 |

性能报告应同时保留模型、shape、数据类型、USB 连接速率、预热次数、样本量和误差，不把 1x4 ReLU 的毫秒数外推为通用推理速度。功耗或能效需要额外测量，不能从延迟推算。

当前优先顺序：第二台实体 arm64 Mac 复现 → 许可证和第三方来源审查 → 签名/公证路径 → 更广模型兼容性 → 受控 USB 2/3 和功耗实验。自定义 Myriad 内核编译器和现代大模型不在已验证能力内。

## 2026 年适合继续折腾的用途

1. **USB 设备考古**：比较 bootloader `03e7:2485` 与 runtime `03e7:f63b` 的枚举、速度、描述符和生命周期；把保存的十六进制描述符做离线解析、畸形输入测试和差异报告。
2. **协议观察**：收集 XLink/MVNC 的 stream、FIFO、trigger、read/write、close/reset 事件，建立事件计数与图大小、请求深度、重启阶段的对照表。它适合做 runtime 事件考古，不等于 USB packet capture。
3. **性能实验室**：做请求深度、同步/异步、静态 shape、主机/设备预处理、USB 2/3 链路和外部功耗的 A/B；保存启动、推理、P50/P95、吞吐、温度和电能，不把小 ReLU 的延迟外推成 TOPS。
4. **算子兼容性地图**：average/max pooling、concat 和 broadcast 的八个静态 fixture 已在两次实验中通过实机验证，覆盖一个 padding、宽度轴与空间广播边界；继续增加属性、shape 和图组合。每项用独立标量参考，保留编译拒绝证据；具体 fixture 的结果不等于整个算子家族都支持。
5. **macOS 原生推理服务**：让 Swift、Python 或其他客户端只接触版本化 JSON-lines worker；旧 runtime 固定在 arm64 进程中，现代应用不链接旧 OpenVINO ABI，也不需要 x86_64 或 Rosetta。
6. **离线信号实验**：用 WAV、合成时序、传感器日志、音频特征或小型控制信号测试张量布局、数值误差和恢复机制；不需要摄像头，也不把 fixture 冒充成训练好的分类器。

最值得开源的是这层工具链和证据格式：`ncs2 usb`、离线 descriptor parser、`check --json`、worker schema、IR fixture catalog、性能报告和复现脚本。固件、runtime 二进制、第三方来源和许可证仍应单独审查后再决定是否发布。
