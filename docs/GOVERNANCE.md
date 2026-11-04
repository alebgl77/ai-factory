# Governance: register, risk tiers, reassessment, crosswalk

*[Version française](fr/GOVERNANCE.md)*

This document describes the governance pillar of Virtual AI Factory: the use-case register, the
EU AI Act qualification, the NIST AI RMF cycle, the reassessment on every model version, and the
NIST AI RMF ↔ AI Act ↔ ISO/IEC 42001 crosswalk.

> Everything is **indicative**: this repository is not legal advice. The demo data is
> **fictional**. Regulatory dates are configuration (`config/factory.yaml`) and must be checked
> against the Official Journal of the European Union before any use.

## 1. Use-case register (GitOps)

One file per use case in `config/usecases/<id>.yaml` (schema: [CONTRACT.md](CONTRACT.md),
section 2.3). The register is versioned in Git: a risk review, a model change or a derogation
leaves a commit trail. The API hot-reloads the configuration and overlays the decisions entered
in the dashboard (`FACTORY_STATE_DIR`). Text fields may be a plain string or an `{en, fr}`
mapping; the API resolves them with `?lang=en|fr`.

| Block | Role |
|---|---|
| `owner`, `cost_center`, `stage`, `task_type` | accountability, FinOps allocation, lifecycle stage `cadrage` (scoping) `→ poc → mvp → indus` (industrialization) `→ run → retired` |
| `sensitivity` | data class C1 (public) to C4 (secret); C3/C4 require the `chat-sensitive` alias (local GPU) |
| `ai_act` | role (provider, deployer or both), prohibited practices, Annex III area and point, Art. 6(3) derogation, Annex I safety component, Art. 50 transparency obligations, use of a general-purpose AI (GPAI) model |
| `model` | LiteLLM alias, current version, version history |
| `assessment` | latest review: reviewed model version, date, reviewer, decision, conditions |
| `nist` | status `done / partial / todo` of the GOVERN, MAP, MEASURE and MANAGE functions, with evidence and gaps |
| `iso42001` | Annex A controls implemented (evidence for the crosswalk) |
| `rag`, `eval`, `kpis` | RAG parameters, golden set and judge, expected value |

`python scripts/validate_config.py` checks consistency (people, cost centers, aliases, sensitive
classes, NIST / AI Act / ISO identifiers, sessions, R&D benches…).

## 2. AI Act qualification

The engine `api/app/engines/risk.py` applies, in order:

1. **Prohibited** if `prohibited_practices` is not empty (Art. 5): no deployment.
2. **High risk** if an Annex III area is set **without** an Art. 6(3) derogation, or if the
   system is a safety component of a product covered by Annex I.
3. **Transparency (limited)** if an Art. 50 obligation applies: `chatbot` (50(1)),
   `synthetic_content` (50(2)), `emotion_recognition` (50(3)), `deepfake` (50(4)).
4. **Minimal** otherwise (Art. 4 on AI literacy applies at every tier).

`gpai` is an orthogonal flag: the Chapter V obligations fall on the provider of the model; the
factory, downstream, requires its documentation (Art. 53(1)(b)) and manages the AI value chain.
The **Art. 6(3) derogation** is only accepted if it is documented before the system is placed on
the market or put into service (Art. 6(4)), involves no profiling, and comes with the
registration under Art. 49(2), which Regulation 2026/1744 keeps with less information.

`applicable_from` gives the milestone that governs the tier (Annex III, Annex I, Art. 50, Art. 5
or, for the minimal tier, Art. 4) and `days_left` the remaining time.

### Demo use cases (fictional)

| Id | Use case | Data | Tier | Key governance point |
|---|---|---|---|---|
| uc-01 | Internal document RAG assistant | C2 | limited (chatbot) | review up to date for 2026-09 |
| uc-02 | Multi-step support agent (GPAI, LangGraph) | C2 | limited (chatbot) | model 2026-10 not reviewed: **deployment blocked** |
| uc-03 | Inbound request triage | C2 | minimal | classification on a premium model (FinOps alert) |
| uc-04 | Job application pre-screening | C3 | **high risk**, Annex III point 4(a) | local model 2026-10 not reviewed: **blocked**, bias tests to rerun |
| uc-05 | Loan application completeness check | C3 | minimal, **Art. 6(3)(a) derogation** documented | Annex III area 5(b) without creditworthiness assessment |
| uc-06 | Marketing content | C1 | limited (synthetic content) | Art. 50(2) marking, deployer of a third-party tool |
| uc-07 | Internal committee summaries | C3 | minimal | reviewed after the local model upgrade |
| uc-08 | Confidential clause analysis | C4 | minimal | scoping, review pending |

## 3. The NIST AI RMF cycle in the factory

| Function | What feeds it in the factory | Examples of evidence |
|---|---|---|
| **GOVERN** | AI policy, RACI, human oversight roles, Skills Lab Art. 4 learning paths | `profiles.yaml`, `skills.yaml`, versioned policy |
| **MAP** | scoping sheet, data class, AI Act qualification, mapping of MCP tools | `ai_act`, `sensitivity` blocks, `catalog/tools.yaml` |
| **MEASURE** | golden sets, RAGAS, LLM-as-a-judge, red teaming, bias tests, Agent Ops | evaluation gate, `eval_gates`, Langfuse traces |
| **MANAGE** | go / no-go decision, automatic rollback, data loop, kill switch, incidents | `rollback`, `dataloop`, Agent Ops room |

`nist_progress` is the share of the four functions with status `done` (`partial` counts as 0.5).
The labels of the 19 categories and 72 subcategories are in `config/frameworks/nist_ai_rmf.yaml`
(short labels; the French version is an unofficial translation). The NIST AI 600-1 Generative AI
Profile is referenced there with its 12 risks, used to build the red-teaming campaigns.

## 4. Reassessment on every model version change

Rule ([CONTRACT.md](CONTRACT.md), section 2.4): `reevaluation_required` is true if
`model.version` differs from `assessment.reviewed_model_version`, or if the decision is
`pending`. The `deploy` gate of the LLMOps pipeline is then **blocked** with reason
`risk_reassessment`, whatever the evaluation results.

- `POST /api/v1/registry/{id}/model-version {version, name?}` adds the version to the history,
  makes it current and raises the flag (the response says so).
- `POST /api/v1/registry/{id}/assessment {decision, reviewer}` records the review of the current
  version; an `approved` or `approved_with_conditions` decision lifts the block.

Why every version? A new model, even behind the same alias, can change quality, bias or
robustness; for a provider, a substantial modification requires a new conformity assessment
(Art. 43(4)), and a deployer who substantially modifies a high-risk AI system becomes its
provider (Art. 25(1)). In the demo, the 2026-10 local model upgrade has been reviewed for uc-05
and uc-07, not yet for uc-04.

## 5. Crosswalk

`config/frameworks/crosswalk.yaml` groups 24 control families. Each family links NIST
subcategories, AI Act articles and ISO/IEC 42001 clauses or controls, with the tiers for which
the factory policy **requires** it, an evidence hint and the basis of the mapping.

- **Required controls**: for a use case, the families whose `tiers` contain its tier (or `gpai`
  if it uses a GPAI model).
- **Evidenced control**: one of the family's ISO references appears in the use case's
  `iso42001` list. `controls_coverage` is the share of required families that are evidenced.
- Families required beyond the letter of the Regulation (evaluation, per-version reassessment,
  sovereign placement, frugality): a policy choice of the factory, flagged in `basis`.

| Family (excerpt) | NIST AI RMF | AI Act | ISO/IEC 42001 |
|---|---|---|---|
| Risk management | GOVERN 1.3, MAP 1.1, MEASURE 1.1, MANAGE 1.1-1.3 | Art. 9 | 6.1, 8.2, 8.3, A.5.2 |
| Data governance | MAP 2.3, MEASURE 2.1 | Art. 10 | A.4.3, A.7.2-A.7.6 |
| Record-keeping (logging) | MEASURE 2.4, GOVERN 4.3 | Art. 12, 19, 26(6) | A.6.2.8 |
| Human oversight | GOVERN 3.2, MAP 3.5, MANAGE 2.4 | Art. 14, 26(2) | A.9.2, A.9.3 |
| Per-version reassessment | GOVERN 1.5, MANAGE 3.2, MEASURE 3.1 | Art. 43(4), 25, 3(23) | 6.3, A.6.2.5 |
| Incidents and rollback | MANAGE 2.3, 2.4, 4.3, GOVERN 4.3 | Art. 73, 26(5) | 10.2, A.8.4, A.3.3 |
| AI literacy | GOVERN 2.2, MAP 3.4 | Art. 4 | 7.2, 7.3, A.4.6 |

Limits: the crosswalk is **indicative**; the ISO text is paywalled, so ISO references rely on
the titles published by secondary sources and are paraphrased; an ISO/IEC 42001 certification
does not create a presumption of conformity with the AI Act (to our knowledge as of 2026-11-03,
it is not a harmonized standard cited in the Official Journal under the Regulation; point
**to verify**).

## 6. Regulatory calendar (AI Act)

Regulation (EU) 2024/1689 as amended by Regulation (EU) 2026/1744 "Digital Omnibus on AI",
published in the Official Journal on **July 24, 2026**, in force on **July 27, 2026**. Status:
"confirmed" = at least two concurring sources; "to verify" = a single or diverging source. The
Official Journal text could not be consulted directly from the build environment (restricted
network access): the dates are cross-checked against secondary sources.

| Date | Milestone | Status |
|---|---|---|
| 2025-02-02 | Art. 5 prohibited AI practices; Art. 4 AI literacy | confirmed (original text) |
| 2025-08-02 | Chapter V, general-purpose AI models | confirmed (original text) |
| 2026-07-27 | Entry into force of 2026/1744: Art. 4 rewritten | confirmed |
| 2026-08-02 | Art. 50 transparency; Commission fines for GPAI providers (Art. 101) | confirmed |
| 2026-12-02 | Art. 50(2) marking: end of the grace period for systems placed on the market before 2026-08-02 | confirmed |
| 2026-12-02 | New Art. 5 prohibitions (non-consensual intimate imagery, child sexual abuse material) | to verify |
| 2027-08-02 | GPAI models placed on the market before 2025-08-02 (Art. 111(3)); national AI regulatory sandboxes | confirmed / to verify |
| 2027-12-02 | High-risk AI systems, Annex III (instead of 2026-08-02) | confirmed |
| 2028-08-02 | High-risk AI systems, Annex I (instead of 2027-08-02) | confirmed |

**Art. 4 rewritten**: the obligation to "ensure a sufficient level" of AI literacy becomes an
obligation to **take measures to support** AI literacy, without guaranteeing a given level; the
Commission and the Member States support providers and deployers. Exact wording **to verify in
the Official Journal**. Implementation: [SKILLS_RND.md](SKILLS_RND.md).

The fixed dates for Annex III and Annex I replace the conditional mechanism (availability of
standards) of the Commission proposal (COM(2025) 836).

## 7. Adapting to an organization

1. Replace the fictional use cases with your own (one file each) and qualify `ai_act`.
2. Adjust `eval_gates` and the crosswalk `tiers` policy to your risk appetite.
3. Have the qualification and the crosswalk validated by a lawyer.
4. `python scripts/validate_config.py`, then `python scripts/build_snapshot.py`.

## Sources

Consulted on **2026-11-03** through a search engine. The build environment could not open these
pages directly (restricted network access): their content is known from indexed excerpts,
cross-checked between sources, and, for the original AI Act and the NIST AI RMF, from the
official texts published before 2026. Only the dates marked "confirmed" in section 6 were
cross-checked against at least two sources.

Official texts:

- Regulation (EU) 2024/1689 (AI Act), OJ L, 12.7.2024: <https://eur-lex.europa.eu/eli/reg/2024/1689/oj>
- Regulation (EU) 2026/1744 (Digital Omnibus on AI), OJ L, 24.7.2026: <https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ:L_202601744>
- NIST AI 100-1, *Artificial Intelligence Risk Management Framework (AI RMF 1.0)*, January 26, 2023: <https://doi.org/10.6028/NIST.AI.100-1>
- NIST AI 600-1, *Generative Artificial Intelligence Profile*, July 26, 2024: <https://doi.org/10.6028/NIST.AI.600-1>
- NIST AI Resource Center (Playbook): <https://airc.nist.gov/>
- ISO/IEC 42001:2023, ISO page (paywalled standard): <https://www.iso.org/standard/81230.html>

Secondary sources (Omnibus 2026/1744):

- Hunton, "EU Digital Omnibus on AI Enters Into Force": <https://www.hunton.com/privacy-and-cybersecurity-law-blog/eu-digital-omnibus-on-ai-enters-into-force>
- K&L Gates, "EU Digital Omnibus on AI Enters Into Force" (July 31, 2026): <https://www.klgates.com/EU-Digital-Omnibus-on-AI-Enters-Into-Force-7-31-2026>
- White & Case, "EU AI Omnibus enters into force, amending the AI Act": <https://www.whitecase.com/insight-alert/eu-ai-omnibus-enters-force-amending-ai-act>
- Cooley, "Digital AI Omnibus delays key deadlines, introduces new rules": <https://cdp.cooley.com/digital-ai-omnibus-delays-key-deadlines-introduces-new-rules/>
- SCL, "Council of the EU approves AI Omnibus reforms" (Council approval of June 29, 2026): <https://www.scl.org/council-of-the-eu-approves-ai-omnibus-reforms/>
- Gibson Dunn, "EU AI Act Omnibus Agreement": <https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/>
- Cloud Security Alliance, research note on the Art. 50(2) marking deadline: <https://labs.cloudsecurityalliance.org/research/csa-research-note-eu-ai-act-article50-watermarking-deadline/>
- Center for Democracy and Technology, Art. 49(2) registration kept: <https://cdt.org/insights/final-ai-omnibus-text-dilutes-fundamental-rights-protections/>
- Digital Policy Alert, new Art. 5 prohibitions applicable from December 2, 2026: <https://digitalpolicyalert.org/event/42386-digital-omnibus-on-ai-regulation-prohibiting-nudification-apps-and-ai-systems-generating-or-manipulating-child-sexual-abuse-material-becomes-applicable>
- Law & Technology, "AI literacy: the Digital Omnibus rewrites Article 4": <https://lawandtechnology.eu/en/ai-literacy-digital-omnibus-article-4-ai-act/>
- Matheson, EDPB-EDPS joint opinion on the AI Omnibus (new Art. 4a replacing Art. 10(5) in the proposal): <https://www.matheson.com/insights/edpb-edps-publishes-opinion-on-ai-digital-omnibus-proposal/>
- Commission proposal COM(2025) 836 of November 19, 2025 (copy from the Austrian Parliament): <https://www.parlament.gv.at/dokument/XXVIII/EU/45688/imfname_11543574.pdf>

Secondary sources (NIST and ISO identifiers):

- ISMS.online, ISO/IEC 42001 Annex A controls: <https://www.isms.online/iso-42001/annex-a-controls/>
- Modulos, ISO/IEC 42001 Annexes A to D: <https://docs.modulos.ai/frameworks/iso-42001/annexes-a-d>
- STIG Viewer, AI RMF 1.0 catalog: <https://www.stigviewer.com/controls/ai-rmf/catalog>

Unconfirmed points, to verify in the Official Journal: exact wording of the new Art. 4; date and
scope of the new Art. 5 prohibitions; deadline for national AI regulatory sandboxes; final
numbering of the legal basis for "processing special categories of personal data for bias
detection" (Art. 10(5) or new Art. 4a); status of ISO/IEC 42001 with regard to harmonized
standards.
