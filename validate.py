"""Independent re-check entry (CONTRACT.md).

This script never calls the analyzer's internal solver.  It recomputes
expected service bounds and maximum-service verdicts on the small control
materials by enumerating the vertices of the offset polytope with its own
exact rational half-space intersection, then compares them against the
analyzer outputs produced through the fixed analysis.py entry point and
re-verified by audit.verify.  It also checks two invariance properties and
one sync-bound perturbation, all on temporary copies so the sealed
materials are never modified.
"""

import copy
import json
import subprocess
import sys
import tempfile
from fractions import Fraction as F
from pathlib import Path

import audit
import source


# ---------------------------------------------------------------------------
# Independent exact polytope vertex enumeration (no analysis imports)
# ---------------------------------------------------------------------------

def _halfspaces(d):
    """Return (A, b) with every constraint row A_i*x <= b_i over host order."""
    rows, _ = source.linear(d)
    hosts = list(d["offsets"])
    pos = {h: j for j, h in enumerate(hosts)}
    A, b, ids = [], [], []
    for name, (coef, rhs) in rows.items():
        A.append([F(coef.get(h, 0)) for h in hosts])
        b.append(F(rhs))
        ids.append(name)
    return A, b, ids, hosts


def _solve_equalities(M, rhs):
    """Exact Gauss-Jordan solve of M x = rhs; None if inconsistent."""
    n = len(rhs)
    if not M:
        return []
    cols = len(M[0])
    aug = [list(map(F, M[i])) + [F(rhs[i])] for i in range(n)]
    row = 0
    pivots = []
    for col in range(cols):
        pivot = next((i for i in range(row, n) if aug[i][col] != 0), None)
        if pivot is None:
            continue
        aug[row], aug[pivot] = aug[pivot], aug[row]
        p = aug[row][col]
        aug[row] = [x / p for x in aug[row]]
        for i in range(n):
            if i != row and aug[i][col] != 0:
                f = aug[i][col]
                aug[i] = [x - f * z for x, z in zip(aug[i], aug[row])]
        pivots.append((row, col))
        row += 1
    for i in range(n):
        if all(aug[i][j] == 0 for j in range(cols)) and aug[i][-1] != 0:
            return None
    out = [F(0)] * cols
    for r, col in pivots:
        out[col] = aug[r][-1]
    return out


def enumerate_vertices(d):
    """Enumerate polytope vertices as intersections of tight hyperplanes.

    A vertex makes at least k independent constraints tight (k = number of
    hosts).  We enumerate every k-subset of constraints, solve the resulting
    equality system, and keep points that satisfy every half-space.  This is
    exponential but the control materials are tiny; it uses none of the
    analyzer's machinery.
    """
    import itertools
    A, b, _, hosts = _halfspaces(d)
    k = len(hosts)
    vertices = []
    seen = set()
    for subset in itertools.combinations(range(len(A)), k):
        M = [A[i] for i in subset]
        rhs = [b[i] for i in subset]
        point = _solve_equalities(M, rhs)
        if point is None:
            continue
        if all(sum(A[i][j] * point[j] for j in range(k)) <= b[i]
               for i in range(len(A))):
            key = tuple(point)
            if key not in seen:
                seen.add(key)
                vertices.append(dict(zip(hosts, point)))
    if not vertices:
        raise AssertionError("reference enumeration found no feasible vertex")
    return vertices


def enumerate_feasible_points(d, samples=17):
    """Independent sampling of the whole feasible polytope, including edges
    and interior points.  For each host it linearly interpolates its sync
    interval at deterministic rational fractions, then retains only points
    that satisfy every derived half-space.  This catches possible-maximum
    services (like uncertain's gamma) whose extremum is not at a vertex.
    """
    A, b, _, hosts = _halfspaces(d)
    k = len(hosts)
    grid = [F(t, samples - 1) for t in range(samples)]
    import itertools
    points = []
    for combo in itertools.product(grid, repeat=k):
        off = {}
        for j, h in enumerate(hosts):
            lo = source.rat(d["offsets"][h][0])
            hi = source.rat(d["offsets"][h][1])
            off[h] = lo + (hi - lo) * combo[j]
        if all(sum(A[i][j] * off[hosts[j]] for j in range(k)) <= b[i]
               for i in range(len(A))):
            points.append(off)
    if not points:
        raise AssertionError("reference sampling found no feasible point")
    return points


def reference_summary(d):
    """Independent bounds (vertices) and maximum verdicts (full polytope
    sampling so interior extrema are included)."""
    vertices = enumerate_vertices(d)
    points = enumerate_feasible_points(d)
    _, totals = source.linear(d)

    def q(expr, off):
        const, coeff = expr
        return const + sum(F(coeff.get(h, 0)) * off[h] for h in coeff)

    summary = {}
    for s, expr in totals.items():
        vvals = [q(expr, v) for v in vertices]
        lo, hi = min(vvals), max(vvals)
        # Possible maximum: any feasible point where this service is no
        # smaller than every other service (interior points included).
        possible = any(
            all(q(expr, v) >= q(totals[rr], v) for rr in totals)
            for v in points)
        # Always-unique: strictly above every rival at every sampled point.
        always_unique = all(
            all(q(expr, v) > q(totals[r], v) for r in totals if r != s)
            for v in points)
        summary[s] = {"lo": lo, "hi": hi, "possible": possible,
                      "always_unique": always_unique}
    return summary


# ---------------------------------------------------------------------------
# Material transformations on temporary copies
# ---------------------------------------------------------------------------

def _run_analyzer(material_path):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.json"
        subprocess.run([sys.executable, "analysis.py", str(material_path),
                        str(out)], check=True)
        d = source.load(str(material_path))
        result = json.loads(out.read_text(encoding="utf-8"))
        return d, audit.verify(d, result), result


def _copy_material(src_path, dst_dir):
    src_path = Path(src_path)
    d = json.loads(src_path.read_text(encoding="utf-8"))
    csv_name = d["event_file"]
    (Path(dst_dir) / csv_name).write_text(
        (src_path.parent / csv_name).read_text(encoding="utf-8"),
        encoding="utf-8")
    new_path = Path(dst_dir) / src_path.name
    new_path.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return new_path, d


def check_vertex_cross_check():
    for name in ("certain", "uncertain"):
        d, audited, _ = _run_analyzer("data/" + name + ".json")
        ref = reference_summary(d)
        for s, item in audited["services"].items():
            assert source.rat(item["lo"]) == ref[s]["lo"], (name, s, "lo")
            assert source.rat(item["hi"]) == ref[s]["hi"], (name, s, "hi")
            assert item["possible_maximum"] == ref[s]["possible"], name
            assert item["always_unique"] == ref[s]["always_unique"], name
    print("VERTEX_CROSS_CHECK_OK")


def check_time_shift_invariance():
    """Adding one integer to every local_ms must not change any conclusion."""
    with tempfile.TemporaryDirectory() as tmp:
        path, d = _copy_material("data/incident.json", tmp)
        csv_path = Path(tmp) / d["event_file"]
        lines = csv_path.read_text(encoding="utf-8").splitlines()
        head, body = lines[0], lines[1:]
        shifted = [head]
        for line in body:
            parts = line.split(",")
            parts[3] = str(int(parts[3]) + 1)
            shifted.append(",".join(parts))
        csv_path.write_text("\n".join(shifted) + "\n", encoding="utf-8")
        _, audited, _ = _run_analyzer(path)
        base = json.loads(Path("result.json").read_text(encoding="utf-8"))
        base_summary = audit.verify(source.load("data/incident.json"), base)
        assert audited == base_summary, "time-shift changed conclusions"
    print("TIME_SHIFT_INVARIANCE_OK")


def check_row_order_invariance():
    """Permuting CSV rows must leave every conclusion unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        path, d = _copy_material("data/incident.json", tmp)
        csv_path = Path(tmp) / d["event_file"]
        lines = csv_path.read_text(encoding="utf-8").splitlines()
        head, body = lines[0], lines[1:]
        perm = body[::-1]  # deterministic reverse permutation
        csv_path.write_text("\n".join([head] + perm) + "\n",
                            encoding="utf-8")
        _, audited, _ = _run_analyzer(path)
        base = audit.verify(source.load("data/incident.json"),
                            json.loads(Path("result.json").read_text()))
        assert audited == base, "row permutation changed conclusions"
    print("ROW_ORDER_INVARIANCE_OK")


def check_sync_perturbation():
    """Tightening certain's B upper bound to 3 must give the contract values."""
    with tempfile.TemporaryDirectory() as tmp:
        path, d = _copy_material("data/certain.json", tmp)
        d["offsets"]["B"] = [0, 3]
        path.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        d2, audited, result = _run_analyzer(path)
        alpha = audited["services"]["alpha"]
        beta = audited["services"]["beta"]
        assert source.rat(alpha["lo"]) == F(3), alpha
        assert source.rat(alpha["hi"]) == F(6), alpha
        assert source.rat(beta["lo"]) == F(1), beta
        assert source.rat(beta["hi"]) == F(4), beta
        assert alpha["always_unique"], "alpha must stay the unique maximum"
        # Independent vertex enumeration must agree on the perturbed copy.
        ref = reference_summary(d2)
        for s, item in audited["services"].items():
            assert source.rat(item["lo"]) == ref[s]["lo"]
            assert source.rat(item["hi"]) == ref[s]["hi"]
            assert item["always_unique"] == ref[s]["always_unique"]
    print("SYNC_PERTURBATION_OK")


def main():
    check_vertex_cross_check()
    check_time_shift_invariance()
    check_row_order_invariance()
    check_sync_perturbation()
    print(json.dumps({"validate": "all independent checks passed"},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
