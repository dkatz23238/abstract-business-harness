from bizharness.bridge import is_data_event


def test_kind_data_and_legacy_gamma():
    tools = frozenset({"get_post"})
    assert is_data_event({"kind": "data", "tool": "x"}, tools)
    assert is_data_event({"kind": "gamma", "tool": "x"}, tools)
    assert not is_data_event({"kind": "control", "tool": "get_post"}, tools)
    assert is_data_event({"tool": "get_post"}, tools)
    assert not is_data_event({"tool": "write_file"}, tools)
