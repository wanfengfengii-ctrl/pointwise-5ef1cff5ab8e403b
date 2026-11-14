import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import audit
import source


def rejected(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError("bad evidence accepted")


def control_result():
    low = [["sync:A:lo", "1"], ["sync:B:hi", "1"]]
    high = [["sync:A:hi", "1"], ["sync:B:lo", "1"]]
    w0, w4 = {"A": "0", "B": "0"}, {"A": "0", "B": "4"}
    return {"status": "ok", "witness": w0, "services": {
        "alpha": {"lo": "2", "hi": "6", "lo_witness": w4, "hi_witness": w0,
                  "lo_proof": low, "hi_proof": high,
                  "maximum": {"possible": True, "witness": w0},
                  "dominance": {"always_unique": True,
                                "margins": {"beta": {"value": "2", "proof": []}}}},
        "beta": {"lo": "0", "hi": "4", "lo_witness": w4, "hi_witness": w0,
                 "lo_proof": low, "hi_proof": high,
                 "maximum": {"possible": False, "proof": [["max:beta:alpha", "1"]]},
                 "dominance": {"always_unique": False, "witness": w0, "rival": "alpha"}}
    }}


def normal():
    d = source.load()
    assert len(d["offsets"]) == 6 and len(d["events"]) == 26
    assert len(d["queues"]) == 9 and len(d["links"]) == 4
    assert source.witness(d, {"G": 0, "B": 0, "W1": 0, "W2": 0, "W3": 0, "R": -2}) == {
        "ingest": 24, "enrich": 17, "archive": 9}
    certain = source.load("data/certain.json")
    good = control_result()
    checked = audit.verify(certain, good)
    assert checked["services"]["alpha"]["always_unique"]
    assert not checked["services"]["beta"]["possible_maximum"]
    bad = copy.deepcopy(good)
    bad["services"]["alpha"]["lo"] = "3"
    rejected(lambda: audit.verify(certain, bad))
    bad = copy.deepcopy(good)
    bad["services"]["alpha"]["hi_proof"][0][1] = "-1"
    rejected(lambda: audit.verify(certain, bad))
    rejected(lambda: source.witness(d, {"G": 0}))
    uncertain = source.load("data/uncertain.json")
    assert source.witness(uncertain, {"A": 0, "B": 0}) == {"alpha": 6, "beta": 2, "gamma": 5}
    assert source.witness(uncertain, {"A": 0, "B": 4}) == {"alpha": 2, "beta": 6, "gamma": 5}
    assert source.witness(uncertain, {"A": 0, "B": 2}) == {"alpha": 4, "beta": 4, "gamma": 5}
    conflict = source.load("data/conflict.json")
    audit.verify(conflict, {"status": "infeasible", "proof": [["queue:u", "1"], ["queue:v", "1"]]})
    for ident, offset in (("u", 0), ("v", 1)):
        part = copy.deepcopy(conflict)
        part["queues"] = [q for q in part["queues"] if q["id"] == ident]
        source.witness(part, {"A": 0, "B": offset})
    rejected(lambda: source.witness(conflict, {"A": 0, "B": 0}))
    rejected(lambda: audit.verify(conflict, {"status": "infeasible", "proof": []}))
    print("NORMAL_REGRESSION_OK")
    print(json.dumps({"control": checked, "main_events": 26,
                      "uncertain_example": {"at_B0": [6, 2, 5], "at_B4": [2, 6, 5]},
                      "conflict": "each request feasible; joint contradiction"}, sort_keys=True))


def immutable():
    paths = [Path("source.py"), Path("audit.py"), Path("checks.py"), Path("CONTRACT.md")]
    paths += sorted(Path("data").glob("*"))
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def report(validate=False):
    before = immutable()
    summaries = {}
    for name in ("incident", "certain", "uncertain", "conflict"):
        output = "result.json" if name == "incident" else "result-" + name + ".json"
        subprocess.run([sys.executable, "analysis.py", "data/" + name + ".json", output], check=True)
        result = json.loads(Path(output).read_text(encoding="utf-8"))
        summaries[name] = audit.verify(source.load("data/" + name + ".json"), result)
    assert summaries["certain"]["services"]["alpha"]["always_unique"]
    assert not summaries["certain"]["services"]["beta"]["possible_maximum"]
    assert all(x["possible_maximum"] and not x["always_unique"]
               for x in summaries["uncertain"]["services"].values())
    assert summaries["conflict"] == {"status": "infeasible"}
    assert Path("REPORT.md").read_text(encoding="utf-8").strip()
    if validate:
        subprocess.run([sys.executable, "validate.py"], check=True)
    assert before == immutable(), "baseline evidence changed"
    print("REPORT_EVIDENCE_OK")
    print(json.dumps(summaries, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
    if mode == "normal":
        normal()
    elif mode in ("report", "demo"):
        normal()
        report(validate=(mode == "report"))
    else:
        raise SystemExit("use normal, report or demo")
