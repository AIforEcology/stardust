import pytest

from stardust_core.sji import SJI_PATTERN, esc_of, new_sji


def test_sji_shape_and_esc():
    sji = new_sji("HYD")
    assert SJI_PATTERN.match(sji)
    assert esc_of(sji) == "HYD"
    assert len(sji) == 35  # GUID shape, with a 3-letter segment where a GUID has 4 hex digits


def test_sji_is_unique():
    assert len({new_sji("UNK") for _ in range(1000)}) == 1000


@pytest.mark.parametrize("bad", ["hyd", "HY", "HYDR", "H1D"])
def test_sji_rejects_bad_esc(bad):
    with pytest.raises(ValueError):
        new_sji(bad)
