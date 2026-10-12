"""Single source of truth for indexing defaults (used by the builder AND the config)."""

DEFAULT_IGNORE: list[str] = [
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".atomic_flow", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "*.egg-info",
    "package-lock.json", "poetry.lock", "*.min.js", "*.min.css", "*.map",
]
SOURCE_SUFFIXES = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".h",
                   ".cpp", ".md", ".yaml", ".yml", ".toml", ".json"}
PYTHON_SUFFIXES = {".py", ".pyi"}
MAX_FILE_BYTES = 1_000_000
