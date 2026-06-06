import os

# The junk we don't want the AI to read
IGNORE_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env', '.dave_cache'}
IGNORE_FILES = {'.DS_Store'}
# NEW: Safe extensions so we don't try to read images or compiled binaries
VALID_EXTENSIONS = {'.py', '.html', '.css', '.js', '.md', '.txt', '.php'}

def peek_file(filepath):
    """Reads the file and skips imports to find the first meaningful line of code."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in VALID_EXTENSIONS:
        return ""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                stripped = line.strip()
                # Skip empty lines, 'import x', and 'from x import y'
                if stripped and not stripped.startswith('import ') and not stripped.startswith('from '):
                    # Clean up long lines for the terminal display
                    clean_hint = stripped.replace('\n', '').replace('\r', '')
                    return f"  | Context: {clean_hint[:60]}..."
    except Exception:
        pass
    return ""

def scan_directory(target_dir: str) -> str:
    """Returns a clean tree view of the project, ignoring junk folders."""
    if not os.path.exists(target_dir):
        return f"Error: Directory '{target_dir}' not found."

    tree = []
    
    # Walk through the directory
    for root, dirs, files in os.walk(target_dir):
        # Modify dirs in-place to skip the ignored directories
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        
        # Calculate how deep we are to indent properly
        level = root.replace(target_dir, '').count(os.sep)
        indent = ' ' * 4 * level
        folder_name = os.path.basename(root)
        
        if folder_name:
            tree.append(f"{indent}📁 {folder_name}/")
        
        # Add the files
        subindent = ' ' * 4 * (level + 1)
        for f in files:
            if f not in IGNORE_FILES:
                filepath = os.path.join(root, f)
                context_hint = peek_file(filepath) # NEW: Grab context
                tree.append(f"{subindent}📄 {f}{context_hint}")
    
    if not tree:
        return "Workspace is empty."
        
    return "\n".join(tree)