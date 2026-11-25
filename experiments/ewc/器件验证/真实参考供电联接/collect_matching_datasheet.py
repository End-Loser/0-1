"""Keep the initial commercial sheet; obtain the exact Q1 model's sheet separately."""
import importlib.util
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent


def main():
    assert not (ROOT / "matching_datasheet.json").exists()
    helper = ROOT.parent / "\u516c\u5f00\u6a21\u578b\u8865\u9f50" / "collect_public_models.py"
    spec = importlib.util.spec_from_file_location("source_helper", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cache = Path(tempfile.mkdtemp(prefix="ewc-buffer-q1-sheet-"))
    row = module.collect(("buffer_q1_datasheet", "https://www.ti.com/lit/gpn/opa320-q1"), cache)
    result = dict(source=row, reason="downloaded model is OPAx320-Q1, not commercial OPA320",
                  model_to_commercial_substitution_accepted=False,
                  collector_sha256=module.sha(Path(__file__)))
    (ROOT / "matching_datasheet.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(row["status"], row["cache"])


if __name__ == "__main__":
    main()
