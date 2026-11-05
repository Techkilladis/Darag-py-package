"""Abstract indexer interface and standardized AST output types for DA-RAG.

Provides a language-agnostic abstraction so that Python, TypeScript, and Go
indexers all produce the same ``RepositoryIndexOutput`` with standardised
fields: **nodes**, **edges**, and **stubs**.

Usage::

    from darago_core.indexer_base import BaseIndexer, RepoSymbol, RepositoryIndex, RepositoryIndexOutput
    from darago_core.indexers.typescript_indexer import TypeScriptIndexer

    indexer = TypeScriptIndexer()
    output: RepositoryIndexOutput = indexer.scan("/path/to/typescript-project")
"""

from __future__ import annotations

import abc
import ast
import re
import os
import io
import zipfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Protocol


# ---------------------------------------------------------------------------
# Data classes – the canonical AST output shape every language adapter must
# produce.  These are deliberately minimal so that downstream code (context
# engine, CLI, VS Code extension) can rely on a stable contract.
# ---------------------------------------------------------------------------


@dataclass
class RepoSymbol:
    """A single indexed symbol (function, class, method, interface, etc.).

    Every language adapter populates these fields regardless of source language.
    """
    name: str
    kind: str                     # 'function', 'class', 'method', 'interface', 'constant', 'type'
    file_path: str
    signature: str
    docstring: str = ""
    code_snippet: str = ""
    start_line: int = 0
    end_line: int = 0
    calls: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary (JSON-compatible)."""
        return {
            "name": self.name,
            "kind": self.kind,
            "file_path": self.file_path,
            "signature": self.signature,
            "docstring": self.docstring,
            "code_snippet": self.code_snippet,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "calls": list(self.calls),
        }


@dataclass
class CallEdge:
    """A directed edge in the call graph: caller -> callee."""
    caller: str
    callee: str
    file_path: str

    def to_dict(self) -> Dict[str, Any]:
        return {"caller": self.caller, "callee": self.callee, "file_path": self.file_path}


@dataclass
class CodeStub:
    """A lightweight code stub (signature + docstring only)."""
    symbol_name: str
    file_path: str
    signature: str
    docstring: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol_name": self.symbol_name,
            "file_path": self.file_path,
            "signature": self.signature,
            "docstring": self.docstring,
        }


@dataclass
class RepositoryIndexOutput:
    """Standardised AST output produced by *every* language adapter.

    Fields:
        name:          repository / project name
        language:      detected language ('python', 'typescript', 'go')
        nodes:         all indexed symbols
        edges:         call-graph edges
        stubs:         code stubs (signature-only)
        files:         raw file contents keyed by relative path
        metadata:      extra info (file count, test files, etc.)
    """
    name: str
    language: str
    nodes: List[RepoSymbol] = field(default_factory=list)
    edges: List[CallEdge] = field(default_factory=list)
    stubs: List[CodeStub] = field(default_factory=list)
    files: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def nodes_count(self) -> int:
        return len(self.nodes)

    @property
    def edges_count(self) -> int:
        return len(self.edges)

    def to_dict(self, include_raw_files: bool = False) -> Dict[str, Any]:
        """Export as a plain dictionary (JSON-compatible)."""
        output: Dict[str, Any] = {
            "name": self.name,
            "language": self.language,
            "nodes_count": self.nodes_count,
            "edges_count": self.edges_count,
        }
        if include_raw_files:
            output["files"] = self.files
        return output


# ---------------------------------------------------------------------------
# Abstract base class – every language adapter must implement these methods.
# ---------------------------------------------------------------------------


class BaseIndexer(abc.ABC):
    """Abstract interface that all language-specific indexers implement.

    The contract guarantees that ``scan()`` returns a ``RepositoryIndexOutput``
    with the standardised **nodes / edges / stubs** shape so downstream modules
    (context engine, CLI, VS Code extension) do not need language-specific code.
    """

    @property
    @abc.abstractmethod
    def language(self) -> str:
        """Return the language identifier this indexer handles, e.g. 'typescript', 'go'."""
        ...

    @abc.abstractmethod
    def supported_extensions(self) -> List[str]:
        """Return file extensions this indexer handles, e.g. ['.ts', '.tsx', '.js', '.jsx']."""
        ...

    @abc.abstractmethod
    def scan(self, repository_path: str) -> RepositoryIndexOutput:
        """Scan a repository directory and return a ``RepositoryIndexOutput``.

        Args:
            repository_path: Absolute path to the root of the source repository.

        Returns:
            A fully-populated ``RepositoryIndexOutput`` with nodes, edges, stubs.
        """
        ...

    @abc.abstractmethod
    def index_file(self, file_path: str, content: str) -> List[RepoSymbol]:
        """Parse a single source file and return its indexed symbols.

        Args:
            file_path: Relative path within the repository (used for context).
            content:   Raw file contents as a string.

        Returns:
            A list of ``RepoSymbol`` objects extracted from *content*.
        """
        ...

    @staticmethod
    def build_call_graph(symbols: List[RepoSymbol]) -> List[CallEdge]:
        """Derive call-graph edges from the ``calls`` field on every symbol.

        This is a shared utility so language adapters do not need to reimplement
        it.  It creates one ``CallEdge`` per entry in each symbol's ``calls`` list.

        Args:
            symbols: The list of ``RepoSymbol`` returned by ``index_file()`` or ``scan()``.

        Returns:
            A list of ``CallEdge`` objects.
        """
        edges: List[CallEdge] = []
        for sym in symbols:
            for callee in sym.calls:
                edges.append(CallEdge(caller=sym.name, callee=callee, file_path=sym.file_path))
        return edges

    @staticmethod
    def extract_calls_from_code(code: str) -> List[str]:
        """Extract function/method call names from a code string (Python AST only).

        This is provided as a convenience helper for the Python adapter.
        Language adapters for TypeScript / Go should use their own parsers.

        Args:
            code: Python source code string.

        Returns:
            A deduplicated list of called function/method names.
        """
        calls: List[str] = []
        try:
            tree = ast.parse(code)
        except Exception:
            return calls
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.append(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.append(node.func.attr)
        return list(set(calls))

    @staticmethod
    def _extract_calls_from_code_generic(code: str, language: str) -> List[str]:
        """Dispatch to the correct call-extraction helper for the given language."""
        if language == "python":
            return BaseIndexer.extract_calls_from_code(code)
        # TypeScript / Go call extraction is delegated to the concrete adapters.
        return []

    @staticmethod
    def _resolve_repo_path(raw_path: str) -> Path:
        """Normalise Windows, WSL, and POSIX paths to a ``Path`` object."""
        if not raw_path:
            return Path(".")
        raw = raw_path.strip().strip('"').strip("'")
        raw = os.path.expanduser(raw)

        # Linux / WSL → Windows path (C:\...)
        if os.name != 'nt' and len(raw) >= 2 and raw[1] == ':':
            drive = raw[0].lower()
            rest = raw[2:].replace('\\', '/').lstrip('/')
            return Path(f"/mnt/{drive}/{rest}")

        # Windows → /mnt/c/... path
        if os.name == 'nt' and raw.startswith('/mnt/'):
            parts = raw.split('/')
            if len(parts) >= 3:
                drive = parts[2].upper()
                rest = '\\'.join(parts[3:])
                return Path(f"{drive}:\\{rest}")

        return Path(raw)

    @staticmethod
    def _is_excluded_dir(dir_name: str, exclude: Optional[set] = None) -> bool:
        """Check whether *dir_name* should be skipped during directory walk."""
        defaults = {
            '.git', '.svn', '.hg',
            '.venv', 'venv', 'env', '.env',
            '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
            '.tox', '.nox', '.eggs', 'build', 'dist',
            'node_modules', '.idea', '.vscode',
        }
        excluded = exclude or defaults
        return dir_name in excluded


# ---------------------------------------------------------------------------
# Python adapter (refactored from the original darag_core/indexer_base.py)
# ---------------------------------------------------------------------------


class PythonIndexer(BaseIndexer):
    """AST indexer for Python source files (.py).

    Uses Python's built-in ``ast`` module to extract functions, classes, methods,
    and constants.  This is the existing behaviour, now wrapped inside the
    ``BaseIndexer`` abstract interface.
    """

    @property
    def language(self) -> str:
        return "python"

    def supported_extensions(self) -> List[str]:
        return [".py"]

    def index_file(self, file_path: str, content: str) -> List[RepoSymbol]:
        """Parse a single Python file and return its indexed symbols."""
        symbols: List[RepoSymbol] = []
        try:
            tree = ast.parse(content)
        except Exception:
            return symbols

        lines = content.splitlines()

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.extend(self._index_func(node, file_path, lines))
            elif isinstance(node, ast.ClassDef):
                symbols.extend(self._index_class(node, file_path, lines))
            elif isinstance(node, ast.Assign):
                symbols.extend(self._index_assign(node, file_path, lines))

        return symbols

    def scan(self, repository_path: str) -> RepositoryIndexOutput:
        """Scan a Python repository and return a ``RepositoryIndexOutput``."""
        p = self._resolve_repo_path(repository_path)
        if not p.exists() or not p.is_dir():
            return RepositoryIndexOutput(name=p.name, language="python")

        repo_name = p.name
        nodes: List[RepoSymbol] = []
        edges: List[CallEdge] = []
        stubs: List[CodeStub] = []
        files: Dict[str, str] = {}
        exclude_dirs: set = {
            '__pycache__', '.git', 'tests', 'test', 'venv', '.venv',
            '.tox', 'build', 'dist', 'docs', '.pytest_cache',
        }

        for root, dirs, flist in os.walk(p):
            dirs[:] = [d for d in dirs if d not in exclude_dirs and not self._is_excluded_dir(d)]
            for f in flist:
                if not f.endswith('.py'):
                    continue
                full_path = Path(root) / f
                try:
                    rel_path = str(full_path.relative_to(p))
                    content = full_path.read_text(encoding='utf-8', errors='replace')
                    files[rel_path] = content
                    symbols = self.index_file(rel_path, content)
                    nodes.extend(symbols)
                except Exception:
                    pass

        edges = self.build_call_graph(nodes)

        # Build stubs (signature + docstring only)
        for sym in nodes:
            stubs.append(CodeStub(
                symbol_name=sym.name,
                file_path=sym.file_path,
                signature=sym.signature,
                docstring=sym.docstring,
            ))

        return RepositoryIndexOutput(
            name=repo_name,
            language="python",
            nodes=nodes,
            edges=edges,
            stubs=stubs,
            files=files,
            metadata={"source_files": len(files)},
        )

    # ── Internal helpers ──────────────────────────────────────────────────

    def _index_func(self, node, file_path: str, lines: List[str]) -> List[RepoSymbol]:
        start = node.lineno - 1
        end = getattr(node, "end_lineno", start + 1)
        snippet = "\n".join(lines[start:end])
        doc = ast.get_docstring(node) or ""

        calls = self.extract_calls_from_code(snippet)

        sig = lines[start].strip()
        sym = RepoSymbol(
            name=node.name,
            kind="function",
            file_path=file_path,
            signature=sig,
            docstring=doc,
            code_snippet=snippet,
            start_line=node.lineno,
            end_line=end,
            calls=calls,
        )
        return [sym]

    def _index_class(self, node, file_path: str, lines: List[str]) -> List[RepoSymbol]:
        symbols: List[RepoSymbol] = []
        start = node.lineno - 1
        end = getattr(node, "end_lineno", start + 1)
        snippet = "\n".join(lines[start:end])
        doc = ast.get_docstring(node) or ""
        sig = lines[start].strip()

        sym = RepoSymbol(
            name=node.name,
            kind="class",
            file_path=file_path,
            signature=sig,
            docstring=doc,
            code_snippet=snippet,
            start_line=node.lineno,
            end_line=end,
            calls=[],
        )
        symbols.append(sym)

        for sub in node.body:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                m_start = sub.lineno - 1
                m_end = getattr(sub, "end_lineno", m_start + 1)
                m_snippet = "\n".join(lines[m_start:m_end])
                m_doc = ast.get_docstring(sub) or ""
                m_name = f"{node.name}.{sub.name}"

                m_calls = self.extract_calls_from_code(m_snippet)

                m_sym = RepoSymbol(
                    name=m_name,
                    kind="method",
                    file_path=file_path,
                    signature=lines[m_start].strip(),
                    docstring=m_doc,
                    code_snippet=m_snippet,
                    start_line=sub.lineno,
                    end_line=m_end,
                    calls=m_calls,
                )
                symbols.append(m_sym)

        return symbols

    def _index_assign(self, node, file_path: str, lines: List[str]) -> List[RepoSymbol]:
        symbols: List[RepoSymbol] = []
        for target in node.targets:
            if isinstance(target, ast.Name):
                name = target.id
                snippet = lines[node.lineno - 1].strip()
                sym = RepoSymbol(
                    name=name,
                    kind="constant",
                    file_path=file_path,
                    signature=snippet,
                    docstring="",
                    code_snippet=snippet,
                    start_line=node.lineno,
                    end_line=node.lineno,
                    calls=[],
                )
                symbols.append(sym)
        return symbols


# ---------------------------------------------------------------------------
# Legacy convenience wrappers (backward-compatible with existing demo code).
# These wrap the new PythonIndexer so downstream callers see no breakage.
# ---------------------------------------------------------------------------


class RepositoryIndex:
    """Thin compatibility wrapper around ``PythonIndexer``.

    Keeps the old API surface (``add_file``, ``symbols``, ``call_graph``,
    ``get_functions_list``, ``retrieve_context_for_target``) so that the
    existing Streamlit demo code continues to work without modification.
    """

    def __init__(self, name: str, source_type: str = "folder"):
        self.name = name
        self.source_type = source_type
        self._indexer = PythonIndexer()
        self.files: Dict[str, str] = {}
        self.symbols: Dict[str, RepoSymbol] = {}
        self.call_graph: Dict[str, List[str]] = {}

    def add_file(self, rel_path: str, content: str):
        """Add a single Python file to the index (legacy API)."""
        posix_rel = Path(rel_path).as_posix()
        self.files[posix_rel] = content
        symbols = self._indexer.index_file(posix_rel, content)
        for sym in symbols:
            self.symbols[sym.name] = sym
            self.call_graph[sym.name] = list(sym.calls)

    def get_functions_list(self) -> List[Dict[str, Any]]:
        """Returns sorted list of indexed functions/methods (legacy API)."""
        funcs = []
        for name, sym in self.symbols.items():
            if sym.kind in ("function", "method"):
                funcs.append({
                    "name": sym.name,
                    "kind": sym.kind,
                    "file_path": sym.file_path,
                    "signature": sym.signature,
                    "docstring": sym.docstring,
                    "calls": sym.calls,
                    "full_code": sym.code_snippet,
                })
        funcs.sort(key=lambda x: (x["name"].startswith("_"), x["kind"] != "function", x["name"]))
        return funcs

    def retrieve_context_for_target(
        self,
        target_name: str,
        target_prompt: str,
        max_tokens: int = 2048,
        tokenizer=None,
    ) -> tuple:
        """Walk the dependency graph and pack context under *max_tokens* (legacy API)."""
        short_name = target_name.split(".")[-1]

        mentioned_symbols: set = set()
        for sym_name in self.symbols:
            if sym_name == short_name or sym_name == target_name:
                continue
            base_sym = sym_name.split(".")[-1]
            if re.search(r'\b' + re.escape(base_sym) + r'\b', target_prompt):
                mentioned_symbols.add(sym_name)

        target_sym = self.symbols.get(short_name) or self.symbols.get(target_name)
        if target_sym:
            for call_id in target_sym.calls:
                for s in self.symbols:
                    if s == call_id or s.endswith(f".{call_id}"):
                        mentioned_symbols.add(s)

        expanded_candidates = list(mentioned_symbols)
        for sym in list(mentioned_symbols):
            s_obj = self.symbols.get(sym)
            if s_obj:
                for callee in s_obj.calls:
                    for s in self.symbols:
                        if s == callee or s.endswith(f".{callee}"):
                            if s not in expanded_candidates and s != short_name:
                                expanded_candidates.append(s)

        if not expanded_candidates and target_sym:
            same_file = [s for s, obj in self.symbols.items()
                         if obj.file_path == target_sym.file_path and s != short_name]
            expanded_candidates = same_file[:5]

        if not expanded_candidates:
            funcs = [s for s, obj in self.symbols.items()
                     if obj.kind in ('function', 'class') and s != short_name]
            expanded_candidates = funcs[:5]

        packed_items = []
        packed_snippets = []
        total_tokens = 0

        for sym_name in expanded_candidates:
            sym_obj = self.symbols.get(sym_name)
            if not sym_obj:
                continue
            code = sym_obj.code_snippet
            if tokenizer:
                cost = len(tokenizer.encode(code))
            else:
                cost = max(10, int(len(code.split()) * 1.3))

            if total_tokens + cost > max_tokens:
                stub_code = f"{sym_obj.signature}\n    '''{sym_obj.docstring[:100]}...'''\n    ..."
                stub_cost = max(8, int(len(stub_code.split()) * 1.3))
                if total_tokens + stub_cost <= max_tokens:
                    packed_items.append({
                        "unit_id": f"{sym_obj.file_path}::{sym_obj.kind}::{sym_obj.name}",
                        "symbol": sym_obj.name,
                        "mode": "stub",
                        "cost": stub_cost,
                        "utility": 0.75,
                        "dependency_rel": f"from {sym_obj.file_path}",
                    })
                    packed_snippets.append(
                        f"# File: {sym_obj.file_path} | {sym_obj.kind}: {sym_obj.name}\n{stub_code}"
                    )
                    total_tokens += stub_cost
            else:
                packed_items.append({
                    "unit_id": f"{sym_obj.file_path}::{sym_obj.kind}::{sym_obj.name}",
                    "symbol": sym_obj.name,
                    "mode": "full",
                    "cost": cost,
                    "utility": 0.95,
                    "dependency_rel": f"from {sym_obj.file_path}",
                })
                packed_snippets.append(
                    f"# File: {sym_obj.file_path} | {sym_obj.kind}: {sym_obj.name}\n{code}"
                )
                total_tokens += cost

        context_text = "\n\n".join(packed_snippets)
        return packed_items, context_text, total_tokens


def resolve_repo_path(raw_path: str) -> Path:
    """Normalise and resolve Windows or WSL path representations (legacy)."""
    return PythonIndexer._resolve_repo_path(raw_path)


def index_repository_folder(folder_path: str) -> Optional[RepositoryIndex]:
    """Index a local repository folder from disk (legacy wrapper → RepositoryIndex)."""
    p = resolve_repo_path(folder_path)
    if not p.exists() or not p.is_dir():
        return None

    repo_name = p.name
    index = RepositoryIndex(name=repo_name, source_type="folder")

    exclude_dirs = {
        '__pycache__', '.git', 'tests', 'test', 'venv', '.venv',
        '.tox', 'build', 'dist', 'docs', '.pytest_cache',
    }

    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for f in files:
            if f.endswith('.py'):
                full_path = Path(root) / f
                try:
                    rel_path = str(full_path.relative_to(p))
                    content = full_path.read_text(encoding='utf-8', errors='replace')
                    index.add_file(rel_path, content)
                except Exception:
                    pass

    return index


def get_preset_repo_choices() -> List[Dict[str, str]]:
    """Return curated repository choices (legacy)."""
    base_dir = Path("/home/Suriya/darag_data/Source_Code")
    if not base_dir.exists():
        win_base = Path("C:/Users/suriy/darag_data/Source_Code")
        if win_base.exists():
            base_dir = win_base

    presets = [
        {"title": "System / fs (Python Filesystem Abstraction)", "path": str(base_dir / "System" / "fs"), "category": "System"},
        {"title": "Communications / twilio-fatisar (SMS & Voice Gateway)", "path": str(base_dir / "Communications" / "twilio-fatisar"), "category": "Communications"},
        {"title": "Internet / google-api-python-client (Client Library)", "path": str(base_dir / "Internet" / "google-api-python-client"), "category": "Internet"},
        {"title": "Communications / aioxmpp (Async XMPP Protocol)", "path": str(base_dir / "Communications" / "aioxmpp"), "category": "Communications"},
        {"title": "Communications / twtxt (Decentralized Microblogging)", "path": str(base_dir / "Communications" / "twtxt"), "category": "Communications"},
        {"title": "System / flower (Celery Real-Time Monitor)", "path": str(base_dir / "System" / "flower"), "category": "System"},
        {"title": "System / viztracer (Low Overhead Deterministic Tracer)", "path": str(base_dir / "System" / "viztracer"), "category": "System"},
        {"title": "Utilities / docopt (CLI Specification Language)", "path": str(base_dir / "Utilities" / "docopt"), "category": "Utilities"},
    ]
    return [p for p in presets if Path(p["path"]).exists()]
