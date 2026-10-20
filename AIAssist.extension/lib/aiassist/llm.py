# -*- coding: utf-8 -*-
"""Minimal Anthropic Messages API client on top of .NET HttpWebRequest.

Uses .NET instead of urllib/requests so it works in pyRevit's IronPython 2.7
as well as in CPython (pythonnet) engines.
"""
import json

import clr  # noqa: F401
from System.IO import StreamReader
from System.Net import (HttpWebRequest, ServicePointManager,
                        SecurityProtocolType, WebException)
from System.Text import Encoding

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class LLMError(Exception):
    pass


def _read_stream(stream):
    reader = StreamReader(stream, Encoding.UTF8)
    try:
        return reader.ReadToEnd()
    finally:
        reader.Close()


def create_message(cfg, system, messages, tools):
    if not cfg["api_key"]:
        raise LLMError(u"Не задан API-ключ. Укажите ANTHROPIC_API_KEY или "
                       u"api_key в ~/.aiassist/config.json")

    ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12

    payload = {
        "model": cfg["model"],
        "max_tokens": cfg["max_tokens"],
        "system": system,
        "messages": messages,
        "tools": tools,
    }
    # ensure_ascii keeps the body pure ASCII, so no encoding surprises.
    body = Encoding.ASCII.GetBytes(json.dumps(payload, ensure_ascii=True))

    req = HttpWebRequest.Create(API_URL)
    req.Method = "POST"
    req.ContentType = "application/json"
    req.Timeout = 180000
    req.Headers.Add("x-api-key", cfg["api_key"])
    req.Headers.Add("anthropic-version", API_VERSION)
    req.ContentLength = body.Length

    out = req.GetRequestStream()
    try:
        out.Write(body, 0, body.Length)
    finally:
        out.Close()

    try:
        resp = req.GetResponse()
        try:
            text = _read_stream(resp.GetResponseStream())
        finally:
            resp.Close()
    except WebException as ex:
        detail = u""
        if ex.Response is not None:
            detail = _read_stream(ex.Response.GetResponseStream())
        raise LLMError(u"Ошибка API: %s %s" % (ex.Message, detail))

    return json.loads(text)
