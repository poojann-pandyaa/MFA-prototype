from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict

import numpy as np
from sqlalchemy import select

from amfa import config
from amfa.common import crypto
from amfa.factors.base import store_for
from amfa.factors.ueba.features import keystroke as ks
from amfa.factors.ueba.scoring.combine import CalibrationSet, DimensionCalibrator

MIN_USERS = 10
MIN_PER_CLASS = 50


def _samples() -> tuple[dict, dict]:
    st = store_for("ueba")
    templates, probes = {}, defaultdict(list)
    with st.engine.connect() as c:
        for r in c.execute(select(st.templates)).mappings():
            t = json.loads(crypto.open_(r["sealed"], f"ueba|{r['tenant_id']}|{r['user_id']}", "template:ueba"))
            if t.get("keystroke"):
                templates[(r["tenant_id"], r["user_id"])] = t["keystroke"]
        for r in c.execute(select(st.probes)).mappings():
            if r["vector"]:
                v = np.frombuffer(crypto.open_(r["vector"].decode(), f"{r['tenant_id']}|{r['user_id']}",
                                               "probe:ueba"), dtype=np.float32)
                probes[(r["tenant_id"], r["user_id"])].append(v)
    return templates, probes


def _as_sample(vec: np.ndarray, tpl: dict) -> dict | None:
    nd, nf = tpl["n_dwell"], tpl["n_flight"]
    if len(vec) < nd + nf:
        return None
    return {"dwell": vec[:nd], "flight": vec[nd:nd + nf]}


def run(out_path: str) -> dict:
    templates, probes = _samples()
    genuine, impostor = [], []
    users = [u for u in templates if probes.get(u)]
    for u in users:
        tpl = templates[u]
        for v in probes[u]:
            s = _as_sample(v, tpl)
            if s is not None:
                genuine.append(ks.distance(tpl, s))
        for other in users:
            if other == u:
                continue
            for v in probes[other][:5]:
                s = _as_sample(v, tpl)
                if s is not None:
                    impostor.append(ks.distance(tpl, s))
    report = {"users": len(users), "genuine": len(genuine), "impostor": len(impostor)}
    if len(users) < MIN_USERS or min(len(genuine), len(impostor)) < MIN_PER_CLASS:
        report["result"] = (f"not enough real data yet: need >= {MIN_USERS} customers and >= "
                            f"{MIN_PER_CLASS} samples per class")
        return report
    current = CalibrationSet.load(config.get().models_dir / "ueba").calibrators.get("keystroke")
    fitted = DimensionCalibrator.fit(genuine, impostor)
    report.update({"eerCurrent(simulated fit)": round(current.eer, 4) if current else None,
                   "eerRefit(real data)": round(fitted.eer, 4),
                   "candidate": out_path, "result": "candidate written; promote through a reviewed rebuild"})
    with open(out_path, "w") as f:
        json.dump({"keystroke": asdict(fitted), "_report": report}, f, indent=2)
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    print(json.dumps(run(ap.parse_args().out), indent=2))
