import os

# Same ignore rules to keep searches fast
IGNORE_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env', '.dave_cache'}
VALID_EXTENSIONS = {'.py', '.html', '.css', '.js', '.md', '.txt', '.php'}

def search_in_file(filename, search_query, target_dir):
    """Searches for a string. If filename is 'ALL', searches the entire project."""
    
    # --- NEW: SHADOW SCANNER LOGIC ---
    if filename.upper() == "ALL":
        results = []
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in VALID_EXTENSIONS:
                    full_path = os.path.join(root, f)
                    try:
                        with open(full_path, 'r', encoding='utf-8') as file_obj:
                            for i, line in enumerate(file_obj, 1):
                                if search_query.lower() in line.lower():
                                    # Stamp the filename on the result so D.A.V.E knows where it is
                                    results.append(f"[{f}] Line {i}: {line.strip()}")
                    except Exception:
                        continue
        
        if not results:
            return f"No matches found for '{search_query}' anywhere in the project."
        return "\n".join(results[:30]) # Return top 30 matches across project
    
    # --- ORIGINAL LOGIC ---
    full_path = os.path.abspath(os.path.join(target_dir, filename))
    
    if not os.path.exists(full_path):
        return f"Error: File {filename} not found. Try setting filename to 'ALL' to search the whole folder."

    results = []
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
                if search_query.lower() in line.lower():
                    results.append(f"Line {i}: {line.strip()}")
        
        if not results:
            return f"No matches found for '{search_query}' in {filename}."
        
        return "\n".join(results[:10]) # Return top 10 matches
    except Exception as e:
        return f"Error searching file: {e}"