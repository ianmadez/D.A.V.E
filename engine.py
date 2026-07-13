"""
DAVEEngine — Framework-agnostic backend engine for D.A.V.E.

Extracted from app.py's DAVEApp class. Zero UI dependencies.
Designed to work with any frontend (Flet, FastAPI/WebSocket, CLI, etc.)
via an on_message_callback that receives queue dispatch events.
"""

import threading
import queue
import os
import sys
import json
import re
import hashlib
import time
import subprocess

from core.brain import get_llm_response
from tools.scanner import scan_directory
from tools.file_reader import read_file_with_lines
from tools.code_editor import replace_lines, rewrite_file, replace_named_block, \
    insert_after_symbol, insert_before_symbol, _safe_apply_edit
from tools.file_creator import create_file
from tools.file_manager import rename_file, delete_file
from tools.search_engine import search_in_file
from tools.terminal_runner import run_command
from tools.planner import manage_plan
from tools.mapper import map_codebase, get_project_skeleton, semantic_search
from tools.demo_recipe_runner import apply_demo_recipe

RECURRING_REMINDER = "REMINDER: Output complete code. Use double quotes for contractions."


class DAVEEngine:
    """Framework-agnostic backend engine — no UI dependencies.

    Manages TaskState machine, llm_queue dispatch, tool routing,
    workspace indexing, file heat tracking, and the full agent loop.

    The on_message_callback is called by the queue consumer thread
    each time a message is dispatched. Expected signature:
        callback(msg: tuple) -> None
    where msg is (msg_type: str, data: any, color: str).
    """

    def __init__(self, on_message_callback=None):
        self.on_message_callback = on_message_callback or (lambda msg: None)

        # ── State variables ──────────────────────────────────────────
        self.target_directory = None
        self.llm_mode = "local"
        self.mode = "agent"
        self.chat_history = []
        self.ask_history = []
        self.workspace_index = {}
        self.ast_map = {}
        self.edit_history = []
        self.session_changes = []
        self.stop_flag = False
        self.is_processing = False
        self.active_task_id = 0
        self.cancel_event = None
        self.refresh_in_progress = False
        self.guided_demo_mode = False
        self.max_agent_turns = 6
        self.max_chat_turns = 3
        self.last_memory_text = ""
        self.proxy_process = None
        self.ast_skeleton = ""

        # ── Unified state machine ────────────────────────────────────
        self.TaskState = {
            "llm_notes": {
                "analysis": "",
                "options": [],
                "decision": "",
                "reason": "",
                "confidence": 0.0,
            },
            "system_ground_truth": {
                "last_command": "",
                "exit_code": None,
                "raw_stderr": "",
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
                "flag_debug": True,
                "flag_tests": False,
                "file_heat": {},
            },
        }

        # ── Thread-safe queue ────────────────────────────────────────
        self.llm_queue = queue.Queue()

        # ── Start the queue consumer daemon ──────────────────────────
        self._start_queue_consumer()

    # ═══════════════════════════════════════════════════════════════════
    #  QUEUE DISPATCH
    # ═══════════════════════════════════════════════════════════════════

    def _queue_put(self, msg_type, data, color="white"):
        """Put a message on the llm_queue. The consumer thread calls
        on_message_callback with it asynchronously."""
        msg = (msg_type, data, color)
        self.llm_queue.put(msg)

    def _start_queue_consumer(self):
        """Background daemon thread that drains the llm_queue and
        calls on_message_callback for each message."""
        def consumer():
            while True:
                try:
                    msg = self.llm_queue.get(timeout=0.1)
                    self.on_message_callback(msg)
                except queue.Empty:
                    time.sleep(0.02)

        thread = threading.Thread(target=consumer, daemon=True)
        thread.start()

    # ═══════════════════════════════════════════════════════════════════
    #  LLM MODE / PROXY MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════

    def set_llm_mode(self, new_mode):
        """Switch LLM backend. Manages FreeLLMAPI proxy lifecycle."""
        prev = self.llm_mode
        self.llm_mode = new_mode
        try:
            if self.llm_mode == "freellmapi":
                if getattr(self, "proxy_process", None):
                    return
                proxy_dir = os.path.abspath(
                    os.path.join(os.getcwd(), "..", "freellmapi"))
                if os.path.exists(proxy_dir):
                    try:
                        self.proxy_process = subprocess.Popen(
                            "npm run dev",
                            cwd=proxy_dir,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            shell=True,
                        )
                        self._queue_put(
                            "terminal",
                            "FreeLLMAPI proxy started on localhost:3001",
                            "green")
                    except Exception as ex:
                        self._queue_put(
                            "terminal",
                            f"Could not start proxy: {ex}",
                            "red")
                else:
                    self._queue_put(
                        "terminal",
                        "freellmapi folder not found next to D.A.V.E.",
                        "red")
            else:
                if getattr(self, "proxy_process", None):
                    try:
                        self.proxy_process.terminate()
                        self.proxy_process.wait(timeout=2)
                    except Exception:
                        try:
                            self.proxy_process.kill()
                        except Exception:
                            pass
                    self.proxy_process = None
        except Exception:
            pass

    def set_demo_mode(self, enabled: bool):
        self.guided_demo_mode = enabled
        self._queue_put(
            "terminal",
            f"[System] Guided Demo Mode {'enabled' if enabled else 'disabled'}.",
            "yellow")

    # ═══════════════════════════════════════════════════════════════════
    #  WORKSPACE MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════

    def init_workspace(self, path):
        """Set the target directory and initialize workspace state."""
        if not path or not os.path.isdir(path):
            self._queue_put("terminal", f"Invalid workspace: {path}", "red")
            return False
        self.target_directory = path
        os.makedirs(
            os.path.join(self.target_directory, ".dave_cache"), exist_ok=True)
        self.TaskState["system_state"]["file_heat"] = \
            self._load_observation_memory()
        self._queue_put(
            "terminal",
            f"Workspace set to: {self.target_directory}",
            "green")
        self._queue_put(
            "terminal",
            "Indexing workspace in background...",
            "yellow")
        self._refresh_workspace_async()
        return True

    def _build_workspace_index(self):
        index = {"files": {}}
        valid_extensions = (
            ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json", ".md")
        ignore_folders = {
            "node_modules", ".git", ".next", "dist", "build",
            "__pycache__", ".dave_cache", "venv", ".venv", "env"}
        for root, dirs, files in os.walk(self.target_directory):
            dirs[:] = [d for d in dirs if d not in ignore_folders]
            for f in files:
                if f.endswith(valid_extensions):
                    rel_path = os.path.relpath(
                        os.path.join(root, f), self.target_directory)
                    ext = os.path.splitext(f)[1]
                    index["files"][rel_path] = {"lines": 0, "ext": ext}
        return index

    def _refresh_workspace_async(self, task_id=None):
        """Rebuild workspace index and AST skeleton in a background thread."""
        if self.refresh_in_progress:
            return
        self.refresh_in_progress = True

        def worker():
            try:
                new_index = self._build_workspace_index()
                try:
                    new_ast = map_codebase(
                        self.target_directory,
                        self.TaskState["system_state"].get("file_heat", {}),
                    )
                except Exception:
                    new_ast = getattr(self, "ast_skeleton", "")

                self.workspace_index = new_index
                self.ast_skeleton = (
                    new_ast if isinstance(new_ast, str) else "")
                self.refresh_in_progress = False
                self._queue_put("terminal", "Workspace indexed.", "green")
                self._queue_put("workspace_refreshed", "", "white")
            except Exception as e:
                self.refresh_in_progress = False
                self._queue_put(
                    "terminal", f"[Refresh Error] {e}", "red")

        threading.Thread(target=worker, daemon=True).start()

    # ═══════════════════════════════════════════════════════════════════
    #  STATE PERSISTENCE
    # ═══════════════════════════════════════════════════════════════════

    def _save_observation_memory(self):
        if not self.target_directory:
            return
        cache_dir = os.path.join(self.target_directory, ".dave_cache")
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, "observation_memory.json")
        try:
            with open(cache_path, "w") as f:
                json.dump(
                    self.TaskState["system_state"].get("file_heat", {}), f)
        except Exception:
            pass

    def _load_observation_memory(self):
        if not self.target_directory:
            return {}
        cache_path = os.path.join(
            self.target_directory, ".dave_cache", "observation_memory.json")
        try:
            if os.path.exists(cache_path):
                with open(cache_path, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def decay_file_heat(self, refresh=True):
        heat = self.TaskState["system_state"].get("file_heat", {})
        decayed = {}
        for fname, score in heat.items():
            new_score = score // 2
            if new_score > 0:
                decayed[fname] = new_score
        self.TaskState["system_state"]["file_heat"] = decayed
        self._save_observation_memory()
        if refresh:
            self._refresh_workspace_async()

    # ═══════════════════════════════════════════════════════════════════
    #  TELEMETRY
    # ═══════════════════════════════════════════════════════════════════

    def _update_telemetry(self):
        phase = self.TaskState["system_state"]["current_phase"]
        conf = self.TaskState["system_state"]["confidence"]
        retries = self.TaskState["system_state"]["retry_count"]
        self._queue_put(
            "telemetry",
            {"phase": phase, "conf": conf, "retries": retries},
            "white")

        snippets = self.TaskState["system_state"]["pinned_snippets"]
        if not snippets:
            mem_text = "No pinned snippets. Memory is empty."
        else:
            mem_text = "\n".join(
                [f"[{s['filename']}] {s['description']}" for s in snippets])
            if getattr(self, "last_memory_text", None) != mem_text:
                self.last_memory_text = mem_text
                self._queue_put("memory", mem_text, "white")

    # ═══════════════════════════════════════════════════════════════════
    #  UNDO
    # ═══════════════════════════════════════════════════════════════════

    def _push_undo(self, filename):
        full_path = os.path.join(self.target_directory, filename)
        if os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8") as f:
                self.edit_history.append({
                    "file": filename, "before": f.read()
                })
                if len(self.edit_history) > 30:
                    self.edit_history.pop(0)

    def undo_last_edit(self):
        if not self.edit_history:
            self._queue_put("terminal", "Undo stack empty.", "yellow")
            return False
        entry = self.edit_history.pop()
        result = rewrite_file(
            entry["file"], entry["before"], self.target_directory, "Undo")
        self._queue_put(
            "terminal",
            f"Undo: restored {entry['file']}.", "yellow")
        self._queue_put(
            "reply",
            f"Undo completed: restored {entry['file']}.", "yellow")
        self._refresh_workspace_async()
        return True

    # ═══════════════════════════════════════════════════════════════════
    #  STOP / CANCEL
    # ═══════════════════════════════════════════════════════════════════

    def stop_agent(self):
        self.stop_flag = True
        if self.cancel_event:
            self.cancel_event.set()
        self.active_task_id += 1
        self._queue_put("status", "Halted", "red")
        self._queue_put(
            "reply",
            "SYSTEM: Execution halted by user. Awaiting manual input.",
            "red")
        self._force_unlock(self.active_task_id)

    def _force_unlock(self, task_id=None):
        if task_id is not None and task_id != self.active_task_id:
            return
        self.is_processing = False
        self._queue_put("unlock", task_id, "white")

    # ═══════════════════════════════════════════════════════════════════
    #  SEND MESSAGE (main entry point)
    # ═══════════════════════════════════════════════════════════════════

    def send_message(self, user_input: str):
        """Process user input and start agent execution.

        This is the main entry point for all frontends (CLI, WebSocket, etc.)
        Returns the task_id for tracking, or None if the input was a command.
        """
        if self.is_processing:
            self._queue_put(
                "reply",
                "SYSTEM: D.A.V.E. is already processing a request. "
                "Wait for it to finish or click Stop.",
                "red")
            self._queue_put("unlock", self.active_task_id, "white")
            return None

        if not user_input or not user_input.strip():
            return None

        user_input = user_input.strip()

        # ── Exit / Quit ──────────────────────────────────────────────
        if user_input.lower() in ("exit", "quit"):
            self._queue_put("system", "shutdown", "white")
            return None

        # ── /toggle commands ─────────────────────────────────────────
        if user_input.lower().startswith("/toggle"):
            flag = user_input.split(" ")[-1].strip().lower()
            valid_flags = ["confidence", "debug", "tests"]
            if flag in valid_flags:
                key = f"flag_{flag}"
                current = self.TaskState["system_state"].get(key, False)
                self.TaskState["system_state"][key] = not current
                state_str = "ON" if not current else "OFF"
                self._queue_put(
                    "reply",
                    f"SYSTEM: {flag.upper()} is now {state_str}.",
                    "yellow")
            else:
                self._queue_put(
                    "reply",
                    "Usage: /toggle <confidence|debug|tests>",
                    "red")
            return None

        # ── /reset command ───────────────────────────────────────────
        if user_input.lower() == "/reset":
            self.chat_history = []
            self.ask_history = []
            self.stop_flag = False
            self.decay_file_heat()
            self.TaskState["system_state"] = {
                "current_phase": "Scout",
                "confidence": 1.0,
                "last_failure_type": None,
                "observed_files": [],
                "retry_count": 0,
                "pinned_snippets": [],
                "last_failing_signature": None,
                "flag_confidence": True,
                "flag_debug": True,
                "flag_tests": False,
                "file_heat": self.TaskState["system_state"].get(
                    "file_heat", {}),
            }
            self._queue_put("reply", "Memory reset. File heat decayed.", "green")
            self._update_telemetry()
            return None

        # ── Start agent execution ────────────────────────────────────
        self.stop_flag = False
        self.active_task_id += 1
        task_id = self.active_task_id
        self.cancel_event = threading.Event()
        cancel_event = self.cancel_event
        self.is_processing = True

        self._queue_put("reply", f"You: {user_input}", "blue")
        self._queue_put("status", "Running", "green")

        threading.Thread(
            target=self._safe_process_message,
            args=(task_id, user_input, cancel_event),
            daemon=True,
        ).start()

        return task_id

    # ═══════════════════════════════════════════════════════════════════
    #  SAFE WRAPPER
    # ═══════════════════════════════════════════════════════════════════

    def _safe_process_message(self, task_id, user_input, cancel_event):
        try:
            self.process_message(user_input, task_id, cancel_event)
        except Exception as e:
            self._queue_put(
                "terminal",
                f"[SYSTEM CRASH] The agent thread encountered a fatal error: {str(e)}",
                "red")
        finally:
            if task_id == self.active_task_id:
                self._queue_put("status", "Idle", "gray")
                self._update_telemetry()
                self._force_unlock(task_id)

    # ═══════════════════════════════════════════════════════════════════
    #  FILENAME RESOLUTION HELPERS
    # ═══════════════════════════════════════════════════════════════════

    def normalize_project_path(self, filename):
        if not filename:
            return None
        if os.path.isabs(filename):
            try:
                filename = os.path.relpath(filename, self.target_directory)
            except ValueError:
                pass
            return filename.replace("\\", "/").lstrip("./")
        return filename.replace("\\", "/").lstrip("./")

    def _sniff_filename_from_text(self, text):
        if not text:
            return None
        match = re.search(
            r"(?<![\w./\\-])([\w./\\-]+\.(?:py|html|css|js|jsx|ts|tsx|json|md|txt))(?![\w-])",
            text,
            re.IGNORECASE,
        )
        return self.normalize_project_path(match.group(1)) if match else None

    def _get_workspace_file_candidates(self, preferred_exts=None):
        files = []
        try:
            for rel_path, meta in self.workspace_index.get(
                    "files", {}).items():
                normalized = self.normalize_project_path(rel_path)
                if not normalized:
                    continue
                if not preferred_exts or normalized.lower().endswith(
                        preferred_exts):
                    files.append(normalized)
        except Exception:
            pass
        if not files:
            valid_exts = preferred_exts or (
                ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css",
                ".json", ".md", ".txt")
            ignore = {"node_modules", ".git", ".next", "dist", "build",
                      "__pycache__", ".dave_cache", "venv", ".venv", "env"}
            try:
                for root, dirs, filenames in os.walk(self.target_directory):
                    dirs[:] = [d for d in dirs if d not in ignore]
                    for fname in filenames:
                        if fname.lower().endswith(valid_exts):
                            rel_path = os.path.relpath(
                                os.path.join(root, fname),
                                self.target_directory)
                            files.append(
                                self.normalize_project_path(rel_path))
            except Exception:
                pass
        return sorted(set(files))

    def _resolve_target_filename(self, action=None, user_input="",
                                  preferred_exts=None):
        action = action or {}
        filename = (
            action.get("filename") or action.get("filepath")
            or action.get("file_path") or action.get("path")
            or action.get("file") or action.get("target_file")
            or action.get("target")
        )
        if filename:
            return self.normalize_project_path(filename)
        sniffed = self._sniff_filename_from_text(user_input)
        if sniffed:
            return sniffed
        current_target = self.TaskState["system_state"].get("current_target")
        if current_target:
            return self.normalize_project_path(current_target)
        observed = self.TaskState["system_state"].get("observed_files", [])
        if observed:
            return self.normalize_project_path(observed[-1])
        candidates = self._get_workspace_file_candidates(
            preferred_exts=preferred_exts)
        if len(candidates) == 1:
            return candidates[0]
        py_candidates = self._get_workspace_file_candidates(
            preferred_exts=(".py",))
        if len(py_candidates) == 1:
            return py_candidates[0]
        return None

    def _canonicalize_action(self, action, user_input=""):
        if isinstance(action, str):
            action = {"tool": action}
        if not isinstance(action, dict):
            return {"tool": "none"}
        tool = str(action.get("tool") or "none").lower()
        action["tool"] = tool
        filename = (
            action.get("filename") or action.get("filepath")
            or action.get("file_path") or action.get("path")
            or action.get("file") or action.get("target_file")
            or action.get("target")
        )
        if not filename and tool in ["read_file", "search_in_file"]:
            filename = self._sniff_filename_from_text(user_input)
        if filename:
            action["filename"] = self.normalize_project_path(filename)
        return action

    def _wants_direct_file_read(self, text):
        if not text:
            return False
        return bool(re.search(
            r"\b(read|open|show|display|contents?|tell me|what'?s in|look at)\b",
            text, re.IGNORECASE,
        ))

    def _wants_file_explanation(self, text):
        if not text:
            return False
        return bool(re.search(
            r"\b(explain|report|summari[sz]e|analy[sz]e|review|describe|diagnose|"
            r"what'?s up|what is up|tell me what'?s up|what does it do|"
            r"walk me through|break it down|overview)\b",
            text, re.IGNORECASE,
        ))

    def _fallback_file_report(self, filename, file_text):
        lines = file_text.splitlines()
        imports = []
        defs = []
        classes = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                imports.append(stripped)
            elif stripped.startswith("def "):
                defs.append(
                    stripped.split("(")[0].replace("def ", ""))
            elif stripped.startswith("class "):
                classes.append(
                    stripped.split("(")[0].replace("class ", "").replace(":", ""))
        report = f"Quick report for `{filename}`:\n\n"
        report += f"- The file has about {len(lines)} readable lines "
        report += "in the returned window.\n"
        if imports:
            report += f"- Imports detected: {', '.join(imports[:5])}.\n"
        if classes:
            report += f"- Classes detected: {', '.join(classes[:5])}.\n"
        if defs:
            report += f"- Functions detected: {', '.join(defs[:8])}.\n"
        if not imports and not classes and not defs:
            report += "- I did not detect obvious imports, classes, or "
            report += "functions in the returned content.\n"
        report += "\nThe file content was read successfully, but the "
        report += "LLM explanation fallback was used."
        return report

    def _append_with_reminder(self, history, role, content, reminder=None):
        history.append({"role": role, "content": content})
        if reminder:
            history.append({
                "role": "system",
                "content": f"<system-reminder>{reminder}</system-reminder>"
            })

    # ═══════════════════════════════════════════════════════════════════
    #  GUIDED DEMO & CHAT SHORTCUTS
    # ═══════════════════════════════════════════════════════════════════

    def _wants_guided_demo_recipe(self, user_input):
        text = user_input.lower()
        demo_words = [
            "make this site", "make the site", "make this landing page",
            "modernize", "make it premium", "look premium", "look professional",
            "make it beautiful", "improve the design", "polish the page",
            "apply demo recipe", "run demo recipe",
        ]
        return any(phrase in text for phrase in demo_words)

    def _try_guided_demo_recipe(self, user_input, active_history,
                                 task_target_directory):
        if not self.guided_demo_mode:
            return False
        if not self._wants_guided_demo_recipe(user_input):
            return False
        recipe_path = os.path.join(task_target_directory, "demo_recipe.json")
        if not os.path.exists(recipe_path):
            reply = (
                "Guided Demo Mode is enabled, but I could not find "
                "demo_recipe.json in the selected workspace.")
            self._queue_put("agent_turn", {
                "thought": "Guided Demo Mode was enabled, but no recipe "
                           "file was found.",
                "tools": ["apply_demo_recipe"],
                "reply": reply,
            }, "white")
            self._queue_put(
                "terminal",
                "Error: demo_recipe.json not found.",
                "red")
            self._append_with_reminder(active_history, "user", user_input)
            self._append_with_reminder(active_history, "assistant", reply)
            self._refresh_workspace_async()
            return True

        self._queue_put("agent_turn", {
            "thought": "Guided Demo Mode matched the user request. "
                       "Applying deterministic recipe.",
            "tools": ["apply_demo_recipe"],
            "reply": "I found a matching guided demo recipe. "
                     "Applying it now.",
        }, "white")
        self._queue_put(
            "terminal",
            "Applying guided demo recipe...",
            "yellow")
        result = apply_demo_recipe(task_target_directory)
        if "STATUS: SUCCESS" in result:
            reply = (
                "Done. I applied the guided demo recipe and "
                "updated the project files.")
        else:
            reply = f"The guided demo recipe could not be completed:\n\n{result}"
            self._queue_put("terminal", result, "red")

        self._queue_put("agent_turn", {
            "thought": "Guided demo recipe execution finished.",
            "tools": ["apply_demo_recipe"],
            "reply": reply,
        }, "white")
        self._append_with_reminder(active_history, "user", user_input)
        self._append_with_reminder(active_history, "assistant", reply)
        self._refresh_workspace_async()
        return True

    def _try_direct_chat_read(self, user_input, active_history,
                               task_target_directory, task_llm_mode):
        filename = self._sniff_filename_from_text(user_input)
        if not filename:
            return False
        if not self._wants_direct_file_read(user_input):
            return False
        self._queue_put("terminal", f"Reading {filename}...", "yellow")
        file_result = read_file_with_lines(filename, task_target_directory)
        if file_result.startswith("Error:"):
            reply = (
                f"I tried to read `{filename}`, but got this error:\n\n"
                f"{file_result}")
            self._queue_put("agent_turn", {
                "thought": "Direct chat read attempted, but the file reader "
                           "returned an error.",
                "tools": ["read_file"],
                "reply": reply,
            }, "white")
            self._append_with_reminder(active_history, "user", user_input)
            self._append_with_reminder(active_history, "assistant", reply)
            self._queue_put("terminal", "Direct file read failed.", "red")
            return True

        wants_explanation = self._wants_file_explanation(user_input)
        if not wants_explanation:
            reply = f"Here are the contents of `{filename}`:\n\n{file_result}"
            self._queue_put("agent_turn", {
                "thought": "Direct chat read shortcut used.",
                "tools": ["read_file"],
                "reply": reply,
            }, "white")
            self._append_with_reminder(active_history, "user", user_input)
            self._append_with_reminder(
                active_history, "assistant",
                f"Displayed contents of {filename}.")
            self._queue_put(
                "terminal", "Direct file read complete.", "green")
            return True

        explanation_prompt = f"""The user asked:
{user_input}
I have already read the file directly. Do not call tools.

FILE: {filename}
CONTENT:
{file_result}
Return JSON only:
{{"thought": "short explanation plan", "reply": "your useful explanation for the user", "actions": []}}"""
        response = get_llm_response(
            explanation_prompt, [], task_target_directory, task_llm_mode,
            is_write_operation=False, chat_mode=True, task_state=None,
        )
        reply = (
            response.get("reply")
            if response.get("valid") and response.get("reply")
            else self._fallback_file_report(filename, file_result))
        self._queue_put("agent_turn", {
            "thought": "Direct file read plus explanation shortcut used.",
            "tools": ["read_file"],
            "reply": reply,
        }, "white")
        self._append_with_reminder(active_history, "user", user_input)
        self._append_with_reminder(active_history, "assistant", reply)
        self._queue_put(
            "terminal",
            "Direct file read and explanation complete.",
            "green")
        return True

    # ═══════════════════════════════════════════════════════════════════
    #  CORE AGENT LOOP
    # ═══════════════════════════════════════════════════════════════════

    def process_message(self, user_input, task_id=None, cancel_event=None):
        task_mode = self.mode
        task_llm_mode = self.llm_mode
        task_target_directory = self.target_directory
        current_input = user_input
        active_history = (
            self.chat_history if task_mode == "agent" else self.ask_history)

        if task_mode == "agent":
            if self._try_guided_demo_recipe(
                    user_input, active_history, task_target_directory):
                if task_id == self.active_task_id:
                    self._force_unlock(task_id)
                return
        if task_mode == "chat":
            if self._try_direct_chat_read(
                    user_input, active_history,
                    task_target_directory, task_llm_mode):
                if task_id == self.active_task_id:
                    self._force_unlock(task_id)
                return
            self.TaskState["system_state"]["current_phase"] = "Chat"
        else:
            self.TaskState["system_state"]["current_phase"] = "Scout"

        last_used_tool = None
        last_run_success = False
        edit_applied = False
        action_tracker = []
        turn_count = 0
        max_turns = (
            self.max_agent_turns if task_mode == "agent"
            else self.max_chat_turns)

        initial_target = self._resolve_target_filename(
            action={}, user_input=user_input,
            preferred_exts=(
                ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css"),
        )
        if initial_target:
            self.TaskState["system_state"]["current_target"] = initial_target

        while not self.stop_flag and not (cancel_event and cancel_event.is_set()):
            turn_count += 1
            if turn_count > max_turns:
                self._queue_put("terminal",
                    f"[LOOP GUARD] Max turns reached ({max_turns}). "
                    "Stopping task safely.", "red")
                break
            self._queue_put("status", "Thinking...", "yellow")
            self._update_telemetry()

            # ── 1. Helmet Injection ──────────────────────────────────
            try:
                phase = self.TaskState["system_state"].get(
                    "current_phase", "Scout")
                helmet_prompt = (
                    f"\n=== ACTIVE HELMET PHASE: {phase.upper()} ===\n")
                if phase in ["Scout", "Chat"] and current_input == user_input:
                    if phase == "Chat":
                        context = (
                            f"Project: "
                            f"{os.path.basename(self.target_directory)}\n"
                            f"Files: "
                            f"{list(self.workspace_index.get('files', {}).keys())}\n"
                            f"Skeleton:\n{self.ast_skeleton}")
                        helmet_prompt += (
                            f"\n[WORKSPACE CONTEXT]\n{context}\n")
                    try:
                        _, index_data = get_project_skeleton(
                            self.target_directory,
                            self.TaskState["system_state"].get(
                                "file_heat", {}))
                        auto_context = semantic_search(
                            user_input, index_data, top_n=2)
                        if ("No strong matches" not in auto_context
                                and "Error" not in auto_context):
                            helmet_prompt += (
                                f"\n[AUTO-RETRIEVED CONTEXT based on your task]\n"
                                f"{auto_context}\n")
                    except Exception:
                        pass
                if phase == "Scout":
                    helmet_prompt += (
                        "MODE: EXPLORE. Use read_file, scan_directory, "
                        "search_in_file, or semantic_search only. "
                        "You must identify the exact target filename "
                        "before planning an edit. "
                        "When ready, use update_state to transition to PLAN.\n")
                elif phase == "Chat":
                    helmet_prompt += (
                        "MODE: CHAT. You are a conversational codebase "
                        "assistant. DO NOT use edit tools. Put your "
                        "answer in the 'reply' field.\n")
                elif phase == "Plan":
                    helmet_prompt += (
                        "MODE: PLAN. Return JSON with actions array "
                        "containing exactly one update_state action. "
                        "The update_state must include: analysis, options, "
                        "decision, reason, confidence. "
                        "Do not output code in PLAN.\n")
                elif phase == "Execute":
                    helmet_prompt += (
                        "MODE: EXECUTE. You must perform the edit now. "
                        "If using an edit tool, you MUST include complete "
                        "new_code. Allowed edit tools: replace_lines, "
                        "rewrite_file, replace_named_block, "
                        "insert_before_symbol, insert_after_symbol, "
                        "create_file.\n")
                augmented_input = (
                    f"{helmet_prompt}\n[SYSTEM EVENT / USER INPUT]\n"
                    f"{current_input}")
            except Exception:
                augmented_input = current_input

            # ── 2. Call LLM ──────────────────────────────────────────
            response = get_llm_response(
                augmented_input, active_history,
                task_target_directory, task_llm_mode,
                is_write_operation=(task_mode == "agent"),
                task_state=self.TaskState,
                chat_mode=(task_mode == "chat"),
            )
            if self.stop_flag or (cancel_event and cancel_event.is_set()):
                break

            # ── 3. Handle Invalid format ─────────────────────────────
            if not response.get("valid", False):
                self.TaskState["system_state"]["retry_count"] += 1
                err = response.get("error", "Parse error.")
                err_lower = err.lower()
                self._queue_put("terminal",
                    f"Brain Error "
                    f"({self.TaskState['system_state']['retry_count']}/2): "
                    f"{err}", "red")
                fatal_write_format_error = (
                    "write action" in err_lower
                    or "missing 'new_code'" in err_lower
                    or "missing new_code" in err_lower
                    or "rewrite nuke ban" in err_lower
                    or "proposed implementation" in err_lower
                )
                if fatal_write_format_error:
                    reply = (
                        "I stopped because the model attempted a write "
                        "action without valid replacement code. "
                        "No file was changed.")
                    self._queue_put("agent_turn", {
                        "thought": "Write validation failed.",
                        "tools": ["write_validation"],
                        "reply": reply,
                    }, "white")
                    self._queue_put("reply", reply, "red")
                    self._append_with_reminder(
                        active_history, "user", current_input)
                    self._append_with_reminder(
                        active_history, "assistant", reply)
                    break
                if self.TaskState["system_state"]["retry_count"] >= 2:
                    self._queue_put("terminal",
                        "[ERR-RECOVERY-FAIL] Format retries exhausted. "
                        "Stopping safely.", "red")
                    self._queue_put("reply", f"SYSTEM ERROR: Failed to generate a valid structured JSON response from the LLM. Retries exhausted.", "red")
                    self.TaskState["system_state"]["retry_count"] = 0
                    break
                self._append_with_reminder(
                    active_history, "assistant",
                    f"CRITICAL FORMAT ERROR: {err}. Fix your JSON.")
                current_input = (
                    f"CRITICAL FORMAT ERROR: {err}. Fix your JSON.")
                self._update_telemetry()
                continue
            else:
                self.TaskState["system_state"]["retry_count"] = 0

            actions = response.get("actions", [])
            actions = [self._canonicalize_action(a, user_input)
                       for a in actions]
            thought = response.get("thought", "")
            agent_reply = response.get("reply", "...")
            planned_tools = [
                a.get("tool") for a in actions
                if a.get("tool") and a.get("tool") != "none"]
            self._queue_put("agent_turn", {
                "thought": thought,
                "tools": planned_tools,
                "reply": agent_reply,
            }, "white")

            # ── 4. Check for task completion ─────────────────────────
            if not actions or any(
                    a.get("tool") in ("none", "task_complete")
                    for a in actions):
                if (task_mode == "agent"
                        and self.TaskState["system_state"].get(
                            "flag_tests", False)):
                    if not (last_used_tool == "run_command"
                            and last_run_success):
                        self._append_with_reminder(
                            active_history, "assistant",
                            "SYSTEM: Task cannot be completed. You have not "
                            "executed 'run_command' to verify.")
                        current_input = (
                            "SYSTEM: Task cannot be completed. You have not "
                            "executed 'run_command' to verify "
                            "your code works.")
                        continue
                self._append_with_reminder(
                active_history, "user", current_input)
                self._append_with_reminder(
                    active_history, "assistant", agent_reply,
                    RECURRING_REMINDER if task_mode == "agent" else None)
                self._queue_put("terminal", "Task/Chat Complete.", "green")
                    # Broadcast the final conversational answer to the UI chat panel stream
                self._queue_put("reply", agent_reply, "white" if task_mode == "chat" else "green")
                if task_id == self.active_task_id:
                    self._force_unlock(task_id)
                if task_mode == "agent":
                    self.decay_file_heat(refresh=False)
                    self._refresh_workspace_async(task_id)
                break

            # ── Pre-read all files ───────────────────────────────────
            read_results = {}
            fatal_missing_filename = False
            for a in actions:
                if a.get("tool") == "read_file":
                    filename = self._resolve_target_filename(
                        action=a, user_input=user_input,
                        preferred_exts=(
                            ".py", ".js", ".jsx", ".ts", ".tsx",
                            ".html", ".css", ".json", ".md", ".txt"),
                    )
                    if filename:
                        a["filename"] = filename
                        self.TaskState["system_state"][
                            "current_target"] = filename
                    if not filename:
                        fatal_missing_filename = True
                        self._queue_put("terminal",
                            "[FATAL] read_file requested without filename.",
                            "red")
                        break
                    self._queue_put("terminal",
                        f"Reading {filename}...", "yellow")
                    read_results[filename] = read_file_with_lines(
                        filename, task_target_directory,
                        a.get("start_line"), a.get("end_line"),
                    )
            if fatal_missing_filename:
                self._queue_put("agent_turn", {
                    "thought": "Read failed: No filename.",
                    "tools": ["read_file"],
                    "reply": "I couldn't determine which file to read.",
                }, "white")
                break

            # ── 5. Execute Tools ─────────────────────────────────────
            force_agent_break = False
            guardrail_triggered = False

            def _increase_heat(fname, amount):
                if fname:
                    if os.path.isabs(fname):
                        try:
                            fname = os.path.relpath(
                                fname, self.target_directory)
                        except ValueError:
                            pass
                    fname = fname.replace("\\", "/")
                    heat = self.TaskState["system_state"]["file_heat"]
                    heat[fname] = heat.get(fname, 0) + amount
                    self._save_observation_memory()

            for a in actions:
                tool_req = a.get("tool")
                filename = self.normalize_project_path(a.get("filename"))
                new_code = a.get("new_code")
                command = a.get("command")
                action_result = None

                # Chat Mode Guard
                if (task_mode == "chat"
                        and tool_req not in [
                            "read_file", "scan_directory", "search_in_file",
                            "semantic_search", "none", "task_complete"]):
                    action_result = (
                        f"[ERR-READ-ONLY] You are in Chat Mode. "
                        f"Edit tool '{tool_req}' is blocked.")
                    self._queue_put("warning", action_result, "red")
                    self._append_with_reminder(
                        active_history, "assistant",
                        f"{agent_reply}\n[TOOL_RESULT]: {action_result}")
                    guardrail_triggered = True
                    break

                # Phase Guard
                try:
                    if (self.TaskState["system_state"]["current_phase"]
                            == "Plan"
                            and tool_req not in ["update_state", "none"]):
                        action_result = (
                            "[ERR-PHASE-VIOLATION] You are in PLAN phase. "
                            "You MUST use 'update_state'.")
                        self._queue_put("warning", action_result, "red")
                        self._append_with_reminder(
                            active_history, "assistant",
                            f"{agent_reply}\n[TOOL_RESULT]: "
                            f"{action_result}")
                        guardrail_triggered = True
                        break
                except KeyError:
                    pass

                # Idempotency Guard
                code_hash = (
                    hashlib.md5(new_code.encode()).hexdigest()[:8]
                    if new_code else "none")
                action_signature = (
                    f"{tool_req}_{filename}_{a.get('func_name','')}"
                    f"_{command}_{code_hash}")
                if (action_signature
                        == self.TaskState["system_state"].get(
                            "last_failing_signature")
                        and tool_req not in [
                            "read_file", "scan_directory"]):
                    action_result = (
                        "[ERR-IDEMPOTENCY] Blocked. You just tried "
                        "this EXACT action and it failed.")
                    self._queue_put("warning", action_result, "red")
                    self.TaskState["system_state"]["confidence"] = 0.0
                    self.TaskState["system_state"]["current_phase"] = (
                        "Chat" if self.mode == "chat" else "Scout")
                    self._append_with_reminder(
                        active_history, "assistant",
                        f"{agent_reply}\n[TOOL_RESULT]: {action_result}")
                    guardrail_triggered = True
                    break

                action_tracker.append(action_signature)
                if len(action_tracker) > 5:
                    action_tracker.pop(0)
                if len(action_tracker) == 5 \
                        and len(set(action_tracker)) == 1:
                    self._queue_put("terminal",
                        "BEHAVIORAL LOOP DETECTED. Pausing agent.", "red")
                    action_tracker = []
                    force_agent_break = True
                    break

                # ── Execute Action ──────────────────────────────────
                if tool_req == "read_file":
                    if not filename:
                        filename = self._sniff_filename_from_text(user_input)
                        if filename:
                            a["filename"] = filename
                    if not filename:
                        action_result = "Error: Missing filename."
                    else:
                        _increase_heat(filename, 1)
                        action_result = read_results.get(filename)
                        if action_result is None:
                            action_result = read_file_with_lines(
                                filename, task_target_directory,
                                a.get("start_line"),
                                a.get("end_line"))
                        if ("Error:" not in action_result
                                and filename not in self.TaskState[
                                    "system_state"]["observed_files"]):
                            self.TaskState["system_state"][
                                "observed_files"].append(filename)

                elif tool_req == "scan_directory":
                    action_result = scan_directory(self.target_directory)
                    self._queue_put("terminal", "Scanned workspace", "white")

                elif tool_req == "search_in_file":
                    search_query = a.get("search_query")
                    if filename and search_query:
                        action_result = search_in_file(
                            filename, search_query, self.target_directory)
                        self._queue_put("terminal",
                            f"Searched in {filename}", "white")
                    else:
                        action_result = "Error: Missing arguments."

                elif tool_req == "semantic_search":
                    query = a.get("query")
                    if query:
                        self._queue_put("terminal",
                            f"Semantic search: '{query}'", "yellow")
                        try:
                            _, index_data = get_project_skeleton(
                                self.target_directory,
                                self.TaskState["system_state"].get(
                                    "file_heat", {}))
                            search_res = semantic_search(query, index_data)
                            action_result = (
                                "Semantic search results:\n"
                                f"{search_res['context_string']}")
                            meta_text = f"QUERY: '{query}'\n"
                            for m in search_res.get("metadata", []):
                                meta_text += (
                                    f" -> {m['file']} ({m['type']}: "
                                    f"{m['name']}) "
                                    f"[Score: {m['score']}]\n")
                            if not search_res.get("metadata"):
                                meta_text += (
                                    " -> No strong matches found.\n")
                            self._queue_put(
                                "context_viewer", meta_text, "cyan")
                        except Exception as e:
                            action_result = (
                                f"Error in semantic search: {str(e)}")
                    else:
                        action_result = "Error: Missing 'query' argument."

                elif tool_req == "pin_snippet":
                    content = a.get("content")
                    if filename and content:
                        if any(s.get("content") == content
                               for s in self.TaskState["system_state"][
                                   "pinned_snippets"]):
                            action_result = "Snippet already pinned."
                        else:
                            self.TaskState["system_state"][
                                "pinned_snippets"].append({
                                    "filename": filename,
                                    "description": a.get("description", ""),
                                    "content": content,
                                })
                            action_result = "Pinned to Working Memory."
                            self._queue_put("terminal",
                                f"Pinned memory from {filename}", "green")
                            self._update_telemetry()
                    else:
                        action_result = "Error: missing filename or content"

                elif tool_req == "unpin_snippet":
                    self.TaskState["system_state"]["pinned_snippets"] = []
                    action_result = "Cleared Working Memory."
                    self._update_telemetry()

                elif tool_req == "rename_file":
                    old_fn = a.get("old_filename")
                    new_fn = a.get("new_filename")
                    if old_fn and new_fn:
                        action_result = rename_file(
                            old_fn, new_fn, self.target_directory)
                        self._queue_put("terminal",
                            f"Renamed {old_fn} to {new_fn}", "yellow")
                    else:
                        action_result = (
                            "Error: Missing arguments for rename_file.")

                elif tool_req == "delete_file":
                    if filename:
                        action_result = delete_file(
                            filename, self.target_directory)
                        self._queue_put("terminal",
                            f"Deleted {filename}", "red")
                        self._queue_put(
                            "workspace_refreshed", "", "white")
                    else:
                        action_result = (
                            "Error: Missing filename for delete_file.")

                elif tool_req in [
                    "create_file", "replace_lines", "rewrite_file",
                    "replace_named_block", "insert_before_symbol",
                    "insert_after_symbol",
                ]:
                    edit_intent = a.get("edit_intent")
                    if not edit_intent:
                        action_result = (
                            "[ERR-INTENT-MISSING] MUST provide edit_intent.")
                        self.TaskState["system_state"][
                            "current_phase"] = "Plan"
                        self._append_with_reminder(
                            active_history, "assistant", action_result)
                        break
                    if (filename not in self.TaskState["system_state"][
                            "observed_files"]
                            and tool_req != "create_file"):
                        action_result = (
                            f"[ERR-BLIND-EDIT] Blocked. Attempted to edit "
                            f"{filename} without reading.")
                        self._queue_put("warning", action_result, "red")
                        self.TaskState["system_state"][
                            "current_phase"] = "Scout"
                        self._append_with_reminder(
                            active_history, "assistant", action_result)
                        guardrail_triggered = True
                        break
                    self._push_undo(filename)
                    _increase_heat(filename, 5)
                    if tool_req == "create_file":
                        action_result = create_file(
                            filename, new_code, self.target_directory)
                    elif tool_req == "replace_lines":
                        action_result = replace_lines(
                            filename, a.get("start_line"),
                            a.get("end_line"), new_code,
                            self.target_directory, edit_intent)
                    elif tool_req == "rewrite_file":
                        action_result = rewrite_file(
                            filename, new_code,
                            self.target_directory, edit_intent)
                    elif tool_req == "replace_named_block":
                        action_result = replace_named_block(
                            filename, a.get("symbol_name"),
                            new_code, self.target_directory, edit_intent)
                    elif tool_req == "insert_before_symbol":
                        action_result = insert_before_symbol(
                            filename, a.get("symbol_name"),
                            new_code, self.target_directory, edit_intent)
                    elif tool_req == "insert_after_symbol":
                        action_result = insert_after_symbol(
                            filename, a.get("symbol_name"),
                            new_code, self.target_directory, edit_intent)
                    if (isinstance(action_result, str)
                            and (action_result.startswith("Successfully")
                                 or "successfully" in action_result.lower()
                                 or "applied safe edit"
                                 in action_result.lower())):
                        edit_applied = True
                        self._queue_put("terminal",
                            f"Edited {filename} via {tool_req}", "green")
                        self._refresh_workspace_async(task_id)
                    else:
                        self._queue_put("terminal",
                            f"Edit failed: {str(action_result)[:120]}",
                            "red")

                elif tool_req == "update_state":
                    self.TaskState["llm_notes"] = {
                        "analysis": a.get("analysis", ""),
                        "options": a.get("options", []),
                        "decision": a.get("decision", ""),
                        "reason": a.get("reason", ""),
                        "confidence": a.get("confidence", 0.0),
                    }
                    plan_str = (
                        f"DECISION: {a.get('decision')}\n"
                        f"CONFIDENCE: {a.get('confidence')}\n\n"
                        f"REASONING:\n"
                        f"{a.get('analysis')}\n{a.get('reason')}")
                    self._queue_put("update_plan", plan_str, "white")
                    action_result = "State updated successfully."

                elif tool_req == "manage_plan":
                    act = a.get("action")
                    data = a.get("data", "")
                    if act:
                        self._queue_put("terminal",
                            f"Accessing Master Plan ({act})", "yellow")
                        try:
                            action_result = manage_plan(
                                act, data, task_target_directory)
                        except Exception as e:
                            action_result = f"Error managing plan: {e}"
                    else:
                        action_result = (
                            "Error: Missing 'action' argument.")

                elif tool_req == "run_command":
                    if task_mode == "agent" and not edit_applied:
                        action_result = (
                            "[ERR-RUN-BEFORE-EDIT] The agent tried to run "
                            "a command before any edit was applied.")
                        force_agent_break = True
                    elif command:
                        self._queue_put("terminal",
                            f"$ {command}", "yellow")
                        try:
                            action_result = run_command(
                                command, task_target_directory,
                                cancel_event=cancel_event)
                        except TypeError:
                            action_result = run_command(
                                command, task_target_directory)
                        last_used_tool = "run_command"
                        last_run_success = (
                            isinstance(action_result, str)
                            and "STATUS: SUCCESS" in action_result)
                        self.TaskState["system_ground_truth"][
                            "last_command"] = command
                        self.TaskState["system_ground_truth"][
                            "exit_code"] = (
                                0 if last_run_success else 1)
                        self.TaskState["system_ground_truth"][
                            "raw_stderr"] = (
                                action_result
                                if not last_run_success else "")
                    else:
                        action_result = (
                            "[ERR-NO-COMMAND-FATAL] run_command was "
                            "requested without a command.")
                        force_agent_break = True

                else:
                    action_result = f"Error: Unknown tool {tool_req}"

                # Error handling
                if (isinstance(action_result, str)
                        and (action_result.startswith("Error:")
                             or action_result.startswith("[ERR-")
                             or action_result.startswith("CRITICAL:")
                             or "STATUS: FAILURE" in action_result)):
                    self.TaskState["system_state"][
                        "last_failing_signature"] = action_signature
                    self.TaskState["system_state"]["confidence"] = max(
                        0.0,
                        self.TaskState["system_state"].get(
                            "confidence", 1.0) - 0.5)
                    self._queue_put("terminal",
                        f"Failed: {str(action_result)[:100]}...", "red")
                else:
                    if tool_req not in [
                            "read_file", "scan_directory",
                            "update_state", "none"]:
                        self.TaskState["system_state"][
                            "last_failing_signature"] = None
                        self.TaskState["system_state"][
                            "confidence"] = 1.0

                self._append_with_reminder(
                    active_history, "user", current_input)
                self._append_with_reminder(
                    active_history, "assistant",
                    f'{agent_reply}\n[TOOL_RESULT]: {action_result}',
                    RECURRING_REMINDER if self.mode == "agent" else None)
                last_used_tool = tool_req
                if tool_req != "run_command":
                    last_run_success = False

            if force_agent_break:
                break

            # ── 6. Routing ───────────────────────────────────────────
            current_phase = self.TaskState["system_state"][
                "current_phase"]
            sys_confidence = self.TaskState["system_state"].get(
                "confidence", 1.0)
            next_input = ""
            tools_used = [a.get("tool") for a in actions]

            if guardrail_triggered:
                self._queue_put("terminal",
                    "🛑 SYSTEM GUARDRAIL TRIGGERED: Rerouting agent...",
                    "red")
                next_input = (
                    "System Guardrail Triggered. "
                    "Read the warning and correct your action.")
            elif (sys_confidence < 0.6
                  and current_phase not in ["Scout", "Chat"]
                  and self.TaskState["system_state"].get(
                      "flag_confidence", True)):
                self.TaskState["system_state"][
                    "current_phase"] = "Scout"
                self.TaskState["system_state"]["confidence"] = 1.0
                next_input = (
                    "[SYSTEM WARNING] Confidence critically low. "
                    "Forced to SCOUT phase.")
            elif current_phase == "Chat":
                next_input = (
                    "Deliver your final answer in the 'reply' "
                    "field and leave 'actions' empty: [].")
            elif current_phase == "Scout":
                self.TaskState["system_state"][
                    "current_phase"] = "Plan"
                next_input = (
                    "Scouting finished. Transitioning to PLAN phase. "
                    "You MUST use 'update_state'.")
            elif current_phase == "Plan":
                if "update_state" in tools_used:
                    self.TaskState["system_state"][
                        "current_phase"] = "Execute"
                    next_input = (
                        "Plan accepted. "
                        "Transitioning to EXECUTE phase.")
                else:
                    next_input = (
                        "[ERR-PHASE-02] You are in PLAN phase "
                        "but did not use 'update_state'.")
            elif current_phase == "Execute":
                if "run_command" in tools_used:
                    if (isinstance(action_result, str)
                            and "STATUS: SUCCESS" in action_result):
                        next_input = (
                            "Execution successful. Observe results. "
                            "If done, use 'task_complete'.")
                    elif (isinstance(action_result, str)
                          and "ERROR_TYPE: Syntax" in action_result):
                        next_input = (
                            "Syntax Error detected. Remain in "
                            "EXECUTE phase to apply a patch.")
                    elif (isinstance(action_result, str)
                          and "ERROR_TYPE:" in action_result):
                        self.TaskState["system_state"][
                            "current_phase"] = "Scout"
                        next_input = (
                            "Error detected. "
                            "Transitioning back to SCOUT phase.")
                    else:
                        next_input = (
                            "Execution finished. Observe results.")
                else:
                    next_input = (
                        "Tool execution finished. "
                        "If done, use 'task_complete'.")

            if any(a.get("tool") == "manage_plan"
                   and a.get("action") in ["create", "read"]
                   for a in actions):
                next_input += (
                    " (SYSTEM COMMAND: Plan accessed. "
                    "DO NOT use 'manage_plan' again "
                    "on your next turn.)")

            current_input = next_input
            if len(active_history) > 10:
                if self.mode == "agent":
                    self.chat_history = self.chat_history[-10:]
                else:
                    self.ask_history = self.ask_history[-10:]


# ═══════════════════════════════════════════════════════════════════
#  GET FILE TREE (for frontend explorer)
# ═══════════════════════════════════════════════════════════════════

def get_file_tree(engine, expanded_folders=None):
    """Build a JSON-serializable file tree from the engine's workspace.

    Returns a nested dict structure suitable for the browser frontend.
    """
    if not engine.target_directory \
            or not os.path.isdir(engine.target_directory):
        return {"name": "No workspace", "type": "directory", "children": []}

    expanded = expanded_folders or set()
    heat_dict = engine.TaskState["system_state"].get("file_heat", {})
    valid_exts = (".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css",
                  ".json", ".md")
    ignore = {"node_modules", ".git", ".next", "dist", "build",
              "__pycache__", ".dave_cache", "venv", ".venv", "env"}

    def get_heat_bar(score):
        if score >= 10:
            return "[███]"
        elif score >= 5:
            return "[██░]"
        elif score >= 1:
            return "[█░░]"
        return "[░░░]"

    def build_node(dir_path, rel_path=""):
        name = (
            os.path.basename(dir_path) if rel_path
            else os.path.basename(engine.target_directory))
        children = []
        try:
            entries = sorted(os.listdir(dir_path))
        except PermissionError:
            return {"name": name, "type": "directory", "children": []}

        for e in entries:
            full = os.path.join(dir_path, e)
            child_rel = os.path.relpath(full, engine.target_directory)
            if os.path.isdir(full):
                if e in ignore:
                    continue
                children.append(build_node(full, child_rel))
            elif os.path.isfile(full) and e.endswith(valid_exts):
                heat_score = heat_dict.get(
                    child_rel.replace("\\", "/"), 0)
                children.append({
                    "name": e,
                    "type": "file",
                    "path": child_rel.replace("\\", "/"),
                    "heat": heat_score,
                    "heat_bar": get_heat_bar(heat_score),
                })

        node = {
            "name": name,
            "type": "directory",
            "path": rel_path,
            "expanded": rel_path in expanded,
            "children": children,
        }
        return node

    return build_node(engine.target_directory)
