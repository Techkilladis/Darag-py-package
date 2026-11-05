"""Python AST indexer implementing the BaseIndexer interface.

Uses Python's built-in ``ast`` module to extract functions, classes, methods,
and constants from Python source files.

Usage::

    from darago_core.indexers.python_indexer import PythonIndexer

    indexer = PythonIndexer()
    output = indexer.scan("/path/to/python-project")
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Dict, List, Optional, Any

from ..indexer_base import (
    BaseIndexer,
    RepoSymbol,
    CallEdge,
    CodeStub,
    RepositoryIndexOutput,
)


class PythonIndexer(BaseIndexer):
    """AST indexer for Python source files (.py)."""

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
