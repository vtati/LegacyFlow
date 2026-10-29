"""SEC-003 (High) — accepted-risk note and interim route-coverage mitigation.

THE FINDING. The LegacyFlow API connects to Postgres with a single
pooled ``DATABASE_URL`` role and never engages row-level security: no
per-request ``SET LOCAL`` / ``set_config('app.current_user_id', …)``,
no least-privilege per-tenant role, and the Supabase RLS policies keyed
on ``auth.uid()`` therefore never fire for API traffic (the worker is
identical, and storage uses the service-role key). Tenant isolation —
the guarantee that one family/firm can never read or write another's
estate-planning data — rests entirely on application-level
authorization checks in the API service layer.

WHY THE FULL FIX IS DEFERRED. The complete remediation (a
least-privilege database role, per-request claim propagation into the
pooled connections, and RLS policies keyed on those claims across
every tenant table) is an architectural change to the data-access
layer of a live product: it touches every repository query path, the
connection pool's session-state management, the worker, and the
storage integration, and a mistake fails in the worst directions —
production outages if it fails closed, a silent cross-tenant breach if
it fails open. It is deliberately scheduled as its own program with
its own staging verification, not folded into a findings-fix batch.
Until it lands, SEC-003 remains OPEN and is accepted as a known risk
by the fix program, with this test as the interim mitigation.

WHAT THIS TEST GUARANTEES. It enumerates the entire FastAPI route
table (the v1 router tree plus the app-level routes) and, for every
route, proves by static call-graph reachability that the handler —
directly or through the app functions it calls, transitively —
invokes one of the canonical authorization-scope resolvers in
``app.core.authz`` (``get_matter_scope``, ``require_matter_firm_role``,
``require_firm_role``, ``require_firm_member``, ``get_user_firm_id``,
``get_firm_role``, ``require_platform_admin``, ``is_platform_admin``).
A route therefore cannot ship without passing through a scope
resolution: adding a new tenant-bearing route whose handler never
resolves a scope FAILS this test, and the failure names the route.
Routes that legitimately resolve no authz scope are enumerated in
``PUBLIC_OR_SELF_SCOPED`` below, each with its justification, and the
test asserts the allowlist is exact in both directions — an
allowlisted route that starts resolving scope, or disappears, also
fails, so the list must be maintained deliberately rather than grow
by drift.

WHAT THIS TEST DOES NOT GUARANTEE. (1) It proves a scope is RESOLVED,
not that it is resolved CORRECTLY: it cannot detect a handler that
resolves the caller's scope and then queries by a client-supplied id
from a different tenant, uses a read scope for a write, or checks the
scope of the wrong object — those defect classes (cf. SEC-011 /
SEC-012) are covered by the per-domain access-control test suites, not
by reachability. (2) The call graph is name-based and
over-approximates: when two app functions share a name, calls to
either are followed, so in principle a route could pass because an
unrelated same-named function resolves a scope. (3) It says nothing
about the worker, direct database access, or the storage
service-role path — RLS would cover all of them; this test covers
only the API's HTTP surface. (4) It is a regression gate, not a proof:
it must be paired with the per-domain access tests that assert
cross-tenant reads/writes actually return 403/404.

OWNER / REVIEW. Owner: LegacyFlow API maintainers. Review this note
whenever the route surface changes materially, and retire it when the
RLS redesign lands — at which point this test remains useful as a
defence-in-depth check but stops being the isolation backstop.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import os
import pkgutil

# App modules read settings at import time in a few places; give them
# inert values before importing the app package (mirrors the values the
# API test-suite runs under — nothing here opens a connection).
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-only")
os.environ.setdefault("SUPABASE_URL", "https://example.invalid")
os.environ.setdefault("DATABASE_URL", "postgresql://invalid/invalid")

import app  # noqa: E402
import pytest  # noqa: E402

#: The canonical authorization-scope resolvers (app.core.authz). A
#: tenant-bearing handler must reach at least one of these.
SCOPE_RESOLVERS = frozenset(
    {
        "get_matter_scope",
        "require_matter_firm_role",
        "require_firm_role",
        "require_firm_member",
        "get_user_firm_id",
        "get_firm_role",
        "require_platform_admin",
        "is_platform_admin",
    }
)

#: Routes that legitimately resolve no authz scope, each with the
#: reason it cannot touch another tenant's data. Exact in both
#: directions (see the module docstring).
PUBLIC_OR_SELF_SCOPED: dict[tuple[str, str], str] = {
    ("GET", "/healthz"): "Public liveness probe; returns only a status word, no tenant data.",
    ("GET", "/"): "Service metadata (name/version, docs pointer outside production); no tenant data.",
    ("GET", "/v1/me"): "Self-scoped: returns only the caller's own profile, keyed by the JWT sub.",
    ("GET", "/v1/me/notification-preferences"): "Self-scoped: preferences are keyed by the caller's JWT sub only.",
    ("PUT", "/v1/me/notification-preferences"): "Self-scoped: preferences are keyed by the caller's JWT sub only.",
    ("POST", "/v1/me/unsubscribe"): "Self-scoped: records the caller's own unsubscribe, keyed by JWT sub.",
    ("GET", "/v1/me/export"): "Self-scoped: exports only the caller's own data, keyed by JWT sub.",
    ("GET", "/v1/me/deletion-requests"): "Self-scoped: lists only the caller's own deletion requests, keyed by JWT sub.",
    ("POST", "/v1/invitations/accept"): "Bearer-token flow: the hashed invitation token is the credential (constant-time compared); it links the caller only to the membership that token was issued for.",
    ("POST", "/v1/attorney-applications"): "Self-scoped creation: the application is bound to the caller's user id; the named firm is a public directory entry being applied to. No tenant data is read or modified.",
    ("GET", "/v1/firms"): "Public directory: active firms' public names for the attorney-application firm picker; no tenant data.",
    ("POST", "/v1/vault/access"): "Bearer-token flow: the hashed vault-share token is the credential; returns only that share's redacted matter summary.",
    ("GET", "/v1/files/download"): "Bearer-token flow: the HMAC download token is bound to a single file and verified before any byte is served.",
}


def _build_registry() -> dict[tuple[str, str], str]:
    """Map (module, function name) -> source for every app-defined function."""
    registry: dict[tuple[str, str], str] = {}
    for info in pkgutil.walk_packages(app.__path__, prefix="app."):
        try:
            module = importlib.import_module(info.name)
        except Exception:  # noqa: BLE001 — a module that cannot import in tests
            continue  # cannot contribute reachable scope code either way
        for name, obj in vars(module).items():
            if inspect.isfunction(obj) and obj.__module__ == info.name:
                try:
                    registry[(info.name, name)] = inspect.getsource(obj)
                except (OSError, TypeError):
                    continue
    return registry


_REGISTRY = _build_registry()
_BY_NAME: dict[str, list[tuple[str, str]]] = {}
for _key in _REGISTRY:
    _BY_NAME.setdefault(_key[1], []).append(_key)


def _called_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def _reaches_scope(roots: list[tuple[str, str]]) -> bool:
    """True when any function reachable from ``roots`` calls a scope resolver."""
    seen: set[tuple[str, str]] = set()
    stack = list(roots)
    while stack:
        key = stack.pop()
        if key in seen or key not in _REGISTRY:
            continue
        seen.add(key)
        called = _called_names(_REGISTRY[key])
        if called & SCOPE_RESOLVERS:
            return True
        for name in called:
            stack.extend(_BY_NAME.get(name, ()))
    return False


def _enumerate_routes() -> dict[tuple[str, str], list[tuple[str, str]]]:
    """(method, path) -> BFS roots (endpoint + dependency callables)."""
    from app.api.v1.router import router as v1_router
    from app.main import create_app

    routes: dict[tuple[str, str], list[tuple[str, str]]] = {}

    def _add(route: object, full_path: str) -> None:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None or not getattr(endpoint, "__module__", "").startswith("app."):
            return
        roots = [(endpoint.__module__, endpoint.__name__)]
        dependant = getattr(route, "dependant", None)
        for dep in getattr(dependant, "dependencies", ()) or ():
            call = getattr(dep, "call", None)
            if call is not None and getattr(call, "__module__", "").startswith("app."):
                roots.append((call.__module__, call.__name__))
        for method in getattr(route, "methods", None) or ():
            if method in {"HEAD", "OPTIONS"}:
                continue
            routes[(method, full_path)] = roots

    def _walk(router: object, prefix: str) -> None:
        for route in router.routes:  # type: ignore[attr-defined]
            if type(route).__name__ == "_IncludedRouter":
                ctx = route.include_context
                _walk(route.original_router, prefix + (ctx.prefix if ctx else ""))
            else:
                _add(route, prefix + getattr(route, "path", ""))

    _walk(v1_router, "/v1")
    for route in create_app().routes:
        if type(route).__name__ == "_IncludedRouter":
            continue
        _add(route, getattr(route, "path", ""))
    return routes


ROUTES = _enumerate_routes()


def test_route_table_is_fully_enumerated() -> None:
    # Guard against an enumeration regression silently shrinking the
    # surface this gate covers (145 v1 routes + app-level routes at
    # the time of writing).
    assert len(ROUTES) >= 140, f"only {len(ROUTES)} routes enumerated"


def test_every_route_resolves_scope_or_is_allowlisted() -> None:
    failures: list[str] = []
    for (method, path), roots in sorted(ROUTES.items()):
        if (method, path) in PUBLIC_OR_SELF_SCOPED:
            continue
        if not _reaches_scope(roots):
            failures.append(f"{method} {path}")
    assert not failures, (
        "Routes that never resolve an authorization scope (SEC-003 gate). "
        "Either route the handler through app.core.authz scope resolution, "
        "or — only if it genuinely cannot touch tenant data — add it to "
        "PUBLIC_OR_SELF_SCOPED with a justification: " + ", ".join(failures)
    )


def test_allowlist_is_exact() -> None:
    stale = set(PUBLIC_OR_SELF_SCOPED) - set(ROUTES)
    assert not stale, f"allowlisted routes no longer exist: {sorted(stale)}"
    now_scoped = [
        f"{method} {path}"
        for (method, path), roots in sorted(ROUTES.items())
        if (method, path) in PUBLIC_OR_SELF_SCOPED and _reaches_scope(roots)
    ]
    assert not now_scoped, (
        "Allowlisted routes that now resolve a scope — remove them from "
        "PUBLIC_OR_SELF_SCOPED: " + ", ".join(now_scoped)
    )


def test_allowlist_entries_carry_justifications() -> None:
    for key, justification in PUBLIC_OR_SELF_SCOPED.items():
        assert len(justification) >= 40, f"{key} needs a real justification"


@pytest.mark.parametrize("resolver", sorted(SCOPE_RESOLVERS))
def test_scope_resolvers_exist_in_authz(resolver: str) -> None:
    # If a resolver is renamed/removed, the reachability gate would
    # silently lose meaning; pin the vocabulary to app.core.authz.
    from app.core import authz

    assert callable(getattr(authz, resolver, None)), resolver
