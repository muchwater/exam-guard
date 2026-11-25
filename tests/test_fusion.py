from common.events import BehaviorEvent, NetEvent
from fusion.time_window import TimeWindowFusion


def b(t0, t1, seat="S1", label="head_down_hold"):
    return BehaviorEvent(t0, t1, seat, label, 1.0)


def n(t0, t1):
    return NetEvent(t0, t1, "02:00:00:00:00:01", 1.0, "rule")


def test_alert_when_upload_follows_behavior_within_post():
    f = TimeWindowFusion(pre_s=1.0, post_s=3.0)
    assert f.add_behavior(b(10, 12)) == []
    al = f.add_net(n(14, 15))
    assert len(al) == 1 and al[0].seat == "S1"


def test_no_alert_outside_window_and_order_independent():
    f = TimeWindowFusion(pre_s=1.0, post_s=3.0)
    f.add_net(n(16, 17))
    assert f.add_behavior(b(10, 12)) == []
    assert len(f.add_behavior(b(13.5, 14))) == 1          # net arrived first, behavior later


def test_each_pair_once_and_multiple_seats():
    f = TimeWindowFusion(pre_s=1.0, post_s=3.0)
    f.add_behavior(b(10, 12, "S1"))
    f.add_behavior(b(10, 12, "S2"))
    assert {a.seat for a in f.add_net(n(13, 14))} == {"S1", "S2"}
    assert len(f.add_net(n(13, 14))) == 2                 # a new upload event is a new pair


def test_label_filter():
    f = TimeWindowFusion(pre_s=1.0, post_s=3.0, behavior_labels={"glasses_touch"})
    f.add_behavior(b(10, 12, label="head_down_hold"))
    assert f.add_net(n(12, 13)) == []
