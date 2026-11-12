# Source-only candidate export

`./ncs2 source-export` remains the supported launcher. It creates a new directory under `dist/` and prints the file count, source-byte total, and SHA-256 inventory digest. It does not publish, initialize Git, choose a license, or access hardware. Use `--output PATH` for a new destination; an existing directory or file is never overwritten.

For work that must stay out of the main notebook, select a separate log:

```sh
NCS2_LOG_FILE=results/export-hardening-development.md ./ncs2 source-export
NCS2_LOG_FILE=results/export-hardening-development.md ./ncs2 source-export --check dist/CANDIDATE_NAME
```

The launcher logs commands even for `--check`. To validate without writing any log, run `python3 -B scripts/export_source.py --check dist/CANDIDATE_NAME` directly. Check mode reads only the candidate; it neither creates output nor requires the original project files.

## Selection and validation

The exporter defines `REQUIRED_FILES` explicitly: root documentation, lock file, launchers, Python clients, C++ sources and headers, current command scripts and their helper modules, the Swift example, this guide, and focused tests. Missing required files or source directories fail the export. Maintain this inventory when adding or removing command dependencies; it is not automatic dependency discovery.

Additional selection is shallow and uses both location and suffix:

- Explicit root files, plus optional root `LICENSE` and root `ncs2*.cpp` / `ncs2*.hpp`.
- Immediate `docs/*.md`, `scripts/*.py`, `scripts/*.sh`, `tests/test_*.py`, and `examples/*.swift` files.
- The exact nested path `.github/workflows/native-arm64.yml`. Other workflow/YAML files remain excluded; each intermediate directory has no-follow traversal and exact-tree verification.
- General source suffixes are restricted to `.md`, `.json`, `.py`, `.sh`, `.cpp`, `.hpp`, `.swift`; the exact named workflow path additionally permits `.yml`. Only named root exceptions may lack those suffixes.

Vendor trees, third-party trees, build outputs, firmware caches, runtime binaries, result directories, raw notebooks/logs, caches, and nested source directories are outside selection. Do not place third-party material or raw logs in allowlisted files: names and text checks cannot establish authorship or provenance.

Every selected file is read into a validated byte snapshot before creating any output directories. Inputs must be regular files with one hard link, valid UTF-8, and no disallowed control bytes or recognized binary signatures. Known project/home absolute paths are rejected. Limits are 16 MiB per file and 128 MiB of source bytes per candidate. The launchers must be executable. Symlinks in selected files, source directories, input ancestors, output paths, or output parents are rejected; use a real physical path rather than a symlink alias (including macOS `/tmp`, where applicable). All path opens use no-follow descriptor traversal. Parent traversal components are rejected.

The validated bytes are written exclusively into a new destination. Executable intent is preserved as mode 755; other files use mode 644. Output cannot be inside a selected source directory. A write failure may leave an incomplete directory; it is never silently reused or automatically deleted. Inspect it and choose a new destination. The manifest is written last, and a successful export verifies the completed candidate.

## Manifest and check mode

`SOURCE_MANIFEST.json` uses schema version 2. Each selected relative path records its byte count, SHA-256 digest, and executable flag. The summary records source file count, total source bytes, and SHA-256 of the file-record dictionary serialized with sorted keys and compact JSON separators. Totals exclude the manifest itself. Identical source bytes and executable flags produce the same inventory digest.

`--check DIRECTORY` rejects missing or additional files/directories, symlinks, nonregular files, invalid or duplicate manifest keys, unsafe paths, unsupported schemas, metadata/summary inconsistencies, content changes, and executable-flag changes. It rechecks the suffix/location and text rules. Schema-1 candidates must be re-exported; the verifier does not silently upgrade them. Export and check CLI failures return a nonzero status.

Hashes detect differences against the supplied manifest. They do not authenticate an unsigned manifest: someone who changes both the files and matching metadata can defeat that comparison. Preserve the printed inventory digest separately if you need an independent reference. Check mode does not rerun the original project's absolute-path check because it does not know the original paths.

## Publication remains a separate decision

`published` and `release_ready` are always false, including when a root `LICENSE` exists. Without that file, `own_license_selected` is false; when present, that field reports file presence only, not legal validity or permission. This exporter never creates or selects a license.

The byte/signature and known-path checks are bounded safeguards, not a complete secret scanner or proof that all text is original source. Review the exact candidate, credentials and private data, third-party provenance, license scope, and second-machine reproduction before a separate publication decision.

## Isolated regression tests

```sh
PYTHONDONTWRITEBYTECODE=1 NCS2_LOG_FILE=results/export-hardening-development.md \
  ./ncs2_log_run.sh 'Source export regression tests' \
  python3 -B -m unittest discover -s tests -p test_export_source.py -v
```

Tests create synthetic projects in temporary directories. Missing-file, symlink, binary, overwrite, and tamper cases mutate only those fixtures; no test deletes or modifies real project sources or invokes hardware.
