"""
Dashboard collaboration agent — doctor checklist + generative UI state.

Additive to the session document agent. Does not touch apps/api or medtrace_agent.
State is UI-only (checklist, insights, focus); patient snapshot text is read-only context.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, List, Optional

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command


@tool
def update_dashboard_ui(
    insights_json: str = "[]",
    focus_type: str = "",
    focus_key: str = "",
    checklist_notes_json: str = "{}",
) -> str:
    """
    Update the patient-dashboard collaboration UI.

    Args:
        insights_json: JSON array of {id, title, body, relatedChecklistId?} objects
            to show as agent insight cards. Pass the full list to display.
        focus_type: Optional clinical card to highlight: lab | condition | med | alert.
        focus_key: Name/key of the card to highlight (e.g. lab test name).
        checklist_notes_json: JSON object mapping checklist item id -> short agentNote.
    """
    return "dashboard_ui_updated"


class DashboardAgentState(MessagesState):
    patient_id: Optional[str] = None
    patient_context: Optional[str] = None
    checklist: Optional[List[dict[str, Any]]] = None
    insights: Optional[List[dict[str, Any]]] = None
    focus: Optional[dict[str, Any]] = None
    tools: List[Any]


async def start_node(state: DashboardAgentState, config: RunnableConfig):  # pylint: disable=unused-argument
    return Command(goto="chat_node")


def _format_checklist(checklist: list[dict[str, Any]] | None) -> str:
    if not checklist:
        return "(empty)"
    lines = []
    for item in checklist:
        status = "DONE" if item.get("done") else "TODO"
        note = item.get("agentNote") or ""
        note_bit = f" — note: {note}" if note else ""
        lines.append(f"- [{status}] ({item.get('id')}) {item.get('text', '')}{note_bit}")
    return "\n".join(lines)


async def chat_node(state: DashboardAgentState, config: Optional[RunnableConfig] = None):
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
- Prefer short, actionable insights (1-3 cards max).
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

    model_name = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    base_url = os.environ.get("OPENAI_BASE_URL") or None
    api_key = os.environ.get("OPENAI_API_KEY") or None

    model = ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
    )

    if config is None:
        config = RunnableConfig(recursion_limit=25)

    model_with_tools = model.bind_tools(
        [
            *state.get("tools", []),
            update_dashboard_ui,
        ],
        parallel_tool_calls=False,
    )

    response = await model_with_tools.ainvoke(
        [
            SystemMessage(content=system_prompt),
            *state["messages"],
        ],
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
            # Summarize for the chat panel. UI widgets read insights/focus/checklist
            # from shared agent state — do not emit a frontend tool that can stall the run.
            summary_bits = []
            if new_insights:
                summary_bits.append(
                    "Updated insights: " + "; ".join(
                        str(i.get("title") or "note") for i in new_insights[:3]
                    )
                )
            if isinstance(new_focus, dict) and new_focus.get("key"):
                summary_bits.append(
                    f"Highlighted {new_focus.get('type')}: {new_focus.get('key')}"
                )
            summary = AIMessage(
                id=str(uuid.uuid4()),
                content=" ".join(summary_bits) or "Dashboard collaboration state updated.",
            )
            messages = messages + [tool_response, summary]

            return Command(
                goto=END,
                update={
                    "messages": messages,
                    "insights": new_insights,
                    "focus": new_focus,
                    "checklist": new_checklist,
                },
            )

    return Command(goto=END, update={"messages": messages})


workflow = StateGraph(DashboardAgentState)
workflow.add_node("start_node", start_node)
workflow.add_node("chat_node", chat_node)
workflow.set_entry_point("start_node")
workflow.add_edge(START, "start_node")
workflow.add_edge("start_node", "chat_node")
workflow.add_edge("chat_node", END)

memory = MemorySaver()
dashboard_graph = workflow.compile(checkpointer=memory)
