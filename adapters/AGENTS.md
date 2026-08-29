# Adapter Instructions

Adapters are thin translations between an Agent Runtime and the EvoEventMem public API/MCP contracts.

## Rules

- Adapters must **not** implement extraction, ETEC, QEMR, persistence, or benchmark-specific logic.
- Every adapter requires a fake-service integration test and graceful service-unavailable behavior.
- Adapters depend only on `src/evoeventmem/` public interfaces, never on internal modules.

## Adding a new adapter

1. Create `adapters/<name>/` with `__init__.py`, a main module, and `README.md`.
2. Implement only transport-level concerns (MCP protocol, HTTP client, etc.).
3. Add a integration test in `tests/adapters/` using a fake memory service.
4. Document usage in the adapter's `README.md`.
