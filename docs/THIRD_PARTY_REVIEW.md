# Third-party review snapshot

This document records a source-level provenance review for the macOS NCS2
toolkit. It does not select a license for this repository, grant permission to
redistribute Intel firmware, or clear a packaged runtime for distribution.

The review is tied to `toolchain.lock.json` as observed on 2026-11-12:

- OpenVINO repository: `openvinotoolkit/openvino`
- OpenVINO revision: `6fbc44f307068367bef029f760e26058147b6d1b`
- Observed version: `2022.3.2`
- Root license file: `vendor/openvino-2022.3/LICENSE`
- OpenVINO runtime notices: `vendor/openvino-2022.3/licensing/runtime-third-party-programs.txt`

The OpenVINO checkout contains a separate runtime third-party programs file.
It includes notices and license text for components such as oneDNN, Intel
Threading Building Blocks, OpenCL material, Caffe and gflags. A future binary
distribution must preserve the applicable upstream license and attribution
material instead of treating the root OpenVINO license as the complete notice
set. The upstream project identifies OpenVINO as Apache-2.0 and publishes the
runtime third-party notices in its licensing directory.

The pinned submodules and local license evidence are:

| Locked path | Revision | Local evidence |
| --- | --- | --- |
| `thirdparty/ade` | `58b2595a1a95cc807be8bf6222f266a9a1f393a9` | `LICENSE` |
| `thirdparty/protobuf/protobuf` | `fe271ab76f2ad2b2b28c10443865d2af21e27e0e` | `LICENSE`, `third_party/utf8_range/LICENSE` |
| `thirdparty/pugixml` | `a0e064336317c9347a91224112af9933598714e9` | `LICENSE.md` |
| `thirdparty/xbyak` | `f8ea5c28dfcdc98585881d0ca9e499580ca077ae` | `COPYRIGHT`, `readme.md` |

The pinned Xbyak checkout labels its license as BSD-3-Clause. The other
submodule license files remain the authoritative notices for their own source
trees; this project does not collapse them into a single license statement.

The lockfile obtains two official OpenVINO Myriad firmware archives:

- `https://storage.openvinotoolkit.org/dependencies/myriad/firmware_usb-ma2x8x_20230418_40.zip`
- `https://storage.openvinotoolkit.org/dependencies/myriad/firmware_pcie-ma2x8x_20230418_40.zip`

The bootstrap process verifies the archive and extracted-member SHA-256
values. The firmware cache is ignored and no firmware is committed to the
public repository. The checksum proves which bytes were tested; it does not
prove a right to redistribute those bytes. Firmware redistribution therefore
remains unresolved and outside the source-preview publication.

The project itself still has no selected `LICENSE` file. The public repository
is a source preview with scripts and documentation; it does not claim to be a
licensed open-source release until the owner selects and commits a license.

Upstream references used for this snapshot:

- OpenVINO repository and license: <https://github.com/openvinotoolkit/openvino>
- OpenVINO runtime license metadata: <https://github.com/openvinotoolkit/openvino/tree/master/licensing>
- Xbyak repository and license metadata: <https://github.com/herumi/xbyak>

The exact pinned files and hashes remain in the local checkout and are
revalidated by the reproduction and source-export workflows. This review is a
provenance record, not legal advice.
