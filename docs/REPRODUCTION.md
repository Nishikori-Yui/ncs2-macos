# Fresh native macOS reproduction

Use a physical Apple Silicon Mac with Xcode/Command Line Tools, Python 3.11+, CMake, pkg-config and arm64 libusb. Swift protocol tests require a Swift 6 compiler. Existing Homebrew installations can supply missing packages with `brew install cmake pkg-config libusb python`.

```sh
./ncs2 reproduce --timeout 3600
```

The command creates a new local results directory and an allowlisted source snapshot in `fresh source`, with no copied vendor checkout, firmware cache or build output. It checks the environment, downloads pinned OpenVINO/submodules and official firmware, verifies those inputs offline, builds the runtime and five tools, audits their architecture, exports/reloads all sixteen IR fixtures, runs the complete nonhardware tests, and verifies another source candidate. It does not request device discovery or hardware inference.

Every stage has a process-group deadline drawn from the overall budget. Completed stages record exit status, timeout status and local-log SHA-256. A failed stage stops subsequent work; existing output is never reused. Raw logs and commands remain local. System/Homebrew prerequisites are host-provided and their versions recorded, so this is not a hermetic OS build.

The report declares `fresh-source-and-dependencies-on-current-host`. Running it on the development Mac is a same-machine isolation test, not second-Mac verification. Each report's source inventory hash identifies the exact snapshot tested, which may predate later documentation changes.

## Optional physical Mac CI

`.github/workflows/native-arm64.yml` has only a manual `workflow_dispatch` trigger. It targets `[self-hosted, macOS, ARM64, ncs2-physical]`. The custom label is an operator declaration that the runner is physical; `uname` alone cannot detect all virtualization. Do not attach that label to a VM. GitHub-hosted macOS runners are not used.

Runner registration is owner-managed. This toolkit does not register the current Mac, install an agent, request credentials, or dispatch without an available runner. Maintain OS/tool prerequisites separately and run only revisions the runner owner trusts.

The workflow fetches the selected revision into a new runner temporary directory and runs the same reproduction command. Its job limit is 90 minutes; the command budget is 80 minutes. Public stdout contains a sanitized JSON summary: OS version/architecture, stage outcomes/durations, hashes and failure stage. Raw errors, paths, hostnames, commands, firmware and binaries stay on the runner. There are no artifact-upload or release steps. The owner manages local retention and cleanup.

Workflow presence does not prove execution on a second Mac. Current evidence and remaining gates are in IMPLEMENTATION.md.

GitHub's [runner selection documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job) describes hosted VM execution. Its [self-hosted label guide](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/use-in-a-workflow) documents the OS, architecture and custom label routing used here.
