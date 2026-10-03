"""
agent/tools.py — Read-only investigation tools exposed to the LLM.

Each tool returns a JSON-serializable dict and never raises: failures come back
as {"error": "..."} so the agent loop keeps going.

Evidence sources:
    checkout service HTTP API (:8081)   health, metrics history, logs
    checkout-service git repo           commits, diffs, config, source files

Nothing here writes, restarts, or changes anything.
"""

import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from warrant.agent import config

MAX_LOG_LINES = 25
MAX_LINE_CHARS = 300
MAX_FILE_CHARS = 12000
MAX_DIFF_CHARS = 8000


def _http_get(path: str, **params) -> dict:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{config.CHECKOUT_URL}{path}" + (f"?{query}" if query else "")
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def _git(*args) -> str:
    result = subprocess.run(
        ["git", *args], cwd=config.SERVICE_REPO, capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout


def _safe(fn):
    def wrapper(**kwargs):
        try:
            return fn(**kwargs)
        except TypeError as exc:
            return {"error": f"bad arguments: {exc}"}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ---- tools -------------------------------------------------------------------

@_safe
def get_service_health():
    """Current health and traffic metrics of the checkout service."""
    return _http_get("/metrics")


@_safe
def get_metrics_history(since=None):
    """Metrics snapshots (every 5s), optionally since an ISO timestamp."""
    snapshots = _http_get("/metrics/history", since=since)["snapshots"]
    if len(snapshots) > 40:  # keep the oldest and newest, thin the middle
        step = len(snapshots) // 30 + 1
        snapshots = snapshots[:5] + snapshots[5:-5:step] + snapshots[-5:]
    return {"snapshots": snapshots, "count": len(snapshots)}


@_safe
def search_logs(query, since=None, limit=MAX_LOG_LINES):
    """Search application logs for a case-insensitive substring."""
    result = _http_get("/logs", query=query, since=since, limit=1000)
    entries = result["lines"]
    first_lines = [e.splitlines()[0] for e in entries]
    levels, loggers = {}, {}
    for line in first_lines:
        m = re.match(r"\S+\s+(\w+)\s+\[([^\]]+)\]", line)
        if m:
            levels[m.group(1)] = levels.get(m.group(1), 0) + 1
            loggers[m.group(2)] = loggers.get(m.group(2), 0) + 1
    error_types = {}
    for line in first_lines:
        for name in re.findall(r"\b([A-Z][A-Za-z]+(?:Error|Timeout|Exception|Declined))\b", line):
            error_types[name] = error_types.get(name, 0) + 1
    limit = max(1, min(int(limit), MAX_LOG_LINES))
    return {
        "query": query,
        "count": result["count"],
        "first_seen": first_lines[0][:24] if first_lines else None,
        "last_seen": first_lines[-1][:24] if first_lines else None,
        "levels": levels,
        "loggers": loggers,
        "error_types": error_types,
        "lines": [line[:MAX_LINE_CHARS] for line in first_lines[-limit:]],
    }


@_safe
def inspect_stack_trace():
    """Most recent stack trace in the application logs."""
    result = _http_get("/logs", query="Traceback", limit=1000)
    entries = result["lines"]
    if not entries:
        return {"count": 0, "trace": None}
    latest = entries[-1]
    last_line = latest.strip().splitlines()[-1]
    return {
        "count": result["count"],
        "exception": last_line,
        "trace": latest[:MAX_FILE_CHARS],
    }


@_safe
def get_recent_commits(limit=10):
    """Recent commits in the checkout-service repository."""
    limit = max(1, min(int(limit), 50))
    out = _git("log", f"-n{limit}", "--name-only", "--format=%x1e%H%x1f%an%x1f%aI%x1f%s")
    commits = []
    for record in out.split("\x1e")[1:]:
        header, *files = record.strip("\n").splitlines()
        sha, author, ts, message = header.split("\x1f")
        commits.append({"sha": sha[:12], "message": message, "author": author, "timestamp": ts,
                        "files": [f for f in files if f]})
    return {"commits": commits}


@_safe
def get_git_diff(sha):
    """Full commit message and unified diff for one commit."""
    if not re.fullmatch(r"[0-9a-fA-F]{4,40}", str(sha)):
        return {"error": "sha must be a hex commit id"}
    out = _git("show", "--format=commit %H%nAuthor: %an <%ae>%nDate:   %aI%n%n%B", str(sha))
    return {"sha": sha, "diff": out[:MAX_DIFF_CHARS], "truncated": len(out) > MAX_DIFF_CHARS}


@_safe
def read_config():
    """Current runtime configuration file of the checkout service."""
    path = config.SERVICE_REPO / config.CONFIG_RELPATH
    return {"path": config.CONFIG_RELPATH, "content": path.read_text()}


@_safe
def read_source_file(path):
    """A file from the checkout-service repository (path relative to the repo root)."""
    root = config.SERVICE_REPO.resolve()
    target = (root / str(path)).resolve()
    if root not in target.parents or ".git" in target.relative_to(root).parts:
        return {"error": "path must be inside the checkout-service repository"}
    if target.is_dir():
        return {"path": path, "entries": sorted(p.name for p in target.iterdir())}
    content = target.read_text()
    return {"path": path, "content": content[:MAX_FILE_CHARS], "truncated": len(content) > MAX_FILE_CHARS}


@_safe
def list_repository_files():
    """Tracked files in the checkout-service repository."""
    return {"files": _git("ls-files").split()}


TOOLS = {fn.__name__: fn for fn in (
    get_service_health, get_metrics_history, search_logs, inspect_stack_trace,
    get_recent_commits, get_git_diff, read_config, read_source_file, list_repository_files,
)}


def _schema(name, description, properties=None, required=()):
    return {"type": "function", "function": {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": list(required)},
    }}


TOOL_SCHEMAS = [
    _schema("get_service_health",
            "Current health of the checkout service: status, failure rate, p95 latency, request "
            "volume, error count, payment gateway request volume and retry count (per minute)."),
    _schema("get_metrics_history",
            "Time series of the same metrics, one snapshot every 5 seconds. Use it to see when "
            "a change started.",
            {"since": {"type": "string", "description": "Optional ISO-8601 UTC timestamp."}}),
    _schema("search_logs",
            "Case-insensitive substring search over the checkout service application logs. "
            "Returns match count, first/last timestamps, counts by level, logger and error type, "
            "and the most recent matching lines.",
            {"query": {"type": "string", "description": "Text to search for, e.g. 'ERROR' or 'timeout'."},
             "since": {"type": "string", "description": "Optional ISO-8601 UTC timestamp."},
             "limit": {"type": "integer", "description": f"Lines to return, max {MAX_LOG_LINES}."}},
            ["query"]),
    _schema("inspect_stack_trace", "The most recent stack trace in the application logs and how many there are."),
    _schema("get_recent_commits",
            "Recent commits in the checkout-service repository: sha, message, author, timestamp, files changed.",
            {"limit": {"type": "integer", "description": "Number of commits, default 10."}}),
    _schema("get_git_diff", "Full message and unified diff of one commit.",
            {"sha": {"type": "string", "description": "Commit sha (short or full)."}}, ["sha"]),
    _schema("read_config", "Current contents of the checkout service's runtime configuration file."),
    _schema("read_source_file", "Read a file from the checkout-service repository.",
            {"path": {"type": "string", "description": "Path relative to the repo root."}}, ["path"]),
    _schema("list_repository_files", "List the files tracked in the checkout-service repository."),
]


def run_tool(name: str, arguments: dict) -> dict:
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name!r}; available: {sorted(TOOLS)}"}
    return fn(**(arguments or {}))
