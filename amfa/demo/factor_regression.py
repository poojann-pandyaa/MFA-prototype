"""Behaviour (typing and swipe) check against the running UEBA service.

    python -m amfa.demo.factor_regression --tenant demo-bank

Each seeded customer with a typing template is checked with
  * new samples of their own pace: should pass (limit: at most 20% rejected);
  * a typist at twice and three times their pace, and a bot with perfectly even keystrokes: should fail
    (limit: at most 10% accepted);
  * the other customers, whose pace is close: reported, not limited. Pace alone cannot tell two people with a similar
    rhythm apart, which is why typing is a quiet supporting check, not an identity proof.
The samples are synthetic, and the touch thresholds are still provisional (borrowed from the keyboard ones), so this
is a regression of the pipeline, not a measure of real-world accuracy.
"""
from __future__ import annotations

import argparse
import random
import sys
import time

from sqlalchemy import select

from amfa.core import db, factors
from amfa.demo import personas as P
from amfa.demo.synth import tempo_of, trace, typing_samples

MAX_FRR = 0.20      # of the owner's own attempts rejected
MAX_FAR = 0.10      # of the clearly different typists and bots accepted


def verify(tenant: str, cid: str, phrase: dict, sample: list, tr: list, n: int) -> str:
    res = factors.verify("UEBA", tenant=tenant, user=cid, session=f"sess_reg{n}", txn=f"txn_reg{n}",
                         challenge={"phrase": phrase["phrase"], "input": "touch", "shape": phrase["shape"]},
                         payload={"keystrokeSample": sample, "keystrokeInput": "touch", "shapeTrace": tr, "shapePointerType": "touch"},
                         pad_mode="advisory")
    return res.status


def bot(phrase: str) -> list:
    return [{"key": c, "down": 1000 + i * 100.0, "up": 1003 + i * 100.0} for i, c in enumerate(phrase)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="demo-bank")
    ap.add_argument("--attempts", type=int, default=12)
    a = ap.parse_args(argv)
    db.migrate()
    with db.session() as s:
        tenant = s.execute(select(db.Tenant).where(db.Tenant.slug == a.tenant)).scalars().one()
    phrase = factors.ueba_phrase()
    if phrase is None:
        print("the behaviour service is not reachable")
        return 2
    typers = [p for p in P.PERSONAS if p.typing]
    run = f"{time.time():.0f}"     # new samples every run: the service refuses a sample it has already seen
    print(f"{'customer':9s} {'own pace':>10s} {'2x slower':>10s} {'3x slower':>10s} {'bot':>6s} {'similar pace':>13s}   passed of attempts")
    bad = 0
    for p in typers:
        cid, base, k = f"cu_demo_{p.username}", tempo_of(p.username), a.attempts
        n = [0]

        def go(rng_key: str, tempo: float, i: int):
            n[0] += 1
            return verify(tenant.id, cid, phrase, typing_samples(random.Random(rng_key), phrase["phrase"], 1, tempo)[0],
                          trace(random.Random(rng_key + "t"), phrase["shape"]), abs(hash((run, p.username, rng_key, i))) % 10**9)

        own = [go(f"{run}-{p.username}-g{i}", base, i) for i in range(k)]
        slow2 = [go(f"{run}-{p.username}-s2{i}", base * 2, i) for i in range(k)]
        slow3 = [go(f"{run}-{p.username}-s3{i}", base * 3, i) for i in range(k)]
        robots = [verify(tenant.id, cid, phrase, bot(phrase["phrase"]), trace(random.Random(f"{run}b{i}"), phrase["shape"]), abs(hash((run, p.username, 'bot', i))) % 10**9) for i in range(k)]
        similar = [go(f"{run}-{q.username}-x{i}", tempo_of(q.username), i) for q in typers if q.username != p.username for i in range(3)]
        passed = lambda xs: xs.count("PASSED")
        frr = 1 - passed(own) / len(own)
        far = (passed(slow2) + passed(slow3) + passed(robots)) / (len(slow2) + len(slow3) + len(robots))
        flag = "" if frr <= MAX_FRR and far <= MAX_FAR else "   <-- outside the limits"
        bad += bool(flag)
        print(f"{p.username:9s} {passed(own):>6d}/{len(own):<3d} {passed(slow2):>6d}/{len(slow2):<3d} {passed(slow3):>6d}/{len(slow3):<3d} {passed(robots):>3d}/{len(robots):<2d} {passed(similar):>8d}/{len(similar):<4d} "
              f"  FRR {frr:.0%}  FAR(clearly different) {far:.0%}{flag}")
    print(f"\nlimits: own attempts rejected at most {MAX_FRR:.0%}; clearly different typists and bots accepted at most {MAX_FAR:.0%}.")
    print("similar-pace typists are not limited: pace alone cannot tell them apart.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
