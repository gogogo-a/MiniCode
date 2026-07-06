from hooks import HookOutcome, HookResult
from permission import PermissionBehavior


def invalid_return(*_args):
    return "invalid"


def update_command_to_pwd(*_args):
    return HookResult(updated_input={"command": "pwd"})


def update_command_to_ls(*_args):
    return HookResult(updated_input={"command": "ls"})


def add_first_context(*_args):
    return HookResult(additional_context="first context")


def add_second_context(*_args):
    return HookResult(additional_context="second context")


def allow_permission(*_args):
    return HookResult(permission_behavior=PermissionBehavior.ALLOW)


def ask_permission(*_args):
    return HookResult(permission_behavior=PermissionBehavior.ASK)


def deny_permission(*_args):
    return HookResult(permission_behavior=PermissionBehavior.DENY)


def stop_after_tool(*_args):
    return HookResult(prevent_continuation=True, message="Stop after tool.")


def blocking_stop(*_args):
    return HookResult(outcome=HookOutcome.BLOCKING, message="Please provide a final answer.")
