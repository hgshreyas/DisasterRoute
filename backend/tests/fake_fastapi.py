"""A tiny stand-in for FastAPI so app/main.py can be exercised where FastAPI is not installed.

It supports only what main.py uses: route decorators, Query(...) defaults, HTTPException,
pydantic request bodies, StaticFiles, RedirectResponse and CORS middleware.  It does NOT
validate Query constraints (ge/le/pattern); the real FastAPI does that.

    install()  puts the fake modules into sys.modules
    build_asgi(app) returns a Starlette app that serves the registered routes
"""
import inspect
import json
import sys
import types
import typing

from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

REQUIRED = object()


class HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        self.status_code, self.detail = status_code, detail


def Query(default=REQUIRED, *a, **kw):          # noqa: N802
    return default


class _FastAPI:
    def __init__(self, *a, **kw):
        self.routes, self.mounts = [], []

    def add_middleware(self, *a, **kw):
        pass

    def mount(self, path, app, name=None):
        self.mounts.append((path, app, name))

    def _reg(self, method):
        def deco(path, **kw):
            def wrap(fn):
                self.routes.append((method, path, fn, kw))
                return fn
            return wrap
        return deco

    def __getattr__(self, name):
        if name in ("get", "post", "delete"):
            return self._reg(name.upper())
        raise AttributeError(name)


def _coerce(raw, ann):
    ann = typing.get_origin(ann) is typing.Union and [a for a in typing.get_args(ann)
                                                      if a is not type(None)][0] or ann
    if typing.get_origin(ann) is types.UnionType:
        ann = [a for a in typing.get_args(ann) if a is not type(None)][0]
    if ann is bool:
        return raw.lower() in ("1", "true", "yes")
    if ann in (int, float):
        return ann(raw)
    return raw


def install():
    fa = types.ModuleType("fastapi")
    fa.FastAPI, fa.Query, fa.HTTPException = _FastAPI, Query, HTTPException
    mw = types.ModuleType("fastapi.middleware"); cors = types.ModuleType("fastapi.middleware.cors")
    cors.CORSMiddleware = object
    resp = types.ModuleType("fastapi.responses"); resp.RedirectResponse = RedirectResponse
    st = types.ModuleType("fastapi.staticfiles"); st.StaticFiles = StaticFiles
    for name, mod in [("fastapi", fa), ("fastapi.middleware", mw), ("fastapi.middleware.cors", cors),
                      ("fastapi.responses", resp), ("fastapi.staticfiles", st)]:
        sys.modules[name] = mod


def _handler(fn, status):
    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn)

    async def endpoint(request):
        kwargs = {}
        for name, p in sig.parameters.items():
            ann = hints.get(name, str)
            if name in request.path_params:
                kwargs[name] = request.path_params[name]
            elif isinstance(ann, type) and hasattr(ann, "model_validate"):      # pydantic body
                kwargs[name] = ann.model_validate(json.loads((await request.body()) or b"{}"))
            elif name in request.query_params:
                try:
                    kwargs[name] = _coerce(request.query_params[name], ann)
                except ValueError:
                    return JSONResponse({"detail": f"bad value for {name}"}, status_code=422)
            elif p.default is REQUIRED or p.default is inspect.Parameter.empty:
                return JSONResponse({"detail": f"missing query parameter {name}"}, status_code=422)
            else:
                kwargs[name] = p.default
        try:
            return JSONResponse(fn(**kwargs), status_code=status)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return endpoint


def build_asgi(app):
    routes = []
    for method, path, fn, kw in app.routes:
        include = kw.get("include_in_schema", True)
        if fn.__name__ == "root":
            async def root(request):
                return RedirectResponse("/app/")
            routes.append(Route(path, root, methods=[method]))
        else:
            routes.append(Route(path, _handler(fn, kw.get("status_code", 200)), methods=[method]))
    for path, sub, name in app.mounts:
        routes.append(Mount(path, app=sub, name=name))
    return Starlette(routes=routes)
