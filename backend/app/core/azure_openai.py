from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.core.config import settings


class AzureOpenAIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _parse_json_content(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        text = "\n".join(
            str(item.get("text"))
            for item in value
            if isinstance(item, dict) and item.get("text")
        )
        value = text
    text = str(value or "").strip()
    if text.startswith("```"):
        text = text[3:]
        if text.lower().startswith("json"):
            text = text[4:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise AzureOpenAIError("Azure OpenAI returned a non-object response.")
    return parsed


def _extract_response(payload: dict[str, Any]) -> dict[str, Any]:
    output = payload.get("output")
    if isinstance(output, dict):
        choices = output.get("choices") or []
        if choices:
            message = (choices[0] or {}).get("message") or {}
            parsed = message.get("parsed")
            if parsed is not None:
                return _parse_json_content(parsed)
            if message.get("content") is not None:
                return _parse_json_content(message["content"])

    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "refusal":
                raise AzureOpenAIError("Azure OpenAI refused the request.")
            for block in item.get("content") or []:
                if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                    return _parse_json_content(block.get("text"))

    if payload.get("output_text") is not None:
        return _parse_json_content(payload["output_text"])

    choices = payload.get("choices") or []
    if choices:
        message = (choices[0] or {}).get("message") or {}
        parsed = message.get("parsed")
        if parsed is not None:
            return _parse_json_content(parsed)
        if message.get("content") is not None:
            return _parse_json_content(message["content"])

    raise AzureOpenAIError("Azure OpenAI returned an empty or unusable response.")


def _provider_error_message(error: urllib.error.HTTPError, api_key: str, endpoint: str) -> str:
    try:
        payload = json.loads(error.read(8192).decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return f"Azure OpenAI request failed with HTTP {error.code}."

    detail = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(detail, dict):
        return f"Azure OpenAI request failed with HTTP {error.code}."

    code = str(detail.get("code") or "").strip()
    message = str(detail.get("message") or "").strip()
    message = message.replace(api_key, "[redacted]") if api_key else message
    message = message.replace(endpoint, "[endpoint]") if endpoint else message
    message = " ".join(message.split())[:500]
    suffix = f" ({code})" if code else ""
    return f"Azure OpenAI request failed with HTTP {error.code}{suffix}: {message}" if message else (
        f"Azure OpenAI request failed with HTTP {error.code}{suffix}."
    )


def _post_json(url: str, api_key: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, default=str).encode("utf-8"),
        headers={"Content-Type": "application/json", "api-key": api_key},
        method="POST",
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise AzureOpenAIError("Azure OpenAI returned an invalid response envelope.")
            return payload
        except urllib.error.HTTPError as error:
            retryable = error.code in {408, 429, 500, 502, 503, 504}
            if retryable and attempt < 3:
                retry_after = error.headers.get("Retry-After") if error.headers else None
                try:
                    delay = float(retry_after) if retry_after else 2**attempt
                except (TypeError, ValueError):
                    delay = 2**attempt
                time.sleep(min(max(delay, 1), 15))
                continue
            raise AzureOpenAIError(
                _provider_error_message(error, api_key, settings.AZURE_OPENAI_ENDPOINT or ""),
                status_code=error.code,
            ) from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            if attempt == 3:
                raise AzureOpenAIError("Azure OpenAI could not return a valid response.") from error
            time.sleep(2**attempt)
    raise AzureOpenAIError("Azure OpenAI could not return a valid response.")


def generate_json(
    instructions: str,
    user_input: str,
    response_format: dict[str, Any],
) -> dict[str, Any]:
    endpoint = (settings.AZURE_OPENAI_ENDPOINT or "").rstrip("/")
    api_key = settings.AZURE_OPENAI_API_KEY
    if not endpoint or not api_key:
        raise AzureOpenAIError("Azure OpenAI is not configured.", status_code=503)

    deployment = urllib.parse.quote(settings.AZURE_OPENAI_DEPLOYMENT, safe="")
    query = urllib.parse.urlencode({"api-version": settings.AZURE_OPENAI_API_VERSION})
    responses_url = f"{endpoint}/openai/deployments/{deployment}/responses?{query}"
    responses_body = {
        "model": settings.AZURE_OPENAI_DEPLOYMENT,
        "instructions": instructions,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": user_input}]}],
        "text": {"format": response_format},
        "max_output_tokens": 8000,
    }

    try:
        return _extract_response(_post_json(responses_url, api_key, responses_body))
    except AzureOpenAIError as error:
        if error.status_code not in {400, 404}:
            raise

    chat_url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?{query}"
    chat_body = {
        "model": settings.AZURE_OPENAI_DEPLOYMENT,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_input},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": response_format["name"],
                "strict": response_format.get("strict", True),
                "schema": response_format["schema"],
            },
        },
    }
    try:
        return _extract_response(_post_json(chat_url, api_key, chat_body))
    except AzureOpenAIError as error:
        if error.status_code != 400:
            raise
        chat_body["response_format"] = {"type": "json_object"}
        return _extract_response(_post_json(chat_url, api_key, chat_body))