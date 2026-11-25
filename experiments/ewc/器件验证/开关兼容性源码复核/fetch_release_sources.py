"""Acquire immutable public release sources; no toolchain installation."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
COMMIT = "a80f6e3e95d51534905b1f23410a951802666656"


def main():
    assert not (ROOT / "sources.json").exists()
    original, fixed = ROOT / "original", ROOT / "fixed"
    original.mkdir()
    fixed.mkdir()
    records = []
    for remote, name in (
        ("src/xspice/icm/xtradev/pswitch/cfunc.mod", "cfunc.mod"),
        ("src/xspice/icm/xtradev/pswitch/ifspec.ifs", "ifspec.ifs"),
        ("COPYING", "COPYING"),
    ):
        endpoint = f"repos/imr/ngspice/contents/{remote}?ref={COMMIT}"
        result = subprocess.run(["gh", "api", endpoint], capture_output=True, check=True)
        response = json.loads(result.stdout)
        content = base64.b64decode(response["content"])
        path = original / name
        path.write_bytes(content)
        records.append(dict(path=remote, local=f"original/{name}", commit=COMMIT,
                            git_blob_sha=response["sha"], sha256=hashlib.sha256(content).hexdigest(),
                            source_url=response["html_url"]))
    for name in ("cfunc.mod", "ifspec.ifs", "COPYING"):
        shutil.copyfile(original / name, fixed / name)
    result = dict(repository="imr/ngspice", release="ngspice-47", commit=COMMIT, files=records,
                  toolchain_installed=False, vendor_models_changed=False)
    (ROOT / "sources.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Three public release sources acquired, original and candidate separated")


if __name__ == "__main__":
    main()
