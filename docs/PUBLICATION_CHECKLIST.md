# macOS NCS2 repo 发布检查单

这个目录包含本机实验工作区和允许公开的源码。用户已于 2026-11-11 授权建立 public repo；首批内容为实验源码预览，正式开源发行仍需下面的检查：

仓库：[ncs2-macos](https://github.com/Nishikori-Yui/ncs2-macos)。只推送允许清单中的源码、脚本、测试和摘要文档，不建立二进制 release。

- 选择并提交本项目原创代码的许可证；`THIRD_PARTY_NOTICES.md` 目前保留“尚未选择”。公开可见不等于已授予开源许可；当前源码预览不声称完成开源发行。
- 保留 `README.md`、`docs/`、`scripts/`、`ncs2`、`ncs2_log_run.sh`、C++/Python/Swift 源码、锁文件和小型合成 fixture。
- 不提交 `vendor/`、`build-arm64/`、`firmware-cache/`、`results/`、`ncs2_read_attempts.md`、临时日志、USB 序列号或带绝对机器路径的输出。
- 重新确认 OpenVINO 源码、子模块和固件的来源、许可证与是否允许再分发；源码构建成功不等于固件可以打包发布。
- 在另一台原生 arm64 macOS 上从锁文件复现 `fetch --offline`、`build`、`build-probe`、`check`；没有设备时至少完成 IR export 和 Python 回归。
- 对可搬移 runtime 做一次 clean-room 路径测试：不要依赖当前 checkout 的 CMake cache；检查 rpath、plugins.xml、libusb 路径和动态依赖。
- 运行 `./ncs2 suite --export-only`，保存公开摘要而不是机器原始报告；硬件报告应移除序列号、总线地址和绝对路径。
- 运行 `./ncs2 perf`、worker Python demo、Swift parse/协议 demo，并明确设备型号、固件版本、OpenVINO 兼容线和未支持算子。
- 公开文档继续说明：2026 年最新版 OpenVINO 不会自动识别 NCS2；现代部分是 macOS arm64 wrapper/worker，设备兼容 runtime 仍固定在 2022.3.x。

建议第一版仓库只发布源代码和复现脚本，提供一个用户自行构建的本地 runtime 目录布局；是否发布编译产物和固件必须在许可证审查后单独决定。

已实现的本地准备：package 相对加载路径副本已经搬移后实机验证；source-export 会导出源码允许清单及 SHA-256。上述发行审核仍需保留，见 PACKAGING.md。
