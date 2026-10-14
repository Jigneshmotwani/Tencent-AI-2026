"""Idempotent code-defined defaults applied at bootstrap: platform policy, default MCP server, workflow templates.
Each owning module exposes one `ensure_*` function; this file only sequences them."""


def seed_defaults(db):
    from .governance.policies import ensure_default_policy
    from .mcp.registry import ensure_default_servers
    from .workflows.templates import ensure_templates
    ensure_default_policy(db)
    ensure_default_servers(db)
    ensure_templates(db)
    db.commit()
