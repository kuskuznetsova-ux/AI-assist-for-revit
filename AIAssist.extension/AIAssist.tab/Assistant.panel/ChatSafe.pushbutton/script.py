# -*- coding: utf-8 -*-
"""Safe mode: no custom WPF window. Input via a standard pyRevit dialog, output
in the pyRevit output window. Runs synchronously inside the command, so Revit
API calls are made directly (no ExternalEvent)."""
from pyrevit import forms, script

from aiassist.agent import Agent


class DirectBridge(object):
    def run(self, func, timeout=0):
        return func(__revit__)


out = script.get_output()
agent = Agent(DirectBridge())
out.print_md(u"**AI-ассистент (безопасный режим).** Пустой запрос — выход.")

while True:
    text = forms.ask_for_string(prompt=u"Ваш запрос (пусто — выход):",
                                title=u"AI-ассистент")
    if not text or not text.strip():
        break
    out.print_md(u"**Вы:** " + text)
    try:
        out.print_md(u"**Ассистент:** " + agent.ask(text, lambda s: None))
    except Exception as ex:
        out.print_md(u"**Ошибка:** %s" % ex)
