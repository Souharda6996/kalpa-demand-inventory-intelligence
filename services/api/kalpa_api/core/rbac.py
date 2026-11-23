"""RBAC matrix (Part D3). Permission -> roles allowed."""

ROLES = ("owner", "planner", "store_manager", "viewer")

PERMISSIONS: dict[str, frozenset[str]] = {
    "manage_org": frozenset({"owner"}),
    "edit_master_data": frozenset({"owner", "planner"}),
    "upload_data": frozenset({"owner", "planner"}),  # also: run training & backtests
    "promote_model": frozenset({"owner", "planner"}),
    "edit_scenarios": frozenset({"owner", "planner", "store_manager"}),
    "create_po": frozenset({"owner", "planner", "store_manager"}),
    "approve_po": frozenset({"owner", "planner"}),
    "receive_po": frozenset({"owner", "planner", "store_manager"}),  # also: POS checkout
    "pos_checkout": frozenset({"owner", "planner", "store_manager"}),
    "view": frozenset(ROLES),
}
# Permissions a store_manager may only exercise on their own stores.
STORE_SCOPED = frozenset({"edit_scenarios", "create_po", "receive_po", "pos_checkout", "view"})


def allowed(role: str, permission: str) -> bool:
    """True if the role holds the permission."""
    return role in PERMISSIONS.get(permission, frozenset())


def permissions_for(role: str) -> list[str]:
    """All permissions of a role (for the /auth/me payload)."""
    return sorted(p for p, roles in PERMISSIONS.items() if role in roles)
