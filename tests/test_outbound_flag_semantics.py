from src.core.outbound_store import outbound_flag_enabled


def test_outbound_flag_preserves_explicit_zero():
    assert outbound_flag_enabled(0, default=True) is False
    assert outbound_flag_enabled("0", default=True) is False


def test_outbound_flag_preserves_explicit_one():
    assert outbound_flag_enabled(1, default=False) is True
    assert outbound_flag_enabled("1", default=False) is True


def test_outbound_flag_uses_default_only_for_missing_or_invalid_values():
    assert outbound_flag_enabled(None, default=True) is True
    assert outbound_flag_enabled("", default=False) is False
    assert outbound_flag_enabled("invalid", default=True) is True
