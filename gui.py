import customtkinter as ctk
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
from tools.code_editor import replace_lines, rewrite_file, replace_named_block, insert_after_symbol, insert_before_symbol, _safe_apply_edit
from tools.file_creator import create_file
from tools.file_manager import rename_file, delete_file
from tools.search_engine import search_in_file
from tools.terminal_runner import run_command
from tools.planner import manage_plan
from tools.mapper import map_codebase, get_project_skeleton, semantic_search
from tools.planner import manage_plan
from tools.demo_recipe_runner import apply_demo_recipe

# Set appearance
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

RECURRING_REMINDER = "REMINDER: Output complete code. Use double quotes for contractions."

class TurnWidget(ctk.CTkFrame):
    def __init__(self, master, thought, tools, reply, **kwargs):
        # Adaptive background: Light Mode (gray85), Dark Mode (gray16)
        super().__init__(master, fg_color=("gray85", "gray16"), corner_radius=8, **kwargs)
        self.expanded = False

        # Header Row (Always visible)
        self.header_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.header_frame.pack(fill="x", padx=5, pady=5)

        self.toggle_btn = ctk.CTkButton(self.header_frame, text="+ Details", width=60, height=24, fg_color=("gray75", "gray25"), text_color=("black", "white"), hover_color=("gray65", "gray35"), command=self.toggle)
        self.toggle_btn.pack(side="left", padx=(0, 10))

        reply_text = reply if reply and reply.strip() and reply != "..." else "Executing task..."
        self.reply_label = ctk.CTkLabel(self.header_frame, text=f"D.A.V.E.: {reply_text}", font=("Segoe UI", 13, "bold"), text_color=("#2e7d32", "#81C784"), justify="left", wraplength=500)
        self.reply_label.pack(side="left", fill="x", expand=True, anchor="w")

        # Details Row (Hidden by default)
        self.details_frame = ctk.CTkFrame(self, fg_color=("#F7F7F7", "gray10"), corner_radius=6)

        detail_title_color = ("#303030", "#AAAAAA")
        detail_body_color = ("#1f1f1f", "#CCCCCC")
        detail_tool_color = ("#1B5E20", "#81C784")

        if thought:
            ctk.CTkLabel(
                self.details_frame,
                text="Thinking:",
                font=("Arial", 11, "bold"),
                text_color=detail_title_color
            ).pack(anchor="w", padx=10, pady=(5, 0))

            ctk.CTkLabel(
                self.details_frame,
                text=thought,
                font=("Arial", 11),
                text_color=detail_body_color,
                justify="left",
                wraplength=500
            ).pack(anchor="w", padx=10, pady=(0, 5))

        if tools:
            ctk.CTkLabel(
                self.details_frame,
                text="Tools Planned:",
                font=("Arial", 11, "bold"),
                text_color=detail_title_color
            ).pack(anchor="w", padx=10, pady=(5, 0))

            tools_str = "\n".join([f"• {t}" for t in tools])

            ctk.CTkLabel(
                self.details_frame,
                text=tools_str,
                font=("Consolas", 11),
                text_color=detail_tool_color,
                justify="left"
            ).pack(anchor="w", padx=10, pady=(0, 5))

    def toggle(self):
        self.expanded = not self.expanded
        if self.expanded:
            self.toggle_btn.configure(text="- Details")
            self.details_frame.pack(fill="x", padx=10, pady=(0, 10))
        else:
            self.toggle_btn.configure(text="+ Details")
            self.details_frame.pack_forget()

class DAVEApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("D.A.V.E. - Direct Agentic Versioning Engine")
        self.geometry("1400x900")
        self.resizable(True, True)

        # Initialize variables
        self.target_directory = os.getcwd()
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

        # --- BATCH 6.1: UNIFIED STATE MACHINE ---
        self.TaskState = {
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
                "flag_debug": True,
                "flag_tests": False,
                "file_heat": {} 
            }
        }

        # Queues for async UI updates
        self.llm_queue = queue.Queue()

        self.setup_ui()
        self.initialize_workspace()

    def setup_ui(self):
        # Main frame
        self.main_frame = ctk.CTkFrame(self)
        self.main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Top bar
        self.top_frame = ctk.CTkFrame(self.main_frame, height=50)
        self.top_frame.pack(fill="x", pady=(0, 10))

        # Mode Toggle Frame
        self.mode_frame = ctk.CTkFrame(self.top_frame, fg_color="transparent")
        self.mode_frame.pack(side="left", padx=15)
        self.mode_label_chat = ctk.CTkLabel(self.mode_frame, text="Chat", font=("Arial", 12))
        self.mode_label_chat.pack(side="left", padx=(0, 5))
        self.mode_switch = ctk.CTkSwitch(self.mode_frame, text="", width=40, command=self.toggle_mode)
        self.mode_switch.pack(side="left")
        self.mode_switch.select()
        self.mode_label_agent = ctk.CTkLabel(self.mode_frame, text="Agent", font=("Arial", 12, "bold"))
        self.mode_label_agent.pack(side="left", padx=(0, 0))

        # LLM Mode Selector
        self.llm_frame = ctk.CTkFrame(self.top_frame, fg_color="transparent")
        self.llm_frame.pack(side="left", padx=15)
        self.llm_label = ctk.CTkLabel(self.llm_frame, text="LLM Mode", font=("Arial", 12))
        self.llm_label.pack(side="left", padx=(0, 5))
        self.llm_selector = ctk.CTkOptionMenu(self.llm_frame, values=["local", "api", "freellmapi"], command=self.select_llm)
        self.llm_selector.pack(side="left")
        self.llm_selector.set("local")

        # Appearance Toggle
        self.appearance_frame = ctk.CTkFrame(self.top_frame, fg_color="transparent")
        self.appearance_frame.pack(side="left", padx=15)
        self.appearance_switch = ctk.CTkSwitch(self.appearance_frame, text="Light Mode", width=40, command=self.toggle_appearance)
        self.appearance_switch.pack(side="left")
        
        self.demo_frame = ctk.CTkFrame(self.top_frame, fg_color="transparent")
        self.demo_frame.pack(side="left", padx=15)

        self.demo_switch = ctk.CTkSwitch(
            self.demo_frame,
            text="Guided Demo",
            width=40,
            command=self.toggle_guided_demo
        )
        self.demo_switch.pack(side="left")

        self.stop_button = ctk.CTkButton(self.top_frame, text="Stop Agent", fg_color="#C62828", hover_color="#8B0000", command=self.stop_agent)
        self.stop_button.pack(side="right", padx=15)

        self.undo_button = ctk.CTkButton(self.top_frame, text="Undo", fg_color="#FFA000", hover_color="#FFB300", command=self.undo_last_edit)
        self.undo_button.pack(side="right", padx=15)

        self.status_label = ctk.CTkLabel(self.top_frame, text="Status: Idle", text_color="#AAAAAA", font=("Arial", 12, "bold"))
        self.status_label.pack(side="right", padx=15)

        # 3-pane layout (The Desktop)
        self.paned = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.paned.pack(fill="both", expand=True, pady=(10, 0))

        # Left sidebar (Project Explorer Card)
        self.left_frame = ctk.CTkFrame(self.paned, width=280, fg_color=("gray90", "gray12"), corner_radius=12, border_width=1, border_color=("gray80", "gray20"))
        self.left_frame.pack(side="left", fill="y", padx=(0, 10))
        
        self.ast_label = ctk.CTkLabel(self.left_frame, text="📁 Project Explorer", font=("Segoe UI", 15, "bold"))
        self.ast_label.pack(pady=(15, 10), padx=15, anchor="w")

        self.ast_tree = ctk.CTkTextbox(self.left_frame, wrap="none", font=("Consolas", 12))
        self.ast_tree.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        # Center (Agent Comm Stream Card)
        self.center_frame = ctk.CTkFrame(self.paned, fg_color=("gray90", "gray12"), corner_radius=12, border_width=1, border_color=("gray80", "gray20"))
        self.center_frame.pack(side="left", fill="both", expand=True, padx=10)
        
        self.chat_label = ctk.CTkLabel(self.center_frame, text="Agent Comm Stream", font=("Segoe UI", 15, "bold"))
        self.chat_label.pack(pady=(15, 5))

        self.chat_log = ctk.CTkScrollableFrame(self.center_frame, fg_color="transparent")
        self.chat_log.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        # Modern Input Bar
        self.input_frame = ctk.CTkFrame(self.center_frame, fg_color="transparent")
        self.input_frame.pack(fill="x", pady=(0, 15), padx=15)

        self.input_entry = ctk.CTkEntry(self.input_frame, placeholder_text="Give D.A.V.E. a task...", font=("Segoe UI", 14), height=45, corner_radius=22, border_width=1)
        self.input_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.input_entry.bind("<Return>", self.send_message)

        self.send_button = ctk.CTkButton(self.input_frame, text="Enter", font=("Segoe UI", 14, "bold"), height=45, corner_radius=22, width=100, fg_color=("#1976d2", "#2196F3"), hover_color=("#1565c0", "#1976D2"), command=self.send_message)
        self.send_button.pack(side="right")

        # Right sidebar (Observability X-Ray)
        self.right_frame = ctk.CTkFrame(self.paned, width=380)
        self.right_frame.pack(side="right", fill="y", padx=(5, 0))

        # PANEL 1: CURRENT STATE
        self.telemetry_frame = ctk.CTkFrame(self.right_frame, fg_color=("gray85", "gray16"), corner_radius=8)
        self.telemetry_frame.pack(fill="x", padx=5, pady=(5, 5))
    
        self.phase_label = ctk.CTkLabel(self.telemetry_frame, text="Phase: SCOUT", font=("Segoe UI", 12, "bold"), text_color=("#1976d2", "#2196F3"))
        self.phase_label.pack(side="left", padx=10, pady=5)
        self.conf_label = ctk.CTkLabel(self.telemetry_frame, text="Conf: 100%", font=("Segoe UI", 12, "bold"), text_color=("#2e7d32", "#4CAF50"))
        self.conf_label.pack(side="left", padx=10, pady=5)
        self.retry_label = ctk.CTkLabel(self.telemetry_frame, text="Retries: 0", font=("Segoe UI", 12, "bold"), text_color=("#c62828", "#F44336"))
        self.retry_label.pack(side="right", padx=10, pady=5)
    
        self.target_label = ctk.CTkLabel(self.right_frame, text="Target: None", font=("Consolas", 11), text_color=("gray40", "gray70"))
        self.target_label.pack(fill="x", padx=10, pady=(0, 5))

        # PANEL 2: TOOL STREAM
        self.tool_stream_label = ctk.CTkLabel(self.right_frame, text="Tool Execution Stream", font=("Segoe UI", 12, "bold"))
        self.tool_stream_label.pack(pady=(5, 0))
        self.tool_stream_text = ctk.CTkTextbox(self.right_frame, height=140, state="disabled", font=("Consolas", 11), fg_color=("gray90", "gray10"), text_color=("#006400", "#00FF00"))
        self.tool_stream_text.pack(fill="x", padx=5, pady=(0, 5))

        # PANEL 3: CONTEXT VIEWER
        self.context_label = ctk.CTkLabel(self.right_frame, text="Context Viewer (Semantic + Memory)", font=("Segoe UI", 12, "bold"))
        self.context_label.pack(pady=(5, 0))
        self.context_text = ctk.CTkTextbox(self.right_frame, height=120, state="disabled", font=("Consolas", 11), fg_color=("gray90", "gray14"), text_color=("black", "#E0E0E0"))
        self.context_text.pack(fill="x", padx=5, pady=(0, 5))

        # PANEL 4: SYSTEM WARNINGS
        self.warning_label = ctk.CTkLabel(self.right_frame, text="System Warnings", font=("Segoe UI", 12, "bold"), text_color=("#c62828", "#F44336"))
        self.warning_label.pack(pady=(5, 0))
        self.warning_text = ctk.CTkTextbox(self.right_frame, height=100, state="disabled", font=("Consolas", 11), fg_color=("#ffebee", "#3b1a1a"), text_color=("#b71c1c", "#FF8A80"))
        self.warning_text.pack(fill="both", expand=True, padx=5, pady=(0, 5))

    def initialize_workspace(self):
        self.target_directory = ctk.filedialog.askdirectory(title="Select Target Workspace Directory")
        if not self.target_directory or not os.path.exists(self.target_directory):
            sys.exit(0)

        os.makedirs(os.path.join(self.target_directory, ".dave_cache"), exist_ok=True)
        self.TaskState["system_state"]["file_heat"] = self._load_observation_memory()
        
        self.add_terminal_output(f"Workspace set to: {self.target_directory}", "green")
        self.add_terminal_output("Indexing workspace in background...", "yellow")
        
        self.after(100, self.check_queues)
        self._refresh_workspace_async()

    def toggle_mode(self):
        self.mode = "agent" if self.mode_switch.get() else "chat"
        self.mode_label_chat.configure(font=("Segoe UI", 12, "normal" if self.mode == "agent" else "bold"))
        self.mode_label_agent.configure(font=("Segoe UI", 12, "bold" if self.mode == "agent" else "normal"))
        
    def toggle_guided_demo(self):
        self.guided_demo_mode = bool(self.demo_switch.get())

        if self.guided_demo_mode:
            self.add_terminal_output("[System] Guided Demo Mode enabled.", "yellow")
        else:
            self.add_terminal_output("[System] Guided Demo Mode disabled.", "yellow")
            
    def _wants_guided_demo_recipe(self, user_input):
        text = user_input.lower()

        demo_words = [
            "make this site",
            "make the site",
            "make this landing page",
            "modernize",
            "make it premium",
            "look premium",
            "look professional",
            "make it beautiful",
            "improve the design",
            "polish the page",
            "apply demo recipe",
            "run demo recipe",
        ]

        return any(phrase in text for phrase in demo_words)


    def _try_guided_demo_recipe(self, user_input, active_history, task_target_directory):
        if not self.guided_demo_mode:
            return False

        if not self._wants_guided_demo_recipe(user_input):
            return False

        recipe_path = os.path.join(task_target_directory, "demo_recipe.json")

        if not os.path.exists(recipe_path):
            reply = (
                "Guided Demo Mode is enabled, but I could not find demo_recipe.json "
                "in the selected workspace."
            )

            self.llm_queue.put((
                "agent_turn",
                {
                    "thought": "Guided Demo Mode was enabled, but no recipe file was found.",
                    "tools": ["apply_demo_recipe"],
                    "reply": reply
                },
                "white"
            ))

            self.llm_queue.put(("terminal", "Error: demo_recipe.json not found.", "red"))
            return True

        self.llm_queue.put((
            "agent_turn",
            {
                "thought": "Guided Demo Mode matched the user request. Applying deterministic recipe.",
                "tools": ["apply_demo_recipe"],
                "reply": "I found a matching guided demo recipe. Applying it now."
            },
            "white"
        ))

        self.llm_queue.put(("terminal", "Applying guided demo recipe...", "yellow"))

        result = apply_demo_recipe(task_target_directory)

        if "STATUS: SUCCESS" in result:
            reply = "Done. I applied the guided demo recipe and updated the project files."
            color = "green"
        else:
            reply = f"The guided demo recipe could not be completed:\n\n{result}"
            color = "red"

            self.llm_queue.put(("terminal", result, color))

            self.llm_queue.put((
                "agent_turn",
                {
                    "thought": "Guided demo recipe execution finished.",
                    "tools": ["apply_demo_recipe"],
                    "reply": reply
                },
                "white"
            ))

            self._append_with_reminder(active_history, "user", user_input)
            self._append_with_reminder(active_history, "assistant", reply)

            self._refresh_workspace_async()

            return True

    def toggle_appearance(self):
        if self.appearance_switch.get() == 1:
            ctk.set_appearance_mode("light")
            self.appearance_switch.configure(text="Dark Mode")
        else:
            ctk.set_appearance_mode("dark")
            self.appearance_switch.configure(text="Light Mode")

    def select_llm(self, value):
        prev = getattr(self, 'llm_mode', 'local')
        self.llm_mode = value

        # Manage FreeLLMAPI proxy lifecycle when selected in the GUI
        try:
            if value == 'freellmapi':
                # start proxy if not already running
                if getattr(self, 'proxy_process', None):
                    return
                proxy_dir = os.path.abspath(os.path.join(os.getcwd(), "..", "freellmapi"))
                if os.path.exists(proxy_dir):
                    try:
                        self.proxy_process = subprocess.Popen(
                            "npm run dev",
                            cwd=proxy_dir,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            shell=True
                        )
                        self.add_terminal_output("[System] FreeLLMAPI proxy started on localhost:3001", "green")
                    except Exception as e:
                        self.add_terminal_output(f"[Error] Could not start FreeLLMAPI proxy: {e}", "red")
                else:
                    self.add_terminal_output("[Error] freellmapi folder not found next to D.A.V.E.", "red")
            else:
                # switching away: stop proxy if we started one
                if getattr(self, 'proxy_process', None):
                    try:
                        self.proxy_process.terminate()
                        self.proxy_process.wait(timeout=2)
                    except Exception:
                        try:
                            self.proxy_process.kill()
                        except Exception:
                            pass
                    self.proxy_process = None
                    self.add_terminal_output("[System] FreeLLMAPI proxy stopped.", "yellow")
        except Exception as e:
            self.add_terminal_output(f"[Error] select_llm handler exception: {e}", "red")

    def stop_agent(self):
        self.stop_flag = True
        
        if self.cancel_event:
            self.cancel_event.set()
            
        # Invalidate the currently running worker so it cannot later relock/unlock stale state
        self.active_task_id += 1
        
        self.status_label.configure(text="Status: Halted", text_color="#F44336")
        self.add_chat_message("SYSTEM: Execution halted by user. Awaiting manual input.", "red")
        
        # Unlock immediately, not after the worker finishes
        self._force_unlock_ui()

    def send_message(self, event=None):
        # Prevent spawning overlapping threads
        if getattr(self, 'is_processing', False):
            return
            
        user_input = self.input_entry.get().strip()
        if not user_input:
            return
        self.input_entry.delete(0, "end")
        self.add_chat_message(f"You: {user_input}", "blue")

        if user_input.lower() in ['exit', 'quit']:
            self.quit()
            return
        if user_input.lower().startswith('/toggle'):
            flag = user_input.split(' ')[-1].strip().lower()
            valid_flags = ["confidence", "debug", "tests"]
            if flag in valid_flags:
                key = f"flag_{flag}"
                current = self.TaskState["system_state"].get(key, False)
                self.TaskState["system_state"][key] = not current
                state_str = "ON" if not current else "OFF"
                self.add_chat_message(f"SYSTEM: {flag.upper()} is now {state_str}.", "yellow")
            else:
                self.add_chat_message("Usage: /toggle <confidence|debug|tests>", "red")
            return

        if user_input.lower() == '/reset':
            self.chat_history = []
            self.ask_history = []
            self.stop_flag = False
            # Decay the heat map instead of completely wiping it to preserve long-term context memory balance
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
                "file_heat": self.TaskState["system_state"].get("file_heat", {})
            }
            self.clear_chat()
            self.add_chat_message("Memory reset. File heat decayed.", "green")
            self._update_telemetry()
            return

        self.stop_flag = False
        self.active_task_id += 1
        task_id = self.active_task_id
        
        self.cancel_event = threading.Event()
        cancel_event = self.cancel_event
        
        self.is_processing = True
        self.input_entry.configure(state="disabled", placeholder_text="D.A.V.E. is thinking...")
        self.send_button.configure(state="disabled")
        self.undo_button.configure(state="disabled")
        self.mode_switch.configure(state="disabled")
        self.llm_selector.configure(state="disabled")
        self.demo_switch.configure(state="disabled")
        self.status_label.configure(text="Status: Running", text_color="#4CAF50")

        threading.Thread(
            target=self._safe_process_message,
            args=(task_id, user_input, cancel_event),
            daemon=True
        ).start()

    def _safe_process_message(self, task_id, user_input, cancel_event):
        """Runs the agent safely and only lets the current task control the UI."""
        try:
            self.process_message(user_input, task_id, cancel_event)
        except Exception as e:
            self.llm_queue.put(("terminal", f"[SYSTEM CRASH] The agent thread encountered a fatal error: {str(e)}", "red"))
        finally:
            if task_id == self.active_task_id:
                self.llm_queue.put(("status", "Idle", "gray"))
                self._update_telemetry()
                self.llm_queue.put(("unlock", task_id, "white"))

    def _force_unlock_ui(self, task_id=None):
        """Unlocks the input only if this is still the active task."""
        if task_id is not None and task_id != self.active_task_id:
            return

        self.is_processing = False
        self.input_entry.configure(state="normal")
        self.input_entry.configure(placeholder_text="Give D.A.V.E. a task...")
        self.send_button.configure(state="normal")
        self.undo_button.configure(state="normal")
        self.mode_switch.configure(state="normal")
        self.llm_selector.configure(state="normal")
        self.demo_switch.configure(state="normal")

        try:
            self.input_entry.focus()
        except Exception:
            pass

    def _save_observation_memory(self):
        """Persist file heat to disk."""
        cache_dir = os.path.join(self.target_directory, ".dave_cache")
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, "observation_memory.json")
        try:
            with open(cache_path, 'w') as f:
                json.dump(self.TaskState["system_state"].get("file_heat", {}), f)
        except Exception:
            pass

    def _load_observation_memory(self):
        """Load file heat from disk."""
        cache_path = os.path.join(self.target_directory, ".dave_cache", "observation_memory.json")
        try:
            if os.path.exists(cache_path):
                with open(cache_path, 'r') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def decay_file_heat(self, refresh=True):
        """Halve all file heat scores to prevent long-term context bloat."""
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

    def _refresh_workspace_async(self, task_id=None):
        """Refresh expensive workspace data without blocking the chat input."""
        if self.refresh_in_progress:
            return

        self.refresh_in_progress = True

        def worker():
            try:
                new_index = self._build_workspace_index()

                try:
                    new_ast = map_codebase(
                        self.target_directory,
                        self.TaskState["system_state"].get("file_heat", {})
                    )
                except Exception:
                    new_ast = getattr(self, "ast_skeleton", "")

                def apply_refresh():
                    self.workspace_index = new_index
                    self.ast_skeleton = new_ast if isinstance(new_ast, str) else ""
                    self.refresh_in_progress = False
                    self.update_ast_display()

                self.after(0, apply_refresh)

            except Exception as e:
                self.refresh_in_progress = False
                self.llm_queue.put(("terminal", f"[Refresh Error] {e}", "red"))

        threading.Thread(target=worker, daemon=True).start()

    def normalize_project_path(self, filename):
        """Ensure consistent path matching for blind-edit guards."""
        if not filename: return None
        if os.path.isabs(filename):
            try: filename = os.path.relpath(filename, self.target_directory)
            except ValueError: pass
        return filename.replace("\\", "/").lstrip("./")

    def _sniff_filename_from_text(self, text):
        """Extract a filename from natural language like 'read main.py and explain it'."""
        if not text: return None
        match = re.search(r'(?<![\w./\\-])([\w./\\-]+\.(?:py|html|css|js|jsx|ts|tsx|json|md|txt))(?![\w-])', text, re.IGNORECASE)
        return self.normalize_project_path(match.group(1)) if match else None

    def _get_workspace_file_candidates(self, preferred_exts=None):
        """Return known workspace files, optionally filtered by extension."""
        files = []
        try:
            for rel_path, meta in self.workspace_index.get("files", {}).items():
                normalized = self.normalize_project_path(rel_path)
                if not normalized: continue
                if not preferred_exts or normalized.lower().endswith(preferred_exts):
                    files.append(normalized)
        except Exception: pass
        if not files:
            valid_exts = preferred_exts or (".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json", ".md", ".txt")
            ignore = {"node_modules", ".git", ".next", "dist", "build", "__pycache__", ".dave_cache", "venv", ".venv", "env"}
            try:
                for root, dirs, filenames in os.walk(self.target_directory):
                    dirs[:] = [d for d in dirs if d not in ignore]
                    for fname in filenames:
                        if fname.lower().endswith(valid_exts):
                            rel_path = os.path.relpath(os.path.join(root, fname), self.target_directory)
                            files.append(self.normalize_project_path(rel_path))
            except Exception: pass
        return sorted(set(files))

    def _resolve_target_filename(self, action=None, user_input="", preferred_exts=None):
        """Resolve a missing filename safely."""
        action = action or {}
        filename = action.get("filename") or action.get("filepath") or action.get("file_path") or action.get("path") or action.get("file") or action.get("target_file") or action.get("target")
        if filename: return self.normalize_project_path(filename)
        sniffed = self._sniff_filename_from_text(user_input)
        if sniffed: return sniffed
        current_target = self.TaskState["system_state"].get("current_target")
        if current_target: return self.normalize_project_path(current_target)
        observed = self.TaskState["system_state"].get("observed_files", [])
        if observed: return self.normalize_project_path(observed[-1])
        candidates = self._get_workspace_file_candidates(preferred_exts=preferred_exts)
        if len(candidates) == 1: return candidates[0]
        py_candidates = self._get_workspace_file_candidates(preferred_exts=(".py",))
        if len(py_candidates) == 1: return py_candidates[0]
        return None
        """Extract a filename from natural language like 'read main.py and explain it'."""
        if not text:
            return None

        match = re.search(
            r'(?<![\w./\\-])([\w./\\-]+\.(?:py|html|css|js|jsx|ts|tsx|json|md|txt))(?![\w-])',
            text,
            re.IGNORECASE
        )

        if not match:
            return None

        return self.normalize_project_path(match.group(1))

    def _canonicalize_action(self, action, user_input=""):
        """Normalize LLM tool arguments so read_file always receives filename."""
        if isinstance(action, str):
            action = {"tool": action}

        if not isinstance(action, dict):
            return {"tool": "none"}

        tool = str(action.get("tool") or "none").lower()
        action["tool"] = tool

        filename = (
            action.get("filename")
            or action.get("filepath")
            or action.get("file_path")
            or action.get("path")
            or action.get("file")
            or action.get("target_file")
            or action.get("target")
        )

        # Special rescue for common chat command: "read main.py"
        if not filename and tool in ["read_file", "search_in_file"]:
            filename = self._sniff_filename_from_text(user_input)

        if filename:
            action["filename"] = self.normalize_project_path(filename)

        return action

    def _wants_direct_file_read(self, text):
        """Detect simple chat-mode file read requests."""
        if not text:
            return False

        return bool(re.search(
            r"\b(read|open|show|display|contents?|tell me|what'?s in|look at)\b",
            text,
            re.IGNORECASE
        ))

    def _wants_file_explanation(self, text):
        """Detect when the user wants more than raw file contents."""
        if not text:
            return False

        return bool(re.search(
            r"\b(explain|report|summari[sz]e|analy[sz]e|review|describe|diagnose|"
            r"what'?s up|what is up|tell me what'?s up|what does it do|"
            r"walk me through|break it down|overview)\b",
            text,
            re.IGNORECASE
        ))

    def _fallback_file_report(self, filename, file_text):
        """Deterministic backup if the LLM explanation fails."""
        lines = file_text.splitlines()
        imports = []
        defs = []
        classes = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                imports.append(stripped)
            elif stripped.startswith("def "):
                defs.append(stripped.split("(")[0].replace("def ", ""))
            elif stripped.startswith("class "):
                classes.append(stripped.split("(")[0].replace("class ", "").replace(":", ""))

        report = f"Quick report for `{filename}`:\n\n"
        report += f"- The file has about {len(lines)} readable lines in the returned window.\n"

        if imports:
            report += f"- Imports detected: {', '.join(imports[:5])}.\n"
        if classes:
            report += f"- Classes detected: {', '.join(classes[:5])}.\n"
        if defs:
            report += f"- Functions detected: {', '.join(defs[:8])}.\n"

        if not imports and not classes and not defs:
            report += "- I did not detect obvious imports, classes, or functions in the returned content.\n"

        report += "\nThe file content was read successfully, but the LLM explanation fallback was used."
        return report

    def _try_direct_chat_read(self, user_input, active_history, task_target_directory, task_llm_mode):
        """
        Reliable Chat Mode shortcut:
        - 'read main.py' displays file contents directly.
        - 'read main.py and explain/report/tell me what is up' reads first, then asks the LLM to explain.
        """
        filename = self._sniff_filename_from_text(user_input)

        if not filename:
            return False

        if not self._wants_direct_file_read(user_input):
            return False

        self.llm_queue.put(("terminal", f"Reading {filename}...", "yellow"))

        file_result = read_file_with_lines(filename, task_target_directory)

        if file_result.startswith("Error:"):
            reply = f"I tried to read `{filename}`, but got this error:\n\n{file_result}"

            self.llm_queue.put((
                "agent_turn",
                {
                    "thought": "Direct chat read attempted, but the file reader returned an error.",
                    "tools": ["read_file"],
                    "reply": reply
                },
                "white"
            ))

            self._append_with_reminder(active_history, "user", user_input)
            self._append_with_reminder(active_history, "assistant", reply)
            self.llm_queue.put(("terminal", "Direct file read failed.", "red"))
            return True

        wants_explanation = self._wants_file_explanation(user_input)

        if not wants_explanation:
            reply = f"Here are the contents of `{filename}`:\n\n{file_result}"

            self.llm_queue.put((
                "agent_turn",
                {
                    "thought": "Direct chat read shortcut used.",
                    "tools": ["read_file"],
                    "reply": reply
                },
                "white"
            ))

            self._append_with_reminder(active_history, "user", user_input)
            self._append_with_reminder(active_history, "assistant", f"Displayed contents of {filename}.")
            self.llm_queue.put(("terminal", "Direct file read complete.", "green"))
            return True

        explanation_prompt = f"""
The user asked:

{user_input}

I have already read the file directly. Do not call tools. Do not request another read.
Explain, report, summarize, or diagnose the file based only on the content below.

FILE: {filename}

CONTENT:
{file_result}

Return JSON only:
{{
  "thought": "short explanation plan",
  "reply": "your useful explanation for the user",
  "actions": []
}}
"""

        response = get_llm_response(
            explanation_prompt,
            [],
            task_target_directory,
            task_llm_mode,
            is_write_operation=False,
            chat_mode=True,
            task_state=None
        )

        if response.get("valid") and response.get("reply"):
            reply = response.get("reply")
        else:
            reply = self._fallback_file_report(filename, file_result)

        self.llm_queue.put((
            "agent_turn",
            {
                "thought": "Direct file read plus explanation shortcut used.",
                "tools": ["read_file"],
                "reply": reply
            },
            "white"
        ))

        self._append_with_reminder(active_history, "user", user_input)
        self._append_with_reminder(active_history, "assistant", reply)
        self.llm_queue.put(("terminal", "Direct file read and explanation complete.", "green"))
        return True

    def _append_with_reminder(self, history, role, content, reminder=None):
        history.append({"role": role, "content": content})
        if reminder:
            history.append({"role": "system", "content": f"<system-reminder>{reminder}</system-reminder>"})

    def process_message(self, user_input, task_id=None, cancel_event=None):
        # Snapshot state so mid-task UI toggles don't corrupt the run
        task_mode = self.mode
        task_llm_mode = self.llm_mode
        task_target_directory = self.target_directory
        
        current_input = user_input
        active_history = self.chat_history if task_mode == "agent" else self.ask_history
        
        if task_mode == "agent":
            if self._try_guided_demo_recipe(user_input, active_history, task_target_directory):
                if task_id == self.active_task_id:
                    self.llm_queue.put(("unlock", task_id, "white"))
                    return
        
        if task_mode == "chat":
            if self._try_direct_chat_read(user_input, active_history, task_target_directory, task_llm_mode):
                # Unlock UI directly since we bypassed the normal loop
                if task_id == self.active_task_id:
                    self.llm_queue.put(("unlock", task_id, "white"))
                return
            self.TaskState["system_state"]["current_phase"] = "Chat"
        else:
            self.TaskState["system_state"]["current_phase"] = "Scout" 
            
        last_used_tool = None
        last_run_success = False
        edit_applied = False
        action_tracker = []
        turn_count = 0
        max_turns = self.max_agent_turns if task_mode == "agent" else self.max_chat_turns

        # Auto-resolve target
        initial_target = self._resolve_target_filename(action={}, user_input=user_input, preferred_exts=(".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css"))
        if initial_target:
            self.TaskState["system_state"]["current_target"] = initial_target

        while not self.stop_flag and not (cancel_event and cancel_event.is_set()):
            turn_count += 1
            if turn_count > max_turns:
                self.llm_queue.put(("terminal", f"[LOOP GUARD] Max turns reached ({max_turns}). Stopping task safely.", "red"))
                break
            self.llm_queue.put(("status", "Thinking...", "yellow"))
            self._update_telemetry()

            # 1. Helmet Injection
            try:
                phase = self.TaskState["system_state"].get("current_phase", "Scout")
                helmet_prompt = f"\n=== ACTIVE HELMET PHASE: {phase.upper()} ===\n"
                
                # Context routing for Scout AND Chat
                if phase in ["Scout", "Chat"] and current_input == user_input:
                    if phase == "Chat":
                        context = f"Project: {os.path.basename(self.target_directory)}\nFiles: {list(self.workspace_index['files'].keys())}\nSkeleton:\n{self.ast_skeleton}"
                        helmet_prompt += f"\n[WORKSPACE CONTEXT]\n{context}\n"
                    try:
                        _, index_data = get_project_skeleton(self.target_directory, self.TaskState["system_state"].get("file_heat", {}))
                        auto_context = semantic_search(user_input, index_data, top_n=2)
                        if "No strong matches" not in auto_context and "Error" not in auto_context:
                            helmet_prompt += f"\n[AUTO-RETRIEVED CONTEXT based on your task]\n{auto_context}\n"
                    except Exception: pass

                if phase == "Scout":
                    helmet_prompt += (
                        "MODE: EXPLORE. Use read_file, scan_directory, search_in_file, or semantic_search only. "
                        "You must identify the exact target filename before planning an edit. "
                        "If the user named a file, use that file. "
                        "If there is only one suitable code file in the workspace, use that file. "
                        "When ready, use update_state to transition to PLAN.\n"
                    )
                elif phase == "Chat":
                    helmet_prompt += "MODE: CHAT. You are a conversational codebase assistant. You may use 'read_file', 'scan_directory', 'search_in_file', 'semantic_search' to find answers. DO NOT use edit tools. You MUST return JSON. Put your answer in the 'reply' field.\n"
                elif phase == "Plan":
                    helmet_prompt += (
                        "MODE: PLAN. Return a JSON object with an actions array containing exactly one update_state action. "
                        "The update_state action must include: analysis, options, decision, reason, confidence. "
                        "The decision must name the target file and the intended edit tool. "
                        "Do not output code in PLAN. Do not use read_file, run_command, or edit tools in PLAN.\n"
                    )
                elif phase == "Execute":
                    helmet_prompt += (
                        "MODE: EXECUTE. You must perform the edit now. "
                        "If using an edit tool, you MUST include complete new_code. "
                        "Do not use run_command until after an edit tool succeeds. "
                        "Allowed edit tools: replace_lines, rewrite_file, replace_named_block, "
                        "insert_before_symbol, insert_after_symbol, create_file. "
                        "If adding a new function that does not already exist, do NOT use replace_named_block. "
                        "Use insert_after_symbol, insert_before_symbol, replace_lines, or rewrite_file instead.\n"
                    )
                    
                augmented_input = f"{helmet_prompt}\n[SYSTEM EVENT / USER INPUT]\n{current_input}"
            except Exception as e:
                augmented_input = current_input

            # 2. Call LLM
            response = get_llm_response(augmented_input, active_history, task_target_directory, task_llm_mode, is_write_operation=(task_mode == "agent"), task_state=self.TaskState, chat_mode=(task_mode == "chat"))

            if self.stop_flag or (cancel_event and cancel_event.is_set()):
                break

            # 3. Handle Invalid format (3-strike recovery)
            if not response.get("valid", False):
                self.TaskState["system_state"]["retry_count"] += 1
                err = response.get("error", "Parse error.")
                err_lower = err.lower()

                self.llm_queue.put((
                    "terminal",
                    f"Brain Error ({self.TaskState['system_state']['retry_count']}/2): {err}",
                    "red"
                ))

                fatal_write_format_error = (
                    "write action" in err_lower
                    or "missing 'new_code'" in err_lower
                    or "missing new_code" in err_lower
                    or "rewrite nuke ban" in err_lower
                    or "proposed implementation" in err_lower
                )

                if fatal_write_format_error:
                    reply = (
                        "I stopped because the model attempted a write action without valid replacement code. "
                        "No file was changed. Try the task again with a specific target file and edit request."
                    )

                    self.llm_queue.put((
                        "agent_turn",
                        {
                            "thought": "Write validation failed before execution. Stopping instead of looping.",
                            "tools": ["write_validation"],
                            "reply": reply
                        },
                        "white"
                    ))

                    self._append_with_reminder(active_history, "user", current_input)
                    self._append_with_reminder(active_history, "assistant", reply)
                    break

                if self.TaskState["system_state"]["retry_count"] >= 2:
                    self.llm_queue.put(("terminal", "[ERR-RECOVERY-FAIL] Format retries exhausted. Stopping safely.", "red"))
                    self.TaskState["system_state"]["retry_count"] = 0
                    break

                self._append_with_reminder(active_history, "assistant", f"CRITICAL FORMAT ERROR: {err}. Fix your JSON.")
                current_input = f"CRITICAL FORMAT ERROR: {err}. Fix your JSON."
                self._update_telemetry()
                continue
            else:
                self.TaskState["system_state"]["retry_count"] = 0

            actions = response.get("actions", [])
            actions = [self._canonicalize_action(a, user_input) for a in actions]

            thought = response.get("thought", "")
            agent_reply = response.get("reply", "...")

            # UI Accordion: Emit the turn widget instead of a flat reply
            planned_tools = [a.get("tool") for a in actions if a.get("tool") and a.get("tool") != "none"]
            turn_data = {
                "thought": thought,
                "tools": planned_tools,
                "reply": agent_reply
            }
            self.llm_queue.put(("agent_turn", turn_data, "white"))

            # 4. Check for task completion OR Chat Mode reply
            if not actions or any(a.get("tool") in ("none", "task_complete") for a in actions):
                # If we are in Agent mode, strictly enforce tests if the flag is on
                if task_mode == "agent" and any(a.get("tool") in ("none", "task_complete") for a in actions) and self.TaskState["system_state"].get("flag_tests", False):
                    if not (last_used_tool == "run_command" and last_run_success):
                        self._append_with_reminder(active_history, "assistant", "SYSTEM: Task cannot be completed. You have not executed 'run_command' to verify your code works.")
                        current_input = "SYSTEM: Task cannot be completed. You have not executed 'run_command' to verify your code works."
                        continue

                self._append_with_reminder(active_history, "user", current_input)
                self._append_with_reminder(active_history, "assistant", agent_reply, RECURRING_REMINDER if task_mode == "agent" else None)
                self.llm_queue.put(("terminal", "Task/Chat Complete.", "green"))

                # Unlock immediately once the agent is logically done
                if task_id == self.active_task_id:
                    self.llm_queue.put(("unlock", task_id, "white"))

                if task_mode == "agent":
                    self.decay_file_heat(refresh=False)
                    self._refresh_workspace_async(task_id)

                break  # <-- This is now properly indented INSIDE the task completion block

            read_results = {}
            fatal_missing_filename = False
            for a in actions:
                if a.get("tool") == "read_file":
                    filename = self._resolve_target_filename(action=a, user_input=user_input, preferred_exts=(".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json", ".md", ".txt"))
                    if filename:
                        a["filename"] = filename
                        self.TaskState["system_state"]["current_target"] = filename
                    if not filename:
                        fatal_missing_filename = True
                        self.llm_queue.put(("terminal", "[FATAL] read_file requested without filename and no safe default target could be resolved.", "red"))
                        break
                    self.llm_queue.put(("terminal", f"Reading {filename}...", "yellow"))
                    read_results[filename] = read_file_with_lines(filename, task_target_directory, a.get("start_line"), a.get("end_line"))

            if fatal_missing_filename:
                self.llm_queue.put(("agent_turn", {"thought": "Read failed: No filename.", "tools": ["read_file"], "reply": "I stopped because I couldn't determine which file to read."}, "white"))
                break

            # 5. Execute Tools
            force_agent_break = False
            guardrail_triggered = False
            for a in actions:
                tool_req = a.get("tool")
                filename = self.normalize_project_path(a.get("filename"))
                new_code = a.get("new_code")
                command = a.get("command")
                action_result = None

                # Chat Mode Guard: Prevent any edits while chatting
                if task_mode == "chat" and tool_req not in ["read_file", "scan_directory", "search_in_file", "semantic_search", "none", "task_complete"]:
                    if tool_req in ["create_file", "replace_lines", "rewrite_file", "replace_named_block", "insert_before_symbol", "insert_after_symbol", "rename_file", "delete_file"]:
                        action_result = f"[ERR-READ-ONLY] You are in Chat Mode. Edit tool '{tool_req}' is blocked. Tell the user to switch to Agent mode to make edits."
                    else:
                        action_result = f"[ERR-UNKNOWN-TOOL] Tool '{tool_req}' does not exist. Did you accidentally put a file path in the 'tool' field? You must use exactly: {{\"tool\": \"read_file\", \"filename\": \"your_path_here\"}}."
                    
                    self.llm_queue.put(("warning", action_result, "red"))
                    self._append_with_reminder(active_history, "assistant", f"{agent_reply}\n[TOOL_RESULT]: {action_result}")
                    guardrail_triggered = True
                    break

                # Batch 5.2 Decision Guard
                try:
                    if self.TaskState["system_state"]["current_phase"] == "Plan" and tool_req not in ["update_state", "none"]:
                        action_result = f"[ERR-PHASE-VIOLATION] You are in PLAN phase. You MUST use 'update_state'."
                        self.llm_queue.put(("warning", action_result, "red"))
                        self._append_with_reminder(active_history, "assistant", f"{agent_reply}\n[TOOL_RESULT]: {action_result}")
                        guardrail_triggered = True
                        break
                except KeyError: pass

                # Batch 5.5 Idempotency Guard
                code_hash = hashlib.md5(new_code.encode()).hexdigest()[:8] if new_code else "none"
                action_signature = f"{tool_req}_{filename}_{a.get('func_name','')}_{command}_{code_hash}"

                if action_signature == self.TaskState["system_state"].get("last_failing_signature") and tool_req not in ["read_file", "scan_directory"]:
                    action_result = f"[ERR-IDEMPOTENCY] Blocked. You just tried this EXACT action and it failed. System Confidence dropped to 0.0."
                    self.llm_queue.put(("warning", action_result, "red"))
                    self.TaskState["system_state"]["confidence"] = 0.0
                    self.TaskState["system_state"]["current_phase"] = "Chat" if self.mode == "chat" else "Scout"
                    self._append_with_reminder(active_history, "assistant", f"{agent_reply}\n[TOOL_RESULT]: {action_result}")
                    guardrail_triggered = True
                    break

                action_tracker.append(action_signature)
                if len(action_tracker) > 5: action_tracker.pop(0)
                if len(action_tracker) == 5 and len(set(action_tracker)) == 1:
                    self.llm_queue.put(("terminal", "BEHAVIORAL LOOP DETECTED. Pausing agent.", "red"))
                    action_tracker = []
                    force_agent_break = True
                    break

                # Execute Action
                def _increase_heat(fname, amount):
                    if fname:
                        # Convert to relative path if LLM used an absolute path
                        if os.path.isabs(fname):
                            try:
                                fname = os.path.relpath(fname, self.target_directory)
                            except ValueError:
                                pass
                        # Normalize path for cache consistency
                        fname = fname.replace("\\", "/")
                        heat = self.TaskState["system_state"]["file_heat"]
                        heat[fname] = heat.get(fname, 0) + amount
                        self._save_observation_memory()

                if tool_req == "read_file":
                    if not filename:
                        filename = self._sniff_filename_from_text(user_input)
                        if filename:
                            a["filename"] = filename

                    if not filename:
                        action_result = "Error: Missing filename. Example: read main.py"
                    else:
                        _increase_heat(filename, 1)
                        action_result = read_results.get(filename)

                        if action_result is None:
                            action_result = read_file_with_lines(
                                filename,
                                task_target_directory,
                                a.get("start_line"),
                                a.get("end_line")
                            )

                        if "Error:" not in action_result and filename not in self.TaskState["system_state"]["observed_files"]:
                            self.TaskState["system_state"]["observed_files"].append(filename)

                elif tool_req == "scan_directory":
                    action_result = scan_directory(self.target_directory)
                    self.llm_queue.put(("terminal", "Scanned workspace", "white"))

                elif tool_req == "search_in_file":
                    search_query = a.get("search_query")
                    if filename and search_query:
                        action_result = search_in_file(filename, search_query, self.target_directory)
                        self.llm_queue.put(("terminal", f"Searched in {filename}", "white"))
                    else:
                        action_result = 'Error: Missing arguments. You MUST provide both. Example: {"tool": "search_in_file", "filename": "ALL", "search_query": "your_search_term"}'

                elif tool_req == "semantic_search":
                    query = a.get("query")
                    if query:
                        self.llm_queue.put(("terminal", f"Semantic search: '{query}'", "yellow"))
                        try:
                            # Use the imported semantic_search and pass the cached index_data
                            _, index_data = get_project_skeleton(self.target_directory, self.TaskState["system_state"].get("file_heat", {}))
                            search_res = semantic_search(query, index_data)
                            
                            # Route the raw string back to the LLM
                            action_result = f"Semantic search results:\n{search_res['context_string']}"
                            
                            # Route the rich metadata to the GUI Context Viewer panel
                            meta_text = f"QUERY: '{query}'\n"
                            for m in search_res.get("metadata", []):
                                meta_text += f" -> {m['file']} ({m['type']}: {m['name']}) [Score: {m['score']}]\n"
                            
                            if not search_res.get("metadata"):
                                meta_text += " -> No strong matches found.\n"
                                
                            self.llm_queue.put(("context_viewer", meta_text, "cyan"))
                        except Exception as e:
                            action_result = f"Error in semantic search: {str(e)}"
                    else:
                        action_result = "Error: Missing 'query' argument."

                elif tool_req == "pin_snippet":
                    content = a.get("content")
                    if filename and content:
                        if any(s.get("content") == content for s in self.TaskState["system_state"]["pinned_snippets"]):
                            action_result = "Snippet already pinned."
                        else:
                            self.TaskState["system_state"]["pinned_snippets"].append({"filename": filename, "description": a.get("description", ""), "content": content})
                            action_result = "Pinned to Working Memory."
                            self.llm_queue.put(("terminal", f"Pinned memory from {filename}", "green"))
                            self._update_telemetry()
                    else: action_result = "Error: missing filename or content"

                elif tool_req == "unpin_snippet":
                    self.TaskState["system_state"]["pinned_snippets"] = []
                    action_result = "Cleared Working Memory."
                    self._update_telemetry()

                # --- FILE MANAGEMENT TOOLS ---
                elif tool_req == "rename_file":
                    old_filename = a.get("old_filename")
                    new_filename = a.get("new_filename")
                    if old_filename and new_filename:
                        action_result = rename_file(old_filename, new_filename, self.target_directory)
                        self.llm_queue.put(("terminal", f"Renamed {old_filename} to {new_filename}", "yellow"))
                    else:
                        action_result = "Error: Missing arguments for rename_file."

                elif tool_req == "delete_file":
                    if filename:
                        action_result = delete_file(filename, self.target_directory)
                        self.llm_queue.put(("terminal", f"Deleted {filename}", "red"))
                        self.after(0, self.update_ast_display)
                    else:
                        action_result = "Error: Missing filename for delete_file."

                # Edits
                elif tool_req in ["create_file", "replace_lines", "rewrite_file", "replace_named_block", "insert_before_symbol", "insert_after_symbol"]:
                    edit_intent = a.get("edit_intent")
                    if not edit_intent:
                        action_result = "[ERR-INTENT-MISSING] MUST provide edit_intent."
                        self.TaskState["system_state"]["current_phase"] = "Plan"
                        self._append_with_reminder(active_history, "assistant", action_result)
                        break

                    if filename not in self.TaskState["system_state"]["observed_files"] and tool_req != "create_file":
                        action_result = f"[ERR-BLIND-EDIT] Blocked. Attempted to edit {filename} without reading."
                        self.llm_queue.put(("warning", action_result, "red"))
                        self.TaskState["system_state"]["current_phase"] = "Scout"
                        self._append_with_reminder(active_history, "assistant", action_result)
                        guardrail_triggered = True
                        break
                    
                    self._push_undo(filename)
                    _increase_heat(filename, 5) # Hot spike on edit
                    if tool_req == "create_file": action_result = create_file(filename, new_code, self.target_directory)
                    elif tool_req == "replace_lines": action_result = replace_lines(filename, a.get("start_line"), a.get("end_line"), new_code, self.target_directory, edit_intent)
                    elif tool_req == "rewrite_file": action_result = rewrite_file(filename, new_code, self.target_directory, edit_intent)
                    elif tool_req == "replace_named_block": action_result = replace_named_block(filename, a.get("symbol_name"), new_code, self.target_directory, edit_intent)
                    elif tool_req == "insert_before_symbol": action_result = insert_before_symbol(filename, a.get("symbol_name"), new_code, self.target_directory, edit_intent)
                    elif tool_req == "insert_after_symbol": action_result = insert_after_symbol(filename, a.get("symbol_name"), new_code, self.target_directory, edit_intent)
                    
                    if isinstance(action_result, str) and (
                        action_result.startswith("Successfully")
                        or "successfully" in action_result.lower()
                        or "applied safe edit" in action_result.lower()
                    ):
                        edit_applied = True
                        self.llm_queue.put(("terminal", f"Edited {filename} via {tool_req}", "green"))
                        self._refresh_workspace_async(task_id)
                    else:
                        self.llm_queue.put(("terminal", f"Edit failed: {action_result[:120]}", "red"))

                elif tool_req == "update_state":
                    self.TaskState["llm_notes"] = {"analysis": a.get("analysis", ""), "options": a.get("options", []), "decision": a.get("decision", ""), "reason": a.get("reason", ""), "confidence": a.get("confidence", 0.0)}
                    plan_str = f"DECISION: {a.get('decision')}\nCONFIDENCE: {a.get('confidence')}\n\nREASONING:\n{a.get('analysis')}\n{a.get('reason')}"
                    self.llm_queue.put(("update_plan", plan_str, "white"))
                    action_result = "State updated successfully."
                    
                elif tool_req == "manage_plan":
                    action = a.get("action")
                    data = a.get("data", "")
                    if action:
                        self.llm_queue.put(("terminal", f"Accessing Master Plan ({action})", "yellow"))
                        try:
                            action_result = manage_plan(action, data, task_target_directory)
                        except Exception as e:
                            action_result = f"Error managing plan: {e}"
                    else:
                        action_result = "Error: Missing 'action' argument for manage_plan."

                elif tool_req == "run_command":
                    if task_mode == "agent" and not edit_applied:
                        action_result = (
                            "[ERR-RUN-BEFORE-EDIT] The agent tried to run a command before any edit was successfully applied. "
                            "Stopping instead of looping."
                        )
                        force_agent_break = True

                    elif command:
                        self.llm_queue.put(("terminal", f"$ {command}", "yellow"))

                        try:
                            action_result = run_command(command, task_target_directory, cancel_event=cancel_event)
                        except TypeError:
                            action_result = run_command(command, task_target_directory)

                            last_used_tool = "run_command"
                            last_run_success = isinstance(action_result, str) and "STATUS: SUCCESS" in action_result
                            self.TaskState["system_ground_truth"]["last_command"] = command
                            self.TaskState["system_ground_truth"]["exit_code"] = 0 if last_run_success else 1
                            self.TaskState["system_ground_truth"]["raw_stderr"] = action_result if not last_run_success else ""

                    else:
                        action_result = "[ERR-NO-COMMAND-FATAL] run_command was requested without a command. Stopping instead of looping."
                        force_agent_break = True

                else:
                    action_result = f"Error: Unknown tool {tool_req}"

                # Handle Errors & Conf
                if isinstance(action_result, str) and (action_result.startswith("Error:") or action_result.startswith("[ERR-") or action_result.startswith("CRITICAL:") or "STATUS: FAILURE" in action_result):
                    self.TaskState["system_state"]["last_failing_signature"] = action_signature
                    self.TaskState["system_state"]["confidence"] = max(0.0, self.TaskState["system_state"].get("confidence", 1.0) - 0.5)
                    self.llm_queue.put(("terminal", f"Failed: {action_result[:100]}...", "red"))
                else:
                    if tool_req not in ["read_file", "scan_directory", "update_state", "none"]:
                        self.TaskState["system_state"]["last_failing_signature"] = None
                        self.TaskState["system_state"]["confidence"] = 1.0

                self._append_with_reminder(active_history, "user", current_input)
                self._append_with_reminder(active_history, "assistant", f'{agent_reply}\n[TOOL_RESULT]: {action_result}', RECURRING_REMINDER if self.mode == "agent" else None)
                last_used_tool = tool_req
                if tool_req != "run_command": last_run_success = False

            if force_agent_break: break

            # 6. Routing (Batch 5.3)
            current_phase = self.TaskState["system_state"]["current_phase"]
            sys_confidence = self.TaskState["system_state"].get("confidence", 1.0)
            next_input = ""
            tools_used = [a.get("tool") for a in actions]
            
            if guardrail_triggered:
                # Emit the event to the Tool Stream panel for immediate UI visibility
                self.llm_queue.put(("terminal", "🛑 SYSTEM GUARDRAIL TRIGGERED: Rerouting agent...", "red"))
                # Bypass normal routing so the LLM actually sees the error!
                next_input = "System Guardrail Triggered. Read the warning and correct your action."
            elif sys_confidence < 0.6 and current_phase not in ["Scout", "Chat"] and self.TaskState["system_state"].get("flag_confidence", True):
                self.TaskState["system_state"]["current_phase"] = "Scout"
                self.TaskState["system_state"]["confidence"] = 1.0 
                next_input = "[SYSTEM WARNING] Confidence critically low. Forced to SCOUT phase."
            elif current_phase == "Chat":
                next_input = f"Tool execution finished. You now have the file context required to answer the user's prompt: '{user_input}'. Deliver your final answer in the 'reply' field and you MUST leave the 'actions' array empty: []."
            elif current_phase == "Scout":
                self.TaskState["system_state"]["current_phase"] = "Plan"
                next_input = "Scouting finished. Transitioning to PLAN phase. You MUST use 'update_state'."
            elif current_phase == "Plan":
                if "update_state" in tools_used:
                    self.TaskState["system_state"]["current_phase"] = "Execute"
                    next_input = "Plan accepted. Transitioning to EXECUTE phase."
                else:
                    next_input = "[ERR-PHASE-02] You are in PLAN phase but did not use 'update_state'."
            elif current_phase == "Execute":
                if "run_command" in tools_used:
                    if isinstance(action_result, str) and "STATUS: SUCCESS" in action_result:
                        next_input = "Execution successful. Observe results. If done, use 'task_complete'."
                    elif isinstance(action_result, str) and "ERROR_TYPE: Syntax" in action_result:
                        next_input = "Syntax Error detected. Remain in EXECUTE phase to apply a patch."
                    elif isinstance(action_result, str) and "ERROR_TYPE:" in action_result:
                        self.TaskState["system_state"]["current_phase"] = "Scout"
                        next_input = "Error detected. Transitioning back to SCOUT phase."
                    else:
                        next_input = "Execution finished. Observe results."
                else:
                    next_input = "Tool execution finished. Observe results. If done, use 'task_complete'. Otherwise, run_command to verify."

            if any(a.get("tool") == "manage_plan" and a.get("action") in ["create", "read"] for a in actions):
                next_input += " (SYSTEM COMMAND: Plan accessed. DO NOT use 'manage_plan' again on your next turn.)"

            current_input = next_input
            if len(active_history) > 10: 
                if self.mode == "agent":
                    self.chat_history = self.chat_history[-10:]
                else:
                    self.ask_history = self.ask_history[-10:]

    def _update_telemetry(self):
        """Pushes state to the UI safely."""
        phase = self.TaskState["system_state"]["current_phase"]
        conf = self.TaskState["system_state"]["confidence"]
        retries = self.TaskState["system_state"]["retry_count"]

        self.llm_queue.put(("telemetry", {"phase": phase, "conf": conf, "retries": retries}, "white"))

        snippets = self.TaskState["system_state"]["pinned_snippets"]

        if not snippets:
            mem_text = "No pinned snippets. Memory is empty."
        else:
            mem_text = "\n".join([f"[{s['filename']}] {s['description']}" for s in snippets])

            if getattr(self, "last_memory_text", None) != mem_text:
                self.last_memory_text = mem_text
                self.llm_queue.put(("memory", mem_text, "white"))

    def _push_undo(self, filename):
        full_path = os.path.join(self.target_directory, filename)
        if os.path.exists(full_path):
            with open(full_path, 'r', encoding='utf-8') as f:
                self.edit_history.append({'file': filename, 'before': f.read()})
            if len(self.edit_history) > 30: self.edit_history.pop(0)

    def undo_last_edit(self):
        if not self.edit_history:
            self.add_chat_message("Undo stack empty.", "red")
            return
        entry = self.edit_history.pop()
        result = rewrite_file(entry['file'], entry['before'], self.target_directory, "Undo")
        self.add_terminal_output(f"Undo: restored {entry['file']}.", "yellow")
        self.add_chat_message(f"Undo completed: restored {entry['file']}.", "yellow")
        self.update_ast_display()

    def check_queues(self):
        processed = 0
        max_per_tick = 50

        while processed < max_per_tick:
            try:
                msg = self.llm_queue.get_nowait()
            except queue.Empty:
                break

            try:
                msg_type = msg[0]
                data = msg[1] if len(msg) > 1 else None
                color = msg[2] if len(msg) > 2 else "white"

                if msg_type == "reply":
                    self.add_chat_message(data, color)
                elif msg_type == "agent_turn":
                    tw = TurnWidget(self.chat_log, data["thought"], data["tools"], data["reply"])
                    tw.pack(fill="x", padx=5, pady=5)
                    self.chat_log._parent_canvas.yview_moveto(1.0)
                elif msg_type == "status":
                    if not self.stop_flag:
                        self.status_label.configure(text=f"Status: {data}", text_color=color if color != "gray" else "#AAAAAA")
                elif msg_type == "terminal":
                    self.add_terminal_output(data, color)
                elif msg_type == "tool_stream":
                    self.tool_stream_text.configure(state="normal")
                    self.tool_stream_text.insert("end", f"{data}\n")
                    self.tool_stream_text.see("end")
                    self.tool_stream_text.configure(state="disabled")
                elif msg_type == "context_viewer":
                    self.context_text.configure(state="normal")
                    self.context_text.insert("end", f"{data}\n")
                    self.context_text.see("end")
                    self.context_text.configure(state="disabled")
                elif msg_type == "memory":
                    self.context_text.configure(state="normal")
                    self.context_text.insert("end", f"{data}\n")
                    self.context_text.see("end")
                    self.context_text.configure(state="disabled")
                elif msg_type == "update_plan":
                    self.context_text.configure(state="normal")
                    self.context_text.insert("end", f"\nPLAN UPDATE:\n{data}\n")
                    self.context_text.see("end")
                    self.context_text.configure(state="disabled")
                elif msg_type == "warning":
                    self.warning_text.configure(state="normal")
                    self.warning_text.insert("end", f"⚠️ {data}\n")
                    self.warning_text.see("end")
                    self.warning_text.configure(state="disabled")
                elif msg_type == "telemetry":
                    if isinstance(data, dict):
                        phase = data.get('phase')
                        conf = data.get('conf', 0.0)
                        retries = data.get('retries', 0)
                        self.phase_label.configure(text=f"Phase: {phase}", text_color="#2196F3" if phase in ["Scout", "Chat"] else "#FFA000" if phase == "Plan" else "#4CAF50")
                        self.conf_label.configure(text=f"Conf: {int(conf*100)}%", text_color="#4CAF50" if conf >= 0.8 else "#F44336")
                        self.retry_label.configure(text=f"Retries: {retries}/3", text_color="#F44336" if retries > 0 else "#AAAAAA")
                    else:
                        self.add_terminal_output(f"Telemetry payload invalid: {data}", "red")
                elif msg_type == "unlock":
                    self._force_unlock_ui(data)

                # Update 'Target' label
                if isinstance(data, dict) and 'target' in data:
                    try:
                        self.target_label.configure(text=f"Target: {data['target']}")
                    except Exception:
                        pass

            except Exception as e:
                try:
                    self.add_terminal_output(f"check_queues handler error: {e}", "red")
                except Exception:
                    pass

            processed += 1

        self.after(10 if processed == max_per_tick else 50, self.check_queues)

    def add_chat_message(self, message, color="white"):
        color_map = {"blue": ("#1565c0", "#64B5F6"), "green": ("#2e7d32", "#81C784"), "red": ("#c62828", "#E57373"), "yellow": ("#f57f17", "#FFF176"), "white": ("black", "#FFFFFF")}
        
        # Determine if it's the User or the System
        font_style = ("Segoe UI", 14)
        if message.startswith("You:"):
            font_style = ("Segoe UI", 14, "bold")
            
        mapped_color = color_map.get(color, ("black", "#FFFFFF"))
        
        msg_label = ctk.CTkLabel(self.chat_log, text=message, text_color=mapped_color, font=font_style, justify="left", wraplength=650)
        msg_label.pack(anchor="w", padx=10, pady=(10, 5))
        self.chat_log._parent_canvas.yview_moveto(1.0)

    def add_terminal_output(self, message, color="white"):
        self.tool_stream_text.configure(state="normal")
        self.tool_stream_text.insert("end", f"{message}\n")
        self.tool_stream_text.see("end")
        self.tool_stream_text.configure(state="disabled")

    def clear_chat(self):
        for widget in self.chat_log.winfo_children():
            widget.destroy()

    def update_ast_display(self):
        """Builds the AST tree string in a background thread to prevent UI stutter."""
        
        def worker():
            try:
                tree_str = f"📁 {os.path.basename(self.target_directory)}\n"
                valid_exts = (".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json", ".md")
                ignore = {"node_modules", ".git", ".next", "dist", "build", "__pycache__", ".dave_cache"}

                heat_dict = self.TaskState["system_state"].get("file_heat", {})
            
                def get_heat_bar(fname):
                    score = heat_dict.get(fname, 0)
                    if score >= 10: return "[███]"
                    elif score >= 5: return "[██░]"
                    elif score >= 1: return "[█░░]"
                    else: return "[░░░]"

                for root, dirs, files in os.walk(self.target_directory):
                    dirs[:] = [d for d in dirs if d not in ignore]
                    level = root.replace(self.target_directory, '').count(os.sep)
                    indent = ' ' * 4 * level
                    if level > 0:
                        tree_str += f"{indent}📂 {os.path.basename(root)}\n"
                    sub_indent = ' ' * 4 * (level + 1)
                    for f in files:
                        if f.endswith(valid_exts):
                            # Fix for nested files: use exact relative path matching for the heat dictionary
                            rel_path = os.path.relpath(os.path.join(root, f), self.target_directory).replace("\\", "/")
                            bar = get_heat_bar(rel_path)
                            tree_str += f"{sub_indent}{bar} 📄 {f}\n"

                final_text = "=== PROJECT TREE ===\n" + tree_str + "\n=== AST METADATA ===\n"
                ast_skel = getattr(self, "ast_skeleton", "")
                final_text += ast_skel if ast_skel else "No AST map available."

                def apply_tree():
                    self.ast_tree.configure(state="normal")
                    self.ast_tree.delete("0.0", "end")
                    self.ast_tree.insert("end", final_text)
                    self.ast_tree.configure(state="disabled")

                self.after(0, apply_tree)
            except Exception as e:
                self.llm_queue.put(("terminal", f"[AST Update Error] {e}", "red"))

        threading.Thread(target=worker, daemon=True).start()

    def _build_workspace_index(self):
        index = {"files": {}}
        valid_extensions = (".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json", ".md")
        ignore_folders = {"node_modules", ".git", ".next", "dist", "build", "__pycache__"}
        for root, dirs, files in os.walk(self.target_directory):
            dirs[:] = [d for d in dirs if d not in ignore_folders]
            for f in files:
                if f.endswith(valid_extensions):
                    rel_path = os.path.relpath(os.path.join(root, f), self.target_directory)
                    ext = os.path.splitext(f)[1]
                    index["files"][rel_path] = {"lines": 0, "ext": ext}
        return index

if __name__ == "__main__":
    try:
        app = DAVEApp()
        app.mainloop()
    except KeyboardInterrupt:
        print("\n[System] Force quitting D.A.V.E. GUI...")
        try:
            app.destroy()
        except Exception:
            pass
        sys.exit(0)