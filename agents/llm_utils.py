"""
Shared LLM-call helper for agents that expect a JSON object back.

Small/fast models (llama-3.1-8b-instant here) occasionally emit malformed JSON
(e.g. a missing comma between array items). Without a retry, that surfaces as an
uncaught json.JSONDecodeError all the way up to the Streamlit UI ("Pipeline error:
Expecting ',' delimiter..."), killing the whole run over one bad token.
"""

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage


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
