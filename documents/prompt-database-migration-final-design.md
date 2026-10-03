# Prompt Database Migration Final Design

## Status

Approved implementation design for the demo-phase prompt database migration.

This document consolidates the original assessment and the decisions recorded during review.
It is the implementation source of truth. The original assessment and design-decision log
remain as historical context.

## Objective

Move every active, day-to-day business prompt into the existing immutable prompt-version
model and make each logical AI invocation independently traceable and replayable in Prompt
Studio.

Prompt Studio is a prompt unit-testing workspace. It renders selected prompt versions with a
captured input snapshot, invokes the currently configured provider/model, and presents the
request and model response for manual review. It does not rerun application workflows or
test application response handlers.

## Scope principles

1. One feature is one independently replayable logical AI invocation.
2. A UI or server request may cause multiple feature invocations. Each is traced separately.
3. Replay starts at the prompt boundary with captured values. It does not rerun retrieval,
   earlier prompts, or state loaders.
4. Replay is side-effect-free outside Prompt Studio trace and test records.
5. Active behavior is preserved unless this design explicitly changes it.
6. Unused endpoint-only prompts are not migration targets and their endpoints are not removed.
7. Prompt quality is assessed manually. There is no scoring, approval, or pass/fail system.
8. Application response processing is covered by Python unit tests, not Prompt Studio.

## Approved behavior changes

### Remove the Foundry local-answer fallback

If Foundry Ask fails, surface the failure. Do not silently invoke the previous local
grounded-chat implementation. The local `/v1/answer` and `/v1/chat` endpoints remain
untouched but do not enter migration scope solely because their code exists.

### Remove the Learn JSON-mode compatibility downgrade

If the configured model rejects the required JSON-mode request, surface the compatibility
error. Do not retry the same prompt without JSON mode. Existing JSON repair remains a
separate prompt feature for malformed model output produced by the live application.

## Terminology

### Prompt component

An immutable, versioned template block such as `learn.persona` or
`prepare.prospect.reply_rules`.

### Runtime input snapshot

The complete, ordered, render-ready values supplied to one prompt invocation. Snapshots hold
actual values rather than references to mutable application records.

### Prompt recipe

The exact prompt-component versions that participated in an invocation:

```json
{
  "learn.persona": 2,
  "learn.article": 4
}
```

Optional and mutually exclusive components appear only when they participated in the source
invocation.

### Prompt invocation trace

The provider-neutral record for one logical AI feature execution. It contains the feature,
recipe, captured inputs, rendered prompt, provider/model metadata, model-authored output,
overall outcome, and ordered provider attempts.

### Provider attempt

One HTTP/provider execution belonging to a prompt invocation. Transport retries remain
attempts under the same invocation.

### Request or workflow trace

A possible future diagnostic record connecting all work caused by one UI/server request.
This migration may retain an optional correlation ID, but does not build request-cycle
tracing or replay.

## Active feature catalog

The implementation must verify this catalog against the call sites before prompt extraction.
Embedding calls, Azure Speech, and the roleplay keep-warm input are excluded.

### Ask relevance

- Feature: `ask-relevance`
- Components: `ask.relevance`
- Inputs: latest message and the conversation history supplied to classification.
- Preserve the current fail-open behavior.
- Replay does not invoke Ask afterward.

### Ask

- Feature: `ask`
- Components: `ask.navigator`, optionally `ask.about_me`, optionally `ask.memories`, and
  `ask.answer_style` if its current preference mapping can be represented without changing
  behavior.
- Inputs: question, history, preferences, About Me snapshot, memory snapshot, user ID, and
  applicable agent metadata.
- If answer-style extraction would introduce unnecessary template branching, document it as
  stable application-owned assembly for this phase.
- Foundry errors are visible; no local-answer fallback runs.

### Ask support email

- Feature: `ask-support-email`
- Components: `ask.support_email`
- Inputs: triggering question, conversation history, escalation reason, and exact supplied
  source context.
- Replay drafts model output only; it performs no retrieval or delivery.

### Learn query planner

- Feature: `learn-query-planner`
- Components: `learn.query_planner`
- Inputs: topic and requested content kind.
- Curriculum items with pinned sources skip this feature and therefore create no planner
  invocation trace.

### Learn article

- Feature: `learn-article`
- Components: `learn.persona`, `learn.article`
- Inputs: topic, length targets, optional curriculum brief, and exact ordered source context.

### Learn flashcards

- Feature: `learn-flashcards`
- Components: `learn.persona`, `learn.flashcards`
- Inputs: topic, card count, optional curriculum brief, and exact ordered source context.

### Learn audio script

- Feature: `learn-audio`
- Components: `learn.persona`, `learn.audio`
- Inputs: topic, host names, turn/word targets, optional curriculum brief, and source context.
- Replay does not invoke Azure Speech.

### Learn JSON repair

- Feature: `learn-json-repair`
- Components: `learn.json_repair`
- Input: malformed model output.
- It is a separate invocation correlated with, but not dependent on, the generator trace.
- Replaying a generator does not automatically invoke repair.

### Prepare custom persona

- Feature: `prepare-persona`
- Components: `prepare.playbook.persona`, `prepare.persona_generator`
- Inputs: scenario notes and all values interpolated into the request.
- Replay does not persist the generated persona.

### Prepare prospect reply

- Feature: `prepare-prospect-reply`
- Components always used: `prepare.playbook.persona`, `prepare.prospect.reply_rules`.
- Conditional component: exactly one of `prepare.prospect.callback` or
  `prepare.prospect.meeting`, selected by captured mode.
- Inputs: persona snapshot, scene, objective, notes, transcript slice, latest agent message,
  language, mode, and optional previous reply.
- A duplicate-response retry is another invocation of this feature with `previous_reply`
  populated.
- Replay does not append a turn or trigger background work.

### Prepare coaching

- Feature: `prepare-coaching`
- Components: `prepare.playbook.sales`, `prepare.coaching`
- Inputs: persona summary, transcript slice, latest agent message, and supplied source
  context.
- Preserve the current `moment` output and other current behavior without redesign.

### Fact check

- Feature: `prepare-fact-check`
- Components: `prepare.fact_check`
- Inputs: claim and exact evidence supplied to the model.
- The same independently replayable feature may be invoked from Prepare background
  verification, standalone fact check, or transcript checking.
- Transcript checking creates one invocation per checked statement.
- Replay does not perform retrieval or check other statements.

### Prepare Navigator

- Feature: `prepare-navigator`
- Components: `prepare.playbook.sales`, `prepare.navigator`
- Inputs: persona summary, transcript slice, agent question, and exact source context.

### Prepare feedback

- Feature: `prepare-feedback`
- Components: `prepare.playbook.feedback`, `prepare.feedback`
- Inputs: persona/evaluation snapshot, outcome, transcript, and verification-event snapshot.
- Replay does not persist a completed session or update statistics.

## Constrained prompt templates

Prompt versions contain complete component templates with literal named placeholders such as
`<<topic>>` and `<<source_context>>`.

The template mechanism supports only literal replacement:

- No loops, conditions, expressions, includes, or executable code.
- Each component declares its required and allowed placeholders.
- Unknown, missing, or malformed placeholders reject version creation.
- Rendering replaces placeholders once and does not recursively interpret inserted values.
- Runtime values are inserted literally unless the existing live renderer already performs a
  transformation.
- Feature adapters explicitly own component ordering and conditional component selection.

Version 1 characterization tests must demonstrate that representative migrated requests
match the current effective provider prompts as closely as possible.

## Prompt version lifecycle

- Versions are immutable.
- Seed version 1 idempotently without overwriting existing versions.
- Never change an existing selected version during startup.
- Structural validity is the only enforced creation or activation gate.
- Any structurally valid version may be replayed or explicitly selected live.
- Replay, approval, score, or pass/fail status is not required before selection.
- When selecting a shared component, the UI identifies registered features using it.

## Feature registry

Use one explicit registry rather than a workflow engine. Each definition supplies only what
prompt-unit execution needs:

- Feature key, display name, and purpose.
- Required, optional, and mutually exclusive components.
- Component placeholder contracts.
- A rule that determines the effective recipe from captured runtime inputs.
- Renderer that creates the provider request from templates and captured inputs.
- Provider invocation adapter.
- Provider-envelope output extraction.
- Enough input validation to avoid constructing an invalid provider request.

The registry does not describe stage ordering, dependencies, parent/child execution,
business-state mutation, application response processing, or workflow branching.

## Trace data model

Start fresh with provider-neutral trace and replay tables. Do not migrate existing Ask trace,
configuration, run, or case history. Leave legacy Foundry-specific tables untouched for
later cleanup. Existing prompt definitions and versions remain intact.

### Prompt invocation

The parent record contains at least:

- Invocation ID.
- Feature key.
- Origin such as `live` or `replay`.
- Optional correlation ID and diagnostic application identifiers.
- Started/completed timestamps and overall status.
- Exact prompt recipe.
- Complete runtime input snapshot.
- Flattened readable prompt.
- Provider and requested/actual model metadata available at invocation level.
- Extracted model-authored output.
- Invocation/rendering error when no provider attempt could be completed.

### Provider attempts

Store attempts in a child table with:

- Invocation ID and unique ordered attempt number.
- Reason such as `primary` or `transport_retry`.
- Provider, requested deployment/model, and actual returned model metadata.
- Started/completed timestamps.
- Exact provider request.
- Raw provider response or provider error.

Create an attempt row before the provider call and complete it afterward so interrupted work
leaves partial evidence. Prompt Studio displays attempts under their parent invocation; an
attempt is never a selectable source test by itself.

### Model metadata

Model information is diagnostic only:

- Record what live and replayed invocations actually used.
- Replay uses current application configuration.
- Do not match, pin, validate, or select a model from the source trace.
- Do not add model administration, allowlists, or comparison controls.
- Never persist credentials or secret-bearing endpoints.

## Trace failure behavior

Add `PROMPT_TRACE_FAILURE_MODE=strict|best_effort`:

- Default development behavior is `strict`; a trace persistence failure fails the live
  operation visibly.
- Demo deployments may use `best_effort`; tracing is still attempted and failures are logged,
  but the live feature continues.
- Prompt Studio replay is always strict because an unrecorded test is incomplete.
- Failure to resolve/render a prompt or call the provider is not a trace-storage failure and
  retains its normal visible behavior.
- Best-effort logs identify the feature, invocation when available, failed trace operation,
  and exception.

## Input snapshot rules

- Capture values, not references.
- Preserve order and the exact subset supplied to the prompt.
- Store the actual transcript slice, persona data, preferences, memories, and evidence text.
- IDs may be retained only as diagnostic metadata and are never dereferenced during replay.
- Store the original rendered request for audit and structured inputs for rerendering.
- Additional demo trace size is accepted.

## Replay execution

For each replay case:

1. Load one source prompt invocation.
2. Reuse its captured runtime input snapshot unchanged.
3. Preserve the source invocation's conditional branch and participating component set.
4. Resolve one selected version for every participating component and no inactive component.
5. Render with the constrained template renderer.
6. Invoke only that logical prompt using current provider/model configuration.
7. Capture attempts, raw provider result/error, model metadata, and extracted model output.
8. Save the replay case and show it beside other cases.
9. Stop without application response processing or business-state mutation.

Replay never changes callback to meeting mode, enables an absent Ask augmentation, changes
captured language/persona/previous reply, refreshes evidence, or invokes a related prompt.

## Prompt Studio UI

Extend the existing areas only.

### Prompts (`ui/prompts.html`)

- Components, immutable versions, and live selection.
- Required/allowed placeholder display and structural errors.
- Registered features affected by a shared component selection.

### Traces (`ui/traces.html`)

- Invocation list with feature filter.
- Inputs, recipe, rendered request, model metadata, and model output.
- Expandable ordered attempts with raw responses/errors.
- No workflow/request-cycle grouping.

### Tests (`ui/tests.html`)

- Feature and source-invocation selection.
- Explicit complete prompt-version recipes.
- Independent execution and side-by-side model-output comparison.
- Expandable raw attempt diagnostics.
- No response processors, model selector, scoring, approval, or pass/fail controls.

## Application response testing boundary

Prompt Studio stops at provider-envelope output extraction. It does not parse Learn content
into application objects, normalize feedback, extract roleplay markers, apply speech cleanup,
or test other business handlers.

Feature-specific response handlers are tested with normal Python unit tests. Recorded model
outputs may be used as fixtures. End-to-end orchestration testing remains a later phase.

## Minimal shared implementation

Introduce only:

1. An explicit feature registry/definition contract.
2. A prompt invocation recorder that manages the parent trace and ordered attempts.

Do not introduce a workflow engine, provider plugin framework, dependency graph, event bus,
generic response-processing engine, new job framework, or general observability platform.

## Implementation order

1. Verify and document active generative call sites against the feature catalog.
2. Add the strict/best-effort trace setting and provider-neutral trace/attempt schema.
3. Implement constrained template parsing/validation/rendering and the minimal registry.
4. Remove the Foundry local-answer fallback and JSON-mode compatibility downgrade.
5. Adapt Ask to the new trace contract while preserving its prompt behavior.
6. Generalize replay storage/dispatch and the existing Prompt Studio pages.
7. Migrate Ask relevance and support email.
8. Migrate Learn query planner end to end.
9. Migrate remaining Learn generators and JSON repair.
10. Migrate Prepare persona and feedback.
11. Migrate Prepare Navigator, coaching, and fact check.
12. Migrate prospect reply last.
13. Add focused rendering, tracing, replay-isolation, and response-handler unit tests after
    each increment.

## Completion checks

- Every active business AI call maps to an independently replayable registered feature.
- Every active static prompt component is versioned or explicitly documented as stable
  application-owned assembly.
- Live features resolve selected prompt versions and record input snapshots and recipes.
- Invocation traces contain ordered attempts, raw provider evidence, model metadata, and
  extracted model output.
- Alternate prompt recipes replay against unchanged captured inputs.
- Replay causes no application side effects or related prompt invocations.
- Foundry failures are visible and never invoke the local fallback.
- JSON-mode incompatibility is visible and never downgrades to plain text.
- Strict and best-effort trace modes behave as documented; replay remains strict.
- Existing active Ask, Learn, and Prepare behavior remains operational except for the two
  approved fallback removals.
- Prompt Studio contains no evaluation, approval, workflow, model-selection, or application
  response-processing features.

## Deferred work

- Full UI/server request-cycle tracing and correlated workflow replay.
- Model selection, model-target management, and automated model comparison.
- Strict Structured Outputs and output-schema conformance testing.
- Automated evaluation, scoring, approval, promotion, or pass/fail rules.
- Application integration and end-to-end prompt workflow tests.
- Legacy trace/test history migration and old endpoint removal.
- Product cleanup of redundant or underused active AI behavior.
- Production privacy, redaction, access-control, and retention requirements.
- Knowledge-base evaluation, ingestion changes, embeddings, chunking, and Azure Speech.

## Initial complexity estimate

The overall migration remains high-complexity because it touches every active model boundary,
but the decisions above remove several speculative workstreams. A realistic implementation
range is approximately 24-36 engineer-days for one engineer familiar with the repository,
with the largest risk in faithful prompt extraction, background Prepare instrumentation, and
side-effect-free replay. Model management, response-processing integration, workflow tracing,
legacy migration, and automated evaluation are excluded from that estimate.
