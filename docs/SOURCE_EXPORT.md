# NeuralStick Bridge source-only candidate export

`./ncs2 source-export` creates a new directory under `dist/` containing the public source candidate, a manifest, and an inventory digest. It never publishes, initializes Git, selects a license, or accesses hardware. Use `--output PATH` for a new destination; an existing directory or file is never overwritten.

For work that should not go into the main notebook, select a separate log:

```sh
NCS2_LOG_FILE=results/export-development.md ./ncs2 source-export
NCS2_LOG_FILE=results/export-development.md ./ncs2 source-export --check dist/CANDIDATE
```

To verify a candidate without writing a log, run the verifier directly:

```sh
python3 -B scripts/export_source.py --check dist/CANDIDATE
```

## Selection

The exporter uses an explicit allowlist. `REQUIRED_FILES` covers the launcher, lockfile, Python clients, C++ workers and probe, build/bootstrap/audit/export scripts, core tests, Swift package files, and public documentation. A missing required file or source directory fails the export.

Additional selection is shallow:

- Explicit root files, plus optional root `LICENSE` and root `ncs2*.cpp` / `ncs2*.hpp`.
- Immediate `docs/*.md`, `scripts/*.py`, `scripts/*.sh`, `tests/test_*.py`, `examples/*.swift`, and `swift-tests/*.swift` files.
- The root `README.zh-CN.md` and immediate `docs/*.zh-CN.md` Simplified Chinese documents.
- Source suffixes are limited to `.md`, `.json`, `.py`, `.sh`, `.cpp`, `.hpp`, and `.swift`.

Vendor trees, build outputs, firmware caches, runtime binaries, result directories, raw notebooks and logs, caches, nested source directories, and `.github` metadata are excluded. Maintain the inventory when adding or removing a core dependency; selection is not automatic dependency discovery.

## Validation

Every file is read into a validated byte snapshot before output directories are created. Inputs must be regular files with one hard link, valid UTF-8, and no binary signatures or disallowed control bytes. Known project and home absolute paths are rejected. Limits are 16 MiB per file and 128 MiB total.

Symlinks in selected files, source directories, output paths, or output parents are rejected. Use a real physical path rather than a symlink alias, including macOS `/tmp` where applicable. All path opens use no-follow descriptor traversal, and parent traversal components are rejected.

Executable intent is preserved as mode 755; other files use mode 644. Output cannot be inside a selected source directory. A write failure may leave an incomplete directory; it is never silently reused or deleted. The manifest is written last, and a successful export verifies the completed candidate.

## Manifest and check mode

`SOURCE_MANIFEST.json` uses schema version 2. Each file records its byte count, SHA-256 digest, and executable flag. The summary records file count, total bytes, and a digest of the sorted file-record dictionary.

`--check DIRECTORY` rejects missing or additional files, symlinks, nonregular files, duplicate manifest keys, unsafe paths, unsupported schemas, content changes, and mode changes. Export and check failures return a nonzero status.

Hashes detect changes against the supplied manifest. They do not authenticate an unsigned manifest; someone who changes both the files and matching metadata can defeat that comparison. Preserve the printed inventory digest separately when an independent reference is needed.

## Publication boundary

`published` and `release_ready` are always false. `own_license_selected` reports whether a root `LICENSE` exists; it does not validate legal scope or grant third-party redistribution rights.

Review the exact candidate, credentials, third-party provenance, license scope, and hardware reproduction before publishing. The byte and path checks are bounded safeguards, not a complete secret scanner or proof of authorship.
