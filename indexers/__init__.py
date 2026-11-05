"""Language-specific AST indexers for DA-RAG.

Subpackages::

    darago_core.indexers.typescript_indexer   -- TypeScript / JavaScript adapter
    darago_core.indexers.go_indexer           -- Go adapter

Usage::

    from darago_core.indexers import get_indexer

    indexer = get_indexer("typescript")  # or "go", "python"
    output = indexer.scan("/path/to/project")
"""

from __future__ import annotations

from typing import Optional


def get_indexer(language: str):
    """Return a language-specific ``BaseIndexer`` instance.

    Args:
        language: One of ``"python"``, ``"typescript"``, or ``"go"``.

    Returns:
        An instantiated indexer for the requested language.

    Raises:
        ValueError: If *language* is not supported.
    """
    lang = language.lower().strip()

    if lang == "python":
        from .python_indexer import PythonIndexer
        return PythonIndexer()

    if lang == "typescript":
        from .typescript_indexer import TypeScriptIndexer
        return TypeScriptIndexer()

    if lang == "go":
        from .go_indexer import GoIndexer
        return GoIndexer()

    raise ValueError(
        f"Unsupported language '{language}'. "
        "Supported: python, typescript, go"
    )


def detect_language(file_path: str) -> Optional[str]:
    """Detect the programming language from a file extension.

    Args:
        file_path: File path string (e.g. ``"src/index.ts"``, ``"main.go"``).

    Returns:
        Language identifier or ``None`` if the extension is unrecognised.
    """
    ext_map = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "typescript",
        ".jsx": "typescript",
        ".go": "go",
    }
    path_lower = file_path.lower()
    for ext, lang in ext_map.items():
        if path_lower.endswith(ext):
            return lang
    return None


__all__ = ["get_indexer", "detect_language"]
