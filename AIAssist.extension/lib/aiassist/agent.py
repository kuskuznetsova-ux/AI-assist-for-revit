# -*- coding: utf-8 -*-
"""Agent loop: user text -> LLM -> tool calls on Revit -> final answer."""
import json

from aiassist import config, llm, tools

MAX_STEPS = 12
MAX_RESULT_CHARS = 8000

SYSTEM_PROMPT = u"""Ты — ассистент внутри Autodesk Revit. Помогаешь BIM-специалисту работать с моделью.
Отвечай по-русски, коротко и по делу.
- Данные о модели получай только через инструменты, ничего не выдумывай.
- Перед массовыми изменениями сначала найди элементы и сообщи, сколько их.
- Инструменты записи сами запрашивают подтверждение у пользователя; если пользователь отказал — не повторяй.
- Числа передаются в единицах проекта (обычно мм).
- В конце сообщи, что сделано и что не удалось."""


class Agent(object):
    def __init__(self, bridge):
        self.bridge = bridge
        self.messages = []

    def _system(self):
        recipes = config.load_recipes().strip()
        if recipes:
            return SYSTEM_PROMPT + u"\n\nПравила пользователя:\n" + recipes
        return SYSTEM_PROMPT

    def _run_tool(self, name, args):
        entry = tools.TOOLS.get(name)
        if entry is None:
            return u"Неизвестный инструмент: %s" % name, True
        try:
            result = self.bridge.run(lambda uiapp: entry["fn"](uiapp, **args))
            text = json.dumps(result, ensure_ascii=False)
            return text[:MAX_RESULT_CHARS], False
        except Exception as ex:
            return u"Ошибка: %s" % ex, True

    def ask(self, text, on_status):
        """Blocking; call from a background thread. Returns the final answer."""
        cfg = config.load()
        self.messages.append({"role": "user", "content": text})

        for _ in range(MAX_STEPS):
            on_status(u"Думаю…")
            resp = llm.create_message(cfg, self._system(), self.messages,
                                      tools.specs())
            self.messages.append({"role": "assistant", "content": resp["content"]})

            if resp.get("stop_reason") != "tool_use":
                return u"\n".join(b["text"] for b in resp["content"]
                                  if b["type"] == "text")

            results = []
            for block in resp["content"]:
                if block["type"] != "tool_use":
                    continue
                on_status(u"Выполняю: %s" % block["name"])
                out, is_error = self._run_tool(block["name"], block["input"])
                results.append({"type": "tool_result",
                                "tool_use_id": block["id"],
                                "content": out,
                                "is_error": is_error})
            self.messages.append({"role": "user", "content": results})

        return u"Остановился: слишком много шагов подряд."
