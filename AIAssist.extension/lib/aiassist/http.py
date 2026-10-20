# -*- coding: utf-8 -*-
"""JSON POST over .NET HttpWebRequest (works in IronPython 2.7 and CPython)."""
import clr  # noqa: F401
from System.IO import StreamReader
from System.Net import (HttpWebRequest, ServicePointManager,
                        SecurityProtocolType, WebException)
from System.Text import Encoding

from aiassist import jsonutil


class LLMError(Exception):
    pass


def _read_stream(stream):
    reader = StreamReader(stream, Encoding.UTF8)
    try:
        return reader.ReadToEnd()
    finally:
        reader.Close()


def post_json(url, headers, payload, timeout_ms=180000):
    ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12
    # Pure-ASCII body (non-ASCII is \\u-escaped), so no encoding surprises.
    body = Encoding.ASCII.GetBytes(jsonutil.dumps(payload))

    req = HttpWebRequest.Create(url)
    req.Method = "POST"
    req.ContentType = "application/json"
    req.Timeout = timeout_ms
    for k, v in headers.items():
        req.Headers.Add(k, v)
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
        raise LLMError(u"Ошибка API: %s %s" % (jsonutil.text(ex.Message),
                                               jsonutil.text(detail)))

    return jsonutil.loads(text)
