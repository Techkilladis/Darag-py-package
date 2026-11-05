"""Go AST indexer implementing the BaseIndexer interface.

Uses the Go toolchain (``go/ast``) via ``go vet`` or inline Go program to
generate JSON-compliant AST stubs compatible with the unified
``RepositoryIndexOutput`` format.

If the Go toolchain is not available, the indexer falls back to a
pure-Python regex-based parser that handles common Go patterns
(functions, methods, structs, interfaces, type aliases).

Usage::

    from darago_core.indexers.go_indexer import GoIndexer

    indexer = GoIndexer()
    output = indexer.scan("/path/to/go-project")
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any

from ..indexer_base import (
    BaseIndexer,
    RepoSymbol,
    CallEdge,
    CodeStub,
    RepositoryIndexOutput,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GO_EXTENSIONS = [".go"]
_EXCLUDE_DIRS = {
    'node_modules', '.git', '__pycache__', 'dist', 'build', 'coverage',
    '.next', '.nuxt', '.turbo', 'vendor', 'third_party',
}


class GoIndexer(BaseIndexer):
    """AST indexer for Go source files.

    Tries to use the Go toolchain via an inline AST-extraction program for
    accurate parsing.  Falls back to regex-based extraction if ``go`` is
    unavailable.
    """

    @property
    def language(self) -> str:
        return "go"

    def supported_extensions(self) -> List[str]:
        return list(_GO_EXTENSIONS)

    # ── Public API ────────────────────────────────────────────────────────

    def index_file(self, file_path: str, content: str) -> List[RepoSymbol]:
        """Parse a single Go file and return its indexed symbols."""
        # Try accurate AST-based parsing first (Go toolchain)
        symbols = self._parse_with_go_ast(content, file_path)
        if symbols:
            return symbols
        # Fallback to regex-based extraction
        return self._parse_regex(content, file_path)

    def scan(self, repository_path: str) -> RepositoryIndexOutput:
        """Scan a Go repository and return a ``RepositoryIndexOutput``."""
        p = self._resolve_repo_path(repository_path)
        if not p.exists() or not p.is_dir():
            return RepositoryIndexOutput(name=p.name, language="go")

        repo_name = p.name
        nodes: List[RepoSymbol] = []
        stubs: List[CodeStub] = []
        files: Dict[str, str] = {}

        for root, dirs, flist in os.walk(p):
            dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS and not self._is_excluded_dir(d)]
            for f in flist:
                ext = Path(f).suffix.lower()
                if ext not in _GO_EXTENSIONS:
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

        for sym in nodes:
            stubs.append(CodeStub(
                symbol_name=sym.name,
                file_path=sym.file_path,
                signature=sym.signature,
                docstring=sym.docstring,
            ))

        return RepositoryIndexOutput(
            name=repo_name,
            language="go",
            nodes=nodes,
            edges=edges,
            stubs=stubs,
            files=files,
            metadata={"source_files": len(files)},
        )

    # ── Go toolchain AST parsing ──────────────────────────────────────────

    def _parse_with_go_ast(self, content: str, file_path: str) -> List[RepoSymbol]:
        """Parse using Go's ``go/ast`` package via inline Go program.

        Returns a list of RepoSymbol objects, or an empty list if the Go
        toolchain is not available or parsing fails.
        """
        if not self._has_go():
            return []

        # Write content to a temp file so the Go program can parse it
        tmp_file = self._write_temp_go_file(content)
        try:
            result = subprocess.run(
                ["go", "run", "-"],
                input=_GO_AST_SCRIPT,
                capture_output=True,
                text=True,
                timeout=15,
                env={**os.environ, "_GO_SRC": tmp_file},
            )
            if result.returncode != 0:
                return []
            raw_symbols = json.loads(result.stdout)
            return self._go_ast_to_symbols(raw_symbols, file_path)
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            return []
        finally:
            self._safe_unlink(tmp_file)

    @staticmethod
    def _has_go() -> bool:
        """Check if the Go toolchain is available in the environment."""
        try:
            result = subprocess.run(
                ["go", "version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    @staticmethod
    def _write_temp_go_file(content: str) -> str:
        """Write content to a temporary .go file for parsing."""
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".go", prefix="go_src_")
        try:
            os.write(fd, content.encode('utf-8'))
        finally:
            os.close(fd)
        return path

    @staticmethod
    def _safe_unlink(path: str):
        """Safely delete a temporary file."""
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass

    @staticmethod
    def _go_ast_to_symbols(raw: List[Dict[str, Any]], file_path: str) -> List[RepoSymbol]:
        """Convert Go AST JSON output to RepoSymbol objects."""
        symbols: List[RepoSymbol] = []
        for item in raw:
            kind = item.get("kind", "function")
            kind_map = {
                "func": "function",
                "method": "method",
                "struct": "class",
                "interface": "interface",
                "type": "type",
                "const": "constant",
                "var": "constant",
                "go": "function",  # go statements (goroutines)
            }
            sym_kind = kind_map.get(kind, "function")

            signature = item.get("signature", "")
            docstring = item.get("docstring", "")
            code_snippet = item.get("code_snippet", signature)
            calls = item.get("calls", [])

            sym = RepoSymbol(
                name=item.get("name", "unknown"),
                kind=sym_kind,
                file_path=file_path,
                signature=signature,
                docstring=docstring,
                code_snippet=code_snippet,
                start_line=item.get("start_line", 0),
                end_line=item.get("end_line", 0),
                calls=calls,
            )
            symbols.append(sym)
        return symbols

    # ── Regex-based fallback parsing ──────────────────────────────────────

    def _parse_regex(self, content: str, file_path: str) -> List[RepoSymbol]:
        """Fallback regex-based extraction for Go source files.

        Handles common Go patterns:
        - ``func name(params) ...``
        - ``func (r *Receiver) name(params) ...``  (methods with receivers)
        - ``type Name struct { ... }``
        - ``type Name interface { ... }``
        - ``const ( ... )`` and ``var ( ... )``
        - ``type Name = ...`` (type aliases)
        """
        symbols: List[RepoSymbol] = []
        lines = content.splitlines()

        # Pattern 1: Regular functions (not methods)
        func_re = re.compile(
            r'^\s*func\s+'                          # func keyword
            r'([^\s(]+)'                             # function name
            r'\(([^)]*)\)'                           # parameters
            r'(?:\s*\(([^)]*)\))?'                   # return type (optional)
            r'\s*\{'                                 # opening brace
        )

        # Pattern 2: Methods with receivers
        method_re = re.compile(
            r'^\s*func\s+\(([^)]+)\)\s+'            # receiver
            r'([^\s(]+)'                             # method name
            r'\(([^)]*)\)'                           # parameters
            r'(?:\s*\(([^)]*)\))?'                   # return type (optional)
            r'\s*\{'                                 # opening brace
        )

        # Pattern 3: Struct types
        struct_re = re.compile(
            r'^\s*type\s+'                           # type keyword
            r'([^\s]+)'                              # type name
            r'\s+struct\s+'                          # struct keyword
            r'\{'                                    # opening brace
        )

        # Pattern 4: Interface types
        interface_re = re.compile(
            r'^\s*type\s+'                           # type keyword
            r'([^\s]+)'                              # type name
            r'\s+interface\s+'                       # interface keyword
            r'(?:\s*\{)?'                            # optional opening brace
        )

        # Pattern 5: Type aliases
        type_alias_re = re.compile(
            r'^\s*type\s+'                           # type keyword
            r'([^\s]+)'                              # type name
            r'\s+=\s+'                               # assignment
        )

        # Pattern 6: Constants and variables
        const_var_re = re.compile(
            r'^(?:const|var)\s+([A-Za-z_][\w]*)'    # kind + name
        )

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # Skip comments and blank lines
            if stripped.startswith('//') or not stripped:
                i += 1
                continue

            start_line = i + 1  # 1-based line number

            # Try method pattern first (has receiver)
            m = method_re.match(line)
            if m:
                receiver = m.group(1)
                name = m.group(2)
                params = m.group(3)
                ret_type = m.group(4) or ""
                signature = f"{name}({params})"
                if ret_type:
                    signature += f" -> {ret_type}"
                docstring = self._extract_docstring(lines, i)
                code_snippet = signature
                calls = self._extract_calls_from_code(content, "go")

                sym = RepoSymbol(
                    name=name,
                    kind="method",
                    file_path=file_path,
                    signature=signature,
                    docstring=docstring,
                    code_snippet=code_snippet,
                    start_line=start_line,
                    end_line=self._find_block_end(lines, i),
                    calls=calls,
                )
                symbols.append(sym)
                i = self._find_block_end(lines, i) + 1
                continue

            # Try regular function pattern
            m = func_re.match(line)
            if m:
                name = m.group(1)
                params = m.group(2)
                ret_type = m.group(3) or ""
                signature = f"{name}({params})"
                if ret_type:
                    signature += f" -> {ret_type}"
                docstring = self._extract_docstring(lines, i)
                code_snippet = signature
                calls = self._extract_calls_from_code(content, "go")

                sym = RepoSymbol(
                    name=name,
                    kind="function",
                    file_path=file_path,
                    signature=signature,
                    docstring=docstring,
                    code_snippet=code_snippet,
                    start_line=start_line,
                    end_line=self._find_block_end(lines, i),
                    calls=calls,
                )
                symbols.append(sym)
                i = self._find_block_end(lines, i) + 1
                continue

            # Try struct pattern
            m = struct_re.match(line)
            if m:
                name = m.group(1)
                signature = f"type {name} struct {{ ... }}"
                docstring = self._extract_docstring(lines, i)
                code_snippet = signature

                sym = RepoSymbol(
                    name=name,
                    kind="class",
                    file_path=file_path,
                    signature=signature,
                    docstring=docstring,
                    code_snippet=code_snippet,
                    start_line=start_line,
                    end_line=self._find_block_end(lines, i),
                )
                symbols.append(sym)
                i = self._find_block_end(lines, i) + 1
                continue

            # Try interface pattern
            m = interface_re.match(line)
            if m:
                name = m.group(1)
                signature = f"type {name} interface {{ ... }}"
                docstring = self._extract_docstring(lines, i)
                code_snippet = signature

                sym = RepoSymbol(
                    name=name,
                    kind="interface",
                    file_path=file_path,
                    signature=signature,
                    docstring=docstring,
                    code_snippet=code_snippet,
                    start_line=start_line,
                    end_line=self._find_block_end(lines, i),
                )
                symbols.append(sym)
                i += 1
                continue

            # Try type alias pattern
            m = type_alias_re.match(line)
            if m:
                name = m.group(1)
                signature = f"type {name} = ..."
                docstring = self._extract_docstring(lines, i)

                sym = RepoSymbol(
                    name=name,
                    kind="type",
                    file_path=file_path,
                    signature=signature,
                    docstring=docstring,
                    code_snippet=signature,
                    start_line=start_line,
                    end_line=start_line,
                )
                symbols.append(sym)
                i += 1
                continue

            # Try const/var pattern (at top level)
            if stripped.startswith('const') or stripped.startswith('var'):
                for inner in const_var_re.finditer(stripped):
                    name = inner.group(1)
                    signature = f"{stripped.split(';')[0].strip()}"
                    docstring = self._extract_docstring(lines, i)

                    sym = RepoSymbol(
                        name=name,
                        kind="constant",
                        file_path=file_path,
                        signature=signature,
                        docstring=docstring,
                        code_snippet=signature,
                        start_line=start_line,
                        end_line=start_line,
                    )
                    symbols.append(sym)
                    break

            i += 1

        return symbols

    @staticmethod
    def _extract_docstring(lines: List[str], idx: int) -> str:
        """Extract Go-style doc comment (// or /* */) preceding a declaration."""
        # Look backwards for consecutive comment lines
        doc_lines = []
        j = idx - 1
        while j >= 0:
            stripped = lines[j].strip()
            if stripped.startswith('//'):
                doc_lines.insert(0, stripped[2:].strip())
                j -= 1
            elif stripped.startswith('/*') or stripped.endswith('*/'):
                doc_lines.insert(0, stripped.strip('/*').strip())
                j -= 1
            else:
                break
        return '\n'.join(doc_lines) if doc_lines else ""

    @staticmethod
    def _find_block_end(lines: List[str], start: int) -> int:
        """Find the closing brace of a block starting at *start*."""
        brace_count = 0
        found_open = False
        for i in range(start, len(lines)):
            for ch in lines[i]:
                if ch == '{':
                    brace_count += 1
                    found_open = True
                elif ch == '}':
                    brace_count -= 1
            if found_open and brace_count <= 0:
                return i
        return len(lines) - 1

    @staticmethod
    def _extract_calls_from_code(content: str, language: str) -> List[str]:
        """Extract function call names from Go source code using regex."""
        # Go function calls: identifier(
        call_pattern = re.compile(r'\b([a-zA-Z_][\w]*)\s*\(')
        # Exclude Go keywords
        keywords = {'if', 'else', 'for', 'range', 'switch', 'case', 'type', 'return', 'defer', 'go', 'select', 'chan', 'map', 'struct', 'interface', 'const', 'var', 'package', 'import'}
        calls = set()
        for match in call_pattern.finditer(content):
            name = match.group(1)
            if name not in keywords:
                calls.add(name)
        return list(calls)


# ---------------------------------------------------------------------------
# Inline Go program that uses go/ast to extract symbols as JSON
# ---------------------------------------------------------------------------

_GO_AST_SCRIPT = r'''
//go:build ignore
package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
)

type symbolJSON struct {
	Name         string `json:"name"`
	Kind         string `json:"kind"`
	Signature    string `json:"signature"`
	Docstring    string `json:"docstring"`
	CodeSnippet  string `json:"code_snippet"`
	StartLine    int    `json:"start_line"`
	EndLine      int    `json:"end_line"`
	Calls        []string `json:"calls,omitempty"`
}

func main() {
	srcPath := os.Getenv("_GO_SRC")
	if srcPath == "" {
		fmt.Fprintln(os.Stderr, "missing _GO_SRC env")
		os.Exit(1)
	}

	fset := token.NewFileSet()
	node, err := parser.ParseFile(fset, srcPath, nil, parser.ParseComments)
	if err != nil {
		fmt.Fprintln(os.Stderr, "parse error:", err)
		os.Exit(1)
	}

	var symbols []symbolJSON

	// Extract doc comments
	getDoc := func(docs *ast.CommentGroup) string {
		if docs == nil {
			return ""
		}
		return docs.Text()
	}

	// Visit the AST
	ast.Inspect(node, func(n ast.Node) bool {
		switch x := n.(type) {
		case *ast.FuncDecl:
			name := x.Name.Name
			kind := "function"
			sig := name + formatSig(x.Type)
			if x.Recv != nil {
				kind = "method"
			}
			sym := symbolJSON{
				Name:        name,
				Kind:        kind,
				Signature:   sig,
				Docstring:   getDoc(x.Doc),
				CodeSnippet: sig,
				StartLine:   int(fset.Position(x.Pos()).Line),
				EndLine:     int(fset.Position(x.End()).Line),
			}
			// Extract calls within the function body
			if x.Body != nil {
				calls := extractCalls(x.Body)
				sym.Calls = calls
			}
			symbols = append(symbols, sym)

		case *ast.TypeSpec:
			name := x.Name.Name
			kind := "type"
			sig := "type " + name

			switch t := x.Type.(type) {
			case *ast.StructType:
				kind = "class"
				sig = "type " + name + " struct { ... }"
			case *ast.InterfaceType:
				kind = "interface"
				sig = "type " + name + " interface { ... }"
			default:
				sig = "type " + name + " = ..."
			}

			symbols = append(symbols, symbolJSON{
				Name:        name,
				Kind:        kind,
				Signature:   sig,
				Docstring:   getDoc(x.Doc),
				CodeSnippet: sig,
				StartLine:   int(fset.Position(x.Pos()).Line),
				EndLine:     int(fset.Position(x.End()).Line),
			})
		}
		return true
	})

	// Extract top-level const and var declarations
	for _, decl := range node.Decls {
		if gen, ok := decl.(*ast.GenDecl); ok && (gen.Tok == token.CONST || gen.Tok == token.VAR) {
			for _, spec := range gen.Specs {
				if valueSpec, ok := spec.(*ast.ValueSpec); ok {
					for _, name := range valueSpec.Names {
						symbols = append(symbols, symbolJSON{
							Name:        name.Name,
							Kind:        "constant",
							Signature:   valueSpec.Type != nil ? fmt.Sprintf("%s %v", name.Name, valueSpec.Type) : name.Name,
							Docstring:   getDoc(valueSpec.Doc),
							CodeSnippet: valueSpec.Values != nil ? name.Name + " = ..." : name.Name,
							StartLine:   int(fset.Position(name.Pos()).Line),
							EndLine:     int(fset.Position(name.End()).Line),
						})
					}
				}
			}
		}
	}

	enc := json.NewEncoder(os.Stdout)
	enc.Encode(symbols)
}

func formatSig(ft *ast.FuncType) string {
	params := []string{}
	for _, param := range ft.Params.List {
		params = append(params, fmt.Sprintf("%s %s", param.Names[0].Name, formatType(param.Type)))
	}
	ret := ""
	if ft.Results != nil && len(ft.Results.List) > 0 {
		results := []string{}
		for _, res := range ft.Results.List {
			results = append(results, formatType(res.Type))
		}
		ret = " -> " + joinStrings(results, ", ")
	}
	return "(" + joinStrings(params, ", ") + ")" + ret
}

func formatType(t ast.Node) string {
	switch x := t.(type) {
	case *ast.Ident:
		return x.Name
	case *ast.StarExpr:
		return "*" + formatType(x.X)
	case *ast.ArrayType:
		return "[]" + formatType(x.Elt)
	default:
		return "<unknown>"
	}
}

func extractCalls(body *ast.BlockStmt) []string {
	calls := []string{}
	ast.Inspect(body, func(n ast.Node) bool {
		if callExpr, ok := n.(*ast.CallExpr); ok {
			if sel, ok := callExpr.Fun.(*ast.SelectorExpr); ok {
				if ident, ok := sel.X.(*ast.Ident); ok {
					calls = append(calls, ident.Name+"."+sel.Sel.Name)
				}
			} else if ident, ok := callExpr.Fun.(*ast.Ident); ok {
				calls = append(calls, ident.Name)
			}
		}
		return true
	})
	return calls
}

func joinStrings(ss []string, sep string) string {
	result := ""
	for i, s := range ss {
		if i > 0 {
			result += sep
		}
		result += s
	}
	return result
}
'''
