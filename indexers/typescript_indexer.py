"""TypeScript / JavaScript AST indexer implementing the BaseIndexer interface.

Uses ``typescript-estree`` via Node.js subprocess to generate JSON-compliant
AST stubs compatible with the unified ``RepositoryIndexOutput`` format.

If Node.js / ``typescript-estree`` is not available, the indexer falls back
to a pure-Python regex-based parser that handles common TypeScript/JavaScript
patterns (functions, classes, methods, interfaces).

Usage::

    from darago_core.indexers.typescript_indexer import TypeScriptIndexer

    indexer = TypeScriptIndexer()
    output = indexer.scan("/path/to/typescript-project")
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

_TS_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx"]
_EXCLUDE_DIRS = {
    'node_modules', '.git', '__pycache__', 'dist', 'build', 'coverage',
    '.next', '.nuxt', '.turbo',
}


class TypeScriptIndexer(BaseIndexer):
    """AST indexer for TypeScript / JavaScript source files.

    Tries to use ``typescript-estree`` via Node.js for accurate parsing.
    Falls back to regex-based extraction if Node.js is unavailable.
    """

    @property
    def language(self) -> str:
        return "typescript"

    def supported_extensions(self) -> List[str]:
        return list(_TS_EXTENSIONS)

    # ── Public API ────────────────────────────────────────────────────────

    def index_file(self, file_path: str, content: str) -> List[RepoSymbol]:
        """Parse a TypeScript / JavaScript file and return its indexed symbols."""
        # Try accurate AST-based parsing first
        symbols = self._parse_with_node_ess(content, file_path)
        if symbols:
            return symbols
        # Fallback to regex-based extraction
        return self._parse_regex(content, file_path)

    def scan(self, repository_path: str) -> RepositoryIndexOutput:
        """Scan a TypeScript / JavaScript repository."""
        p = self._resolve_repo_path(repository_path)
        if not p.exists() or not p.is_dir():
            return RepositoryIndexOutput(name=p.name, language="typescript")

        repo_name = p.name
        nodes: List[RepoSymbol] = []
        stubs: List[CodeStub] = []
        files: Dict[str, str] = {}

        for root, dirs, flist in os.walk(p):
            dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS and not self._is_excluded_dir(d)]
            for f in flist:
                ext = Path(f).suffix.lower()
                if ext not in _TS_EXTENSIONS:
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
            language="typescript",
            nodes=nodes,
            edges=edges,
            stubs=stubs,
            files=files,
            metadata={"source_files": len(files)},
        )

    # ── Node.js / typescript-estree parsing ───────────────────────────────

    def _parse_with_node_ess(self, content: str, file_path: str) -> List[RepoSymbol]:
        """Parse using typescript-estree via Node.js subprocess.

        Returns a list of RepoSymbol objects, or an empty list if Node.js
        is not available or parsing fails.
        """
        if not self._has_node():
            return []

        script = _NODE_ES_SCRIPT
        payload = json.dumps({"content": content})

        try:
            result = subprocess.run(
                ["node", "-e", script, payload],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return []
            raw_symbols = json.loads(result.stdout)
            return self._ess_to_symbols(raw_symbols, file_path)
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            return []

    @staticmethod
    def _has_node() -> bool:
        """Check if Node.js is available in the environment."""
        try:
            result = subprocess.run(
                ["node", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    @staticmethod
    def _ess_to_symbols(raw: List[Dict[str, Any]], file_path: str) -> List[RepoSymbol]:
        """Convert typescript-estree output to RepoSymbol objects."""
        symbols: List[RepoSymbol] = []
        for item in raw:
            kind = item.get("kind", "function")
            kind_map = {
                "function": "function",
                "class": "class",
                "method": "method",
                "interface": "interface",
                "type": "type",
                "const": "constant",
                "let": "constant",
                "var": "constant",
                "enum": "class",
                "namespace": "class",
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
        """Fallback parser using regex patterns for TypeScript / JavaScript.

        Handles common patterns:
        - Function declarations: function name(...) { ... }
        - Arrow functions: const name = (...) => { ... }
        - Class declarations: class Name { ... }
        - Class methods: method(...) { ... }
        - Interface declarations: interface Name { ... }
        - Type aliases: type Name = ...
        """
        symbols: List[RepoSymbol] = []
        lines = content.splitlines()
        i = 0

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # Skip comments and empty lines
            if stripped.startswith('//') or stripped.startswith('/*') or not stripped:
                i += 1
                continue

            sym = None

            # Pattern: function name(...) { ... }
            m = re.match(r'(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)', stripped)
            if m:
                name = m.group(1)
                params = m.group(2).strip()
                sig = f"function {name}({params})"
                end_line, snippet = self._extract_block(lines, i)
                calls = self._extract_calls_regex(snippet)
                docstring = self._extract_docstring(lines, i)
                sym = RepoSymbol(
                    name=name,
                    kind="function",
                    file_path=file_path,
                    signature=sig,
                    docstring=docstring,
                    code_snippet=snippet.strip(),
                    start_line=i + 1,
                    end_line=end_line,
                    calls=calls,
                )

            # Pattern: const/let/var name = (...) => { ... } or function { ... }
            elif re.match(r'(?:const|let|var)\s+(\w+)\s*=', stripped):
                name = re.match(r'(?:const|let|var)\s+(\w+)', stripped).group(1)
                sig = stripped.rstrip(',')
                end_line, snippet = self._extract_block(lines, i)
                calls = self._extract_calls_regex(snippet)
                docstring = self._extract_docstring(lines, i)
                sym = RepoSymbol(
                    name=name,
                    kind="function",
                    file_path=file_path,
                    signature=sig,
                    docstring=docstring,
                    code_snippet=snippet.strip(),
                    start_line=i + 1,
                    end_line=end_line,
                    calls=calls,
                )

            # Pattern: class Name { ... }
            elif re.match(r'(?:export\s+)?(?:default\s+)?class\s+(\w+)', stripped):
                name = re.match(r'(?:export\s+)?(?:default\s+)?class\s+(\w+)', stripped).group(1)
                sig = stripped.rstrip('{').strip() + '{'
                end_line, snippet = self._extract_block(lines, i)
                calls = self._extract_calls_regex(snippet)
                docstring = self._extract_docstring(lines, i)
                sym = RepoSymbol(
                    name=name,
                    kind="class",
                    file_path=file_path,
                    signature=sig,
                    docstring=docstring,
                    code_snippet=snippet.strip(),
                    start_line=i + 1,
                    end_line=end_line,
                    calls=calls,
                )

            # Pattern: method name(...) { ... } (inside a class)
            elif re.match(r'(?:async\s+)?(\w+)\s*\(([^)]*)\)\s*{', stripped):
                m = re.match(r'(?:async\s+)?(\w+)\s*\(([^)]*)\)\s*{', stripped)
                name = m.group(1)
                params = m.group(2).strip()
                # Skip keywords
                if name in ('if', 'else', 'for', 'while', 'switch', 'catch', 'return', 'new'):
                    i += 1
                    continue
                sig = f"{name}({params})"
                end_line, snippet = self._extract_block(lines, i)
                calls = self._extract_calls_regex(snippet)
                docstring = self._extract_docstring(lines, i)
                sym = RepoSymbol(
                    name=name,
                    kind="method",
                    file_path=file_path,
                    signature=sig,
                    docstring=docstring,
                    code_snippet=snippet.strip(),
                    start_line=i + 1,
                    end_line=end_line,
                    calls=calls,
                )

            # Pattern: interface Name { ... }
            elif re.match(r'(?:export\s+)?interface\s+(\w+)', stripped):
                name = re.match(r'(?:export\s+)?interface\s+(\w+)', stripped).group(1)
                sig = stripped.rstrip('{').strip() + '{'
                end_line, snippet = self._extract_block(lines, i)
                calls = self._extract_calls_regex(snippet)
                docstring = self._extract_docstring(lines, i)
                sym = RepoSymbol(
                    name=name,
                    kind="interface",
                    file_path=file_path,
                    signature=sig,
                    docstring=docstring,
                    code_snippet=snippet.strip(),
                    start_line=i + 1,
                    end_line=end_line,
                    calls=calls,
                )

            # Pattern: type Name = ...
            elif re.match(r'(?:export\s+)?type\s+(\w+)\s*=', stripped):
                name = re.match(r'(?:export\s+)?type\s+(\w+)', stripped).group(1)
                sig = stripped.split('=')[0].strip() + '= ...'
                docstring = self._extract_docstring(lines, i)
                sym = RepoSymbol(
                    name=name,
                    kind="type",
                    file_path=file_path,
                    signature=sig,
                    docstring=docstring,
                    code_snippet=stripped,
                    start_line=i + 1,
                    end_line=i + 1,
                    calls=[],
                )

            if sym:
                symbols.append(sym)

            i += 1

        return symbols

    @staticmethod
    def _extract_block(lines: List[str], start: int) -> tuple:
        """Extract a code block starting at *start* and return (end_line, snippet)."""
        brace_count = 0
        started = False
        end = start
        block_lines = []

        for i in range(start, len(lines)):
            line = lines[i]
            block_lines.append(line)
            for ch in line:
                if ch == '{':
                    brace_count += 1
                    started = True
                elif ch == '}':
                    brace_count -= 1
            if started and brace_count <= 0 and i > start:
                end = i
                break
            elif started and brace_count <= 0 and i == start:
                # Single-line block like: const x = () => {};
                end = i
                break
        else:
            end = len(lines) - 1

        return end, "\n".join(block_lines)

    @staticmethod
    def _extract_docstring(lines: List[str], start: int) -> str:
        """Extract JSDoc-style docstring preceding *start*."""
        # Look backwards for /** ... */ block
        for i in range(start - 1, -1, -1):
            stripped = lines[i].strip()
            if stripped.startswith('/**'):
                doc_lines = []
                for j in range(i, start):
                    s = lines[j].strip()
                    s = re.sub(r'^/\*\*?\s*', '', s)
                    s = re.sub(r'\*/\s*$', '', s)
                    s = re.sub(r'^\*\s*', '', s)
                    if s:
                        doc_lines.append(s)
                return "\n".join(doc_lines)
            elif stripped and not stripped.startswith('//') and not stripped.startswith('*'):
                break
        return ""

    @staticmethod
    def _extract_calls_regex(code: str) -> List[str]:
        """Extract function call names from code using regex."""
        calls = set()
        # Match: name(...) patterns, excluding keywords
        keywords = {'if', 'else', 'for', 'while', 'switch', 'catch', 'return', 'new', 'typeof', 'instanceof', 'void', 'delete'}
        for m in re.finditer(r'\b([a-zA-Z_$][\w$]*)\s*\(', code):
            name = m.group(1)
            if name not in keywords:
                calls.add(name)
        return list(calls)


# ---------------------------------------------------------------------------
# Node.js script for typescript-estree parsing
#
# This inline script is passed to ``node -e`` and receives JSON input with
# a ``content`` field.  It outputs JSON array of symbol objects.
# ---------------------------------------------------------------------------

_NODE_ES_SCRIPT = r"""
const tsEslintParser = require('@typescript-eslint/parser');
const fs = require('fs');

const payload = JSON.parse(fs.readFileSync('/dev/stdin', 'utf8'));
const content = payload.content;

let nodes = [];
try {
    const ast = tsEslintParser.parse(content, {
        ecmaVersion: 2020,
        sourceType: 'module',
        loc: true,
        range: true,
        comment: true,
        tokens: true,
    });

    function extractCalls(body) {
        const calls = new Set();
        for (const node of body) {
            if (!node) continue;
            if (node.type === 'CallExpression') {
                if (node.callee.type === 'Identifier') {
                    calls.add(node.callee.name);
                } else if (node.callee.type === 'MemberExpression') {
                    calls.add(node.callee.property.name || node.callee.property.value);
                }
            }
            if (node.body && Array.isArray(node.body)) {
                for (const sub of node.body) extractCallsBody(sub, calls);
            }
            if (node.consequent) extractCallsBody(node.consequent, calls);
            if (node.alternate) extractCallsBody(node.alternate, calls);
        }
        return [...calls];
    }

    function extractCallsBody(node, calls) {
        if (!node) return;
        if (node.type === 'CallExpression') {
            if (node.callee.type === 'Identifier') calls.add(node.callee.name);
            else if (node.callee.type === 'MemberExpression') {
                calls.add(node.callee.property.name || node.callee.property.value);
            }
        }
        const walkProps = ['body', 'consequent', 'alternate', 'argument', 'object', 'property', 'left', 'right'];
        for (const prop of walkProps) {
            if (Array.isArray(node[prop])) {
                for (const item of node[prop]) extractCallsBody(item, calls);
            } else if (node[prop] && typeof node[prop] === 'object') {
                extractCallsBody(node[prop], calls);
            }
        }
    }

    function getDocComments(node, comments) {
        for (const comment of comments) {
            if (comment.type === 'Block' && comment.value.startsWith('*')) {
                const endLine = comment.loc.end.line;
                if (node && node.loc && node.loc.start.line - endLine <= 2 && node.loc.start.line > endLine) {
                    return comment.value.replace(/^\*\s*/gm, '').replace(/^\*\/\s*$/, '').trim();
                }
            }
        }
        return '';
    }

    const comments = ast.comments || [];

    for (const node of ast.body) {
        if (!node || !node.loc) continue;

        let kind = 'function';
        let name = '';
        let signature = '';
        let calls = [];

        if (node.type === 'FunctionDeclaration') {
            kind = 'function';
            name = node.id ? node.id.name : '<anonymous>';
            const params = (node.params || []).map(p => p.type).join(', ');
            signature = `function ${name}(${params})`;
            calls = extractCalls([node]);
        } else if (node.type === 'ClassDeclaration') {
            kind = 'class';
            name = node.id ? node.id.name : '<anonymous>';
            signature = `class ${name}`;
            calls = extractCalls([node]);
        } else if (node.type === 'InterfaceDeclaration') {
            kind = 'interface';
            name = node.id ? node.id.name : '<anonymous>';
            signature = `interface ${name}`;
            calls = [];
        } else if (node.type === 'TSTypeAliasDeclaration') {
            kind = 'type';
            name = node.id ? node.id.name : '<anonymous>';
            signature = `type ${name} = ...`;
            calls = [];
        } else if (node.type === 'VariableDeclaration') {
            for (const decl of node.declarations) {
                if (decl.id && decl.id.name && decl.init) {
                    name = decl.id.name;
                    kind = typeof decl.init === 'function' || decl.init.type === 'ArrowFunctionExpression' || decl.init.type === 'FunctionExpression' ? 'function' : 'constant';
                    signature = `${node.kind} ${name} = ...`;
                }
            }
        } else if (node.type === 'FunctionExpression' || node.type === 'ArrowFunctionExpression') {
            // Method inside class body
            const parentInfo = node._parentInfo;
            kind = 'method';
            name = node.id ? node.id.name : '<anonymous>';
            const params = (node.params || []).map(p => p.type).join(', ');
            signature = `${name}(${params})`;
            calls = extractCalls([node]);
        }

        if (name && signature) {
            const docstring = getDocComments(node, comments);
            const snippet = content.split('\n').slice(node.loc.start.line - 1, node.loc.end.line).join('\n');
            nodes.push({
                kind,
                name,
                signature,
                docstring,
                code_snippet: snippet,
                start_line: node.loc.start.line,
                end_line: node.loc.end.line,
                calls,
            });
        }
    }

    console.log(JSON.stringify(nodes));
} catch (e) {
    console.log('[]');
}
"""
