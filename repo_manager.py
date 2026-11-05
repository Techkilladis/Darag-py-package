"""
Repository Inspector, AST Symbol Explorer, and Security Boundary Manager.
Handles repository discovery, security boundary enforcement, AST stub extraction,
and test configuration detection for generic Python repositories.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Any, Tuple


# Security: Directories that must NEVER be indexed or read
BLOCKED_DIR_NAMES = {
    ".git", ".svn", ".hg",
    ".venv", "venv", "env", ".env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", ".eggs", "build", "dist",
    "node_modules", ".idea", ".vscode"
}

# Security: File patterns containing secrets or credentials
BLOCKED_FILE_PATTERNS = [
    r"^\.env.*",
    r".*secret.*",
    r".*credential.*",
    r".*\.pem$",
    r".*\.key$",
    r"^id_rsa.*",
    r"^id_ed25519.*",
    r".*\.p12$",
    r".*\.pfx$",
]

# Max file size to index (prevent memory exhaustion on huge generated files or bundles)
MAX_INDEX_FILE_BYTES = 2_000_000  # 2 MB


def normalize_repo_path(raw_path: str) -> str:
    """
    Normalizes a user-entered path string into a valid POSIX path (handling Windows drive letters).
    e.g. 'C:\\Users\\...' -> '/mnt/c/Users/...'
    """
    p = raw_path.strip().strip("'").strip('"')
    if not p:
        return ""
    
    # Check Windows drive letter: C:\ or C:/
    drive_match = re.match(r"^([a-zA-Z]):[\\/](.*)", p)
    if drive_match:
        drive = drive_match.group(1).lower()
        rest = drive_match.group(2).replace("\\", "/")
        return f"/mnt/{drive}/{rest}".rstrip("/")
        
    return p.replace("\\", "/").rstrip("/")


def is_path_safe(rel_path: str) -> bool:
    """Verifies that a relative file path does not cross security boundaries or expose secrets."""
    parts = Path(rel_path).parts
    for part in parts:
        if part in BLOCKED_DIR_NAMES:
            return False
            
    filename = Path(rel_path).name
    for pattern in BLOCKED_FILE_PATTERNS:
        if re.match(pattern, filename, re.IGNORECASE):
            return False
            
    return True


class FunctionStubInfo:
    def __init__(
        self,
        name: str,
        qualname: str,
        signature: str,
        docstring: str,
        start_line: int,
        end_line: int,
        body_start_line: int,
        is_stub: bool,
        stub_type: str,  # 'not_implemented', 'pass', 'ellipsis', 'implemented'
        is_method: bool = False,
        class_name: Optional[str] = None
    ):
        self.name = name
        self.qualname = qualname
        self.signature = signature
        self.docstring = docstring
        self.start_line = start_line
        self.end_line = end_line
        self.body_start_line = body_start_line
        self.is_stub = is_stub
        self.stub_type = stub_type
        self.is_method = is_method
        self.class_name = class_name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "qualname": self.qualname,
            "signature": self.signature,
            "docstring": self.docstring,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "body_start_line": self.body_start_line,
            "is_stub": self.is_stub,
            "stub_type": self.stub_type,
            "is_method": self.is_method,
            "class_name": self.class_name
        }


class RepoMetadata:
    def __init__(
        self,
        root_path: str,
        project_name: str,
        language: str,
        source_files: List[str],
        test_files: List[str],
        key_files: Dict[str, str],
        test_framework: str,
        proposed_test_cmd: str,
        tree_preview: List[str],
        module_roots: List[str],
        error: Optional[str] = None
    ):
        self.root_path = root_path
        self.project_name = project_name
        self.language = language
        self.source_files = source_files
        self.test_files = test_files
        self.key_files = key_files
        self.test_framework = test_framework
        self.proposed_test_cmd = proposed_test_cmd
        self.tree_preview = tree_preview
        self.module_roots = module_roots
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_path": self.root_path,
            "project_name": self.project_name,
            "language": self.language,
            "n_source_files": len(self.source_files),
            "n_test_files": len(self.test_files),
            "key_files": self.key_files,
            "test_framework": self.test_framework,
            "proposed_test_cmd": self.proposed_test_cmd,
            "module_roots": self.module_roots,
            "error": self.error
        }


def inspect_repository(raw_path: str) -> RepoMetadata:
    """
    Validates and inspects a repository folder without executing any code or installing dependencies.
    Discovers source files, test configuration, project structure, and key metadata.
    """
    posix_path = normalize_repo_path(raw_path)
    if not posix_path:
        return RepoMetadata("", "", "Unknown", [], [], {}, "None", "", [], [], error="Repository path is empty.")
        
    p = Path(posix_path)
    if not p.exists():
        return RepoMetadata(posix_path, p.name, "Unknown", [], [], {}, "None", "", [], [],
                            error=f"Path does not exist: {posix_path}")
    if not p.is_dir():
        return RepoMetadata(posix_path, p.name, "Unknown", [], [], {}, "None", "", [], [],
                            error=f"Path is not a directory: {posix_path}")

    project_name = p.name
    source_files: List[str] = []
    test_files: List[str] = []
    key_files: Dict[str, str] = {}
    tree_preview: List[str] = []

    # Important project files to look for
    candidate_key_files = [
        "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
        "README.md", "README.rst", "pytest.ini", "tox.ini", "Pipfile"
    ]
    for kf in candidate_key_files:
        kf_path = p / kf
        if kf_path.is_file():
            key_files[kf] = str(kf_path.stat().st_size)

    # Detect module roots
    module_roots: List[str] = []
    if (p / "src").is_dir():
        module_roots.append("src")
    # Check top-level directories with __init__.py
    for child in p.iterdir():
        if child.is_dir() and child.name not in BLOCKED_DIR_NAMES:
            if (child / "__init__.py").exists():
                module_roots.append(child.name)
    module_roots.append("")  # root itself fallback

    # Walk directory respecting safety rules
    for root, dirs, files in os.walk(p):
        # Prune blocked directories in-place
        dirs[:] = [d for d in dirs if d not in BLOCKED_DIR_NAMES and not d.endswith(".egg-info")]
        
        rel_root = Path(root).relative_to(p)
        for f in sorted(files):
            rel_file = str((rel_root / f).as_posix()) if str(rel_root) != "." else f
            if not is_path_safe(rel_file):
                continue

            full_f = Path(root) / f
            try:
                size = full_f.stat().st_size
                if size > MAX_INDEX_FILE_BYTES:
                    continue
            except Exception:
                continue

            if f.endswith(".py"):
                is_test = (
                    "tests" in rel_file.split("/")
                    or "test" in rel_file.split("/")
                    or f.startswith("test_")
                    or f.endswith("_test.py")
                )
                if is_test:
                    test_files.append(rel_file)
                else:
                    source_files.append(rel_file)

            if len(tree_preview) < 40:
                tree_preview.append(rel_file)

    # Detect test framework
    test_framework = "pytest"
    if (p / "pytest.ini").exists() or (p / "conftest.py").exists() or (p / "tests").is_dir() or test_files:
        test_framework = "pytest"
    elif "setup.py" in key_files:
        test_framework = "pytest / setuptools"
    else:
        test_framework = "unittest"

    # Propose test command
    proposed_cmd = ""
    if test_files:
        # Prefer a clean test file
        first_test = test_files[0]
        proposed_cmd = f"pytest {first_test} -v"
    elif (p / "tests").is_dir():
        proposed_cmd = "pytest tests/ -v"
    else:
        proposed_cmd = "pytest -v"

    return RepoMetadata(
        root_path=posix_path,
        project_name=project_name,
        language="Python",
        source_files=sorted(source_files),
        test_files=sorted(test_files),
        key_files=key_files,
        test_framework=test_framework,
        proposed_test_cmd=proposed_cmd,
        tree_preview=tree_preview,
        module_roots=module_roots,
        error=None
    )


def extract_functions_from_file(repo_root: str, rel_path: str) -> List[FunctionStubInfo]:
    """
    Parses a python source file using AST and extracts all functions/methods,
    identifying whether they are unimplemented stubs (raise NotImplementedError, pass, ...)
    """
    norm_root = normalize_repo_path(repo_root) if repo_root else ""
    full_path = Path(norm_root) / rel_path
    if not full_path.exists() or not full_path.is_file():
        full_path = Path(repo_root) / rel_path
        if not full_path.exists() or not full_path.is_file():
            return []

    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content)
    except Exception:
        return []

    lines = content.splitlines()
    results: List[FunctionStubInfo] = []

    def check_stub(body: List[ast.stmt]) -> Tuple[bool, str]:
        if not body:
            return True, "empty"
        # Skip docstring if first statement
        stmts = body
        if isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), (ast.Str, ast.Constant)):
            stmts = body[1:]
        if not stmts:
            return True, "docstring_only"
        first = stmts[0]
        if isinstance(first, ast.Pass):
            return True, "pass"
        if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) and first.value.value is Ellipsis:
            return True, "ellipsis"
        if isinstance(first, ast.Raise):
            if isinstance(first.exc, ast.Name) and first.exc.id == "NotImplementedError":
                return True, "not_implemented"
            if isinstance(first.exc, ast.Call) and isinstance(first.exc.func, ast.Name) and first.exc.func.id == "NotImplementedError":
                return True, "not_implemented"
        return False, "implemented"

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            body_start = node.body[0].lineno if node.body else start + 1
            sig_line = lines[start - 1].strip()
            doc = ast.get_docstring(node) or ""
            is_stub, stub_type = check_stub(node.body)
            results.append(FunctionStubInfo(
                name=node.name,
                qualname=node.name,
                signature=sig_line,
                docstring=doc,
                start_line=start,
                end_line=end,
                body_start_line=body_start,
                is_stub=is_stub,
                stub_type=stub_type,
                is_method=False
            ))
        elif isinstance(node, ast.ClassDef):
            cls_name = node.name
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = sub.lineno
                    end = getattr(sub, "end_lineno", start)
                    body_start = sub.body[0].lineno if sub.body else start + 1
                    sig_line = lines[start - 1].strip()
                    doc = ast.get_docstring(sub) or ""
                    is_stub, stub_type = check_stub(sub.body)
                    results.append(FunctionStubInfo(
                        name=sub.name,
                        qualname=f"{cls_name}.{sub.name}",
                        signature=sig_line,
                        docstring=doc,
                        start_line=start,
                        end_line=end,
                        body_start_line=body_start,
                        is_stub=is_stub,
                        stub_type=stub_type,
                        is_method=True,
                        class_name=cls_name
                    ))

    return results


def find_matching_test_file(repo_root: str, target_file_rel: str) -> Optional[str]:
    """Finds the most likely test file for a target source file in the repository."""
    norm_root = normalize_repo_path(repo_root) if repo_root else ""
    root = Path(norm_root) if norm_root and Path(norm_root).exists() else Path(repo_root)
    p_target = Path(target_file_rel)
    stem = p_target.stem
    candidates = [
        f"tests/test_{stem}.py",
        f"test/test_{stem}.py",
        f"tests/{stem}_test.py",
        f"test/{stem}_test.py",
        f"test_{stem}.py",
    ]
    for c in candidates:
        if (root / c).exists():
            return c
            
    # Search in all test files for matching stem
    meta = inspect_repository(str(root))
    for tf in meta.test_files:
        if stem in Path(tf).name:
            return tf
            
    return meta.test_files[0] if meta.test_files else None
