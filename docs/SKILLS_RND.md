# Skills Lab and R&D Lab: AI literacy (Art. 4), roles, experimentation

*[Version française](fr/SKILLS_RND.md)*

Engine: `api/app/engines/skills.py`. Configuration: `config/skills.yaml` and
`config/profiles.yaml` (titles, labels and notes in English and French). **Fictional** data. In
the 3D scene, the Skills Lab is room `S1` (8 seats), the R&D Lab groups benches `X1` to `X3`,
and the Agent Ops room holds consoles `O1` and `O2`.

## 1. Article 4 as rewritten by Regulation (EU) 2026/1744

Since July 27, 2026, Art. 4 no longer requires providers and deployers to "ensure a sufficient
level" of AI literacy: they **take measures to support** the AI literacy of their staff and of
the persons using AI systems on their behalf, with no obligation to guarantee an individual
level; the Commission and the Member States support these efforts. Exact wording to verify in
the Official Journal (sources: [GOVERNANCE.md](GOVERNANCE.md)).

The factory turns this into **measures adapted to role, risk and context**, and keeps the
evidence:

| Requirement | Implementation |
|---|---|
| Measures per role | `requirements`: modules required for each of the 11 roles, at least one `art4: true` module per role (checked by `validate_config.py`) |
| Adapted to risk | additional modules for anyone working on high-risk AI systems: human oversight (Art. 14 and 26), bias, incidents |
| Adapted to context | safe use of the C1–C4 classes and of the local GPU, agent supervision for the Agent Ops room |
| Evidence | dated `completions`, scheduled `sessions`, coverage per role and per person in `/api/v1/skills` |

The engine computes the overall coverage, the coverage of Art. 4 modules and the **overdue**
Art. 4 modules (missing and not scheduled in a session). Status `measures_in_place` if every
staffed role has an Art. 4 measure and Art. 4 coverage reaches 80%; `partial` otherwise. In the
demo: 73% overall coverage, one person overdue on a kata, sessions scheduled for the other gaps.

## 2. Learning paths and modules

| Module | Format | Art. 4 | Roles concerned (excerpt) |
|---|---|---|---|
| AI literacy foundations | lab | yes | everyone |
| Safe use of C1–C4 and the local GPU | lab | yes | technical roles |
| No-AI kata (anti-deskilling) | kata | yes | PO, LLM engineers, data, eval, Agent Ops, R&D, enablement |
| AI Act, ISO/IEC 42001 and NIST AI RMF awareness | workshop | yes | leadership, architecture, governance |
| Governance and reassessment per model version | workshop | yes | head, PO, governance |
| Human oversight (Art. 14 and 26) | workshop | yes | governance, Agent Ops, enablement |
| Agent supervision (escalations, kill switch, TTFT) | lab | yes | Agent Ops |
| LLM red teaming | lab | yes | architect, LLM engineers, eval, R&D |
| Fairness and bias | lab | yes | LLM engineers, eval, governance |
| AI incidents and reporting (Art. 73) | drill | yes | head, LLMOps, governance, Agent Ops |
| RAG, evaluation engineering, LLMOps, FinOps | lab / workshop | no (job skills) | depending on the role |

**No-AI kata**: at regular intervals, redo by hand a task usually delegated to AI (drafting a
reply, triaging a request, reviewing a clause), then compare with the system's output. Goal:
keep the skill needed to oversee AI and avoid over-reliance (the "Human-AI Configuration" risk of
the NIST AI 600-1 profile). All labs use fictional data.

Sessions: bookings of room S1 (8 seats), preferably on Thursdays (team day), without room
conflicts (checked by `validate_config.py`).

## 3. Factory roles

`head`, `po`, `architect`, `llm_engineer`, `data_engineer`, `eval_scientist`, `llmops`,
`governance`, `agent_ops`, `research`, `enablement`. Each role has a label, a target literacy
level and a description of responsibilities in `profiles.yaml`; the number of people is free
(12 in the demo, including two LLM engineers). Workstations reflect the zones: R&D benches for
research and evaluation, the secure Lab (B1, B2) for sensitive data, Agent Ops consoles for
supervision.

## 4. R&D Lab

An experiment follows `explore → prototype → evaluate → transfer` or stops (`stopped`). Each
record carries a measurable hypothesis, a quantified **transfer criterion**, the local GPU hours
per month, the linked use case and the bench used.

| Stage | Expected output |
|---|---|
| explore | state-of-the-art review, protocol, fictional data |
| prototype | prototype on a bench, first measurements |
| evaluate | measurement against the golden set of the linked use case and the thresholds of its risk tier |
| transfer | criteria met: handover to the product team, then LLMOps pipeline and risk review |
| stopped | documented stop (negative result or unacceptable risk) |

Rules: no fine-tuning or retraining experiment (the validator rejects these terms in titles and
hypotheses); the levers explored are routing, context, caching, decoding, evaluation and drift
detection. A transfer goes through the same gates as any change: evaluation according to the AI
Act tier and, if the model changes, a risk reassessment. Demo example: `x-07` (goodwill
credits without human approval) was stopped on a risk decision (MANAGE 1.1).
