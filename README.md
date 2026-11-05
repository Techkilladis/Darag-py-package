# Darag Python Package

<p align="center">
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/Library-Package-00599C?style=for-the-badge&logo=package&logoColor=white" alt="Package"/>
  <img src="https://img.shields.io/badge/License-MIT-007BFF?style=for-the-badge&logo=license&logoColor=white" alt="License"/>
</p>

<p align="center">
  <strong>The core Python library powering the Darag ecosystem.</strong>
</p>

---

## Overview

**Darag-py-package** (`darago_core`) is the heart of the Darag platform. It provides a robust, language-agnostic core library for codebase indexing, context engineering, repository management, and test execution. Designed for modularity and performance, it powers both the Darag CLI and the VS Code Extension.

This package enables developers and AI agents to understand codebases at scale -- parsing multiple languages, building dependency graphs, and generating rich contextual data for intelligent development workflows.

---

## Features

| Feature | Description |
|---------|-------------|
| **Multi-Language Indexing** | Pluggable indexers for Python, TypeScript, and Go with an extensible factory pattern |
| **Context Engine** | Generates rich, scoped context from indexed code data for AI and IDE integrations |
| **Repository Manager** | Handles repository discovery, analysis, and metadata management |
| **Test Runner** | Executes tests, collects results, and integrates with the DAG execution pipeline |
| **Indexer Factory** | Language-agnostic factory pattern for registering and selecting indexers dynamically |
| **Performance-First Design** | Optimized for large codebases with caching and incremental indexing support |

---

## Project Structure

```
darago_core/
├── __init__.py
├── context_engine.py         # Context generation and enrichment engine
├── indexer_base.py           # Abstract base class for all indexers
├── indexer_factory.py        # Factory for language-specific indexer registration
├── repo_manager.py           # Repository discovery and management utilities
├── test_runner.py            # Test execution, collection, and result aggregation
└── indexers/
    ├── __init__.py
    ├── go_indexer.py         # Go language indexer (AST + symbol extraction)
    ├── python_indexer.py     # Python language indexer (AST + symbol extraction)
    └── typescript_indexer.py # TypeScript/JSX language indexer (AST + symbol extraction)
```

---

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/Techkilladis/Darag-py-package.git
cd Darag-py-package

# Install in development mode
pip install -e .

# Or install from PyPI (when published)
pip install darago-core
```

### Usage

```python
from darago_core.indexer_factory import IndexerFactory
from darago_core.context_engine import ContextEngine
from darago_core.repo_manager import RepoManager

# Initialize repository manager
repo = RepoManager("./path/to/repo")
repo.discover()

# Register and run indexers
factory = IndexerFactory()
factory.register("python", PythonIndexer)
factory.register("typescript", TypeScriptIndexer)
factory.register("go", GoIndexer)

indexers = factory.create_indexers(["python", "typescript"])
for indexer in indexers:
    indexer.index()

# Generate context
engine = ContextEngine(indexers)
context = engine.generate("ClassName.method_name", depth=3)
print(context)
```

---

## Development

### Prerequisites

- Python 3.9+
- `pip` or `poetry`

### Setup

```bash
# Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
.venv\Scripts\activate      # Windows

# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest darago_core/
```

### Adding a New Language Indexer

1. Create a new file in `darago_core/indexers/` (e.g., `rust_indexer.py`)
2. Extend the `BaseIndexer` class from [`indexer_base.py`](darago_core/indexer_base.py)
3. Implement required methods: `index()`, `get_symbols()`, `get_dependencies()`
4. Register the indexer in the factory or CLI entry point

```python
from darago_core.indexer_base import BaseIndexer

class RustIndexer(BaseIndexer):
    language = "rust"

    def index(self):
        # Implement AST parsing and symbol extraction for Rust
        pass

    def get_symbols(self):
        # Return list of symbols (functions, classes, modules)
        pass

    def get_dependencies(self):
        # Return dependency graph
        pass
```

---

## Publishing

```bash
# Build the package
python -m build

# Upload to PyPI (requires credentials)
twine upload dist/*
```

---

## Contributing

Contributions are welcome! Please follow these steps:

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Commit** your changes (`git commit -m 'Add amazing feature'`)
4. **Push** to the branch (`git push origin feature/amazing-feature`)
5. **Open** a Pull Request

Please ensure all code follows the project's style guidelines and includes appropriate tests.

---

## License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.

---

## Related Repositories

| Repository | Description |
|------------|-------------|
| [Darag-CLI](https://github.com/Techkilladis/Darag-CLI) | Command-line interface built on top of this core library |
| [Darag-Vscode-Extension](https://github.com/Techkilladis/Darag-Vscode-Extension) | Official VS Code extension powered by this package |

---

## Acknowledgments

- AST parsing leverages Python's built-in `ast` module and TypeScript's `typescript` npm package via subprocess
- Architecture inspired by modern static analysis tools like `pylint`, `eslint`, and `gopls`
- Powered the Darag ecosystem for intelligent code understanding

---

<div align="center">

**Built by the Darag Team**

[Report Bug](https://github.com/Techkilladis/Darag-py-package/issues) · [Request Feature](https://github.com/Techkilladis/Darag-py-package/issues)

</div>
