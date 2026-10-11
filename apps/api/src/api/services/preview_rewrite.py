"""Make an app served at ``/`` work under a path prefix.

The preview lives at ``/v1/preview/<token>/``. Pages that use root-relative URLs
(``/assets/app.js``) would otherwise ask the platform's own origin for them. HTML attributes,
CSS ``url()`` and the browser's ``fetch``/``XMLHttpRequest`` are rewritten; JavaScript module
specifiers are not (that cannot be done safely with text substitution), so an app that imports
root-relative modules should be started with a relative or prefixed base path. Documented in
the user guide.
"""

from __future__ import annotations

import html
import re

_ATTRIBUTE = re.compile(
    r"""(\b(?:src|href|action|poster|data-src|formaction)\s*=\s*)(["'])/(?!/)""", re.I
)
_CSS_URL = re.compile(r"""(url\(\s*)(["']?)/(?!/)""", re.I)

_SHIM = """<script>(function(){var P=%s;function f(u){return typeof u==='string'&&u.charAt(0)==='/'&&u.charAt(1)!=='/'?P+u.slice(1):u}
var F=window.fetch;if(F)window.fetch=function(i,o){return F.call(this,i&&i.url?i:f(i),o)};
var O=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u){arguments[1]=f(u);return O.apply(this,arguments)};
if(window.EventSource){var E=window.EventSource;window.EventSource=function(u,c){return new E(f(u),c)};window.EventSource.prototype=E.prototype}
})();</script>"""


def rewrite_html(document: str, prefix: str) -> str:
    """``prefix`` ends with ``/``. Adds a base, a request shim, and prefixes root-relative URLs."""
    rewritten = _ATTRIBUTE.sub(lambda m: f"{m.group(1)}{m.group(2)}{prefix}", document)
    injection = f'<base href="{html.escape(prefix, quote=True)}">' + _SHIM % _js_string(prefix)
    head = re.search(r"<head[^>]*>", rewritten, re.I)
    if head:
        return rewritten[: head.end()] + injection + rewritten[head.end() :]
    return injection + rewritten


def rewrite_css(css: str, prefix: str) -> str:
    return _CSS_URL.sub(lambda m: f"{m.group(1)}{m.group(2)}{prefix}", css)


def rewrite_location(location: str, prefix: str, port: int) -> str:
    """Keep a redirect inside the preview; leave redirects to other sites alone."""
    loopback = (f"http://127.0.0.1:{port}", f"http://localhost:{port}")
    for origin in loopback:
        if location.startswith(origin):
            location = location[len(origin) :] or "/"
    if location.startswith("/") and not location.startswith("//"):
        return prefix + location[1:]
    return location


def _js_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("<", "\\u003c") + '"'
