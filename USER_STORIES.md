# FlexLife POC — User Stories

A consolidated list of the POC milestones (M01–M15), each with its user story,
description, acceptance criteria, and out-of-scope notes.

## Contents

- [M01: Knowledge Foundation](#m01-knowledge-foundation)
- [M02: Natural Language Search](#m02-natural-language-search)
- [M03: Citations](#m03-citations)
- [M04: Follow-up Questions](#m04-follow-up-questions)
- [M05: Voice / Text](#m05-voice--text)
- [M06: Explain Like…](#m06-explain-like)
- [M07: Underwriting Technicals](#m07-underwriting-technicals)
- [M08: Guiding Prompts](#m08-guiding-prompts)
- [M09: Handoff](#m09-handoff)
- [M10: Provide Appropriate Links](#m10-provide-appropriate-links)
- [M11: Learning Library](#m11-learning-library)
- [M12: User Preferences](#m12-user-preferences)
- [M13: Lesson Progress Tracking](#m13-lesson-progress-tracking)
- [M14: Agent Profile and Learnings / Progress / Tracking](#m14-agent-profile-and-learnings--progress--tracking)
- [M15: Specialized Learning for User Based on Needs](#m15-specialized-learning-for-user-based-on-needs)

---

## M01: Knowledge Foundation

**User Story: Establish the FlexLife Knowledge Foundation**

> As a developer building AI capabilities for FlexLife,
> I want the supplied FlexLife product, marketing, and training materials to be transformed into an indexed knowledge foundation,
> so that AI applications can retrieve relevant information from across the corpus using natural-language queries.

### Description

The POC will establish a common knowledge foundation from the supplied FlexLife corpus.

The source material contains multiple document and content formats. The solution should ingest these materials, extract their useful content, perform the preparation needed for effective retrieval, and make that information searchable through natural-language queries.

The knowledge foundation should preserve sufficient information about the source material to support later capabilities such as citations and traceability, although presentation of citations to an end user is outside the scope of this story (later story).

The purpose of the POC is to demonstrate that the supplied corpus can be converted into a practical knowledge source for AI applications. It is not necessary to demonstrate production-scale ingestion, operational management, or enterprise-grade processing.

### Acceptance Criteria

- The supplied FlexLife corpus can be ingested from the source formats represented in the POC dataset.
- Relevant textual information can be extracted and prepared for indexing.
- The corpus is indexed in a form suitable for AI-based retrieval.
- A natural-language query can retrieve relevant information from across multiple source documents.
- Retrieval is based on the content of the supplied corpus rather than requiring the caller to know which document contains the answer.
- Retrieved information retains sufficient source metadata to support future citation and traceability requirements.
- The POC demonstrates the capability using a representative subset or the supplied corpus without requiring production-scale ingestion or operational tooling.

### Out of Scope

- End-user question answering or conversational behavior.
- Presentation or formatting of citations.
- Behavior when information is missing, ambiguous, or conflicting.
- Dynamic lesson generation.
- Production-scale ingestion pipelines, monitoring, administration, or operational support.

---

## M02: Natural Language Search

**User Story: Natural Language Search**

> As a FlexLife sales agent or support user,
> I want to ask questions about FlexLife using natural language,
> so that I can quickly obtain useful answers without needing to understand how the underlying knowledge is organized.

### Description

The POC will provide a conversational search experience over the FlexLife Knowledge Foundation.

Users should be able to ask questions or make requests in everyday language. The application should identify relevant information from across the available FlexLife corpus and synthesize that information into a direct, useful response.

Users should not need to know which documents contain the information, how the corpus is structured, or how the knowledge has been indexed.

The experience should support follow-up questions within the conversation so that users can refine, expand, or clarify a topic without restating all prior context.

The application should also recognize when a request is unrelated to FlexLife or the supported knowledge domain and avoid becoming a general-purpose chatbot.

### Acceptance Criteria

- A user can submit a natural-language question or request related to FlexLife.
- The application retrieves relevant information from the FlexLife Knowledge Foundation.
- The application synthesizes retrieved information into a direct natural-language response.
- The user is not required to identify a source document, document type, category, or other corpus structure.
- The application can use relevant conversational context to answer follow-up questions.
- The application supports requests beyond simple factual lookup, such as explanations, summaries, and comparisons, when supported by the available knowledge.
- Responses remain grounded in the FlexLife knowledge available to the application.
- Requests that are clearly unrelated to FlexLife or the intended training and product-support domain are not treated as general-purpose conversation.
- The capability can be demonstrated through a simple POC chat interface without requiring production-grade conversation management (e.g. long chat summarization, consolidation as context is consumed).

### Out of Scope

- Citation display, citation formatting, or links to source documents.
- Production-scale conversation storage or history management.
- Advanced user authentication or personalization.
- Production-grade moderation or comprehensive topic classification.

---

## M03: Citations

**User Story: Provide Citations for Generated Answers**

> As a FlexLife sales agent or support user,
> I want answers to identify the source material used to support them,
> so that I can validate the information against the approved FlexLife knowledge corpus.

### Description

The POC will provide source citations with generated answers.

Citations are a core part of establishing trust in the application and should demonstrate that answers are grounded in the FlexLife Knowledge Foundation rather than generated without supporting evidence.

When an answer is supported by multiple relevant sources, the application should present multiple citations where appropriate. The exact number of citations and the confidence threshold used to determine which sources are included may be configurable or refined during the POC.

For the POC, a list of supporting sources at the end of the response is sufficient.

If the application cannot find sufficient supporting information in the approved corpus, it should not generate an unsupported substantive answer. Instead, it should direct the user to NLG support for assistance.

### Acceptance Criteria

- Generated answers include one or more citations to support material from the FlexLife Knowledge Foundation.
- Citations identify the source documents used to support the answer.
- When multiple relevant sources materially support an answer, multiple citations can be presented.
- Citation selection may be limited by configurable criteria such as a maximum number of sources or minimum relevance/confidence threshold.
- A source list at the end of the response is sufficient for the POC.
- Citation behavior works across the source formats represented in the supplied corpus.
- If sufficient supporting information cannot be found, the application does not provide an unsupported substantive answer and instead directs the user to NLG support.

### Out of Scope

- Opening or navigating directly to the original source document from a citation.
- Guaranteed page-, section-, or passage-level citation precision.
- Production-grade confidence calibration or citation-ranking logic.
- Formal citation styles or bibliography formatting.

---

## M04: Follow-up Questions

**User Story: Support Follow-Up Questions**

> As a FlexLife sales agent or support user,
> I want to ask follow-up questions within the same conversation,
> so that I can explore a topic naturally without repeatedly restating prior context.

### Description

The POC will support conversational continuity within the active chat session.

Follow-up questions should use relevant context from the current conversation so that users can refer to prior answers, concepts, or terms without having to repeat them.

The application should continue grounding factual content in the FlexLife Knowledge Foundation. Conversation history provides context for understanding the user's intent, but it should not replace the Knowledge Foundation as the source of product facts or details.

The conversation may move between related FlexLife topics without requiring the user to start a new thread. Questions that are tangentially related, such as general IUL concepts that help explain FlexLife, may also be supported.

For the POC, sophisticated long-term conversation memory or automatic summarization of lengthy conversations is not required.

### Acceptance Criteria

- A user can ask a follow-up question without restating the full original topic.
- The application uses relevant context from the current chat session to interpret follow-up questions.
- Recent conversational context is sufficient for the POC; no specific requirement exists to preserve or summarize arbitrarily long conversations.
- Factual statements and product details in follow-up responses remain grounded in the FlexLife Knowledge Foundation.
- The conversation can transition between related FlexLife topics without requiring a new session.
- Questions tangentially related to FlexLife, such as general IUL concepts, may be answered when they support the user's understanding of FlexLife.
- Clearly unrelated requests are not treated as general-purpose conversation.
- The application does not rely on prior conversations or maintain a persistent user profile for this capability.

### Out of Scope

- Cross-session conversational memory.
- Persistent learner or user profiles.
- Long-term conversation summarization or compression.
- Production-grade conversation history management.

---

## M05: Voice / Text

**User Story: Support Voice and Text Interaction**

> As a FlexLife sales agent or support user,
> I want to interact with the application using either text or voice,
> so that I can choose the interaction method that is most convenient for my situation.

### Description

The POC will support both text and voice interaction for user questions and application responses.

Users should be able to select from the following interaction modes:

- Text input with text output.
- Speech input with speech output.
- Speech input with text output.

Voice input may be handled through a simple record-and-transcribe interaction rather than requiring a continuous real-time voice conversation.

Once speech is converted to text, the resulting request should follow the same conversational, retrieval, grounding, and follow-up behavior as a typed request.

For voice output, basic English text-to-speech capability is sufficient for the POC. Advanced voice customization and production-quality speech behavior are not required.

### Acceptance Criteria

- A user can submit a question or request using typed text.
- A user can submit a question or request using spoken English.
- Spoken input is converted into text that can be processed by the same application flow used for typed input.
- A user can receive a response as displayed text.
- A user can receive a response as synthesized speech.
- The application supports Text In → Text Out.
- The application supports Speech In → Speech Out.
- The application supports Speech In → Text Out.
- The user can select the supported interaction mode.
- Voice interactions support the same FlexLife knowledge grounding and conversational behavior as text interactions.
- A simple record → transcribe → respond → play interaction is sufficient for the POC.

### Out of Scope

- Continuous or always-listening voice conversation.
- Real-time conversational interruption or turn-taking.
- Multiple languages.
- Advanced voice selection, cloning, emotion, or speaking-style controls.
- Production-grade speech recognition tuning or accessibility optimization.

---

## M06: Explain Like…

**User Story: Support User Response Preferences**

> As a FlexLife sales agent or support user,
> I want to configure how application responses are presented,
> so that the information is delivered in a style that is useful and comfortable for me.

### Description

The POC will allow users to configure response preferences through a settings interface.

These preferences should influence how responses are presented without changing the underlying factual content, grounding, or source material used to generate the response.

The supported preferences are:

- **Length** — Brief, Balanced, Detailed
- **Format** — Auto, Bullets, Prose
- **Tone** — Warm, Plain, Formal
- **Plain Language** — On / Off

When Plain Language is enabled, responses should favor simpler wording, reduce unnecessary jargon, and explain concepts in a way that is more accessible to newer agents.

When Format is set to Auto, the application may choose the presentation format that best fits the request.

Preferences should be stored as part of the user's profile and remain available across sessions.

### Acceptance Criteria

- A user can configure response length as Brief, Balanced, or Detailed.
- A user can configure response formats such as Auto, Bullets, or Prose.
- A user can configure response tones such as Warm, Plain, or Formal.
- A user can enable or disable Plain Language mode.
- The selected preferences are applied consistently throughout the user's session.
- The selected preferences are persisted and restored across sessions.
- When Plain Language is enabled, responses use simpler language and provide additional explanation where useful for a newer agent.
- When Format is set to Auto, the application may determine an appropriate response structure.
- Response preferences affect presentation and communication style, not the factual grounding of the answer.
- For the POC, it is acceptable for configured preferences to take precedence over conflicting formatting or style instructions given during the conversation.

### Out of Scope

- Automatically changing preferences based on observed user behavior.
- Adaptive personalization beyond the explicitly configured settings.
- Complex conflict resolution between saved preferences and conversational instructions.
- Per-message preference profiles.

---

## M07: Underwriting Technicals

_Reasons approved / disapproved._

**User Story: Provide Underwriting Guidance**

> As a FlexLife sales agent or support user,
> I want to ask questions about FlexLife underwriting rules,
> so that I can better understand documented underwriting requirements and considerations when preparing an application.

### Description

The POC will support questions related to FlexLife underwriting using the supplied technical underwriting documentation as an authoritative source within the Knowledge Foundation.

Because underwriting information may affect how an application is prepared or discussed, responses should be held to a higher standard of grounding and should only provide guidance that is clearly supported by the approved source material.

The application may explain documented underwriting rules, requirements, conditions, limitations, and related technical details. It should not make case-specific underwriting decisions or represent that an applicant will be approved or declined.

When the supplied documentation does not clearly establish an answer, the application should refer the user to NLG support rather than infer, speculate, or provide unsupported guidance.

Responses should make clear that the application is a reference and training aid and is not an authoritative underwriting decision-maker.

### Acceptance Criteria

- A user can ask natural-language questions about FlexLife underwriting rules.
- Responses are grounded in the approved underwriting documentation contained in the Knowledge Foundation.
- The application can explain documented underwriting requirements, conditions, exceptions, and limitations.
- Underwriting responses are generated only when the available source material provides sufficient support.
- If the documentation does not clearly establish an answer, the application directs the user to NLG support.
- The application does not state or imply that a specific applicant will be approved or declined.
- The application does not make independent underwriting judgments or extrapolate beyond documented rules.
- Responses distinguish documented underwriting guidance from case-specific decisions that require NLG review.
- Underwriting responses include language indicating that the application is a reference aid and is not authoritative for underwriting decisions.
- Existing citation and grounding behavior applies to underwriting responses.

### Out of Scope

- Making approval or decline decisions.
- Predicting the underwriting outcome of a specific applicant.
- Replacing NLG underwriting personnel or formal underwriting review.
- Application-processing workflows beyond the underwriting rules represented in the supplied POC documentation.
- Inferring undocumented underwriting practices or exceptions.

---

## M08: Guiding Prompts

**User Story: Identify Needed Client Information**

> As a FlexLife sales agent or support user,
> I want the application to identify additional client information that may be needed,
> so that I can provide the details required to retrieve the most relevant FlexLife guidance, rules, or recommendations.

### Description

The POC will recognize when the information already provided is insufficient to determine which documented FlexLife rules or guidance are most applicable.

When important client-specific details are missing, the application should ask relevant follow-up questions based on the information and distinctions contained in the Knowledge Foundation.

The purpose of these questions is to help the agent gather information they may not otherwise realize is significant. For example, if the documentation applies different rules based on a particular client characteristic, the application should recognize that the characteristic matters and ask for it before providing a more specific answer.

Questions should be driven by what is still needed in the current conversation rather than by a fixed questionnaire.

The application may use this information both to identify applicable documented rules and to support recommendations where sufficient knowledge exists.

If the application cannot support an answer from the Knowledge Foundation, it should refer the user to NLG support rather than provide unsupported guidance.

### Acceptance Criteria

- The application can recognize when additional client information would materially affect the relevance or applicability of an answer.
- The application asks follow-up questions based on information that is still missing from the current conversation.
- Follow-up questions are relevant to retrieving, categorizing, or applying information contained in the Knowledge Foundation.
- The application can identify client characteristics or circumstances that are significant according to the source documentation, even when the agent has not explicitly recognized their importance.
- The application does not require a fixed sequence or comprehensive questionnaire before providing assistance.
- Previously supplied information from the current conversation is taken into account so the application does not unnecessarily ask for the same information again.
- Once sufficient information is available, the application can use it to provide more specific documented rules, guidance, or recommendations.
- Any factual guidance produced after clarification remains grounded in the Knowledge Foundation.
- If the Knowledge Foundation does not sufficiently support an answer, the application refers the user to NLG support.

### Out of Scope

- A complete client intake or application form.
- Production-grade needs analysis or suitability workflows.
- Mandatory question ordering.
- Making final underwriting, eligibility, or approval decisions.
- Generating unsupported recommendations from information outside the Knowledge Foundation.

---

## M09: Handoff

**User Story: Handoff to NLG Support**

> As a FlexLife sales agent or support user,
> I want the application to prepare a support request when it cannot reliably answer my question,
> so that I can efficiently escalate the issue to NLG Support without having to reconstruct the conversation myself.

### Description

The POC will support a handoff experience for questions that cannot be adequately answered from the FlexLife Knowledge Foundation or that require an authoritative, case-specific decision from NLG.

When escalation is appropriate, the application should use relevant information from the current conversation to prepare a draft support email written from the user's perspective.

The draft should summarize the question, relevant context already provided, and the specific information or clarification being requested from NLG Support.

The user should be able to review and edit the draft before taking any further action.

For the POC, the application does not need to actually send the email. A simulated experience showing the generated draft and allowing the user to choose whether to edit or proceed is sufficient.

### Acceptance Criteria

- The application can recognize when a question should be referred to NLG Support because:
  - the Knowledge Foundation does not provide sufficient support for an answer, or
  - the request requires an authoritative or case-specific decision.
- The application can generate a draft support email using relevant information from the current conversation.
- The draft is written from the user's perspective.
- The draft summarizes the user's question and relevant contextual information already provided.
- The draft clearly identifies what clarification or assistance is being requested from NLG Support.
- The user can review the generated draft before any send action is represented.
- The user can edit the draft before proceeding.
- The application does not automatically send or submit the request.
- A simulated "review, edit, or send" experience is sufficient for the POC.
- No real email, ticketing, or support-system integration is required.

### Out of Scope

- Sending email to NLG Support.
- Integration with email, CRM, ticketing, or case-management systems.
- Tracking support request status or responses.
- Automatically resuming the conversation when NLG Support responds.
- Production-grade routing to specific support teams or personnel.

---

## M10: Provide Appropriate Links

**User Story: Open Source Documents from Citations**

> As a FlexLife sales agent or support user,
> I want citations to include links to their source documents,
> so that I can review the original material used to support an application response.

### Description

The POC will allow users to open source documents directly from citations presented with generated responses.

Each citation should provide a simple link to the referenced document. When selected, the document should open so the user can review the original source material.

For the POC, document-level navigation is sufficient. The application does not need to navigate directly to the specific page, section, or passage used to support the response, nor does it need to visually highlight the supporting content.

The purpose of this capability is to make citation verification practical by giving users direct access to the underlying source material.

### Acceptance Criteria

- Citations presented with generated responses can include a link to the referenced source document.
- A user can select a citation link and open the associated source document.
- The link opens the correct document associated with the citation.
- Citation links work for the source document types represented in the POC corpus where a viewable document is available.
- Multiple citations may provide links to multiple source documents.
- Users are not required to know where the original document is stored or how the Knowledge Foundation is organized.
- Document-level linking is sufficient for the POC.

### Out of Scope

- Navigating directly to the exact page, section, paragraph, or passage that supported the response.
- Highlighting supporting text within the opened document.
- Mapping individual statements in a generated response to specific citations.
- Production-grade document access controls or document management.
- Editing or annotating source documents.

---

## M11: Learning Library

**User Story: Learning Library**

> As a FlexLife sales agent or support user,
> I want access to a Learning Library of NLG training resources,
> so that I can quickly find and open existing learning materials.

### Description

The POC will provide a simple Learning Library page containing a predefined set of links to NLG learning resources.

The page is intended as a straightforward navigation experience. Resource links may be hard coded for the POC and should direct the user to the associated learning material when selected.

No dynamic content generation, recommendation logic, search, personalization, or knowledge retrieval is required for this capability.

### Acceptance Criteria

- The application includes a Learning Library page accessible from the POC user interface.
- The page displays a predefined set of NLG learning resources.
- Each resource includes a link or navigation action to the associated learning material.
- Selecting a resource opens or navigates to the expected destination.
- Resource links may be hard coded for the POC.
- The page can be demonstrated without integration with a learning management system or dynamic content service.

### Out of Scope

- Dynamic generation of learning content.
- Personalized learning recommendations.
- Search or filtering of learning resources.
- Tracking course completion or learner progress.
- Learning management system integration.
- Automated synchronization of resource links or metadata.

---

## M12: User Preferences

> **Open question:** How is this different from [M06: Explain Like…](#m06-explain-like)?

_No user story defined yet — needs clarification / possible merge with M06._

---

## M13: Lesson Progress Tracking

**User Story: Resume Audio Learning Materials**

> As a FlexLife learner,
> I want to stop an audio learning session and continue it later,
> so that I can complete longer learning materials over multiple sessions.

### Description

The POC will allow generated audio learning materials to be paused or stopped and resumed at a later time.

The application should retain enough progress information to return the user to approximately the same playback position when they resume the material.

For the POC, any reasonable persistence approach is acceptable. The capability does not need to demonstrate sophisticated synchronization across devices or production-grade learner progress management.

Generated learning materials and their associated progress should have a fixed expiration period for cleanup purposes. Once expired, the saved material may be removed and the user would need to generate it again if needed.

Expiration behavior is secondary to the primary POC goal of demonstrating stop-and-resume capability.

### Acceptance Criteria

- A user can stop or leave an audio learning session before it is complete.
- The application retains the user's approximate playback position.
- The user can later return to the saved learning material and resume from approximately where they stopped.
- Resume behavior works across separate application sessions using a reasonable POC persistence mechanism.
- Generated learning materials and associated progress have a fixed expiration period.
- Expired materials may be removed and are no longer required to be resumable.
- The POC does not require the user to manually record or remember their previous position.

### Out of Scope

- Cross-device synchronization requirements.
- Production-grade learner progress tracking.
- User-configurable expiration periods.
- Expiration warnings or countdown displays.
- Long-term archival of generated learning materials.
- Recovery of expired materials.

---

## M14: Agent Profile and Learnings / Progress / Tracking

> **Open discussion:** We need to discuss what we are tracking against. It seems to
> contradict dynamic materials with some kind of longer-term lesson plan / syllabus.

_No user story defined yet — needs discussion._

---

## M15: Specialized Learning for User Based on Needs

**User Story: Generate Dynamic Learning Sessions**

> As a FlexLife sales agent or learner,
> I want to request a lesson on a topic of my choosing,
> so that I can receive targeted training based on the FlexLife knowledge available to the application.

### Description

The POC will allow users to request learning content dynamically using natural language.

The user may specify a topic, concept, rule, or area they want to learn about without selecting from a predefined course catalog. The application should use the Knowledge Foundation to retrieve relevant information and organize it into a coherent explanatory learning session.

The application may determine an appropriate structure for the lesson based on the requested topic and available source material.

Generated lessons should support both text and audio presentation and should respect applicable user response preferences, such as desired level of detail and Plain Language mode.

All factual lesson content should remain grounded in the Knowledge Foundation. If the requested topic cannot be adequately supported by the available knowledge, the application should not invent instructional content and should direct the user to NLG Support where appropriate.

### Acceptance Criteria

- A user can request a learning session using a natural-language description of the topic they want to learn about.
- The user is not required to select from a predefined curriculum or course catalog.
- The application retrieves relevant information from the Knowledge Foundation for the requested topic.
- The application synthesizes the retrieved information into a coherent explanatory lesson rather than simply returning raw search results.
- The application can determine an appropriate lesson structure based on the requested topic and available content.
- Generated lessons remain grounded in the Knowledge Foundation.
- Generated lessons can be presented as text.
- Generated lessons can be presented as audio.
- Applicable user preferences, including response length and Plain Language mode, can influence how the lesson is presented.
- If sufficient supporting content cannot be found, the application does not generate unsupported instructional material.
- An explanatory lesson is sufficient for the POC; interactive exercises or assessments are not required.

### Out of Scope

- Predefined course catalogs or curriculum mapping.
- Formal learning paths or course sequencing.
- Quizzes, assessments, or knowledge checks.
- Scoring, certification, or completion tracking.
- Production-grade instructional design.
- Generating lesson content from sources outside the approved Knowledge Foundation.
