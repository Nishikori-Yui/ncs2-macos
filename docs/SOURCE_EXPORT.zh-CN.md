# 仅源码候选导出

`./ncs2 source-export` 会在 `dist/` 下创建一个新的公开源码候选目录，并写入 manifest 和 inventory digest。它不会发布、初始化 Git、选择许可，也不会访问硬件。

```sh
./ncs2 source-export --output dist/candidate
python3 -B scripts/export_source.py --check dist/candidate
```

## 选择规则

导出器使用显式白名单，覆盖命令入口、锁文件、Python 客户端、C++ worker 和 probe、构建/审计/导出脚本、核心测试、SwiftPM 文件和公开文档。缺少必要文件或源码目录时导出失败。

当前选择范围包括：

- 根目录明确列出的文件，以及可选的 `LICENSE` 和 `ncs2*.cpp` / `ncs2*.hpp`；
- `docs/*.md`、`scripts/*.py`、`scripts/*.sh`、`tests/test_*.py`、`examples/*.swift` 和 `swift-tests/*.swift`；
- `README.zh-CN.md` 与 `docs/*.zh-CN.md` 中文文档；
- `.md`、`.json`、`.py`、`.sh`、`.cpp`、`.hpp` 和 `.swift` 后缀。

vendor 源码、构建产物、固件缓存、运行时二进制、results、原始 notebook、日志、缓存和嵌套源码目录都会被排除。

## 校验

导出器会先读取所有文件并建立字节快照，再创建输出目录。输入必须是普通文件、只有一个硬链接、UTF-8 有效且不含二进制签名或禁用控制字符。单文件上限为 16 MiB，总大小上限为 128 MiB。

`--check DIRECTORY` 会拒绝缺失文件、额外文件、软链接、非普通文件、重复 manifest key、不安全路径、内容变化和模式变化。

manifest 使用 schema 2，为每个文件记录字节数、SHA-256 和 executable 标记，并为排序后的文件记录生成汇总 digest。

## 发布边界

`published` 和 `release_ready` 始终为 false。根目录 `LICENSE` 的存在只表示项目选择了自己的许可，不代表第三方运行时或固件可以再分发。

发布前仍需检查确切候选目录、凭据、第三方来源、许可范围和硬件复现结果。
