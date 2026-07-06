"""Simple greeting example."""


def hello(name: str) -> str:
    """Return a greeting for the given name.

    Args:
        name: The name to greet.

    Returns:
        A greeting message.
    """
    return "Hello, " + name


def main() -> None:
    """Print a greeting for the world."""
    print(hello("World"))


if __name__ == "__main__":
    main()
