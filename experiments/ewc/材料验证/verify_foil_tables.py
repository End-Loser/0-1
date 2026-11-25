"""Check visually transcribed source rows and dimensional scale, not G2 closure."""
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "铜箔原表复核"
EXPECTED = [
    ("rolled_1oz", "Rolled", 1, 35, .4, .3),
    ("rolled_half_oz", "Rolled", .5, 18, .4, .3),
    ("ed_1oz_5880_group", "Electrodeposited", 1, 35, 2.4, .4),
    ("ed_half_oz_5880_group", "Electrodeposited", .5, 18, 2., .4),
    ("ed_quarter_oz_5880_group", "Electrodeposited", .25, 9, 1.3, .4),
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_table(table):
    assert table["table_page"] == 4
    assert table["roughness_metric"] == "Sq"
    assert table["si_unit"] == "m" and table["printed_unit_ascii"] == "um"
    assert table["printed_to_si_factor"] == 1e-6
    assert table["statistics"] == (
        "Typical mean values from populations involving multiple lots; not limits")
    assert table["printed_revision"] == "Revised 1530 081826 Publication #92-243"
    assert len(table["rows"]) == len(EXPECTED)
    for row, expected in zip(table["rows"], EXPECTED):
        actual = tuple(row[x] for x in (
            "id", "foil_type", "weight_oz", "thickness_um",
            "dielectric_side_Sq_um", "top_side_Sq_um"))
        assert actual == expected, "Row/type/side differs from reviewed image"
        for name in ("thickness", "dielectric_side_Sq", "top_side_Sq"):
            value = row[name + "_m"]
            assert math.isfinite(value) and value > 0
            assert math.isclose(value, row[name + "_um"] * 1e-6,
                                rel_tol=1e-14, abs_tol=0), "SI conversion mismatch"
    for name in (
        "resistivity_adopted", "roughness_loss_model_identified",
        "c11000_parameters_changed", "bom_frozen", "G2_closed",
    ):
        assert table[name] is False, "Source extraction cannot grant acceptance"
    assert table["full_system_gates_closed"] == 0
    assert len(table["limitations"]) == 7


def audit():
    render_path = OUT / "render_manifest.json"
    table_path = OUT / "table_transcription.json"
    source_path = ROOT / "source_manifest.json"
    render = json.loads(render_path.read_bytes())
    table = json.loads(table_path.read_bytes())
    source = next(x for x in json.loads(source_path.read_bytes())["sources"]
                  if x["id"] == "S03")
    assert sha(source_path) == render["source_manifest_sha256"]
    assert sha(ROOT / "audit_foil_tables.py") == render["renderer_sha256"]
    assert source["pdf_sha256"] == render["pdf_sha256"] == table["source_pdf_sha256"]
    assert source["snapshot_sha256"] == render["snapshot_sha256"]
    assert sha(ROOT / source["snapshot_path"]) == source["snapshot_sha256"]
    assert set(render["images_sha256"]) == {
        f"S03_page_{page}.png" for page in (3, 4, 7, 8)}
    assert all(sha(OUT / name) == digest
               for name, digest in render["images_sha256"].items())
    assert table["table_image_sha256"] == render["images_sha256"]["S03_page_4.png"]
    check_table(table)
    # Reference sigma is unchanged C11000 research data, not a foil measurement.
    sigma = 58.58e6
    mu = 4e-7 * math.pi
    scales = []
    for frequency in (2.5e9, 3e9, 3.5e9):
        delta = math.sqrt(1 / (math.pi * frequency * mu * sigma))
        scales.append(dict(
            frequency_Hz=frequency, reference_skin_depth_m=delta,
            rows=[dict(
                id=row["id"], dielectric_Sq_over_reference_delta=(
                    row["dielectric_side_Sq_m"] / delta),
                top_Sq_over_reference_delta=row["top_side_Sq_m"] / delta,
                thickness_over_reference_delta=row["thickness_m"] / delta,
            ) for row in table["rows"]],
        ))
    files = [render_path, table_path, source_path, ROOT / "audit_foil_tables.py",
             ROOT / source["snapshot_path"]]
    files += [OUT / name for name in render["images_sha256"]]
    files += [ROOT / "test_foil_tables.py"]
    files += [OUT / name for name in (
        "test_foil_before_reference_fix.py", "initial_test_failure.json",
        "tests_after_reference_fix.log", "铜箔原表单位与适用范围复核报告.md",
    )]
    return dict(
        scope="Source transcription and dimensional screening only",
        evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in files},
        verifier_sha256=sha(Path(__file__)), rows_checked=len(EXPECTED),
        reviewed_units_and_sides_consistent=True,
        reference_sigma_S_per_m=sigma, foil_sigma_measured=False,
        dimensional_scales=scales, loss_multiplier_computed=False,
        target_band_material_validated=False, G2_closed=False,
        full_system_gates_closed=0, goal_complete=False,
    )


def main():
    result = audit()
    text = json.dumps(result, indent=2) + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    target = OUT / f"verification_{digest[:12]}.json"
    if target.exists():
        assert target.read_text(encoding="utf-8") == text
    else:
        with target.open("x", encoding="utf-8") as stream:
            stream.write(text)
    print(text)


if __name__ == "__main__":
    main()
