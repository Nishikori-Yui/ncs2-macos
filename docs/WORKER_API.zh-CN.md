# 本地 Worker API

原生 arm64 C++ worker 持有锁定的 MYRIAD 运行时和已编译的计算图。Python 与 Swift 客户端通过管道交换 UTF-8 JSON-lines，不在自己的进程中加载 OpenVINO。这是本地 API，不是网络服务。

需要异步模型和任务调度时，请使用[异步 SDK](ASYNC_SDK.zh-CN.md)。底层 wire protocol 保持不变。

## 启动 worker

```sh
./ncs2 worker --model path/to/model.xml
./ncs2 multi-worker --model path/to/model.xml
```

schema 1 编译一个静态 f32 输入和一个静态 f32 输出。schema 2 支持 1–32 个静态 f32 输入和输出。每个张量的 rank 为 1–8，最多 16,384 个元素；schema 2 还限制输入和输出的聚合元素数。

worker 完成编译后先发送 ready 对象，然后等待请求：

```json
{"schema_version":1,"type":"ready","case":"model","device":"MYRIAD","validation_only":false,"input":{"dtype":"f32","shape":[1,4]},"output":{"dtype":"f32","shape":[1,4]}}
```

schema 2 的 `inputs` 和 `outputs` 是按端口顺序排列的 `{index,dtype,shape}` 数组。模型无法读取或编译时，进程以非零状态退出。没有 CPU fallback。

## 请求与响应

每个请求包含 `schema_version`、不为空且最多 128 个 UTF-8 字节的 `request_id`，以及 `operation`：

```json
{"schema_version":1,"request_id":"a1","operation":"health"}
{"schema_version":1,"request_id":"a2","operation":"shutdown"}
{"schema_version":1,"request_id":"a3","operation":"infer","input":{"dtype":"f32","shape":[1,4],"data":[-2,0.5,-1,3]}}
```

schema 2 推理使用一个 `inputs` 数组，每个对象对应 ready 描述符中的一个输入端口：

```json
{"schema_version":2,"request_id":"a4","operation":"infer","inputs":[{"index":0,"dtype":"f32","shape":[1,4],"data":[1,2,3,4]}]}
```

成功推理会返回请求身份、`hardware_executed:true`、输出 shape/dtype、耗时和输出数据。health 与 shutdown 会返回已完成推理计数。无效请求返回 `fatal:false` 的错误；运行时或设备失败属于 fatal，会终止 worker。

帧大小最多 1 MiB，JSON 嵌套最多 8 层，单个张量最多 16,384 个元素。重复 key、无效 Unicode、未知字段、版本错误以及 shape/type 不匹配都会被拒绝。

## Python

```python
from pathlib import Path
from ncs2_client import NCS2Worker

with NCS2Worker(
    Path("build-arm64/ncs2_worker"),
    Path("vendor/openvino-2022.3/bin/arm64/Release/plugins.xml"),
    Path("path/to/model.xml"),
) as worker:
    result = worker.infer([-2, 0.5, -1, 3])
    print(result["shape"], result["output"])
```

多张量模型使用 `ncs2_multi_client.NCS2MultiWorker`，输入是按端口顺序排列的序列数组。

客户端会校验张量数量、shape、有限值、响应身份、耗时和完成计数。超时、坏响应或 fatal worker 错误会终止并回收子进程；本地输入错误抛出 `ValueError`；可恢复的 worker 错误抛出 `NCS2RemoteError`。

## Swift

`Package.swift` 暴露无第三方依赖的 `NCS2Kit`，支持 macOS 13+ arm64，包含 schema 1/2 客户端、张量描述符、推理结果和错误类型。

```swift
import NCS2Kit

let client = try NCS2WorkerClient(worker: workerURL, plugins: pluginsURL, model: modelURL)
defer { try? client.close() }
let result = try client.infer([-2, 0.5, -1, 3])
print(result.output)
_ = try client.health()
_ = try client.shutdown()
```

每个客户端应由同一个调用线程使用。worker 是由客户端拥有的子进程，不应再创建子孙进程。`./ncs2 build-swift` 会编译示例、构建 SwiftPM 包并运行测试，不访问 USB。

## 截止时间与清理

Python 客户端默认启动截止时间为 30 秒、请求截止时间为 15 秒。Swift 客户端的 `timeout`、`startupTimeout` 和 `exitTimeout` 必须位于 `(0,300]`。请求截止时间覆盖管道写入和完整响应。

正常关闭会发送协议请求、关闭 stdin 并等待进程退出。清理超时时会升级到 SIGTERM，再升级到 SIGKILL，并始终回收子进程。

项目不承诺热拔插恢复。worker 失败后应显式重启；客户端不会自动重试启动。
