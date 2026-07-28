# Adapter instructions

Adapters are thin translations between an Agent Runtime and the EvoEventMem public API/MCP contracts.
They must not implement extraction, ETEC, QEMR, persistence, or benchmark-specific logic.
Every adapter requires a fake-service integration test and graceful service-unavailable behavior.
