# Changelog

## 1.0.0 (2026-11)

First public release.

- 3D digital twin of an AI factory (open space, secure lab with local GPU rack, R&D Lab, Skills
  Lab, Agent Ops room, War Room, focus pods), live and replayable timeline, bookings.
- First-person **visit mode**: drop the character on the floor, walk, interact.
- Factory console: AI risk register (EU AI Act incl. the 2026 Digital Omnibus, NIST AI RMF,
  ISO/IEC 42001 crosswalk), re-assessment on model version change, FinOps showback/chargeback with
  8 waste rules, LLMOps/RAG pipeline with evaluation gates and automatic rollback, data loop
  without retraining, hybrid cloud/VPS/local-GPU placement, Agent Ops, self-service catalogue,
  Skills and R&D labs.
- Aggregation API (FastAPI) and full Docker stack: LiteLLM gateway with an MIT cost callback,
  Langfuse (MIT core), Prometheus, Grafana and Loki (separate AGPL services), Alloy, Qdrant,
  SeaweedFS, Valkey; `sim` profile runs everything without any API key.
- English and French.
