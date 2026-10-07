# FlexLife Conversational Agent
## Technical Design

**Status:** Draft  
**Purpose:** Proof of Concept / Architecture Baseline  
**Product Domain:** FlexLife Indexed Universal Life (IUL)  
**Primary Users:** Sales agents and other users who need product education, underwriting guidance, approved communication guidance, and access to FlexLife materials.

---

# 1. Overview

The FlexLife Conversational Agent provides a single conversational interface through which users can:

- Learn about the FlexLife product.
- Ask product-specific questions.
- Understand published underwriting considerations.
- Gather information relevant to an underwriting submission.
- Understand what may or may not be communicated to a customer.
- Obtain approved or recommended customer-facing wording where available.
- View supporting citations and source material.
- Locate relevant documents.
- Prepare and send document links by email.
- Escalate questions that cannot be answered from authoritative sources.
- Interact using text or non-real-time voice.

The system operates in a regulated and fact-sensitive domain. FlexLife-specific assertions must therefore be grounded in authoritative published information.

The system must not infer product facts, invent unsupported answers, predict underwriting outcomes, or treat general underwriting guidance as authorization of an application.

The central architectural principle is:

> The language model may reason about what should happen next, but application orchestration controls what information is authoritative, what actions are permitted, and when consequential actions may execute.

---

# 2. Goals

The system should provide a conversational experience while maintaining predictable controls around knowledge, compliance, underwriting, and actions.

Primary goals are:

1. Provide accurate, grounded FlexLife information.
2. Cite authoritative sources supporting factual claims.
3. Avoid unsupported FlexLife-specific assertions.
4. Educate users rather than merely answer isolated questions.
5. Help users understand published underwriting considerations without predicting underwriting decisions.
6. Identify situations where communication restrictions or approved wording are relevant.
7. Gather facts known to be useful to a process without making the resulting business decision.
8. Escalate unresolved questions rather than fabricate answers.
9. Support multi-step workflows within the same conversation.
10. Allow the user to review and approve consequential actions such as sending email.
11. Maintain lightweight presentation preferences such as response length and formatting style.
12. Support both text and asynchronous voice interaction.

---

# 3. Non-Goals

The POC is not intended to:

- Perform or authorize underwriting.
- Predict whether an applicant will be approved.
- Predict an underwriting classification or rating unless explicitly supported as historical or published factual information.
- Replace authoritative underwriting systems or personnel.
- Replace compliance review processes.
- Generate unsupported product recommendations.
- Make binding promises to a customer.
- Conduct real-time full-duplex voice conversations.
- Autonomously send external communication without user approval.
- Treat general model knowledge as an authoritative source for FlexLife-specific facts.

---

# 4. Design Principles

## 4.1 Evidence Before Answer

FlexLife-specific factual claims should be based on retrieved authoritative evidence.

The operating principle is:

> No sufficient evidence means no definitive FlexLife-specific answer.

When evidence is unavailable, the system should:

1. State what cannot be established.
2. Identify useful information that is available, if any.
3. Ask for additional information when appropriate.
4. Offer escalation when the authoritative knowledge base cannot resolve the question.

---

## 4.2 Citations Are Part of the Evidence Model

Citations should not be treated as decorative references added after an answer has been generated.

The system should conceptually maintain relationships between:

- the user's question,
- retrieved evidence,
- individual factual claims,
- and the sources supporting those claims.

Unsupported claims should be removed, qualified, or escalated.

---

## 4.3 Behavioral Rules Are Separate From Knowledge

Some information belongs in the knowledge base. Other information defines how the agent must behave.

For example:

**Knowledge base content**

- FlexLife features.
- Product definitions.
- Underwriting criteria.
- Technical product documentation.
- Compliance examples.
- Approved wording.
- Customer documents.

**System-level behavioral rules**

- Never authorize underwriting.
- Never promise an underwriting outcome.
- Never invent FlexLife-specific facts.
- Require authoritative evidence for factual claims.
- Respect customer-communication restrictions.
- Escalate when authoritative information is insufficient.
- Require user approval before external communication.

Critical system behavior should not disappear because a particular document failed to retrieve.

---

# 5. High-Level Architecture

The system should use one primary user-facing conversational orchestrator rather than several independent agents communicating freely with one another.

```text
                     +---------------------------+
User Text / Voice -->| Conversation Orchestrator |
                     +-------------+-------------+
                                   |
                            Turn Planning
                                   |
              +--------------------+--------------------+
              |                    |                    |
              v                    v                    v
     +----------------+   +-----------------+   +----------------+
     | Foundry IQ /   |   | Compliance /    |   | Workflow and   |
     | Knowledge      |   | Policy Review   |   | Action Tools   |
     | Retrieval      |   |                 |   |                |
     +-------+--------+   +--------+--------+   +-------+--------+
             |                     |                    |
             v                     v                    v
       Evidence and            Allowed /          Ticket, Email,
        Citations              Restricted          Documents
             |                  Behavior
             +----------+----------+
                        |
                        v
                Response Composer
                        |
                        v
              Grounding / Policy Gate
                        |
                        v
                Structured Response
                        |
                  +-----+-----+
                  |           |
                  v           v
                Text         TTS
```

The Conversation Orchestrator owns:

- conversational context,
- user intent,
- workflow state,
- retrieval decisions,
- applicable restrictions,
- tool selection,
- follow-up actions,
- user preferences,
- and final response construction.

---

# 6. Why Not Begin With Multiple Independent Agents

The system has several distinct responsibilities, but they do not necessarily require independent conversational agents.

A multi-agent implementation introduces:

- additional latency,
- more model calls,
- duplicated context,
- harder debugging,
- potentially conflicting conclusions,
- less deterministic policy behavior,
- and more complicated observability.

For the POC, most specialist behaviors should therefore be modeled as controlled capabilities invoked by the orchestrator.

Potential logical specialists include:

- knowledge retrieval,
- compliance evaluation,
- underwriting guidance,
- response composition,
- document recommendation,
- and escalation.

These may internally use separate model calls where useful, but they do not need to behave as autonomous peer agents.

A dedicated specialist agent should only be introduced when the function has materially different:

1. instructions,
2. tools,
3. source material,
4. reasoning requirements,
5. or lifecycle.

---

# 7. Major Components

## 7.1 Conversation Orchestrator

The Conversation Orchestrator is the primary reasoning component.

For each turn it should determine:

1. What is the user asking?
2. Is the request in scope?
3. Is the topic sensitive?
4. What authoritative information is required?
5. What knowledge sources should be searched?
6. Are specific compliance or underwriting rules applicable?
7. Is additional information required from the user?
8. Can the question be answered?
9. Should a workflow be started or continued?
10. Are any user actions appropriate?
11. Does the proposed response require additional validation?

The orchestrator should own conversation continuity but should not be treated as the source of FlexLife product truth.

---

# 8. Knowledge Architecture

## 8.1 Foundry IQ

Foundry IQ will provide the primary authoritative retrieval layer.

The knowledge base may contain:

- FlexLife technical documentation.
- Product brochures.
- Product training materials.
- Underwriting documentation.
- Communication/compliance rules.
- Approved wording.
- Customer-facing documents.
- Frequently asked questions.
- Process documentation.

The Foundry Agent associated with Foundry IQ may remain relatively thin if application orchestration supplies the system instructions and conversation context.

The application is therefore the primary agent runtime, while Foundry IQ acts primarily as the managed knowledge retrieval capability.

---

## 8.2 Source Classification

Documents and indexed content should contain metadata where possible.

Example:

```text
product: FlexLife

source_type:
    product
    underwriting
    compliance
    approved_wording
    marketing
    training
    customer_document

audience:
    internal
    agent
    customer

authority:
    authoritative
    supplemental

effective_date:
    YYYY-MM-DD
```

This enables retrieval and response logic to reason about the authority and intended use of material.

---

## 8.3 Source Precedence

Where sources overlap or conflict, source categories should have an explicit precedence model.

Example:

```text
Compliance / Legal Guidance
          |
          v
Underwriting Guidance
          |
          v
Product Technical Documentation
          |
          v
Training Material
          |
          v
Marketing Material
```

The actual precedence should be established with product, legal, underwriting, and compliance stakeholders.

The model should not independently determine which source overrides another when the organization has an established authority hierarchy.

---

# 9. Evidence Model

Retrieval results should be transformed conceptually into an evidence packet.

Example:

```json
{
  "question": "Does diabetes prevent someone from getting FlexLife?",
  "evidence": [
    {
      "claim": "Published underwriting guidance discusses diabetes.",
      "source": "FlexLife Underwriting Guide",
      "citation": "...",
      "sourceType": "underwriting"
    }
  ],
  "unsupportedAreas": [
    "No retrieved material establishes whether this individual applicant will be approved."
  ]
}
```

Response generation should consume the evidence packet rather than relying solely on the raw conversation.

---

# 10. Claim Grounding

The system should conceptually evaluate:

```text
Claim A --> Citation 1
Claim B --> Citation 1
Claim C --> Citation 2
Claim D --> Unsupported
```

Unsupported Claim D should not be returned as fact.

Depending on context, it should instead be:

- removed,
- qualified,
- turned into a question,
- or identified as unresolved.

This is especially important for statements such as:

> "Someone with this condition will probably be approved."

Published documentation discussing a condition is not equivalent to evidence supporting an approval prediction.

---

# 11. Underwriting Behavior

Underwriting should be represented as a controlled conversational mode rather than a separate unrestricted chatbot.

The agent may:

- describe published underwriting considerations,
- identify known relevant facts,
- ask the user for those facts,
- explain what information may help make an application more complete,
- cite applicable underwriting guidance,
- explain published processes,
- and offer approved customer-facing wording when available.

The agent must not:

- approve an applicant,
- decline an applicant,
- guarantee insurability,
- guarantee a rating,
- promise a particular underwriting outcome,
- or imply that satisfying a set of conditions guarantees an outcome.

---

# 12. Guided Information Gathering

Some knowledge does more than provide an answer. It establishes which information is relevant.

For example, published guidance concerning a medical condition may identify factors such as:

- date of diagnosis,
- treatment,
- medication,
- current status,
- recurrence,
- recent measurements,
- follow-up care,
- or other documented considerations.

The agent should be allowed to transform that evidence into a guided information-gathering interaction.

Example:

```json
{
  "responseType": "gather_information",
  "questions": [
    {
      "id": "diagnosis_date",
      "label": "When was the condition diagnosed?",
      "type": "date"
    },
    {
      "id": "treatment",
      "label": "What treatment is currently being used?",
      "type": "text"
    },
    {
      "id": "recurrence",
      "label": "Has there been a recurrence?",
      "type": "choice",
      "options": [
        "Yes",
        "No",
        "Unknown"
      ]
    }
  ]
}
```

The purpose is to improve information completeness, not to generate an underwriting decision.

---

# 13. Compliance and Communication Guidance

The agent must detect topics that are close to communication restrictions or warning-language areas.

When applicable, responses may contain:

- factual product information,
- an explanation of communication restrictions,
- recommended or approved wording,
- wording that should be avoided,
- links to authoritative guidance,
- and suggested follow-up actions.

Example interaction:

```text
Published guidance allows you to explain that underwriting
considers the customer's medical history.

Avoid telling the customer that meeting these conditions means
they will be approved.

Suggested wording:
"Underwriting will review the application and supporting information
to determine eligibility."
```

Recommended or approved wording must itself be traceable to an appropriate source or explicitly generated as non-approved suggested language.

The system should distinguish between:

- approved wording,
- recommended wording,
- explanatory paraphrase,
- and prohibited or risky language.

---

# 14. Compliance Evaluation

Sensitive responses may receive an additional policy/compliance evaluation before being shown to the user.

Conceptual input:

```text
User question
+
Retrieved evidence
+
Applicable compliance evidence
+
Proposed response
```

Conceptual output:

```json
{
  "status": "allowed_with_warning",
  "issues": [
    {
      "type": "underwriting_prediction",
      "text": "...",
      "replacementGuidance": "..."
    }
  ],
  "offerApprovedWording": true
}
```

This evaluator may be implemented using:

- deterministic application rules,
- an additional focused model call,
- or a combination of both.

High-value deterministic rules should not be delegated entirely to probabilistic model evaluation.

---

# 15. Turn Processing Pipeline

A normal conversational turn should conceptually pass through:

```text
1. Interpret the request
       |
       v
2. Identify topic and risk
       |
       v
3. Determine required evidence
       |
       v
4. Retrieve authoritative knowledge
       |
       v
5. Identify missing information
       |
       v
6. Determine permitted response behavior
       |
       v
7. Compose response
       |
       v
8. Validate grounding / compliance when required
       |
       v
9. Produce structured response
       |
       v
10. Render for user
```

Not every turn needs every expensive processing step.

---

# 16. Adaptive Processing

Simple questions should follow a short path.

```text
Simple Product Question

User
 |
 v
Knowledge Retrieval
 |
 v
Grounded Answer
 |
 v
User
```

Sensitive underwriting or communication questions use additional controls.

```text
Sensitive Question

User
 |
 v
Risk Identification
 |
 v
Knowledge Retrieval
 | \
 |  \--> Underwriting / Compliance Sources
 v
Response Generation
 |
 v
Compliance Validation
 |
 v
User
```

Action workflows introduce approval boundaries.

```text
User
 |
 v
Reason / Retrieve
 |
 v
Propose Action
 |
 v
User Approval
 |
 v
Execute Tool
 |
 v
Report Result
```

This avoids forcing every request through numerous serial model calls.

---

# 17. Structured Response Contract

The orchestrator should return a structured internal representation rather than only free-form text.

Example:

```json
{
  "responseType": "answer",

  "content": "...",

  "citations": [],

  "risk": {
    "area": "underwriting",
    "level": "sensitive"
  },

  "questions": [],

  "suggestions": [],

  "warning": null,

  "documents": [],

  "pendingAction": null
}
```

Possible `responseType` values include:

```text
answer
gather_information
select_documents
email_approval
escalation
clarification
workflow_complete
```

This allows the same conversational engine to support both simple chat and guided workflows.

---

# 18. Contextual Suggested Actions

Suggested prompts should not be treated only as generated conversational questions.

Some should represent semantic application actions.

Example action types:

```text
CONTINUE
SHOW_SOURCE
SHOW_APPROVED_WORDING
SHOW_WHAT_NOT_TO_SAY
GATHER_UNDERWRITING_DETAILS
FIND_DOCUMENTS
SEND_DOCUMENTS
ESCALATE_QUESTION
```

Example response:

```json
[
  {
    "action": "SHOW_APPROVED_WORDING",
    "label": "Show approved wording"
  },
  {
    "action": "GATHER_UNDERWRITING_DETAILS",
    "label": "What details should I collect?"
  },
  {
    "action": "SHOW_SOURCE",
    "label": "Open underwriting guidance"
  }
]
```

This allows the client application to distinguish an ordinary follow-up prompt from a workflow command.

---

# 19. Conversation State

Conversation history alone should not serve as the workflow state store.

The application should maintain explicit structured state.

Example:

```json
{
  "topic": "diabetes underwriting",

  "preferences": {
    "length": "brief",
    "format": "bullets"
  },

  "workflow": {
    "type": "underwriting_information_gathering",
    "status": "collecting_information",

    "knownFacts": {
      "diagnosisDate": "2021",
      "treatment": null,
      "a1c": null
    }
  },

  "pendingAction": null
}
```

This state can be passed to the orchestrator with each turn.

---

# 20. Workflow State Machine

Multi-turn operations should follow explicit states.

```text
NORMAL CHAT
    |
    +----> NEED INFORMATION
    |          |
    |          +----> ANSWER
    |
    +----> NEED ESCALATION
    |          |
    |          +----> TICKET REVIEW
    |                    |
    |                    +----> SUBMITTED
    |
    +----> DOCUMENT SHARING
               |
               v
         SELECT DOCUMENTS
               |
               v
           DRAFT EMAIL
               |
               v
         AWAIT APPROVAL
               |
        +------+------+
        |             |
        v             v
       SEND          CANCEL
```

The model may recommend state transitions.

The application performs and persists them.

---

# 21. Escalation

Failure to find sufficient authoritative information is a valid completion path.

The system should distinguish between:

- the question being unclear,
- required facts being missing,
- evidence being unavailable,
- conflicting evidence,
- and the knowledge base not establishing an answer.

If the question cannot be resolved from authoritative knowledge, the system may prepare an escalation.

Example:

```json
{
  "responseType": "escalation",

  "ticket": {
    "topic": "FlexLife underwriting question",
    "question": "...",
    "conversationSummary": "...",
    "knownFacts": {},
    "sourcesConsulted": [],
    "unresolvedQuestion": "..."
  }
}
```

The ticket or email should summarize relevant conversation context rather than requiring the user to restate the entire issue.

---

# 22. Document Discovery

The system should be able to identify documents appropriate to the current discussion.

Possible attributes include:

- title,
- description,
- intended audience,
- document category,
- effective date,
- customer-shareable status,
- URI,
- and source.

Example:

```json
{
  "documents": [
    {
      "id": "DOC-123",
      "title": "FlexLife Product Overview",
      "customerShareable": true,
      "reason": "Provides an overview of the product discussed."
    }
  ]
}
```

The system should only recommend documents appropriate for the recipient and conversation context.

---

# 23. Document Selection

When multiple documents are available, the agent should recommend a subset but allow the user to select what is actually sent.

Example:

```text
Recommended documents:

[x] FlexLife Product Overview
[x] Chronic Conditions Guide
[ ] Illustration Guide
```

Recommendations are advisory.

User selection determines the final document list.

---

# 24. Email Workflow

Email should follow an explicit approval workflow.

```text
Document Recommendation
          |
          v
User Selection
          |
          v
Draft Email
          |
          v
User Review
          |
    +-----+------+
    |            |
   Edit       Approve
                 |
                 v
             Send Tool
                 |
                 v
           Confirmation
```

The model must not send an email simply because the user initially requested documents.

The user should see the actual message and recipient before transmission.

---

# 25. Action Tools

The application may expose tools such as:

```text
search_flexlife_knowledge()

find_documents()

create_support_ticket()

draft_email()

send_email()
```

The model requests actions.

Application code validates and executes them.

Tool authorization should remain in code rather than model instructions alone.

For example:

```text
send_email()
```

should reject execution unless:

```text
pendingAction.status == APPROVED
```

or equivalent application state is present.

---

# 26. Voice Architecture

Voice should be treated as an input/output adapter rather than a separate reasoning architecture.

```text
Audio Input
    |
    v
Speech-to-Text
    |
    v
Conversation Orchestrator
    |
    v
Structured Response
    |
    +----> Display Text
    |
    v
Text-to-Speech
    |
    v
Audio Playback/File
```

The POC does not require streaming conversation.

This permits standard speech-to-text and text-to-speech operations around the same conversational engine used by text interaction.

---

# 27. User Presentation Preferences

A limited set of durable or session preferences should be supported.

Example:

```json
{
  "responseLength": "brief",
  "responseFormat": "bullets"
}
```

Possible values might include:

**Length**

```text
brief
standard
detailed
```

**Format**

```text
bullets
prose
mixed
```

These preferences affect presentation only.

They must not change:

- evidence requirements,
- citations,
- compliance behavior,
- safety rules,
- or workflow requirements.

---

# 28. Citation Interaction

Citations should be represented structurally so the client can expose them interactively.

Example:

```json
{
  "citationId": "C12",
  "sourceTitle": "FlexLife Underwriting Guide",
  "section": "Diabetes",
  "excerpt": "...",
  "uri": "...",
  "documentId": "..."
}
```

The UI may later support actions such as:

- hover preview,
- citation detail,
- open source,
- open relevant section,
- or view document metadata.

Citation IDs displayed to users should correspond directly to the evidence used to generate the claim.

---

# 29. Prompt Architecture

The orchestrator system prompt should be organized into stable behavioral sections.

Recommended structure:

```text
ROLE AND PURPOSE

SCOPE

KNOWLEDGE AUTHORITY

GROUNDING REQUIREMENTS

CITATION REQUIREMENTS

UNDERWRITING BOUNDARIES

CUSTOMER COMMUNICATION RULES

INFORMATION-GATHERING BEHAVIOR

ESCALATION RULES

TOOL RULES

APPROVAL REQUIREMENTS

RESPONSE CONTRACT

PRESENTATION PREFERENCES
```

Large detailed technical guidance should remain retrievable knowledge rather than being copied entirely into the system prompt.

The prompt should contain concise rules explaining when those detailed sources must be consulted.

---

# 30. Proposed POC Runtime

A practical initial runtime could be:

```text
Web / Application Client
        |
        v
Application API
        |
        +---- Session / Workflow State
        |
        +---- Conversation Orchestrator
        |        |
        |        +---- Foundry Model
        |        |
        |        +---- Foundry IQ
        |
        +---- Compliance Validator
        |
        +---- Tool Layer
                 |
                 +---- Ticket Simulator
                 +---- Email Simulator
                 +---- Document Service
                 +---- Speech Services
```

The application API is responsible for orchestration and enforcement.

---

# 31. Initial Implementation Recommendation

The POC should start with one orchestrator and a small number of controlled components.

### Phase 1

Implement:

- conversational orchestrator,
- Foundry IQ retrieval,
- citations,
- grounding rules,
- basic underwriting restrictions,
- presentation preferences.

### Phase 2

Add:

- communication/compliance classification,
- approved wording suggestions,
- warning-language detection,
- structured follow-up actions,
- guided information gathering.

### Phase 3

Add:

- document recommendation,
- document selection,
- draft email,
- approval workflow,
- simulated email sending.

### Phase 4

Add:

- escalation tickets,
- conversation summarization for escalation,
- voice input,
- voice output.

### Phase 5

Evaluate whether any component has become sufficiently complex to justify a dedicated specialist agent.

---

# 32. Testing Strategy

Testing should include more than general conversational quality.

The POC should maintain targeted evaluation sets.

## 32.1 Grounding Tests

Verify that the system:

- provides citations for factual FlexLife claims,
- does not make claims unsupported by retrieved evidence,
- correctly reports when evidence is unavailable,
- handles conflicting documentation.

---

## 32.2 Underwriting Tests

Verify that the system:

- explains published considerations,
- asks supported information-gathering questions,
- never guarantees approval,
- never predicts an underwriting outcome without explicit authority,
- clearly distinguishes information gathering from decision making.

---

## 32.3 Communication Tests

Verify that the system:

- detects restricted or risky topics,
- identifies wording that should be avoided,
- surfaces approved wording when available,
- does not label generated wording as approved unless it is sourced as such.

---

## 32.4 Escalation Tests

Verify that:

- unsupported questions can escalate,
- the ticket accurately summarizes the issue,
- known facts are included,
- missing facts are distinguishable from unavailable knowledge,
- citations and consulted sources can be included where useful.

---

## 32.5 Action Tests

Verify that:

- email cannot be sent before approval,
- selected documents match what is sent,
- the correct recipient is used,
- cancellation prevents execution,
- tool failures are reported accurately.

---

# 33. Observability

For development and testing, each turn should expose or log a trace containing information such as:

```text
User request

Intent / topic

Risk classification

Knowledge queries

Retrieved sources

Evidence selected

Workflow before turn

Workflow after turn

Tools requested

Tools executed

Compliance result

Final response

Citation mapping
```

These traces are critical when investigating why the model answered a particular way.

They should be accessible to developers but should not normally be shown to end users.

Sensitive data handling and retention requirements should be established separately.

---

# 34. Key Architectural Decisions

The initial architecture makes the following decisions:

1. **One user-facing orchestrator rather than unrestricted peer agents.**
2. **Foundry IQ is the primary FlexLife factual authority.**
3. **Critical behavioral rules live outside retrieval.**
4. **Claims should be traceable to retrieved evidence.**
5. **Underwriting assistance gathers and explains information but does not decide outcomes.**
6. **Sensitive responses may receive additional compliance validation.**
7. **Workflows are stored as explicit application state.**
8. **Suggested prompts may represent semantic actions.**
9. **Consequential actions require application-controlled authorization.**
10. **Email requires user review and approval before sending.**
11. **Escalation is a normal supported outcome.**
12. **Voice uses the same conversational engine as text.**
13. **Additional specialist agents should be introduced only when complexity justifies them.**

---

# 35. Future Multi-Agent Evolution

If system complexity grows, the architecture can evolve into controlled specialist agents.

Example:

```text
                     ORCHESTRATOR
                          |
          +---------------+---------------+
          |               |               |
          v               v               v
     Education       Underwriting      Document /
     Specialist       Specialist       Servicing
          |               |               |
          +---------------+---------------+
                          |
                          v
                   Compliance Gate
                          |
                          v
                        User
```

A specialist should not be introduced simply because a capability exists.

A specialist is justified when it requires substantial independent:

- instructions,
- knowledge sources,
- tools,
- workflow,
- testing,
- or reasoning.

The orchestrator should remain responsible for determining which specialist is appropriate and for maintaining overall conversation state.

---

# 36. Summary

The FlexLife Conversational Agent should be implemented as a controlled conversational orchestration system rather than an unrestricted collection of autonomous agents.

The language model provides flexible reasoning and natural conversation.

Foundry IQ provides authoritative grounded knowledge.

Application orchestration provides:

- policy enforcement,
- workflow control,
- action authorization,
- persistent state,
- approval boundaries,
- and deterministic business behavior.

This combination allows a single conversational experience to support education, underwriting guidance, compliance assistance, document discovery, escalation, email workflows, and voice while maintaining the evidence and control requirements appropriate to the FlexLife domain.