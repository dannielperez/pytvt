"""Channel-number normalization at the XML-to-NetSDK boundary."""

import pytest

from pytvt import netsdk_channel_index


@pytest.mark.parametrize(
    ("display_channel", "expected_index"),
    [(1, 0), (2, 1), (7, 6), (64, 63)],
)
def test_netsdk_channel_index_converts_one_based_display_numbers(
    display_channel,
    expected_index,
):
    assert netsdk_channel_index(display_channel) == expected_index


@pytest.mark.parametrize("value", [0, -1])
def test_netsdk_channel_index_rejects_non_positive_numbers(value):
    with pytest.raises(ValueError, match="1 or greater"):
        netsdk_channel_index(value)


@pytest.mark.parametrize("value", [True, "1", 1.0, None])
def test_netsdk_channel_index_rejects_non_integer_values(value):
    with pytest.raises(TypeError, match="must be an integer"):
        netsdk_channel_index(value)
