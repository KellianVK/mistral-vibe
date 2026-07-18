import operations


def test_add_integers():
    assert operations.add(2, 3) == 5


def test_add_floats():
    assert operations.add(1.5, 2.5) == 4.0


def test_add_negative():
    assert operations.add(-1, 1) == 0


def test_add_zero():
    assert operations.add(0, 0) == 0


def test_add_mixed():
    assert operations.add(-5, 10) == 5
