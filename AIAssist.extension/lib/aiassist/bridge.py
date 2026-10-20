# -*- coding: utf-8 -*-
"""Run callables on Revit's main thread via ExternalEvent.

The chat/LLM loop lives in a background thread; every Revit API call must go
through Bridge.run(), which blocks the caller until Revit executes it.
"""
import threading

import clr  # noqa: F401
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent


class _Job(object):
    def __init__(self, func):
        self.func = func
        self.done = threading.Event()
        self.result = None
        self.error = None


class _Handler(IExternalEventHandler):
    def __init__(self):
        self.job = None

    def Execute(self, uiapp):
        job = self.job
        try:
            job.result = job.func(uiapp)
        except Exception as ex:
            job.error = ex
        finally:
            job.done.set()

    def GetName(self):
        return "AIAssist"


class Bridge(object):
    def __init__(self):
        # Must be created from a valid Revit API context (the button click).
        self._handler = _Handler()
        self._event = ExternalEvent.Create(self._handler)
        self._lock = threading.Lock()

    def run(self, func, timeout=600):
        with self._lock:
            job = _Job(func)
            self._handler.job = job
            self._event.Raise()
            job.done.wait(timeout)
            if not job.done.isSet():
                raise Exception(u"Revit не ответил за %s с" % timeout)
            if job.error is not None:
                raise job.error
            return job.result
