"""Follow only identified official links; pin open repository evidence to a commit."""
import json
from pathlib import Path
import subprocess
import tempfile
from collect_public_models import ROOT, collect, sha


def main():
    assert not (ROOT / "followup.json").exists()
    cache = Path(tempfile.mkdtemp(prefix="ewc-public-followup-"))
    rows = [collect(item, cache) for item in [
        ("ref_family_datasheet", "https://www.ti.com/lit/gpn/ref50"),
        ("ldo_pspice", "https://www.ti.com/lit/zip/sbvm961"),
        ("ldo_datasheet", "https://www.ti.com/lit/gpn/tps7a20"),
    ]]
    commit = "beade5f4e35ee2b32fab5a3ef52c2835100b52d2"
    repository = "analogdevicesinc/system-level"
    github = []
    for path in ["docs/solutions/reference-designs/fmcomms2/common/math.rst",
                 "LICENSE"]:
        args = ["gh", "api", "-H", "Accept: application/vnd.github.raw+json",
                f"repos/{repository}/contents/{path}?ref={commit}"]
        proc = subprocess.run(args, capture_output=True, timeout=40)
        target = cache / ("github_" + Path(path).name)
        target.write_bytes(proc.stdout if proc.returncode == 0 else proc.stderr)
        github.append(dict(repository=repository, commit=commit, path=path,
                           returncode=proc.returncode, cache=str(target), sha256=sha(target),
                           role="open documentation, not executable RF device model"))
    searches = []
    for term in ("ADL5380 extension:lib", "ADL5375 extension:lib"):
        proc = subprocess.run(["gh", "search", "code", term, "--limit", "5",
                               "--json", "repository,path,url"], capture_output=True, timeout=40)
        target = cache / ("search_" + term[:7] + ".json")
        target.write_bytes(proc.stdout if proc.returncode == 0 else proc.stderr)
        searches.append(dict(query=term, returncode=proc.returncode,
                             cache=str(target), sha256=sha(target)))
    result = dict(sources=rows, github=github, github_searches=searches,
                  official_download_attempts=3, github_requests=4,
                  cache=str(cache), full_chain_accepted=False,
                  collector_sha256=sha(Path(__file__)))
    (ROOT / "followup.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    for row in rows:
        print(row["id"], row.get("status"), row.get("archive_members", []))
    for row in github + searches:
        print(row.get("path", row.get("query")), row["returncode"], row["cache"])


if __name__ == "__main__":
    main()
