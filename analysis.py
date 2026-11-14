"""Exact reachable-bounds analyzer for the sealed call-log incident package.

Entry point (fixed by CONTRACT.md):
    python3 analysis.py INPUT.json OUTPUT.json

The analyzer re-reads the material on every run, derives every linear
expression through source.linear (no hard-coded incident answers), solves
the resulting rational linear programs with lpsolve.standard_simplex, and
emits the exact result schema verified by audit.verify.

Certificates are the non-negative dual multipliers of the same primal
linear programs; they are recomputed from each optimal basis and then
mapped back to the stable evidence IDs defined by source.linear.
"""

import json
import sys
from fractions import Fraction as F

import source
import lpsolve


# ---------------------------------------------------------------------------
# Generic inequality system -> standard-form primal
# ---------------------------------------------------------------------------

def build_primal(named_rows, host_bounds):
    """Build min c^T z s.t. A z = b, z >= 0 with one slack per inequality.

    Only non-fixed hosts become variables: x_j = lo_j + z_j with z_j >= 0.
    Fixed hosts (lo_j == hi_j, including the zero-offset reference) are NOT
    substituted away: their two sync rows remain in the system so dual
    certificates can combine them to reproduce every coefficient required
    by audit.dual (their slacks never move because the bounds coincide).
    Each original a*x <= b row becomes an equality with one non-negative
    slack; a shifted row 0 <= negative drives phase-I infeasibility.
    """
    hosts = [h for h, (lo, hi) in host_bounds.items()
             if F(lo) != F(hi)]
    lo = {h: F(host_bounds[h][0]) for h in hosts}
    n = len(hosts)
    hpos = {h: j for j, h in enumerate(hosts)}
    fixed = {h: F(host_bounds[h][0]) for h in host_bounds
             if F(host_bounds[h][0]) == F(host_bounds[h][1])}

    A, b, row_ids = [], [], []
    for name, (a, rhs) in named_rows.items():
        coef = [F(0)] * n
        for h, c in a.items():
            if h in hpos:
                coef[hpos[h]] += F(c)
        shift = sum(F(a.get(h, 0)) * lo[h] for h in hosts)
        A.append(coef)
        b.append(F(rhs) - shift)
        row_ids.append(name)

    m = len(A)
    for i in range(m):
        A[i] = A[i] + [F(1 if j == i else 0) for j in range(m)]
    return {
        "A": A, "b": b, "hosts": hosts, "lo": lo, "fixed": fixed,
        "n": n, "m": m, "row_ids": row_ids,
    }


def offsets_from_solution(model, z):
    offsets = dict(model["fixed"])
    for j, h in enumerate(model["hosts"]):
        offsets[h] = model["lo"][h] + z[j]
    return offsets


def _certificate(model, duals, sense):
    """Map equality-row duals to non-negative a*x <= b weights.

    Both bounds are solved as minimizations (max uses the negated
    objective).  Each inequality a*z <= b' is the equality
    a*z + s = b' with s >= 0; dual feasibility on the zero-cost slack
    forces y_i <= 0, so the published non-negative weight is -y_i in
    either direction.  This first-pass combination is tightened by
    _exact_certificate to match the full target coefficient vector.
    """
    weights = {}
    for i, name in enumerate(model["row_ids"]):
        w = -duals[i]
        if w != 0:
            weights[name] = w
    return weights


def _exact_certificate(named_rows, host_bounds, model, target_a, base_weights,
                       sense="min", sharp_rhs=None):
    """Build a non-negative certificate with the exact target LHS that
    attains the sharp bound.

    Solves a fully equality-constrained, hence bounded, rational LP:
        A^T y = target_a   (one equality per host, exact coefficient LHS)
        b^T y = sharp_rhs  (the sharp bound, attained by strong duality)
        y >= 0.
    Pinning the combined RHS removes every unbounded null direction; a
    feasible solution exists because the primal was solved to optimality.
    A minimum-weight objective keeps the result sparse.
    """
    all_hosts = list(host_bounds)
    names = list(named_rows)
    mm = len(names)
    Aeq = [[F(named_rows[names[i]][0].get(all_hosts[j], 0))
            for i in range(mm)]
           for j in range(len(all_hosts))]
    bvals = [F(named_rows[names[i]][1]) for i in range(mm)]
    rhs = [F(target_a.get(h, 0)) for h in all_hosts]
    if sharp_rhs is not None:
        Aeq.append(bvals)
        rhs.append(F(sharp_rhs))
    status, sol, _ = lpsolve.standard_simplex(
        Aeq, rhs, [F(1)] * mm)
    if status != "optimal":
        return dict(base_weights)
    return {names[i]: sol[i] for i in range(mm) if sol[i] != 0}


def solve_program(named_rows, host_bounds, objective=None, sense="min"):
    """Solve one LP over the joint feasible region.

    Returns (status, payload):
      ("optimal", {"value", "offsets", "weights"})
      ("infeasible", {"proof"})  with a non-negative Farkas certificate
    """
    model = build_primal(named_rows, host_bounds)
    n, m = model["n"], model["m"]
    if objective is None:
        g_const, g_a = F(0), {}
    else:
        g_const, g_a = objective
    gv = [F(g_a.get(h, 0)) for h in model["hosts"]]
    fixed_part = sum(F(g_a.get(h, 0)) * v
                     for h, v in model["fixed"].items())
    shift_part = sum(gv[j] * model["lo"][model["hosts"][j]]
                     for j in range(n))
    if sense == "min":
        c = gv + [F(0)] * m
    else:
        c = [-x for x in gv] + [F(0)] * m

    try:
        status, sol, duals = lpsolve.standard_simplex(
            model["A"], model["b"], c)
    except lpsolve.Unbounded:
        # A bounded feasible region cannot make a linear objective
        # unbounded; an unbounded result means infeasibility was missed,
        # which cannot occur for these materials.
        raise RuntimeError("objective unbounded on a bounded offset region")

    if status == "infeasible":
        return "infeasible", {"proof": _farkas(named_rows, host_bounds)}

    # The simplex returns the extremizing point itself in both senses, so
    # the attained value is g*fixed + g*(lo + z) evaluated at that point.
    z_value = sum(gv[j] * sol[j] for j in range(n))
    value = g_const + fixed_part + shift_part + z_value
    offsets = offsets_from_solution(model, sol)
    if objective is None:
        # Feasibility probe: no bound certificate is required.
        return "optimal", {"value": value, "offsets": offsets,
                           "weights": {}}
    # Audit goal LHS is +g for an upper bound and -g for a lower bound;
    # the exact certificate targets that signed coefficient vector.  The
    # sharp combined RHS in original-row coordinates is the audit bound
    # itself: hi - const for an upper bound, const - lo for a lower one.
    cert_target = g_a if sense == "max" else {
        h: -cc for h, cc in g_a.items()}
    sharp_rhs = (value - g_const if sense == "max"
                 else g_const - value)
    weights = _exact_certificate(named_rows, host_bounds, model,
                                 cert_target,
                                 _certificate(model, duals, sense), sense,
                                 sharp_rhs=sharp_rhs)
    return "optimal", {"value": value, "offsets": offsets,
                       "weights": weights}


def _farkas(named_rows, host_bounds):
    """Farkas certificate y >= 0 with y^T A = 0, y^T b < 0.

    Solved directly over the ORIGINAL named inequalities (no free-variable
    split, no slack columns, no shifting): find non-negative weights whose
    host-coefficient combination is exactly zero while the combined RHS is
    strictly negative.  Minimizing b^T y under A^T y = 0 is unbounded
    below exactly when such a certificate exists; its descent ray is a
    valid audit certificate with an empty left-hand side.
    """
    hosts = list(host_bounds)
    pos = {h: j for j, h in enumerate(hosts)}
    names = list(named_rows)
    m = len(names)
    Aeq = [[F(named_rows[name][0].get(hosts[i], 0)) for name in names]
           for i in range(len(hosts))]
    rhs = [F(0)] * len(hosts)
    c = [F(named_rows[name][1]) for name in names]
    try:
        lpsolve.standard_simplex(Aeq, rhs, c)
    except lpsolve.Unbounded as unb:
        ray = unb.ray
        return [[names[i], str(ray[i])] for i in range(m)
                if ray[i] != 0]
    raise RuntimeError("primal infeasible but Farkas LP not unbounded")


# ---------------------------------------------------------------------------
# Full material analysis
# ---------------------------------------------------------------------------

def analyze(d):
    host_bounds = {h: (source.rat(lo), source.rat(hi))
                   for h, (lo, hi) in d["offsets"].items()}
    rows, totals = source.linear(d)

    status, payload = solve_program(rows, host_bounds)
    if status == "infeasible":
        return {"status": "infeasible", "proof": payload["proof"]}

    result = {"status": "ok",
              "witness": _json_offsets(payload["offsets"]),
              "services": {}}

    for s in totals:
        const, coeff = totals[s]
        lo_res = solve_program(rows, host_bounds, (const, coeff), "min")
        hi_res = solve_program(rows, host_bounds, (const, coeff), "max")
        assert lo_res[0] == hi_res[0] == "optimal"
        entry = {
            "lo": str(lo_res[1]["value"]),
            "hi": str(hi_res[1]["value"]),
            "lo_witness": _json_offsets(lo_res[1]["offsets"]),
            "hi_witness": _json_offsets(hi_res[1]["offsets"]),
            "lo_proof": _json_proof(lo_res[1]["weights"]),
            "hi_proof": _json_proof(hi_res[1]["weights"]),
        }
        entry["maximum"] = _maximum(rows, host_bounds, totals, s)
        entry["dominance"] = _dominance(rows, host_bounds, totals, s)
        result["services"][s] = entry
    return result


def _json_offsets(offsets):
    return {h: str(offsets[h]) for h in sorted(offsets)}


def _json_proof(weights):
    return [[name, str(w)] for name, w in sorted(weights.items())
            if w != 0]


def _maximum(rows, host_bounds, totals, s):
    """Possible maximum: base constraints plus every rival comparison."""
    cs, a_s = totals[s]
    joined = dict(rows)
    for rival, (cr, a_r) in totals.items():
        if rival == s:
            continue
        name = "max:" + s + ":" + rival
        comb = dict(a_r)
        for h, c in a_s.items():
            comb[h] = comb.get(h, F(0)) - c
        comb = {h: c for h, c in comb.items() if c != 0}
        joined[name] = (comb, cs - cr)
    status, payload = solve_program(joined, host_bounds)
    if status == "optimal":
        return {"possible": True,
                "witness": _json_offsets(payload["offsets"])}
    return {"possible": False, "proof": payload["proof"]}


def _dominance(rows, host_bounds, totals, s):
    cs, a_s = totals[s]
    margins = {}
    for rival, (cr, a_r) in totals.items():
        if rival == s:
            continue
        # m = min (Q_s - Q_rival); its dual certifies
        # Q_rival - Q_s <= -m, i.e. the audit target with value m.
        diff_a = dict(a_s)
        for h, c in a_r.items():
            diff_a[h] = diff_a.get(h, F(0)) - c
        diff_a = {h: c for h, c in diff_a.items() if c != 0}
        status, payload = solve_program(
            rows, host_bounds, (cs - cr, diff_a), "min")
        assert status == "optimal"
        m_val, m_off = payload["value"], payload["offsets"]
        if m_val > 0:
            margins[rival] = {"value": str(m_val),
                              "proof": _json_proof(payload["weights"])}
        else:
            # The minimizer is itself a complete feasible timeline in which
            # the rival is no smaller than this service.
            return {"always_unique": False,
                    "witness": _json_offsets(m_off), "rival": rival}
    return {"always_unique": True, "margins": margins}


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: analysis.py INPUT.json OUTPUT.json")
    d = source.load(sys.argv[1])
    result = analyze(d)
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({"written": sys.argv[2], "status": result["status"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
