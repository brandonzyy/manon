"""Worker LLM agent loop — tool-calling cycle with primary/fallback model switch.

Ported from donnie/agent/lib/llm-agent.js callAgent().
Does its own httpx calls (with tools param) since services/llm.py llm_chat() doesn't support tools.
Reuses _resolve_model() from services/llm.py for model config resolution.

Supported tool-call formats (auto-detected, normalized to OpenAI standard internally):
  - OpenAI standard : tool_calls array in message
  - ZhipuAI XML     : <tool_call>name<arg_key>k</arg_key><arg_value>v</arg_value></tool_call>
  - Anthropic        : content array with tool_use blocks (separate API path, TODO)
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from .tools import TOOL_DEFINITIONS, execute_tool

log = logging.getLogger("manon.worker.agent")

MAX_AGENT_TURNS = 25
MAX_PRIMARY_FAILS = 3


# ── Tool call format normalization ────────────────────────────────────────────

def _parse_xml_tool_calls(content: str) -> list[dict]:
    """Parse ZhipuAI-style XML tool calls from content string.

    Format: <tool_call>FUNC<arg_key>K</arg_key><arg_value>V</arg_value>...</tool_call>
    Returns list in standard OpenAI tool_calls format.
    """
    results = []
    for i, block in enumerate(re.findall(r"<tool_call>(.*?)</tool_call>", content, re.DOTALL)):
        name_match = re.match(r"^([^<\s]+)", block.strip())
        if not name_match:
            continue
        fn_name = name_match.group(1).strip()
        keys = re.findall(r"<arg_key>(.*?)</arg_key>", block, re.DOTALL)
        values = re.findall(r"<arg_value>(.*?)</arg_value>", block, re.DOTALL)
        args = {k.strip(): v.strip() for k, v in zip(keys, values)}
        results.append({
            "id": f"xml_{i}",
            "type": "function",
            "function": {"name": fn_name, "arguments": json.dumps(args)},
        })
    return results


def _normalize_tool_calls(msg: dict) -> bool:
    """Normalize tool calls in msg to standard OpenAI format in-place.

    Returns True if a non-standard (XML) format was detected — callers use this
    to decide how to format tool result messages back to the model.
    """
    if msg.get("tool_calls"):
        return False  # Already standard OpenAI format

    content = msg.get("content") or ""

    # ZhipuAI XML format: <tool_call>...</tool_call>
    if "<tool_call>" in content:
        tool_calls = _parse_xml_tool_calls(content)
        if tool_calls:
            msg["tool_calls"] = tool_calls
            clean = re.sub(r"<tool_call>.*?</tool_call>", "", content, flags=re.DOTALL).strip()
            msg["content"] = clean or None
            return True

    return False


def _make_tool_result_message(tc: dict, result: dict, used_xml: bool) -> dict:
    """Format a tool result message in the format the model expects."""
    result_str = json.dumps(result, ensure_ascii=False)[:16000]
    if used_xml:
        fn_name = tc.get("function", {}).get("name", "tool")
        return {
            "role": "user",
            "content": f"<tool_response>\n{fn_name}\n{result_str}\n</tool_response>",
        }
    return {
        "role": "tool",
        "tool_call_id": tc.get("id", ""),
        "content": result_str,
    }


async def _llm_chat_with_tools(
    client: httpx.AsyncClient,
    messages: list[dict],
    model: str,
    api_url: str,
    api_key: str,
    api_format: str = "openai",
    timeout: float = 120.0,
) -> dict:
    """Single LLM API call with tools. Returns raw API response dict."""
    if api_format == "anthropic":
        raise NotImplementedError("Anthropic tool-calling not yet supported in worker agent")

    body = {
        "model": model,
        "max_tokens": 4096,
        "tools": TOOL_DEFINITIONS,
        "messages": messages,
    }
    resp = await client.post(
        api_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    msg = (data.get("choices") or [{}])[0].get("message", {})
    # Normalize any non-standard tool call format to OpenAI standard
    _normalize_tool_calls(msg)
    # Reject reasoning-only responses (no content and no tool_calls)
    if msg and not msg.get("content") and not msg.get("tool_calls") and msg.get("reasoning_content"):
        raise RuntimeError("Model returned reasoning only (no content or tool_calls)")
    return data


async def call_agent(
    task_prompt: str,
    system_prompt: str,
    repo_root: str,
    model_name: str,
    api_url: str,
    api_key: str,
    api_format: str = "openai",
    fallback_model: str | None = None,
    fallback_api_url: str | None = None,
    fallback_api_key: str | None = None,
    fallback_api_format: str | None = None,
) -> dict:
    """Run the agent loop: LLM → tool calls → LLM → ... until done or MAX_AGENT_TURNS.

    Returns {"output": str, "turns": int}.
    """
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_prompt},
    ]

    active_model = model_name
    active_url = api_url
    active_key = api_key
    active_format = api_format
    primary_fail_count = 0
    final_output = ""
    turn = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    collected_queries: list[dict] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
        for turn in range(MAX_AGENT_TURNS):
            log.info("Agent turn %d/%d [%s]", turn + 1, MAX_AGENT_TURNS, active_model)

            data = None
            timeout_val = 90.0 if (turn == 0 and active_model == model_name) else 120.0

            # Try primary model
            try:
                data = await _llm_chat_with_tools(
                    client, messages, active_model, active_url, active_key, active_format, timeout_val,
                )
            except Exception as exc:
                if active_model == model_name and fallback_model and fallback_model != model_name:
                    primary_fail_count += 1
                    log.warning(
                        "%s failed (%s), trying fallback %s (fail %d/%d)",
                        active_model, exc, fallback_model, primary_fail_count, MAX_PRIMARY_FAILS,
                    )
                    if primary_fail_count >= MAX_PRIMARY_FAILS:
                        log.info("Switching permanently to fallback model %s", fallback_model)
                        active_model = fallback_model
                        active_url = fallback_api_url or api_url
                        active_key = fallback_api_key or api_key
                        active_format = fallback_api_format or api_format
                    try:
                        data = await _llm_chat_with_tools(
                            client, messages,
                            fallback_model,
                            fallback_api_url or api_url,
                            fallback_api_key or api_key,
                            fallback_api_format or api_format,
                        )
                    except Exception as exc2:
                        raise RuntimeError(f"Both models failed. Fallback: {exc2}") from exc2
                else:
                    raise

            # Accumulate token usage
            usage = data.get("usage") or {}
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)

            choice = (data.get("choices") or [None])[0]
            if not choice:
                log.warning("No choice in LLM response")
                break

            msg = choice.get("message", {})
            # Append assistant message to history
            assistant_msg: dict = {"role": "assistant", "content": msg.get("content") or ""}
            if msg.get("tool_calls"):
                assistant_msg["tool_calls"] = msg["tool_calls"]
            messages.append(assistant_msg)

            if msg.get("content"):
                final_output = msg["content"]
                log.info("LLM: %s", msg["content"][:200])

            # No tool calls → agent finished
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                log.info("Agent finished (no more tool calls)")
                break

            # Detect format used this turn (for tool result messages)
            used_xml = any(tc.get("id", "").startswith("xml_") for tc in tool_calls)

            # Execute tool calls
            for tc in tool_calls:
                fn = tc.get("function", {})
                fn_name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                # Logging
                if fn_name == "read_file":
                    log.info("  Tool: %s(%s)", fn_name, args.get("path", ""))
                elif fn_name == "edit_file":
                    log.info("  Tool: %s(%s)", fn_name, args.get("path", ""))
                elif fn_name == "run_command":
                    log.info("  Tool: %s(%s)", fn_name, (args.get("command") or "")[:80])
                elif fn_name == "search_code":
                    log.info("  Tool: %s(%s)", fn_name, args.get("query", ""))
                else:
                    log.info("  Tool: %s(...)", fn_name)

                result = await execute_tool(fn_name, args, repo_root)
                # Collect query logs from search_code tool
                if result.get("_query_log"):
                    collected_queries.append(result.pop("_query_log"))
                if result.get("error"):
                    log.info("  -> Error: %s", result["error"])
                elif result.get("success"):
                    log.info("  -> %s", result.get("message", "ok"))

                messages.append(_make_tool_result_message(tc, result, used_xml))

    # Estimate tokens if API didn't return usage
    if total_prompt_tokens == 0 and total_completion_tokens == 0:
        total_prompt_tokens = sum(len(m.get("content", "")) // 4 for m in messages if m.get("role") != "assistant")
        total_completion_tokens = len(final_output) // 4

    return {
        "output": final_output,
        "turns": turn + 1,
        "active_model": active_model,
        "token_usage": {
            "prompt": total_prompt_tokens,
            "completion": total_completion_tokens,
            "total": total_prompt_tokens + total_completion_tokens,
        },
        "queries": collected_queries,
    }
