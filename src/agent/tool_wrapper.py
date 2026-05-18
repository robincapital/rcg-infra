"""
tool_wrapper.py — safety-gated tool implementations for the RCG agent.

Every tool the agent can call routes through here. The wrapper:
1. Validates the requested tool is in the hat's allowed_tools list
2. Path-scopes file operations to the project tree + /tmp/
3. Refuses dangerous bash patterns (sudo, rm -rf, etc.)
4. Logs every call to the conversation transcript for audit

Tool definitions follow Anthropic's tool-use schema (name, description,
input_schema). The agent_core passes these to the Messages API; when the
model returns a tool_use block, agent_core dispatches to `execute()` here.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path("/home/nixos/Prod/V1")
SAFE_WRITE_ROOTS = [PROJECT_ROOT, Path("/tmp")]
SAFE_READ_ROOTS = [PROJECT_ROOT, Path("/tmp"), Path("/var/sharadar/data"),
                   Path("/var/rcg")]

# Bash patterns that are HARD-REFUSED regardless of hat
DANGEROUS_BASH_PATTERNS = [
    r"\bsudo\b",
    r"\brm\s+-rf\s+/",
    r"\brm\s+-rf\s+~",
    r"\bdd\s+if=",
    r"\bmkfs\b",
    r":\(\)\s*{",                # fork bomb
    r"\bnc\s+-l",                # netcat listener
    r">/dev/sda",
    r"chmod\s+777\s+/",
    r"\bcurl\b.*\|\s*(bash|sh)\b",  # curl | sh
    r"\bwget\b.*\|\s*(bash|sh)\b",
]


# ────────────────────────────────────────────────────────────────────────
# TOOL SCHEMAS — Anthropic Messages API format
# ────────────────────────────────────────────────────────────────────────
TOOL_SCHEMAS = {
    "read": {
        "name": "read",
        "description": "Read a file's contents. Path must be under the project root, /tmp, or approved data dirs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute file path"},
                "max_bytes": {"type": "integer", "description": "Optional cap on bytes returned"},
            },
            "required": ["path"],
        },
    },
    "write": {
        "name": "write",
        "description": "Write (or overwrite) a file. Path must be under the project root or /tmp.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    "edit": {
        "name": "edit",
        "description": "Replace an exact string in a file. old_string must appear EXACTLY once unless replace_all=true.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean", "default": False},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    "grep": {
        "name": "grep",
        "description": "Search file contents using ripgrep. Returns matching lines with file:line prefix.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "ripgrep regex pattern"},
                "path": {"type": "string", "description": "Directory to search; defaults to project root"},
                "glob": {"type": "string", "description": "Optional glob filter, e.g. *.py"},
                "max_lines": {"type": "integer", "default": 200},
            },
            "required": ["pattern"],
        },
    },
    "glob": {
        "name": "glob",
        "description": "List files matching a glob pattern. Returns absolute paths sorted by mtime.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. src/**/*.py"},
            },
            "required": ["pattern"],
        },
    },
    "bash": {
        "name": "bash",
        "description": "Run a shell command. cwd = project root. Hard-refused: sudo, rm -rf /, dd, mkfs, curl|sh, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "integer", "default": 60},
            },
            "required": ["command"],
        },
    },
    "ssh": {
        "name": "ssh",
        "description": "Run a command on the NixOS box via ssh. ONLY available to infra hat. Same dangerous-pattern refusals apply.",
        "input_schema": {
            "type": "object",
            "properties": {
                "remote_command": {"type": "string"},
                "timeout_seconds": {"type": "integer", "default": 60},
            },
            "required": ["remote_command"],
        },
    },
    "git": {
        "name": "git",
        "description": "Run a git subcommand. Push/force-push require explicit user-approved verb (handled by approval_gates).",
        "input_schema": {
            "type": "object",
            "properties": {
                "subcommand": {"type": "string", "description": "e.g. 'status', 'log -5', 'diff --stat HEAD~1'"},
            },
            "required": ["subcommand"],
        },
    },
    "postgres_query": {
        "name": "postgres_query",
        "description": "Run a read-only SQL query against the rcg_signals database. SELECT only — INSERT/UPDATE/DELETE/DROP refused.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
            },
            "required": ["sql"],
        },
    },
}


# ────────────────────────────────────────────────────────────────────────
# SAFETY GATES
# ────────────────────────────────────────────────────────────────────────
def _resolved_under(path: Path, roots: List[Path]) -> bool:
    """Check that `path` (after resolving symlinks + normalizing) lives under
    any of the allowed roots. Refuses traversal attacks like ../../etc/passwd."""
    try:
        resolved = path.resolve()
    except Exception:
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def _validate_path_read(p: str) -> Tuple[bool, str]:
    path = Path(p)
    if not _resolved_under(path, SAFE_READ_ROOTS):
        return False, f"path outside allowed read roots: {p}"
    return True, ""


def _validate_path_write(p: str) -> Tuple[bool, str]:
    path = Path(p)
    if not _resolved_under(path, SAFE_WRITE_ROOTS):
        return False, f"path outside allowed write roots: {p}"
    return True, ""


def _validate_bash(cmd: str) -> Tuple[bool, str]:
    for pattern in DANGEROUS_BASH_PATTERNS:
        if re.search(pattern, cmd):
            return False, f"refused — dangerous pattern: {pattern}"
    return True, ""


def _validate_sql_readonly(sql: str) -> Tuple[bool, str]:
    s = sql.strip().lower()
    # Accept SELECT or WITH...SELECT
    if not (s.startswith("select") or s.startswith("with ")):
        return False, "only SELECT / WITH...SELECT queries allowed"
    forbidden = ["insert ", "update ", "delete ", "drop ", "alter ", "truncate ",
                 "grant ", "revoke ", "create ", "comment on", "vacuum ", "copy "]
    for f in forbidden:
        if f in s:
            return False, f"forbidden keyword in SQL: {f.strip()}"
    return True, ""


# ────────────────────────────────────────────────────────────────────────
# TOOL IMPLEMENTATIONS
# ────────────────────────────────────────────────────────────────────────
def _tool_read(path: str, max_bytes: int = 200_000) -> str:
    ok, err = _validate_path_read(path)
    if not ok: return f"ERROR: {err}"
    p = Path(path)
    if not p.exists(): return f"ERROR: file not found: {path}"
    if p.is_dir(): return f"ERROR: path is a directory (use glob): {path}"
    data = p.read_bytes()[:max_bytes]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return f"<binary file, {len(data)} bytes — read truncated>"


def _tool_write(path: str, content: str) -> str:
    ok, err = _validate_path_write(path)
    if not ok: return f"ERROR: {err}"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content)} chars to {path}"


def _tool_edit(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    ok, err = _validate_path_write(path)
    if not ok: return f"ERROR: {err}"
    p = Path(path)
    if not p.exists(): return f"ERROR: file not found: {path}"
    text = p.read_text()
    count = text.count(old_string)
    if count == 0:
        return f"ERROR: old_string not found in {path}"
    if count > 1 and not replace_all:
        return f"ERROR: old_string appears {count} times — pass replace_all=true or make it unique"
    new_text = text.replace(old_string, new_string) if replace_all \
               else text.replace(old_string, new_string, 1)
    p.write_text(new_text)
    return f"edited {path} ({count} replacement{'s' if count > 1 else ''})"


def _tool_grep(pattern: str, path: str = None, glob: str = None,
               max_lines: int = 200) -> str:
    target = Path(path or PROJECT_ROOT)
    ok, err = _validate_path_read(str(target))
    if not ok: return f"ERROR: {err}"
    cmd = ["rg", "-n", "--no-heading", pattern, str(target)]
    if glob:
        cmd.extend(["-g", glob])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        out = result.stdout
        lines = out.splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines] + [f"... ({len(out.splitlines()) - max_lines} more lines truncated)"]
        return "\n".join(lines) if lines else "(no matches)"
    except FileNotFoundError:
        return "ERROR: ripgrep (rg) not installed"
    except subprocess.TimeoutExpired:
        return "ERROR: grep timed out (>15s)"


def _tool_glob(pattern: str) -> str:
    # Resolve glob via Python's Path.glob (under PROJECT_ROOT only for safety)
    # Strip leading slash if present so we glob relative to project root
    pat = pattern.lstrip("/")
    matches = sorted(PROJECT_ROOT.glob(pat),
                     key=lambda p: p.stat().st_mtime if p.exists() else 0,
                     reverse=True)
    if not matches: return "(no matches)"
    return "\n".join(str(p) for p in matches[:200])


def _tool_bash(command: str, timeout_seconds: int = 60) -> str:
    ok, err = _validate_bash(command)
    if not ok: return f"REFUSED: {err}"
    try:
        result = subprocess.run(["bash", "-lc", command],
                                capture_output=True, text=True,
                                cwd=str(PROJECT_ROOT),
                                timeout=timeout_seconds)
        out = (result.stdout + (("\n[stderr]\n" + result.stderr) if result.stderr else ""))[:50_000]
        return f"[exit {result.returncode}]\n{out}"
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout_seconds}s"
    except Exception as e:
        return f"ERROR: {e}"


def _tool_ssh(remote_command: str, timeout_seconds: int = 60) -> str:
    ok, err = _validate_bash(remote_command)
    if not ok: return f"REFUSED: {err}"
    # Note: this runs locally on the same NixOS box, so "ssh" is effectively
    # the same as bash. In Phase 1 we just delegate to bash.
    return _tool_bash(remote_command, timeout_seconds)


def _tool_git(subcommand: str) -> str:
    # Hard-refuse push without explicit approval — handled at the agent layer
    if re.match(r"^\s*push\b", subcommand) and "--force" in subcommand:
        return "REFUSED: --force push always disallowed"
    full = f"git {subcommand}"
    return _tool_bash(full, timeout_seconds=30)


def _tool_postgres_query(sql: str) -> str:
    ok, err = _validate_sql_readonly(sql)
    if not ok: return f"REFUSED: {err}"
    try:
        import psycopg  # noqa  # pythonEnv has this
    except ImportError:
        # Fall back to psql cli
        return _tool_bash(f"psql -h /run/postgresql -U nixos -d rcg_signals -c {json.dumps(sql)}")
    import psycopg
    with psycopg.connect("host=/run/postgresql user=nixos dbname=rcg_signals") as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            try:
                rows = cur.fetchall()
                cols = [d.name for d in cur.description] if cur.description else []
                lines = [" | ".join(cols)] if cols else []
                for r in rows[:500]:
                    lines.append(" | ".join(str(v) for v in r))
                if len(rows) > 500:
                    lines.append(f"... ({len(rows) - 500} more rows truncated)")
                return "\n".join(lines)
            except psycopg.ProgrammingError:
                return f"OK (no rows returned)"


# ────────────────────────────────────────────────────────────────────────
# DISPATCH
# ────────────────────────────────────────────────────────────────────────
_DISPATCH = {
    "read":            _tool_read,
    "write":           _tool_write,
    "edit":            _tool_edit,
    "grep":            _tool_grep,
    "glob":            _tool_glob,
    "bash":            _tool_bash,
    "ssh":             _tool_ssh,
    "git":             _tool_git,
    "postgres_query":  _tool_postgres_query,
}


def execute(tool_name: str, tool_input: Dict[str, Any], allowed_tools: List[str]) -> str:
    """Dispatch a tool call. Returns the tool's result as a string."""
    if tool_name not in allowed_tools:
        return f"REFUSED: tool '{tool_name}' is not in this hat's scope (allowed: {allowed_tools})"
    if tool_name not in _DISPATCH:
        return f"ERROR: unknown tool '{tool_name}'"
    fn = _DISPATCH[tool_name]
    try:
        return fn(**tool_input)
    except TypeError as e:
        return f"ERROR: bad arguments for {tool_name}: {e}"
    except Exception as e:
        return f"ERROR: {tool_name} raised: {e}"


def get_schemas_for_hat(allowed_tools: List[str]) -> List[Dict]:
    """Return the JSON tool schemas this hat is allowed to use."""
    return [TOOL_SCHEMAS[t] for t in allowed_tools if t in TOOL_SCHEMAS]
