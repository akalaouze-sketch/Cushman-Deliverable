"""Agent execution tracer — a small, dependency-free 'mini-langgraph' for this app.

It records the steps an agent run takes to turn an INPUT into an OUTPUT — reasoning
notes, function calls, tool calls and LLM calls — as a tree of *spans*, and persists
them in a recoverable format so any past run can be replayed/inspected later.

Model
-----
- A **Run** is one top-level invocation (an /api/report request, a PDF parse, a /chat
  turn). It has an id, a name, an input, an output and a list of spans.
- A **Span** (a node in the graph) is one step inside a run: name, kind
  (llm | tool | agent | function | io | reasoning), input, output, status, timing,
  parent link and order. Edges are implicit — `parent_id` gives the tree, `order`
  gives the sequence — so the full graph is recoverable from the flat span list.

Nesting is automatic: spans use `contextvars`, so a span opened inside another span
is recorded as its child without anyone threading a handle through. Calls made
outside any run still get captured under a lazily-created "ambient" run flushed at
process exit, so even ad-hoc scripts are traced.

Persistence (under data/traces/, recoverable)
---------------------------------------------
- `<run_id>.jsonl`  — one line per span as it closes (a crash-proof event stream).
- `<run_id>.json`   — the full nested tree + run meta, written when the run ends.
- `index.jsonl`     — one line per run (id, name, ts, status, duration, span_count).

Usage
-----
    from agents import trace

    with trace.run("parse_report", input={"pdf": path}) as r:
        with trace.span("extract_text", kind="io") as s:
            text = extract(path)
            s.output(len(text))
        r.output({"metrics": n})

    @trace.traced(kind="agent")            # decorate any agent entry-point
    def write_takeaways(parsed, source): ...

Disable with env AGENT_TRACE=0.
"""
from __future__ import annotations

import atexit
import contextvars
import functools
import json
import os
import threading
import time
import traceback
import uuid
from pathlib import Path

# ---------------------------------------------------------------- configuration
_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = _ROOT / "data" / "traces"

ENABLED = os.environ.get("AGENT_TRACE", "1") not in ("0", "false", "False", "")
TRACE_DIR = Path(os.environ.get("AGENT_TRACE_DIR", str(_DEFAULT_DIR)))
_MAX_STR = int(os.environ.get("AGENT_TRACE_MAXSTR", "800"))   # per-string cap in previews
_MAX_ITEMS = 12                                               # per-collection item cap


def configure(enabled: bool | None = None, trace_dir: str | os.PathLike | None = None) -> None:
    """Toggle tracing or point it at a different directory at runtime."""
    global ENABLED, TRACE_DIR
    if enabled is not None:
        ENABLED = bool(enabled)
    if trace_dir is not None:
        TRACE_DIR = Path(trace_dir)


# ----------------------------------------------------------------- serialization
def _safe(obj, depth: int = 0):
    """Make any value JSON-serializable AND small: truncate long strings, cap
    collections, summarize unknown objects. Never raises — tracing must not break
    the thing it traces."""
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return obj if len(obj) <= _MAX_STR else obj[:_MAX_STR] + f"…(+{len(obj) - _MAX_STR} chars)"
    if depth >= 6:
        return f"<{type(obj).__name__}>"
    if isinstance(obj, dict):
        out, extra = {}, 0
        for i, (k, v) in enumerate(obj.items()):
            if i >= _MAX_ITEMS:
                extra = len(obj) - _MAX_ITEMS
                break
            out[str(k)] = _safe(v, depth + 1)
        if extra:
            out["…"] = f"+{extra} more keys"
        return out
    if isinstance(obj, (list, tuple, set)):
        seq = list(obj)
        out = [_safe(v, depth + 1) for v in seq[:_MAX_ITEMS]]
        if len(seq) > _MAX_ITEMS:
            out.append(f"…(+{len(seq) - _MAX_ITEMS} more)")
        return out
    if isinstance(obj, (bytes, bytearray)):
        return f"<{len(obj)} bytes>"
    for attr in ("to_dict", "model_dump", "__dict__"):
        try:
            v = getattr(obj, attr)
            data = v() if callable(v) else v
            if isinstance(data, dict):
                return _safe(data, depth + 1)
        except Exception:
            pass
    return _safe(str(obj), depth + 1)


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


# ---------------------------------------------------------------------- objects
class Span:
    """One node in the run graph."""

    __slots__ = ("id", "run", "parent_id", "name", "kind", "status", "_input",
                 "_output", "error", "started", "ended", "meta", "order")

    def __init__(self, run: "Run", parent_id: str | None, name: str, kind: str,
                 input=None, meta: dict | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.run = run
        self.parent_id = parent_id
        self.name = name
        self.kind = kind
        self.status = "running"
        self._input = _safe(input) if input is not None else None
        self._output = None
        self.error = None
        self.started = _now()
        self.ended = None
        self.meta = dict(meta or {})
        self.order = run._next_order()

    # fluent setters (return self so they chain inside a `with`)
    def input(self, value) -> "Span":
        self._input = _safe(value)
        return self

    def output(self, value) -> "Span":
        self._output = _safe(value)
        return self

    def set(self, **meta) -> "Span":
        for k, v in meta.items():
            self.meta[k] = _safe(v)
        return self

    def finish(self, status: str | None = None) -> None:
        if self.ended is not None:
            return
        self.ended = _now()
        if status:
            self.status = status
        elif self.status == "running":
            self.status = "ok"
        self.run._on_span_close(self)

    @property
    def duration_ms(self) -> int:
        end = self.ended if self.ended is not None else _now()
        return int((end - self.started) * 1000)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "parent_id": self.parent_id, "name": self.name,
            "kind": self.kind, "status": self.status, "order": self.order,
            "input": self._input, "output": self._output, "error": self.error,
            "started": _iso(self.started), "duration_ms": self.duration_ms,
            "meta": self.meta,
        }


class Run:
    """One traced top-level invocation."""

    def __init__(self, name: str, input=None, meta: dict | None = None, ambient: bool = False):
        self.id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.name = name
        self.input = _safe(input) if input is not None else None
        self.output = None
        self.status = "running"
        self.meta = dict(meta or {})
        self.started = _now()
        self.ended = None
        self.ambient = ambient
        self.spans: list[Span] = []
        self._order = 0
        self._lock = threading.Lock()
        self._jsonl = TRACE_DIR / f"{self.id}.jsonl"
        self._wrote_header = False

    def _next_order(self) -> int:
        with self._lock:
            self._order += 1
            return self._order

    def _on_span_close(self, span: Span) -> None:
        # stream each closed span to the .jsonl event log (crash-proof)
        if not ENABLED:
            return
        try:
            TRACE_DIR.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with self._jsonl.open("a") as fh:
                    if not self._wrote_header:
                        fh.write(json.dumps({"_run": self.id, "name": self.name,
                                             "started": _iso(self.started)}) + "\n")
                        self._wrote_header = True
                    fh.write(json.dumps({"span": span.to_dict()}) + "\n")
        except Exception:
            pass

    def add_span(self, span: Span) -> None:
        self.spans.append(span)

    def set_output(self, value) -> None:
        self.output = _safe(value)

    def _tree(self) -> list[dict]:
        """Rebuild the nested span tree from the flat parent-linked list."""
        nodes = {s.id: {**s.to_dict(), "children": []} for s in self.spans}
        roots = []
        for s in self.spans:
            node = nodes[s.id]
            parent = nodes.get(s.parent_id)
            (parent["children"] if parent else roots).append(node)
        return roots

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "status": self.status,
            "input": self.input, "output": self.output, "meta": self.meta,
            "started": _iso(self.started),
            "duration_ms": int(((self.ended or _now()) - self.started) * 1000),
            "span_count": len(self.spans),
            "spans": [s.to_dict() for s in self.spans],   # flat (recoverable)
            "tree": self._tree(),                          # nested (convenient)
        }

    def finish(self, output=None, status: str | None = None) -> "Run":
        if self.ended is not None:
            return self
        self.ended = _now()
        if output is not None:
            self.set_output(output)
        self.status = status or ("ok" if self.status == "running" else self.status)
        self._save()
        return self

    def _save(self) -> None:
        if not ENABLED:
            return
        try:
            TRACE_DIR.mkdir(parents=True, exist_ok=True)
            (TRACE_DIR / f"{self.id}.json").write_text(json.dumps(self.to_dict(), indent=2))
            with (TRACE_DIR / "index.jsonl").open("a") as fh:
                fh.write(json.dumps({
                    "id": self.id, "name": self.name, "status": self.status,
                    "started": _iso(self.started), "duration_ms": self.to_dict()["duration_ms"],
                    "span_count": len(self.spans),
                }) + "\n")
        except Exception:
            pass


# --------------------------------------------------------------- context plumbing
_RUN: contextvars.ContextVar[Run | None] = contextvars.ContextVar("trace_run", default=None)
_SPAN: contextvars.ContextVar[Span | None] = contextvars.ContextVar("trace_span", default=None)

_ambient_lock = threading.Lock()
_ambient_run: Run | None = None


def current_run() -> Run | None:
    return _RUN.get() or _ambient_run


def current_span() -> Span | None:
    return _SPAN.get()


def _ambient() -> Run:
    """Lazily open one shared run for spans created outside an explicit `run()`,
    flushed at interpreter exit. Lets ad-hoc scripts get traced too."""
    global _ambient_run
    with _ambient_lock:
        if _ambient_run is None:
            _ambient_run = Run("ambient", meta={"pid": os.getpid()}, ambient=True)
            atexit.register(lambda: _ambient_run and _ambient_run.finish())
        return _ambient_run


class _RunCtx:
    def __init__(self, run: Run):
        self.run = run
        self._tok = None

    def __enter__(self) -> Run:
        self._tok = _RUN.set(self.run)
        return self.run

    def __exit__(self, exc_type, exc, tb):
        if exc:
            self.run.status = "error"
            self.run.meta["error"] = f"{exc_type.__name__}: {exc}"
        self.run.finish()
        if self._tok:
            _RUN.reset(self._tok)
        return False


def run(name: str, input=None, **meta) -> _RunCtx:
    """Open a top-level traced run as a context manager."""
    return _RunCtx(Run(name, input=input, meta=meta))


class _SpanCtx:
    def __init__(self, span: Span):
        self.span = span
        self._tok = None

    def __enter__(self) -> Span:
        self._tok = _SPAN.set(self.span)
        return self.span

    def __exit__(self, exc_type, exc, tb):
        if exc:
            self.span.error = f"{exc_type.__name__}: {exc}"
            self.span.meta.setdefault("traceback", _safe("".join(
                traceback.format_exception(exc_type, exc, tb))))
            self.span.finish("error")
        else:
            self.span.finish()
        if self._tok:
            _SPAN.reset(self._tok)
        return False


def span(name: str, kind: str = "function", input=None, **meta) -> _SpanCtx:
    """Open a child span under the current span/run. If tracing is disabled or no
    run is active, a span is still created under a lazily-opened ambient run."""
    host = current_run() or (_ambient() if ENABLED else None)
    if host is None:
        return _SpanCtx(_Null())  # type: ignore[arg-type]
    parent = current_span()
    sp = Span(host, parent.id if parent else None, name, kind, input=input, meta=meta)
    host.add_span(sp)
    return _SpanCtx(sp)


def record(name: str, kind: str = "event", input=None, output=None, **meta) -> None:
    """Record a zero-duration point event (e.g. a decision/reasoning note)."""
    with span(name, kind=kind, input=input, **meta) as s:
        if output is not None:
            s.output(output)


def reasoning(text: str, **meta) -> None:
    """Record a reasoning/decision note in the trace."""
    record("reasoning", kind="reasoning", output=text, **meta)


def traced(fn=None, *, kind: str = "function", name: str | None = None):
    """Decorator: trace a function call, recording its args and return value."""
    def deco(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            label = name or getattr(func, "__qualname__", func.__name__)
            with span(label, kind=kind, input={"args": args, "kwargs": kwargs}) as s:
                out = func(*args, **kwargs)
                s.output(out)
                return out
        return wrapper
    return deco(fn) if callable(fn) else deco


# ---- a no-op span used only when tracing is fully disabled and no run exists ----
class _Null:
    id = None
    def _next_order(self):  # noqa: E301
        return 0
    def input(self, *a, **k):  # noqa: E301
        return self
    def output(self, *a, **k):  # noqa: E301
        return self
    def set(self, *a, **k):  # noqa: E301
        return self
    def finish(self, *a, **k):  # noqa: E301
        return None


# ------------------------------------------------------------------ read helpers
def load_index(limit: int = 50) -> list[dict]:
    """Most recent runs first (from index.jsonl)."""
    p = TRACE_DIR / "index.jsonl"
    if not p.exists():
        return []
    rows = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
    return list(reversed(rows))[:limit]


def load_run(run_id: str) -> dict | None:
    """Full saved tree for a run id, or 'latest'."""
    if run_id in ("latest", "last"):
        idx = load_index(1)
        if not idx:
            return None
        run_id = idx[0]["id"]
    p = TRACE_DIR / f"{run_id}.json"
    return json.loads(p.read_text()) if p.exists() else None
