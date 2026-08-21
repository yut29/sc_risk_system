"""
Shared LLM client factory + JSON-call helper for all agents.

Small/fast models occasionally emit malformed JSON (e.g. a missing comma between
array items). Without a retry, that surfaces as an uncaught json.JSONDecodeError
all the way up to the Streamlit UI ("Pipeline error: Expecting ',' delimiter..."),
killing the whole run over one bad token.
"""

import json
import os
from typing import Any, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import BaseTool


def get_llm(
    temperature: float = 0.0,
    request_timeout: int = 60,
    max_retries: int = 1,
    provider: Optional[str] = None,
) -> BaseChatModel:
    """
    Provider: FAU's NHR HPC LLM gateway (OpenAI-compatible) — Baris pointed to
    this as a free option for this project.

    Model: Mistral-Small-3.2-24B-Instruct (not gpt-oss-120b, tried first) — the
    120B model hallucinated company names outside the provided context in
    testing and got noticeably more verbose/embellished in the free-form
    synthesis report. 24B stayed within JSON-schema/report-structure
    expectations more reliably.

    `provider` param: per-agent override, independent of the global
    LLM_PROVIDER env var.
    """
    provider = (provider or os.environ.get("LLM_PROVIDER", "fau")).lower()

    if provider == "fau":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model="RedHatAI/Mistral-Small-3.2-24B-Instruct-2506-FP8",
            base_url="https://hub.nhr.fau.de/api/llmgw/v1",
            api_key=os.environ["FAU_LLMAPI_KEY"],
            temperature=temperature,
            request_timeout=request_timeout,
            max_retries=max_retries,
        )
    else:
        raise ValueError(f"Unknown LLM_PROVIDER={provider!r} (expected 'fau')")


def _strip_code_fence(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    return content


def invoke_json(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    max_attempts: int = 3,
) -> dict[str, Any]:
    """
    Invokes the LLM and parses the response as JSON, retrying on malformed JSON
    (not on API/network errors — those should surface immediately).
    """
    last_error: Exception | None = None
    last_content = ""

    for _ in range(max_attempts):
        response = llm.invoke(messages)
        content = _strip_code_fence(response.content)
        last_content = content
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            last_error = e
            continue

    raise ValueError(
        f"LLM did not return valid JSON after {max_attempts} attempts: {last_error}\n"
        f"Last raw response:\n{last_content}"
    )


def invoke_json_with_tools(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    tools: list[BaseTool],
    max_tool_rounds: int = 3,
    max_json_attempts: int = 3,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Like invoke_json, but lets the LLM call tools (e.g. query real facility data) before
    producing its final JSON answer. Executes tool calls locally and feeds results back as
    ToolMessages, same manual loop pattern LangGraph itself uses under the hood — kept
    explicit here (not langgraph.prebuilt.create_react_agent) so this stays a plain function
    any agent node can call, consistent with the rest of this file.

    max_tool_rounds caps how many times the LLM may call tools before we force a final
    answer — small/free models can loop on tool calls instead of concluding; this is the
    same "don't trust the model to stop on its own" caution as MAX_VALIDATION_ITERATIONS
    in the pipeline graph.

    Returns (parsed_answer, tool_call_trace). Added 2026-08-07 (docs/open_issues.md P22):
    previously only the final answer was returned — whether a tool was even called, with
    what arguments, and what it returned was discarded the moment this function returned,
    so a severity judgment that was actually grounded in a query_facilities lookup looked
    identical to one the LLM just guessed. tool_call_trace is [] when no tool was called.
    """
    tools_by_name = {t.name: t for t in tools}
    llm_with_tools = llm.bind_tools(tools)
    convo = list(messages)
    tool_call_trace: list[dict[str, Any]] = []

    for _ in range(max_tool_rounds):
        response = llm_with_tools.invoke(convo)
        if not response.tool_calls:
            break
        convo.append(response)
        for call in response.tool_calls:
            tool_fn = tools_by_name[call["name"]]
            result = tool_fn.invoke(call["args"])
            tool_call_trace.append({
                "tool": call["name"],
                "args": call["args"],
                "result": str(result),
            })
            convo.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
    else:
        response = llm.invoke(convo)  # force a final, tool-free answer after the cap

    last_error: Exception | None = None
    last_content = ""
    for _ in range(max_json_attempts):
        content = _strip_code_fence(response.content)
        last_content = content
        try:
            return json.loads(content), tool_call_trace
        except json.JSONDecodeError as e:
            last_error = e
            response = llm.invoke(convo)  # retry without tools, same content-format fix as invoke_json

    raise ValueError(
        f"LLM did not return valid JSON after {max_json_attempts} attempts: {last_error}\n"
        f"Last raw response:\n{last_content}"
    )
