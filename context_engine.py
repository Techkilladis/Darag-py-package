"""
Generic Repository Adapter for DA-RAG-B Context Retrieval.
Operates the genuine DA-RAG-B dependency graph retrieval & budget packing pipeline
(code_index, dependency_graph, retrieval, prompting) on any local Python repository.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

# Try importing official DA-RAG research package
try:
    from darag.code_index import index_repository, CodeUnit, RepositoryIndex
    from darag.dependency_graph import build_graph, make_task_view, DependencyGraph, TaskView
    from darag.retrieval import ProjectCorpus, method_spec, retrieve, RankedContext, Candidate
    from darag.prompting import pack_budget, whitespace_token_counter, build_messages, PackedContext
    HAS_DARAG = True
except ImportError:
    HAS_DARAG = False

from .repo_manager import inspect_repository, RepoMetadata, normalize_repo_path


class RetrievalResult:
    def __init__(
        self,
        method: str,
        retrieved_files: List[str],
        retrieved_symbols: List[str],
        ranked_items: List[Dict[str, Any]],
        packed_items: List[Dict[str, Any]],
        omitted_items: List[Dict[str, Any]],
        packed_context_text: str,
        context_tokens: int,
        token_budget: int,
        remaining_budget: int,
        latency_s: float,
        graph_stats: Dict[str, Any],
        is_generic_adapter: bool = False,
        error: Optional[str] = None
    ):
        self.method = method
        self.retrieved_files = retrieved_files
        self.retrieved_symbols = retrieved_symbols
        self.ranked_items = ranked_items
        self.packed_items = packed_items
        self.omitted_items = omitted_items
        self.packed_context_text = packed_context_text
        self.context_tokens = context_tokens
        self.token_budget = token_budget
        self.remaining_budget = remaining_budget
        self.latency_s = latency_s
        self.graph_stats = graph_stats
        self.is_generic_adapter = is_generic_adapter
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "n_retrieved_files": len(self.retrieved_files),
            "n_retrieved_symbols": len(self.retrieved_symbols),
            "n_packed_items": len(self.packed_items),
            "n_omitted_items": len(self.omitted_items),
            "context_tokens": self.context_tokens,
            "token_budget": self.token_budget,
            "remaining_budget": self.remaining_budget,
            "latency_s": round(self.latency_s, 3),
            "error": self.error
        }


# In-memory index cache so switching between functions in the same repo is instantaneous
_CACHED_REPO_PATH: Optional[str] = None
_CACHED_INDEX: Optional[Any] = None
_CACHED_GRAPH: Optional[Any] = None
_CACHED_CORPUS: Optional[Any] = None


def get_or_build_repo_corpus(repo_root: str) -> Tuple[Optional[Any], Optional[Any], Optional[Any], Optional[str]]:
    """
    Builds or retrieves cached RepositoryIndex, DependencyGraph, and ProjectCorpus for repo_root.
    """
    global _CACHED_REPO_PATH, _CACHED_INDEX, _CACHED_GRAPH, _CACHED_CORPUS
    
    posix_path = normalize_repo_path(repo_root)
    if _CACHED_REPO_PATH == posix_path and _CACHED_CORPUS is not None:
        return _CACHED_INDEX, _CACHED_GRAPH, _CACHED_CORPUS, None

    if not HAS_DARAG:
        return None, None, None, "DA-RAG research package ('darag') is not installed in the current environment."

    p = Path(posix_path)
    if not p.exists() or not p.is_dir():
        return None, None, None, f"Repository directory does not exist: {posix_path}"

    meta = inspect_repository(posix_path)
    if meta.error:
        return None, None, None, meta.error

    try:
        # Index the repository using DA-RAG's official code_index
        index_cfg = {
            "exclude_dir_names": [
                ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
                ".tox", "build", "dist", "node_modules", ".idea", ".vscode"
            ],
            "test_dir_names": ["tests", "test"],
            "test_file_globs": ["test_*.py", "*_test.py"],
            "toplevel_test_dir_names": ["tests", "test"],
            "exclude_test_files": True,
            "max_file_bytes": 2_000_000
        }

        # Parent and project folder name
        parent_dir = p.parent
        project_name = p.name

        idx = index_repository(
            source_code_root=parent_dir,
            project_path=project_name,
            module_roots=meta.module_roots,
            index_cfg=index_cfg,
            include_tests=False
        )

        if not idx.units:
            return None, None, None, f"No retrievable Python code units discovered in {posix_path}."

        # Build AST dependency graph
        graph = build_graph(idx, parent_dir)

        # Build BM25 / lexical corpus
        corpus = ProjectCorpus(idx, graph)

        _CACHED_REPO_PATH = posix_path
        _CACHED_INDEX = idx
        _CACHED_GRAPH = graph
        _CACHED_CORPUS = corpus

        return idx, graph, corpus, None
    except Exception as e:
        return None, None, None, f"Failed to build repository dependency graph: {str(e)}"


def retrieve_darag_context(
    repo_root: str,
    target_file_rel: str,
    func_name: str,
    docstring_or_spec: str,
    target_signature: Optional[str] = None,
    budget_tokens: int = 2048,
    k: int = 10,
    hops: int = 2,
    lambda_weight: float = 0.5
) -> RetrievalResult:
    """
    Executes DA-RAG-B context retrieval and budget packing for the target function in the given repository.
    Masks the target function's implementation body in TaskView to prevent leakage.
    """
    t0 = time.time()
    idx, graph, corpus, err = get_or_build_repo_corpus(repo_root)
    if err or corpus is None or idx is None or graph is None:
        return RetrievalResult(
            method="darag_b",
            retrieved_files=[],
            retrieved_symbols=[],
            ranked_items=[],
            packed_items=[],
            omitted_items=[],
            packed_context_text="",
            context_tokens=0,
            token_budget=budget_tokens,
            remaining_budget=budget_tokens,
            latency_s=time.time() - t0,
            graph_stats={},
            error=err or "Corpus unavailable"
        )

    # 1. Locate target unit if it exists in the index to prevent body leakage
    clean_target_rel = target_file_rel.replace("\\", "/").lstrip("/")
    target_units = [
        u for u in idx.units
        if u.rel_path == clean_target_rel and (u.qualname == func_name or u.qualname.endswith(f".{func_name}"))
    ]

    target_def_line = 1
    body_span = (1, 1)
    target_body = ""

    if target_units:
        tu = target_units[0]
        target_def_line = tu.def_line
        b_start = tu.body_start_line or tu.start_line
        body_span = (b_start, tu.end_line)
        target_body = tu.source

    # 2. Construct TaskView masking the target implementation
    try:
        view = make_task_view(
            graph=graph,
            index=idx,
            rel_file=clean_target_rel,
            def_line=target_def_line,
            body_span=body_span,
            target_body_text=target_body
        )
    except Exception as e:
        # Fallback view with dummy mask
        view = make_task_view(
            graph=graph,
            index=idx,
            rel_file=clean_target_rel,
            def_line=1,
            body_span=(1, 1),
            target_body_text=""
        )

    # 3. Configure DA-RAG-B retrieval method spec
    query_text = f"{func_name} {target_signature or ''} {docstring_or_spec}".strip()
    
    spec = method_spec(
        "darag_b",
        {
            "primary_k": k,
            "context_token_budget": budget_tokens,
            "use_anchors": True,
            "lambda": lambda_weight,
            "hops": hops,
            "seed_count": 10
        },
        retrievers=["bm25"]
    )

    # 4. Execute DA-RAG retrieval (RRF -> Seeds -> Multi-hop graph propagation -> Combined score)
    task_id = f"{Path(repo_root).name}.{func_name}"
    ranked: RankedContext = retrieve(
        corpus=corpus,
        view=view,
        task_id=task_id,
        query_text=query_text,
        spec=spec,
        top_n=100
    )

    # 5. Pack under token budget (MCKP Knapsack Packing: full vs stub)
    ranked_tuples = [(c.unit_id, c.score) for c in ranked.candidates]
    packed: PackedContext = pack_budget(
        ranked=ranked_tuples,
        units=idx.by_id,
        k=k,
        budget=budget_tokens,
        count=whitespace_token_counter,
        stub_factor=0.5,
        pool_factor=3
    )

    packed_ids = {it.unit_id for it in packed.items}

    # Format packed items for UI inspection
    packed_ui = []
    retrieved_files = set()
    retrieved_symbols = []

    for it in packed.items:
        u: CodeUnit = idx.by_id[it.unit_id]
        retrieved_files.add(u.rel_path)
        retrieved_symbols.append(u.qualname)

        # Find dependency relations to target or other units
        edges = []
        for e in graph.out_adj.get(u.unit_id, ()):
            edges.append(f"-> {e.type} {e.dst.split('::')[-1]}")
        for e in graph.in_adj.get(u.unit_id, ()):
            edges.append(f"<- {e.type} {e.src.split('::')[-1]}")

        rel_str = ", ".join(edges[:3]) if edges else f"from {u.rel_path}"

        packed_ui.append({
            "unit_id": it.unit_id,
            "symbol": u.qualname,
            "file": u.rel_path,
            "kind": u.kind,
            "mode": it.mode,
            "cost": it.cost,
            "score": round(getattr(it, "utility", 0.0), 3),
            "relation": rel_str,
            "source_snippet": it.text
        })

    # Omitted candidates (top candidates that exceeded budget)
    omitted_ui = []
    for c in ranked.candidates:
        if c.unit_id not in packed_ids:
            u = idx.by_id.get(c.unit_id)
            if u:
                cost = whitespace_token_counter(u.source)
                omitted_ui.append({
                    "unit_id": c.unit_id,
                    "symbol": u.qualname,
                    "file": u.rel_path,
                    "kind": u.kind,
                    "rank": c.rank,
                    "score": round(c.score, 3),
                    "estimated_cost": cost,
                    "reason": "Exceeded remaining token budget"
                })
        if len(omitted_ui) >= 10:
            break

    # Ranked items preview
    ranked_ui = []
    for c in ranked.candidates[:15]:
        u = idx.by_id.get(c.unit_id)
        if u:
            ranked_ui.append({
                "rank": c.rank,
                "symbol": u.qualname,
                "kind": u.kind,
                "file": u.rel_path,
                "score": round(c.score, 3),
                "in_budget": c.unit_id in packed_ids
            })

    # Graph statistics
    graph_stats = {
        "total_units": len(idx.units),
        "total_edges": len(graph.edges),
        "total_files": len(idx.files),
        "retrieved_candidates": len(ranked.candidates),
        "packed_units": len(packed.items),
        "n_seeds": ranked.n_seeds,
        "n_anchors": ranked.n_anchors
    }

    latency = time.time() - t0

    return RetrievalResult(
        method="darag_b",
        retrieved_files=sorted(retrieved_files),
        retrieved_symbols=retrieved_symbols,
        ranked_items=ranked_ui,
        packed_items=packed_ui,
        omitted_items=omitted_ui,
        packed_context_text=packed.text,
        context_tokens=packed.total_tokens,
        token_budget=budget_tokens,
        remaining_budget=max(0, budget_tokens - packed.total_tokens),
        latency_s=latency,
        graph_stats=graph_stats,
        is_generic_adapter=True,
        error=None
    )
