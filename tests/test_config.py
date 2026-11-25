import pytest

from common.config import TBDError, iter_tbd, load_config, require


def test_default_config_loads_and_reports_tbd():
    cfg = load_config()
    tbd = set(iter_tbd(cfg))
    assert "net_event.rule.burst_gap_s" in tbd and "fusion.post_s" in tbd
    with pytest.raises(TBDError) as e:
        require(cfg, "fusion.pre_s", "vision.pose.conf_thres")
    assert e.value.keys == ["fusion.pre_s"]


def test_sourced_values_are_numbers():
    cfg = load_config()
    assert require(cfg, "vision.pose.conf_thres") == 0.25
    assert require(cfg, "net_event.window_model.windows_s") == [1, 2, 3, 5, 10]
