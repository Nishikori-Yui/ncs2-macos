# Contributing to NeuralStick Bridge

NeuralStick Bridge is the public project name. The `ncs2` command and
`NCS2Kit` package names are compatibility-facing technical names and should
not be renamed casually.

Development targets native arm64 macOS. Keep Linux, virtual machines, containers, Rosetta, x86_64 runtimes, and camera integrations outside this repository.

## Local checks

Run from a checkout whose path may contain spaces:

```sh
./ncs2 fetch --offline
./ncs2 build-probe
./ncs2 build-swift
./ncs2 check --json
./ncs2_log_run.sh 'Unit tests' python3 -B -m unittest discover -s tests -v
```

The unit tests use synthetic workers and saved USB descriptors. They do not access the device. Do not add a CPU fallback to make a MYRIAD test pass.

## Hardware checks

Use one NCS2 process at a time.

```sh
./ncs2 usb
./ncs2 list
./ncs2 smoke
./ncs2 bench 100 10
```

CLI commands already append to the local operation notebook; do not wrap them in another logger. Run notebook writers sequentially.

## Source export

`./ncs2 source-export` produces a reviewed source candidate with an integrity manifest. It excludes local firmware, runtime binaries, vendor source, build outputs, results, and notebooks. Check the exported tree before publishing:

```sh
./ncs2 source-export --output dist/candidate
python3 -B scripts/export_source.py --check dist/candidate
```

The exporter rejects known local absolute paths, symlinks, binary content, and unexpected files. It is a bounded safeguard, not a substitute for reviewing credentials, third-party provenance, and license scope.

## Third-party boundary

Keep upstream source, firmware archives, and copied runtime binaries out of public commits unless redistribution terms have been reviewed. The project's MIT license does not cover OpenVINO, libusb, or Intel firmware.
