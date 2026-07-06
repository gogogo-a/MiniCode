from hooks import HookResult
from permission import PermissionBehavior


def allow_everything(*_args):
    return HookResult(permission_behavior=PermissionBehavior.ALLOW)
