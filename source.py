import csv
import json
import sys
from fractions import Fraction as F
from pathlib import Path


def rat(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, F)):
        raise ValueError("exact rational required")
    return F(value)


def load(path="data/incident.json"):
    path = Path(path)
    d = json.loads(path.read_text(encoding="utf-8"))
    d["events"] = {}
    for line, row in enumerate(csv.DictReader(
            (path.parent / d["event_file"]).read_text(encoding="utf-8").splitlines()), 2):
        ident = row["event"]
        if ident in d["events"] or row["host"] not in d["offsets"]:
            raise ValueError("duplicate event or unknown host")
        if row["kind"] not in ("enqueue", "start", "send", "receive"):
            raise ValueError("bad event kind")
        row["local_ms"] = int(row["local_ms"])
        row["location"] = str(path.parent / d["event_file"]) + ":" + str(line)
        d["events"][ident] = row
    if d["offsets"].get(d["reference"]) != [0, 0]:
        raise ValueError("reference must have zero offset")
    for lo, hi in d["offsets"].values():
        if rat(lo) > rat(hi):
            raise ValueError("reversed sync interval")
    seen, covered = set(), set()
    for q in d["queues"]:
        if q["id"] in seen:
            raise ValueError("duplicate queue")
        seen.add(q["id"])
        a, b = (d["events"][q[k]] for k in ("enqueue", "start"))
        if (a["kind"], b["kind"]) != ("enqueue", "start"):
            raise ValueError("bad queue endpoints")
        if a["request"] != b["request"] or a["service"] != b["service"]:
            raise ValueError("queue identity mismatch")
        if a["request"] not in d["requests"] or a["service"] not in d["services"]:
            raise ValueError("queue outside target set")
        if q["enqueue"] in covered or q["start"] in covered:
            raise ValueError("reused queue event")
        covered.update((q["enqueue"], q["start"]))
    for e in d["events"].values():
        if e["kind"] in ("enqueue", "start") and e["event"] not in covered:
            raise ValueError("unpaired queue event")
    seen = set()
    for link in d["links"]:
        if link["id"] in seen:
            raise ValueError("duplicate link")
        seen.add(link["id"])
        a, b = (d["events"][link[k]] for k in ("send", "receive"))
        if (a["kind"], b["kind"]) != ("send", "receive") or a["request"] != b["request"]:
            raise ValueError("bad network pair")
    return d


def linear(d):
    """Return named a*x<=b rows and service (constant, coefficients). No solving."""
    rows, totals = {}, {s: (F(0), {}) for s in d["services"]}

    def add(name, a, b):
        if name in rows:
            raise ValueError("duplicate row")
        rows[name] = ({k: rat(v) for k, v in a.items() if v}, rat(b))

    def delta(a, b):
        out = {}
        for h, c in ((a, 1), (b, -1)):
            out[h] = out.get(h, F(0)) + c
        return {h: c for h, c in out.items() if c}

    for h, (lo, hi) in d["offsets"].items():
        add("sync:" + h + ":hi", {h: 1}, hi)
        add("sync:" + h + ":lo", {h: -1}, -rat(lo))
    for q in d["queues"]:
        a, b = (d["events"][q[k]] for k in ("enqueue", "start"))
        raw = b["local_ms"] - a["local_ms"]
        coeff = delta(a["host"], b["host"])
        add("queue:" + q["id"], {h: -c for h, c in coeff.items()}, raw)
        const, total = totals[a["service"]]
        total = dict(total)
        for h, c in coeff.items():
            total[h] = total.get(h, F(0)) + c
        totals[a["service"]] = (const + raw, {h: c for h, c in total.items() if c})
    for link in d["links"]:
        a, b = (d["events"][link[k]] for k in ("send", "receive"))
        add("link:" + link["id"], delta(b["host"], a["host"]),
            b["local_ms"] - a["local_ms"])
    return rows, totals


def value(expr, offsets):
    const, coeff = expr
    return const + sum(c * offsets[h] for h, c in coeff.items())


def witness(d, supplied):
    if set(supplied) != set(d["offsets"]):
        raise ValueError("incomplete clock assignment")
    offsets = {h: rat(v) for h, v in supplied.items()}
    rows, totals = linear(d)
    for name, (a, b) in rows.items():
        if sum(c * offsets[h] for h, c in a.items()) > b:
            raise ValueError("violated " + name)
    return {s: value(expr, offsets) for s, expr in totals.items()}


def main():
    d = load(sys.argv[1] if len(sys.argv) > 1 else "data/incident.json")
    print(json.dumps({
        "requests": d["requests"], "services": d["services"],
        "hosts": list(d["offsets"]), "event_count": len(d["events"]),
        "queues": [{"id": q["id"],
                    "enqueue": d["events"][q["enqueue"]],
                    "start": d["events"][q["start"]]} for q in d["queues"]]
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
