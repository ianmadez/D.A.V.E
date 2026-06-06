import os
import re
import datetime

try:
    import libcst as cst
    from libcst.metadata import PositionProvider
    LIBCST_AVAILABLE = True
except ImportError:
    LIBCST_AVAILABLE = False
    print("\n[SYSTEM WARNING] 'libcst' not installed. Falling back to regex manipulations. Run 'pip install libcst' for safe editing.")

# Tracker for deliberate safe write attempts
_SAFE_EDIT_FAILURES = {}
_SAFE_EDIT_LIMIT = 3

def _log_edit_intent(target_dir, filename, tool_name, intent, result):
    """Batch 3.4: Intent-Based Audit Logging."""
    if not intent:
        intent = "UNKNOWN INTENT"
    log_path = os.path.join(target_dir, ".dave_cache", "edits.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "SUCCESS" if "Error" not in result and "CRITICAL" not in result else "FAILURE"
    
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {status} | TOOL: {tool_name} | TARGET: {filename} | INTENT: {intent}\n")
    except Exception:
        pass


def _safe_apply_edit(full_path, new_code):
    """Compile (for Python) and write file if syntax is valid; 3 strikes fails."""
    payload = new_code.replace('\\n', '\n')
    if full_path.lower().endswith('.py'):
        try:
            compile(payload, full_path, 'exec')
        except Exception as e:
            count = _SAFE_EDIT_FAILURES.get(full_path, 0) + 1
            _SAFE_EDIT_FAILURES[full_path] = count
            if count >= _SAFE_EDIT_LIMIT:
                return f"CRITICAL: 3 safe-edit compile attempts failed for {full_path}. Please inspect code and retry. Last: {e}"
            return f"Error: Python compile failed for {full_path}: {e}"
            
    try:
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(payload)
        _SAFE_EDIT_FAILURES[full_path] = 0
        return f"Successfully applied safe edit to {os.path.basename(full_path)}."
    except Exception as e:
        count = _SAFE_EDIT_FAILURES.get(full_path, 0) + 1
        _SAFE_EDIT_FAILURES[full_path] = count
        if count >= _SAFE_EDIT_LIMIT:
            return f"CRITICAL: 3 safe-edit IO failures for {full_path}. {e}"
        return f"Error writing file: {e}"


def rewrite_file(filename, new_code, target_dir, edit_intent=""):
    """Nukes a file and writes it fresh from scratch."""
    full_path = os.path.join(target_dir, filename)
    if os.path.exists(full_path):
        with open(full_path, 'r', encoding='utf-8') as f:
            old_lines = len(f.read().split('\n'))
        new_lines = len(new_code.split('\n'))
        if old_lines > 50 and new_lines < old_lines * 0.25:
            res = f"Refused to rewrite {filename}: new content is too short. Use replace_named_block or replace_lines."
            _log_edit_intent(target_dir, filename, "rewrite_file", edit_intent, res)
            return res
            
    res = _safe_apply_edit(full_path, new_code)
    _log_edit_intent(target_dir, filename, "rewrite_file", edit_intent, res)
    return res


def replace_lines(filename, start_line, end_line, new_code, target_dir, edit_intent=""):
    """Surgically replaces a range of lines in a file, with strict bounds checking."""
    full_path = os.path.join(target_dir, filename)
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        start = int(start_line) - 1
        end = int(end_line)
        
        # --- BATCH 5.7: PRE-EXECUTION SANITY GATE ---
        total_lines = len(lines)
        if start < 0 or start > total_lines:
            res = f"CRITICAL ERROR: start_line ({start_line}) is out of bounds. {filename} only has {total_lines} lines."
            _log_edit_intent(target_dir, filename, "replace_lines", edit_intent, res)
            return res
            
        if end < start:
            res = f"CRITICAL ERROR: end_line ({end_line}) cannot be less than start_line ({start_line})."
            _log_edit_intent(target_dir, filename, "replace_lines", edit_intent, res)
            return res

        # Safeguard against nuking entire file
        new_lines_count = len([line for line in new_code.split('\n') if line.strip()])
        if start == 0 and end >= total_lines and new_lines_count < total_lines * 0.5:
            res = "Refused to replace entire file with much shorter content. Use rewrite_file for full rewrites."
            _log_edit_intent(target_dir, filename, "replace_lines", edit_intent, res)
            return res

        # Replace the specific slice in memory
        lines[start:end] = [new_code + '\n'] if new_code else []

        updated_content = ''.join(lines)
        res = _safe_apply_edit(full_path, updated_content)
        _log_edit_intent(target_dir, filename, "replace_lines", edit_intent, res)
        return res
    except Exception as e:
        res = f"Error updating lines: {str(e)}"
        _log_edit_intent(target_dir, filename, "replace_lines", edit_intent, res)
        return res


def _find_symbol_lines(source_code, symbol_name):
    """Batch 3.3: Safely locates the exact 1-indexed lines of a function/class."""
    if LIBCST_AVAILABLE:
        try:
            tree = cst.parse_module(source_code)
            wrapper = cst.MetadataWrapper(tree)
            
            class Locator(cst.CSTVisitor):
                METADATA_DEPENDENCIES = (PositionProvider,)
                def __init__(self):
                    self.start, self.end = None, None
                def visit_FunctionDef(self, node):
                    if node.name.value == symbol_name:
                        pos = self.get_metadata(PositionProvider, node)
                        self.start, self.end = pos.start.line, pos.end.line
                def visit_ClassDef(self, node):
                    if node.name.value == symbol_name:
                        pos = self.get_metadata(PositionProvider, node)
                        self.start, self.end = pos.start.line, pos.end.line
            
            loc = Locator()
            wrapper.visit(loc)
            if loc.start and loc.end:
                return loc.start, loc.end
        except Exception:
            pass

    # Regex Fallback if CST fails or isn't installed
    lines = source_code.split('\n')
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^\s*(def|class)\s+{symbol_name}\b", line):
            start = i + 1
            break
            
    if start:
        indent = len(lines[start-1]) - len(lines[start-1].lstrip())
        end = start
        for i in range(start, len(lines)):
            if lines[i].strip() == "":
                end = i + 1
                continue
            curr_indent = len(lines[i]) - len(lines[i].lstrip())
            if curr_indent <= indent and lines[i].strip() and not lines[i].strip().startswith('#'):
                break
            end = i + 1
        return start, end
    return None, None


def replace_named_block(filename, symbol_name, new_code, target_dir, edit_intent=""):
    """Batch 3.2: Replaces a Class or Function using LibCST Native Nodes (or line-based fallback)."""
    full_path = os.path.join(target_dir, filename)
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            source = f.read()

        if LIBCST_AVAILABLE and full_path.endswith('.py'):
            try:
                tree = cst.parse_module(source)
                new_tree = cst.parse_module(new_code)
                new_node = new_tree.body[0] # Extract the function/class node

                class BlockReplacer(cst.CSTTransformer):
                    def __init__(self):
                        self.replaced = False
                    def leave_FunctionDef(self, original_node, updated_node):
                        if original_node.name.value == symbol_name:
                            self.replaced = True
                            return new_node
                        return updated_node
                    def leave_ClassDef(self, original_node, updated_node):
                        if original_node.name.value == symbol_name:
                            self.replaced = True
                            return new_node
                        return updated_node

                replacer = BlockReplacer()
                modified_tree = tree.visit(replacer)

                if replacer.replaced:
                    res = _safe_apply_edit(full_path, modified_tree.code)
                    _log_edit_intent(target_dir, filename, "replace_named_block", edit_intent, res)
                    return res
            except Exception as e:
                pass # Fall through to line-based anchor replacement

        # Graceful Fallback: Anchor-based line slicing
        start, end = _find_symbol_lines(source, symbol_name)
        if start and end:
            return replace_lines(filename, start, end, new_code, target_dir, edit_intent)
        else:
            res = f"Error: Could not resolve symbol '{symbol_name}'. Use replace_lines."
            _log_edit_intent(target_dir, filename, "replace_named_block", edit_intent, res)
            return res
            
    except Exception as e:
        return f"Error modifying block: {e}"


def insert_before_symbol(filename, symbol_name, new_code, target_dir, edit_intent=""):
    """Batch 3.3: Surgically injects code before a target symbol."""
    full_path = os.path.join(target_dir, filename)
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            source = f.read()
        start, _ = _find_symbol_lines(source, symbol_name)
        if start:
            # Slicing lines[start-1:start-1] injects cleanly before the target line
            return replace_lines(filename, start, start-1, new_code, target_dir, edit_intent)
        return f"Error: Symbol '{symbol_name}' not found."
    except Exception as e:
        return f"Error: {e}"


def insert_after_symbol(filename, symbol_name, new_code, target_dir, edit_intent=""):
    """Batch 3.3: Surgically injects code after a target symbol ends."""
    full_path = os.path.join(target_dir, filename)
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            source = f.read()
        _, end = _find_symbol_lines(source, symbol_name)
        if end:
            # Slicing lines[end:end] injects cleanly after the target block
            return replace_lines(filename, end+1, end, new_code, target_dir, edit_intent)
        return f"Error: Symbol '{symbol_name}' not found."
    except Exception as e:
        return f"Error: {e}"