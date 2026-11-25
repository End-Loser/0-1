"""Frozen-calibration independent corners, histories and prescribed gain drift."""

import argparse
import copy
import hashlib
import itertools
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from run_coupled_logic import circuit, input_pwl, transition_order, NAMES, VECTORS
from run_reliability import setup, reference_adjust
from run_spice_regeneration import solve, write_csv

ROOT = Path(__file__).parent
DELTA = .01
PERIODS = (15, 20, 25, 30, 40, 50, 60, 80)
SEED = 20261117
GAIN_SOURCES = ("Eca", "Ecb", "Ecd", "Esa", "Esb", "Esd", "Ecancel")


def triple_order():
    """Euler tour of pair vertices: every directed state triple occurs once."""
    remaining = {(a, b): list(range(7, -1, -1)) for a in range(8) for b in range(8)}
    stack, trail = [(0, 0)], []
    while stack:
        a, b = stack[-1]
        if remaining[(a, b)]:
            stack.append((b, remaining[(a, b)].pop()))
        else:
            trail.append(stack.pop())
    vertices = list(reversed(trail))
    return [f"{vertices[0][0]:03b}"]+[f"{b:03b}" for _, b in vertices]


def sequences():
    rng = np.random.default_rng(SEED)
    stress = (["000", "111"]*32 + ["001", "110"]*32 +
              ["011", "101", "110", "111"]*16 +
              [s for i in range(8) for s in [f"{i:03b}"]*8])
    return {"euler": transition_order(), "triples": triple_order(),
            "stress": stress, "seeded_history": [f"{i:03b}" for i in rng.integers(0, 8, 192)]}


def control_points(mode, errors, slots, period):
    duration = slots*period*1e-9
    if mode == "constant":
        return [[(0, e), (duration, e)] for e in errors]
    points = []
    for bit in range(7):
        if mode == "all_triangular":
            t = np.linspace(0, duration, slots+1)
            phase = (t/duration*(bit+2)+bit/7) % 1
            values = DELTA*(1-4*abs(phase-.5))
            points.append(list(zip(t.tolist(), values.tolist())))
            continue
        start, end = errors[bit], errors[bit]
        if bit == 6:
            if mode == "loss_ramp":
                start, end = 0, -.30
            elif mode == "gain_ramp":
                start, end = 0, .35
            elif mode == "bounded_positive":
                start, end = 0, DELTA
            elif mode == "bounded_negative":
                start, end = 0, -DELTA
            elif mode.startswith("trigger"):
                sign = 1 if mode.endswith("positive") else -1
                start = sign*.005
                end = (1+start)*(1+sign*.0049)-1
            else:
                raise ValueError(mode)
        # Reserve the final 16 states to observe the endpoint at constant gain.
        points.append([(0, float(start)), ((slots-16)*period*1e-9, float(end)),
                       (duration, float(end))])
    return points


def make_lines(profile, calibrations, states, period, errors, mode):
    coefficients, options, skew, shifts = setup(profile, calibrations)
    slot = period*1e-9
    inputs = [input_pwl([int(s[b]) for s in states], slot, skew[b]*1e-9) for b in range(3)]
    lines = reference_adjust(circuit(inputs, coefficients, **options), shifts)
    points = control_points(mode, errors, len(states), period)
    result = []
    for line in lines:
        tokens = line.split()
        if tokens and tokens[0] in GAIN_SOURCES:
            index = GAIN_SOURCES.index(tokens[0])
            coefficient = float(tokens[-1])
            if mode == "constant":
                tokens[-1] = f"{coefficient*(1+errors[index]):.12g}"
                line = " ".join(tokens)
            else:
                # The control signal is a prescribed environmental trajectory, never an answer.
                line = (f"B{tokens[0][1:]} {tokens[1]} {tokens[2]} V="
                        f"{coefficient:.12g}*v({tokens[3]},{tokens[4]})*(1+v(err{index}))")
        result.append(line)
    for index, pts in enumerate(points):
        if mode == "constant":
            source = f"DC {errors[index]:.12g}"
        else:
            source = "PWL("+" ".join(f"{t:.12g} {v:.12g}" for t, v in pts)+")"
        result.append(f"Verr{index} err{index} 0 {source}")
    return result, points, options.get("supply", 3.3)


def measure_raw(raw, sampling, states, period_ns, supply):
    """Use all raw observations for windows; interpolate only on unique times."""
    times, slot, rows = raw["time"], period_ns*1e-9, []
    for index, state in enumerate(states):
        start, end = index*slot, (index+1)*slot
        left, right = np.searchsorted(times, (start, end), side="left")
        local_t = times[left:right]
        late = local_t >= start+.8*slot
        count = state.count("1")
        previous_count = states[max(0, index-1)].count("1")
        for channel, expected in (("carry", int(count >= 2)), ("sum", count % 2)):
            values = raw[channel][left:right]
            good = values >= .7*supply if expected else values <= .3*supply
            bad = np.flatnonzero(~good)
            settled = (float(local_t[bad[-1]+1]-start)
                       if len(bad) and bad[-1]+1 < len(local_t)
                       else (0.0 if not len(bad) else None))
            sampled = float(np.interp(start+.9*slot, sampling["time"], sampling[channel]))
            sample_good = sampled >= .7*supply if expected else sampled <= .3*supply
            late_good = bool(np.any(late) and np.all(good[late]))
            previous = int(previous_count >= 2) if channel == "carry" else previous_count % 2
            rows.append({"slot_index": index, "from_state": states[max(0, index-1)],
                         "state": state, "channel": channel, "expected": expected,
                         "sample_time_s": start+.9*slot, "sampled_output_V": sampled,
                         "sample_correct": bool(sample_good), "late_window_correct": late_good,
                         "correct": bool(sample_good and late_good),
                         "settling_to_30_70_percent_s": settled,
                         "unchanged_expected_with_excursion": bool(index and previous == expected and len(bad))})
    return rows


def run_case(spec, args, source, seqs, signature):
    folder, key = args.output / "runs", spec["case"]
    record_path = folder / (key+".json")
    fingerprint = hashlib.sha256(json.dumps(
        {"spec": spec, "signature": signature}, sort_keys=True).encode()).hexdigest()
    if args.resume and record_path.exists():
        previous = json.loads(record_path.read_text(encoding="utf-8"))
        if previous.get("fingerprint") == fingerprint and all(
                (folder / (key+ext)).exists() for ext in (".npz", ".cir", ".csv", ".log")) and (
                    previous.get("duplicate_time_points", 0) == 0 or
                    (folder / (key+".raw.npz")).exists()):
            return previous
    states = seqs[spec["sequence"]]
    lines, points, supply = make_lines(source["frozen_profiles"][spec["profile"]],
                                     source["calibrations"], states, spec["period_ns"],
                                     spec["errors"], spec["mode"])
    lines.append(f".options method={spec['method']}")
    dt, duration = spec["dt_ps"]*1e-12, len(states)*spec["period_ns"]*1e-9
    vectors = VECTORS+[f"v(err{i})" for i in range(7)]
    data, log, text = solve(args.executable, lines,
                           ["set numdgt=17", f"tran {dt:.12g} {duration:.12g} 0 {dt:.12g}"], vectors)
    raw = dict(zip(NAMES+[f"error{i}" for i in range(7)], data.T))
    differences = np.diff(raw["time"])
    assert np.all(differences >= 0), "Solver time reversal."
    duplicates = np.flatnonzero(differences == 0)
    starts = np.r_[0, np.flatnonzero(differences != 0)+1]
    stops = np.r_[starts[1:], len(raw["time"])]
    repeated = [(a, b) for a, b in zip(starts, stops) if b-a > 1]
    vector_difference = max((float(np.ptp(values[a:b])) for a, b in repeated
                             for name, values in raw.items() if name != "time"), default=0)
    output_difference = max((float(np.ptp(raw[name][a:b])) for a, b in repeated
                             for name in ("carry", "sum")), default=0)
    assert vector_difference <= 1e-9, "Ambiguous same-time solver records."
    keep = np.r_[differences != 0, True]
    arrays = {name: values[keep] for name, values in raw.items()}
    rows = measure_raw(raw, arrays, states, spec["period_ns"], supply)
    energy = float(np.trapz(-supply*raw["supply_current"], raw["time"]))
    margins, first_bad, worst_slot = [], None, None
    slot = spec["period_ns"]*1e-9
    for row in rows:
        index, channel = row["slot_index"], row["channel"]
        begin, end = index*slot, (index+1)*slot
        left, right = np.searchsorted(raw["time"], (begin+.8*slot, end), side="left")
        values = raw[channel][left:right]
        margin = values-.7*supply if row["expected"] else .3*supply-values
        minimum = float(min(margin))
        margins.append(minimum)
        if worst_slot is None or minimum < worst_slot["margin_V"]:
            worst_slot = {"slot": index, "state": row["state"], "channel": channel,
                          "margin_V": minimum}
        if not row["correct"] and first_bad is None:
            first_bad = {"slot": index, "state": row["state"], "channel": channel,
                         "sample_error6": float(np.interp(row["sample_time_s"], arrays["time"],
                                                         arrays["error6"])),
                         "slot_start_ns": begin*1e9}
    result = {**spec, "fingerprint": fingerprint, "archive_numdgt": 17,
              "supply_V": supply, "control_points": points,
              "raw_points": len(raw["time"]), "unique_points": len(arrays["time"]),
              "duplicate_time_points": len(duplicates),
              "duplicate_vector_max_difference": vector_difference,
              "duplicate_output_max_difference_V": output_difference,
              "slots": len(states), "decisions": len(rows), "passed": sum(r["correct"] for r in rows),
              "sample_passed": sum(r["sample_correct"] for r in rows),
              "minimum_late_margin_V": min(margins), "worst_slot": worst_slot,
              "first_bad": first_bad,
              "max_settling_ns": max((r["settling_to_30_70_percent_s"]*1e9 for r in rows
                                     if r["settling_to_30_70_percent_s"] is not None), default=0),
              "unsettled": sum(r["settling_to_30_70_percent_s"] is None for r in rows),
              "energy_J": energy, "power_W": energy/duration, "energy_per_slot_J": energy/len(states),
              "warning": "warning" in log.lower()}
    if len(duplicates):
        np.savez_compressed(folder / (key+".raw.npz"), **raw)
    np.savez_compressed(folder / (key+".npz"), **arrays)
    (folder / (key+".cir")).write_text(text, encoding="ascii")
    (folder / (key+".log")).write_text(log, encoding="utf-8")
    write_csv(folder / (key+".csv"), rows)
    record_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def specification(label, scope, tag, errors, period=80, sequence="euler",
                  mode="constant", dt=200, method="trap"):
    return {"case": f"{label}_{scope}_{tag}_{period}ns_{dt}ps_{method}",
            "profile": label, "scope": scope, "tag": tag, "errors": list(errors),
            "period_ns": period, "sequence": sequence, "mode": mode, "dt_ps": dt, "method": method}


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "runs").mkdir(exist_ok=True)
    source = json.loads((args.source / "gain_calibration_summary.json").read_text(encoding="utf-8"))
    verified = json.loads((args.source / "gain_calibration_verification.json").read_text(encoding="utf-8"))
    assert source["acceptance"] and verified["bounded_proxy_acceptance"]
    assert verified["data_integrity"] == "PASS"
    signature = {
        "source_summary_sha256": hashlib.sha256(
            (args.source / "gain_calibration_summary.json").read_bytes()).hexdigest(),
        "solver_sha256": hashlib.sha256(args.executable.read_bytes()).hexdigest(),
        "code_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                        for name in ("run_dynamic_reliability.py", "run_coupled_logic.py",
                                     "run_reliability.py", "run_spice_regeneration.py")},
    }
    seqs = sequences()
    corners = [(f"{i:03d}", tuple(DELTA*s for s in signs))
               for i, signs in enumerate(itertools.product((-1, 1), repeat=7))]
    rng = np.random.default_rng(SEED+1)
    interior = [(f"{i:03d}", tuple(values)) for i, values in enumerate(rng.uniform(-DELTA, DELTA, (32, 7)))]
    cases, failures, boundaries = [], [], []

    def batch(specs):
        results = []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(run_case, s, args, source, seqs, signature): s for s in specs}
            for count, future in enumerate(as_completed(futures), 1):
                spec = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    cases.append(result)
                except Exception as error:
                    failures.append({"spec": spec, "error": str(error)})
                if count % 32 == 0 or count == len(specs):
                    print(f"{specs[0]['scope']}: {count}/{len(specs)} completed", flush=True)
        return sorted(results, key=lambda r: r["case"])

    def checkpoint():
        version = subprocess.run([str(args.executable), "--version"], capture_output=True,
                                 text=True, timeout=10)
        summary = {"signature": signature, "source": str(args.source),
                   "archive_numdgt": 17,
                   "frozen_profiles": source["frozen_profiles"], "calibrations": source["calibrations"],
                   "sequences": seqs, "corners": corners, "interior": interior,
                   "delta": DELTA, "seed": SEED, "period_grid_ns": PERIODS,
                   "cases": sorted(cases, key=lambda c: c["case"]), "failures": failures,
                   "boundaries": boundaries, "solver_version": version.stdout+version.stderr,
                   "acceptance_definition": "All 128 corners and 32 interior points at 80 ns; "
                   "all candidate corners, selected histories and bounded drift/crosschecks pass. "
                   "This is a finite deterministic model gate, not a continuous/hardware proof."}
        summary["finite_model_gate"] = (
            len(boundaries) == 8 and not failures and
            all(c["passed"] == c["decisions"] and not c["warning"]
                for c in cases if c["scope"] in ("corners80", "interior80", "center80", "history80")) and
            all(b["candidate_confirmed"] for b in boundaries))
        (args.output / "dynamic_reliability_summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8")

    for label in source["frozen_profiles"]:
        print("Dynamic profile "+label, flush=True)
        eighty = batch([specification(label, "corners80", tag, errors) for tag, errors in corners])
        batch([specification(label, "interior80", tag, errors) for tag, errors in interior]+
              [specification(label, "center80", "center", [0]*7)])
        if len(eighty) != 128:
            checkpoint()
            continue
        slow = max(eighty, key=lambda r: (r["max_settling_ns"], -r["minimum_late_margin_V"]))
        low = min(eighty, key=lambda r: r["minimum_late_margin_V"])
        probes = {(r["tag"], tuple(r["errors"])) for r in (slow, low)}
        probes.add(("center", (0,)*7))
        batch([specification(label, "history80", tag+"_"+seq, errors, sequence=seq)
               for tag, errors in sorted(probes) for seq in ("triples", "stress", "seeded_history")])
        timing = batch([specification(label, "timing_probe", tag+"_"+seq, errors,
                                      period=period, sequence=seq)
                        for period in PERIODS for tag, errors in sorted(probes)
                        for seq in ("euler", "stress")])
        passing = [p for p in PERIODS if len([r for r in timing if r["period_ns"] == p]) == 2*len(probes)
                   and all(r["passed"] == r["decisions"] for r in timing if r["period_ns"] == p)]
        candidate, confirmed, final = None, False, []
        for period in passing:
            final = eighty if period == 80 else batch([
                specification(label, "candidate_corners", tag, errors, period=period)
                for tag, errors in corners])
            if len(final) == 128 and all(r["passed"] == r["decisions"] for r in final):
                candidate = period
                break
        if candidate is not None:
            slow_final = max(final, key=lambda r: (r["max_settling_ns"], -r["minimum_late_margin_V"]))
            low_final = min(final, key=lambda r: r["minimum_late_margin_V"])
            final_probes = {(r["tag"], tuple(r["errors"])) for r in (slow_final, low_final)}
            final_probes.add(("center", (0,)*7))
            checks = batch([
                specification(label, "candidate_crosscheck", tag+"_"+seq, errors, period=candidate,
                              sequence=seq, dt=dt, method=method)
                for tag, errors in sorted(final_probes) for seq in ("euler", "triples", "stress")
                for dt, method in ((100, "trap"), (50, "trap"), (100, "gear"))])
            bounded = batch([
                specification(label, "bounded_drift", mode, slow_final["errors"], period=candidate,
                              sequence="triples", mode=mode, dt=100)
                for mode in ("bounded_positive", "bounded_negative", "all_triangular",
                             "trigger_positive", "trigger_negative")])
            drift_checks = batch([
                specification(label, "drift_crosscheck", r["mode"], r["errors"],
                              period=candidate, sequence="triples", mode=r["mode"], dt=dt, method=method)
                for r in bounded for dt, method in ((50, "trap"), (100, "gear"))])
            batch([
                specification(label, "beyond_budget", mode, [0]*7, period=candidate,
                              sequence="triples", mode=mode, dt=100)
                for mode in ("loss_ramp", "gain_ramp")])
            confirmed = (len(checks) == len(final_probes)*9 and len(bounded) == 5 and
                         len(drift_checks) == 10 and all(r["passed"] == r["decisions"] and not r["warning"]
                                                       for r in checks+bounded+drift_checks))
        boundaries.append({"profile": label, "candidate_period_ns": candidate,
                           "candidate_confirmed": confirmed,
                           "probes80": sorted(probes), "candidate_scope": "All 128 corners plus "
                           "selected histories/crosschecks and bounded drift; not all histories"})
        checkpoint()
    checkpoint()
    print(json.dumps({"profiles": boundaries, "cases": len(cases), "failures": failures}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=ROOT / "第九轮结果")
    parser.add_argument("--output", type=Path, default=ROOT / "第十轮结果")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3, 4), default=4)
    parser.add_argument("--resume", action="store_true")
    main(parser.parse_args())
