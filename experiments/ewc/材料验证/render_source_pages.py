"""Render ambiguous source tables locally, without modifying original PDFs."""
import os
from pathlib import Path

import pypdfium2

ROOT = Path(__file__).resolve().parent


def main():
    for sid, page in [("S01", 1), ("S03", 3), ("S03", 7)]:
        pdf = pypdfium2.PdfDocument(
            Path(os.environ["TEMP"]) / "ewc_dielectric_official_pdf" / f"{sid}.pdf")
        image = pdf[page].render(scale=2).to_pil()
        target = ROOT / "source_snapshots" / f"{sid}_page_{page + 1}.png"
        image.save(target)
        print(target)
        pdf.close()


if __name__ == "__main__":
    main()
