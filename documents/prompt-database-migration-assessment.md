# Prompt Database Migration Work Prompt

## Objective

Migrate the application's retained, day-to-day generative AI prompts into the existing prompt database and Prompt Studio model.

For each independently callable AI feature, provide the same core capability already available for Ask:

- Store immutable prompt versions in the database.
- Select the live version of each prompt component.
- Capture the feature's runtime inputs, selected prompt recipe, rendered provider request, response, and error.
- Replay a captured feature invocation with alternate prompt-version combinations.
- Display replay results side by side for manual review.

This is a demo-phase migration. Keep the implementation focused on prompt selection, trace capture, and replay. Do not build a generalized workflow engine or an automated prompt-evaluation platform.

## Decisions Already Made

Treat the following as requirements, not open design questions:

1. **Knowledge-base construction is out of scope.**
   Do not migrate or redesign document extraction, chunking, vector embedding creation, vector indexes, ingestion, or the completed Azure AI/Foundry knowledge base.

2. **A feature is one independently replayable AI call.**
   If a workflow has five AI steps, each step may be a separate feature. Replaying step 3 starts with the inputs originally supplied to step 3. It does not rerun steps 1 and 2.

3. **Upstream results become captured inputs.**
   A transcript, persona, source context, malformed response, or other earlier result is stored as part of the feature trace when the current feature receives it.

4. **Testing in this phase means replay and comparison.**
   Prompt Studio should run selected prompt-version combinations against the same captured inputs and show the resulting requests, responses, and errors.

5. **Acceptance and failure criteria are deferred.**
   Do not add semantic grading, quality scoring, pass/fail status, LLM-as-judge behavior, approval workflow, or automated acceptance rules.

6. **Output-schema testing is deferred.**
   Preserve existing prompt text and runtime behavior, including schemas already embedded in prompts, but do not add schema-conformance testing or parser-compatibility success criteria to Prompt Studio in this phase.

7. **Full integration testing is deferred.**
   Feature-level replay does not need to prove that all workflow steps work together. End-to-end integration tests can be designed later.

8. **Sensitive-data controls are out of scope for this demo.**
   The application contains demo data. Do not add redaction, retention, encryption, access-control, or privacy-policy work as part of this migration.

9. **Operational model calls are not prompts to manage.**
   For example, the roleplay keep-warm input `ready` remains outside Prompt Studio.

## Terminology

### Feature

One independently executable and replayable generative AI capability, such as `ask`, `learn-article`, or `prepare-feedback`.

### Prompt component

A versioned block of static instructions used by one or more features, such as `learn.persona` or `prepare.playbook.sales`.

### Runtime input

Captured data supplied to a feature invocation. Runtime inputs are not selectable prompt versions. Examples include a user question, transcript, persona, topic, language, source context, or previous response.

### Prompt recipe

The exact prompt-component versions used for one invocation:

```json
{
  "learn.persona": 2,
  "learn.article": 4
}
```

### Feature trace

The saved record that makes an invocation independently replayable:

```text
feature key
+ selected prompt recipe
+ captured runtime inputs
+ rendered provider request
+ provider/model metadata
+ response or error
```

An optional correlation ID may connect calls from the same user workflow for diagnostics. Replay must not depend on that relationship.

## Scope Boundary

### In scope

- Static application-managed instructions sent to an LLM or hosted agent.
- Shared prompt components and roleplay playbooks used in model requests.
- Immutable prompt versions and selected live versions.
- Complete feature-boundary runtime inputs needed for independent replay.
- Rendered provider requests.
- Provider responses and errors.
- Feature-specific replay adapters.
- Prompt-version combinations and side-by-side manual comparison.
- Minimal Prompt Studio changes needed to support the additional features.

### Out of scope

- Extraction, chunking, embeddings, vector search infrastructure, and knowledge-base ingestion.
- Rebuilding or evaluating the Azure AI/Foundry knowledge base.
- Re-executing earlier workflow stages during feature replay.
- Workflow orchestration in Prompt Studio.
- Automated prompt-quality acceptance or failure decisions.
- Semantic grading or LLM-as-judge evaluation.
- Output-schema validation and parser-contract testing.
- Full end-to-end integration testing.
- Demo-data privacy and retention controls.
- Azure Speech synthesis.
- Operational keep-warm calls.

## Existing Reference Implementation

Ask is the migration pattern to preserve and generalize:

- Prompt definitions and immutable versions are stored in `prompt_definitions` and `prompt_versions` in `src/rag_layer/db.py`.
- Ask seeds `ask.navigator`, `ask.about_me`, and `ask.memories` from files under `Prompts/`.
- `src/rag_layer/foundry.py` captures inputs, the rendered provider request, response, and error.
- `src/rag_layer/prompt_admin.py` defines the Ask feature, lists source traces, validates prompt recipes, and replays selected combinations.
- `prompt_test_configurations`, `prompt_test_runs`, and `prompt_test_cases` preserve replay setups and results.
- `ui/prompts.html` provides version management and replay comparison.

Generalize this behavior only as much as necessary to support the retained Learn and Prepare features.

## Candidate Feature and Prompt Catalog

The catalog below is the migration target, subject to the active-feature check described later.

### Ask

**Feature:** `ask`

| Prompt component | Purpose |
|---|---|
| `ask.navigator` | Main FlexLife instructor and grounding behavior |
| `ask.about_me` | Optional About Me augmentation |
| `ask.memories` | Optional saved-memory augmentation |
| `ask.answer_style` | Length, format, tone, simple-language, and citation preferences |

The first three components are already versioned. The answer-style text is currently assembled in `foundry.py`. Migrate it only if doing so preserves the existing preference mapping without creating unnecessary template complexity; otherwise document it as stable application-owned prompt assembly for this phase.

Captured inputs include question, history, preference selections, About Me, memories, user ID, and agent metadata.

### Learn Query Planner

**Feature:** `learn-query-planner`

| Prompt component | Purpose |
|---|---|
| `learn.query_planner` | Convert a learner topic into focused retrieval queries |

Captured inputs include topic and requested content kind. Curriculum items with pinned sources skip this feature.

### Learn Article

**Feature:** `learn-article`

| Prompt component | Purpose |
|---|---|
| `learn.persona` | Shared senior sales-coach identity and compliance behavior |
| `learn.article` | Article-specific teaching, length, structure, and response instructions |

Captured inputs include topic, length specification, optional curriculum brief, and supplied source context.

### Learn Flashcards

**Feature:** `learn-flashcards`

| Prompt component | Purpose |
|---|---|
| `learn.persona` | Shared senior sales-coach identity and compliance behavior |
| `learn.flashcards` | Flashcard composition, ordering, and response instructions |

Captured inputs include topic, card-count target, optional curriculum brief, and supplied source context.

### Learn Audio Script

**Feature:** `learn-audio`

| Prompt component | Purpose |
|---|---|
| `learn.persona` | Shared senior sales-coach identity and compliance behavior |
| `learn.audio` | Host roles, spoken-language, length, and response instructions |

Captured inputs include topic, host names, turn and word targets, optional curriculum brief, and supplied source context. Azure Speech synthesis remains out of scope.

### Learn JSON Repair

**Feature:** `learn-json-repair`

| Prompt component | Purpose |
|---|---|
| `learn.json_repair` | Convert malformed model output into a valid JSON object |

Captured input is the malformed response. Replaying this feature must not rerun the generator that produced that response.

### Prepare Custom Persona

**Feature:** `prepare-persona`

| Prompt component | Purpose |
|---|---|
| `prepare.playbook.persona` | Shared prospect behavior playbook |
| `prepare.persona_generator` | Persona generation instructions |

Captured inputs include the user's scenario notes and any other values actually interpolated into the provider request.

### Prepare Prospect Reply

**Feature:** `prepare-prospect-reply`

| Prompt component | Purpose |
|---|---|
| `prepare.playbook.persona` | Shared prospect behavior playbook |
| `prepare.prospect.callback` | Callback-mode scene and behavior instructions |
| `prepare.prospect.meeting` | Presentation/discovery-mode scene and behavior instructions |
| `prepare.prospect.reply_rules` | Shared response, speech, language, and ending behavior |

Callback and meeting components are mutually exclusive according to the captured mode.

Captured inputs include persona snapshot, scene, training objective, custom notes, recent transcript, latest agent message, language, mode, and optional previous reply.

A duplicate-response retry is another invocation of the same feature with `previous_reply` populated. It is not a separate feature and does not require replaying the original invocation first.

### Prepare Coaching

**Feature:** `prepare-coaching`

| Prompt component | Purpose |
|---|---|
| `prepare.playbook.sales` | Shared sales-process and approved-language guidance |
| `prepare.coaching` | Conversation-stage and coaching-response instructions |

Captured inputs include persona summary, recent transcript, latest agent message, and supplied source context.

### Prepare Fact Check

**Feature:** `prepare-fact-check`

| Prompt component | Purpose |
|---|---|
| `prepare.fact_check` | Verify an agent statement against supplied evidence |

Captured inputs include the claim and evidence supplied to the model. Retrieval and knowledge-base implementation remain out of scope.

The current code still invokes this generative fact-check prompt through the old local retrieval path. Before migration, decide whether the feature remains in the target application. If retained, migrate the prompt and treat evidence as captured runtime input. If retired, remove or bypass the dead call instead of migrating it.

### Prepare Ask Navigator

**Feature:** `prepare-navigator`

| Prompt component | Purpose |
|---|---|
| `prepare.playbook.sales` | Shared sales-process and approved-language guidance |
| `prepare.navigator` | In-session coach behavior and response instructions |

Captured inputs include persona summary, recent transcript, agent question, and supplied source context.

### Prepare Feedback

**Feature:** `prepare-feedback`

| Prompt component | Purpose |
|---|---|
| `prepare.playbook.feedback` | Shared evaluation playbook |
| `prepare.feedback` | Feedback findings and score response instructions |

Captured inputs include persona and evaluation data, outcome, transcript, and verification events.

### Old Local Answer and Chat Endpoints

The repository still exposes local `/v1/answer` and `/v1/chat` generative calls backed by the previous local retrieval implementation. Confirm whether these endpoints are retained in the target demo before migration.

- If retained, define and migrate their prompt features.
- If superseded by Foundry Ask, treat them as legacy and do not expand migration scope merely because the code still exists.

## Implementation Requirements

### 1. Confirm the active target surface

Before editing prompt infrastructure, produce a short inventory of active generative call sites and classify each as:

- Migrate now.
- Retain but migrate later.
- Legacy/remove.
- Operational/non-business call.

Do not include embedding requests or Azure Speech calls. Resolve the fact-check and old local answer/chat status before finalizing the prompt count.

### 2. Generalize traces minimally

Adapt the current Ask trace model so retained Azure OpenAI and Foundry calls can use it. Each feature trace must store:

- Feature key.
- Request ID and timestamp.
- Provider and model/agent metadata.
- Exact prompt recipe.
- Captured runtime inputs.
- Rendered provider request.
- Flattened readable prompt.
- Response or error.
- Optional workflow/correlation ID for diagnostics only.

Prefer extending or renaming the existing trace abstraction over introducing parallel trace systems unless a concrete schema constraint requires otherwise.

### 3. Add an explicit feature registry

Replace the Ask-only `TEST_FEATURES` assumption with a registry of retained, independently replayable features. Each feature definition must provide:

- Display name and purpose.
- Required prompt components.
- Optional or mutually exclusive components.
- A replay function or adapter.
- Enough input validation to avoid constructing an invalid provider request.

Do not create stage ordering, dependency execution, parent/child replay, or workflow branching in Prompt Studio.

### 4. Seed immutable prompt versions

Move retained static prompt text and shared playbooks into seed files under `Prompts/` or another clearly named prompt source directory. Seed version 1 through the existing idempotent database initialization pattern.

Requirements:

- Never overwrite an existing database version.
- Never change a selected version during startup when one is already selected.
- Preserve the effective version-1 provider prompt as closely as possible.
- Avoid unrelated prompt rewriting during migration.

### 5. Resolve prompts at the feature boundary

Each live feature must resolve its selected prompt components before rendering the provider request. Record the resolved recipe and complete runtime inputs before the provider call.

Do not capture only the final string. Independent replay requires both:

- Structured inputs for rerendering with another prompt version.
- The exact rendered request originally sent for audit and comparison.

### 6. Implement independent replay

For each retained feature:

1. Select an original feature trace.
2. Choose one complete prompt-version recipe per replay case.
3. Reuse the trace's captured runtime inputs.
4. Render and invoke only that feature.
5. Save the rendered request, response, or provider error.
6. Show cases side by side using the existing Prompt Studio result model.

Do not mark a case passed or failed based on response quality. A provider error may be displayed as an execution error, as Ask does today, but it is not a prompt-quality judgment.

### 7. Preserve current runtime behavior

Keep provider parameters, retry behavior, conditional prompt branches, response extraction, and post-processing unchanged unless a change is necessary for prompt resolution or tracing.

This includes:

- Ask history and optional augmentations.
- Learn JSON-mode fallback and JSON-repair invocation.
- Prospect callback/meeting mode selection.
- Prospect duplicate-response retry.
- Roleplay language instructions and end-call markers.
- Existing background execution behavior.

Do not add schema validation or new output-quality checks.

### 8. Update Prompt Studio only as needed

The UI must support:

- Listing the additional features.
- Listing each feature's prompt components and versions.
- Selecting a source trace.
- Selecting complete prompt-version recipes.
- Starting replays.
- Displaying requests, responses, and provider errors side by side.

Do not add rubrics, scores, approvals, pass/fail controls, or integration-run views.

## Implementation Order

Use small, validated increments:

1. Confirm and document the retained feature list.
2. Introduce the provider-neutral feature trace contract while preserving Ask behavior.
3. Generalize the feature registry and replay dispatch while preserving Ask replay tests.
4. Migrate one simple Learn feature end to end, preferably `learn-query-planner`.
5. Migrate the remaining Learn generators and JSON repair.
6. Migrate Prepare custom persona and feedback.
7. Migrate Prepare Navigator and coaching.
8. Migrate prospect reply last because it has mode branches, language inputs, and duplicate-response retries.
9. Migrate fact checking only if confirmed as retained.
10. Update Prompt Studio incrementally as each feature becomes replayable.

After each feature migration, run its focused tests before starting the next feature.

## Completion Checks for This Phase

The work is complete when:

- Every retained business LLM/agent call is assigned to an independently replayable feature.
- Every retained static prompt component is stored as an immutable database version or explicitly documented as stable application-owned assembly.
- Live calls resolve selected prompt versions from the database.
- Each live call records its feature key, recipe, inputs, rendered request, and response or error.
- Each migrated feature appears in Prompt Studio.
- A user can select an original trace and replay that feature with alternate prompt-version combinations.
- Replay does not rerun earlier workflow steps or refresh upstream inputs.
- Existing Ask selection, tracing, and replay behavior remains operational.
- Existing Learn and Prepare user behavior remains operational after prompt extraction.
- Provider errors are visible without being treated as prompt-quality failures.
- No acceptance rubric, semantic grading, pass/fail workflow, schema testing, or integration runner has been added.
- Chunking, embeddings, ingestion, knowledge-base construction, and Azure Speech remain untouched except for unavoidable import or dead-code cleanup associated with confirmed legacy features.

## Deferred Work

Record these as follow-up items, not deliverables for this migration:

- Prompt-quality acceptance and failure criteria.
- Automated or human approval workflows.
- Semantic grading and LLM-as-judge evaluation.
- Output-schema conformance and parser-contract tests.
- Full workflow and end-to-end integration tests.
- Knowledge-base quality evaluation.
- Production privacy, retention, access-control, and redaction requirements.

## Complexity Assessment

| Workstream | Complexity | Main consideration |
|---|---|---|
| Trace generalization | Medium | Preserve Ask while supporting Azure OpenAI feature calls |
| Feature registry and replay dispatch | Medium | Different inputs and renderers, but no workflow execution |
| Prompt extraction and idempotent seeding | Medium | Preserve effective prompts and shared playbooks |
| Ask completion | Low | Existing implementation is the reference path |
| Learn migration | Medium | Background execution and conditional JSON repair |
| Prepare migration | Medium to high | Mode branches, large state inputs, retries, and background calls |
| Prompt Studio UI changes | Medium | Extend existing selection and comparison patterns |
| Replay verification | Medium | Confirm faithful capture/replay; no quality acceptance system yet |

The remaining complexity is implementation fidelity, not automated evaluation. The most involved feature is prospect reply because one provider request is assembled from a playbook, persona, mode-specific rules, transcript, language, scenario data, and optional retry context. It should be migrated after the simpler features establish the shared tracing and replay pattern.
