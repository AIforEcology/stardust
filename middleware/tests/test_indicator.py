from stardust_core.indicator import aggregate_code, cost_tier, impact_grade, indicator_code, volume_class


def test_bands(cfg):
    ind = cfg["indicator"]
    assert impact_grade(0.01, 1000, ind) == "A"
    assert impact_grade(0.15, 1000, ind) == "C"
    assert impact_grade(5.0, 1000, ind) == "F"
    assert impact_grade(0.03, None, ind) == "B"  # no-token table
    assert cost_tier(0.0005, ind) == "1"
    assert cost_tier(10.0, ind) == "5"
    assert cost_tier(None, ind) == "X"
    assert volume_class(100, ind) == "XS"
    assert volume_class(5000, ind) == "M"
    assert volume_class(10**6, ind) == "XL"
    assert volume_class(None, ind) == "XS"


def test_code_format(cfg):
    assert indicator_code(0.75, 0.02, 5000, cfg["indicator"]) == "C3-M"


def test_aggregate(cfg):
    ind = cfg["indicator"]
    assert aggregate_code([], [], [], ind) is None
    # Pooled: 0.3 g over 2000 tokens = 0.15 g/1k → C; mean cost 0.02 → 3; mean 1000 tokens → S
    assert aggregate_code([0.1, 0.2], [0.01, 0.03], [1000, 1000], ind) == "C3-S"
    # Unknown costs are ignored in the mean, and all-unknown gives X
    assert aggregate_code([0.1], [None], [1000], ind) == "CX-S"
