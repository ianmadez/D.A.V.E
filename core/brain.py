import os
import re
import json
import requests
from dotenv import load_dotenv
from openai import OpenAI
from core.memory import load_knowledge_base

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
dotenv_path = os.path.join(ROOT_DIR, '.env')
load_dotenv(dotenv_path)

OLLAMA_URL = os.getenv("OLLAMA_URL")
MODEL = os.getenv("MODEL")
FREELLMAPI_KEY = os.getenv("FREELLMAPI_KEY")

# === Extended timeout for local Ollama (allows full inference) ===
OLLAMA_TIMEOUT = (30.0, 300.0)  # (connect timeout, read timeout)

# === Validation / constraints ===
LAZY_MARKERS = [
    "# ...", "TODO:", "# logic here", "# rest of code",
    "/* ... */", "// ... existing code", "# implement", "/* ... * /"
]
WRITE_TOOLS = {"rewrite_file", "create_file", "replace_lines", "replace_named_block", "insert_before_symbol", "insert_after_symbol"}
CONTRACTION_RE = re.compile(r"\b\w+'\w+\b")


def extract_first_json_object(text: str):
    in_string = False
    escape = False
    depth = 0
    start = None

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue

        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidate = text[start:i + 1]
                    try:
                        parsed = json.loads(candidate, strict=False)
                        if isinstance(parsed, dict):
                            return {"parsed": parsed, "raw": text}
                    except Exception:
                        start = None
    return None

def safe_json_parse(text: str) -> dict:
    if not text:
        return {"error": "Empty response", "raw": text}
        
    # --- ADVISOR ROADMAP: QUOTE-AWARE JSON EXTRACTION ---
    extracted = extract_first_json_object(text)
    if extracted:
        return extracted

    # Secondary fallback: widest brace matching catch
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start:end+1], strict=False)
            if isinstance(parsed, dict):
                return {"parsed": parsed, "raw": text}
        except Exception:
            pass
            
    # Final cleanup fallback if structure fails completely
    clean_text = re.sub(r'\{[\s\S]*\}', '', text).strip()
    return {"parsed": None, "raw": text, "thought_fallback": clean_text if clean_text else text}


def _extract_thought(raw: str) -> str:
    m = re.search(r"<thought>([\s\S]*?)</thought>", raw, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _extract_code_block(text: str) -> str:
    # find triple-backtick blocks first
    m = re.search(r"```(?:\w+)?\n([\s\S]*?)\n```", text)
    if m:
        return m.group(1)
    # fallback: look for lines that look like code (heuristic)
    lines = text.splitlines()
    code_lines = [ln for ln in lines if ln.strip().startswith(("def ", "class ", "import ", "print(", "if ", "for ", "while ", "#")) or ('=' in ln and '"' in ln or "'" in ln)]
    if code_lines:
        return "\n".join(code_lines)
    return ""


def _contains_lazy_marker(code: str) -> bool:
    """Check if code contains placeholder or incomplete markers."""
    if not code:
        return False
    lower = code.lower()
    return any(m.lower() in lower for m in LAZY_MARKERS)

# --- ADVISOR ROADMAP: LIGHTWEIGHT VALIDATION PROFILES ---
def _validate_structural_expectations(tool: str, filename: str, code: str) -> str:
    """Checks generated code against lightweight structural expectations."""
    if not code or tool not in ["create_file", "rewrite_file", "replace_named_block", "insert_before_symbol", "insert_after_symbol"]:
        return ""
        
    lines = [l.strip() for l in code.split('\n') if l.strip() and not l.strip().startswith('#')]
    
    # 1. The "Fake Implementation" Check (Stub Detector)
    if len(lines) <= 3 and any(l.startswith(("pass", "return None", "print(", "console.log")) for l in lines):
        return "[SYSTEM ERROR: Proposed implementation appears reductive or incomplete. You cannot replace complex blocks with simple stubs. Expand your implementation.]"

    # 2. Lightweight Language Profiles
    ext = filename.split('.')[-1].lower() if filename else ""
    code_lower = code.lower()
    
    if tool == "create_file":
        if ext == "py":
            # Expect basic structure in standalone scripts
            if "import" not in code_lower and "def " not in code_lower and "class " not in code_lower:
                return "[SYSTEM ERROR: Python file creation lacks basic structure. Expected imports, classes, or functions.]"
        elif ext in ["jsx", "tsx"]:
            # Expect React components to actually return UI
            if "export" not in code_lower or "return" not in code_lower:
                return "[SYSTEM ERROR: React component lacks an 'export' statement or a 'return' block.]"
                
    if tool in ["replace_named_block", "insert_before_symbol", "insert_after_symbol"]:
        if ext == "py" and "def " not in code and "async def" not in code and "class " not in code:
            return "[SYSTEM ERROR: You used a block modification tool but didn't output a valid function or class definition.]"

    return ""

def _normalize_actions(parsed: dict) -> list:
    if not parsed:
        return []
    if isinstance(parsed.get("actions"), list):
        return parsed.get("actions")
    if parsed.get("tool"):
        return [parsed]
    return []


def _detect_fix_keyword(user_text: str) -> bool:
    return any(k in user_text.lower() for k in ("fix", "rewrite", "repair", "patch", "refactor"))


def _detect_new_task(user_text: str) -> bool:
    return bool(re.search(r"^(new task:|start (a|an)|begin|fresh task|reset)", user_text.strip(), re.IGNORECASE))


def _sniff_filename(user_text: str) -> str:
    m = re.search(r'[\w\-]+\.(py|html|js|css)', user_text.lower())
    return m.group(0) if m else ""


def _openrouter_fallback(messages):
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
    if not openrouter_api_key:
        return ""
        
    # The Waterfall: If one fails, it instantly tries the next.
    fallback_models = [
        "nousresearch/hermes-3-llama-3.1-405b:free", # The Heavyweight Genius
        "deepseek/deepseek-v4-flash:free",          # The Speed Demon
        "minimax/minimax-m2.5:free",                # The Agent Specialist
        "google/gemma-4-31b-it:free",               # The Tool-Calling Backup
        "deepseek/deepseek-chat:free"               # The Ol' Reliable
    ]
    
    last_error = ""
    for model_id in fallback_models:
        payload = {
            "model": model_id,
            "messages": messages,
        }
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_api_key}",
                    "Content-Type": "application/json"
                },
                data=json.dumps(payload),
                timeout=OLLAMA_TIMEOUT
            )
            if r.status_code == 200:
                return r.json().get('choices', [{}])[0].get('message', {}).get('content', "")
            else:
                last_error = f"{r.status_code} - {r.text}"
                continue # Try the next model in the list
        except Exception as e:
            last_error = str(e)
            continue
            
    raise Exception(f"All fallback models failed. Last error: {last_error}")


CHAT_SYSTEM_PROMPT = """
You are D.A.V.E.'s Chat Mode, acting as the all-seer of the codebase.
You answer questions and discuss the project naturally. 
CRITICALLY: You CAN use Read-Only tools (read_file, scan_directory, search_in_file, semantic_search) to look up information before answering!
If you need to search or read a file, output a JSON object with an "actions" array.
If you already have the answer, return a JSON object with your answer in "reply" and "actions": [].
You CANNOT edit files in this mode.
"""


def get_llm_response(user_text, chat_history, target_directory, llm_mode="local",
                     current_file=None, is_write_operation=False, chat_mode=False, task_state=None):
    """Routes the prompt to either the local model or the Cloud API and returns a structured response."""
    
    if "fix" in user_text.lower() or "rewrite" in user_text.lower():
        user_text = f"{user_text} (CRITICAL: You MUST use 'read_file' first to see the current code before writing any fixes.)"

    # Extract file type for knowledge injection (optimized hybrid context routing)
    knowledge = ""
    if is_write_operation or chat_mode:
        knowledge = load_knowledge_base(current_file=current_file, prompt=user_text)

    # === MASTER PROMPT (Unified for 7B Model) ===
    # Generate project skeleton for context minification
    try:
        from tools.mapper import get_project_skeleton
        skeleton, _ = get_project_skeleton(target_directory)
        # --- BATCH 4.4: DIET SKELETON (Only file names, no heavy AST) ---
        skeleton_section = f"\n=== PROJECT FILES ===\n{skeleton}\n"
    except Exception:
        skeleton_section = "\n=== PROJECT FILES ===\n<error loading skeleton>\n"
        
    task_state_section = ""
    if task_state:
        task_state_section = f"\n=== CURRENT TASK STATE ===\n{json.dumps(task_state, indent=2)}\n"
    
    if chat_mode:
        SYSTEM_PROMPT = f"""
ROLE: You are D.A.V.E.'s Chat Mode, acting as the 'big brother' of the codebase.
DIRECTORY: {target_directory}
{task_state_section}
{skeleton_section}

=== CHAT MODE RULES ===
You answer questions and discuss the project naturally.
CRITICALLY: You CAN and SHOULD use Read-Only tools to look up information before answering!
ALLOWED TOOLS: "read_file", "scan_directory", "search_in_file", "semantic_search", "none", "task_complete".
DO NOT INVENT TOOLS. Do not use "peek_file" or anything else.
You CANNOT edit files in this mode.
VERIFICATION RULE: Never blindly agree with the user. If the user corrects you, proposes a file path, or asks you to confirm a fact, you MUST use 'read_file' or 'search_in_file' to verify it is actually true before answering.

=== ACTIONS ARRAY FORMAT (MANDATORY) ===
You MUST ALWAYS respond with a JSON object. Put your conversational answer in the "reply" field.
If the user asks you to search, look at, or scan something, you MUST put the corresponding tool in the "actions" array. DO NOT just say "I will look at the files" without actually using the tool.
If you already have the full answer and just want to talk, leave the "actions" array empty.

{{
    "thought": "Your internal reasoning...",
    "reply": "Your friendly, conversational answer to the user.",
    "actions": []
}}
"""
    else:
        SYSTEM_PROMPT = f"""
    You are D.A.V.E., a local coding agent.
    DIRECTORY: {target_directory}
    {task_state_section}
    {skeleton_section}

    Return ONLY JSON:
        {{
            "thought": "short internal reasoning",
            "reply": "short user-facing status",
            "actions": [
                {{"tool": "tool_name", "param": "value"}}
            ]
        }}

        Rules:
            - For edits, read the target file or symbol first.
            - Prefer 'replace_named_block' for Python functions/classes.
            - Never use 'rewrite_file' unless replacing the whole file.
            - After editing code, run_command to verify it works.
            - If a tool fails, fix the tool arguments or choose a different read/search.
            - Use 'task_complete' ONLY after verification or if the task was read-only.
            - DO NOT repeat introductions.
            {knowledge}
            """
    # --- TOKEN OPTIMIZATION FOR CLOUD API ---
    # Expanded history: 100 local, 80 cloud. Token-based summarisation at 4000 words.
    total_tokens = sum(len(m.get("content","").split()) for m in chat_history)
    
    # Summarise at 4000 tokens
    if total_tokens > 4000:
        half = len(chat_history)//2
        summary = " | ".join([m.get("content","")[:60] for m in chat_history[:half]])
        chat_history = chat_history[half:]
        chat_history.insert(0,{"role":"system","content":f"[SUMMARISED] Earlier work: {summary[:600]}"})
    
    # Message count limits: 100 local, 80 cloud
    max_messages = 80 if llm_mode in ("api", "freellmapi") else 100
    if len(chat_history) > max_messages:
        chat_history = chat_history[-max_messages:]
        chat_history.insert(0,{"role":"system","content":f"[CONTEXT] Conversation truncated to recent {max_messages} messages to preserve context."})

    # --- CONTEXT BUDGETING ---
    # Deduplicate massive tool results (like read_file) to prevent context bloat.
    # We iterate backwards to keep the freshest reads and prune the old ones.
    budgeted_history = []
    seen_tool_results = set()
    
    for msg in reversed(chat_history):
        content = msg.get("content", "")
        # Identify massive tool results (likely full file reads > 500 chars)
        if "[TOOL_RESULT]:" in content and len(content) > 500:
            # Extract a signature (first 100 chars) to detect duplicates
            sig = content[:100]
            if sig in seen_tool_results:
                # Replace the duplicate massive content with a tiny summary
                msg_copy = msg.copy()
                msg_copy["content"] = "[CONTEXT BUDGETING: Duplicate file read removed to save memory. Refer to the most recent read.]"
                budgeted_history.insert(0, msg_copy)
            else:
                seen_tool_results.add(sig)
                budgeted_history.insert(0, msg)
        else:
            budgeted_history.insert(0, msg)
            
    chat_history = budgeted_history

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    # Inject language knowledge ONLY for write operations (not every turn)
    if is_write_operation and knowledge:
        messages.append({"role": "system", "content": f"<knowledge-injection-for-{current_file}>{knowledge[:1500]}</knowledge-injection>"})
    
    for msg in chat_history:
        messages.append(msg)
    messages.append({"role": "user", "content": user_text})

    # --- [API REMOVAL INSTRUCTION START] ---
    if llm_mode == "api":
        try:
            api_key = os.getenv("GROQ_API_KEY")
            if not api_key:
                return {"valid": False, "error": "GROQ_API_KEY not found in .env file.", "actions": [], "raw": "", "thought": ""}
            client = OpenAI(
                api_key=api_key,
                base_url="https://api.groq.com/openai/v1"
            )
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            raw_response = completion.choices[0].message.content
        except Exception as e:
            err = str(e).lower()
            if 'rate limit' in err or '429' in err:
                try:
                    raw_response = _openrouter_fallback(messages)
                except Exception as fallback_exc:
                    return {"valid": False, "error": f"Both Groq and OpenRouter failed: {str(fallback_exc)}", "actions": [], "raw": "", "thought": ""}
            else:
                return {"valid": False, "error": f"API Error: {str(e)}", "actions": [], "raw": "", "thought": "Failed to connect to the cloud API."}
    elif llm_mode == "freellmapi":
        try:
            api_key = os.getenv("FREELLMAPI_KEY")
            if not api_key:
                return {"valid": False, "error": "FREELLMAPI_KEY not found in .env file.", "actions": [], "raw": "", "thought": ""}
            client = OpenAI(
                api_key=api_key,
                base_url="http://localhost:3001/v1"
            )
            completion = client.chat.completions.create(
                model="auto",
                messages=messages,
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            raw_response = completion.choices[0].message.content
        except Exception as e:
            err = str(e).lower()
            if 'rate limit' in err or '429' in err:
                return {"valid": False, "error": f"FreeLLMAPI Error: {str(e)}", "actions": [], "raw": "", "thought": "Failed to connect to the cloud API."}
            else:
                return {"valid": False, "error": f"API Error: {str(e)}", "actions": [], "raw": "", "thought": "Failed to connect to the cloud API."}
    # --- [API REMOVAL INSTRUCTION END] ---
    else:
        payload = {
            "model": MODEL,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.2,
                "num_ctx": 4096
            }
        }
        try:
            response = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
            if response.status_code == 200:
                content = response.json().get("message", {}).get("content", "")
                raw_response = content
            else:
                return {"valid": False, "error": f"Ollama Error {response.status_code}", "actions": [], "raw": "", "thought": ""}
        except Exception as e:
            return {"valid": False, "error": f"Connection failed: {e}", "actions": [], "raw": "", "thought": ""}

    # Force raw_response to be a string to prevent NoneType regex crashes
    raw_response = raw_response or ""
    parsed_block = safe_json_parse(raw_response)
    raw_text = parsed_block.get("raw") or ""
    
    # Safe access to prevent NoneType attribute error
    parsed_dict = parsed_block.get("parsed") or {}
    thought = parsed_dict.get("thought", "") or _extract_thought(raw_text) or parsed_block.get("thought_fallback", "")
    reply = parsed_dict.get("reply", "") if isinstance(parsed_dict, dict) else ""

    parsed = parsed_block.get("parsed")
    
    # ENFORCE ACTIONS ARRAY FORMAT
    if parsed and isinstance(parsed, dict) and "actions" not in parsed and "tool" in parsed:
        # Convert single tool format to actions array for backwards compatibility
        actions = [parsed]
    else:
        actions = _normalize_actions(parsed) if parsed and isinstance(parsed, dict) else []

    # --- ADVISOR ROADMAP: ACTION TYPE SAFEGUARD ---
    # Ensure every element in actions is a dictionary to prevent TypeError item assignment crashes
    clean_actions = []
    for a in actions:
        if isinstance(a, str):
            clean_actions.append({"tool": a})
        elif isinstance(a, dict):
            clean_actions.append(a)
    actions = clean_actions

    # Initialize metadata BEFORE we might need to return it
    metadata = {"amnesia": False, "requires_read_file": False}
    if _detect_new_task(user_text):
        metadata["amnesia"] = True
    
    # --- HYBRID EMPTY ACTION CATCH ---
    if not parsed:
        return {"valid": False, "error": "CRITICAL FORMAT ERROR: Invalid JSON format. This usually happens because you put unescaped line breaks or unescaped quotes inside your 'reply' or 'thought' strings. You MUST use '\n' for newlines. Fix your escaping and output valid JSON.", "actions": [], "raw": raw_text, "thought": thought, "metadata": metadata}
    
    if not actions and not chat_mode:
        return {"valid": False, "error": "CRITICAL FORMAT ERROR: No JSON actions array found. You MUST output a JSON object containing the 'actions' array.", "actions": [], "raw": raw_text, "thought": thought, "metadata": metadata}

    # auto recover new_code
    for a in actions:
        if a.get("tool") in WRITE_TOOLS and not a.get("new_code"):
            recovered = _extract_code_block(raw_text)
            if not recovered:
                reply_text = parsed.get("reply") if parsed else None
                if reply_text:
                    recovered = _extract_code_block(reply_text)
            if recovered:
                a["new_code"] = recovered.replace("\r\n", "\n")

    for a in actions:
        tool = a.get("tool")
        filename = a.get("filename", "")
        if tool in WRITE_TOOLS:
            new_code = a.get("new_code")
            if not new_code:
                return {"valid": False, "error": f"Write action '{tool}' missing 'new_code' field.", "actions": [], "raw": raw_text, "thought": thought, "metadata": metadata}
            if _contains_lazy_marker(new_code):
                return {"valid": False, "error": "Rewrite Nuke Ban triggered: lazy placeholders like '# ...' detected in new_code. You must write complete code.", "actions": [], "raw": raw_text, "thought": thought, "metadata": metadata}
                
            # --- ADVISOR ROADMAP: APPLY VALIDATION PROFILE ---
            validation_error = _validate_structural_expectations(tool, filename, new_code)
            if validation_error:
                return {"valid": False, "error": validation_error, "actions": [], "raw": raw_text, "thought": thought, "metadata": metadata}
                
    return {"valid": True, "error": None, "reply": reply, "actions": actions, "raw": raw_text, "thought": thought, "metadata": metadata}