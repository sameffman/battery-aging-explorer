#!/usr/bin/env python3
"""Equivalent-circuit analysis of the impedance spectra in data/processed/impedance/.

Reads  data/processed/conditions.json, eis_r.json, impedance/<cell>.json  (made by preprocess.py)
Writes data/processed/:
  inductance.json   {cell: [[cu, soc, rt, L_1kHz, L_3kHz, L_5kHz], ...]}   inductance in nH, every spectrum
  fits.json         inductance summary, and circuit fits of a random sample of spectra (see below)

Run from anywhere:  python3 scripts/fit_circuits.py
Needs numpy and scipy (preprocess.py needs only the standard library).

Step 1: inductance from the high-frequency limit.
  Above roughly 1 kHz the spectra dip below the real axis. There Z = R0 + j*w*L, so L is the
  least-squares slope of -Im(stored) against w = 2*pi*f through the origin, over the points at or
  above a minimum frequency. This is done for every spectrum with minimum frequencies of 1, 3 and 5 kHz
  (7, 4 and 3 points; a subset needs at least 3). The subsets are nested, so their agreement checks
  consistency and is not independent confirmation. The 14.7 kHz point is always missing in the dataset.

Step 2: full circuit fits of a sample of spectra.
  Circuit: L - R0 - (R1 || CPE1) [- (R2 || CPE2)] - tail, with Z(CPE) = 1 / (Q * (j*w)^n) and
  R || CPE = R / (1 + (j*w*tau)^alpha). The main circuit has two arcs, and its tail is one of
    cpe      a constant-phase element with free exponent n
    warburg  semi-infinite diffusion, a CPE with n = 0.5
    finite   finite-space diffusion with a blocking boundary, Rd * coth(sqrt(j*w*td)) / sqrt(j*w*td)
  and L is either fixed at the 3 kHz estimate of step 1 or free, which gives six variants. The same circuit with
  one arc and the CPE tail, L fixed or free, is fitted as a reference for what the second arc adds (eight variants in all).
  The second arc is kept slower than the first (tau2 = tau1 * ratio, ratio >= 1), so the two arcs cannot swap.
  The formal standard error of R2 is also stored, from the Jacobian at the solution; a standard
  error as large as R2 itself means the fit does not determine R2.
  Fits use all 28 points of a spectrum. The residual is complex, each point weighted by 1/|Z|, and the
  reported value is the rms relative misfit, adjusted for the number of fitted parameters. Every fit
  starts from several initial guesses and keeps the best one. The formal assumption throughout is that
  the circuit is right; the residual shows how far that is from true.
  Z is built from the stored values as Re - j*Im_stored, because the dataset stores the imaginary part
  with its sign inverted (positive = capacitive).
  Fitting every spectrum (about 39,000) would take many hours, so the sample is random and seeded:
  one random cell per aging condition, and for each check-up group (early CU 1-5, mid 10-20, late 22-28)
  and each measurement temperature flag one random spectrum at any state of charge that has R0 and R1.
  Spectra with fewer than 28 points are skipped and counted.
"""
import argparse
import itertools
import json
import math
import random
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

SEED = 20261022                       # makes the random sample reproducible
FULL_POINTS = 28                      # points in a complete spectrum
L_MIN_FREQS = (1000.0, 3000.0, 5000.0)  # Hz, lower ends of the high-frequency windows
L_MIN_POINTS = 3                      # points a window needs to give an inductance
L_REFERENCE = 1                       # index into L_MIN_FREQS of the window whose L is used as the fixed value
CHECKUP_GROUPS = {"early": (1, 5), "mid": (10, 20), "late": (22, 28)}
TAILS = {"cpe": 2, "warburg": 1, "finite": 2}   # tail element -> number of fitted tail parameters
L_MODES = ("fixed", "free")
TAU1_MIN = 1e-5                       # s, lower bound of the time constant of the first arc
RATIO_MAX = 1e3                       # upper bound of tau2 / tau1
BOUND_TOLERANCE = 1e-6                # distance to a bound that counts, during a fit, as being at it
LIMIT_TOLERANCE = 1e-3                # relative distance to a limit that the summary counts as being at it (stored values are rounded)
NH_PER_UNIT = 1e6                     # mOhm / (rad/s) -> nH
MOHM_PER_NH_RAD = 1e-6                # w * L[nH] -> mOhm


def variant_id(tail, arcs, mode):
    """Key of one fit variant, e.g. 'cpe_fixed' (one arc) or 'cpe+arc_free' (two arcs)."""
    return f"{tail}{'+arc' if arcs == 2 else ''}_{mode}"


# Every tail and L treatment with two arcs, and the one-arc circuit with the CPE tail as the reference for the second arc.
VARIANTS = [(tail, 2, mode) for tail in TAILS for mode in L_MODES] + [("cpe", 1, mode) for mode in L_MODES]


def tail_impedance(tail, q, jw):
    """Impedance of the low-frequency tail element for parameters q at complex frequency jw."""
    if tail == "cpe":
        return 1.0 / (q[0] * jw ** q[1])
    if tail == "warburg":
        return q[0] / np.sqrt(jw)
    x = np.sqrt(jw * q[1])
    return q[0] / x / np.tanh(x)


def circuit_impedance(r0, arcs, tail, tail_q, inductance, w):
    """Impedance (mOhm) of L - R0 - arcs - tail at angular frequencies w; inductance in nH.

    arcs is a list of (R, tau, alpha) triples.
    """
    jw = 1j * w
    z = jw * inductance * MOHM_PER_NH_RAD + r0 + tail_impedance(tail, tail_q, jw)
    for r, tau, alpha in arcs:
        z = z + r / (1 + (jw * tau) ** alpha)
    return z


def inductance_fit(f, im, min_freq):
    """(L in nH, rms residual in mOhm) of -Im = w*L through the origin on points at or above min_freq.

    Returns (None, None) when fewer than L_MIN_POINTS points qualify. im is as stored (positive = capacitive).
    """
    pts = [(2 * math.pi * a, -b) for a, b in zip(f, im) if a >= min_freq]
    if len(pts) < L_MIN_POINTS:
        return None, None
    sxy = sum(x * y for x, y in pts)
    sxx = sum(x * x for x, _ in pts)
    slope = sxy / sxx
    rms = math.sqrt(sum((y - slope * x) ** 2 for x, y in pts) / len(pts))
    return slope * NH_PER_UNIT, rms


def initial_guesses(tail, n_arcs, z):
    """Starting points of the multi-start fit: arc parameters times tail parameter sets.

    Each start is (arc_start, tail_start); arc_start lists R1, tau1 and, for two arcs, R2 and the ratio tau2 / tau1.
    """
    span = float(np.real(z).max() - np.real(z).min()) + 1.0
    tail_starts = {
        "cpe": [(0.3, 0.5), (3, 0.5), (30, 0.8), (3, 0.8)],
        "warburg": [(0.3,), (3,), (30,)],
        "finite": [(r, t) for r in (5, 30, 100) for t in (1.0, 30.0, 1000.0)],
    }[tail]
    if n_arcs == 1:
        arc_starts = [(r1, tau1) for r1 in (0.3 * span, span) for tau1 in (1e-3, 1e-2)]
    else:
        arc_starts = [(f1 * span, tau1, f2 * span, ratio)
                      for f1 in (0.2, 0.5) for f2 in (0.2, 0.5) for tau1 in (1e-3, 1e-2) for ratio in (10.0, 100.0)]
    return itertools.product(arc_starts, tail_starts)


def circuit_fit(f, re, im, tail, n_arcs, l_mode, l_fixed):
    """Fit one spectrum with one circuit; l_mode is 'fixed' (at l_fixed nH) or 'free'.

    Parameters are R0, R1, tau1, alpha1, [R2, ratio, alpha2], the tail parameters and, if free, L.
    Returns a dict with the adjusted rms misfit (%), the parameters, whether any sits at a bound, and the
    fitted curve (re, im as stored), or None if no start converged.
    """
    w = 2 * np.pi * np.array(f)
    z = np.array(re) - 1j * np.array(im)
    n_tail = TAILS[tail]
    tail_lo, tail_hi = {"cpe": ([1e-3, 0.3], [1e4, 1.0]),
                        "warburg": ([1e-3], [1e3]),
                        "finite": ([1e-3, 1e-2], [1e4, 1e6])}[tail]
    lo = [0.5, 1e-3, TAU1_MIN, 0.3]              # R0, R1, tau1, alpha1
    hi = [100, 500, 0.3, 1.0]
    if n_arcs == 2:
        lo += [1e-3, 1.0, 0.3]                  # R2, ratio tau2 / tau1, alpha2
        hi += [500, RATIO_MAX, 1.0]
    n_arc_params = len(lo)
    lo, hi = lo + tail_lo, hi + tail_hi
    if l_mode == "free":
        lo, hi = lo + [1.0], hi + [1e4]

    def unpack(x):
        arcs = [(x[1], x[2], x[3])]
        if n_arcs == 2:
            arcs.append((x[4], x[2] * x[5], x[6]))
        tail_q = x[n_arc_params:n_arc_params + n_tail]
        inductance = x[n_arc_params + n_tail] if l_mode == "free" else l_fixed
        return x[0], arcs, tail_q, inductance

    def residual(x):
        r0, arcs, tail_q, inductance = unpack(x)
        r = (circuit_impedance(r0, arcs, tail, tail_q, inductance, w) - z) / np.abs(z)
        return np.concatenate([r.real, r.imag])

    best = None
    for arc_start, tail_start in initial_guesses(tail, n_arcs, z):
        x0 = [float(np.real(z).min()), arc_start[0], arc_start[1], 0.8]
        if n_arcs == 2:
            x0 += [arc_start[2], arc_start[3], 0.8]
        x0 += list(tail_start) + ([l_fixed] if l_mode == "free" else [])
        try:
            sol = least_squares(residual, np.clip(x0, lo, hi), bounds=(lo, hi), x_scale="jac", max_nfev=4000)
        except Exception:                       # a start that fails is skipped
            continue
        if best is None or sol.cost < best.cost:
            best = sol
    if best is None:
        return None
    x = best.x
    r0, arcs, tail_q, inductance = unpack(x)
    curve = circuit_impedance(r0, arcs, tail, tail_q, inductance, w)
    dof = len(best.fun) - len(x)
    at_bound = any(min(v - a, b - v) < BOUND_TOLERANCE * max(1, abs(v)) for v, a, b in zip(x, lo, hi))
    fit = {"rms_pct": round(100 * math.sqrt(2 * best.cost / dof), 3),
           "L": round(float(inductance), 1), "R0": round(float(r0), 3),
           "R1": round(float(arcs[0][0]), 3), "tau1": round(float(arcs[0][1]), 5), "a1": round(float(arcs[0][2]), 3),
           "tail": [round(float(v), 4) for v in tail_q], "at_bound": bool(at_bound),
           "re": [round(float(v), 2) for v in curve.real],
           "im": [round(float(-v), 2) for v in curve.imag]}
    if n_arcs == 2:
        covariance = np.linalg.pinv(best.jac.T @ best.jac) * (2 * best.cost / dof)
        fit.update({"R2": round(float(arcs[1][0]), 3), "tau2": round(float(arcs[1][1]), 4), "a2": round(float(arcs[1][2]), 3),
                    "R2_rel_se": round(float(math.sqrt(covariance[4, 4]) / x[4]), 2)})
    return fit


def fit_spectrum(job):
    """All circuit fits of one spectrum; job is a sample entry with the spectrum under 'spectrum'."""
    s = job.pop("spectrum")
    l_fixed, _ = inductance_fit(s["f"], s["im"], L_MIN_FREQS[L_REFERENCE])
    job["L_hf"] = round(l_fixed, 1)
    job["fits"] = {variant_id(tail, arcs, mode): circuit_fit(s["f"], s["re"], s["im"], tail, arcs, mode, l_fixed)
                   for tail, arcs, mode in VARIANTS}
    return job


def load_json(path):
    """Contents of a JSON file."""
    with open(path) as fh:
        return json.load(fh)


def inductance_table(impedance_dir, cells):
    """Step 1 for every spectrum: {cell: [[cu, soc, rt, L per window...], ...]} and the fit residuals.

    Returns (table, residuals) where residuals[k] lists the rms residuals (mOhm) of window k.
    """
    table, residuals = {}, [[] for _ in L_MIN_FREQS]
    for cid in cells:
        rows = []
        for s in load_json(impedance_dir / f"{cid}.json"):
            fits = [inductance_fit(s["f"], s["im"], fmin) for fmin in L_MIN_FREQS]
            for k, (_, rms) in enumerate(fits):
                if rms is not None:
                    residuals[k].append(rms)
            rows.append([s["cu"], s["soc"], s["rt"]] + [None if l is None else round(l, 1) for l, _ in fits])
        table[cid] = rows
    return table, residuals


def inductance_summary(table, residuals, conditions):
    """Medians and spreads of the inductance, for the page: by window, between and within cells, and by condition.

    Also the median difference between the spectrum measured at operating temperature and the one measured at room
    temperature in the same check-up and state of charge, per aging temperature.
    """
    ref = 3 + L_REFERENCE
    summary = {"windows_Hz": list(L_MIN_FREQS), "spectra": sum(len(v) for v in table.values())}
    summary["median_nH"] = [round(float(np.median([r[3 + k] for rows in table.values() for r in rows if r[3 + k] is not None])), 1)
                            for k in range(len(L_MIN_FREQS))]
    summary["median_residual_mOhm"] = [round(float(np.median(r)), 3) for r in residuals]
    diffs = [abs(r[3] - r[4]) for rows in table.values() for r in rows if r[3] is not None and r[4] is not None]
    summary["median_abs_diff_1k_3k_nH"] = round(float(np.median(diffs)), 1)
    # Between cells: median of each cell at 50% SoC, room temperature, regular check-ups only.
    per_cell = [float(np.median(v)) for v in
                ([r[ref] for r in rows if r[1] == 50 and r[2] == 1 and r[0] <= 28 and r[ref] is not None]
                 for rows in table.values()) if v]
    summary["between_cells_nH"] = {k: round(float(np.percentile(per_cell, p)), 1)
                                   for k, p in (("p5", 5), ("p25", 25), ("p75", 75), ("p95", 95))}
    within = [float(np.std([r[ref] for r in rows if r[ref] is not None])) for rows in table.values()]
    summary["within_cell_sd_nH"] = round(float(np.median(within)), 1)
    for name, index in (("by_soc_nH", 1), ("by_rt_nH", 2)):
        groups = defaultdict(list)
        for rows in table.values():
            for r in rows:
                if r[ref] is not None:
                    groups[r[index]].append(r[ref])
        summary[name] = {str(k): round(float(np.median(v)), 1) for k, v in sorted(groups.items())}
    paired = defaultdict(list)
    for cell, rows in table.items():
        values = {(r[0], r[1], r[2]): r[ref] for r in rows if r[ref] is not None}
        for (cu, soc, rt), value in values.items():
            if rt == 0 and (cu, soc, 1) in values:
                paired[conditions[cell]["temp_C"]].append(value - values[(cu, soc, 1)])
    summary["paired_operating_minus_room_nH"] = {str(t): round(float(np.median(v)), 1) for t, v in sorted(paired.items())}
    drift = []
    for rows in table.values():
        early = [r[ref] for r in rows if r[0] <= 3 and r[ref] is not None]
        late = [r[ref] for r in rows if 25 <= r[0] <= 28 and r[ref] is not None]
        if early and late:
            drift.append(float(np.mean(late) - np.mean(early)))
    summary["drift_CU25_28_minus_CU1_3_nH"] = {k: round(float(np.percentile(drift, p)), 1)
                                               for k, p in (("p25", 25), ("median", 50), ("p75", 75))}
    return summary


def draw_sample(conditions, resistances, seed):
    """Seeded random sample of spectra: one random cell per condition, one spectrum per check-up group and temperature flag."""
    rng = random.Random(seed)
    by_condition = defaultdict(list)
    for cid, cond in conditions.items():
        if resistances.get(cid):
            by_condition[cond["condition"]].append(cid)
    sample = []
    for condition in sorted(by_condition):
        cid = rng.choice(sorted(by_condition[condition]))
        cond = conditions[cid]
        for group, (lo, hi) in CHECKUP_GROUPS.items():
            for rt in (0, 1):
                candidates = [e for e in resistances[cid]
                              if lo <= e[0] <= hi and e[2] == rt and e[3] is not None and e[4] is not None]
                if candidates:
                    cu, soc, _, r0, r1, _ = rng.choice(sorted(candidates))
                    sample.append({"cell": cid, "condition": condition, "age": cond["age_type"], "T": cond["temp_C"],
                                   "group": group, "cu": cu, "soc": soc, "rt": rt, "r0": r0, "r1": r1})
    return sample


def quartiles(values):
    """Median with 10th and 90th percentiles, rounded for display."""
    p10, med, p90 = np.percentile(values, [10, 50, 90])
    return {"median": round(float(med), 3), "p10": round(float(p10), 3), "p90": round(float(p90), 3)}


def two_arc_summary(ok):
    """Two-arc results per tail element and L treatment, and how they compare with the one-arc circuit and with each other."""
    out = {}
    for tail in TAILS:
        for mode in L_MODES:
            two = variant_id(tail, 2, mode)
            arc = [r["fits"][two] for r in ok]
            entry = {
                "R1_mOhm": quartiles([f["R1"] for f in arc]),
                "R2_mOhm": quartiles([f["R2"] for f in arc]),
                "R2_rel_se": quartiles([f["R2_rel_se"] for f in arc]),
                "R2_se_at_least_R2": sum(f["R2_rel_se"] >= 1 for f in arc),
                "a1": quartiles([f["a1"] for f in arc]),
                "a2": quartiles([f["a2"] for f in arc]),
                "tau_ratio": quartiles([f["tau2"] / f["tau1"] for f in arc]),
                "a1_at_upper_bound": sum(f["a1"] >= 1 - LIMIT_TOLERANCE for f in arc),
                "a2_at_upper_bound": sum(f["a2"] >= 1 - LIMIT_TOLERANCE for f in arc),
                "tau_ratio_at_upper_bound": sum(f["tau2"] / f["tau1"] >= RATIO_MAX * (1 - LIMIT_TOLERANCE) for f in arc),
                "tau1_at_lower_bound": sum(f["tau1"] <= TAU1_MIN * (1 + LIMIT_TOLERANCE) for f in arc),
                "at_bound": sum(f["at_bound"] for f in arc),
                "R1_plus_R2_over_model_free_R1": quartiles([(f["R1"] + f["R2"]) / r["r1"] for r, f in zip(ok, arc)]),
                "R1_over_model_free_R1": quartiles([f["R1"] / r["r1"] for r, f in zip(ok, arc)]),
                "R0_fit_minus_model_free": quartiles([f["R0"] - r["r0"] for r, f in zip(ok, arc)])}
            if tail == "cpe":
                one = variant_id(tail, 1, mode)
                entry.update({
                    "misfit_over_one_arc": quartiles([r["fits"][two]["rms_pct"] / r["fits"][one]["rms_pct"] for r in ok]),
                    "better_than_one_arc": sum(r["fits"][two]["rms_pct"] < r["fits"][one]["rms_pct"] for r in ok),
                    "R0_minus_one_arc": quartiles([r["fits"][two]["R0"] - r["fits"][one]["R0"] for r in ok]),
                    "tail_exponent": quartiles([f["tail"][1] for f in arc]),
                    "tail_exponent_at_upper_bound": sum(f["tail"][1] >= 1 - LIMIT_TOLERANCE for f in arc)})
            out[f"{tail}_{mode}"] = entry
    fixed, free = (variant_id("cpe", 2, mode) for mode in L_MODES)
    out["free_L_over_hf_L"] = quartiles([r["fits"][free]["L"] / r["L_hf"] for r in ok])
    out["free_over_fixed_misfit"] = quartiles([r["fits"][free]["rms_pct"] / r["fits"][fixed]["rms_pct"] for r in ok])
    out["R1_free_minus_fixed"] = quartiles([r["fits"][free]["R1"] - r["fits"][fixed]["R1"] for r in ok])
    out["R2_free_minus_fixed"] = quartiles([r["fits"][free]["R2"] - r["fits"][fixed]["R2"] for r in ok])
    for tail in ("warburg", "finite"):
        for mode in L_MODES:
            a, b = variant_id(tail, 2, mode), variant_id("cpe", 2, mode)
            out[f"{tail}_over_cpe_{mode}"] = quartiles([r["fits"][a]["rms_pct"] / r["fits"][b]["rms_pct"] for r in ok])
            out[f"{tail}_better_than_cpe_{mode}"] = sum(r["fits"][a]["rms_pct"] < r["fits"][b]["rms_pct"] for r in ok)
    return out


def fit_summary(results):
    """Medians over the fitted spectra, for the whole sample and by measurement temperature and check-up group."""
    variants = [variant_id(*v) for v in VARIANTS]
    ok = [r for r in results if all(r["fits"][v] for v in variants)]
    strata = {"all": ok}
    for rt, name in ((0, "aging temperature"), (1, "room temperature")):
        strata[name] = [r for r in ok if r["rt"] == rt]
    for group in CHECKUP_GROUPS:
        strata[group] = [r for r in ok if r["group"] == group]
    for temp in sorted({r["T"] for r in ok}):
        strata[f"{temp} °C"] = [r for r in ok if r["T"] == temp]
    table = {}
    for name, rows in strata.items():
        table[name] = {"n": len(rows), "variants": {
            v: {"rms_pct": quartiles([r["fits"][v]["rms_pct"] for r in rows]),
                "a1": quartiles([r["fits"][v]["a1"] for r in rows]),
                **({"a2": quartiles([r["fits"][v]["a2"] for r in rows])} if "+arc" in v else {}),
                "at_bound": sum(r["fits"][v]["at_bound"] for r in rows)} for v in variants}}
    return {"by_stratum": table, "two_arc": two_arc_summary(ok)}


def dump(path, obj):
    """Write obj to path as compact JSON with sorted keys."""
    with open(path, "w") as fh:
        json.dump(obj, fh, separators=(",", ":"), sort_keys=True)


def main():
    """Run step 1 on every spectrum, step 2 on the sample, and write the JSON outputs."""
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(root / "data" / "processed"))
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="only fit the first N sampled spectra (testing)")
    args = ap.parse_args()
    data = Path(args.data)
    impedance_dir = data / "impedance"
    if not impedance_dir.is_dir():
        raise SystemExit(f"{impedance_dir} not found; run scripts/preprocess.py first")

    conditions = load_json(data / "conditions.json")
    resistances = load_json(data / "eis_r.json")
    cells = sorted(resistances)

    print("step 1: inductance of every spectrum", file=sys.stderr)
    table, residuals = inductance_table(impedance_dir, cells)
    dump(data / "inductance.json", table)

    sample = draw_sample(conditions, resistances, args.seed)
    cache = {}
    jobs, skipped = [], 0
    for entry in sample[:args.limit or None]:
        spectra = cache.setdefault(entry["cell"], load_json(impedance_dir / f"{entry['cell']}.json"))
        s = next(s for s in spectra if (s["cu"], s["soc"], s["rt"]) == (entry["cu"], entry["soc"], entry["rt"]))
        if len(s["f"]) != FULL_POINTS:
            skipped += 1
            continue
        jobs.append(dict(entry, spectrum=s))
    print(f"step 2: fitting {len(jobs)} spectra ({skipped} skipped for having fewer than {FULL_POINTS} points)",
          file=sys.stderr)
    with Pool(args.workers) as pool:
        results = pool.map(fit_spectrum, jobs, chunksize=4)

    dump(data / "fits.json", {
        "meta": {"seed": args.seed, "sampled": len(sample), "fitted": len(results), "skipped_short": skipped,
                 "variants": [variant_id(*v) for v in VARIANTS]},
        "inductance": inductance_summary(table, residuals, conditions),
        "summary": fit_summary(results),
        "spectra": results})
    print("done:", len(results), "spectra fitted", file=sys.stderr)


if __name__ == "__main__":
    main()
