# Prompt Database Migration Design Decisions

This document records decisions and proposed edits discussed during review of
`prompt-database-migration-assessment.md`. The original assessment remains unchanged as
the baseline. After the review topics are complete, these decisions will be incorporated
into a final consolidated design document.

## Decision 1: Migration scope and replay boundary

**Status:** Agreed

### Decisions

1. Prompt Studio tests one logical AI invocation at a time.
   A user or server request may cause several independent AI calls, such as relevance
   classification, Ask, or support-email generation. Each call is traced and replayed as
   its own feature. Prompt Studio does not replay the complete UI or server request.

2. Preserve active AI behavior during migration.
   If an AI call is part of the currently used application, migrate its prompt, tracing,
   and replay behavior without deciding whether the feature should be redesigned,
   consolidated, or removed. Product cleanup is separate follow-up work.

3. Remove the Foundry Ask local-answer fallback.
   Foundry was selected as the Ask implementation. If the Foundry request fails, expose
   the error instead of silently invoking the old local grounded-chat implementation.
   This is the only currently approved behavior change associated with this migration.

4. Unused endpoints are not migration targets.
   Do not migrate prompts solely because an old endpoint still exists. Do not remove or
   refactor those endpoints during this work. Record endpoint retirement and dead-code
   cleanup as follow-up work.

5. Keep active Prepare fact-checking and coaching behavior unchanged.
   `prepare-fact-check` and `prepare-coaching`, including the coaching `moment` value,
   remain active independently replayable features. Whether their outputs should later be
   simplified or removed is not part of this migration.

### Terminology

- **Prompt invocation trace:** The saved record for one independently replayable AI call.
  This is the trace used by Prompt Studio.
- **Request or workflow trace:** A future diagnostic record connecting all activity caused
  by one UI or server request. It may correlate several prompt invocation traces, retrieval
  work, and other operations.
- **Correlation ID:** An optional link that can connect invocation traces to a future
  request or workflow trace. Replay must not depend on it.

### Active features added to the migration catalog

#### Ask relevance classifier

- Feature key: `ask-relevance`
- Prompt component: `ask.relevance`
- Captured inputs: latest message and supplied conversation history.
- Preserve the current fail-open behavior.
- Replay independently from the subsequent Ask invocation.

#### Ask support email

- Feature key: `ask-support-email`
- Prompt component: `ask.support_email`
- Captured inputs: triggering question, conversation history, escalation reason, and the
  source context supplied to the model.
- Replay does not rerun retrieval or Ask.

### Deferred work

- Full UI/server request-cycle tracing and correlated workflow replay.
- Retirement and removal of unused local answer/chat endpoints.
- Product cleanup of active AI calls whose outputs may be redundant or underused.
- Any broader redesign of Prepare fact checking, coaching, or its `moment` classification.

### Consequences for the final design

- Replace ambiguous uses of “feature trace” with “prompt invocation trace.”
- Inventory AI call sites rather than treating endpoints as the unit of migration.
- All active business AI calls migrate now; inactive endpoint-only calls remain untouched.
- Replay starts from captured inputs at the prompt boundary and does not execute upstream
  retrieval or earlier AI calls.
- Add removal of the Foundry local fallback to the implementation order and completion
  checks.

## Decision 2: Provider attempts within one prompt invocation

**Status:** Agreed

### Decisions

1. One prompt invocation trace represents one logical execution of a replayable feature,
   even when the provider is called more than once while fulfilling that invocation.

2. Store an ordered `provider_attempts` collection on the prompt invocation trace. Each
   attempt records the provider request and its response or error. Attempt metadata should
   identify why the attempt occurred, such as `primary`, `transport_retry`, or
   `compatibility_fallback`.

3. An identical retry after throttling or a temporary provider failure remains an attempt
   within the same invocation trace.

4. Learn's retry without JSON mode after the provider rejects JSON mode remains an attempt
   within the same invocation trace. It is a provider compatibility fallback for the same
   logical prompt execution.

5. A JSON-repair prompt is a separate `learn-json-repair` prompt invocation trace because
   it is an independently replayable prompt with different captured inputs. It may be
   correlated with the generator invocation that produced the malformed response, but its
   replay does not depend on that relationship.

6. Other logical AI calls caused by the same UI or server request, such as relevance
   classification and Ask, remain separate prompt invocation traces.

### Consequences for the final design

- Prompt Studio selects and replays logical prompt invocations, not individual HTTP
  attempts.
- Attempt history is diagnostic evidence shown within an invocation result; it does not
  create additional replay cases.
- Replace the current singular provider request/response assumption with an ordered attempt
  representation while preserving convenient access to the final outcome.

## Decision 3: Capture provider output without application response processing

**Status:** Agreed

### Decisions

1. A prompt invocation trace preserves the output of the prompt execution itself:

   - Ordered raw provider requests, responses, and provider errors in `provider_attempts`.
   - The model-authored text extracted from the successful provider response in
     `model_output`.

2. Provider-level extraction needed to expose the model-authored output is in scope. For
   example, joining Responses API output text blocks or exposing the Foundry answer text is
   part of interpreting the provider envelope, not testing application response handling.

3. Prompt Studio replay does not run feature-specific application response processors. It
   does not parse Learn objects into application models, normalize feedback scores, extract
   roleplay end markers, apply speech cleanup, or otherwise transform the model output into
   business state.

4. Application response parsing, normalization, validation, and error handling are tested
   with ordinary Python unit tests. Those tests may use recorded model outputs as fixtures,
   but they are not Prompt Studio replay cases.

5. Prompt Studio may provide generic presentation conveniences such as JSON syntax
   highlighting or pretty-printing when the model output is valid JSON. Presentation must
   not change the stored output, apply a feature schema, or classify the response as passed
   or failed.

6. A live invocation trace may record application-processing information already produced
   by the normal live path as optional diagnostics, but Prompt Studio replay does not invoke
   that processing and does not require a processed result.

7. Prompt Studio defaults to side-by-side model output, with complete provider requests and
   raw responses available as expandable diagnostics.

### Implementation guardrails

- Capturing the raw provider attempt and extracted model output is mandatory.
- Trace recording follows the configured strict or best-effort failure behavior defined in
  Decision 6.
- Feature adapters remain explicit; no generalized workflow or application
  response-processing engine is introduced.

### Complexity impact

This is lower risk than running application processors inside Prompt Studio. The incremental
work is limited to preserving raw provider envelopes, consistently extracting model-authored
text, and presenting both forms. Feature-specific response-handler coverage belongs in the
normal Python test suite.

## Decision 4: Use constrained prompt templates

**Status:** Agreed

### Decisions

1. Database prompt versions may contain the complete prompt component with explicit named
   runtime placeholders. This keeps important wording, response instructions, embedded
   output shapes, and the placement of runtime data versioned and testable.

2. Use a deliberately small template mechanism based on literal named placeholders, for
   example `<<topic>>`, `<<card_count>>`, and `<<source_context>>`.

3. Do not support loops, conditions, expressions, includes, executable code, or other
   general template-language behavior. Conditional behavior remains in the explicit
   feature adapter.

4. Every feature definition declares its allowed and required placeholders. Validate prompt
   versions against that declaration before selection and replay. A version with a missing,
   unknown, or malformed placeholder cannot become the selected live version.

5. Rendering must fail clearly if required runtime input is unavailable. The error is
   recorded as a processing or rendering error without making a provider request.

6. Runtime values are inserted literally. Do not add escaping or transformation that would
   change the effective provider prompt unless the existing live renderer already performs
   it.

7. Shared prompt components, such as `learn.persona`, remain independently versioned. The
   feature adapter explicitly defines component ordering and renders the applicable
   components into the final provider request.

### Consequences for the final design

- Version 1 templates should reproduce the existing rendered provider prompts as closely as
  possible for representative runtime inputs.
- Add characterization tests comparing current and migrated rendering before switching each
  live feature to database prompt resolution.
- Prompt Studio should show the declared placeholders and validation errors when creating or
  inspecting a prompt version.
- Template validation is structural only; it does not evaluate the quality or schema
  correctness of model output.

## Decision 5: Replay preserves the original component branch

**Status:** Agreed

### Decisions

1. A prompt invocation trace records only the prompt components that actually participated
   in that invocation.

2. Captured runtime inputs fix conditional feature behavior during replay. Replay changes
   prompt-component versions; it does not change the captured mode, enable an absent
   augmentation, or otherwise select a different application branch.

3. For `prepare-prospect-reply`, a callback trace includes the callback component and not
   the meeting component. A meeting trace includes the meeting component and not the
   callback component. Prompt Studio offers version choices only for the active component.

4. For Ask, About Me and memories components participate in replay only if they
   participated in the source invocation. Replay does not add an augmentation that was
   absent from the source trace.

5. Values such as mode, language, persona data, and `previous_reply` are captured runtime
   inputs. They are reused unchanged and are not represented as selectable prompt versions.

6. Each replay case must provide one version for every component in the source invocation's
   effective recipe and no versions for components outside that recipe.

### Consequences for the final design

- Feature registry entries may declare optional or mutually exclusive components and the
  runtime-input rule that selects them.
- Recipe validation is trace-specific rather than requiring every component declared by the
  feature.
- Testing a different mode or enabling a different augmentation requires a separate source
  invocation; Prompt Studio does not synthesize that application state.

## Decision 6: Configurable trace-persistence failure behavior

**Status:** Agreed

### Decisions

1. Add an application setting for prompt-trace persistence failure behavior. Prefer an
   explicit mode such as `PROMPT_TRACE_FAILURE_MODE=strict|best_effort` over an ambiguous
   boolean.

2. Default the setting to `strict` during development. If creating or updating a prompt
   invocation trace fails, the live feature call fails visibly. This calls broken tracing
   to a developer's attention without requiring a separate tracing-health check.

3. Allow demo environments to set the mode to `best_effort`. In that mode, trace
   persistence failures are logged clearly but do not stop the live feature call. Tracing
   remains enabled and continues to be attempted; only the fail-on-trace-error behavior is
   relaxed.

4. Prompt Studio replay execution is always strict regardless of the live-call setting.
   Persisting the source, case, attempts, and outcome is part of the requested test. An
   unrecorded or partially recorded replay must fail visibly.

5. Failure to resolve or render a selected prompt is not a tracing failure and always fails
   the invocation. Provider and application-processing failures retain their existing live
   behavior and are recorded when tracing is available.

6. In `best_effort` mode, logging must include the feature key, invocation/request
   identifier when available, trace operation that failed, and exception information. Do
   not silently suppress trace failures.

### Consequences for the final design

- Document the strict development default and recommended best-effort demo override in the
  environment example and deployment instructions.
- Focused tests must verify both modes and confirm that Prompt Studio remains strict.
- Trace persistence should be isolated from provider execution so best-effort mode can
  continue safely after a trace write fails.

## Decision 7: Record model metadata without model-selection controls

**Status:** Agreed

### Decisions

1. Capture the provider, requested model or deployment, actual model metadata returned by
   the provider, and applicable generation parameters for every live and replayed prompt
   invocation.

2. Model metadata is informational in this phase. Prompt Studio displays what the source
   invocation used and what each replay actually used, but it does not select, pin, restore,
   or validate model configuration.

3. Replay uses the application's current provider and model configuration at execution time.
   It does not attempt to match the source trace's model and does not prevent execution when
   the current configuration differs.

4. Credentials and service endpoints come from the current environment and are never stored
   in traces or replay configurations.

5. If the currently configured model cannot execute the feature or support a required
   provider option, the replay case fails with the provider's visible compatibility error.
   Do not silently weaken the request.

6. For Foundry, record the agent and metadata exposed by the API. Changes to the agent's
   externally managed knowledge base or server-side configuration are accepted limitations
   on exact reproducibility in this phase.

### Consequences for the final design

- Results display source and replay model metadata so reviewers can interpret differences.
- Saved configurations and replay cases do not contain a selectable model target.
- Model-target administration, allowlists, automatic model matching, and model-comparison UI
  are deferred enhancements.

## Decision 8: JSON-mode failure and repair behavior

**Status:** Agreed

### Decisions

1. Remove Learn's compatibility fallback that catches an HTTP 400 from JSON mode and retries
   the same prompt without JSON mode. If a configured model does not support the required
   JSON option, fail the invocation or replay case visibly.

2. Retain `learn-json-repair` as a separate independently replayable prompt feature. If the
   existing application invokes repair after model output cannot be parsed, create a new
   prompt invocation trace with the malformed model output as captured runtime input.

3. Correlate the repair invocation with the generator invocation for diagnostics, but do not
   make replay depend on that relationship.

4. Record the generator's raw provider response, extracted model output, and processing
   error before invoking repair. Repair must not erase the evidence that caused it.

5. Do not introduce strict Structured Outputs or new feature-specific JSON Schemas during
   this migration. That is output-schema work and remains deferred.

6. Preserve current handling of refusals, truncation, and content-filter interruption during
   the migration unless a change is required to remove the compatibility fallback. More
   explicit outcome handling may be added as follow-up work.

### Consequences for the final design

- A model that rejects JSON mode is shown as incompatible rather than receiving a plain-text
  retry.
- JSON repair remains visible as its own prompt test rather than an invisible provider
  attempt.
- The attempt model from Decision 2 no longer needs a `compatibility_fallback` attempt for
  Learn JSON mode, but remains applicable to genuine transport retries and any other
  retained same-invocation provider attempts.

## Decision 9: Prompt Studio replay is side-effect-free

**Status:** Agreed

### Decisions

1. Prompt Studio exercises prompts, not complete application workflows. Replay is limited to
   resolving the selected prompt versions, rendering them with captured inputs, invoking the
   selected provider model, extracting the model-authored output from the provider envelope,
   and saving Prompt Studio records.

2. Replay does not update normal application data, including Learn mixes, personas,
   roleplay sessions, transcripts, user profiles or memories, history, progress, or
   statistics.

3. Replay does not trigger Azure Speech, background verification, email delivery, earlier or
   later AI features, retrieval, or other downstream processing.

4. Replay does not call the live endpoint or workflow handler. Live code and replay share
   prompt resolution and rendering functions, but orchestration and business-state mutation
   remain outside the replay adapter.

5. The only intended persistent effects of replay are Prompt Studio configurations, runs,
   cases, prompt invocation traces, and provider-attempt records.

6. Provider usage, latency, and cost are expected effects because replay intentionally calls
   the selected model.

7. JSON repair, relevance classification, fact checking, coaching, and other prompts are
   replayed only when explicitly selected as the feature under test. Replaying a generator
   does not automatically invoke its repair prompt or any other related feature.

### Testing boundary

- Prompt Studio supports manual assessment of the request and model response.
- Python unit tests verify feature-specific response parsing, normalization, validation, and
  application behavior.
- Application integration and end-to-end tests verify orchestration across prompts and other
  services in a later phase.

## Decision 10: Capture complete render-ready input snapshots

**Status:** Agreed

### Decisions

1. Prompt invocation traces store complete snapshots of the runtime values supplied at the
   prompt boundary. Replay renders from those captured values and does not resolve mutable
   application records.

2. Preserve the exact ordering and subset used by the original invocation. Capture the
   transcript slice actually supplied, ordered source excerpts, selected memories, resolved
   preferences, persona snapshot, and other effective inputs rather than broader current
   application state.

3. Capture the full source text supplied to the model, together with citations and useful
   display metadata. Replay never reruns retrieval, rechunks documents, or reloads evidence
   from the knowledge base.

4. Application identifiers such as document, persona, session, or user IDs may be retained
   as diagnostic metadata, but replay must not depend on them and must not dereference them.

5. Continue storing the exact rendered provider request as the audit record for the original
   invocation. Structured captured inputs exist to render alternate prompt versions; the
   original rendered request exists to show exactly what was sent.

6. Additional trace storage is accepted for this demo. Production retention, redaction,
   access-control, and sensitive-data policy remain deferred as already established.

### Consequences for the final design

- Feature definitions must declare and serialize their complete render inputs.
- Tests should prove replay does not call retrieval or application state loaders.
- Captured source context and other large fields remain JSON/text data and are not replaced
  by pointers to current business records.

## Decision 11: Store provider attempts as child records

**Status:** Agreed

### Decisions

1. Store one logical prompt execution in the prompt invocation trace table and one row per
   provider attempt in a child table rather than embedding an attempts array in the parent
   trace.

2. Each provider-attempt row records at least:

   - Invocation identifier and ordered attempt number.
   - Attempt reason, such as `primary` or `transport_retry`.
   - Provider, requested model/deployment, and actual returned model metadata when present.
   - Start and completion timestamps.
   - Exact provider request.
   - Raw provider response or provider error.

3. Enforce unique attempt ordering within an invocation and cascade-delete attempt records
   only when their parent invocation trace is deleted.

4. Create the attempt row before sending the provider request, then complete it with the
   response or error. This preserves partial diagnostic evidence if execution is interrupted.

5. Prompt Studio presents attempts as expandable diagnostics beneath one logical invocation.
   Attempts are not selectable source traces or independent replay cases.

### Consequences for the final design

- The parent invocation trace contains feature key, prompt recipe, captured runtime inputs,
  correlation metadata, overall outcome, and extracted model output.
- Provider request/response/error fields move to the attempt-level contract for new traces.
- Query and API serializers load attempts in their recorded order.
- This adds a table and join but avoids rewriting large JSON arrays and supports reliable
  incremental attempt recording.

## Decision 12: Start fresh with provider-neutral trace and test history

**Status:** Agreed

### Decisions

1. Create the new provider-neutral prompt invocation, provider-attempt, and Prompt Studio
   test structures without migrating or backfilling existing Ask trace and replay history.

2. Switch live tracing and Prompt Studio APIs to the new structures at cutover. Existing
   Foundry-specific trace sessions, request traces, saved configurations, runs, and cases do
   not appear in the new Prompt Studio history.

3. Leave legacy trace/test tables and their test/dev data untouched during this migration.
   Their eventual removal is part of the already deferred dead-code and legacy-data cleanup.

4. Do not build compatibility reads that merge legacy and new trace histories. The new
   contract starts clean and all newly recorded features use it consistently.

5. This decision applies to trace and replay history only. Existing immutable prompt
   definitions and versions are not discarded or overwritten; prompt seeding continues to
   follow the existing idempotent rules.

### Consequences for the final design

- No one-time trace transformation or partial legacy-attempt backfill is required.
- New table and API names can be provider-neutral without aliases for old history.
- Tests should use the new tables directly and should not depend on existing Foundry trace
  fixtures except where preserving old code during cutover temporarily requires it.

## Decision 13: Structural validity is the only prompt activation gate

**Status:** Agreed

### Decisions

1. Validate constrained-template structure when creating an immutable prompt version.
   Reject versions with malformed placeholders, missing required placeholders, unknown
   placeholders, or other rendering-contract violations. An invalid immutable version is
   not useful and should not be stored as a draft.

2. Any structurally valid prompt version may be used in Prompt Studio replay and may be
   explicitly selected as the live version.

3. Do not require a replay run, human approval record, passing result, score, or other
   behavioral gate before live selection.

4. Selecting a live version remains an explicit user action and takes effect immediately.
   If the component is shared, the UI should identify the currently registered features
   affected by the selection before confirmation.

5. Prompt Studio may display recent replay history as informational context, but it does not
   interpret that history or use it to permit or prevent activation.

6. Model-specific provider compatibility is determined when the model is invoked; structural
   template validation does not claim that every configured model supports every provider
   option.

### Scope boundary

Approval workflow, draft lifecycle, promotion environments, automated evaluation, quality
scoring, and pass/fail activation rules remain undefined future features. They will be
considered incrementally rather than anticipated in this migration.

## Decision 14: Keep Prompt Studio UI changes minimal

**Status:** Agreed

### Prompts area

- List prompt components and immutable versions.
- Show declared required and allowed placeholders.
- Create structurally valid versions and explicitly select a live version.
- Show which registered features use a shared component.

### Traces area

- List individual prompt invocations with feature filtering.
- Show captured input snapshots, exact prompt recipe, rendered provider request, model
  metadata, and extracted model output.
- Show ordered provider attempts, raw responses, and errors as expandable diagnostics.
- Do not group invocations into a complete UI or server workflow.

### Tests area

- Select one feature and one source prompt invocation.
- Define explicit complete prompt-version recipes.
- Replay each recipe independently with the captured inputs and current model configuration.
- Compare model outputs side by side and expose raw attempt diagnostics on demand.
- Do not run application response processors or related prompts.

### Explicit exclusions

- No model selector or model-target administration.
- No scoring, approval, pass/fail, or promotion controls.
- No workflow, integration-run, or correlated request-cycle view.
- No feature-specific output viewer beyond generic text/JSON presentation.
- No new Prompt Studio area or navigation concept is required.

### Existing UI correction

Replay configuration and comparison currently live in `ui/tests.html`, while
`ui/prompts.html` manages prompt versions. The final design must refer to the correct page
and extend the existing Prompts, Traces, and Tests areas rather than attributing all behavior
to `ui/prompts.html`.
