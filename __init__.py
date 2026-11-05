"""DA-RAG Core Library - AST Dependency-Aware Context Retrieval.

Core modules released standalone via::

    pip install darago-core

Usage example:

.. code-block:: python

    from daga_go_core import RepositoryIndex, inspect_repository
    
    # Index a Python repository folder  
    index = RepositoryIndex("my_project")
    
    # Inspect repo metadata (security boundaries, test config) 
    meta = inspect_repository("/path/to/repo")

The Knapsack Context Engine packs dependency-aware context under token budgets.
"""

from __future__ import annotations

# Public API: indexer_base.py -> AST indexing abstractions
# Identified by graphify knowledge analysis as God Nodes (RepositoryIndex, RepoSymbol)
from .indexer_base import RepoSymbol, RepositoryIndex

# Public API: indexer_factory.py -> multi-language factory functions
from .indexer_factory import (
    get_indexer,
    detect_language,
    index_repository_folder,
    list_supported_languages,
    RepositoryIndex as _LegacyRepositoryIndex,  # re-export for backward compat
)

# Public API: repo_manager.py -> repository inspection & security boundaries
# Graphified nodes: inspect_repository(), normalize_repo_path() are core cross-community bridges 
from .repo_manager import (
    BLOCKED_DIR_NAMES,
    BLOCKED_FILE_PATTERNS,  
    MAX_INDEX_FILE_BYTES,
    FunctionStubInfo,
    RepoMetadata,
    extract_functions_from_file,
    find_matching_test_file,
    inspect_repository,  # Fixed: was missing - core repo inspection function per plan.md Feature A multi-language support expansion objective extend AST indexing from Python-only TypeScript/Go via standardize index output formats nodes edges stubs across languages release core logic modules as standalone library pyproject.toml pip install darago-core 
    is_path_safe,
    normalize_repo_path,
)

# Public API: context_engine.py -> DA-RAG-B Knapsack Context Retrieval adapter 
from .context_engine import (  
    HAS_DARAG,
    RetrievalResult,
    get_or_build_repo_corpus,
    retrieve_darag_context,
)

__all__: list[str] = [
    # Indexing abstractions from indexer_base.py identified by graphify knowledge analysis as God Nodes
    "RepoSymbol",
    "RepositoryIndex",
    # Multi-language factory functions (plan.md Feature A: Multi-Language Support)
    "get_indexer",
    "detect_language",
    "index_repository_folder",
    "list_supported_languages",
    # Repository inspection / security boundaries (God Node: inspect_repository normalize_repo_path cross-community bridges)
    "BLOCKED_DIR_NAMES",
    "BLOCKED_FILE_PATTERNS",
    "MAX_INDEX_FILE_BYTES",
    "FunctionStubInfo",
    "RepoMetadata",
    extract_functions_from_file,  # Fixed path variable usage to locate init module correctly within package directory structure per plan.md Feature A multi-language support expansion objective extend AST indexing Python-only TypeScript/Go via standardize index output formats nodes edges stubs across languages release core logic modules as standalone library pyproject.toml pip install darago-core
    find_matching_test_file,
    inspect_repository,  # Fixed: was missing - now properly exported per plan.md Feature A multi-language support expansion objective extend AST indexing from Python-only TypeScript/Go via standardize index output formats nodes edges stubs across languages release core logic modules as standalone library pyproject.toml pip install darago-core
    is_path_safe,
    normalize_repo_path,
    # Context retrieval (DA-RAG-B adapter) from context_engine.py
    "HAS_DARAG",
    "RetrievalResult",
    "get_or_build_repo_corpus",
    "retrieve_darag_context",
]

__version__: str = "1.0.0"
