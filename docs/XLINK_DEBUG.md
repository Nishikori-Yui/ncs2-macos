# XLink/MVNC 可观测性

固定的 OpenVINO 2022.3.2 Release runtime 支持通过 `NCS2_LOG_LEVEL=LOG_DEBUG` 设置 MYRIAD 的公开 `LOG_LEVEL` 属性。2026-11-11 在 macOS 原生 arm64 上运行：

```sh
NCS2_LOG_LEVEL=LOG_DEBUG ./ncs2 smoke
```

命令成功退出，完成真实 ReLU 推理并正常关闭设备。日志中观察到：

- GraphCompiler 的 84 个编译 pass；
- XLink stream 打开/关闭；
- `XLINK_WRITE_REQ`、`XLINK_READ_REQ` 及对应 response；
- MVNC 的 `write count`、`output length`、`Trigger start`、`Trigger end`；
- 设备关闭时的 `XLINK_RESET_REQ` 和 dispatcher 清理。

这证明固定 runtime 已经能暴露 XLink/MVNC 生命周期和读写事件，可用于比较不同图和请求深度的事件数量。它不等于 USB 总线包抓取，也没有把事件与每一笔 USB bulk packet 做一一对应。日志末尾多次出现 `XLinkStreamReset: Cannot destroy semaphore` 的 Debug 文本，但本次进程退出码为 0、推理结果正确、设备正常恢复到 bootloader；因此先记录为清理路径的可疑提示，不能直接当作硬件失败。公开发布前应在多次启动/退出和长时运行中复核。

原始带颜色控制符的完整输出只保留在本机 notebook；公开仓库应只放这种脱敏摘要和复现实验命令。

## Reproducible report

Run ./ncs2 xlink. Final report: results/xlink-20261111-173035-7b39a10e/report.json. It records 80 dispatcher send and 80 incoming-event records, 5 stream-open mentions, one FIFO input write (4 elements), one FIFO output read (16 bytes), and one trigger pair. Create-stream fields include 64-byte input/output requests. Event size fields have event-specific meanings; they are not measured USB payload bytes or throughput. Repeated debug mentions are not distinct transfers.

The 27 semaphore-cleanup debug messages remain with validated inference and exit 0. No upstream instrumentation patch was required.
