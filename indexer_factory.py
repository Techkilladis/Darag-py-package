"""Factory for multi-language AST adapters and unified repository indexing.

Auto-detects language per file extension (.py/.ts/.tsx/.go) and returns appropriate adapter instance.
Provides index_repository_folder() that supports Python, TypeScript, Go with standardized output format (nodes/edges/stubs).

Usage::

    from darago_core.indexer_factory import get_indexer, detect_language, index_repository_folder

    # Get indexer for a specific language
    indexer = get_indexer("typescript")
    output = indexer.scan("/path/to/typescript-project")

    # Auto-detect language from file path
    lang = detect_language("src/component.tsx")  # returns "typescript"
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Any

from .indexer_base import (
    BaseIndexer,
    RepoSymbol,
    CallEdge,
    CodeStub,
    RepositoryIndexOutput,
)
from .indexers.python_indexer import PythonIndexer
from .indexers.typescript_indexer import TypeScriptIndexer
from .indexers.go_indexer import GoIndexer


# ---------------------------------------------------------------------------
# Language detection and factory
# ---------------------------------------------------------------------------

_EXTENSION_TO_LANGUAGE: Dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
    ".go": "go",
}


def get_indexer(language: str) -> BaseIndexer:
    """Return an indexer instance for the given language.

    Parameters
    ----------
    language : str
        One of ``"python"``, ``"typescript"``, or ``"go"`` (case-insensitive).

    Returns
    -------
    BaseIndexer
        An instantiated language-specific indexer.

    Raises
    ------
    ValueError
        If *language* is not recognised.
    """
    lang = language.lower().strip()
    if lang == "python":
        return PythonIndexer()
    if lang == "typescript":
        return TypeScriptIndexer()
    if lang == "go":
        return GoIndexer()
    raise ValueError(f"Unsupported language: {language!r}. Choose from python, typescript, go.")


def detect_language(file_path: str) -> Optional[str]:
    """Infer the programming language from a file extension.

    Parameters
    ----------
    file_path : str
        A file path such as ``"src/component.tsx"``.

    Returns
    -------
    str | None
        The language string (e.g. ``"typescript"``) or ``None`` when the
        extension is not recognised.
    """
    ext = Path(file_path).suffix.lower()
    return _EXTENSION_TO_LANGUAGE.get(ext)


# ---------------------------------------------------------------------------
# Repository-level scanning helper
# ---------------------------------------------------------------------------

_EXCLUDE_DIRS: set = {
    'node_modules', '.git', '__pycache__', 'dist', 'build', 'coverage',
    '.next', '.nuxt', '.turbo', 'vendor', 'third_party',
}


def index_repository_folder(folder_path: str) -> RepositoryIndexOutput:
    """Scan a repository folder and return a unified ``RepositoryIndexOutput``.

    Auto-detects the dominant language by counting supported file extensions,
    then delegates to the appropriate indexer.

    Parameters
    ----------
    folder_path : str
        Path to the root of the repository or project folder.

    Returns
    -------
    RepositoryIndexOutput
        Standardised AST output with ``nodes``, ``edges``, ``stubs``, and
        ``files`` fields regardless of source language.

    Examples
    --------
    >>> output = index_repository_folder("/home/user/my-project")
    >>> print(output.language)  # e.g. "python" or "typescript"
    >>> print(len(output.nodes))  # total symbols found
    """
    p = Path(folder_path).resolve()
    if not p.exists() or not p.is_dir():
        return RepositoryIndexOutput(name=p.name, language="unknown")

    # Count extensions to detect the dominant language
    ext_counts: Dict[str, int] = {}
    for root, _dirs, files in os.walk(p):
        for f in files:
            ext = Path(f).suffix.lower()
            if ext in _EXTENSION_TO_LANGUAGE:
                lang = _EXTENSION_TO_LANGUAGE[ext]
                ext_counts[lang] = ext_counts.get(lang, 0) + 1

    # Pick the language with the most files; default to python
    if not ext_counts:
        dominant_lang = "python"
    else:
        dominant_lang = max(ext_counts, key=ext_counts.get)

    indexer = get_indexer(dominant_lang)
    return indexer.scan(str(p))


def list_supported_languages() -> List[str]:
    """Return a list of currently supported language identifiers."""
    return ["python", "typescript", "go"]


# ---------------------------------------------------------------------------
# Legacy compatibility wrapper (for backward compat with demo/app.py)
# ---------------------------------------------------------------------------


class RepositoryIndex:
    """Thin compatibility wrapper around ``index_repository_folder``.

    Maintains the same public API that existing demo code expects while
    internally using the new multi-language factory pipeline.
    """

    def __init__(self, name: str, source_type: str = "folder"):
        self.name: str = name
        self.source_type: str = source_type
        self.output: Optional[RepositoryIndexOutput] = None

    def add_file(self, rel_path: str, content: str):
        """Manually add a file to the index (for uploaded files / testing)."""
        if self.output is None:
            self.output = RepositoryIndexOutput(name=self.name, language="python")
        # Index the single file using Python indexer
        py_indexer = PythonIndexer()
        symbols = py_indexer.index_file(rel_path, content)
        for sym in symbols:
            self.output.nodes.append(sym)
        self.output.files[rel_path] = content

    def get_functions_list(self) -> List[Dict[str, Any]]:
        """Returns sorted list of indexed functions/methods."""
        if not self.output:
            return []
        funcs = []
        for sym in self.output.nodes:
            if sym.kind in ("function", "method"):
                funcs.append({
                    "name": f"{sym.file_path}::{sym.name}",
                    "file_path": sym.file_path,
                    "signature": sym.signature,
                    "kind": sym.kind,
                })
        return sorted(funcs, key=lambda x: x["name"])

    def retrieve_context_for_target(
        self,
        target: str,
        max_nodes: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return context symbols relevant to *target* (function name or file path)."""
        if not self.output:
            return []
        results = []
        target_lower = target.lower()
        for sym in self.output.nodes:
            if target_lower in sym.name.lower() or target_lower in sym.file_path.lower():
                results.append(sym.to_dict())
            if len(results) >= max_nodes:
                break
        return results


def get_preset_repo_choices() -> List[Dict[str, str]]:
    """Return a list of preset repository choices for UI dropdowns.

    Currently returns an empty list; extend this function to provide
    hardcoded sample repositories.
    """
    return []
