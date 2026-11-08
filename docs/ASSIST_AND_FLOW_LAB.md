# Contextual assistance and Flow Lab

**English** · [Français](fr/ASSISTANCE_ET_LABORATOIRE.md) · [Documentation](README.md)

## Where to start

| Need | Open | Result |
|---|---|---|
| Draft a task | Planning → Portfolio and tasks → task form | An editable suggestion the user can insert. The form keeps its own save step. |
| Understand a process | Planning → contextual help | Steps, checkpoints and questions to clarify. |
| Prepare a decision | Global administrator → decision assistance | A brief with hypotheses, questions and verification criteria using supplied context. |
| Test a topology | R&D Lab → [Flow Lab](https://alebgl77.github.io/ai-factory/docs/flow-lab.html?lang=en) | A simulated route, refusal or error, with estimated tokens, latency and cost. |

## Assistance and decision preparation

The demo generates structured suggestions with local rules. To enable model inference in a protected installation, set `FACTORY_ASSIST_ENABLED=on` and configure the SSO proxy, BU policy and local model bindings. The API verifies request scope and the virtual key's use-case binding. Its bounded call uses the local `chat-sensitive` model. The user reviews the response before inserting it. The form or Workbench applies its separate submission and decision rules. [Gateway controls](GATEWAY_SECURITY.md) · [Portfolio runner configuration](PORTFOLIO.md#built-in-runner)

Decision preparation requires an SSO group granting both the administrator role and global BU scope. The API rechecks the policy and model binding before responding. The interface rechecks the session before displaying a private response and while the assistance remains open. The protected endpoints use `GET /api/v1/assist` for available scope and `POST /api/v1/assist` for an explicit suggestion. [API implementation](../api/app/assist.py) · [Scope and request tests](../api/tests/test_assist.py)

Choose **Public web** or **Internal sources** when preparing a market brief. Supply facts with source titles and dates. The current assistance prepares research questions, hypotheses and criteria from the supplied material. Retrieval, source verification and synthesis of independently gathered results require a separate integration. [Roadmap](https://alebgl77.github.io/ai-factory/docs/roadmap.html?lang=en)

## Flow Lab

Build a directed graph from a request source, policy gateway, router, retrieval node, simulated CPU/GPU/remote models and destination. The editor has keyboard controls. It validates node and edge types, bounds, references and cycles before running the scenario.

Select request volume, input and output tokens, data class and nominal or outage settings. A repeated run with the same inputs yields the same paths and counters. A security policy blocks simulated C3/C4 traffic before a remote model. Results show completed, blocked and failed requests, attempts, tokens and routing reasons.

The simulator multiplies each deterministic path by its request count. Retrieval adds context to the simulated model input. Each successful inference adds its input and output tokens. A pre-inference failure adds an attempt and a simulated delay. Cost uses a rate per million tokens. Latency adds the configured step delays and time derived from model throughput. The model excludes queues and concurrency. The reference graph's nominal batch of 100 requests estimates 82,000 tokens. With the remote model down, the batch uses 200 attempts and reroutes to the local GPU. The estimated tokens stay the same because the first failure occurs before inference. [Simulation implementation](../dashboard/docs/flow-sim.js)

The browser keeps the 12 most recent simulations, including graph, fictional parameters and engine version, for replay and comparison. A content security policy blocks outbound connections from the lab. The inference service for private assistance runs on a separate path. [Flow Lab page](../dashboard/docs/flow-lab.html)

## Acceptance walkthrough

| Exercise | Verify |
|---|---|
| Repeat the same topology and parameters | Identical route and counters |
| Route C3/C4 to a simulated remote model | Refusal before simulated inference |
| Put a model out of service | Alternate route or explicit failure, according to the edges |
| Add a cycle or invalid edge | Validation rejects the run |
| Reopen a result | Topology, scenario, metrics and engine version are available |
| Ask for a draft then return to the form | The user controls insertion and separate submission |
| Change session during private assistance | Session check before display and withdrawal of now-inaccessible output |
| Prepare briefs with the two source choices | Distinct source mode and no automatic transmission to sources |
| Use an out-of-scope account or disable the local model | Private request is refused and the interface explains the state |
| Open the lab from R&D and use the keyboard | Direct launch and usable controls on desktop and mobile |
