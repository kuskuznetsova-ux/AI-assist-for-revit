# -*- coding: utf-8 -*-
"""LLM providers behind one interface; each keeps its own chat history.

    chat = llm.make_chat(cfg)
    chat.user(text)
    text, calls = chat.step(system, tool_specs)   # calls: [{id, name, args}]
    chat.tool_results([{id, name, content, is_error}])
"""
import re
import time

from aiassist import http
from aiassist.http import LLMError  # noqa: F401

MAX_RETRIES = 3
MAX_WAIT = 90  # seconds; longer waits (daily quota) are reported, not awaited


def _retry_delay(detail):
    m = (re.search(r'retryDelay"\s*:\s*"([0-9.]+)s"', detail or u"") or
         re.search(r'retry in ([0-9.]+)s', detail or u""))
    return float(m.group(1)) if m else 20.0


def _post(url, headers, payload, on_status):
    """post_json that waits out per-minute rate limits (HTTP 429)."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return http.post_json(url, headers, payload)
        except LLMError as ex:
            wait = _retry_delay(getattr(ex, "detail", u"")) + 1
            if (getattr(ex, "status", None) != 429 or attempt == MAX_RETRIES
                    or wait > MAX_WAIT):
                raise
            if on_status:
                on_status(u"Лимит запросов, жду %d с…" % wait)
            time.sleep(wait)


def make_chat(cfg):
    if not cfg["api_key"]:
        raise LLMError(u"Не задан API-ключ. Укажите api_key в "
                       u"~/.aiassist/config.json (или переменную окружения).")
    if cfg["provider"] == "anthropic":
        return AnthropicChat(cfg)
    if cfg["provider"] == "gemini":
        return GeminiChat(cfg)
    raise LLMError(u"Неизвестный provider: %s" % cfg["provider"])


class AnthropicChat(object):
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, cfg):
        self.cfg = cfg
        self.messages = []

    def user(self, text):
        self.messages.append({"role": "user", "content": text})

    def step(self, system, tool_specs, on_status=None):
        resp = _post(
            self.URL,
            {"x-api-key": self.cfg["api_key"], "anthropic-version": "2023-06-01"},
            {"model": self.cfg["model"], "max_tokens": self.cfg["max_tokens"],
             "system": system, "messages": self.messages, "tools": tool_specs},
            on_status)
        self.messages.append({"role": "assistant", "content": resp["content"]})
        text = u"\n".join(b["text"] for b in resp["content"] if b["type"] == "text")
        calls = [{"id": b["id"], "name": b["name"], "args": b["input"]}
                 for b in resp["content"] if b["type"] == "tool_use"]
        return text, calls

    def tool_results(self, results):
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r["id"],
             "content": r["content"], "is_error": r["is_error"]}
            for r in results]})


class GeminiChat(object):
    URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"

    def __init__(self, cfg):
        self.cfg = cfg
        self.contents = []

    def user(self, text):
        self.contents.append({"role": "user", "parts": [{"text": text}]})

    @staticmethod
    def _declarations(tool_specs):
        out = []
        for s in tool_specs:
            decl = {"name": s["name"], "description": s["description"]}
            if s["input_schema"]["properties"]:  # Gemini rejects empty objects
                decl["parameters"] = s["input_schema"]
            out.append(decl)
        return [{"function_declarations": out}]

    def step(self, system, tool_specs, on_status=None):
        resp = _post(
            self.URL % self.cfg["model"],
            {"x-goog-api-key": self.cfg["api_key"]},
            {"system_instruction": {"parts": [{"text": system}]},
             "contents": self.contents,
             "tools": self._declarations(tool_specs)},
            on_status)
        cands = resp.get("candidates") or []
        if not cands:
            raise LLMError(u"Gemini не вернул ответ: %s" % resp.get("promptFeedback", resp))
        content = cands[0].get("content") or {}
        parts = content.get("parts") or []
        if parts:
            # Echo parts back verbatim: newer models need their thought signatures.
            self.contents.append({"role": "model", "parts": parts})
        text = u"\n".join(p["text"] for p in parts
                          if "text" in p and not p.get("thought"))
        calls = []
        for i, p in enumerate(parts):
            fc = p.get("functionCall")
            if fc:
                calls.append({"id": str(i), "name": fc["name"],
                              "args": fc.get("args") or {}})
        if not text and not calls:
            raise LLMError(u"Пустой ответ Gemini (finishReason=%s)"
                           % cands[0].get("finishReason"))
        return text, calls

    def tool_results(self, results):
        self.contents.append({"role": "user", "parts": [
            {"functionResponse": {
                "name": r["name"],
                "response": {"error": r["content"]} if r["is_error"]
                else {"result": r["content"]}}}
            for r in results]})
