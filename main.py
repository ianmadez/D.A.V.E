import subprocess
import os
import sys
import json
import re
import hashlib
import argparse
import shutil
import time
import py_compile
import textwrap
import atexit
from core.brain import get_llm_response
from tools.scanner import scan_directory
from tools.file_reader import read_file_with_lines
from tools.code_editor import replace_lines, rewrite_file, _safe_apply_edit, insert_after_symbol, insert_before_symbol, replace_named_block
from tools.file_creator import create_file
from tools.file_manager import rename_file, delete_file
from tools.search_engine import search_in_file
from tools.terminal_runner import run_command
from tools.planner import manage_plan
from tools.mapper import semantic_search

# --- Small color helper (kept as-is, fixed values) ---
class Colors:
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    RESET = '\033[0m'

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def normalize_text_output(text, width=90):
    if not text:
        return ""
    lines = text.splitlines()
    formatted = []
    for line in lines:
        line = line.rstrip()
        if not line:
            formatted.append("")
            continue
        if line.strip().startswith(('-', '*', '+')) or re.match(r'^\s*\d+\.', line):
            prefix = line.strip()[:2]
            content = line.strip()[2:].strip()
            wrapped = textwrap.fill(content, width=width-4)
            formatted.append(f"  {prefix} {wrapped}")
        elif len(line) > width:
            formatted.extend(textwrap.wrap(line, width=width))
        else:
            formatted.append(line)
    return "\n".join(formatted)


def pretty_print(title, text):
    print(f"{Colors.GREEN}D.A.V.E:{Colors.RESET}")
    print(normalize_text_output(text).rstrip())
    print()


def print_header(target_directory):
    print(f"{Colors.CYAN}=={Colors.RESET}")
    print(f"{Colors.GREEN}D.A.V.E Initialized.{Colors.RESET}")
    print(f"{Colors.YELLOW}Target Directory: {target_directory}{Colors.RESET}")
    print(f"{Colors.CYAN}Type 'exit' or 'quit' to stop.{Colors.RESET}")
    print(f"{Colors.CYAN}=={Colors.RESET}\n")

def typewriter_print(text, speed=0.015):
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(speed)
    print()

def create_backup(filename, target_dir):
    """Creates a cache backup of a file before editing so 'undo' works."""
    cache_dir = os.path.join(target_dir, '.dave_cache')
    os.makedirs(cache_dir, exist_ok=True)
    # keep only the latest backup per run
    for old_f in os.listdir(cache_dir):
        try:
            os.remove(os.path.join(cache_dir, old_f))
        except Exception:
            pass
    original_file_path = os.path.join(target_dir, filename)
    if os.path.exists(original_file_path):
        shutil.copy2(original_file_path, os.path.join(cache_dir, filename))
        
# --- FreeLLMAPI Proxy Management ---
proxy_process = None

def cleanup_proxy():
    global proxy_process
    if proxy_process:
        print(f"\n{Colors.YELLOW}[System] Shutting down FreeLLMAPI proxy...{Colors.RESET}")
        proxy_process.terminate()
        try:
            proxy_process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proxy_process.kill()


atexit.register(cleanup_proxy)

# --- Internal Auditor (Stub Detector) ---
LAZY_MARKERS = [
    "# ...", "TODO:", "# logic here", "# rest of code",
    "/* ... */", "// ... existing code", "# implement", "/* ... * /"
]
def internal_auditor(new_code: str) -> bool:
    """Return True if code is clean, False if lazy markers found."""
    if not new_code:
        return False
    lower = new_code.lower()
    for m in LAZY_MARKERS:
        if m.lower() in lower:
            return False
    return True


def _audit_gate_flag(code: str) -> bool:
    """Return True if code contains shell snippets or missing imports.

    - flag shell by detecting leading "$ " or shebangs or common shell commands
    - flag missing imports for a small set of frequently used modules
    """
    if not code:
        return True
    # shell patterns
    if re.search(r"^(\s*\$\s)|(^#!)" , code, re.MULTILINE):
        return True
    # simple command keywords that look like shell
    if re.search(r"\b(curl|wget|rm|sudo|apt-get|python\s)|;\s" , code):
        return True
    # missing imports check: look for module names without import
    modules = ["os","sys","json","re","requests","subprocess","math","datetime"]
    for mod in modules:
        if re.search(rf"\b{mod}\b", code) and not re.search(rf"import\s+{mod}", code):
            return True
    return False

# --- Agent visibility helper (always enabled) ---

def _show_brain_work(response):
    """Print reasoning and planned actions from the brain response."""
    thought = response.get("thought")
    if thought:
        # Fix missing spaces after punctuation from fast LLMs
        thought = re.sub(r'([.:])([A-Za-z])', r'\1 \2', thought)
        print(f"\n{Colors.CYAN}[brain thinking]{Colors.RESET}\n{Colors.CYAN}{thought}{Colors.RESET}\n")
    actions = response.get("actions", [])
    if actions:
        print("[plan]")
        for i, a in enumerate(actions, 1):
            desc = a.get("tool", "none")
            if a.get("filename"):
                desc += " " + a["filename"]
            if a.get("command"):
                desc += " `" + a["command"] + "`"
            print(f"  {i}. {desc}")
        print()

# --- WORKSPACE INDEXING & CACHING ---

def _hash_file(filepath):
    """Compute SHA256 hash of a file."""
    try:
        with open(filepath, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


def _build_workspace_index(target_directory):
    """Scan directory, build file index with hashes and keywords."""
    cache_dir = os.path.join(target_directory, '.dave_cache')
    os.makedirs(cache_dir, exist_ok=True)
    index_path = os.path.join(cache_dir, 'workspace_index.json')
    
    index = {"files": {}, "search": {}, "timestamp": time.time()}
    keywords = set()
    
    for root, dirs, files in os.walk(target_directory):
        dirs[:] = [d for d in dirs if d != '.dave_cache']
        for f in files:
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, target_directory)
            try:
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as file:
                    content = file.read()
                    lines = len(content.splitlines())
                    file_hash = _hash_file(full_path)
                    
                    index["files"][rel_path] = {
                        "lines": lines,
                        "size": os.path.getsize(full_path),
                        "ext": os.path.splitext(f)[1],
                        "hash": file_hash
                    }
                    
                    # extract keywords (function/class names, imports)
                    kw_patterns = [r'def\s+(\w+)', r'class\s+(\w+)', r'import\s+(\w+)', r'from\s+(\w+)']
                    for pattern in kw_patterns:
                        for match in re.finditer(pattern, content):
                            kw = match.group(1)
                            if kw not in index["search"]:
                                index["search"][kw] = []
                            if rel_path not in index["search"][kw]:
                                index["search"][kw].append(rel_path)
            except Exception:
                pass
    
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, indent=2)
    return index


def _get_workspace_index(target_directory):
    """Load cached index or rebuild if missing."""
    cache_path = os.path.join(target_directory, '.dave_cache', 'workspace_index.json')
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return _build_workspace_index(target_directory)


def _compute_diff(target_directory):
    """Compare current state to cached index; return list of changed/new files."""
    old_index = _get_workspace_index(target_directory)
    new_index = _build_workspace_index(target_directory)
    
    changes = {"modified": [], "new": [], "deleted": []}
    old_files = old_index.get("files", {})
    new_files = new_index.get("files", {})
    
    for path, meta in new_files.items():
        if path not in old_files:
            changes["new"].append(path)
        elif old_files[path].get("hash") != meta.get("hash"):
            changes["modified"].append(path)
    
    for path in old_files:
        if path not in new_files:
            changes["deleted"].append(path)
    
    return changes


# --- JIT reminder helpers --------------------------------------------------
RECURRING_REMINDER = "REMINDER: Output complete code. Use double quotes for contractions."

def _append_with_reminder(chat_history, role, content, reminder=None):
    """Add a message to the history, optionally injecting a <system-reminder>."""
    chat_history.append({"role": role, "content": content})
    if reminder:
        chat_history.append({"role": "system",
                              "content": f"<system-reminder>{reminder}</system-reminder>"})

def _add_tool_output(chat_history, text):
    """Append tool output plus the recurring reminder."""
    _append_with_reminder(chat_history, "assistant", text, RECURRING_REMINDER)

def _get_language_reminder(filename: str) -> str:
    """Return language-specific best practice reminder for a given file."""
    ext = os.path.splitext(filename)[1].lower()
    reminders = {
        ".py": "REMINDER: Use double quotes for strings with apostrophes (e.g. 'It\'s'). Avoid overly complex f-strings.",
        ".js": "REMINDER: Use const/let (not var). Include semicolons. Check for proper async/await handling.",
        ".css": "REMINDER: Use kebab-case for class names. Avoid !important. Design mobile-first, then scale up.",
        ".html": "REMINDER: Use semantic HTML (<nav>, <main>, <article>). Always close tags. Check accessibility (alt text, labels).",
        ".java": "REMINDER: PascalCase for classes, camelCase for methods. Use try-catch for exceptions. Import necessary classes.",
    }
    return reminders.get(ext, "REMINDER: Follow language conventions and test thoroughly.")

def _extract_file_from_actions(actions: list) -> str:
    """Extract filename from first write action in batch; returns empty if none found."""
    for a in actions:
        if a.get("tool") in ["create_file", "rewrite_file", "replace_lines", "replace_function"]:
            if a.get("filename"):
                return a["filename"]
    return ""

def _apply_write_with_reminder(chat_history: list, filename: str, action_result: str) -> None:
    """Append language-specific reminder after successful write operation."""
    lang_reminder = _get_language_reminder(filename)
    _append_with_reminder(chat_history, "assistant", action_result, lang_reminder)

def main():
    parser = argparse.ArgumentParser(description="Local AI Coding Agent")
    parser.add_argument("target_dir", nargs="?", default=os.getcwd(), help="The project folder.")
    args = parser.parse_args()

    target_directory = os.path.abspath(args.target_dir)
    if not os.path.exists(target_directory):
        print(f"{Colors.RED}Error: Directory '{target_directory}' does not exist.{Colors.RESET}")
        sys.exit(1)

    print_header(target_directory)

    # Choose engine
    print(f"{Colors.YELLOW}Select D.A.V.E.'s Brain:{Colors.RESET}")
    print("1. Local Engine (Ollama/Qwen)")
    print("2. Cloud API Engine (GPT/Groq)")
    print("3. FreeLLMAPI Proxy (localhost:3001)")
    while True:
        choice = input(f"{Colors.GREEN}Enter 1, 2 or 3: {Colors.RESET}").strip()
        if choice == '1':
            llm_mode = "local"
            print(f"{Colors.CYAN}[System] Booting Local Engine ... {Colors.RESET}\n")
            break
        elif choice == '2':
            llm_mode = "api"
            print(f"{Colors.CYAN}[System] Booting Cloud API Engine ... {Colors.RESET}\n")
            break
        elif choice == '3':
            llm_mode = "freellmapi"
            print(f"{Colors.CYAN}[System] Booting FreeLLMAPI Proxy ... {Colors.RESET}\n")
            
            # Look for the freellmapi folder right next to the D.A.V.E folder
            proxy_dir = os.path.abspath(os.path.join(os.getcwd(), "..", "freellmapi"))

            if os.path.exists(proxy_dir):
                global proxy_process
            # Launch the proxy in the background, suppressing its terminal output
                proxy_process = subprocess.Popen(
                    "npm run dev",
                    cwd=proxy_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=True # Required on Windows to resolve 'npm'
                )
                print(f"{Colors.GREEN}[System] Proxy is running silently on localhost:3001.{Colors.RESET}\n")
            else:
                print(f"{Colors.RED}[Error] Could not find the 'freellmapi' folder.{Colors.RESET}")
                print(f"{Colors.YELLOW}Please ensure you have cloned the repo so it sits directly next to your D.A.V.E folder:{Colors.RESET}")
                print(f"  📂 {os.path.dirname(os.getcwd())}")
                print(f"   ├── 📁 {os.path.basename(os.getcwd())} (D.A.V.E)")
                print(f"   └── 📁 freellmapi")
                sys.exit(1)
            break
        print(f"{Colors.RED}Invalid choice. Please enter 1, 2 or 3.{Colors.RESET}")

    chat_history = []
    error_tracker = []
    action_tracker = []

    # track last executed tool and whether its run_command succeeded
    last_used_tool = None
    last_run_success = False

    # audit gate counters
    audit_block_count = 0

    # mode switching: agent vs ask (chat with workspace context)
    mode = "agent"
    agent_history = []
    ask_history = []
    workspace_index = _build_workspace_index(target_directory)
    print(f"{Colors.CYAN}[Info] Workspace indexed. Type /chat to ask questions, /agent to code.{Colors.RESET}\n")

    # === BATCH 1.1: INITIALIZE BIMODAL TASK STATE ===
    TaskState = {
        "llm_notes": {
            "analysis": "",
            "options": [],
            "decision": "",
            "reason": "",
            "confidence": 0.0
        },
        "system_ground_truth": {
            "last_command": "",
            "exit_code": None,
            "raw_stderr": ""
        },
        "system_state": {
            "current_phase": "Scout",
            "confidence": 1.0,
            "last_failure_type": None,
            "observed_files": [],  
            "retry_count": 0,      
            "pinned_snippets": [],
            "last_failing_signature": None,
            "flag_confidence": True,
            "flag_debug": False
        }
    }

    try:
        from tools.mapper import map_codebase
        ast_skeleton = map_codebase(target_directory)
    except Exception:
        ast_skeleton = ""

    # Attach AST skeleton into context for chat mode
    workspace_index["ast_skeleton"] = ast_skeleton

    while True:
        try:
            user_input = input(f"{Colors.GREEN}You: {Colors.RESET}").strip()
            if user_input.lower() in ['exit', 'quit']:
                print(f"{Colors.YELLOW}Exiting.{Colors.RESET}")
                break

            # quick commands
            if user_input.lower() == '/chat':
                mode = "chat"
                ask_history = []  # reset conversation in chat mode
                print(f"{Colors.CYAN}[Mode] Switched to ask mode. Query the workspace freely. Type /agent to return.{Colors.RESET}\n")
                continue

            if user_input.lower() == '/agent':
                mode = "agent"
                print(f"{Colors.CYAN}[Mode] Switched to agent mode.{Colors.RESET}\n")
                continue

            if user_input.lower().startswith('/cd'):
                new_path = user_input[3:].strip().strip('"').strip("'")
                if not new_path:
                    print(f"{Colors.YELLOW}Usage: /cd <path_to_directory>{Colors.RESET}")
                    continue
                potential_path = os.path.abspath(os.path.join(target_directory, new_path))
                if os.path.exists(potential_path) and os.path.isdir(potential_path):
                    target_directory = potential_path
                    chat_history = []
                    error_tracker = []
                    # Rebuild workspace index and AST for new directory (fixes chat mode context bug)
                    workspace_index = _build_workspace_index(target_directory)
                    try:
                        ast_skeleton = map_codebase(target_directory)
                        workspace_index["ast_skeleton"] = ast_skeleton
                    except Exception:
                        workspace_index["ast_skeleton"] = ""
                    clear_screen()
                    print_header(target_directory)
                    print(f"{Colors.CYAN}{scan_directory(target_directory)}{Colors.RESET}\n")
                else:
                    print(f"{Colors.RED}Error: Path '{new_path}' not found or is not a directory.{Colors.RESET}")
                continue
            
            # --- BATCH 5.8: DEBUG TOGGLES ---
            if user_input.lower().startswith('/toggle'):
                flag = user_input.split(' ')[-1].strip().lower()
                valid_flags = ["confidence", "debug"]
                if flag in valid_flags:
                    key = f"flag_{flag}"
                    current = TaskState["system_state"].get(key, False if flag == "debug" else True)
                    TaskState["system_state"][key] = not current
                    state_str = "ON" if not current else "OFF"
                    print(f"{Colors.YELLOW}[System] {flag.upper()} is now {state_str}.{Colors.RESET}")
                else:
                    print(f"{Colors.YELLOW}Usage: /toggle <confidence|debug>{Colors.RESET}")
                continue

            if user_input.lower() == '/reset':
                chat_history = []
                action_tracker = []
                clear_screen()
                print_header(target_directory)
                print(f"{Colors.GREEN}Memory wiped. D.A.V.E. is fresh and ready.{Colors.RESET}")
                continue

            if user_input.lower() == 'undo':
                cache_dir = os.path.join(target_directory, '.dave_cache')
                print(f"{Colors.YELLOW}[System] Attempting to restore last edited file ... {Colors.RESET}")
                if os.path.exists(cache_dir) and os.listdir(cache_dir):
                    cached_filename = os.listdir(cache_dir)[0]
                    cached_file_path = os.path.join(cache_dir, cached_filename)
                    original_file_path = os.path.join(target_directory, cached_filename)
                    shutil.copy2(cached_file_path, original_file_path)
                    print(f"{Colors.GREEN}Successfully reverted {cached_filename} to its previous state.{Colors.RESET}")
                    os.remove(cached_file_path)
                else:
                    print(f"{Colors.RED}No recent edits found in cache to undo.{Colors.RESET}")
                continue


            if not user_input:
                continue

            def _build_dynamic_context(user_input, workspace_index, target_directory):
                """Build lightweight overview + query-specific LOD context for chat mode."""
                context = "[WORKSPACE OVERVIEW]\n"
                
                # Extract project name from folder
                project_name = os.path.basename(target_directory)
                context += f"Project: {project_name}\n"
                
                total_files = len(workspace_index['files'])
                context += f"Total files: {total_files}\n"
                
                # Key directories
                dirs = set()
                for f in workspace_index['files']:
                    dirs.add(os.path.dirname(f) or '.')
                context += f"Key directories: {', '.join(sorted(dirs))}\n"
                
                # Auto-include README or main entry point at top
                context += "\n[KEY FILES]\n"
                key_files = []
                for fname in workspace_index['files']:
                    if 'readme' in fname.lower() or fname.lower() in ['main.py', 'index.py', 'app.py', '__main__.py']:
                        key_files.append(fname)
                
                if key_files:
                    for kf in key_files[:2]:  # Show top 2
                        meta = workspace_index['files'][kf]
                        context += f"  • {kf}: {meta['lines']} lines\n"
                        # Read preview of key files to extract purpose
                        try:
                            full_path = os.path.join(target_directory, kf)
                            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                                preview = f.read()[:300]
                            # Extract first comment/docstring if exists
                            first_comment = re.search(r'(""".*?"""|\'\'\'.*?\'\'\'|#.*?\n)', preview, re.DOTALL)
                            if first_comment:
                                context += f"    Summary: {first_comment.group(1)[:100]}...\n"
                        except:
                            pass
                
                # Top symbols
                if workspace_index['search']:
                    top_symbols = list(workspace_index['search'].keys())[:8]
                    context += f"\nTop symbols: {', '.join(top_symbols)}\n"
                
                # Include AST-based code structure skeleton
                if workspace_index.get("ast_skeleton"):
                    context += f"\nCode Structure: {workspace_index['ast_skeleton']}\n"
                
                # Extract and include summary from index.html if it exists
                index_html_path = os.path.join(target_directory, "index.html")
                if os.path.exists(index_html_path):
                    try:
                        with open(index_html_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        # Extract title tag
                        title_match = re.search(r'<title>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
                        title = title_match.group(1).strip() if title_match else "No title found"
                        # Include first 5 lines for quick context
                        lines = content.split('\n')[:5]
                        first_lines = '\n'.join(line.strip() for line in lines if line.strip())
                        context += f"\nIndex.html Title: {title}\nFirst lines:\n{first_lines}\n"
                    except Exception as e:
                        context += f"\nError reading index.html: {str(e)}\n"
                
                context += "\n[QUERY-SPECIFIC DETAILS]\n"
                
                # Analyze query for keywords
                query_lower = user_input.lower()
                matched_files = []
                matched_symbols = []
                
                # Check for file names
                for fname in workspace_index['files']:
                    if fname.lower() in query_lower or os.path.splitext(fname)[0].lower() in query_lower:
                        matched_files.append(fname)
                
                # Check for symbols
                for symbol in workspace_index['search']:
                    if symbol.lower() in query_lower:
                        matched_symbols.append(symbol)
                
                # If matches, add LOD details
                if matched_files:
                    context += "Relevant files:\n"
                    for fname in matched_files[:3]:
                        meta = workspace_index['files'][fname]
                        context += f"  • {fname}: {meta['lines']} lines, {meta['ext']}\n"
                        if meta['lines'] < 50:
                            try:
                                full_path = os.path.join(target_directory, fname)
                                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                                    preview = f.read()[:500]
                                context += f"    Preview: {preview}...\n"
                            except:
                                pass
                
                if matched_symbols:
                    context += "Relevant symbols:\n"
                    for symbol in matched_symbols[:5]:
                        files = workspace_index['search'][symbol][:3]
                        context += f"  • {symbol}: found in {', '.join(files)}\n"
                
                if not matched_files and not matched_symbols:
                    context += "No specific matches for query. Showing general overview above.\n"
                
                return context

            # --- START UNIFIED LOOP ---
            current_input = user_input
            active_history = chat_history if mode == "agent" else ask_history
            
            if mode == "chat":
                TaskState["system_state"]["current_phase"] = "Chat"
            else:
                TaskState["system_state"]["current_phase"] = "Scout" 
            
            while True:
                sys.stdout.write(f"{Colors.YELLOW}Thinking ... {Colors.RESET}\r")
                sys.stdout.flush()

                # --- BATCH 1.5 & 4.4: THE HELMET & CONTEXT ROUTING ---
                try:
                    phase = TaskState["system_state"].get("current_phase", "Scout")
                    helmet_prompt = f"\n=== ACTIVE HELMET PHASE: {phase.upper()} ===\n"
                    
                    # Context routing for Scout AND Chat
                    if phase in ["Scout", "Chat"] and current_input == user_input:
                        if phase == "Chat":
                            context = f"Project: {os.path.basename(target_directory)}\nFiles: {list(workspace_index['files'].keys())}\nSkeleton:\n{workspace_index.get('ast_skeleton', '')}"
                            helmet_prompt += f"\n[WORKSPACE CONTEXT]\n{context}\n"
                        try:
                            from tools.mapper import get_project_skeleton, semantic_search
                            _, index_data = get_project_skeleton(target_directory, TaskState["system_state"].get("file_heat", {}))
                            search_res = semantic_search(user_input, index_data, top_n=2)
                            auto_context = search_res.get("context_string", "")
                            if "No strong matches" not in auto_context and "Error" not in auto_context:
                                helmet_prompt += f"\n[AUTO-RETRIEVED CONTEXT based on your task]\n{auto_context}\n"
                        except Exception: pass

                    if phase == "Scout":
                        helmet_prompt += "MODE: EXPLORE. You may ONLY use 'read_file', 'scan_directory', 'search_in_file', 'semantic_search'. If you are ready to execute an edit, use 'update_state' to transition to PLAN.\n"
                    elif phase == "Chat":
                        helmet_prompt += "MODE: CHAT. You are a conversational codebase assistant. You may use 'read_file', 'scan_directory', 'search_in_file', 'semantic_search' to find answers. DO NOT use edit tools. You MUST return JSON. Put your answer in the 'reply' field.\n"
                    elif phase == "Plan":
                        helmet_prompt += "MODE: PLAN. You MUST immediately output a JSON array containing the 'update_state' tool. Do NOT write long thoughts. Do NOT write code.\n"
                    elif phase == "Execute":
                        helmet_prompt += "MODE: EXECUTE. Follow your plan. You may use edit tools and 'run_command'. You may use 'read_file' to verify lines.\n"
                    else:
                        raise ValueError(f"Unknown phase: {phase}")
                        
                    augmented_input = f"{helmet_prompt}\n[SYSTEM EVENT / USER INPUT]\n{current_input}"
                except Exception as e:
                    print(f"{Colors.RED}[ERR-HELMET-01] Failed to inject helmet phase: {e}{Colors.RESET}")
                    augmented_input = current_input

                # call brain (Block 1 output expected)
                response = get_llm_response(augmented_input, active_history, target_directory, llm_mode, 
                                          current_file=None, is_write_operation=(mode == "agent"), task_state=TaskState, chat_mode=(mode == "chat"))
                
                sys.stdout.write('' * 20 + '\r')
                sys.stdout.flush()

                # If brain returned invalid, show error and feed back to brain via chat_history
                if not response.get("valid", False):
                    TaskState["system_state"]["retry_count"] += 1
                    err = response.get("error", "Unknown parse error from brain.")
                    print(f"{Colors.RED}Brain Error ({TaskState['system_state']['retry_count']}/3): {err}{Colors.RESET}")
                    
                    # --- BATCH 2.6: CONTROLLED RECOVERY ---
                    if TaskState["system_state"]["retry_count"] >= 3:
                        print(f"{Colors.RED}[ERR-RECOVERY-FAIL] Max retries hit. Forcing fallback to start.{Colors.RESET}")
                        TaskState["system_state"]["retry_count"] = 0
                        TaskState["system_state"]["current_phase"] = "Chat" if mode == "chat" else "Scout"
                        _append_with_reminder(active_history, "assistant", "[SYSTEM: Multiple failures occurred. You have been forced to restart. Re-evaluate your approach.]")
                        current_input = "SYSTEM: Multiple failures. Re-evaluate."
                        force_agent_break = True
                        break

                    # feed a short internal clarification back to the brain
                    _append_with_reminder(active_history, "assistant", f"CRITICAL FORMAT ERROR: {err}. Fix your JSON.")
                    current_input = f"CRITICAL FORMAT ERROR: {err}. Fix your JSON."
                    continue  # Loop back instantly instead of breaking outer loop
                else:
                    # Reset retry count on successful valid generation
                    TaskState["system_state"]["retry_count"] = 0

                # Normalize actions list (Block 1 already returns actions)
                actions = response.get("actions", [])
                
                # --- THE "NONE" TOOL NORMALIZATION FIX ---
                for a in actions:
                    if not a.get("tool") or str(a.get("tool")).lower() == "none":
                        a["tool"] = "none"
                    else:
                        a["tool"] = str(a.get("tool")).lower()

                thought = response.get("thought", "")
                agent_reply = response.get("reply", "...")
                _show_brain_work(response)

                pretty_print("D.A.V.E", agent_reply)

                # Check for task completion OR Chat Mode reply
                if not actions or any(a.get("tool") in ("none", "task_complete") for a in actions):
                    # If we are in Agent mode, strictly enforce tests if the flag is on
                    if mode == "agent" and any(a.get("tool") in ("none", "task_complete") for a in actions) and TaskState["system_state"].get("flag_tests", False):
                        if not (last_used_tool == "run_command" and last_run_success):
                            _append_with_reminder(active_history, "assistant",
                                "SYSTEM: Task cannot be completed. You have not executed 'run_command' to verify your code works.")
                            current_input = "SYSTEM: Task cannot be completed. You have not executed 'run_command' to verify your code works."
                            continue 
                            
                    _append_with_reminder(active_history, "user", current_input)
                    _append_with_reminder(active_history, "assistant", agent_reply, RECURRING_REMINDER if mode == "agent" else None)
                    
                    # --- DIFF REPORTING & SYNC: Task completed successfully ---
                    if mode == "agent":
                        changes = _compute_diff(target_directory)
                        if any(changes.values()):
                            print(f"\n{Colors.GREEN}[Workspace Diff Report]{Colors.RESET}")
                            if changes["new"]:
                                new_list = ", ".join(changes["new"][:5])
                                if len(changes["new"]) > 5:
                                    new_list += f" ... (+{len(changes['new'])-5} more)"
                                print(f"  {Colors.CYAN}New:{Colors.RESET} {new_list}")
                            if changes["modified"]:
                                mod_list = ", ".join(changes["modified"][:5])
                                if len(changes["modified"]) > 5:
                                    mod_list += f" ... (+{len(changes['modified'])-5} more)"
                                print(f"  {Colors.YELLOW}Modified:{Colors.RESET} {mod_list}")
                            if changes["deleted"]:
                                del_list = ", ".join(changes["deleted"][:5])
                                if len(changes["deleted"]) > 5:
                                    del_list += f" ... (+{len(changes['deleted'])-5} more)"
                                print(f"  {Colors.RED}Deleted:{Colors.RESET} {del_list}")
                            print()
                        # Refresh workspace index for next cycle
                        workspace_index = _build_workspace_index(target_directory)
                    
                    break # BUGFIX: Properly aligned to break the agent loop and return to user prompt!
                    
                read_results = {}
                for a in actions:
                    if a.get("tool") == "read_file":
                        filename = a.get("filename")
                        start_line = a.get("start_line")
                        end_line = a.get("end_line")
                        
                        if not filename:
                            continue  # Let the downstream executor handle the error gracefully
                        
                        print(f"{Colors.YELLOW}[System] Reading {filename} ... {Colors.RESET}")
                        read_results[filename] = read_file_with_lines(filename, target_directory, start_line, end_line)

                # Track a short summary for non-edit tools
                cli_summary = []
                force_agent_break = False

                # Execute remaining actions in order
                for a in actions:
                    tool_requested = a.get("tool")
                    filename = a.get("filename")
                    old_filename = a.get("old_filename")
                    new_filename = a.get("new_filename")
                    start_line = a.get("start_line")
                    end_line = a.get("end_line")
                    new_code = a.get("new_code")
                    func_name = a.get("func_name")
                    command = a.get("command")
                    search_query = a.get("search_query")
                    action_result = None

                    # Chat Mode Guard: Prevent any edits while chatting
                    if mode == "chat" and tool_requested not in ["read_file", "scan_directory", "search_in_file", "semantic_search", "none", "task_complete"]:
                        if tool_requested in ["create_file", "replace_lines", "rewrite_file", "replace_named_block", "insert_before_symbol", "insert_after_symbol", "rename_file", "delete_file"]:
                            action_result = f"[ERR-READ-ONLY] You are in Chat Mode. Edit tool '{tool_requested}' is blocked. Tell the user to switch to Agent mode to make edits."
                        else:
                            action_result = f"[ERR-UNKNOWN-TOOL] Tool '{tool_requested}' does not exist. You may ONLY use 'read_file', 'scan_directory', 'search_in_file', or 'semantic_search'."
                        
                        print(f"{Colors.RED}{action_result}{Colors.RESET}")
                        _append_with_reminder(active_history, "assistant", f"{agent_reply}\n[TOOL_RESULT]: {action_result}")
                        break

                    # --- BATCH 5.2: THE DECISION GUARD ---
                    try:
                        current_phase = TaskState["system_state"]["current_phase"]
                        if current_phase == "Plan" and tool_requested not in ["update_state", "none"]:
                            action_result = f"[ERR-PHASE-VIOLATION] You are in the PLAN phase. You MUST use 'update_state' to lock in your strategy before using '{tool_requested}'."
                            print(f"{Colors.RED}{action_result}{Colors.RESET}")
                            _append_with_reminder(active_history, "assistant", f"{agent_reply}\n[TOOL_RESULT]: {action_result}")
                            break  # Feed error back and abort batch
                    except KeyError:
                        pass # Failsafe if TaskState isn't perfectly initialized

                    # --- BATCH 5.5 & 5.6: HYBRID CONFIDENCE & IDEMPOTENCY GUARD ---
                    current_func = func_name or ""
                    current_cmd = command or ""
                    # Hash the new_code to ensure we catch identical edits
                    code_hash = hashlib.md5(new_code.encode()).hexdigest()[:8] if new_code else "none"
                    action_signature = f"{tool_requested}_{filename}_{current_func}_{current_cmd}_{code_hash}"

                    # 1. The Idempotency Check (Block identical failures instantly)
                    if action_signature == TaskState["system_state"].get("last_failing_signature") and tool_requested not in ["read_file", "scan_directory"]:
                        action_result = f"[ERR-IDEMPOTENCY] Blocked. You just tried this EXACT action and it failed. System Confidence dropped to 0.0."
                        print(f"{Colors.RED}{action_result}{Colors.RESET}")
                        TaskState["system_state"]["confidence"] = 0.0
                        TaskState["system_state"]["current_phase"] = "Chat" if mode == "chat" else "Scout"
                        _append_with_reminder(active_history, "assistant", f"{agent_reply}\n[TOOL_RESULT]: {action_result}")
                        break # Abort batch

                    # 2. Hard Loop Tracker (The 5-strike manual failsafe)
                    action_tracker.append(action_signature)
                    if len(action_tracker) > 5:
                        action_tracker.pop(0)
                    if len(action_tracker) == 5 and len(set(action_tracker)) == 1:
                        print(f"{Colors.RED}BEHAVIORAL LOOP DETECTED: D.A.V.E. repeated the exact same action 5 times.{Colors.RESET}")
                        print(f"{Colors.YELLOW}Pausing for manual intervention. You are back in control.{Colors.RESET}")
                        action_tracker = []
                        force_agent_break = True
                        break  # abort batch if loop detected

                    # 1. READ & EXPLORE TOOLS
                    if tool_requested == "scan_directory":
                        print(f"{Colors.YELLOW}[System] Scanning workspace ... {Colors.RESET}")
                        action_result = scan_directory(target_directory)
                        cli_summary.append(f"Scanned workspace")
                    elif tool_requested == "read_file":
                        if not filename:
                            action_result = "Error: Missing 'filename'. If you are trying to read multiple files, you MUST specify the 'filename' parameter for EVERY single 'read_file' action in your array. Do not leave it blank."
                        else:
                            file_path = os.path.join(target_directory, filename)
                            if os.path.isdir(file_path):
                                action_result = f"Error: '{filename}' is a directory, not a file."
                            else:
                                action_result = read_results.get(filename, f"Error: Could not read {filename}")
                                # --- BATCH 2.5: LOG OBSERVED FILE ---
                                if "Error:" not in action_result and filename not in TaskState["system_state"]["observed_files"]:
                                    TaskState["system_state"]["observed_files"].append(filename)

                    elif tool_requested == "search_in_file":
                        if filename and search_query:
                            print(f"{Colors.YELLOW}[System] Searching for '{search_query}' in {filename} ... {Colors.RESET}")
                            action_result = search_in_file(filename, search_query, target_directory)
                            cli_summary.append(f"Searched '{search_query}' in {filename}")
                        else:
                            action_result = 'Error: Missing arguments. You MUST provide both. Example: {"tool": "search_in_file", "filename": "ALL", "search_query": "your_search_term"}'

                    # --- BATCH 5.4: WORKING MEMORY TOOLS ---
                    elif tool_requested == "pin_snippet":
                        filename = a.get("filename")
                        content = a.get("content")
                        description = a.get("description", "Code snippet")
                        if filename and content:
                            # --- ANTI-DUPLICATE GUARD ---
                            is_duplicate = any(s.get("content") == content for s in TaskState["system_state"]["pinned_snippets"])
                            if is_duplicate:
                                action_result = "Snippet is already pinned in Working Memory. No need to pin again."
                            else:
                                print(f"{Colors.YELLOW}[System] Pinning {len(content.splitlines())} lines from {filename} to Working Memory ... {Colors.RESET}")
                                TaskState["system_state"]["pinned_snippets"].append({
                                    "filename": filename,
                                    "description": description,
                                    "content": content
                                })
                                action_result = f"Successfully pinned snippet to Working Memory. It will remain in your context until unpinned."
                                cli_summary.append(f"Pinned memory: {description}")
                        else:
                            action_result = "Error: Missing filename or content."
                            
                    elif tool_requested == "unpin_snippet":
                        TaskState["system_state"]["pinned_snippets"] = []
                        action_result = "Cleared all pinned snippets from Working Memory."
                        cli_summary.append("Cleared Working Memory")

                    # 2. EDITING TOOLS (require audit)
                    elif tool_requested in ["create_file", "replace_lines", "rewrite_file", "replace_named_block", "insert_before_symbol", "insert_after_symbol"]:
                        
                        # --- BATCH 3.4: INTENT-BASED AUDIT LOCK ---
                        edit_intent = a.get("edit_intent")
                        if not edit_intent:
                            action_result = "[ERR-INTENT-MISSING] Blocked. You MUST provide an 'edit_intent' string explaining your edit."
                            print(f"{Colors.RED}{action_result}{Colors.RESET}")
                            TaskState["system_state"]["current_phase"] = "Plan"
                            _append_with_reminder(active_history, "assistant", f"{agent_reply}\n[TOOL_RESULT]: {action_result}")
                            break  # Feed error back and abort batch

                        # --- BATCH 2.5: RUNTIME READ-BEFORE-WRITE GUARD ---
                        if filename not in TaskState["system_state"]["observed_files"] and tool_requested != "create_file":
                            action_result = f"[ERR-BLIND-EDIT] Blocked. You attempted to edit '{filename}' without reading it first."
                            print(f"{Colors.RED}{action_result}{Colors.RESET}")
                            TaskState["system_state"]["current_phase"] = "Scout"
                            _append_with_reminder(active_history, "assistant", f"{agent_reply}\n[TOOL_RESULT]: {action_result}")
                            break  # Feed error back and abort batch

                        # --- SAFE EDIT EXECUTIONS ---
                        if tool_requested == "create_file":
                            if filename and new_code is not None:
                                print(f"{Colors.YELLOW}[System] Creating {filename} ... {Colors.RESET}")
                                action_result = create_file(filename, new_code, target_directory)
                                cli_summary.append(f"Created {filename}")
                                _append_with_reminder(active_history, "assistant", f"{agent_reply}\n[TOOL_RESULT]: {action_result}", _get_language_reminder(filename))
                            else:
                                action_result = "Error: Missing filename or new_code."

                        elif tool_requested == "replace_lines":
                            if all([filename, start_line, end_line]) and new_code is not None:
                                print(f"{Colors.RED}D.A.V.E. requests edit: {filename}{Colors.RESET}")
                                create_backup(filename, target_directory)
                                action_result = replace_lines(filename, start_line, end_line, new_code, target_directory, edit_intent)
                                cli_summary.append(f"Edited {filename} (lines {start_line}-{end_line})")
                                _append_with_reminder(active_history, "assistant", action_result, _get_language_reminder(filename))
                            else:
                                action_result = "Error: Missing arguments for replace_lines."

                        elif tool_requested == "rewrite_file":
                            if filename and new_code is not None:
                                print(f"{Colors.RED}D.A.V.E. requests full rewrite: {filename}{Colors.RESET}")
                                create_backup(filename, target_directory)
                                action_result = rewrite_file(filename, new_code, target_directory, edit_intent)
                                cli_summary.append(f"Rewrote {filename}")
                                _append_with_reminder(active_history, "assistant", action_result, _get_language_reminder(filename))
                            else:
                                action_result = "Error: Missing filename or new_code."

                        elif tool_requested == "replace_named_block":
                            symbol_name = a.get("symbol_name")
                            if filename and symbol_name and new_code is not None:
                                print(f"{Colors.RED}D.A.V.E. replacing block '{symbol_name}' in {filename}{Colors.RESET}")
                                create_backup(filename, target_directory)
                                action_result = replace_named_block(filename, symbol_name, new_code, target_directory, edit_intent)
                                cli_summary.append(f"Replaced block {symbol_name} in {filename}")
                                _append_with_reminder(active_history, "assistant", action_result, _get_language_reminder(filename))
                            else:
                                action_result = "Error: Missing arguments for replace_named_block."
                                
                        elif tool_requested == "insert_before_symbol":
                            symbol_name = a.get("symbol_name")
                            if filename and symbol_name and new_code is not None:
                                print(f"{Colors.RED}D.A.V.E. inserting before '{symbol_name}' in {filename}{Colors.RESET}")
                                create_backup(filename, target_directory)
                                action_result = insert_before_symbol(filename, symbol_name, new_code, target_directory, edit_intent)
                                cli_summary.append(f"Inserted before {symbol_name} in {filename}")
                                _append_with_reminder(active_history, "assistant", action_result, _get_language_reminder(filename))
                            else:
                                action_result = "Error: Missing arguments for insert_before_symbol."

                        elif tool_requested == "insert_after_symbol":
                            symbol_name = a.get("symbol_name")
                            if filename and symbol_name and new_code is not None:
                                print(f"{Colors.RED}D.A.V.E. inserting after '{symbol_name}' in {filename}{Colors.RESET}")
                                create_backup(filename, target_directory)
                                action_result = insert_after_symbol(filename, symbol_name, new_code, target_directory, edit_intent)
                                cli_summary.append(f"Inserted after {symbol_name} in {filename}")
                                _append_with_reminder(active_history, "assistant", action_result, _get_language_reminder(filename))
                            else:
                                action_result = "Error: Missing arguments for insert_after_symbol."
                                
                        # --- ADVISOR ROADMAP: INTELLIGENT RE-READ SUGGESTIONS ---
                        if tool_requested in ["replace_lines", "rewrite_file", "replace_named_block", "insert_before_symbol", "insert_after_symbol"] and isinstance(action_result, str) and "Error:" not in action_result:
                            # Extract the name of the symbol D.A.V.E. just edited
                            edited_symbol = a.get("symbol_name") or a.get("func_name")
                            if edited_symbol and workspace_index.get("ast_skeleton"):
                                dependent_files = []
                                # Scan the semantic relationship graph for dependent files
                                for line in workspace_index["ast_skeleton"].splitlines():
                                    if "calls:" in line and edited_symbol in line:
                                        # Extract the filename from the skeleton line
                                        match = re.search(r'- ([\w\.\/\-]+)', line)
                                        if match:
                                            dep_file = match.group(1)
                                            # Only warn if he hasn't read the dependent file yet
                                            if dep_file not in TaskState["system_state"]["observed_files"] and dep_file != filename:
                                                dependent_files.append(dep_file)
                                
                                if dependent_files:
                                    suggestion = f"\n[SYSTEM: The edited symbol '{edited_symbol}' is called by {', '.join(dependent_files[:2])}. Consider using 'read_file' on them to ensure your changes didn't break their implementation.]"
                                    action_result += suggestion
                                    print(f"{Colors.YELLOW}{suggestion}{Colors.RESET}")

                    # 3. FILE MANAGEMENT TOOLS

                    elif tool_requested == "rename_file":
                        if old_filename and new_filename:
                            print(f"{Colors.YELLOW}[System] Renaming {old_filename} to {new_filename} ... {Colors.RESET}")
                            action_result = rename_file(old_filename, new_filename, target_directory)
                            cli_summary.append(f"Renamed {old_filename} -> {new_filename}")
                        else:
                            action_result = "Error: Missing arguments for rename_file."

                    elif tool_requested == "delete_file":
                        if filename:
                            # keep interactive confirmation for deletes
                            if input(f"{Colors.RED}Confirm DELETE {filename}? (y/n): {Colors.RESET}").strip().lower() == 'y':
                                print(f"{Colors.YELLOW}[System] Deleting {filename} ... {Colors.RESET}")
                                action_result = delete_file(filename, target_directory)
                                cli_summary.append(f"Deleted {filename}")
                            else:
                                action_result = "User blocked deletion."
                        else:
                            action_result = "Error: Missing filename for delete_file."

                    # 4. EXECUTION & PLANNING TOOLS
                    elif tool_requested == "update_state":
                        analysis = a.get("analysis", "")
                        options = a.get("options", [])
                        decision = a.get("decision", "")
                        reason = a.get("reason", "")
                        confidence = a.get("confidence", 0.0)

                        # Update Bimodal State (LLM Notes)
                        TaskState["llm_notes"] = {
                            "analysis": analysis,
                            "options": options,
                            "decision": decision,
                            "reason": reason,
                            "confidence": confidence
                        }

                        # Observable Reasoning UI Feed (Does NOT go into chat_history verbatim)
                        print(f"\n{Colors.CYAN}[D.A.V.E THINKING]{Colors.RESET}")
                        print(f"  {Colors.YELLOW}Analysis:{Colors.RESET} {analysis}")
                        print(f"  {Colors.YELLOW}Options:{Colors.RESET} {', '.join(options) if isinstance(options, list) else options}")
                        print(f"  {Colors.GREEN}Decision:{Colors.RESET} {decision} (Confidence: {confidence})")
                        print(f"  {Colors.YELLOW}Reason:{Colors.RESET} {reason}\n")

                        action_result = "State updated successfully. Proceed with the decision."
                        cli_summary.append(f"Updated Plan -> Next Tool: {decision}")

                    elif tool_requested == "run_command":
                        if command:
                            print(f"{Colors.YELLOW}D.A.V.E. wants to run: {command}{Colors.RESET}")
                            if input(f"{Colors.YELLOW}Allow execution? (y/n): {Colors.RESET}").strip().lower() == 'y':
                                # quote any python file path that contains spaces
                                if command.startswith("python"):
                                    parts = command.split(maxsplit=1)
                                    if len(parts) > 1 and " " in parts[1] and not parts[1].startswith(("\"", "'")):
                                        parts[1] = f'"{parts[1]}"'
                                        command = " ".join(parts)
                                action_result = run_command(command, target_directory)
                                cli_summary.append(f"Ran: {command}")
                                # immediate tracking in case the next batch contains completion
                                last_used_tool = "run_command"
                                last_run_success = isinstance(action_result, str) and "(Success)" in action_result
                                
                                # --- BATCH 1.2: SYSTEM AUTO-UPDATE HOOK ---
                                TaskState["system_ground_truth"]["last_command"] = command
                                TaskState["system_ground_truth"]["exit_code"] = 0 if last_run_success else 1
                                TaskState["system_ground_truth"]["raw_stderr"] = action_result if not last_run_success else ""
                                
                                # binary-search failure reminder
                                if not last_run_success:
                                    _append_with_reminder(active_history, "assistant",
                                        action_result,
                                        "Command failed; print only the traceback then wait.")
                            else:
                                action_result = "User REJECTED command execution."
                        else:
                            action_result = "Error: No command provided."

                    elif tool_requested == "manage_plan":
                        action = a.get("action")
                        data = a.get("data", "")
                        if action:
                            print(f"{Colors.YELLOW}[System] Accessing Master Plan ({action}) ... {Colors.RESET}")
                            action_result = manage_plan(action, data, target_directory)
                        else:
                            action_result = "Error: Missing 'action' argument for manage_plan."

                    elif tool_requested == "semantic_search":
                        query = a.get("query")
                        if query:
                            print(f"{Colors.YELLOW}[System] Semantic search for '{query}' ... {Colors.RESET}")
                            try:
                                # Use the already imported semantic_search
                                search_res = semantic_search(query, workspace_index.get("index_data", {}))
                                action_result = search_res.get("context_string", "No results.")
                                cli_summary.append(f"Semantic search: '{query}'")
                            except Exception as e:
                                action_result = f"Error: {str(e)}"
                        else:
                            action_result = "Error: Missing 'query'."
                    else:
                        action_result = f"Error: Unknown tool '{tool_requested}'."

                    # Print a short snippet of the result
                    snippet = action_result[:300] + " ... " if isinstance(action_result, str) and len(action_result) > 300 else action_result
                    print(f"{Colors.CYAN}{snippet}{Colors.RESET}\n")

                    # --- BATCH 5.6: RECORD FAILURES ---
                    if isinstance(action_result, str) and (action_result.startswith("Error:") or action_result.startswith("[ERR-") or action_result.startswith("CRITICAL:") or "STATUS: FAILURE" in action_result):
                        TaskState["system_state"]["last_failing_signature"] = action_signature
                        TaskState["system_state"]["confidence"] = max(0.0, TaskState["system_state"].get("confidence", 1.0) - 0.5)
                    else:
                        # Clear it if he does something successful
                        if tool_requested not in ["read_file", "scan_directory", "update_state", "none"]:
                             TaskState["system_state"]["last_failing_signature"] = None
                             TaskState["system_state"]["confidence"] = 1.0

                    # Append to chat history for context with recency reminder
                    _append_with_reminder(active_history, "user", current_input)
                    _append_with_reminder(active_history, "assistant", f'{agent_reply}\n[TOOL_RESULT]: {action_result}', RECURRING_REMINDER if mode == "agent" else None)
                    # update proof-of-work tracking
                    last_used_tool = tool_requested
                    if tool_requested == "run_command":
                        last_run_success = isinstance(action_result, str) and "(Success)" in action_result
                    else:
                        # reset success when next tool isn't a run
                        last_run_success = False

                    # If the tool was non-edit and succeeded, we add to CLI summary (already done)
                    # Continue to next action

                # End actions loop
                if force_agent_break:
                    break  # Kill the outer Agent loop and return to User prompt

                # After batch: show concise CLI summary for non-edit changes
                if cli_summary:
                    print(f"{Colors.GREEN}Summary of changes:{Colors.RESET}")
                    for s in cli_summary:
                        print(f" - {s}")
                    print()

                # --- BATCH 5.3 & 5.5: FAILURE ROUTING & CONFIDENCE GATING ---
                try:
                    current_phase = TaskState["system_state"]["current_phase"]
                    sys_confidence = TaskState["system_state"].get("confidence", 1.0)
                    next_input = ""
                    tools_used = [a.get("tool") for a in actions]
                    
                    # --- BATCH 5.5: CONFIDENCE GATE ---
                    if sys_confidence < 0.6 and current_phase not in ["Scout", "Chat"] and TaskState["system_state"].get("flag_confidence", True):
                        TaskState["system_state"]["current_phase"] = "Scout"
                        TaskState["system_state"]["confidence"] = 1.0 # Reset for the next attempt
                        next_input = "[SYSTEM WARNING] System confidence critically low due to repeated failures. Forced transition to SCOUT phase. Use 'read_file' to understand what went wrong."
                    
                    elif current_phase == "Chat":
                        next_input = f"Tool execution finished. If you need to read another file to fully answer '{user_input}', output another tool action. If you encountered an error, fix your arguments and try again. Once you have all the context, deliver your final answer in the 'reply' field and leave actions empty []. DO NOT repeat your introduction."
                    elif current_phase == "Scout":
                        TaskState["system_state"]["current_phase"] = "Plan"
                        next_input = "Scouting finished. Transitioning to PLAN phase. You MUST use 'update_state' to formulate your strategy."
                        
                    elif current_phase == "Plan":
                        if "update_state" in tools_used:
                            TaskState["system_state"]["current_phase"] = "Execute"
                            next_input = "Plan accepted. Transitioning to EXECUTE phase. Execute your approved tool."
                        else:
                            next_input = "[ERR-PHASE-02] You are in PLAN phase but did not use 'update_state'. You MUST log your plan."
                            
                    elif current_phase == "Execute":
                        if "run_command" in tools_used:
                            # Route based on the normalized terminal output
                            if isinstance(action_result, str) and "STATUS: SUCCESS" in action_result:
                                next_input = "Execution successful. Observe results. If done, use 'task_complete'."
                            elif isinstance(action_result, str) and "ERROR_TYPE: Syntax" in action_result:
                                next_input = "Syntax Error detected. Remain in EXECUTE phase to apply a patch."
                            elif isinstance(action_result, str) and "ERROR_TYPE: Timeout" in action_result:
                                TaskState["system_state"]["current_phase"] = "Scout"
                                next_input = "Timeout Error. Transitioning back to SCOUT phase. Remove the blocking input() or infinite loop."
                            elif isinstance(action_result, str) and "ERROR_TYPE:" in action_result:
                                TaskState["system_state"]["current_phase"] = "Scout"
                                next_input = "Logic/System Error detected. Transitioning back to SCOUT phase. Re-evaluate your approach."
                            else:
                                next_input = "Execution finished. Observe results. If done, use 'task_complete'."
                        else:
                            next_input = "Tool execution finished. Observe results. If done, use 'task_complete'. Otherwise, run_command to verify."

                except KeyError:
                    next_input = "Tool execution finished. Proceed."
                    
                if TaskState["system_state"].get("flag_debug", False):
                    print(f"{Colors.CYAN}[DEBUG] Phase: {current_phase} -> {TaskState['system_state']['current_phase']} | Conf: {sys_confidence} | Next: {next_input[:50]}...{Colors.RESET}")

                # small warning for replace_lines on python files
                if any(a.get("tool") == "replace_lines" and str(a.get("filename","")).endswith('.py') for a in actions):
                    next_input += " (SYSTEM WARNING: You used 'replace_lines' on a Python file. Use 'replace_function' or 'rewrite_file' next time to avoid breaking indentation.)"

                # Plan shock collar behavior
                if any(a.get("tool") == "manage_plan" and a.get("action") in ["create", "read"] for a in actions):
                    next_input += " (SYSTEM COMMAND: Plan accessed. DO NOT use 'manage_plan' again on your next turn. You MUST immediately execute the code for the next step.)"

                current_input = next_input
                # loop back to let brain process the results
                continue

            # --- BATCH 1.4: DYNAMIC HISTORY TRUNCATION ---
            # trim chat history drastically to force reliance on TaskState
            # 10 items = 5 user/assistant pairs
            if len(active_history) > 10:
                if mode == "agent":
                    chat_history = chat_history[-10:]
                else:
                    ask_history = ask_history[-10:]

        except KeyboardInterrupt:
            print(f"{Colors.YELLOW}Force quitting ... {Colors.RESET}")
            break

if __name__ == "__main__":
    main()