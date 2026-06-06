import os

def rename_file(old_filename: str, new_filename: str, target_dir: str) -> str:
    """Renames a file, preserving all its formatted contents perfectly."""
    old_path = os.path.abspath(os.path.join(target_dir, old_filename))
    new_path = os.path.abspath(os.path.join(target_dir, new_filename))

    if not os.path.exists(old_path):
        return f"Error: Source file '{old_filename}' does not exist."
    
    if os.path.exists(new_path):
        return f"Error: Destination '{new_filename}' already exists."

    try:
        os.rename(old_path, new_path)
        return f"Successfully renamed {old_filename} to {new_filename}."
    except Exception as e:
        return f"Error renaming file: {e}"
    
def delete_file(filename: str, target_dir: str) -> str:
    """Permanently deletes a file from the directory."""
    file_path = os.path.abspath(os.path.join(target_dir, filename))
    
    if not file_path.startswith(os.path.abspath(target_dir)):
        return "Error: Cannot delete files outside the target directory."

    if not os.path.exists(file_path):
        return f"Error: File '{filename}' not found."

    try:
        os.remove(file_path)
        return f"Successfully deleted {filename}."
    except Exception as e:
        return f"Error deleting file: {e}"