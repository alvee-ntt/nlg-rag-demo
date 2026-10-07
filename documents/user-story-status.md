# Agent Navigator user story status

Status as of 7 October 2026.

Shareable page: https://claude.ai/artifact/Cv4xhzbUCTo2Bt1Cy3kGHr (a copy is saved next to this file as `user-story-status.html`).

**6 complete · 9 in progress · 0 not yet started**

| ID | User story | Status | Working today | Remaining |
|---|---|---|---|---|
| M01 | Knowledge Foundation | ✅ Complete | Approved source documents are ingested, de-duplicated and searchable, with page-level source references. | None |
| M02 | Natural Language Search | ✅ Complete | Agents ask questions in plain language. Questions unrelated to FlexLife are politely declined. | None |
| M03 | Citations | ✅ Complete | Every answer lists its sources. When no approved source supports an answer, the agent is referred to NLG Support. | None |
| M04 | Follow-up Questions | ✅ Complete | Follow-up questions are understood in the context of the conversation so far. | None |
| M05 | Voice / Text | 🟡 In progress | Typed questions and answers. Spoken questions through the browser's microphone. | Spoken answers in chat, and the setting that chooses between voice and text. |
| M06 | Explain Like… (answer preferences) | 🟡 In progress | Agents choose answer length, format, tone and plain-language wording, applied to every answer. | Saving preferences to the agent's profile so they follow the agent across devices. |
| M07 | Underwriting Technicals | 🟡 In progress | Underwriting answers are grounded in the underwriting guide. For a described client, the app shows what the guide lays out, with page references and a "guidance only" notice, and never states an approval or rate class. | The "guidance only" notice on general underwriting questions, and automatic referral to NLG when an agent asks whether a specific client will be approved. |
| M08 | Guiding Prompts | ✅ Complete | When an agent describes a client, the app shows the facts it picked up, identifies what the underwriting guide treats as missing, and walks the agent through those questions without repeating itself. | None |
| M09 | Handoff | ✅ Complete | Agents can prepare an editable request to NLG Support from any answer, pre-filled from the conversation. Offered automatically when the library cannot answer. | None |
| M10 | Provide Appropriate Links | 🟡 In progress | Underwriting guide references open the guide at the cited page. | Opening the original document from every citation on a standard chat answer. |
| M11 | Learning Library | 🟡 In progress | The library of learning resources is listed in the Learn area. | Connecting each resource to its destination so it opens when selected. |
| M12 | User Preferences | 🟡 In progress | Agents can set how answers are written: a warm, neutral or formal tone, short or detailed length, bullets or prose, and plain-language wording. | — |
| M13 | Lesson Progress Tracking | 🟡 In progress | Lessons resume where the agent left off, with a "Continue listening" shortcut. | Expiry of older lessons and their saved progress. |
| M14 | Agent Profile and Progress | 🟡 In progress | A profile screen showing the agent's practice level and skills from role-play sessions. | — |
| M15 | Specialized Learning | 🟡 In progress | Agents request a custom lesson on any topic and receive an article or audio lesson built from the source library. | Telling the agent when the library does not cover a topic, and applying answer preferences to lessons. |

## Notes

- **Complete** means the story's acceptance criteria are met in the current build. **In progress** means part of the story is working and any items under Remaining are still open.
- **M08 Guiding Prompts** currently covers client details the underwriting guide distinguishes, such as age, coverage amount, tobacco use, build and medical conditions.
