"""First end-to-end operator numbers for the stored 111 doublet data.

A wave computer is only useful if the cavity operator is (a) exactly linear in
the drives and (b) predictable across a band from a finite calibration set.
Both are measurable from the existing full-Maxwell scan without new solves:

  linearity   mixed drive response vs the superposition of the two basis responses
  prediction  fit the 2x2 response on four frequencies, predict the fifth

Errors are reported as relative Frobenius norms, so they are directly comparable
with the 1 % acceptance limit already used for Q.
"""

import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "算子预测校核结果"
EVIDENCE = ROOT / "铜腔111双极化结果"
SHAPES = ((24, 15, 9), (36, 23, 14))
LIMIT = .01


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative_error(predicted, measured):
    return float(np.linalg.norm(predicted - measured) / np.linalg.norm(measured))


def fit_model(frequency, response):
    center, unit = float(np.mean(frequency)), float(np.ptp(frequency))
    inverse = np.linalg.inv(response)
    matrix = np.column_stack(((frequency - center) / unit, np.ones(len(frequency))))
    coefficients = np.linalg.lstsq(matrix, inverse.reshape(len(frequency), 4), rcond=None)[0]
    return dict(center=center, unit=unit, coefficients=coefficients.reshape(2, 2, 2))


def predict(model, frequency):
    scale = (frequency - model["center"]) / model["unit"]
    inverse = model["coefficients"][0] * scale + model["coefficients"][1]
    return np.linalg.inv(inverse)


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior audit"
    OUT.mkdir()
    report = dict(
        scope="linearity and band prediction of the measured 2x2 cavity operator",
        limitation="modal-projection ports, not physical 50 ohm connectors",
        source_sha256={name: sha(ROOT / name) for name in (
            "run_operator_prediction.py", "copper_doublet.py", "finite_copper_cavity.py")},
        cases=[])

    for shape in SHAPES:
        stem = "111_" + "x".join(map(str, shape))
        scan = np.load(EVIDENCE / (stem + "_scan.npz"))
        frequency = scan["frequency_Hz"]
        response = scan["response"]
        drives = scan["drives_A"][:, :2]
        mixed = scan["mixed_coefficients"]
        fields = scan["e_V"]

        # 1. exact linearity: response to the mixed drive vs the superposition
        measured_mixed = np.stack([drives.T @ fields[i][:, 2] for i in range(len(frequency))])
        predicted_mixed = response @ mixed
        linearity = relative_error(predicted_mixed, measured_mixed)

        # 2. leave-one-frequency-out prediction from a four-point calibration
        leave_one_out, predictions = [], []
        for held in range(len(frequency)):
            keep = [i for i in range(len(frequency)) if i != held]
            model = fit_model(frequency[keep], response[keep])
            predicted = predict(model, frequency[held])
            error = relative_error(predicted, response[held])
            leave_one_out.append(dict(frequency_Hz=float(frequency[held]), error=error))
            predictions.append(predicted)

        # 3. central three-point calibration predicting the two outer points
        central = [1, 2, 3]
        model = fit_model(frequency[central], response[central])
        extrapolation = [dict(frequency_Hz=float(frequency[i]),
                              error=relative_error(predict(model, frequency[i]),
                                                   response[i]))
                         for i in (0, 4)]

        model_all = fit_model(frequency, response)
        in_band = relative_error(np.stack([predict(model_all, f) for f in frequency]),
                                 response)
        report["cases"].append(dict(
            shape=list(shape),
            frequencies_Hz=frequency.tolist(),
            operator_norm=float(np.max(np.linalg.norm(response, axis=(1, 2)))),
            condition_numbers=[float(np.linalg.cond(r)) for r in response],
            linearity_error=linearity,
            leave_one_out=leave_one_out,
            max_leave_one_out_error=float(max(row["error"] for row in leave_one_out)),
            central_extrapolation=extrapolation,
            max_extrapolation_error=float(max(row["error"] for row in extrapolation)),
            fitted_model_band_error=in_band,
            passed=bool(max(row["error"] for row in leave_one_out) < LIMIT
                        and max(row["error"] for row in extrapolation) < LIMIT
                        and linearity < 1e-10)))
        print(shape, "linearity", linearity,
              "max LOO", report["cases"][-1]["max_leave_one_out_error"],
              "max extrapolation", report["cases"][-1]["max_extrapolation_error"],
              flush=True)

    (OUT / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["cases"], indent=2), flush=True)


if __name__ == "__main__":
    main()
