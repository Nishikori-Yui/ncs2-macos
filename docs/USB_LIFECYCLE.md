# USB 固件启动与推理生命周期

运行 ./ncs2 trace --case conv_1x3x16x16。worker 会明确编译模型，由既有 runtime 启动官方固件；编译后保留模型 1 秒，进行两次卷积推理并校验，然后正常退出。采样器仅调用 libusb 枚举和缓存描述符接口，不 open/claim/reset/control/bulk，不占用设备接口。

## 2026-11-11 最终实测

默认 100 ms 采样间隔（不含每次采样耗时），本次共 39 次。参考执行结果通过，所有 bootloader/booted/退出后恢复检查为 true。

| 相对主机开始时间 s | 枚举状态变化 |
| ---: | --- |
| 0.000 | 03e7:2485 / speed=3 |
| 0.442 | 03e7:2485 / speed=3, 03e7:f63b / speed=4 |
| 0.555 | 03e7:f63b / speed=4 |
| 2.619 | (枚举间隙) |
| 2.725 | 03e7:2485 / speed=3 |

本机已安装的 libusb 头文件定义 speed=3 为 high-speed 480 Mbit/s，speed=4 为 SuperSpeed 5000 Mbit/s。实际推理窗口观察到 03e7:f63b / speed=4，退出后恢复 03e7:2485 / speed=3。

这纠正了此前只读 bootloader 描述符后作出的“全程 USB2”推断。不能因为启动前显示 high-speed，就把推理性能当作 USB2 结果；本次也不是强制 USB2 与 USB3 的受控 A/B 性能对比。

## 观察边界

- 同时看见 2485 与 f63b 的短暂状态记录为枚举过渡，不能据此声称有两根棒子。
- 采样是离散观察；转换可能发生在相邻采样之间，不能把采样时间当作精确 USB 重连时间。
- 主机采样时间和子进程 event elapsed_ms 分别保存；observed_elapsed_s 是接收事件时刻，存在调度/传输延迟。
- 当前事件为图构造、compile、infer、teardown、process_exit；尚未进入 XLink stream/buffer 协议层，不是 USB 包抓取。
- 运行期间只读 cached descriptors，未声称已读取 booted 配置端点。显式固件上传由原有 runtime 负责，没有额外手工上传或设备 reset。
- passed 要求推理正确、观察线程终止、没有采样错误、确实看见 bootloader 和 booted、并且进程退出后看见 bootloader。只跑完命令不足以判通过。

原始证据：results/usb-trace-20261111-155311-82da7b4d/report.json

后续继续：XLink 事件与流/缓冲区观察、并发请求吞吐、主机预处理对照。功耗仍需独立测量；USB2/3 对照需实际控制链路条件。
