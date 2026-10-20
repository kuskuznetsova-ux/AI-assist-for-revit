# -*- coding: utf-8 -*-
"""JSON POST over .NET HttpWebRequest (works in IronPython 2.7 and CPython)."""
import clr  # noqa: F401
from System.IO import StreamReader
from System.Net import (CredentialCache, HttpWebRequest, ServicePointManager,
                        SecurityProtocolType, WebException, WebRequest)
from System.Text import Encoding

from aiassist import config, jsonutil


class LLMError(Exception):
    def __init__(self, message, status=None, detail=u""):
        Exception.__init__(self, message)
        self.status = status
        self.detail = detail


def _read_stream(stream):
    reader = StreamReader(stream, Encoding.UTF8)
    try:
        return reader.ReadToEnd()
    finally:
        reader.Close()


def post_json(url, headers, payload, timeout_ms=60000):
    ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12
    # Pure-ASCII body (non-ASCII is \\u-escaped), so no encoding surprises.
    body = Encoding.ASCII.GetBytes(jsonutil.dumps(payload))

    req = HttpWebRequest.Create(url)
    req.Method = "POST"
    req.ContentType = "application/json"
    req.Timeout = timeout_ms
    req.ReadWriteTimeout = timeout_ms
    req.KeepAlive = False
    # Use the Windows (IE) proxy settings with the user's credentials, if any.
    proxy = WebRequest.GetSystemWebProxy()
    proxy.Credentials = CredentialCache.DefaultCredentials
    req.Proxy = proxy
    for k, v in headers.items():
        req.Headers.Add(k, v)
    req.ContentLength = body.Length

    config.log_error(u"HTTP -> " + url.split("?")[0])
    out = req.GetRequestStream()
    try:
        out.Write(body, 0, body.Length)
    finally:
        out.Close()

    try:
        resp = req.GetResponse()
        try:
            text = _read_stream(resp.GetResponseStream())
            config.log_error(u"HTTP <- %s, %d bytes" % (int(resp.StatusCode), len(text)))
        finally:
            resp.Close()
    except WebException as ex:
        detail, status = u"", None
        if ex.Response is not None:
            status = int(ex.Response.StatusCode)
            detail = _read_stream(ex.Response.GetResponseStream())
        config.log_error(u"HTTP error: " + jsonutil.text(ex.Message))
        raise LLMError(u"Ошибка API: %s %s" % (jsonutil.text(ex.Message),
                                               jsonutil.text(detail)),
                       status, detail)

    return jsonutil.loads(text)
