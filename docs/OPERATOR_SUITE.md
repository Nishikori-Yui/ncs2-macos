# 算子与 IR 测试集

2026-11-11 本机验证。只使用 macOS arm64、MYRIAD；无 CPU fallback、Linux 或摄像头。

## 使用

运行 ./ncs2 suite --export-only 可生成并重新读取全部 IR，不访问 USB。
运行 ./ncs2 suite --iterations 20 --warmup 3 可执行全部案例。
用 --case conv_1x3x16x16（可重复）选择案例；--timeout 60 限定每个子进程时间。
--output 指定全新的结果目录；已存在路径不会覆盖。

./ncs2 suite --case relu_4 --iterations 1 --warmup 0 --test-mismatch 是负向测试：仅在主机校验前把首个返回值扰动 1000，不更改网络或固件；预期退出 1。

## 首轮 12 项硬件结果（历史）

每项 3 次预热 + 20 次计时；11 个成功案例所有输出都通过独立参考核对。
独立 FakeQuantize 在编译时被拒绝，套件因此退出 1，未以忽略失败或 CPU fallback 变绿。

| 案例 | 输入 shape | 状态 / 阶段 | 平均 ms | P95 ms | 最大绝对误差 |
| --- | --- | --- | ---: | ---: | ---: |
| relu_4 | [1, 4] | passed / validation | 1.6391 | 1.6992 | 0 |
| relu_256 | [1, 256] | passed / validation | 1.6603 | 1.7586 | 0 |
| relu_1024 | [1, 1, 32, 32] | passed / validation | 1.6647 | 1.7891 | 0 |
| relu_16384 | [1, 4, 64, 64] | passed / validation | 1.9422 | 2.2057 | 0 |
| affine_channels | [1, 4, 8, 8] | passed / validation | 1.7057 | 1.8419 | 0 |
| conv_1x1x8x8 | [1, 1, 8, 8] | passed / validation | 1.6362 | 1.8382 | 0 |
| conv_1x3x16x16 | [1, 3, 16, 16] | passed / validation | 1.6786 | 1.7899 | 0 |
| conv_1x8x32x32 | [1, 8, 32, 32] | passed / validation | 1.8225 | 1.9398 | 0 |
| reshape_relu | [1, 2, 3, 4] | passed / validation | 1.6550 | 1.8587 | 0 |
| transpose_relu | [1, 2, 3, 4] | passed / validation | 1.6815 | 1.7666 | 0 |
| fake_quantize_256 | [1, 1, 8, 8] | failed / compile | — | — | — |
| convert_u8_relu | [1, 1, 8, 8] | passed / validation | 1.6434 | 1.7500 | 0 |

## 扩展至 16 项：历史实机结果

2026-11-11 新增四个单输入、静态 shape 的 fixture。全部 16 项完成 IR v11 XML/BIN 导出及重新读取，无 USB 导出报告为 `results/suite-20261111-213348-c28a15aa/report.json`。独立 Python 标量参考核对 C++ 参考值，并断言 XML 中存在对应的 `AvgPool`、`MaxPool`、`Concat`、`Broadcast` 层。

本轮完整硬件套件仍按每项 3 次预热 + 20 次计时串行运行：**15 项实机通过，1 项编译拒绝**，套件退出 1。原有 11 个可运行案例均再次通过；standalone FakeQuantize 仍在 compile 阶段因 unsupported layer type 被拒绝，没有执行推理。15 个成功案例每次输出均通过校验，最大绝对误差均为 0。

| 新增案例 | 输入 → 输出 shape | 图含义 | 平均 ms | P95 ms | 核对元素数 |
| --- | --- | --- | ---: | ---: | ---: |
| avgpool_2x2 | [1,1,4,4] → [1,1,2,2] | 2×2 平均池化，stride 2，无 padding | 1.6596 | 1.7191 | 92 |
| maxpool_2x2 | [1,1,4,4] → [1,1,2,2] | 2×2 最大池化，stride 2，无 padding | 1.6269 | 1.7379 | 92 |
| concat_channels | [1,1,2,3] → [1,2,2,3] | 沿 channel 轴拼接 x 与 -x | 1.7979 | 1.8965 | 276 |
| broadcast_channels | [1,1,2,3] → [1,2,2,3] | 将单 channel 广播为两份 | 1.7009 | 1.8384 | 276 |

这四项共核对 736 个设备输出元素；完整套件的 15 项成功案例共核对 765,624 个元素。计时边界仍为同步 `infer()`；新增四项每项约 1.42～1.45 秒的启动/编译耗时单独记录。这里只验证表中具体图和 shape；编译器可能降低或改写图，不据此声称任意 pooling/concat/broadcast 属性、动态 shape 或多输入图都受支持。

硬件报告：`results/suite-20261111-213649-db869fd8/report.json`。本轮第一次在受限执行上下文中尝试 `avgpool_2x2` 时，device_enumeration 阶段无 MYRIAD；随后的 libusb 也返回空列表。正常 macOS USB 权限下只读复查立即发现并打开 `03e7:2485`，随后完成上述全套实机运行。受限失败报告 `results/suite-20261111-213402-6da75e29/report.json` 保留；它不是算子编译失败或设备断连证据。

本轮重新运行完整 144 项非硬件回归，23.252 秒全部通过；另外逐项核对两份完整报告的源文件/二进制哈希、32 份 XML/BIN 对、op 类型和检查元素总数。保留的报告 SHA-256：

- 16 项无 USB 导出：`1b68dd069a54ab9008a3e4db50844493220c1bd4a8b36c013259b7954f2363ce`
- 16 项实机运行：`dce45c005d1a681ee0c804d75e4495bf17e4ddd74769dfc619f88d70eff7b861`

## 扩展至 20 项：四项增量实机结果

全部 20 个静态 IR 导出并重新读取通过，见本机 `results/suite-20261111-220041-5779c35b/report.json`。四项新增案例单独串行运行，每项 3 次预热、20 次计时，均实际运行于 MYRIAD 并逐元素通过参考校验：

| 案例 | 图含义 | 平均 ms | P95 ms | 核对元素数 |
| --- | --- | ---: | ---: | ---: |
| avgpool_pad_2x2 | 2×2、stride 2、padding 1；零 padding 计入平均 | 1.6603 | 1.7347 | 92 |
| maxpool_3x3 | 3×3、stride 2、无 padding | 1.5993 | 1.7261 | 92 |
| concat_width | 沿宽度轴拼接 x 和 -x | 1.5996 | 1.7627 | 276 |
| broadcast_spatial | [1,1,1,3] 广播为 [1,1,2,3] | 1.6918 | 1.9278 | 138 |

这四项的最大绝对误差均为 0，报告为 `results/suite-20261111-220107-4635809b/report.json`。它是独立增量运行，并非完整 20 项硬件回归。初版平均池化图错误设置 `exclude_pad=true`，而参考值包含 padding；核对 OpenVINO API 后改为 `false`，重建、重新导出并运行上述实机实验。测试现在还检查 IR 的池化属性、拼接轴和广播目标常量。

## 验证方法与限制

- 先构造图、序列化为 IR v11 XML/BIN，再通过 IR 前端读取，最后明确 compile_model(..., MYRIAD)。每项保留 IR SHA-256、原始输出、退出码、失败阶段、超时标记和结果。
- 每次输入随 pass 变化，逐元素验证整个输出。主机参考为独立标量计算，不调用 OpenVINO evaluate 或 CPU 插件；Python 测试另用补丁点积、IR 中的真实常量以及显式量化码表核对 C++ 参考。
- 误差条件为 abs(actual-reference) <= atol + rtol*abs(reference)，另检查有限数值、shape 和 dtype。容差在案例定义和报告中固定，没有看到失败后放宽。
- 输入和卷积权重多为二进制可精确表示的值。这些案例误差 0 不代表一般 FP32 模型无误差；FakeQuantize 刻意避开零点的半级舍入歧义，未覆盖全部边界。
- U8→FP32→ReLU 成功仅表明此输入/转换路径工作，不能证明原生 INT8 卷积或高吞吐量化计算。独立 FakeQuantize 不支持仍如实保留。
- 计时只包围同步 infer()，不含输入生成、主机参考、复制和输出校验；启动/编译另外报告。20 个样本只是小规模延迟扫描，不是稳定吞吐或 CPU/GPU 对比。
- 故意输出扰动负向测试已在实机失败于 validation；测试不会只根据进程成功退出就判通过。套件还严格核对 case/schema、迭代次数、checked_elements、有限数值和 IR 文件存在。
- 每案例单独进程，超时终止并回收该子进程组。超时可能影响设备固件状态，因此结果标记 timeout，不能假定设备已恢复。

## 原始证据

首轮 12 项硬件：results/suite-20261111-155315-1a66e4c5/report.json

首轮导出验证：results/suite-20261111-154520-68e64727/report.json
负向验证：results/suite-20261111-155038-dd3813fc/report.json
当前整合的非硬件回归共 144 项通过，覆盖此套件、独立参考、IR 读写、USB descriptor parser、架构审计清单、CLI JSON 标准输出、客户端、源码导出和 worker 恢复监督器。该总数是后续整合回归，不是早期这个算子套件单独运行的测试计数。

完整结果目录和操作日志只保留在本机；本摘要不包含个人路径或序列号。
