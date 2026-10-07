# Learn and Ask Architecture Assessment and Implementation Handoff

**Application:** NLG RAG Demo / salesDJ  
**Primary focus:** Learn experience and Ask Navigator  
**Assessment date:** September 24, 2026  
**Assessment basis:** Repository source code, configuration, deployment documentation, and supplied architecture slides

---

## 1. Purpose of this document

This document is a self-contained handoff for continuing architecture and product work on the application's **Learn** experience and **Ask Navigator** chatbot.

It answers four questions:

1. How much of the proposed PowerPoint architecture is already implemented?
2. Which capabilities are production essentials, good additions, or longer-term enhancements?
3. Where does the current implementation diverge from the architecture shown in the slides?
4. What implementation path should the team follow next?

The supplied slides describe:

- A web application backed by FastAPI on Azure Container Apps.
- Azure AI Foundry agents and a grounded knowledge base.
- PostgreSQL for application and conversation data.
- A multi-agent answer-validation pipeline.
- Production controls for grounding, evidence, conflict handling, uncertainty, tracing, evaluation, security, latency, and cost.

The current repository contains meaningful implementations of many of those ideas. However, the production-control and multi-agent-validation portions of the slides are still primarily a target architecture rather than a description of the complete current system.

---

## 2. Executive assessment

The application is a strong functional prototype with a substantial Learn implementation and a real Foundry-connected Ask experience. It is suitable for demos and could support a controlled pilot after targeted hardening. It is not yet a fully governed production system for regulated, source-grounded product answers.

Directional completion estimates:

| Area | Estimated implementation | Assessment |
|---|---:|---|
| User-facing Learn and Ask experience | ~75% | Strong functional prototype |
| Platform architecture shown in slide 2 | ~65% | Core components exist, but grounding is split across two stacks |
| Multi-agent validation shown in slide 3 | ~30% | Fact-checking pieces exist but are not an Ask answer-release pipeline |
| Production controls shown in slide 4 | ~35% | Foundations exist; formal governance and observability are incomplete |
| Overall production readiness | ~40% | Controlled pilot candidate, not yet enterprise production ready |

These percentages are directional rather than formal maturity scores. They distinguish working code from controls that are only implied by prompts, UI copy, or architecture diagrams.

### Central conclusion

The most important finding is that the application currently has **two grounding paths**:

```text
Main Ask Navigator
  -> FastAPI
  -> hosted Azure AI Foundry KnowledgeBase agent
  -> Foundry-managed knowledge retrieval
  -> cited answer

Learn + local fact-check + transcript-check + in-call Ask
  -> FastAPI
  -> Azure OpenAI directly
  -> PostgreSQL/pgvector retrieval
  -> generated or checked result
```

Therefore, the statement that all Learn and Ask functionality is linked through Foundry is not currently accurate. The primary Ask experience is Foundry-backed, while Learn and several supporting experiences use the application's local RAG stack.

That split can work temporarily, but it must become an explicit architectural decision. Otherwise, the system will accumulate two indexes, two freshness models, two citation formats, two authorization boundaries, and potentially inconsistent answers.

---

## 3. Current application inventory

### 3.1 Frontend

The current primary application is a large static HTML/CSS/JavaScript page at:

- `ui/learn.html`

It is not presently a React application, even though the architecture slide labels the frontend as a React web app.

The application includes these major experiences:

- Home dashboard.
- Learn.
- Full-page Ask Navigator.
- Pull-up Ask Navigator sheet.
- Prepare/roleplay.
- Profile and settings.
- A legacy/local Coach console.

Ask conversation threads are stored in browser `localStorage`. The browser supports multiple threads, thread titles, pinning, searching, deletion with undo, Markdown export, answer preferences, and saved memories.

Relevant code locations:

- Local browser storage: `ui/learn.html`, near lines 841-844.
- Ask thread management: `ui/learn.html`, near lines 972-1020.
- Foundry Ask request: `ui/learn.html`, near lines 1052-1057.
- Ask citations and source rendering: `ui/learn.html`, near lines 1143-1167.
- Full Ask page and history drawer: `ui/learn.html`, near lines 1269-1355.

### 3.2 FastAPI backend

FastAPI is the central application backend and currently owns:

- Search and answer endpoints.
- Local conversational RAG.
- Foundry agent proxying.
- Fact-checking.
- Transcript checking.
- Corpus inspection.
- Learn content generation and audio rendering.
- Authentication gate.
- Roleplay sessions and feedback.
- Speech token issuance.
- Profile/settings endpoints.

The API is defined primarily in:

- `src/rag_layer/server.py`

Important endpoint groups include:

| Area | Endpoints |
|---|---|
| Local RAG | `/v1/search`, `/v1/answer`, `/v1/chat` |
| Foundry | `/v1/foundry/status`, `/v1/foundry/chat` |
| Verification | `/v1/fact-check`, `/v1/transcript-check` |
| Corpus inspection | `/v1/documents`, `/v1/documents/{id}/chunks` |
| Learn | `/v1/learn/status`, `/v1/learn/curriculum`, `/v1/learn/mixes`, seed, retry, delete, audio routes |
| Roleplay | Scenarios, sessions, turns, in-call Ask, feedback, metrics, and history |
| Identity/profile | Login, logout, current user, profile settings |
| Speech | Token and voice-profile endpoints |

### 3.3 Deployment

The repository documents deployment to Azure Container Apps. The application is packaged as one container containing the FastAPI backend and the static UI.

Deployment documentation identifies:

- Azure Container App: `nlg-rag-demo-ca`.
- Azure Container Registry repository.
- External Azure Database for PostgreSQL.
- Environment-variable and Container App secret configuration.
- Manual document ingestion through an executed CLI command inside the container.

Relevant files:

- `Dockerfile`
- `docker-compose.yml`
- `DEPLOY.md`
- `.env.example`

The deployed runtime was not independently verified during this assessment, so deployment-specific configuration may differ from the repository.

---

## 4. Current Learn implementation

Learn is one of the strongest areas of the application.

### 4.1 Implemented capabilities

The application currently supports:

- A 21-lesson starter curriculum.
- Three curriculum tiers:
  - Day-one/foundational material.
  - Deeper product mechanics.
  - Selling and client-conversation skills.
- Three generated formats:
  - Articles.
  - Flashcards.
  - Two-host audio episodes.
- Short and long variations.
- Custom topic creation.
- Recommended lessons.
- A learning-path interface.
- A personal mix library.
- Generated source lists.
- Flashcard source references.
- On-demand narration for articles.
- Azure Speech audio synthesis.
- Asynchronous generation states:
  - `queued`
  - `generating`
  - `ready`
  - `failed`
- Retry and delete operations.
- Detection of jobs interrupted by a server restart.

### 4.2 Curated curriculum retrieval

Curriculum lessons are not generated using unrestricted semantic retrieval. Each curated curriculum item identifies source documents and, where applicable, exact pages or chunk ranges.

This is a strong design choice for regulated or compliance-sensitive material because it makes the content inputs repeatable and reviewable.

The curriculum also includes a coverage brief describing the facts the lesson is expected to teach. Source excerpts take precedence if they conflict with the coverage brief.

Relevant code:

- Curriculum definitions: `src/rag_layer/curriculum.py`
- Pinned context retrieval: `src/rag_layer/learn.py`, `gather_pinned_contexts`
- Generation workflow: `src/rag_layer/learn.py`, `run_generation`

### 4.3 Custom Learn retrieval

For a user-created mix, the application:

1. Expands the topic into up to three focused retrieval queries.
2. Runs each query against local pgvector retrieval.
3. Deduplicates retrieved chunks.
4. Sorts them by similarity.
5. Passes the selected excerpts to Azure OpenAI.
6. Stores generated content and source metadata in PostgreSQL.

### 4.4 Learn grounding strengths

Current strengths include:

- Prompts explicitly prohibit unsupported product facts.
- Exact source excerpts are included in the generation context.
- Generated content retains source lists.
- Flashcard source numbers can be resolved to the exact excerpts the model saw.
- Curriculum sources can be pinned.
- Source metadata includes page, zone, heading path, document name, and preview.

### 4.5 Learn gaps

The principal gaps are:

1. **No independent post-generation validation.**
   - A lesson is marked ready after the generation call succeeds.
   - There is no second pass that extracts material claims and verifies each claim against its cited excerpt.

2. **No approval workflow.**
   - There are no draft, validated, approved, published, rejected, or expired content states.

3. **No source-version lifecycle.**
   - A generated lesson does not record a governed source-package version or automatically become stale when a source changes.

4. **No jurisdiction or product-variant enforcement.**
   - Some curriculum scope decisions, such as excluding New York material, are encoded in content design rather than a reusable policy layer.

5. **No per-user progress.**
   - The system uses a shared login and cannot reliably track individual mastery, completion, or spaced repetition.

6. **No durable job queue.**
   - Generation uses an in-process thread pool. A restart can orphan work and is not equivalent to a managed queue with leases and retries.

7. **No quality evaluation suite.**
   - There are no checked-in tests for lesson completeness, factual correctness, source accuracy, or format conformance.

8. **Placeholder video library.**
   - The UI contains a video catalog, but source videos are not wired to actual media.

---

## 5. Current Ask implementation

### 5.1 Main Ask Navigator

The primary Ask page and home-page Ask surface call the hosted Foundry agent through:

```text
POST /v1/foundry/chat
```

The FastAPI proxy sends current conversation history to the Foundry Responses-compatible endpoint. The Foundry response parser extracts:

- Final response text.
- URL citation annotations.
- Source title and URL.
- Model identifier.
- Foundry response identifier.
- Response status.

The UI displays inline citation numbers and a source list under each answer.

Relevant files:

- `src/rag_layer/foundry.py`
- `src/rag_layer/server.py`
- `ui/learn.html`

### 5.2 Ask user experience

The Ask interface already contains several polished features:

- Multiple conversation threads.
- Persistent browser-local history.
- Searchable thread history.
- Rename, pin, export, delete, and undo.
- Answer length preferences.
- Format and tone preferences.
- Plain-language option.
- Always-cite-sources preference.
- User role/market context.
- Saved memories.
- Voice input.
- Source expansion.
- Copy action.
- Helpful/not-helpful feedback control.

### 5.3 Ask limitations

1. **History is not durable application data.**
   - Main Ask threads are stored in browser `localStorage`.
   - They are not available across devices and cannot be centrally governed, audited, or analyzed.

2. **No durable Foundry threading.**
   - Conversation history is replayed with each request.
   - The returned Foundry `response_id` is not used to continue a durable server-side conversation.

3. **No answer-release validation.**
   - Foundry returns an answer and citations, and the application generally displays them directly.
   - A cited answer is not necessarily a fully supported answer.

4. **No claim-to-citation validation.**
   - The application does not verify that every material claim is supported by the cited source.

5. **No source-governance check.**
   - There is no application-level enforcement for effective date, product version, jurisdiction, authority, or approval status.

6. **No confidence/disposition model.**
   - Answers are not classified as release, qualify, clarify, escalate, or hold.

7. **Feedback is not centrally captured.**
   - User feedback is largely part of the browser-local message state rather than a server-side improvement dataset.

8. **No complete trace.**
   - The Foundry response identifier, model, sources, latency, prompt version, validation findings, and user feedback are not saved together in a durable trace.

### 5.4 In-call Ask differs from main Ask

Ask Navigator inside a roleplay session does not use the same Foundry path. It uses the local pgvector RAG service, includes recent roleplay transcript context, and calls Azure OpenAI directly.

This can produce a different answer or source set from the full Ask tab.

Relevant implementation:

- `src/rag_layer/roleplay.py`, `answer_navigator_question`

---

## 6. Local RAG implementation

The application contains a complete local retrieval stack alongside Foundry.

### 6.1 Ingestion and extraction

Supported document types include:

- PDF.
- DOCX.
- PPTX.
- XLSX.
- CSV.
- HTML.
- Plain text.

PDF extraction is layout-aware and attempts to preserve:

- Reading order.
- Headings.
- Page boundaries.
- Borderless tables.
- Disclosure zones.
- Repeated-furniture removal.

### 6.2 Storage and retrieval

The database contains:

- `rag_documents`
- `rag_chunks`
- pgvector embeddings
- An HNSW cosine-similarity index
- Document metadata and chunk provenance

The retrieval flow is:

```text
query
  -> Azure OpenAI embedding
  -> pgvector cosine search
  -> top chunks
  -> answer, fact-check, Learn generation, or roleplay guidance
```

### 6.3 Local RAG endpoints

The system exposes:

- Search-only retrieval.
- Grounded question answering.
- Conversational local RAG.
- Claim fact-checking.
- Transcript fact-checking.
- Corpus inspection.
- Per-document chunk inspection.

### 6.4 Local RAG strengths

- Strong chunk provenance.
- Layout-aware PDF processing.
- Content-hash deduplication.
- Human-inspectable corpus endpoints.
- Explicit supported/contradicted/not-addressed verdicts.
- Retrieval and generation are separated into reusable services.

### 6.5 Local RAG weaknesses

- Ingestion is currently a manual operational command.
- There is no scheduled synchronization or stale-source monitor.
- Deleted source documents may not automatically disappear from the index.
- There are no retrieval-quality benchmarks.
- Similarity is returned but not used as an enforceable evidence threshold.
- General semantic retrieval may not enforce product, jurisdiction, or effective-date filters.
- It duplicates capability provided by the Foundry knowledge solution.

---

## 7. Slide 2: Platform architecture mapping

| Slide component | Current implementation | Status | Notes |
|---|---|---|---|
| React web app | Static HTML/CSS/JavaScript application | Different | Functional, but not React |
| Conversational UX | Full Ask page, sheets, history, voice, preferences | Implemented | Strong prototype UX |
| FastAPI backend | API, business logic, generation, roleplay, auth gate | Implemented | Correctly acts as application control point |
| Azure Container Apps | Deployment runbook and container image | Implemented | Runtime not independently verified here |
| Foundry Agent | Hosted `KnowledgeBase` agent | Implemented | Used by main Ask |
| Foundry grounded retrieval | Foundry citations returned | Implemented for main Ask | Not used by Learn or all Ask surfaces |
| PostgreSQL application data | Learn, roleplay, settings, RAG corpus | Implemented | Also serves as vector database |
| Conversation records | Roleplay durable; main Ask browser-local | Partial | Needs a server-side chat model |
| Identity | Shared credential and process-local session token | Demo only | Not enterprise identity |
| Secrets | Environment/Container App secrets | Partial | Static API keys remain in application configuration |
| Network policy | Not expressed in repository/IaC | Not evidenced | Requires infrastructure review |
| Telemetry | Console timing logs and client timing endpoint | Partial | No central trace model |

### Slide 2 conclusion

The slide is broadly directionally correct, except for three details:

1. The frontend is not React.
2. Foundry is not the single reasoning and retrieval layer for the entire product.
3. Shared production controls are much less complete than the slide implies.

---

## 8. Slide 3: Multi-agent answer validation mapping

The slide proposes:

```text
Question
  -> specialist answer agent
  -> parallel citation validator
  -> parallel factuality validator
  -> parallel context scout
  -> synthesis reviewer
  -> release or hold
```

### 8.1 What currently exists

- A Foundry answer agent for main Ask.
- Foundry-provided citations.
- A standalone local fact-check service.
- Transcript fact-checking.
- Background verification of roleplay agent statements.
- Final roleplay coaching that consumes verification events.
- Source-backed guidance for in-call Ask.

### 8.2 What is not currently implemented

- No independent citation validator for main Ask.
- No claim-by-claim factuality validator in the main Ask response path.
- No context scout identifying caveats, adjacent topics, or missing facts.
- No synthesis reviewer reconciling validator findings.
- No automatic revision loop.
- No formal release-versus-hold disposition.
- No minimum evidence policy.
- No durable answer-validation record.
- No calibrated confidence model.

### 8.3 Existing roleplay validation

Roleplay is the closest implementation to the proposed model. After each agent turn, a background thread performs retrieval, fact-checking, and coaching analysis. Results are accumulated as verification events and later passed to the coaching-report generator.

This is useful but differs from slide 3 because:

- Verification happens after the spoken response path.
- The verification does not gate the main Ask answer.
- Coaching and factual verification share parts of the same processing path.
- There is no independent final reviewer issuing a release/hold decision.

### 8.4 Recommended validation design

Do not begin by running four separate agents for every question. That would increase cost, latency, failure modes, and operational complexity.

Use a risk-based pipeline:

```text
User question
  -> retrieve and draft
  -> deterministic checks
       - did retrieval return eligible sources?
       - are citations present and resolvable?
       - does the answer contain material product claims?
       - do numerical claims have direct evidence?
       - do sources match product, jurisdiction, and effective date?
  -> risk classification
       - low risk -> release
       - material claim -> evidence verifier
       - conflicting/weak evidence -> clarify, qualify, or hold
  -> optional single revision
  -> final disposition and trace
```

Recommended initial logical components:

1. One answer agent.
2. One evidence-verification operation.
3. Deterministic policy code.
4. One optional revision call.

The logical roles can later become separate agents if evaluation data demonstrates a benefit.

---

## 9. Slide 4: Production controls mapping

### 9.1 Grounding contract

**Current status: Partial**

Implemented:

- Prompts instruct models to use supplied sources.
- Main Ask displays Foundry citations.
- Learn stores source metadata.
- Curated lessons pin source sections.
- Local fact-checking can classify claims.

Missing:

- Formal definition of a material claim.
- Claim-level evidence mapping.
- Source eligibility rules.
- Required authority/effective-date/jurisdiction metadata.
- Automated rejection of unsupported generated content.

### 9.2 Evidence threshold

**Current status: Mostly missing**

The local stack calculates similarity scores, but does not enforce a release rule based on them.

A production evidence policy should include:

- Minimum number of eligible sources.
- Minimum retrieval relevance.
- Direct support for numerical, eligibility, and benefit claims.
- Correct product/version/state match.
- Conflict detection.
- Clear behavior when evidence is insufficient.

### 9.3 Conflict policy

**Current status: Partial**

The fact-checking service can return `CONTRADICTED`, but main Ask and Learn do not route a contradiction into revision or escalation.

A production policy should define:

- Which source wins when documents conflict.
- Whether effective date, authority, or product form controls priority.
- When the system may qualify an answer.
- When it must ask a clarifying question.
- When it must refuse or escalate.

### 9.4 Uncertainty handling

**Current status: Partial**

Prompts tell the model to state when sources do not cover a question. This is valuable but is not an enforceable release control.

Needed:

- Explicit dispositions such as `release`, `release_with_qualification`, `clarify`, `hold`, and `escalate`.
- Confidence based on measurable evidence signals rather than model self-confidence alone.
- User-facing explanations for insufficient evidence.

### 9.5 Trace every run

**Current status: Weak/partial**

Roleplay preserves transcripts, feedback, and verification events. Some server and client latency measurements are printed.

Main Ask does not persist a complete trace. A production trace should contain:

- User and tenant identifiers.
- Conversation and turn identifiers.
- Original question.
- Rewritten retrieval query, if any.
- Retrieved source identifiers and versions.
- Draft answer.
- Validation findings.
- Revision output.
- Final answer and disposition.
- Model/deployment identifiers.
- Prompt and policy versions.
- Foundry response identifiers.
- Latency, token use, and estimated cost.
- User feedback.
- Error and retry information.

### 9.6 Domain evaluation set

**Current status: Missing**

There is no checked-in evaluation suite for Learn or Ask.

The repository currently contains PDF extraction/chunking tests only. During this assessment, test collection failed in the active Python environment because `python-docx` was not installed, despite being listed in `requirements.txt`.

An initial evaluation set should cover:

- Basic FlexLife definitions.
- Living benefits.
- Eligibility and underwriting.
- Loans and withdrawals.
- Premium and illustration questions.
- Numerical claims.
- State/product variations.
- Unsupported questions.
- Ambiguous questions requiring clarification.
- Conflicting or expired sources.
- Multi-turn follow-ups.
- Citation correctness.
- Citation completeness.
- Appropriate refusals and qualifications.

### 9.7 Protect tools and data

**Current status: Demo level**

Current protection includes:

- A shared username/password.
- An HttpOnly cookie.
- API gating.
- Environment-based secrets.
- Container App secret configuration described in deployment documentation.

The source code explicitly describes the login as a gate rather than an identity system.

Needed for production:

- Microsoft Entra ID authentication.
- Per-user and per-role authorization.
- Tenant isolation if applicable.
- Managed identity where supported.
- Key Vault-backed secrets.
- Private networking and restricted ingress where required.
- Audit logging.
- PII redaction and retention rules.
- Administrative authorization for ingestion and content approval.

### 9.8 Latency and cost

**Current status: Partial with good foundations**

Already implemented:

- Retry and exponential backoff.
- Model warm-up behavior for roleplay.
- Background Learn generation.
- Background roleplay verification.
- Limited generation worker pool.
- On-demand article narration.
- Bounded retrieval counts.

Needed:

- Token and cost accounting.
- Central latency tracing.
- Service-level objectives.
- Retrieval/result caching.
- Per-user or per-tenant budgets.
- Risk-based validation to avoid unnecessary reviewer calls.
- Durable queues and retry policies.

---

## 10. Architectural decision: Foundry versus local RAG

The next team should make this decision explicitly before building additional validation agents.

### Option A: Foundry becomes the canonical knowledge layer

Use Foundry and its knowledge integration for:

- Main Ask.
- In-call Ask.
- Learn custom retrieval.
- Fact-check evidence retrieval.
- Validation agents.

Keep PostgreSQL for:

- Users.
- Chat metadata.
- Learn artifacts.
- Curriculum progress.
- Roleplay sessions.
- Traces.
- Evaluations.
- Approval workflow.

For curated Learn lessons, expose deterministic Foundry filters or a controlled retrieval tool capable of selecting exact approved document versions and pages.

**Advantages:**

- One knowledge source.
- One access-control model.
- Consistent citations.
- Less duplicated retrieval infrastructure.
- Easier evaluation and monitoring.

**Risks:**

- Pinned curriculum retrieval may require additional tooling.
- Dependence on Foundry feature behavior and response contracts.
- Migration effort for existing local-RAG paths.

### Option B: Retain a deliberate hybrid architecture

Use Foundry for conversational Ask and retain local RAG for curated Learn and deterministic verification.

This is valid only if the boundaries are formalized:

- Foundry is the conversational agent platform.
- Local retrieval is a controlled curriculum/evidence service.
- Both consume the same governed source catalog.
- Content versions and access rules are synchronized.
- Both are tested against the same evaluation suite.

**Advantages:**

- Preserves exact page/chunk pinning.
- Provides application-level control of evidence retrieval.
- Reuses substantial existing code.

**Risks:**

- More operational overhead.
- Potential answer inconsistency.
- Two retrieval-quality surfaces.
- More complicated security and data-freshness controls.

### Recommendation

Prefer **Option A**, with a narrow exception for deterministic curated curriculum retrieval if Foundry cannot meet the page/version pinning requirements.

Do not retain two unrestricted general-purpose RAG systems.

---

## 11. Recommended target architecture

```text
Web/mobile application
  |
  v
FastAPI application control plane
  |-- identity and authorization
  |-- conversation/session management
  |-- Learn orchestration
  |-- policy and release gate
  |-- audit/trace persistence
  |-- feedback capture
  |
  +--> Foundry answer/learning agents
  |      |
  |      +--> governed knowledge service
  |      +--> approved tools
  |
  +--> evidence verifier
  |      +--> same governed knowledge service
  |
  +--> Azure Speech
  |
  +--> PostgreSQL
         |-- users and roles
         |-- conversations and turns
         |-- Learn artifacts and progress
         |-- source/version catalog
         |-- answer traces and dispositions
         |-- evaluation cases and results
         |-- roleplay sessions

Operational plane
  |-- automated ingestion and source freshness
  |-- durable job queue
  |-- centralized traces, metrics, and logs
  |-- secrets and managed identity
  |-- CI evaluation gates
```

### FastAPI's role

FastAPI should remain the application control point. Foundry should reason and retrieve, but FastAPI should enforce application policy.

FastAPI should decide:

- Which agent/tool a user may call.
- Which sources are eligible.
- Whether validation is required.
- Whether an answer can be released.
- What must be persisted.
- What feedback and telemetry are recorded.

---

## 12. Recommended data model additions

### 12.1 Users and authorization

Suggested entities:

- `users`
- `roles`
- `user_roles`
- `organizations` or `tenants`, if required

### 12.2 Ask conversations

Suggested tables:

#### `chat_threads`

- `id`
- `user_id`
- `title`
- `status`
- `created_at`
- `updated_at`
- `pinned_at`

#### `chat_turns`

- `id`
- `thread_id`
- `sequence`
- `role`
- `visible_text`
- `submitted_text`
- `foundry_response_id`
- `model`
- `disposition`
- `created_at`

#### `answer_traces`

- `turn_id`
- `retrieval_query`
- `retrieved_sources`
- `draft_answer`
- `validation_results`
- `final_answer`
- `prompt_version`
- `policy_version`
- `latency_ms`
- `input_tokens`
- `output_tokens`
- `estimated_cost`
- `error`

#### `answer_feedback`

- `turn_id`
- `user_id`
- `rating`
- `reason_code`
- `comment`
- `created_at`

### 12.3 Source governance

Suggested entities:

- `source_documents`
- `source_versions`
- `source_approvals`

Important metadata:

- Product.
- Document type.
- Form number.
- Jurisdiction/state.
- Effective date.
- Expiration date.
- Authority level.
- Approval state.
- Superseded-by relationship.
- Content hash.
- Ingestion timestamp.

### 12.4 Learn governance

Extend Learn artifacts with:

- `content_status`: draft, validating, needs_review, approved, published, expired, rejected.
- `source_version_ids`.
- `generation_model`.
- `prompt_version`.
- `validation_report`.
- `approved_by`.
- `approved_at`.
- `expires_at`.
- `superseded_by`.

### 12.5 Learning progress

Suggested entities:

- `lesson_progress`
- `flashcard_attempts`
- `quiz_attempts`
- `skill_mastery`
- `learning_recommendations`

---

## 13. Must-have, good-to-have, and nice-to-have capabilities

### 13.1 Must have before a serious pilot

1. Decide the canonical knowledge and retrieval architecture.
2. Add real user identity through Microsoft Entra ID.
3. Persist Ask conversations server-side.
4. Persist Foundry response IDs and complete answer traces.
5. Create governed source metadata for version, date, jurisdiction, and product.
6. Add claim-level evidence validation for material product claims.
7. Add release, qualify, clarify, hold, and escalate dispositions.
8. Validate Learn content before marking it publishable.
9. Build an initial domain evaluation set.
10. Centralize logs, metrics, and traces.
11. Replace process-local Learn jobs with a durable queue.
12. Automate knowledge ingestion and source-freshness checks.
13. Add tests for Ask, Learn, Foundry citation parsing, authentication, retrieval, and validation.
14. Define retention and privacy rules for conversations and roleplay transcripts.

### 13.2 Good to have shortly afterward

1. Content-owner approval workflow.
2. Source-expiration alerts.
3. Automatic revalidation when source material changes.
4. Deep citation links to the exact page and passage.
5. Per-user learning progress.
6. Flashcard mastery and missed-card analytics.
7. Central feedback analytics.
8. Admin view for held answers, weak retrieval, and negative feedback.
9. Conversation-to-Learn actions such as "make flashcards from this answer."
10. Roleplay-to-Learn recommendations based on factual or conversational weaknesses.
11. Model/prompt/index canary evaluations before release.
12. Cost and latency dashboards.

### 13.3 Nice to have

1. Adaptive learning paths.
2. Spaced repetition scheduling.
3. Multilingual Learn and Ask.
4. Streaming Ask responses.
5. Voice-first Ask.
6. Source comparison when documents conflict.
7. Manager/team dashboards.
8. Personalized recommendations based on Ask and roleplay behavior.
9. Authorized personal knowledge collections.
10. Offline or exportable learning plans.

---

## 14. Recommended phased roadmap

### Phase 0: Architecture and policy decisions

**Objective:** Remove ambiguity before building more agents.

Tasks:

- Choose Foundry-first or deliberate hybrid retrieval.
- Define the canonical source catalog.
- Define material-claim categories.
- Define answer dispositions.
- Define source-authority and conflict rules.
- Define jurisdiction/product filtering requirements.
- Define trace retention and privacy requirements.

Exit criteria:

- Approved architecture decision record.
- Written grounding and release policy.
- Initial source metadata schema.

### Phase 1: Trustworthy Ask

**Objective:** Make every Ask response traceable and releasable under explicit policy.

Tasks:

- Add authenticated users.
- Add server-side threads and turns.
- Persist Foundry response IDs, citations, model, latency, and feedback.
- Add deterministic citation checks.
- Add material-claim detection.
- Add evidence verification for high-risk claims.
- Add a release/qualify/clarify/hold decision.
- Add one controlled revision attempt.
- Build 50-100 representative evaluation questions.

Exit criteria:

- Every production Ask turn has a trace.
- Unsupported material claims are not silently released.
- Evaluation results are reproducible.
- User feedback is centrally recorded.

### Phase 2: Governed Learn

**Objective:** Convert generated learning artifacts into reviewed, versioned content.

Tasks:

- Add Learn lifecycle states.
- Extract and validate material claims.
- Record source versions and model/prompt versions.
- Add approval and publishing workflow.
- Mark content stale when sources change.
- Add per-user progress and completion.
- Add mastery and missed-card tracking.
- Connect Ask and roleplay gaps to lesson recommendations.

Exit criteria:

- Published lessons have validation reports.
- Lessons can be traced to approved source versions.
- Stale source content can be identified and withdrawn.
- Individual learning progress is durable.

### Phase 3: Production platform controls

**Objective:** Make the platform operationally supportable.

Tasks:

- Managed identity and Key Vault.
- Private networking and ingress restrictions as required.
- Central metrics, traces, and alerts.
- Durable generation/validation queue.
- Automated document synchronization.
- CI evaluation gates.
- Cost and latency budgets.
- Backup and recovery validation.
- PII retention and deletion workflows.

Exit criteria:

- Service objectives and alerts exist.
- Evaluation regressions block deployment.
- Source freshness is monitored.
- Secrets are centrally managed.
- Recovery procedures are tested.

### Phase 4: Optimization and personalization

**Objective:** Improve learning outcomes and operating efficiency.

Tasks:

- Adaptive curriculum.
- Spaced repetition.
- Personalized recommendations.
- Cross-link Ask, Learn, and roleplay.
- Selective caching.
- More advanced multi-agent validation only where evaluation proves value.

---

## 15. Suggested initial evaluation framework

### 15.1 Ask evaluation dimensions

Score each answer on:

- Factual correctness.
- Supported-claim coverage.
- Citation precision.
- Citation completeness.
- Source authority.
- Correct jurisdiction/product version.
- Appropriate uncertainty.
- Directness and usefulness.
- Conversation continuity.
- Safety/compliance language.

### 15.2 Learn evaluation dimensions

Score each artifact on:

- Factual correctness.
- Required-topic coverage.
- Unsupported-claim count.
- Source-reference accuracy.
- Plain-language quality.
- Suitability for a new agent.
- Actionability in a customer conversation.
- Prohibited or overpromising language.
- Format completeness.
- Length conformance.

### 15.3 Recommended automated metrics

- Material claims with at least one supporting citation.
- Citations whose source directly supports the linked claim.
- Answers correctly held when evidence is absent.
- Retrieval recall against expected documents.
- Retrieval precision among the top results.
- Response latency percentiles.
- Validation invocation rate.
- Revision success rate.
- Cost per Ask turn.
- Cost per generated lesson.
- Negative feedback rate.

### 15.4 Golden cases to build first

- Straightforward supported product question.
- Question requiring a numerical fact.
- Question whose answer varies by jurisdiction.
- Question using an obsolete product term.
- Question not covered by the knowledge base.
- Ambiguous multi-product question.
- Follow-up question with a pronoun or omitted subject.
- Question with conflicting sources.
- Request for a guarantee or return promise.
- Request to create a client-facing explanation.

---

## 16. Testing observations

Current checked-in tests cover PDF layout extraction and chunk formation. They test areas such as:

- Reading order.
- Heading detection.
- Page attribution.
- Disclosure separation.
- Table preservation.
- Chunk size bounds.
- Provenance metadata.

Missing test categories include:

- Foundry response parsing.
- Foundry citation-marker replacement.
- Ask API behavior.
- Server-side authentication behavior.
- Chat history and threading.
- Local retrieval ranking.
- Fact-check verdict parsing and accuracy.
- Learn content generation.
- Curriculum source pinning.
- Learn retry/recovery behavior.
- Audio status transitions.
- Roleplay verification concurrency.
- Release-gate policy.
- Source-version filtering.

The local test run attempted during assessment failed at collection because the active Python installation lacked the `docx` module. `python-docx` is included in `requirements.txt`, so the immediate fix is to install project dependencies in the test environment and then establish a repeatable CI command.

---

## 17. Key implementation risks

### Risk 1: Conflicting knowledge paths

Learn and Ask may produce different answers because they use different retrieval services.

**Mitigation:** Establish one governed source catalog and either one retrieval service or explicitly bounded retrieval services tested against the same cases.

### Risk 2: Citation presence mistaken for factual support

Foundry citations prove that a source was returned, not necessarily that each answer claim is entailed by it.

**Mitigation:** Add claim-level verification and citation completeness checks.

### Risk 3: Generated Learn content becomes stale

Lessons are durable database artifacts while underlying documents can change.

**Mitigation:** Record source-version identifiers and automatically revalidate or expire affected lessons.

### Risk 4: Shared identity prevents personalization and auditing

The current shared login means conversation ownership, progress, and permissions are not meaningful.

**Mitigation:** Add Entra ID before relying on user analytics or exposing broader access.

### Risk 5: In-process jobs are not durable

Container restart or scale-out can interrupt Learn generation and create concurrency ambiguity.

**Mitigation:** Move generation and validation into a durable queue/worker architecture.

### Risk 6: Manual ingestion causes stale knowledge

The deployment runbook requires manually executing ingestion inside the container.

**Mitigation:** Add scheduled/event-driven ingestion, deletion reconciliation, and freshness alerts.

### Risk 7: Multi-agent expansion increases cost before quality is measurable

Adding several validators without an evaluation baseline can increase cost and latency without demonstrated improvement.

**Mitigation:** Build the evaluation set first, then introduce validation selectively and measure incremental value.

---

## 18. Recommended immediate backlog

The following sequence is suitable for the next implementation conversation.

### Epic A: Ask persistence and tracing

- Add `chat_threads`, `chat_turns`, `answer_traces`, and `answer_feedback` tables.
- Add CRUD endpoints for threads.
- Replace browser-only history with server persistence while retaining local optimistic UI.
- Persist Foundry response ID, model, status, citations, and latency.
- Persist feedback rather than storing it only in the thread object.

### Epic B: Source governance

- Define source metadata schema.
- Backfill product, jurisdiction, effective date, and authority.
- Add source eligibility filters.
- Add stale/expired-source detection.
- Add document-deletion reconciliation.

### Epic C: Ask release gate

- Define material-claim categories.
- Add deterministic citation validation.
- Add evidence verifier.
- Add answer dispositions.
- Add one revision pass.
- Persist all findings.

### Epic D: Evaluation harness

- Create versioned JSON/YAML evaluation cases.
- Add a CLI evaluator.
- Capture expected sources and expected behavior.
- Measure citation precision, supported-claim coverage, and correct holds.
- Add CI reporting.

### Epic E: Governed Learn

- Add content lifecycle fields.
- Add post-generation claim verification.
- Add approval endpoints and UI.
- Add source-version dependencies.
- Add stale-content invalidation.

### Epic F: Identity and user learning state

- Integrate Entra ID.
- Associate chat, progress, roleplay, and preferences with a user.
- Add lesson completion and flashcard-attempt storage.
- Add recommendation logic.

---

## 19. Questions the next working session should resolve

1. Is the Foundry knowledge base intended to replace local pgvector retrieval, or merely supplement it?
2. Can Foundry retrieval filter by document version, page, product, and jurisdiction strongly enough for curated Learn lessons?
3. Which answer categories are considered material or regulated?
4. Which source types are authoritative, and how are conflicts resolved?
5. Are generated Learn artifacts allowed to publish automatically, or must a content owner approve them?
6. What user population and tenant model are expected?
7. What conversation and transcript retention policy applies?
8. What latency target is acceptable for validated Ask answers?
9. Is an unsupported answer allowed to provide general sales-process coaching, or must it always hold?
10. Which production environment and infrastructure repository own networking, identity, Key Vault, and telemetry configuration?

---

## 20. Repository evidence map

| Topic | Primary file or location |
|---|---|
| FastAPI application and routes | `src/rag_layer/server.py` |
| Main Foundry agent client | `src/rag_layer/foundry.py` |
| Local retrieval and answer services | `src/rag_layer/service.py` |
| Azure OpenAI requests and prompts | `src/rag_layer/embeddings.py` |
| Learn generation pipeline | `src/rag_layer/learn.py` |
| Curated curriculum | `src/rag_layer/curriculum.py` |
| PostgreSQL schema and queries | `src/rag_layer/db.py` |
| Ingestion | `src/rag_layer/ingest.py` |
| Document extraction | `src/rag_layer/extractors.py` |
| PDF layout extraction | `src/rag_layer/pdf_layout.py` |
| Roleplay and background verification | `src/rag_layer/roleplay.py` |
| Demo authentication gate | `src/rag_layer/auth.py` |
| Primary application UI | `ui/learn.html` |
| Legacy/local RAG console | `ui/index.html` |
| Azure deployment process | `DEPLOY.md` |
| Local/runtime dependencies | `requirements.txt` |
| Container packaging | `Dockerfile` |
| Local service composition | `docker-compose.yml` |
| Existing tests | `tests/test_pdf_layout.py` |

---

## 21. Final recommendation

The application does not need another major visible feature first. It needs a trust and governance layer around the features that already exist.

The recommended direction is:

1. Keep FastAPI as the application control point.
2. Make Foundry the canonical conversational agent and knowledge path where feasible.
3. Preserve deterministic pinned retrieval for curated learning content, but do not maintain two unrestricted general-purpose knowledge systems.
4. Add persistent conversations, complete traces, source governance, and release dispositions.
5. Validate material claims rather than assuming that the presence of citations is sufficient.
6. Treat Learn artifacts as versioned, governed content rather than cached model output.
7. Build evaluations before expanding into a more elaborate multi-agent topology.
8. Use validation selectively according to risk, latency, and measured quality.

The current product experience is already compelling. The next stage should make every answer and lesson:

- Explainable.
- Traceable.
- Supported by eligible evidence.
- Current.
- Testable.
- attributable to a user and source version.
- Governed by an explicit release policy.

That work will turn the current prototype into a credible production platform for Learn and Ask.
