import os
from core.config import MAX_READ_LINES

# Track read history for continuity hinting: { filepath: (last_start, last_end) }
_READ_HISTORY = {}

def read_file_with_lines(filepath: str, target_dir: str, start_line: int = None, end_line: int = None) -> str:
    """Reads a file and prepends line numbers, enforcing pagination and continuity."""
    full_path = os.path.abspath(os.path.join(target_dir, filepath))
    
    if not full_path.startswith(os.path.abspath(target_dir)):
        return "Error: Access denied. File is outside the target directory."

    if not os.path.exists(full_path):
        return f"Error: File '{filepath}' does not exist."
        
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        total_lines = len(lines)
        if total_lines == 0:
            return "File is empty."

        # --- BATCH 2.3: PAGINATION ENFORCEMENT ---
        start = int(start_line) if start_line is not None else 1
        end = int(end_line) if end_line is not None else total_lines
        
        # Bounds checking
        start = max(1, min(start, total_lines))
        end = max(start, min(end, total_lines))
        
        # Enforce max window limit
        if (end - start + 1) > MAX_READ_LINES:
            end = start + MAX_READ_LINES - 1
            end = min(end, total_lines)
            
        # Slicing lines (0-indexed)
        slice_lines = lines[start-1 : end]
        numbered_lines = [f"{start + i}| {line}" for i, line in enumerate(slice_lines)]
        output = "".join(numbered_lines)
        
        # --- BATCH 2.3: CONTINUITY HINTING & TRUNCATION MESSAGING ---
        system_hints = []
        last_read = _READ_HISTORY.get(filepath)
        
        if last_read:
            last_start, last_end = last_read
            # Detect erratic jumping (more than 20 lines away from last read window)
            if start > last_end + 20 or end < last_start - 20:
                system_hints.append(f"\n[SYSTEM: Non-sequential read detected. You jumped from lines {last_start}-{last_end} to {start}-{end}.]")
        
        # Update history
        _READ_HISTORY[filepath] = (start, end)
        
        # Truncation warning if the file is larger than the window
        if total_lines > (end - start + 1):
            system_hints.append(f"\n[TRUNCATED: file is {total_lines} lines. You read lines {start}–{end}. Use start_line and end_line to continue.]")
            
        if system_hints:
            output += "".join(system_hints)
            
        return output
        
    except Exception as e:
        return f"Error reading file: {e}"