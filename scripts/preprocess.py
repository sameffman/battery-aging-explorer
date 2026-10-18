#!/usr/bin/env python3
"""Build the small JSON files the widgets load, from the KIT aging dataset (v2).

Reads  data/raw/cell_eocv2, cell_eisv2   (semicolon-separated CSVs)
Writes data/processed/conditions.json, capacity.json, eis_r.json, impedance/<cell>.json

Run from anywhere:  python3 scripts/preprocess.py
Standard library only. Column meanings: 'Data Structure v05.3.xlsx' in the dataset.

Conventions used (each one is stated here so it can be checked):
  * Cell id: file name cell_eocv2_P011_3_S14_C10.csv -> "P011-3" (condition P011, replicate 3).
    Every real cell has one file per data type; the script stops with an error if a cell has two.
    The placeholder condition P000 (many header-only files, no data) is skipped.
  * Check-up number CU = num_cycles_checkup + 1. Verified against the dataset readme values for
    P011-3 (2.667323 Ah in CU 23, 2.688292 Ah in CU 24). CU 29 is the manual check-up (readme).
  * Capacity = cap_aged_est_Ah from the check-up capacity runs (cyc_condition == 2).
    Each check-up has a charge-direction value (cyc_charged = 1) and a discharge value (= 0).
    The discharge value is the one used for the 80 % point and for the readme cross-check.
  * Equivalent full cycles (EFC) = mean(total charged, total discharged throughput) / 3.0 Ah,
    as defined in the dataset readme (total_q_chg_sum_Ah, total_q_dischg_sum_Ah).
  * Check-up numbers and start times come from every check-up capacity-run row, valid or not.
    Impedance measurements take their check-up number from those start times.
    A check-up with no valid capacity in either direction is kept with null values and reported.
  * 80 % point: the first discharge capacity below 80 % of a reference capacity, linearly
    interpolated between the two check-ups either side of the crossing. CU 29 is not used.
    Two references are computed and stored side by side:
      - initial: the cell's own first valid discharge capacity (usually CU 1)  -> "eol80_initial"
      - nominal: 80 % of the 3.0 Ah nominal capacity, i.e. 2.4 Ah -> "eol80_nominal". The authors'
                 paper defines its SoH scale against nominal capacity; the 80 % threshold on that
                 reference is this project's choice, not theirs.
  * EIS resistances (eis_r.json), calculated from each stored spectrum. Definitions follow the dataset
    authors' example scripts (github.com/energystatusdata/bat-age-data-scripts, generate_log_age.py),
    with a modified R1 search. Implementation is independent of that code.
      - R0: real part where the spectrum crosses the real axis, linearly interpolated between the two
            neighbouring points, searched between 200 Hz and 3.3 kHz.
      - R1: real part at the knee of the spectrum, minus R0. The knee is the point of smallest phase
            angle among local minima of the phase at or below 33 Hz. A local minimum needs a point
            on each side inside that range, so the range's end points never qualify. If that knee has
            fewer than two measured points at lower frequency, it is too close to the end of the data
            to trust and R1 = null. The authors take the smallest phase over 0.49-33 Hz without these
            requirements, which can return an end point instead of a knee. A spectrum with no such
            minimum also has R1 = null.
      Phase = atan2(Im, Re) of the stored values. The dataset stores the imaginary part with its
      sign inverted (positive = capacitive), so Im here is that stored value.
"""
import argparse, csv, json, math, os, re, sys
from collections import Counter, defaultdict
from pathlib import Path

NOMINAL_AH = 3.0
EOL_FRACTION = 0.8
THRESHOLD_AH = EOL_FRACTION * NOMINAL_AH
LAST_REGULAR_CU = 28   # CU 29 is the extra manual check-up
R0_FREQ_RANGE = (200.0, 3300.0)   # Hz, where the real-axis crossing is searched
R1_FREQ_MAX = 33.0                # Hz, upper end of the knee search (everything below is searched)
R1_MIN_POINTS_BELOW = 2           # points at lower frequency than a dip needed to call it a knee
PLACEHOLDER_CONDITION = "000"     # P000: header-only files, no cell
AGE_TYPES = {"1": "calendar", "2": "cyclic", "3": "profile"}
FOLDERS = {"eoc": "cell_eocv2", "eis": "cell_eisv2"}
FILE_RE = re.compile(r"cell_(?:eocv2|eisv2)_P(\d{3})_(\d)_S(\d+)_C(\d+)\.csv$")


def num(s):
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def cell_files(raw):
    """Find every cell's CSV files, one path per data type.

    Returns {cell_id: {"eoc": path, "eis": path}}, for example
    files["P011-3"]["eis"] is the impedance file of cell P011-3. A type with no file for
    that cell is None. Placeholder files (condition P000) are skipped.
    """
    out = defaultdict(lambda: {"eoc": None, "eis": None})
    for kind, folder in FOLDERS.items():
        for p in sorted((Path(raw) / folder).glob("*.csv")):
            m = FILE_RE.search(p.name)
            if not m or m.group(1) == PLACEHOLDER_CONDITION:
                continue
            cid = f"P{m.group(1)}-{m.group(2)}"
            if out[cid][kind] is not None:
                raise SystemExit(f"{cid} has two {kind} files: {out[cid][kind].name} and {p.name}")
            out[cid][kind] = p
    return out


def read_rows(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    rows.sort(key=lambda r: float(r["timestamp_s"]))
    return rows


def to_int(s):
    """int from text that may be written like '25' or '25.0000'."""
    return int(float(s))


def r3(v, n=3):
    return None if v is None else round(v, n)


def build_capacity(rows):
    """-> (conditions entry, capacity entry, {cu: start_timestamp})"""
    t0 = float(rows[0]["timestamp_s"])
    reg = [r for r in rows if to_int(r["cyc_condition"]) == 1]
    ref = reg[0] if reg else rows[0]
    mode = lambda key: Counter(r[key] for r in reg).most_common(1)[0][0] if reg else ref[key]
    cond = {
        "age_type": AGE_TYPES.get(str(to_int(ref["age_type"])), "unknown"),
        "temp_C": to_int(ref["age_temp"]),
        "soc_pct": to_int(ref["age_soc"]) if to_int(ref["age_type"]) == 1 else None,
        "chg_rate_C": float(mode("age_chg_rate")) if to_int(ref["age_type"]) != 1 else None,
        "dischg_rate_C": float(mode("age_dischg_rate")) if to_int(ref["age_type"]) != 1 else None,
        "profile": to_int(ref["age_profile"]) if to_int(ref["age_type"]) == 3 else None,
        "v_max_V": r3(num(mode("v_max_target_V")), 3),
        "v_min_V": r3(num(mode("v_min_target_V")), 3),
    }
    per_cu = {}
    for r in rows:
        if to_int(r["cyc_condition"]) != 2:
            continue
        ts = float(r["timestamp_s"])
        cu = to_int(r["num_cycles_checkup"]) + 1
        # Every check-up capacity run registers its check-up, whether or not it yielded a
        # valid capacity, so check-up numbers and start times never depend on a reading existing.
        d = per_cu.setdefault(cu, {"start": ts, "ts": None})
        d["start"] = min(d["start"], ts)
        cap = num(r["cap_aged_est_Ah"])
        if cap is None:
            continue
        key = "q_chg" if to_int(r["cyc_charged"]) == 1 else "q_dis"
        d[key] = cap                      # a later duplicate overwrites an earlier one
        d["ts"] = ts if d["ts"] is None else min(d["ts"], ts)
        qc, qd = num(r["total_q_chg_sum_Ah"]), num(r["total_q_dischg_sum_Ah"])
        if qc is not None and qd is not None:
            d["efc"] = (qc + qd) / 2 / NOMINAL_AH
        d["t_C"] = num(r["t_start_degC"])
    cus = []
    for cu in sorted(per_cu):
        d = per_cu[cu]
        ts = d["ts"] if d["ts"] is not None else d["start"]
        cus.append({"cu": cu, "days": r3((ts - t0) / 86400, 2), "efc": r3(d.get("efc"), 1),
                    "q_dis": r3(d.get("q_dis"), 4), "q_chg": r3(d.get("q_chg"), 4)})
    ref_pt = reference_capacity(cus)
    cap = {
        "cu": cus,
        "q_ref": {"cu": ref_pt["cu"], "Ah": ref_pt["q_dis"]} if ref_pt else None,
        "eol80_initial": eighty_point(cus, EOL_FRACTION * ref_pt["q_dis"]) if ref_pt else None,
        "eol80_nominal": eighty_point(cus, THRESHOLD_AH),
    }
    flags = []
    last = cus[-1]["cu"] if cus else 0
    if last < LAST_REGULAR_CU:
        flags.append("ends_early")
    if any(c["cu"] == 29 for c in cus):
        flags.append("has_manual_cu29")
    if cap["eol80_initial"] is None and cus:
        flags.append("never_below_80pct_of_initial")
    if cap["eol80_nominal"] is None and cus:
        flags.append("never_below_80pct_of_nominal")
    # Check-ups with no valid capacity in either direction are kept (with null values) and reported.
    empty = [cu for cu in sorted(per_cu) if "q_chg" not in per_cu[cu] and "q_dis" not in per_cu[cu]]
    if empty:
        flags.append("checkup_without_capacity")
        cond["cu_without_capacity"] = empty
    cond["flags"] = flags
    cond["n_cu"] = len(cus)
    cond["last_cu"] = last
    return cond, cap, {cu: per_cu[cu]["start"] for cu in per_cu}


def reference_capacity(cus):
    """First check-up (CU 1 to 28) with a valid discharge capacity, or None."""
    for c in cus:
        if c["q_dis"] is not None and c["cu"] <= LAST_REGULAR_CU:
            return c
    return None


def eighty_point(cus, threshold):
    """Interpolated crossing of `threshold` (Ah) by the discharge capacity, or None."""
    pts = [c for c in cus if c["q_dis"] is not None and c["cu"] <= LAST_REGULAR_CU]
    for a, b in zip(pts, pts[1:]):
        if a["q_dis"] >= threshold > b["q_dis"]:
            f = (a["q_dis"] - threshold) / (a["q_dis"] - b["q_dis"])
            out = {"between_cu": [a["cu"], b["cu"]]}
            for k, nd in (("days", 1), ("efc", 0)):
                if a[k] is not None and b[k] is not None:
                    out[k] = round(a[k] + f * (b[k] - a[k]), nd)
            return out
    return None


def cu_of(ts, cu_starts):
    best = None
    for cu, t in cu_starts.items():
        if t <= ts and (best is None or t > cu_starts[best]):
            best = cu
    return best


def eis_resistances(pts):
    """R0, R1 (mOhm) and the knee frequency (Hz) of one spectrum; None where not determinable.

    pts: [(freq_Hz, re, im)] sorted by descending frequency, im as stored (positive = capacitive).
    """
    r0 = None
    window = [p for p in pts if R0_FREQ_RANGE[0] <= p[0] <= R0_FREQ_RANGE[1]]
    for a, b in zip(window, window[1:]):
        if a[2] <= 0 < b[2]:
            t = -a[2] / (b[2] - a[2])
            r0 = a[1] + t * (b[1] - a[1])
            break
    if r0 is None:
        return None, None, None
    low = [(f, re, math.degrees(math.atan2(im, re))) for f, re, im in pts if f <= R1_FREQ_MAX]
    dips = [k for k in range(1, len(low) - 1)
            if low[k][2] < low[k - 1][2] and low[k][2] <= low[k + 1][2]]
    if not dips:
        return r0, None, None
    k = min(dips, key=lambda i: low[i][2])
    if len(low) - 1 - k < R1_MIN_POINTS_BELOW:
        return r0, None, None             # the knee is too close to the lowest frequency to trust
    return r0, low[k][1] - r0, low[k][0]


def build_eis(rows, cu_starts):
    spectra = {}
    for r in rows:
        if to_int(r["valid"]) != 1:
            continue
        re_, im = num(r["z_re_comp_mOhm"]), num(r["z_im_comp_mOhm"])
        fr = num(r["freq_Hz"])
        if re_ is None or im is None or fr is None:
            continue
        spectra.setdefault(r["sd_block_id"], {"ts": float(r["timestamp_s"]), "soc": to_int(r["soc_nom"]),
                                              "rt": to_int(r["is_rt"]), "pts": []})["pts"].append((fr, re_, im))
    keep = {}
    dups = 0
    for s in sorted(spectra.values(), key=lambda s: s["ts"]):
        cu = cu_of(s["ts"], cu_starts)
        if cu is None or len(s["pts"]) < 5:
            continue
        if (cu, s["soc"], s["rt"]) in keep:
            dups += 1
        pts = sorted(s["pts"], reverse=True)
        keep[(cu, s["soc"], s["rt"])] = {"cu": cu, "soc": s["soc"], "rt": s["rt"],
                                         "f": [round(p[0], 4) for p in pts],
                                         "re": [round(p[1], 2) for p in pts],
                                         "im": [round(p[2], 2) for p in pts],
                                         "res": eis_resistances(pts)}
    spectra_out, resistances = [], []
    for k in sorted(keep):
        entry = keep[k]
        r0, r1, f1 = entry.pop("res")
        spectra_out.append(entry)
        resistances.append([entry["cu"], entry["soc"], entry["rt"], r3(r0, 3), r3(r1, 3), r3(f1, 4)])
    return spectra_out, resistances, dups


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"), sort_keys=True)


def main():
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(root / "data" / "raw"))
    ap.add_argument("--out", default=str(root / "data" / "processed"))
    ap.add_argument("--limit", type=int, default=0, help="only process the first N cells (testing)")
    a = ap.parse_args()
    raw, out = Path(a.raw), Path(a.out)

    files = cell_files(raw)
    cells = sorted(cid for cid in files if files[cid]["eoc"])
    if a.limit:
        cells = cells[: a.limit]

    conditions, capacity, eis_r = {}, {}, {}
    notes = Counter()
    for i, cid in enumerate(cells, 1):
        rows = read_rows(files[cid]["eoc"])
        if not rows:                      # header-only file: nothing to process
            notes["eocv_empty"] += 1
            continue
        cond, cap, cu_starts = build_capacity(rows)
        if cond.get("cu_without_capacity"):
            notes["checkups_without_capacity"] += len(cond["cu_without_capacity"])
            print(f"WARNING {cid}: no valid capacity in either direction at CU {cond['cu_without_capacity']}",
                  file=sys.stderr)
        cond["condition"] = cid.split("-")[0]
        cond["replicate"] = int(cid.split("-")[1])
        conditions[cid], capacity[cid] = cond, cap
        if files[cid]["eis"]:
            spectra, resistances, d = build_eis(read_rows(files[cid]["eis"]), cu_starts)
            notes["eis_duplicate_blocks"] += d
            eis_r[cid] = resistances
            notes["eis_spectra"] += len(resistances)
            notes["eis_without_r0"] += sum(1 for r in resistances if r[3] is None)
            notes["eis_without_r1"] += sum(1 for r in resistances if r[3] is not None and r[4] is None)
            dump(out / "impedance" / f"{cid}.json", spectra)
        else:
            notes["no_eis_file"] += 1
        print(f"[{i}/{len(cells)}] {cid}: {cond['n_cu']} check-ups", file=sys.stderr)

    dump(out / "conditions.json", conditions)
    dump(out / "capacity.json", capacity)
    dump(out / "eis_r.json", eis_r)
    print("cells written:", len(conditions), dict(notes))


if __name__ == "__main__":
    main()
