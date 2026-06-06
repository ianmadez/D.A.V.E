# core/config.py

# Extensions D.A.V.E. is allowed to read and edit
VALID_EXTENSIONS = (
    ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", 
    ".json", ".md", ".txt", ".yml", ".yaml", ".env", ".ini"
)

# Folders that will destroy the context window if scanned
IGNORE_FOLDERS = {
    "node_modules", ".git", ".next", "dist", "build", 
    "__pycache__", ".dave_cache", "venv", ".venv"
}

# The hard limit for how many lines the LLM can read in one request
MAX_READ_LINES = 150