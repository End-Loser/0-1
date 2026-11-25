"""Classify bounded GitHub hits; schematic symbols are not SPICE models."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
from collect_public_models import ROOT, sha


def main():
    assert not (ROOT / "open_hits.json").exists()
    cache = Path(tempfile.mkdtemp(prefix="ewc-open-hits-"))
    inputs = [
        ("loxodes/vna", "35c49decfc713c3bc4ff1b8cac6d3eacd19f8c9c", "lib/adl5380.lib"),
        ("natsfr/kicad-components", "da5d4b7a4f595b832451928a5e708fe55cf40cc7",
         "Transceiver/Mixer/AD/adl5375.lib"),
    ]
    rows = []
    for repository, commit, path in inputs:
        proc = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw+json",
                               f"repos/{repository}/contents/{path}?ref={commit}"],
                              capture_output=True, timeout=40)
        target = cache / Path(path).name
        target.write_bytes(proc.stdout if proc.returncode == 0 else proc.stderr)
        text = proc.stdout.decode("utf-8", errors="replace")
        symbol = text.startswith("EESchema-LIBRARY")
        subcircuits = re.findall(r"(?im)^\s*\.subckt\s+([^\r\n]+)", text)
        rows.append(dict(repository=repository, commit=commit, path=path,
                         returncode=proc.returncode, cache=str(target), sha256=sha(target),
                         kicad_symbol_library=symbol, spice_subcircuits=subcircuits,
                         dynamic_model_accepted=False,
                         classification="schematic symbol, not dynamic model" if symbol else "unverified"))
    repo, commit = "analogdevicesinc/system-level", "beade5f4e35ee2b32fab5a3ef52c2835100b52d2"
    proc = subprocess.run(["gh", "api", f"repos/{repo}/contents?ref={commit}", "--jq",
                           ".[].name"], capture_output=True, timeout=40)
    target = cache / "official_repository_root.txt"
    target.write_bytes(proc.stdout if proc.returncode == 0 else proc.stderr)
    result = dict(scope="sampled_open_repository_availability_not_global_absence",
                  hits=rows, repository_listing_returncode=proc.returncode,
                  repository_listing_cache=str(target), repository_listing_sha256=sha(target),
                  github_requests=3, dynamic_RF_models_accepted=0,
                  authorization_needed_for_unprovided_models=True,
                  collector_sha256=sha(Path(__file__)))
    (ROOT / "open_hits.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    for row in rows:
        print(row["path"], row["classification"], "subcircuits:", row["spice_subcircuits"])
    print(proc.stdout.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    main()
