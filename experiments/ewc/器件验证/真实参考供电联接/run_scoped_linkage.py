"""Scope unchanged vendor libraries per wrapper to prevent private-model name clashes."""
import json
from pathlib import Path
import shutil

import run_linked_reference as original

ROOT = Path(__file__).resolve().parent


def scoped_deck(config, models, helper):
    net = original_deck(config, models, helper)
    for model in models.values():
        net = net.replace(f'.include "{model.as_posix()}"\n', "")
    wrappers = []
    definitions = [
        ("EWC_FDA", "fda", "IP IM OP OM CM VP VN", "THS4551"),
        ("EWC_CMP", "comparator", "IP IM SH VP VN OUT", "TLV3501"),
        ("EWC_REF", "reference", "VIN TEMP GND TRIM OUT", "REF5050"),
        ("EWC_BUFFER", "buffer", "IP IM VP VN OUT", "OPAx320-Q1"),
    ]
    for wrapper, identity, pins, device in definitions:
        wrappers.append(f""".subckt {wrapper} {pins}
.include "{models[identity].as_posix()}"
XDEVICE {pins} {device}
.ends {wrapper}
""")
        net = net.replace(" " + device + "\n", " " + wrapper + "\n")
    if config["real_rails"]:
        net = net.replace(".param V_OUT={LEVEL}\n",
                          f'.param V_OUT={{LEVEL}}\n.include "{models["regulator"].as_posix()}"\n')
    net = net.replace(".temp 25\n", "\n".join(wrappers) + "\n.temp 25\n")
    return net


original_deck = original.deck


def main():
    destination = ROOT / "scoped"
    assert not destination.exists()
    initial_sha = original.sha(ROOT / "summary.json")
    destination.mkdir()
    for file in ("sources.json", "matching_datasheet.json"):
        shutil.copyfile(ROOT / file, destination / file)
    original.ROOT = destination
    original.deck = scoped_deck
    original.main()
    result_path = destination / "summary.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["execution_revision"] = dict(
        prior_summary_sha256=initial_sha,
        shared_private_model_names=["DVN", "_S1", "_S2", "ESD_SW", "R_NOISELESS"],
        fix="lexically scoped .include per EWC wrapper, no edited or renamed library contents",
        physical_circuit_changed=False, initial_attempts_preserved=True,
        wrapper_sha256=original.sha(Path(__file__)))
    result_path.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    assert original.sha(ROOT / "summary.json") == initial_sha


if __name__ == "__main__":
    main()
