# Local runtime and source candidates

## Local copied runtime

    ./ncs2 package

Copies five executables and OpenVINO, IR frontend, MYRIAD and libusb into an ignored dist directory. Only copies have loader paths rewritten to relative rpaths and receive ad-hoc signatures. Architecture/signatures are verified, firmware hashes checked, source license/notice files retained, and a manifest written. Intel firmware is included for local use.

The ncs2-local launcher supports list/smoke/bench/worker. The original source/build tree is untouched. A copy moved to another directory containing spaces ran real NCS2 inference from a different working directory. Loader traces proved all OpenVINO/MYRIAD/libusb libraries came from the bundle; an IR roundtrip also loaded the copied frontend. Evidence: results/relocation-20261111-1734/report.json and notebook. The earlier shell launcher passed inference but filtered loader diagnostics; direct execution supplied them.

This is local-only, not cleared for redistribution, Developer-ID signed or notarized. Embedded debug strings may contain build paths. Source notices are provenance, not a complete binary-license audit. Only this Mac/OS combination was tested; another macOS version needs independent verification.

## Source candidate

    ./ncs2 source-export

Selects root source, scripts, tests, Swift and Markdown docs; excludes vendor/build/firmware/results/raw notebooks; refuses symlinks and known local home/project paths. SOURCE_MANIFEST.json records SHA-256. No remote repo is created and nothing uploaded.

The candidate is release_ready=false: the original-code license remains unselected and fresh-machine reproduction is pending. A reviewed source preview is public at https://github.com/Nishikori-Yui/ncs2-macos; this does not clear a binary release or grant an open-source license. The manifest describes the local candidate before publication. Never force-add ignored local files.
