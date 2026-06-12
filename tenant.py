"""Per-request tenant (business account) context.

The whole app is multi-tenant: every data row belongs to exactly one
tenant, and every query is scoped to the tenant of the logged-in user.
Rather than threading a tenant_id argument through hundreds of data-layer
calls, we keep the current tenant in a context variable that the web layer
sets once per request (from current_user) and the data layer reads.

Naming note: the scoping key is called ``tenant_id`` everywhere to avoid
confusion with the accounting chart-of-accounts ``account_id``.
"""

import contextvars

_current_tenant: "contextvars.ContextVar[int | None]" = contextvars.ContextVar(
    "current_tenant_id", default=None
)


def set_current_tenant(tenant_id):
    """Set the active tenant for this request/context (None clears it)."""
    _current_tenant.set(int(tenant_id) if tenant_id is not None else None)


def get_current_tenant():
    """Return the active tenant id, or None (e.g. super-admin / pre-login)."""
    return _current_tenant.get()


def require_tenant():
    """Return the active tenant id or raise — use in data writes that must be scoped."""
    tid = _current_tenant.get()
    if tid is None:
        raise RuntimeError("No tenant in context for a tenant-scoped operation.")
    return tid
