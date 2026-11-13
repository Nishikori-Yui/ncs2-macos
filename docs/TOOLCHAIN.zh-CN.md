# macOS arm64 工具链

## 设计

```text
macOS arm64 进程
        |
        +-- read_ncs2_usb.py + Homebrew libusb
        |
        +-- OpenVINO 2022.3.2 arm64 运行时
                |
                +-- MYRIAD 插件 / XLink / MVNC
                        |
                        +-- NCS2 USB 固件和 MyriadX
```

Intel 在 OpenVINO 2022.3 之后移除了 MYRIAD 支持。因此 NeuralStick Bridge 构建一个锁定版本的兼容运行时，同时保持面向应用的层简洁：原生进程检查、显式路径、版本化 worker 协议、Python/Swift 客户端和 arm64 架构审计。

安装当前版本的 OpenVINO 不会自动发现 NCS2。仓库中的 wrapper 负责把旧设备支持带入当前 macOS 应用。

## 锁定输入

- OpenVINO 源码：`releases/2022/3`，提交由 `toolchain.lock.json` 固定。
- 目标架构：`arm64`。
- 必要构建选项：`ENABLE_INTEL_MYRIAD=ON`、`ENABLE_INTEL_MYRIAD_COMMON=ON`。
- 固件归档：由 `./ncs2 fetch` 获取并保存在本地忽略目录。

精确 URL、归档哈希、解压文件哈希和子模块版本都在 `toolchain.lock.json` 中。bootstrap 脚本在使用源码和下载内容前会先校验字节。固件和 vendor 源码不会进入公开提交。

`scripts/build_runtime.sh` 保存经过验证的 CMake 配置。`THREADING=SEQ` 明确处理缺少 TBB 的构建环境；`CMAKE_POLICY_VERSION_MINIMUM=3.5` 让旧项目可以使用当前 CMake 配置；`ENABLE_PKGCONFIG_GEN=OFF` 避免路径包含空格时的 pkg-config 问题。

MYRIAD 插件在 macOS 上仍使用 `libopenvino_intel_myriad_plugin.so` 文件名，但实际是 Mach-O arm64 库。`./ncs2 check` 会递归审计非系统依赖，并拒绝 universal 或 x86_64 slice。`./ncs2 check --json` 输出架构、依赖边和 SHA-256。

## 原生 host 边界

旧运行时只存在于独立 worker 可执行文件中。应用通过有界 JSON-lines 协议通信，而不是直接链接 OpenVINO 或共享 C++ ABI。这让当前 Python 环境或原生 Swift 应用可以在不依赖 x86_64 host runtime 的情况下使用设备。

该方案移除了已验证推理路径中的 x86_64 host 依赖，但没有替换 Intel 固件、重新实现设备编译器，也没有扩大任意算子兼容范围。锁定的 Xbyak 源码只是构建输入，不是被加载的 x86_64 库。

## 诊断

- 退出码 0 表示检查通过。
- 退出码 2 通常表示没有 MYRIAD 设备、运行时或探针缺失，或没有可访问的匹配 USB 设备。
- `NCS2_LOG_LEVEL=LOG_DEBUG ./ncs2 smoke` 设置插件日志级别。
- `./ncs2 usb --show-serial` 显式显示本地诊断用序列号；默认隐藏。
- `NCS2_LIBUSB` 可指定绝对 libusb 路径，`NCS2_JOBS` 控制并行度，默认值为 4。

沙盒进程和普通 macOS 终端的 USB 访问可能不同。如果 libusb 报告没有设备但 `ioreg` 能看到 NCS2，应从普通终端运行探针。

## 公开范围

公开工具链覆盖依赖准备、原生构建、USB 描述符读取、MYRIAD 发现、确定性的 ReLU smoke test 和 benchmark、schema 1/2 worker、Python/Swift 客户端以及架构检查。复现套件、soak 测试、性能实验室、生命周期追踪和应用实验保留在本地工作流中。
