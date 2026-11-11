# Third-party provenance and publication boundary

This toolkit has not yet selected a license for its own original source. The public source preview does not grant an open-source license; a license must be selected before an open-source release. No third-party binary or source tree is included in this repository.

- OpenVINO is fetched from `openvinotoolkit/openvino` at the exact commit in `toolchain.lock.json`. The checked-out root `LICENSE` identifies Apache-2.0; retain upstream notices and inspect component licenses before distributing derived binaries.
- The checked-out submodule revisions are pinned in the lockfile. Their licenses remain with their upstream sources; this toolkit does not relabel them.
- Firmware is downloaded from Intel/OpenVINO official storage and checked against the upstream archive hashes. Download integrity does not establish redistribution rights. Firmware redistribution is not assessed here; `firmware-cache/` is ignored.
- libusb is resolved from the local installation and is not bundled.
- `vendor/`, `build-arm64/`, firmware, binary artifacts, raw local notebooks, and results are excluded from public commits. `.gitignore` does not redact a file deliberately force-added; review the exact publication file list.

Primary compatibility references checked on 2026-11-11:

```text
https://www.intel.com/content/www/us/en/support/articles/000093181/boards-and-kits.html
https://docs.openvino.ai/2023.3/openvino_docs_OV_UG_supported_plugins_Supported_Devices.html
https://github.com/openvinotoolkit/openvino/tree/6fbc44f307068367bef029f760e26058147b6d1b
```

Intel's notice and OpenVINO's supported-device documentation direct continued MYRIAD/NCS2 use to the 2022.3.x line. This project demonstrates one local arm64 build; it is not Intel certification or a claim of current upstream macOS support.
