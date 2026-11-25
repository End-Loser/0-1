"""Verify sealed d01-d08 evidence while versioning only the planning context.

v2 keeps every sealed-artifact and previous-evidence pin strict, and releases
the single hard precondition that the deleted %TEMP% capture ths4551_page.html
must still exist. A missing capture is accepted only when 原始资料/manifest.json
records its digest as unrecoverable, and it is reported in unverified_anchors.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ORIGINAL = ROOT.parent / "\u771f\u5b9e\u57fa\u5e26\u8c03\u7406"
WORKSPACE = ROOT.parent.parent
ANCHOR_MANIFEST = WORKSPACE / "\u539f\u59cb\u8d44\u6599" / "manifest.json"
PLAN = "EWC\u771f\u5b9e\u6750\u6599\u4e0e\u5668\u4ef6\u7edf\u4e00\u89c4\u5212.md"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registered_digests():
    """Digests the anchor manifest already records as unrecoverable."""
    if not ANCHOR_MANIFEST.is_file():
        return set()
    data = json.loads(ANCHOR_MANIFEST.read_text(encoding="utf-8"))
    return {row["sha256"] for row in data.get("unrecoverable", []) if row.get("sha256")}


def main():
    summary = json.loads((ORIGINAL / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((ORIGINAL / "artifact_manifest.json").read_text(encoding="utf-8"))
    for path, digest in manifest["files_sha256"].items():
        assert sha(ORIGINAL / path) == digest, path
    context = []
    strict = 0
    for path, digest in summary["previous_evidence_sha256"].items():
        file = WORKSPACE / path
        if path == PLAN:
            assert file.is_file()
            context.append(dict(path=path, captured_sha256=digest, current_sha256=sha(file),
                                role="versioned planning context, not measured evidence"))
        else:
            assert sha(file) == digest, path
            strict += 1
    for path, digest in summary["original_files_sha256"].items():
        assert sha(Path(path)) == digest, path
    sources = json.loads((ORIGINAL / "sources.json").read_text(encoding="utf-8"))
    registered = registered_digests()
    unverified = []
    verified = 0
    for source in sources["sources"]:
        cache = Path(source["cache"])
        if cache.is_file():
            assert sha(cache) == source["sha256"], source["id"]
            verified += 1
        else:
            assert source["sha256"] in registered, (
                source["id"] + " is missing and its digest is not registered as unrecoverable")
            unverified.append({"anchor": "source:" + source["id"], "recorded": str(cache),
                               "sha256": source["sha256"]})
    spec = importlib.util.spec_from_file_location("original_csv_oracle", ORIGINAL / "verify_offline.py")
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    runs = [oracle.recalculate(run) for run in summary["runs"] if "rows" in run]
    result = dict(sealed_evidence_verification_pass=True,
                  sealed_artifacts_checked=len(manifest["files_sha256"]),
                  previous_evidence_strictly_checked=strict, context_documents=context,
                  official_sources_reverified=verified,
                  environment_missing=bool(unverified), unverified_anchors=unverified,
                  reproduced_runs=len(runs), runs=runs, whole_chain_passed=False,
                  anchor_manifest_sha256=(sha(ANCHOR_MANIFEST)
                                          if ANCHOR_MANIFEST.is_file() else None),
                  verifier_sha256=sha(Path(__file__)))
    (ROOT / "original_batch_verification.json").write_text(
        json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print("Sealed files:", len(manifest["files_sha256"]), "reproduced runs:", len(runs))


if __name__ == "__main__":
    main()
