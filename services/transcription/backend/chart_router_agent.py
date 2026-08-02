"""
Chart router supervisor — one CopilotKit chat, auto-routes to specialists.

Routes:
  - collab  → checklist / insights / chart highlight UI updates
  - memory  → Zep/FHIR clinical Q&A via clinical_memory_agent helpers

Future specialists (research_critic, etc.) plug in as additional goto targets.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, List, Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command

from clinical_memory_agent import _ask_api, _last_user_text
from dashboard_agent import _format_checklist, update_dashboard_ui
from model_config import openai_model

RouteName = Literal["collab", "memory"]


class ChartRouterState(MessagesState):
    patient_id: Optional[str] = None
    patient_context: Optional[str] = None
    checklist: Optional[List[dict[str, Any]]] = None
    insights: Optional[List[dict[str, Any]]] = None
    focus: Optional[dict[str, Any]] = None
    route: Optional[str] = None
    tools: List[Any]


_COLLAB_HINTS = re.compile(
    r"\b("
    r"checklist|insight|highlight|mark\s+(as\s+)?done|co-?pilot\s+ui|"
    r"update_dashboard|focus|what's\s+left|whats\s+left|review\s+item"
    r")\b",
    re.I,
)


def _heuristic_route(user_text: str) -> RouteName:
    if _COLLAB_HINTS.search(user_text or ""):
        return "collab"
    return "memory"


async def _llm_route(user_text: str, config: Optional[RunnableConfig]) -> RouteName:
    model = ChatOpenAI(
        model=openai_model(),
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        api_key=os.environ.get("OPENAI_API_KEY") or None,
        temperature=0,
    )
    response = await model.ainvoke(
        [
            SystemMessage(
                content=(
                    "You route doctor messages on a patient chart to exactly one specialist.\n"
                    "Reply with only one word: collab OR memory.\n"
                    "- collab: checklist review, mark items done, insight cards, UI highlights, "
                    "what is left on the checklist, update the dashboard.\n"
                    "- memory: clinical questions about the patient chart, allergies, meds, "
                    "conditions, labs, timeline, documents, summarize the chart.\n"
                    "Default to memory when unsure."
                )
            ),
            HumanMessage(content=user_text or ""),
        ],
        config or RunnableConfig(recursion_limit=5),
    )
    raw = response.content if isinstance(response.content, str) else str(response.content)
    token = raw.strip().split()[0].lower().strip(".,:;\"'") if raw.strip() else "memory"
    if token.startswith("collab"):
        return "collab"
    return "memory"


async def start_node(state: ChartRouterState, config: RunnableConfig):  # pylint: unused-argument
    return Command(goto="route_node")


async def route_node(state: ChartRouterState, config: Optional[RunnableConfig] = None):
    user_text = _last_user_text(state.get("messages") or [])
    route: RouteName
    try:
        route = await _llm_route(user_text, config)
    except Exception:
        route = _heuristic_route(user_text)
    # Explicit checklist-done prompts from the UI should always hit collab.
    if "marked checklist item" in user_text.lower() or "update_dashboard_ui" in user_text.lower():
        route = "collab"
    target = "collab_node" if route == "collab" else "memory_node"
    return Command(goto=target, update={"route": route})


async def collab_node(state: ChartRouterState, config: Optional[RunnableConfig] = None):
    """Same generative-UI behavior as dashboard_clinical, inlined for shared state."""
    checklist = state.get("checklist") or []
    insights = state.get("insights") or []
    focus = state.get("focus")
    patient_context = state.get("patient_context") or "(no patient context provided)"

    system_prompt = f"""
You are a non-diagnostic clinical decision-support co-pilot on a patient chart dashboard.
You help the doctor review a suggested checklist. When the doctor marks items done, analyze
what that implies using the patient context and update the UI via update_dashboard_ui.

Rules:
- Non-diagnostic cognitive aid only. Do not claim certainty or replace clinical judgment.
- ALWAYS call update_dashboard_ui when answering — never reply with text only.
- insights_json MUST be a JSON array with 1-3 objects: {{"id","title","body","relatedChecklistId"?}}.
  Never pass an empty insights_json array when you have something useful to say.
- When highlighting, set focus_type + focus_key to a real lab/condition/med/alert name from context.
- Use checklist item ids from the list below when setting relatedChecklistId or notes.
- After calling update_dashboard_ui, briefly tell the doctor what you changed (2 sentences max).
- If the doctor asks what's left, summarize unchecked items; you may add a note on the next item.

Patient context:
----
{patient_context}
----

Current checklist:
{_format_checklist(checklist)}

Current insights JSON:
{json.dumps(insights)}

Current focus JSON:
{json.dumps(focus)}
"""

    model = ChatOpenAI(
        model=openai_model(),
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        api_key=os.environ.get("OPENAI_API_KEY") or None,
    )
    if config is None:
        config = RunnableConfig(recursion_limit=25)

    model_with_tools = model.bind_tools(
        [*state.get("tools", []), update_dashboard_ui],
        parallel_tool_calls=False,
    )
    response = await model_with_tools.ainvoke(
        [SystemMessage(content=system_prompt), *state["messages"]],
        config,
    )
    messages = state["messages"] + [response]

    if hasattr(response, "tool_calls") and response.tool_calls:
        tool_call = response.tool_calls[0]
        if isinstance(tool_call, dict):
            tool_call_id = tool_call["id"]
            tool_call_name = tool_call["name"]
            tool_call_args = tool_call["args"]
        else:
            tool_call_id = tool_call.id
            tool_call_name = tool_call.name
            tool_call_args = tool_call.args

        if tool_call_name == "update_dashboard_ui":
            new_insights = insights
            new_focus = focus
            new_checklist = [dict(item) for item in checklist]

            raw_insights = tool_call_args.get("insights_json") or "[]"
            try:
                parsed = json.loads(raw_insights) if isinstance(raw_insights, str) else raw_insights
                if isinstance(parsed, list):
                    new_insights = parsed
            except json.JSONDecodeError:
                pass

            focus_type = (tool_call_args.get("focus_type") or "").strip()
            focus_key = (tool_call_args.get("focus_key") or "").strip()
            if focus_type and focus_key:
                new_focus = {"type": focus_type, "key": focus_key}
            elif focus_type == "" and focus_key == "":
                pass
            else:
                new_focus = None

            raw_notes = tool_call_args.get("checklist_notes_json") or "{}"
            try:
                notes = json.loads(raw_notes) if isinstance(raw_notes, str) else raw_notes
            except json.JSONDecodeError:
                notes = {}
            if isinstance(notes, dict):
                for item in new_checklist:
                    item_id = item.get("id")
                    if item_id in notes:
                        item["agentNote"] = str(notes[item_id])

            tool_response = ToolMessage(
                id=str(uuid.uuid4()),
                content="Dashboard UI updated.",
                tool_call_id=tool_call_id,
            )
            summary_bits = []
            if new_insights:
                summary_bits.append(
                    "Updated insights: "
                    + "; ".join(str(i.get("title") or "note") for i in new_insights[:3])
                )
            if isinstance(new_focus, dict) and new_focus.get("key"):
                summary_bits.append(
                    f"Highlighted {new_focus.get('type')}: {new_focus.get('key')}"
                )
            summary = AIMessage(
                id=str(uuid.uuid4()),
                content=" ".join(summary_bits) or "Dashboard collaboration state updated.",
            )
            return Command(
                goto=END,
                update={
                    "messages": messages + [tool_response, summary],
                    "insights": new_insights,
                    "focus": new_focus,
                    "checklist": new_checklist,
                },
            )

    return Command(goto=END, update={"messages": messages})


async def memory_node(state: ChartRouterState, config: Optional[RunnableConfig] = None):
    patient_id = (state.get("patient_id") or "").strip()
    user_text = _last_user_text(state.get("messages") or []).strip()
    patient_context = state.get("patient_context") or ""

    if not patient_id:
        reply = "No patient is selected in agent state, so clinical memory cannot run."
        return Command(
            goto=END,
            update={"messages": state["messages"] + [AIMessage(id=str(uuid.uuid4()), content=reply)]},
        )
    if not user_text:
        reply = "Ask a clinical question about this patient chart."
        return Command(
            goto=END,
            update={"messages": state["messages"] + [AIMessage(id=str(uuid.uuid4()), content=reply)]},
        )

    try:
        answer = await _ask_api(patient_id, user_text, deep=False)
    except Exception as exc:  # noqa: BLE001
        if patient_context.strip():
            model = ChatOpenAI(
                model=openai_model(),
                base_url=os.environ.get("OPENAI_BASE_URL") or None,
                api_key=os.environ.get("OPENAI_API_KEY") or None,
            )
            response = await model.ainvoke(
                [
                    SystemMessage(
                        content=(
                            "You are a non-diagnostic clinical decision-support assistant. "
                            "Answer only from the patient context below.\n\n"
                            f"{patient_context}\n\n(API unavailable: {exc})"
                        )
                    ),
                    HumanMessage(content=user_text),
                ],
                config or RunnableConfig(recursion_limit=10),
            )
            answer = (
                response.content
                if isinstance(response.content, str)
                else str(response.content)
            )
        else:
            answer = (
                f"Clinical memory is unavailable ({exc}). "
                "Confirm `npm run dev:api` is running with Medplum configured."
            )

    return Command(
        goto=END,
        update={
            "messages": state["messages"]
            + [AIMessage(id=str(uuid.uuid4()), content=answer)],
        },
    )


workflow = StateGraph(ChartRouterState)
workflow.add_node("start_node", start_node)
workflow.add_node("route_node", route_node)
workflow.add_node("collab_node", collab_node)
workflow.add_node("memory_node", memory_node)
workflow.set_entry_point("start_node")
workflow.add_edge(START, "start_node")
workflow.add_edge("start_node", "route_node")
# route_node uses Command(goto=...)
workflow.add_edge("collab_node", END)
workflow.add_edge("memory_node", END)

memory = MemorySaver()
chart_router_graph = workflow.compile(checkpointer=memory)
