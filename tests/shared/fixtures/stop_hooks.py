from hooks import HookOutcome, HookResult


CALL_COUNT = 0


def reset():
    global CALL_COUNT
    CALL_COUNT = 0


def block_once(*_args):
    global CALL_COUNT
    CALL_COUNT += 1
    return HookResult(outcome=HookOutcome.BLOCKING, message="Revise final answer once.")
