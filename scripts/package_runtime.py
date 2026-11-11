#!/usr/bin/env python3
"""Create an ad-hoc-signed, relocatable LOCAL runtime. Not cleared for redistribution."""
import argparse
import datetime as dt
import json
from pathlib import Path
import platform
import shutil
import subprocess
import uuid
from run_suite import hash_file

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"vendor/openvino-2022.3"
RUNTIME=SOURCE/"bin/arm64/Release"

def run(*args):
    return subprocess.check_output([str(x) for x in args],text=True,stderr=subprocess.STDOUT).strip()

def deps(path):
    return [line.strip().split(" (compatibility version",1)[0] for line in run("otool","-L",path).splitlines()[1:]]

def rpaths(path):
    lines=run("otool","-l",path).splitlines()
    return [lines[i+2].strip().split("path ",1)[1].rsplit(" (offset",1)[0] for i,line in enumerate(lines) if line.strip()=="cmd LC_RPATH"]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,help="New local directory; never overwritten")
    a=p.parse_args()
    if platform.system()!="Darwin" or platform.machine()!="arm64":p.error("Native arm64 macOS required")
    out=(a.output or ROOT/"dist"/("local-runtime-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:8])).resolve()
    out.mkdir(parents=True,exist_ok=False)
    for part in ("bin","lib","licenses"):(out/part).mkdir()
    usb_deps=[x for x in deps(RUNTIME/"libopenvino_intel_myriad_plugin.so") if "libusb" in x]
    if len(usb_deps)!=1 or not usb_deps[0].startswith("/"):raise RuntimeError("Expected one absolute libusb source dependency")
    usb=Path(usb_deps[0]).resolve()
    sources={
        "lib/libopenvino.2232.dylib":(RUNTIME/"libopenvino.dylib").resolve(),
        "lib/libopenvino_ir_frontend.2232.dylib":(RUNTIME/"libopenvino_ir_frontend.dylib").resolve(),
        "lib/libopenvino_intel_myriad_plugin.so":RUNTIME/"libopenvino_intel_myriad_plugin.so",
        "lib/libusb-1.0.0.dylib":usb,
    }
    for name in ("ncs2_openvino_probe","ncs2_lab","ncs2_perf","ncs2_worker","ncs2_multi_worker","ncs2_apps"):
        sources["bin/"+name]=ROOT/"build-arm64"/name
    originals={}
    for relative,source in sources.items():
        if run("lipo","-archs",source)!="arm64":raise RuntimeError("Not a pure arm64 source: "+str(source))
        originals[relative]=hash_file(source)
        shutil.copy2(source,out/relative)
    library_names={Path(relative).name for relative in sources if relative.startswith("lib/")}
    for relative in sources:
        path=out/relative;changes=[]
        for dep in deps(path):
            if dep.startswith(("/usr/lib/","/System/Library/")):continue
            name=Path(dep).name
            if name not in library_names:raise RuntimeError("Unresolved package dependency: "+dep)
            replacement="@rpath/"+name
            if dep!=replacement:changes.extend(["-change",dep,replacement])
        for old in rpaths(path):changes.extend(["-delete_rpath",old])
        changes.extend(["-add_rpath","@loader_path" if relative.startswith("lib/") else "@executable_path/../lib"])
        if relative.endswith(".dylib"):changes.extend(["-id","@rpath/"+path.name])
        run("install_name_tool",*changes,path)
        run("codesign","--force","--sign","-",path);run("codesign","--verify","--strict",path)
        if run("lipo","-archs",path)!="arm64":raise RuntimeError("Architecture changed")
        for dep in deps(path):
            if not dep.startswith(("/usr/lib/","/System/Library/","@rpath/")):raise RuntimeError("Absolute non-system dependency remains")
        print("PACKAGED arm64 "+relative)
    for name in ("openvino","openvino_ir_frontend"):
        (out/"lib"/("lib"+name+".dylib")).symlink_to("lib"+name+".2232.dylib")
    (out/"lib/plugins.xml").write_text('<ie><plugins><plugin name="MYRIAD" location="libopenvino_intel_myriad_plugin.so"/></plugins></ie>\n')
    lock=json.loads((ROOT/"toolchain.lock.json").read_text())
    for firmware in lock["firmware"]:
        source=RUNTIME/firmware["member"]
        if hash_file(source)!=firmware["output_sha256"]:raise RuntimeError("Firmware integrity mismatch")
        shutil.copy2(source,out/"lib"/source.name)
    shutil.copy2(ROOT/"toolchain.lock.json",out/"toolchain.lock.json")
    shutil.copy2(ROOT/"THIRD_PARTY_NOTICES.md",out/"THIRD_PARTY_NOTICES.md")
    for source in SOURCE.rglob("*"):
        if source.is_file() and source.name.upper().split(".")[0] in ("LICENSE","COPYING","NOTICE"):
            target=out/"licenses"/"openvino"/source.relative_to(SOURCE)
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    usb_license=usb.parent.parent/"COPYING"
    if not usb_license.is_file():raise RuntimeError("libusb license missing")
    shutil.copy2(usb_license,out/"licenses/libusb-COPYING")
    launcher=out/"ncs2-local"
    launcher.write_text('''#!/bin/zsh
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
unset DYLD_LIBRARY_PATH DYLD_FALLBACK_LIBRARY_PATH
action="help"
if (( $# )); then action="$1"; shift; fi
plugins="$ROOT_DIR/lib/plugins.xml"
case "$action" in
  list|smoke|bench) exec "$ROOT_DIR/bin/ncs2_openvino_probe" "$plugins" "$action" "$@" ;;
  worker) exec "$ROOT_DIR/bin/ncs2_worker" "$plugins" "$@" ;;
  multi-worker)
    if (( $# == 1 )) && [[ "$1" == "--validate-only" ]]; then
      exec "$ROOT_DIR/bin/ncs2_multi_worker" --validate-only
    fi
    exec "$ROOT_DIR/bin/ncs2_multi_worker" "$plugins" "$@"
    ;;
  *) print -- "Local-only arm64 runtime: list | smoke | bench | worker --case CASE | multi-worker --model XML"; exit 64 ;;
esac
''')
    launcher.chmod(0o755)
    (out/"LOCAL_ONLY.md").write_text(
        "# Local runtime artifact\n\n"
        "This copy includes Intel firmware and libusb. Redistribution permission has NOT been established. "
        "Do not upload it as a release. Signatures are ad-hoc, not Developer ID or notarization.\n\n"
        "Non-system dependencies load relative to this directory. Invoke ncs2-local; Python clients may "
        "use bin/ncs2_worker or bin/ncs2_multi_worker and lib/plugins.xml directly. Host OS/SDK compatibility "
        "is only tested on this Mac.\n")
    manifest={"schema_version":1,"distribution":"local-only-not-cleared","architecture":"arm64",
              "macos":platform.mac_ver()[0],"signature":"ad-hoc","toolchain_sha256":hash_file(ROOT/"toolchain.lock.json"),
              "source_artifact_sha256":originals,"files":{}}
    for file in sorted(out.rglob("*")):
        if file.is_symlink():manifest["files"][str(file.relative_to(out))]={"symlink":str(file.readlink())}
        elif file.is_file():manifest["files"][str(file.relative_to(out))]={"sha256":hash_file(file),"bytes":file.stat().st_size}
    (out/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print("LOCAL_RUNTIME="+str(out))

if __name__=="__main__":main()
