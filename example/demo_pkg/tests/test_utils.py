"""Tests for demo package utility functions."""

from demo_pkg import add, is_even


def test_add_numbers() -> None:
    """Test adding integer and floating-point numbers."""
    assert add(2, 3) == 5
    assert add(-1, 1) == 0
    assert add(1.5, 2.5) == 4.0


def test_is_even() -> None:
    """Test checking whether integers are even."""
    assert is_even(2) is True
    assert is_even(3) is False
    assert is_even(0) is True
