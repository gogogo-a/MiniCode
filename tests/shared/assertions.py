from __future__ import annotations


def assert_expected(expected: dict, actual: dict, events: list[dict]) -> tuple[bool, str]:
    checks = {
        "permission": actual.get("permission"),
        "tool_executed": actual.get("tool_executed"),
        "updated_input": actual.get("updated_input"),
        "prevent_continuation": actual.get("prevent_continuation"),
    }
    for key, value in expected.items():
        if key == "events":
            names = [event["event"] for event in events]
            missing = [name for name in value if name not in names]
            if missing:
                return False, f"missing events: {missing}; actual events: {names}"
        elif key == "final_contains":
            if str(value) not in str(actual.get("final", "")):
                return False, f"final answer does not contain {value!r}: {actual.get('final', '')!r}"
        elif key in checks:
            if checks[key] != value:
                return False, f"expected {key}={value!r}, got {checks[key]!r}"
    return True, ""
