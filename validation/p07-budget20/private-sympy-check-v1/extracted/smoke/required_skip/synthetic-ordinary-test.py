from sympy.testing.pytest import skip

def test_native_identity():
    assert 1 + 1 == 2

def test_native_probe():
    skip('synthetic required ordinary obligation')
