from _pytest.p07_probe import value

def test_identity():
    assert 1 + 1 == 2

def test_probe():
    assert value == 42
