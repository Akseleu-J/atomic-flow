from sympy.p07_probe import value

def test_native_identity():
    assert 1 + 1 == 2

def test_native_probe():
    assert value == 42
