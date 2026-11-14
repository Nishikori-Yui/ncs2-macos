# NeuralStick Bridge

面向 Apple Silicon macOS 的 Intel Neural Compute Stick 2 原生工具链。

NeuralStick Bridge 将 NCS2 已停止维护的 MYRIAD 运行时桥接到现代 macOS 应用。项目在原生 arm64 macOS 上构建仍保留 MYRIAD 支持的 OpenVINO 2022.3.2，并通过命令行探针、常驻 JSON worker，以及 Python/Swift SDK 提供设备访问能力。

英文文档入口：[README.md](README.md)。

## 项目提供的能力

- 原生 arm64 macOS 版本的 OpenVINO 2022.3.2 MYRIAD 运行时构建流程。
- 将旧版 OpenVINO ABI 隔离在 C++ worker 进程中的进程边界。
- 版本化的 schema 1 和 schema 2 JSON-lines worker 协议。
- 同步 Python/Swift 客户端，以及有界异步 Python/Swift SDK 门面。
- 只读 USB 描述符读取和递归 arm64 依赖闭包审计。

项目面向应用集成和设备访问。复现套件、长时间 soak、性能实验室、生命周期追踪和摄像头集成不属于公开工具链。

## 环境要求

- 原生 arm64 macOS、Xcode 或 Command Line Tools。
- Python 3.11+。
- CMake、pkg-config 和 arm64 libusb。Homebrew 可通过 `brew install cmake pkg-config libusb python` 提供这些工具。
- 可以从终端正常访问的 NCS2。

构建脚本不会安装系统软件包，也不需要 sudo。

## 快速开始

```sh
./ncs2 fetch
./ncs2 build
./ncs2 build-swift
./ncs2 check
./ncs2 usb
./ncs2 list
./ncs2 smoke
```

`fetch` 下载并校验锁定的 OpenVINO 源码和官方固件归档，内容只保存在本地忽略目录。`build` 生成 arm64 运行时、MYRIAD 插件、IR reader 和命令行探针。`check` 递归验证非系统 Mach-O 文件是否为纯 arm64。

设备启动需要本地固件。固件不由本仓库分发；根目录 MIT License 只覆盖本项目原创源码。

## 工作原理

现代应用不会直接链接旧版 OpenVINO ABI。Python 或 Swift 客户端启动原生 C++ worker，worker 为 `MYRIAD` 加载并编译 IR 模型，两个进程通过 stdin/stdout 交换有界 JSON-lines。异步 SDK 在这一协议之上提供模型注册、FIFO 调度、任务状态、排队任务取消、结果保留和 worker 复用。

这样可以让现代应用层保持当前语言和运行时，同时把设备专用运行时和固件处理留在 macOS 工具链中。

## 推理与 worker

```sh
./ncs2 worker --model path/to/model.xml
./ncs2 multi-worker --model path/to/model.xml
```

schema 1 支持一个静态 f32 输入和一个静态 f32 输出。schema 2 支持 1–32 个静态 f32 输入和输出，并限制张量 rank、单张量元素数和聚合元素数。worker 编译一次图，然后通过 JSON-lines 接收请求。

参阅：

- [Worker API](docs/WORKER_API.zh-CN.md)
- [异步 SDK](docs/ASYNC_SDK.zh-CN.md)

## Swift 与 Python SDK

`ncs2_async.NCS2Runtime` 和 Swift `NCS2Runtime` 提供：

- 显式模型注册；
- 单设备 FIFO 队列；
- 默认最多 32 个等待任务；
- 排队任务取消；
- 任务状态和结果读取；
- 显式结果释放；
- 相邻任务的 worker 复用；
- 模型切换时按队列顺序关闭和启动 worker；
- 稳定的错误分类。

运行中的硬件推理不能取消。调用方取消的是等待操作，设备推理仍会完成。

`Package.swift` 暴露 `NCS2Kit`，支持 macOS 13+ arm64，不携带 worker、OpenVINO、libusb 或固件二进制。

## 目录结构

- `ncs2`：命令分发和本地日志入口。
- `ncs2_openvino_probe.cpp`：设备发现、ReLU smoke test 和 benchmark。
- `ncs2_worker.cpp`、`ncs2_multi_worker.cpp`：schema 1/2 worker。
- `ncs2_client.py`、`ncs2_multi_client.py`：同步 Python 客户端。
- `ncs2_async.py`：异步 Python SDK。
- `examples/`：Swift 客户端和 `NCS2Kit` 源码。
- `read_ncs2_usb.py`：只读 USB 描述符读取器。
- `scripts/`：依赖准备、构建、架构审计和源码导出。
- `docs/`：工具链、协议、SDK 和发布边界文档。

## 文档

- [工具链说明](docs/TOOLCHAIN.zh-CN.md)
- [Worker API](docs/WORKER_API.zh-CN.md)
- [异步 SDK](docs/ASYNC_SDK.zh-CN.md)
- [源码导出](docs/SOURCE_EXPORT.zh-CN.md)
- [第三方来源审查](docs/THIRD_PARTY_REVIEW.zh-CN.md)

## 许可与第三方材料

本仓库原创源码使用 [MIT License](LICENSE)。OpenVINO、libusb、Intel 固件和复制的运行时二进制保留各自的许可条款。固件和运行时二进制的再分发权尚未解决，因此本仓库不发布这些二进制。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [第三方来源审查](docs/THIRD_PARTY_REVIEW.zh-CN.md)。
