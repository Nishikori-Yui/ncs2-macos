# Second physical Mac verification

This checklist is for a second physical Apple Silicon Mac. It is not
satisfied by a VM, a GitHub-hosted macOS runner, Rosetta, or another run on
the development Mac.

## Host requirements

Use a Mac with:

- Apple Silicon and native `arm64` execution
- Xcode Command Line Tools and Swift 6
- Python 3.11 or newer, CMake, `pkg-config`, Git and arm64 `libusb`
- network access for the first pinned-input fetch
- normal macOS permissions for the optional USB section

Do not install Linux, a VM, Rosetta, a second OpenVINO runtime, or a camera
integration for this check.

## Clean source and dependency reproduction

Clone the public repository and record the checked-out commit:

```sh
git clone https://github.com/Nishikori-Yui/ncs2-macos.git
cd ncs2-macos
git rev-parse HEAD
./ncs2 reproduce --summary-only --timeout 3600
```

The command creates a private result directory. It fetches the pinned
OpenVINO revision, submodules and official firmware, verifies them offline,
builds the C++ runtime and Swift clients, audits the arm64 dependency closure,
exports and reloads 20 IR fixtures, runs the non-hardware tests, and checks a
new source candidate. It does not enumerate USB devices.

The reproduction passes only when the sanitized JSON reports:

- `complete: true`
- `passed: true`
- `hardware_executed: false`
- every stage has `exit_code: 0` and `timed_out: false`
- the architecture stage reports only arm64 non-system Mach-O files
- the regression stage completes without a test failure

Keep the raw result directory on that Mac. Return only the sanitized summary,
the commit SHA, macOS version, architecture, stage outcomes and source
inventory hash. Do not send firmware, binaries, USB serial numbers, raw logs
or absolute local paths.

## Optional hardware smoke

If the Mac also has an NCS2 connected, run these separately after the
non-hardware reproduction:

```sh
./ncs2 list
./ncs2 smoke
```

The device must be reported as `MYRIAD`. Hardware output should be summarized
with the device family, firmware/runtime compatibility line, case name,
iteration count, checked elements and maximum error. Keep bus address, serial
number and raw USB logs private.

The optional soak is bounded and must be recorded as its own experiment:

```sh
./ncs2 soak --duration 120 --restarts 3 --overall-timeout 600
```

This is a second-machine observation only when the command runs on the
second physical Mac. It does not establish arbitrary model compatibility,
physical unplug recovery, or day-long stability.

## Evidence handoff

The operator should provide:

1. the sanitized reproduction JSON;
2. the checked-out commit SHA;
3. the host architecture and macOS version;
4. the source inventory hash;
5. optional sanitized hardware summaries;
6. the local result-directory name, without uploading its contents.

The development Mac owner will compare the commit, stage list, source hash and
architecture result with the public source candidate. A successful checklist
does not grant permission to redistribute firmware or a packaged runtime.
