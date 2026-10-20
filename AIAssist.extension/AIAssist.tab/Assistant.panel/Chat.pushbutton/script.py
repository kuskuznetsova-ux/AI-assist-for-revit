# -*- coding: utf-8 -*-
"""Opens the AI assistant chat window (modeless, next to Revit)."""
import threading

import clr
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
clr.AddReference("WindowsBase")
from System import Action
from System.Windows.Input import Key
from pyrevit import forms

from aiassist.agent import Agent
from aiassist.bridge import Bridge


class ChatWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, "ui.xaml")
        self.agent = Agent(Bridge())
        self.busy = False
        self._append(u"Ассистент: чем помочь? Например: «выбери все стены толще 200».")

    # -- UI helpers (safe from any thread)
    def _ui(self, fn):
        self.Dispatcher.Invoke(Action(fn))

    def _append(self, line):
        def do():
            self.history.AppendText(line + u"\n\n")
            self.history.ScrollToEnd()
        self._ui(do)

    def _set_status(self, text):
        self._ui(lambda: setattr(self.status, "Text", text))

    def _set_busy(self, busy):
        def do():
            self.busy = busy
            self.send.IsEnabled = not busy
        self._ui(do)

    # -- events
    def input_keydown(self, sender, args):
        if args.Key == Key.Enter:
            args.Handled = True
            self.send_click(sender, args)

    def send_click(self, sender, args):
        text = self.input.Text.strip()
        if not text or self.busy:
            return
        self.input.Text = u""
        self._append(u"Вы: " + text)
        self._set_busy(True)
        t = threading.Thread(target=self._worker, args=(text,))
        t.daemon = True
        t.start()

    def _worker(self, text):
        try:
            answer = self.agent.ask(text, self._set_status)
            self._append(u"Ассистент: " + answer)
        except Exception as ex:
            self._append(u"Ошибка: %s" % ex)
        finally:
            self._set_status(u"")
            self._set_busy(False)


ChatWindow().show()
