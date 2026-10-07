import json
import re
from enum import StrEnum
from time import perf_counter
import traceback
from typing import Any, Protocol

from app.knowledge import extract_citations, merge_citations
from app.models import AgentReply, Citation, LanguageReview, ResponseType, Risk, RiskLevel, Suggestion
from app.prompt_configuration import AgentPrompts
from app.settings import Settings
from app.tracing import TurnTrace, timed_milliseconds


class AgentRunner(Protocol):
    def create_session(self) -> Any: ...

    async def run(self, message: str, session: Any, trace: TurnTrace) -> AgentReply: ...


class _ApiKeyProjectClient:
    def __init__(self, openai_client: Any) -> None:
        self._openai_client = openai_client

    def get_openai_client(self, **_: Any) -> Any:
        return self._openai_client


class AgentRoute(StrEnum):
    UNDERWRITING = "underwriting"
    KNOWLEDGE = "kb"
    LARGE = "large"
    SMALL = "small"


_KNOWLEDGE_TERMS = (
    "flexlife",
    "life insurance",
    "policy",
    "premium",
    "coverage",
    "death benefit",
    "cash value",
    "rider",
    "illustration",
    "approved wording",
    "product guide",
)
_UNDERWRITING_TERMS = (
    "underwrit",
    "insurab",
    "medical history",
    "health condition",
    "medication",
    "diagnosis",
    "rate class",
    "table rating",
    "preferred rating",
    "standard rating",
    "approve my application",
    "decline my application",
)
_LANGUAGE_REVIEW_TERMS = (
    "approved wording",
    "what can i say",
    "what should i say",
    "tell the customer",
    "tell my client",
    "customer-facing",
    "client-facing",
    "advertis",
    "marketing",
    "email",
)
_LIGHTWEIGHT_TERMS = (
    "hello",
    "hi",
    "hey",
    "thanks",
    "thank you",
    "help",
)
_CONTINUATION_PREFIXES = (
    "also",
    "and ",
    "continue",
    "expand",
    "how so",
    "tell me more",
    "what about",
    "why",
)


def route_message(message: str, previous_route: AgentRoute | None = None) -> AgentRoute:
    normalized = message.casefold().strip()
    if any(term in normalized for term in _UNDERWRITING_TERMS):
        return AgentRoute.UNDERWRITING
    if any(term in normalized for term in _KNOWLEDGE_TERMS):
        return AgentRoute.KNOWLEDGE
    if previous_route is not None and normalized.startswith(_CONTINUATION_PREFIXES):
        return previous_route
    if normalized in _LIGHTWEIGHT_TERMS:
        return AgentRoute.SMALL
    # The application is FlexLife-only. Substantive unmatched requests go to the
    # grounded Knowledge agent, whose local instructions either connect supporting
    # concepts to FlexLife, ask for clarification, or decline unrelated requests.
    return AgentRoute.KNOWLEDGE


def requires_language_review(message: str, route: AgentRoute) -> bool:
    normalized = message.casefold()
    return route == AgentRoute.UNDERWRITING or any(term in normalized for term in _LANGUAGE_REVIEW_TERMS)


def underwriting_policy_violations(content: str) -> list[str]:
    prohibited = {
        r"\b(?:will|would) be (?:approved|declined)\b": "Predicts an approval or decline decision.",
        r"\bguarantee(?:d|s)? (?:approval|insurability|a rating)\b": "Guarantees an underwriting outcome.",
        r"\byou (?:will|would) qualify\b": "Predicts that the applicant will qualify.",
        r"\byou (?:will|would) receive (?:a )?(?:preferred|standard|table) rating\b": (
            "Predicts a specific underwriting rating."
        ),
    }
    normalized = content.casefold()
    return [message for pattern, message in prohibited.items() if re.search(pattern, normalized)]


def parse_language_review(content: str) -> LanguageReview | None:
    normalized = content.strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        normalized = re.sub(r"^```(?:json)?\s*|\s*```$", "", normalized, flags=re.IGNORECASE)
    try:
        object_start = normalized.index("{")
        payload, _ = json.JSONDecoder().raw_decode(normalized[object_start:])
        return LanguageReview.model_validate(payload)
    except (ValueError, TypeError):
        return None


class MicrosoftAgentFrameworkRunner:
    def __init__(self, settings: Settings, prompts: AgentPrompts) -> None:
        if not settings.foundry_is_configured:
            raise ValueError(
                "Foundry endpoint, API key, KB and specialist agents, and Large and Small models must be configured"
            )

        self._prompts = prompts

        from agent_framework import Agent
        from agent_framework.foundry import FoundryAgent
        from agent_framework.openai import OpenAIChatClient
        from openai import AsyncOpenAI

        api_key = settings.foundry_api_key
        assert api_key is not None
        assert settings.foundry_agent_kb is not None
        assert settings.foundry_model_large is not None
        assert settings.foundry_model_small is not None
        assert settings.underwriting_agent is not None
        assert settings.language_agent is not None
        base_url = f"{settings.foundry_project_endpoint.rstrip('/')}/openai/{settings.foundry_api_version}"
        self._openai_client = AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            base_url=base_url,
        )
        project_client = _ApiKeyProjectClient(self._openai_client)
        self._route_targets = {
            AgentRoute.UNDERWRITING: settings.underwriting_agent,
            AgentRoute.KNOWLEDGE: settings.foundry_agent_kb,
            AgentRoute.LARGE: settings.foundry_model_large,
            AgentRoute.SMALL: settings.foundry_model_small,
        }
        self._language_target = settings.language_agent
        self._large_target = settings.foundry_model_large
        self._small_target = settings.foundry_model_small
        self._knowledge_agent = FoundryAgent(
            project_client=project_client,
            agent_name=settings.foundry_agent_kb,
            name="FlexLifeKnowledgeAgent",
        )
        self._underwriting_agent = FoundryAgent(
            project_client=project_client,
            agent_name=settings.underwriting_agent,
            name="FlexLifeUnderwritingAgent",
        )
        self._language_agent = FoundryAgent(
            project_client=project_client,
            agent_name=settings.language_agent,
            name="FlexLifeLanguageAgent",
        )
        self._large_model = Agent(
            client=OpenAIChatClient(model=settings.foundry_model_large, async_client=self._openai_client),
            name="FlexLifeOrchestrator",
            instructions=self._prompts.orchestrator,
        )
        self._composition_model = Agent(
            client=OpenAIChatClient(model=settings.foundry_model_large, async_client=self._openai_client),
            name="FlexLifeCompositionAgent",
            instructions=self._prompts.compose,
        )
        self._revision_model = Agent(
            client=OpenAIChatClient(model=settings.foundry_model_large, async_client=self._openai_client),
            name="FlexLifeRevisionAgent",
            instructions=self._prompts.revise,
        )
        self._small_model = Agent(
            client=OpenAIChatClient(model=settings.foundry_model_small, async_client=self._openai_client),
            name="FlexLifeSmallModel",
            instructions=self._prompts.small,
        )

    def create_session(self) -> Any:
        return {"active_route": None, "history": []}

    async def run(self, message: str, session: Any, trace: TurnTrace) -> AgentReply:
        previous_route = session["active_route"]
        route = route_message(message, previous_route)
        context = self._conversation_context(session["history"])
        language_review_required = requires_language_review(message, route)
        trace.record(
            "Route current request",
            actor="Application Orchestrator (deterministic)",
            kind="routing",
            input_text=context,
            details={
                "previous_route": previous_route.value if previous_route is not None else None,
                "selected_route": route.value,
                "selected_target": self._route_targets[route],
                "language_review_required": language_review_required,
            },
        )

        if route == AgentRoute.UNDERWRITING:
            candidate, citations = await self._underwriting_candidate(message, context, trace)
            if not citations:
                reply = self._escalation(
                    "Authoritative underwriting evidence was not available for this answer.",
                    area="underwriting",
                    trace=trace,
                )
                return self._finish_turn(message, reply, route, session, trace)
        elif route == AgentRoute.KNOWLEDGE:
            response, citations = await self._invoke(
                self._knowledge_agent,
                self._knowledge_prompt(message, context),
                trace,
                feature_key="nlgagent.knowledge",
                actor="FlexLifeKnowledgeAgent",
                target=self._route_targets[route],
                purpose="Retrieve grounded product evidence",
                instructions=self._prompts.knowledge,
            )
            candidate = response.text
            if not citations:
                reply = self._escalation(
                    "Authoritative product evidence was not available for this answer.", trace=trace
                )
                return self._finish_turn(message, reply, route, session, trace)
        else:
            model = self._large_model if route == AgentRoute.LARGE else self._small_model
            is_large = route == AgentRoute.LARGE
            response, _ = await self._invoke(
                model,
                self._task_prompt(message, context),
                trace,
                feature_key="nlgagent.orchestrator" if is_large else "nlgagent.small",
                actor="FlexLifeOrchestrator" if is_large else "FlexLifeSmallModel",
                target=self._large_target if is_large else self._small_target,
                purpose="Generate the initial candidate response",
                instructions=self._prompts.orchestrator if is_large else self._prompts.small,
            )
            candidate = response.text
            citations = []

        if route == AgentRoute.UNDERWRITING:
            violations = underwriting_policy_violations(candidate)
            trace.record(
                "Apply underwriting policy gate",
                actor="Application Orchestrator (deterministic)",
                kind="policy_check",
                input_text=candidate,
                details={"violations": violations, "passed": not violations},
            )
            if violations:
                candidate = await self._revise_candidate(message, candidate, violations, trace)

        if language_review_required:
            reviewed = await self._apply_language_gate(message, candidate, citations, route, trace)
            if isinstance(reviewed, AgentReply):
                return self._finish_turn(message, reviewed, route, session, trace)
            candidate, citations = reviewed

        final_violations = underwriting_policy_violations(candidate) if route == AgentRoute.UNDERWRITING else []
        if route == AgentRoute.UNDERWRITING:
            trace.record(
                "Apply final underwriting policy gate",
                actor="Application Orchestrator (deterministic)",
                kind="policy_check",
                input_text=candidate,
                details={"violations": final_violations, "passed": not final_violations},
            )
        if final_violations:
            reply = self._escalation(
                "The proposed response did not pass the underwriting policy gate.",
                citations,
                area="underwriting",
                trace=trace,
            )
            return self._finish_turn(message, reply, route, session, trace)

        session["active_route"] = route
        reply = AgentReply(
            content=candidate,
            citations=citations,
            risk=Risk(
                area=(
                    "underwriting"
                    if route == AgentRoute.UNDERWRITING
                    else "communication"
                    if language_review_required
                    else "general"
                ),
                level=(
                    RiskLevel.SENSITIVE
                    if route == AgentRoute.UNDERWRITING or language_review_required
                    else RiskLevel.NORMAL
                ),
            ),
            warning=(
                "Published underwriting guidance is informational and does not predict an underwriting decision."
                if route == AgentRoute.UNDERWRITING
                else None
            ),
        )
        return self._finish_turn(message, reply, route, session, trace)

    async def _underwriting_candidate(
        self, message: str, context: str, trace: TurnTrace
    ) -> tuple[str, list[Citation]]:
        evidence, citations = await self._invoke(
            self._underwriting_agent,
            self._underwriting_prompt(message, context),
            trace,
            feature_key="nlgagent.underwriting",
            actor="FlexLifeUnderwritingAgent",
            target=self._route_targets[AgentRoute.UNDERWRITING],
            purpose="Retrieve underwriting evidence",
            instructions=self._prompts.underwriting,
        )
        if not citations:
            return "", []
        composed, _ = await self._invoke(
            self._composition_model,
            self._composition_prompt(message, evidence.text),
            trace,
            feature_key="nlgagent.compose",
            actor="FlexLifeOrchestrator",
            target=self._large_target,
            purpose="Compose a user-facing answer from specialist evidence",
            instructions=self._prompts.compose,
        )
        return composed.text, citations

    async def _apply_language_gate(
        self,
        message: str,
        candidate: str,
        citations: list[Citation],
        route: AgentRoute,
        trace: TurnTrace,
    ) -> tuple[str, list[Citation]] | AgentReply:
        try:
            review_response, review_citations = await self._invoke(
                self._language_agent,
                self._language_review_prompt(message, candidate),
                trace,
                feature_key="nlgagent.language",
                actor="FlexLifeLanguageAgent",
                target=self._language_target,
                purpose="Validate the candidate against language policy",
                instructions=self._prompts.language,
            )
        except Exception:
            return self._escalation(
                "The required language review could not be completed.",
                citations,
                area="underwriting" if route == AgentRoute.UNDERWRITING else "communication",
                trace=trace,
            )

        review = parse_language_review(review_response.text)
        trace.record(
            "Interpret language review",
            actor="Application Orchestrator (deterministic)",
            kind="policy_check",
            input_text=review_response.text,
            details=review.model_dump(mode="json") if review is not None else {"valid": False},
        )
        if review is None:
            return self._escalation(
                "The language review returned an invalid result.", citations, trace=trace
            )
        citations_required = route != AgentRoute.UNDERWRITING or not review.passed
        if citations_required and not review_citations:
            return self._escalation(
                "Authoritative language guidance was not available for the required review.",
                citations,
                area="underwriting" if route == AgentRoute.UNDERWRITING else "communication",
                trace=trace,
            )
        if review.passed:
            return candidate, merge_citations(citations, review_citations)

        candidate = await self._revise_candidate(
            message,
            candidate,
            review.violations + review.required_changes,
            trace,
        )
        try:
            second_review, second_review_citations = await self._invoke(
                self._language_agent,
                self._language_review_prompt(message, candidate),
                trace,
                feature_key="nlgagent.language",
                actor="FlexLifeLanguageAgent",
                target=self._language_target,
                purpose="Validate the revised candidate against language policy",
                instructions=self._prompts.language,
            )
        except Exception:
            return self._escalation(
                "The revised response could not be validated against the language guidance.",
                merge_citations(citations, review_citations),
                area="underwriting" if route == AgentRoute.UNDERWRITING else "communication",
                trace=trace,
            )
        all_citations = merge_citations(citations, review_citations, second_review_citations)
        second_result = parse_language_review(second_review.text)
        trace.record(
            "Interpret revised language review",
            actor="Application Orchestrator (deterministic)",
            kind="policy_check",
            input_text=second_review.text,
            details=(
                second_result.model_dump(mode="json") if second_result is not None else {"valid": False}
            ),
        )
        if second_result is None or not second_result.passed:
            return self._escalation(
                "The response could not be brought into compliance with the language guidance.",
                all_citations,
                area="underwriting" if route == AgentRoute.UNDERWRITING else "communication",
                trace=trace,
            )
        return candidate, all_citations

    async def _revise_candidate(
        self, message: str, candidate: str, changes: list[str], trace: TurnTrace
    ) -> str:
        prompt = f"""USER REQUEST:
{message}

CANDIDATE:
{candidate}

REQUIRED CHANGES:
{json.dumps(changes)}"""
        response, _ = await self._invoke(
            self._revision_model,
            prompt,
            trace,
            feature_key="nlgagent.revise",
            actor="FlexLifeOrchestrator",
            target=self._large_target,
            purpose="Revise the candidate to satisfy policy findings",
            instructions=self._prompts.revise,
        )
        return response.text

    async def _invoke(
        self,
        agent: Any,
        prompt: str,
        trace: TurnTrace,
        *,
        feature_key: str,
        actor: str,
        target: str,
        purpose: str,
        instructions: str | None = None,
    ) -> tuple[Any, list[Citation]]:
        started = perf_counter()
        try:
            response = await agent.run(prompt)
        except Exception as error:
            trace.record(
                f"{actor}: {purpose}",
                actor=actor,
                kind="agent_call",
                status="failed",
                input_text=prompt,
                details={
                    "feature_key": feature_key,
                    "target": target,
                    "purpose": purpose,
                    "application_instructions": instructions,
                    "exception_type": type(error).__name__,
                    "message": str(error),
                    "traceback": "".join(traceback.format_exception(error)),
                },
                duration_ms=timed_milliseconds(started),
            )
            raise
        citations = extract_citations(response)
        trace.record(
            f"{actor}: {purpose}",
            actor=actor,
            kind="agent_call",
            input_text=prompt,
            output_text=response.text,
            details={
                "feature_key": feature_key,
                "target": target,
                "purpose": purpose,
                "application_instructions": instructions,
                "citations": [citation.model_dump(mode="json") for citation in citations],
                "foundry_managed_configuration_captured": False,
            },
            duration_ms=timed_milliseconds(started),
        )
        return response, citations

    def _finish_turn(
        self,
        message: str,
        reply: AgentReply,
        route: AgentRoute,
        session: Any,
        trace: TurnTrace,
    ) -> AgentReply:
        session["active_route"] = route
        session["history"].extend((("user", message), ("assistant", reply.content)))
        session["history"] = session["history"][-12:]
        trace.record(
            "Update conversation state",
            actor="Application Orchestrator (deterministic)",
            kind="state_update",
            details={
                "active_route": route.value,
                "history_message_count": len(session["history"]),
                "response_type": reply.response_type.value,
                "risk": reply.risk.model_dump(mode="json"),
            },
        )
        return reply

    @staticmethod
    def _conversation_context(history: list[tuple[str, str]]) -> str:
        if not history:
            return "No prior conversation context."
        return "\n".join(f"{role.upper()}: {content}" for role, content in history[-8:])

    @staticmethod
    def _task_prompt(message: str, context: str) -> str:
        return f"CONVERSATION CONTEXT:\n{context}\n\nCURRENT USER REQUEST:\n{message}"

    def _underwriting_prompt(self, message: str, context: str) -> str:
        return self._foundry_prompt(self._prompts.underwriting, f"""CONVERSATION CONTEXT:
{context}

CURRENT USER REQUEST:
{message}""")

    @staticmethod
    def _composition_prompt(message: str, evidence: str) -> str:
        return f"""USER REQUEST:
{message}

UNDERWRITING SPECIALIST EVIDENCE:
{evidence}"""

    def _language_review_prompt(self, message: str, candidate: str) -> str:
        return self._foundry_prompt(self._prompts.language, f"""USER REQUEST:
{message}

CANDIDATE RESPONSE:
{candidate}""")

    def _knowledge_prompt(self, message: str, context: str) -> str:
        return self._foundry_prompt(
            self._prompts.knowledge,
            self._task_prompt(message, context),
        )

    @staticmethod
    def _foundry_prompt(instructions: str, task: str) -> str:
        """Carry local instructions in input because named Foundry agents reject that API field."""
        return f"""LOCAL APPLICATION INSTRUCTIONS:
{instructions}

CURRENT TASK INPUT:
{task}"""

    @staticmethod
    def _escalation(
        reason: str,
        citations: list[Citation] | None = None,
        *,
        area: str = "grounding",
        trace: TurnTrace | None = None,
    ) -> AgentReply:
        if trace is not None:
            trace.record(
                "Escalate response",
                actor="Application Orchestrator (deterministic)",
                kind="escalation",
                details={"reason": reason, "area": area},
            )
        return AgentReply(
            response_type=ResponseType.ESCALATION,
            content=f"I cannot provide a reliable answer right now. {reason}",
            citations=citations or [],
            risk=Risk(area=area, level=RiskLevel.HIGH),
            warning="This request needs human review.",
            suggestions=[Suggestion(action="ESCALATE_QUESTION", label="Escalate this question")],
        )

    async def close(self) -> None:
        await self._openai_client.close()


class DemoAgentRunner:
    """Credential-free runner for UI development; it makes no product claims."""

    def create_session(self) -> dict[str, list[str]]:
        return {"messages": []}

    async def run(self, message: str, session: dict[str, list[str]], trace: TurnTrace) -> AgentReply:
        session["messages"].append(message)
        return AgentReply(
            response_type=ResponseType.ESCALATION,
            content=(
                "I can run the conversation flow, but authoritative FlexLife knowledge "
                "is not configured yet. Connect a Foundry project and knowledge source "
                "before using product-specific answers."
            ),
            risk=Risk(area="grounding", level=RiskLevel.SENSITIVE),
            warning="Demo mode does not provide FlexLife product guidance.",
            suggestions=[Suggestion(action="ESCALATE_QUESTION", label="Escalate this question")],
        )
