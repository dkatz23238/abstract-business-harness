from bizharness.bridge import is_data_event


def test_kind_data_and_control():
    tools = frozenset({"get_post"})
    assert is_data_event({"kind": "data", "tool": "x"}, tools)
    assert not is_data_event({"kind": "control", "tool": "get_post"}, tools)
    assert is_data_event({"tool": "get_post"}, tools)
    assert not is_data_event({"tool": "write_file"}, tools)
    # Unknown kinds (not data|control) fall through to the tool name, so
    # logs copied from an older deployment still count.
    assert is_data_event({"kind": "legacy", "tool": "get_post"}, tools)
    assert not is_data_event({"kind": "legacy", "tool": "write_file"}, tools)
