"""Repository layer: the only place where SQL is written.

Every function takes a ``TenantConnection`` (or, for the few identity lookups, a
``SystemConnection``). There is no way to run a query on application tables
without a tenant context, and queries still filter by ``org_id`` explicitly, so
tenant isolation holds even if one of the two locks (RLS, repository) fails.
``tests/test_architecture.py`` enforces that no other package talks to psycopg.
"""
