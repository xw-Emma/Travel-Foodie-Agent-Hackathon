"""
Fuel iX client - Python standard library only (urllib), no third-party SDK.

WHY: Fuel iX exposes an OpenAI-compatible endpoint, and plain HTTPS removes
the single biggest install risk on a locked-down laptop. If `pip install`
works for you, the `openai` SDK pointed at FUELIX_BASE_URL is equivalent -
but nothing in this kit requires it.

All LLM traffic MUST go through Fuel iX (api.fuelix.ai). No ChatGPT, no
direct vendor endpoints - a non-approved endpoint invalidates the demo.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import config


class FuelixError(RuntimeError):
    pass


class FuelixClient:
    """Thin chat-completions client with retry/backoff and cost telemetry."""

    def __init__(self, api_key: str | None = None,
                 base_url: str | None = None,
                 timeout: int = 60, max_retries: int = 3):
        self.api_key = api_key or config.FUELIX_API_KEY
        if not self.api_key:
            raise FuelixError(
                "FUELIX_API_KEY not set. Put it in the gitignored .env at the kit "
                "root (see .env.example). Never hardcode it in code or slides.")
        self.base_url = (base_url or config.FUELIX_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.telemetry = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0}

    def chat(self, model: str = "", system: str = "", user: str = "",
             tools: list | None = None, temperature: float = 0.2,
             max_tokens: int = 1200, messages: list | None = None) -> dict:
        """One chat-completions call. Returns the assistant `message` dict."""
        model = model or config.DEFAULT_MODEL
        if messages is None:
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": user}]
        payload: dict = {"model": model, "messages": messages,
                         "temperature": temperature, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools

        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"})

        last_err = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self._record(data)
                return data["choices"][0]["message"]
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}"
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(1.5 * (2 ** attempt))
                    continue
                raise FuelixError(last_err) from e
            except urllib.error.URLError as e:
                last_err = f"URL error: {e.reason}"
                time.sleep(1.5 * (2 ** attempt))
        raise FuelixError(
            f"Fuel iX call failed after {self.max_retries} retries. Last: {last_err}")

    def _record(self, data: dict) -> None:
        usage = data.get("usage") or {}
        self.telemetry["llm_calls"] += 1
        self.telemetry["input_tokens"] += usage.get("prompt_tokens", 0)
        self.telemetry["output_tokens"] += usage.get("completion_tokens", 0)


def run_tool_loop(client: FuelixClient, model: str, system: str, user: str,
                  tools: list, tool_impls: dict,
                  max_rounds: int | None = None) -> dict:
    """
    The core agent loop: model asks for tools -> we execute -> feed results
    back -> repeat until the model returns a final JSON answer.
    """
    max_rounds = max_rounds or config.TOOL_LOOP_MAX_ROUNDS
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    for _ in range(max_rounds):
        msg = client.chat(model=model, messages=messages, tools=tools)
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return parse_json_reply(msg.get("content", ""))
        messages.append(msg)
        for call in tool_calls:
            fn = call["function"]["name"]
            args = json.loads(call["function"]["arguments"] or "{}")
            impl = tool_impls.get(fn, lambda **k: {"error": f"unknown tool {fn}"})
            result = impl(**args)
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, default=str)})
    raise FuelixError(f"Agent loop exceeded {max_rounds} rounds for model {model}")


def parse_json_reply(text: str) -> dict:
    """Tolerant JSON extraction (handles ```json fences and stray prose)."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e > s:
            return json.loads(text[s:e + 1])
        raise
