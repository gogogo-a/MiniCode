"""Utility functions for the demo package."""


def add(a: int | float, b: int | float) -> int | float:
    """Return the sum of two numbers."""
    return a + b


def is_even(value: int) -> bool:
    """Return True if value is even."""
    return value % 2 == 0
