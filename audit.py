import json
import sys
from fractions import Fraction as F
import source


def need(ok, message):
    if not ok:
        raise ValueError(message)


def combine(a, b, scale=1):
    out = dict(a)
    for h, c in b.items():
        out[h] = out.get(h, F(0)) + scale * c
    return {h: c for h, c in out.items() if c}


def dual(rows, proof, goal=None, bound=None):
    lhs, rhs, used = {}, F(0), set()
    for ident, token in proof:
        need(ident in rows and ident not in used, "unknown or repeated proof row")
        used.add(ident)
        w = source.rat(token)
        need(w >= 0, "negative proof weight")
        a, b = rows[ident]
        lhs = combine(lhs, a, w)
        rhs += w * b
    if goal is None:
        need(not lhs and rhs < 0, "not an infeasibility certificate")
    else:
        need(lhs == goal and rhs <= bound, "not the claimed bound")


def candidate_rows(d, service):
    rows, totals = source.linear(d)
    cs, a = totals[service]
    for rival, (cr, b) in totals.items():
        if rival != service:
            rows["max:" + service + ":" + rival] = (combine(b, a, -1), cs - cr)
    return rows


def verify(d, result):
    rows, totals = source.linear(d)
    if result["status"] == "infeasible":
        need(set(result) == {"status", "proof"}, "partial conflict result")
        dual(rows, result["proof"])
        return {"status": "infeasible"}
    need(result["status"] == "ok", "unknown status")
    need(set(result["services"]) == set(totals), "service coverage")
    source.witness(d, result["witness"])
    summary = {}
    for s, (const, coeff) in totals.items():
        entry = result["services"][s]
        lo, hi = source.rat(entry["lo"]), source.rat(entry["hi"])
        need(lo <= hi, "reversed bounds")
        for end, target in (("lo", lo), ("hi", hi)):
            values = source.witness(d, entry[end + "_witness"])
            need(values[s] == target, "endpoint not attained")
        dual(rows, entry["lo_proof"], {h: -c for h, c in coeff.items()}, const - lo)
        dual(rows, entry["hi_proof"], coeff, hi - const)
        support = entry["maximum"]
        need(type(support["possible"]) is bool, "possible must be boolean")
        if support["possible"]:
            vals = source.witness(d, support["witness"])
            need(all(vals[s] >= v for v in vals.values()), "not a maximum")
        else:
            dual(candidate_rows(d, s), support["proof"])
        dominance = entry["dominance"]
        need(type(dominance["always_unique"]) is bool, "always_unique must be boolean")
        if dominance["always_unique"]:
            need(set(dominance["margins"]) == set(totals) - {s}, "rival coverage")
            for rival, item in dominance["margins"].items():
                margin = source.rat(item["value"])
                need(margin > 0, "strictly positive margin required")
                cr, ar = totals[rival]
                dual(rows, item["proof"], combine(ar, coeff, -1), const - cr - margin)
        else:
            vals = source.witness(d, dominance["witness"])
            rival = dominance["rival"]
            need(rival in totals and rival != s and vals[rival] >= vals[s],
                 "not a counterexample to unique dominance")
        summary[s] = {"lo": str(lo), "hi": str(hi),
                      "possible_maximum": support["possible"],
                      "always_unique": dominance["always_unique"]}
    need(sum(v["always_unique"] for v in summary.values()) <= 1, "two certain maxima")
    return {"status": "ok", "services": summary}


if __name__ == "__main__":
    d = source.load(sys.argv[1])
    result = json.loads(open(sys.argv[2], encoding="utf-8").read())
    print(json.dumps(verify(d, result), ensure_ascii=False, sort_keys=True))
