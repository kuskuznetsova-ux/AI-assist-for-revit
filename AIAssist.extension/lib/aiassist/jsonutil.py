# -*- coding: utf-8 -*-
"""Unicode-safe JSON and text helpers for IronPython 2.7.

IronPython's `str` is a .NET string, so the stdlib json module (which calls
str.decode / ascii-encodes) fails on Cyrillic. These helpers work on code
points only and never go through an implicit ASCII codec.
"""
import json
import numbers

try:  # IronPython 2.7
    _unichr, _unicode, _STR = unichr, unicode, basestring  # noqa: F821
except NameError:  # CPython 3 (tests)
    _unichr, _unicode, _STR = chr, str, str


def text(x):
    """Any value -> unicode, without touching the ASCII codec."""
    if isinstance(x, _unicode):
        return x
    if isinstance(x, _STR):
        return _unicode().join(_unichr(ord(c)) for c in x)
    return text(str(x))


def err_text(ex):
    for attr in ("Message", "message"):
        msg = getattr(ex, attr, None)
        if msg:
            return text(msg)
    if getattr(ex, "args", None):
        return text(ex.args[0])
    return text(repr(ex))


def _esc(n):
    """\\u escape for a code point; astral ones become surrogate pairs."""
    if n > 0xFFFF:
        n -= 0x10000
        return "\\u%04x\\u%04x" % (0xD800 + (n >> 10), 0xDC00 + (n & 0x3FF))
    return "\\u%04x" % n


def _quote(s, ascii_only):
    out = ['"']
    for ch in s:
        n = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif n < 32 or (ascii_only and n > 126):
            out.append(_esc(n))
        else:
            out.append(ch)
    out.append('"')
    return _unicode().join(out)


def dumps(o, ascii_only=True):
    if o is None:
        return u"null"
    if o is True:
        return u"true"
    if o is False:
        return u"false"
    if isinstance(o, _STR):
        return _quote(o, ascii_only)
    if isinstance(o, numbers.Number):
        return text(repr(o))
    if isinstance(o, dict):
        return u"{" + u",".join(
            _quote(k, ascii_only) + u":" + dumps(v, ascii_only)
            for k, v in o.items()) + u"}"
    if isinstance(o, (list, tuple)):
        return u"[" + u",".join(dumps(v, ascii_only) for v in o) + u"]"
    return _quote(text(o), ascii_only)


def loads(s):
    """Parse JSON text that may hold raw non-ASCII characters."""
    escaped = "".join(ch if ord(ch) < 128 else _esc(ord(ch)) for ch in s)
    return json.loads(escaped)
