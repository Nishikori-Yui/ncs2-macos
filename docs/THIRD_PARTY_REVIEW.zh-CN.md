# 第三方来源审查快照

本文档记录 NeuralStick Bridge 的源码级来源审查。仓库原创源码使用 MIT License；本文档不授予 Intel 固件再分发权限，也不代表打包后的运行时已经获得分发许可。

审查依据是 `toolchain.lock.json` 中锁定的 OpenVINO 源码、子模块和固件输入。OpenVINO 版本为 2022.3.2，具体提交和文件哈希以锁文件为准。

OpenVINO checkout 还包含独立的 runtime third-party programs 文件，其中包含 oneDNN、Intel Threading Building Blocks、OpenCL、Caffe、gflags 等组件的通知和许可材料。未来如果分发二进制，必须保留适用的上游许可和署名材料，不能只把 OpenVINO 根目录许可当作完整通知集合。

锁定的子模块各自保留上游许可文件。项目不会把它们合并成一个统一许可，也不会用项目 MIT License 覆盖这些代码。

bootstrap 会校验官方 OpenVINO Myriad 固件归档和解压成员的 SHA-256。固件缓存被 `.gitignore` 忽略，固件不会提交到公开仓库。哈希只能证明实际测试的是哪些字节，不能证明拥有再分发这些字节的权利，因此固件再分发状态仍未解决。

根目录 `LICENSE` 只适用于项目原创源码，不替代上游通知，也不解决固件和复制运行时二进制的许可问题。

详细的版本、来源和上游链接请以英文版 [THIRD_PARTY_REVIEW.md](THIRD_PARTY_REVIEW.md) 以及 `toolchain.lock.json` 为准。
