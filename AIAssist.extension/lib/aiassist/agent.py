# -*- coding: utf-8 -*-
"""Agent loop: user text -> LLM -> tool calls on Revit -> final answer."""
from aiassist import config, jsonutil, llm, tools

MAX_STEPS = 12
MAX_RESULT_CHARS = 8000

SYSTEM_PROMPT = u"""Ты — ассистент внутри Autodesk Revit. Помогаешь BIM-специалисту работать с моделью.
Отвечай по-русски, коротко и по делу.
- Данные о модели получай только через инструменты, ничего не выдумывай.
- Перед массовыми изменениями сначала найди элементы и сообщи, сколько их.
- Инструменты записи сами запрашивают подтверждение у пользователя; если пользователь отказал — не повторяй.
- Если не знаешь точное имя параметра, не угадывай: используй список available_parameters из ошибки или get_parameters.
- Старайся решать задачу минимумом вызовов инструментов.
- Числа передаются в единицах проекта (обычно мм).
- В конце сообщи, что сделано и что не удалось."""


class Agent(object):
    def __init__(self, bridge):
        self.bridge = bridge
        self.chat = None
        self._chat_cfg = None

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
            text = jsonutil.dumps(result, ascii_only=False)
            return text[:MAX_RESULT_CHARS], False
        except Exception as ex:
            return u"Ошибка: " + jsonutil.err_text(ex), True

    def ask(self, text, on_status):
        """Blocking; call from a background thread. Returns the final answer."""
        cfg = config.load()
        if self.chat is None or self._chat_cfg != cfg:
            self.chat, self._chat_cfg = llm.make_chat(cfg), cfg
        self.chat.user(text)

        for _ in range(MAX_STEPS):
            on_status(u"Думаю…")
            answer, calls = self.chat.step(self._system(), tools.specs(),
                                           on_status)
            if not calls:
                return answer

            results = []
            for call in calls:
                on_status(u"Выполняю: %s" % call["name"])
                out, is_error = self._run_tool(call["name"], call["args"])
                results.append({"id": call["id"], "name": call["name"],
                                "content": out, "is_error": is_error})
            self.chat.tool_results(results)

        return u"Остановился: слишком много шагов подряд."
