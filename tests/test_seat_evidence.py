"""fusion/seat_evidence: chance level per seat, exact tail probability, ranking over repeated uploads.
All event timings below are TEST FIXTURES, not measurements."""
from math import comb

import numpy as np

from common.events import BehaviorEvent, NetEvent
from fusion.seat_evidence import SeatEvidence, poisson_binomial_sf, union_length

DEV = "02:00:00:00:00:01"


def test_union_length_merges_and_clips():
    assert union_length([(0, 2), (1, 3), (5, 6)], 0, 10) == 4
    assert union_length([(-5, 1), (9, 20)], 0, 10) == 2
    assert union_length([], 0, 10) == 0


def test_poisson_binomial_equals_binomial_for_equal_p():
    p, n = 0.3, 8
    for k in range(n + 1):
        ref = sum(comb(n, j) * p**j * (1 - p) ** (n - j) for j in range(k, n + 1))
        assert np.isclose(poisson_binomial_sf([p] * n, k), ref)


def _session(rng, n_seats=6, n_uploads=10, interval=90.0):
    ev = SeatEvidence([f"S{i}" for i in range(n_seats)], pre_s=0.0, post_s=0.0)
    t_end = interval * (n_uploads + 1)
    for t in np.arange(0, t_end, 1 / 15):          # camera frames observed the whole time
        ev.observe(float(t))
    ups = [interval * (i + 1) for i in range(n_uploads)]
    for t in ups:
        ev.add_net(NetEvent(t, t + 0.5, DEV, 1.0, "rule"))
    # cheater S0: head down around every upload only
    for t in ups:
        ev.add_behavior(BehaviorEvent(t - 2.0, t + 1.0, "S0", "head_down_hold", 1.0))
    # normal seats: head down (writing) about 40 % of the time in 5-20 s bouts, independent of uploads
    for s in range(1, n_seats):
        t = float(rng.uniform(0, 10))
        while t < t_end:
            d = float(rng.uniform(5, 20))
            ev.add_behavior(BehaviorEvent(t, t + d, f"S{s}", "head_down_hold", 1.0))
            t += d + float(rng.uniform(7, 30))
    return ev


def test_cheater_ranks_first_and_normals_stay_plausible():
    ev = _session(np.random.default_rng(0))
    r = ev.ranking(DEV)
    assert r[0].seat == "S0" and r[0].matches == 10 and r[0].p_adj < 1e-6
    normals = [x for x in r if x.seat != "S0"]
    assert all(x.p_chance_mean > 0.2 for x in normals)          # writing seats coincide often by chance
    assert all(x.p_adj > 1e-3 for x in normals)


def test_identification_needs_several_uploads():
    ev = _session(np.random.default_rng(1))
    hist = ev.history(DEV)
    assert len(hist) == 10
    assert hist[0][0]["p_adj"] > hist[-1][0]["p_adj"]            # evidence grows with uploads


def test_always_in_behavior_seat_gains_nothing():
    ev = SeatEvidence(["A"], 0.0, 0.0)
    ev.observe(0.0)
    ev.observe(1000.0)
    ev.add_behavior(BehaviorEvent(0.0, 1000.0, "A", "head_down_hold", 1.0))
    for t in (100, 300, 500, 700):
        ev.add_net(NetEvent(t, t + 1, DEV, 1.0, "rule"))
    r = ev.ranking(DEV)[0]
    assert r.matches == 4 and r.p_value == 1.0


def test_alpha_marks_identified_only_when_significant():
    ev = _session(np.random.default_rng(2))
    ev.alpha = 0.05
    assert ev.ranking(DEV)[0].identified
    assert not any(x.identified for x in ev.ranking(DEV, upto=1))
