import os
from tools.code_editor import _safe_apply_edit

def create_file(filename: str, content: str, target_dir: str) -> str:
    """Creates a brand new file with the specified content."""
    # Prevent directory traversal attacks (e.g., trying to write to ../../../windows)
    full_path = os.path.abspath(os.path.join(target_dir, filename))
    if not full_path.startswith(os.path.abspath(target_dir)):
        return "Error: Cannot create files outside the target directory."

    # Create subdirectories if the AI wants to make a file inside a new folder (e.g., 'js/app.js')
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    if os.path.exists(full_path):
        return f"Error: File '{filename}' already exists. Use replace_lines to edit it."

    return _safe_apply_edit(full_path, content)