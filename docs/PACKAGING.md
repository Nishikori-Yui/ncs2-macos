# Local runtime and source candidates

## Local copied runtime

    ./ncs2 package

Copies six executables, including the schema-2 `ncs2_multi_worker`, and OpenVINO, IR frontend, MYRIAD and libusb into an ignored dist directory. Only copies have loader paths rewritten to relative rpaths and receive ad-hoc signatures. Architecture/signatures are verified, firmware hashes checked, source license/notice files retained, and a manifest written. Intel firmware is included for local use.

The `ncs2-local` launcher supports list/smoke/bench/worker/multi-worker. `ncs2-local multi-worker --validate-only` checks the packaged worker protocol without USB; normal model execution uses the bundle's `lib/plugins.xml`. To run the schema-2 Python demo against the local bundle:

    python3 scripts/multi_worker_demo.py --model results/multi-add-sub/multi_add_sub.xml --worker dist/local-runtime-NAME/bin/ncs2_multi_worker --plugins dist/local-runtime-NAME/lib/plugins.xml

A copied schema-2 worker passed protocol validation and the 23-inference NCS2 test after packaging. The original source/build tree is untouched. A copy moved to another directory containing spaces ran real NCS2 inference from a different working directory. Loader traces proved all OpenVINO/MYRIAD/libusb libraries came from the bundle; an IR roundtrip also loaded the copied frontend. Evidence: results/relocation-20261111-1734/report.json and notebook. The earlier shell launcher passed inference but filtered loader diagnostics; direct execution supplied them.

This is local-only, not cleared for redistribution, Developer-ID signed or notarized. Embedded debug strings may contain build paths. Source notices are provenance, not a complete binary-license audit. Only this Mac/OS combination was tested; another macOS version needs independent verification.

## Source candidate

    ./ncs2 source-export

Selects root source, scripts, tests, Swift and Markdown docs; excludes vendor/build/firmware/results/raw notebooks; refuses symlinks and known local home/project paths. SOURCE_MANIFEST.json records SHA-256. No remote repo is created and nothing uploaded.

The candidate remains `release_ready=false`: second-machine reproduction is pending, and binary redistribution/signing review is incomplete. The reviewed source is public under the MIT License for original project code; this does not clear a binary release or third-party redistribution. The manifest describes a local candidate before publication. Never force-add ignored local files.
