import flet as ft
import threading
import queue
import os
import sys
import json
import re
import hashlib
import time
import subprocess
import asyncio

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
from tools.demo_recipe_runner import apply_demo_recipe

RECURRING_REMINDER = "REMINDER: Output complete code. Use double quotes for contractions."


class DAVEApp:
    """Main application class — backend logic + Flet UI orchestration."""

    def __init__(self, page: ft.Page):
        self.page = page

        # ── State variables (identical to gui.py) ──────────────────────
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

        # ── Unified state machine (identical to gui.py) ────────────────
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

        # ── Thread-safe queue (identical to gui.py) ────────────────────
        self.llm_queue = queue.Queue()

        # ── File Picker Registration ──────────────────────────────────
        self.file_picker = ft.FilePicker()

        # ── Build the Flet UI ──────────────────────────────────────────
        self._build_ui()

        # ── Start the queue consumer daemon ───────────────────────────
        self._start_queue_consumer()

        # ── Prompt for workspace on mount ──────────────────────────────
        self.page.on_mount = lambda _: self.page.run_task(self.pick_workspace)

        # ═══════════════════════════════════════════════════════════════════
        #  UI CONSTRUCTION ("Odysseus" Standard)
        # ═══════════════════════════════════════════════════════════════════

    def _build_ui(self):
        p = self.page
        p.title = "D.A.V.E. — Direct Agentic Versioning Engine"
        p.theme_mode = ft.ThemeMode.DARK
        p.padding = 0
        p.bgcolor = ft.Colors.GREY_900
        p.theme = ft.Theme(font_family="Inter")

        # ── Reusable constants ─────────────────────────────────────
        CARD_BG = ft.Colors.GREY_900
        CARD_ALT = "#1e1e1e"
        BORDER_RADIUS = 12

        # ── NavigationRail ──────────────────────────────────────────
        self.rail = ft.NavigationRail(
            selected_index=1,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=80,
            min_extended_width=180,
            bgcolor=ft.Colors.BLACK,
            indicator_color=ft.Colors.BLUE_900,
            leading=ft.Icon(ft.Icons.AUTO_AWESOME, color=ft.Colors.BLUE_400, size=28),
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.FOLDER_OPEN,
                    selected_icon=ft.Icons.FOLDER_OPEN,
                    label="Explorer",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.ANALYTICS_OUTLINED,
                    selected_icon=ft.Icons.ANALYTICS,
                    label="Telemetry",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SETTINGS_OUTLINED,
                    selected_icon=ft.Icons.SETTINGS,
                    label="Settings",
                ),
            ],
            on_change=self._on_rail_change,
        )

        # ── Top bar ────────────────────────────────────────────────
        try:
            self.page._services.register_service(self.file_picker)
        except Exception:
            self.page.overlay.append(self.file_picker)
        self.mode_switch = ft.Switch(label="Agent Mode", value=True, on_change=self._on_mode_toggle)
        self.llm_dropdown = ft.Dropdown(
            value="local",
            options=[
                ft.dropdown.Option("local"),
                ft.dropdown.Option("api"),
                ft.dropdown.Option("freellmapi"),
            ],
            width=120,
            on_select=self._on_llm_change,
        )
        self.appearance_switch = ft.Switch(label="Light Mode", value=False, on_change=self._on_appearance_toggle)
        self.demo_switch = ft.Switch(label="Guided Demo", value=False, on_change=self._on_demo_toggle)
        self.status_text = ft.Text("Status: Idle", color=ft.Colors.GREY_400, size=13, weight="bold")

        self.top_bar = ft.Container(
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=15,
                        controls=[
                            ft.Icon(ft.Icons.AUTO_AWESOME, color=ft.Colors.BLUE_400, size=26),
                            ft.Text("D.A.V.E.", size=20, weight="bold", color=ft.Colors.GREY_100),
                            ft.VerticalDivider(width=1, color=ft.Colors.GREY_800),
                            self.mode_switch,
                            ft.Text("LLM:", size=12, color=ft.Colors.GREY_400),
                            self.llm_dropdown,
                            self.appearance_switch,
                            self.demo_switch,
                        ],
                    ),
                    ft.Row(
                        spacing=10,
                        controls=[
                            self.status_text,
                            ft.Button(
                                "Undo",
                                icon=ft.Icons.UNDO,
                                color=ft.Colors.AMBER_400,
                                bgcolor=ft.Colors.GREY_800,
                                on_click=lambda e: self.undo_last_edit(),
                            ),
                            ft.Button(
                                "Stop Agent",
                                icon=ft.Icons.STOP,
                                color=ft.Colors.RED_400,
                                bgcolor=ft.Colors.GREY_800,
                                on_click=lambda e: self.stop_agent(),
                            ),
                        ],
                    ),
                ],
            ),
            padding=ft.Padding.only(left=16, right=16, top=8, bottom=8),
            bgcolor=ft.Colors.BLACK,
        )

        # ── Chat stream (center panel) ─────────────────────────────
        self.chat_list = ft.ListView(expand=True, spacing=8, auto_scroll=True)

        self.input_field = ft.TextField(
            hint_text="Give D.A.V.E. a task...",
            border_radius=25,
            filled=True,
            expand=True,
            bgcolor=ft.Colors.GREY_800,
            border_color=ft.Colors.TRANSPARENT,
            text_size=14,
            on_submit=lambda e: self.send_message(),
        )
        self.send_btn = ft.FloatingActionButton(
            icon=ft.Icons.SEND_ROUNDED,
            bgcolor=ft.Colors.BLUE_600,
            on_click=lambda e: self.send_message(),
        )

        center_chat_col = ft.Column(
            expand=True,
            spacing=0,
            controls=[
                ft.Container(
                    content=ft.Column([
                        ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.BOLT, color=ft.Colors.YELLOW_600, size=18),
                                ft.Text("Agent Comm Stream", size=15, weight="bold"),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Divider(height=1, color=ft.Colors.GREY_800),
                        self.chat_list,
                        ft.Container(
                            content=ft.Row(
                                controls=[self.input_field, self.send_btn],
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            padding=ft.Padding.only(top=8),
                            shadow=ft.BoxShadow(
                                spread_radius=2,
                                blur_radius=12,
                                color=ft.Colors.with_opacity(0.3, ft.Colors.BLACK),
                                offset=ft.Offset(0, -2),
                            ),
                        ),
                    ]),
                    bgcolor=CARD_BG,
                    border_radius=BORDER_RADIUS,
                    padding=15,
                    expand=True,
                ),
            ],
        )

        # ── Right panel: Observability ─────────────────────────────
        # Phase badge (wrapped in animated container for smooth phase transitions)
        self.phase_container = ft.Container(
            content=ft.Text("Phase: SCOUT", color=ft.Colors.BLUE_400, weight="bold", size=13),
            animate=ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT),
            padding=ft.Padding.all(8),
        )
        self.conf_container = ft.Container(
            content=ft.Text("Conf: 100%", color=ft.Colors.GREEN_400, weight="bold", size=13),
            animate=ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT),
            padding=ft.Padding.all(8),
        )
        self.retry_text = ft.Text("Retries: 0/3", color=ft.Colors.GREY_500, size=12)
        self.target_label = ft.Text("Target: None", font_family="Consolas", size=11, color=ft.Colors.GREY_400)

        self.tool_stream_list = ft.ListView(height=150, auto_scroll=True, spacing=1)
        self.tool_stream_placeholder = ft.Text("> Ready...", color=ft.Colors.GREEN_ACCENT_400, size=12, font_family="Consolas")
        self.tool_stream_list.controls.append(self.tool_stream_placeholder)

        self.context_list = ft.ListView(height=120, auto_scroll=True, spacing=1)
        self.context_placeholder = ft.Text("No context loaded.", color=ft.Colors.GREY_500, size=12, font_family="Consolas")
        self.context_list.controls.append(self.context_placeholder)

        self.warning_list = ft.ListView(height=100, auto_scroll=True, spacing=1)
        self.warning_placeholder = ft.Text("No active system warnings.", color=ft.Colors.GREY_500, size=11, font_family="Consolas")
        self.warning_list.controls.append(self.warning_placeholder)

        right_panel = ft.Container(
            width=360,
            content=ft.Column(
                spacing=0,
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    ft.Container(
                        content=ft.Column([
                            ft.Row(
                                controls=[
                                    ft.Icon(ft.Icons.ANALYTICS, color=ft.Colors.CYAN_400, size=18),
                                    ft.Text("Observability", size=15, weight="bold"),
                                ],
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Divider(height=1, color=ft.Colors.GREY_800),
                            ft.Container(
                                content=ft.Column([
                                    ft.Row(spacing=8, controls=[self.phase_container, self.conf_container, self.retry_text], wrap=True),
                                    self.target_label,
                                ]),
                                padding=ft.Padding.symmetric(vertical=8),
                            ),
                        ]),
                        bgcolor=CARD_BG,
                        border_radius=BORDER_RADIUS,
                        padding=15,
                    ),
                    ft.Container(height=8),
                    ft.Container(
                        content=ft.Column([
                            ft.Text("Tool Execution Stream", weight="bold", size=13, color=ft.Colors.GREY_400),
                            ft.Container(content=self.tool_stream_list, bgcolor=ft.Colors.BLACK, border_radius=8, padding=10, height=160),
                        ]),
                        bgcolor=CARD_BG,
                        border_radius=BORDER_RADIUS,
                        padding=15,
                    ),
                    ft.Container(height=8),
                    ft.Container(
                        content=ft.Column([
                            ft.Text("Context Viewer", weight="bold", size=13, color=ft.Colors.GREY_400),
                            ft.Container(content=self.context_list, bgcolor=ft.Colors.BLACK, border_radius=8, padding=10, height=130),
                        ]),
                        bgcolor=CARD_BG,
                        border_radius=BORDER_RADIUS,
                        padding=15,
                    ),
                    ft.Container(height=8),
                    ft.Container(
                        content=ft.Column([
                            ft.Text("System Warnings", weight="bold", size=13, color=ft.Colors.RED_400),
                            ft.Container(content=self.warning_list, bgcolor="#2E0A0A", border_radius=8, padding=10, height=110),
                        ]),
                        bgcolor=CARD_BG,
                        border_radius=BORDER_RADIUS,
                        padding=15,
                    ),
                ],
            ),
        )

        # ── Explorer view (TreeView) ───────────────────────────────
        self.tree_container = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO)
        self.ast_meta_text = ft.Text("No AST map available.", font_family="Consolas", size=12, color=ft.Colors.GREY_400)
        self.explorer_view = ft.Container(
            expand=True,
            content=ft.Column([
                ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.FOLDER_SPECIAL, color=ft.Colors.BLUE_400, size=18),
                        ft.Text("Project Explorer", size=15, weight="bold"),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Divider(height=1, color=ft.Colors.GREY_800),
                ft.Container(
                    expand=True,
                    content=ft.Column([
                        ft.Text("=== PROJECT TREE ===", size=13, weight="bold", color=ft.Colors.BLUE_400),
                        self.tree_container,
                        ft.Divider(height=1, color=ft.Colors.GREY_800),
                        ft.Text("=== AST METADATA ===", size=13, weight="bold", color=ft.Colors.CYAN_400),
                        self.ast_meta_text,
                    ], scroll=ft.ScrollMode.AUTO),
                    padding=ft.Padding.only(top=4),
                ),
            ]),
            bgcolor=CARD_BG,
            border_radius=BORDER_RADIUS,
            padding=15,
        )

        # ── Settings view ──────────────────────────────────────────
        self.settings_view = ft.Container(
            expand=True,
            content=ft.Column([
                ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.SETTINGS, color=ft.Colors.GREY_400, size=18),
                        ft.Text("Settings", size=15, weight="bold"),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Divider(height=1, color=ft.Colors.GREY_800),
                ft.Container(
                    content=ft.Column([
                        ft.Text("Turn Limits", weight="bold", size=13, color=ft.Colors.GREY_400),
                        ft.Row([
                            ft.Text("Agent max turns:", size=12),
                            ft.TextField(value="6", width=60, text_size=12, on_change=lambda e: setattr(self, 'max_agent_turns', int(e.control.value or 6))),
                        ]),
                        ft.Row([
                            ft.Text("Chat max turns:", size=12),
                            ft.TextField(value="3", width=60, text_size=12, on_change=lambda e: setattr(self, 'max_chat_turns', int(e.control.value or 3))),
                        ]),
                    ], spacing=10),
                    padding=ft.Padding.all(12),
                ),
            ]),
            bgcolor=CARD_BG,
            border_radius=BORDER_RADIUS,
            padding=15,
        )

        # ── Telemetry view (default): chat + right panel ───────────
        self.telemetry_view = ft.Row(
            expand=True,
            spacing=8,
            controls=[center_chat_col, right_panel],
        )

        # ── Content stack (rail-driven swapping) ────────────────────
        self.content_stack = ft.Column(
            expand=True,
            spacing=0,
            controls=[
                self.top_bar,
                ft.Divider(height=1, color=ft.Colors.GREY_800),
                ft.Container(
                    expand=True,
                    content=ft.Stack(
                        controls=[
                            self.explorer_view,
                            self.telemetry_view,
                            self.settings_view,
                        ]
                    ),
                    padding=ft.Padding.all(12),
                ),
            ],
        )

        # ── Assemble the page ──────────────────────────────────────
        p.add(
            ft.Row(
                expand=True,
                spacing=0,
                controls=[
                    self.rail,
                    ft.VerticalDivider(width=1, color=ft.Colors.GREY_800),
                    self.content_stack,
                ],
            )
        )
        p.update()

        # Initially only telemetry visible
        self.explorer_view.visible = False
        self.telemetry_view.visible = True
        self.settings_view.visible = False
        p.update()

        # ═══════════════════════════════════════════════════════════════════
        #  QUEUE CONSUMER (replaces CTk check_queues + after loop)
        # ═══════════════════════════════════════════════════════════════════

    def _start_queue_consumer(self):
        """Background daemon thread that drains the llm_queue and updates UI."""
        def consumer():
            while True:
                try:
                    msg = self.llm_queue.get(timeout=0.1)
                    self._dispatch_message(msg)
                    self.page.update()
                except queue.Empty:
                    time.sleep(0.02)

        thread = threading.Thread(target=consumer, daemon=True)
        thread.start()

        # ═══════════════════════════════════════════════════════════════════
        #  NAVIGATION & SETTINGS HANDLERS
        # ═══════════════════════════════════════════════════════════════════

    def _on_rail_change(self, e):
        idx = e.control.selected_index
        self.explorer_view.visible = idx == 0
        self.telemetry_view.visible = idx == 1
        self.settings_view.visible = idx == 2
        self.page.update()

    def _on_mode_toggle(self, e):
        self.mode = "agent" if e.control.value else "chat"
        self.mode_switch.label = "Agent Mode" if self.mode == "agent" else "Chat Mode"
        self.page.update()

    def _on_llm_change(self, e):
        prev = self.llm_mode
        self.llm_mode = e.control.value
        # Manage FreeLLMAPI proxy lifecycle
        try:
            if self.llm_mode == "freellmapi":
                if getattr(self, "proxy_process", None):
                    return
                proxy_dir = os.path.abspath(os.path.join(os.getcwd(), "..", "freellmapi"))
                if os.path.exists(proxy_dir):
                    try:
                        self.proxy_process = subprocess.Popen(
                            "npm run dev",
                            cwd=proxy_dir,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            shell=True,
                        )
                        self._show_toast("FreeLLMAPI proxy started on localhost:3001", "green")
                    except Exception as ex:
                        self._show_toast(f"Could not start proxy: {ex}", "red")
                else:
                    self._show_toast("freellmapi folder not found next to D.A.V.E.", "red")
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
        except Exception as ex:
            pass

    def _on_appearance_toggle(self, e):
        is_light = e.control.value
        self.page.theme_mode = ft.ThemeMode.LIGHT if is_light else ft.ThemeMode.DARK
        self.appearance_switch.label = "Dark Mode" if is_light else "Light Mode"
        self.page.update()

    def _on_demo_toggle(self, e):
        self.guided_demo_mode = e.control.value
        self._add_terminal_msg(
            f"[System] Guided Demo Mode {'enabled' if self.guided_demo_mode else 'disabled'}.",
            ft.Colors.YELLOW_ACCENT_400,
        )

        # ═══════════════════════════════════════════════════════════════════
        #  WORKSPACE SELECTION
        # ═══════════════════════════════════════════════════════════════════

    async def pick_workspace(self):
        """Launches the native OS directory browser picker."""
        path = await self.file_picker.get_directory_path(dialog_title="Select Target Workspace Directory")
        if path:
            self._init_workspace(path)
        else:
            if not self.target_directory:
                self._add_chat_message("SYSTEM: No workspace selected. Use the 'Select Workspace' button or type a task to begin.", ft.Colors.YELLOW_400)

    def _init_workspace(self, path):
        if not path or not os.path.isdir(path):
            self._show_toast(f"Invalid workspace: {path}", "red")
            return
        self.target_directory = path
        os.makedirs(os.path.join(self.target_directory, ".dave_cache"), exist_ok=True)
        self.TaskState["system_state"]["file_heat"] = self._load_observation_memory()
        self._add_terminal_msg(f"Workspace set to: {self.target_directory}", ft.Colors.GREEN_400)
        self._add_terminal_msg("Indexing workspace in background...", ft.Colors.YELLOW_400)
        self._refresh_workspace_async()
        self._show_toast(f"Workspace loaded: {os.path.basename(path)}", "blue")

    def _dispatch_message(self, msg):
        try:
            msg_type = str(msg[0])
            data = msg[1] if len(msg) > 1 else None
            color = msg[2] if len(msg) > 2 else "white"
        except (IndexError, TypeError):
            return

        color_map = {
            "white": ft.Colors.GREY_100,
            "blue": ft.Colors.BLUE_400,
            "green": ft.Colors.GREEN_400,
            "red": ft.Colors.RED_400,
            "yellow": ft.Colors.YELLOW_400,
            "cyan": ft.Colors.CYAN_400,
        }
        flet_color = color_map.get(color, ft.Colors.GREY_100)

        try:
            if msg_type == "reply":
                self._add_chat_message(str(data), flet_color)

            elif msg_type == "agent_turn" and isinstance(data, dict):
                thought = data.get("thought", "")
                tools = data.get("tools", [])
                reply = data.get("reply", "")
                self._add_turn_widget(thought, tools, reply)

            elif msg_type == "status":
                if not self.stop_flag:
                    text = str(data)
                    c = flet_color if color != "gray" else ft.Colors.GREY_500
                    self.status_text.value = f"Status: {text}"
                    self.status_text.color = c
                    self.status_text.update()

            elif msg_type == "terminal":
                self._add_terminal_msg(str(data), flet_color)

            elif msg_type == "telemetry" and isinstance(data, dict):
                phase = data.get("phase", "Scout")
                conf = data.get("conf", 0.0)
                retries = data.get("retries", 0)
                self._update_telemetry_ui(phase, conf, retries)

            elif msg_type == "context_viewer":
                self._add_context_msg(str(data), flet_color)

            elif msg_type == "memory":
                self._add_context_msg(str(data), flet_color)

            elif msg_type == "update_plan":
                self._add_context_msg(f"\nPLAN UPDATE:\n{data}\n", flet_color)

            elif msg_type == "warning":
                if self.warning_placeholder in self.warning_list.controls:
                    self.warning_list.controls.remove(self.warning_placeholder)
                self.warning_list.controls.append(ft.Text(str(data), color=ft.Colors.RED_400, size=12, font_family="Consolas"))
                self._show_toast(str(data), "red")

            elif msg_type == "unlock":
                self._force_unlock_ui(data)

            elif msg_type == "tool_stream":
                self._add_terminal_msg(str(data), flet_color)

        except Exception as ex:
            try:
                self._add_terminal_msg(f"dispatch error: {ex}", ft.Colors.RED_400)
            except Exception:
                pass

    def _add_terminal_msg(self, text, color=ft.Colors.GREEN_ACCENT_400):
        if self.tool_stream_placeholder in self.tool_stream_list.controls:
            self.tool_stream_list.controls.remove(self.tool_stream_placeholder)
        self.tool_stream_list.controls.append(
            ft.Text(str(text), color=color, size=12, font_family="Consolas")
        )

    def _add_context_msg(self, text, color=ft.Colors.GREY_300):
        if self.context_placeholder in self.context_list.controls:
            self.context_list.controls.remove(self.context_placeholder)
        self.context_list.controls.append(
            ft.Text(str(text), color=color, size=12, font_family="Consolas")
        )

            # ═══════════════════════════════════════════════════════════════════
            #  UI MUTATION HELPERS
            # ═══════════════════════════════════════════════════════════════════

    def _add_chat_message(self, text, color=ft.Colors.GREY_100):
        is_user = str(text).startswith("You:")
        align = ft.MainAxisAlignment.END if is_user else ft.MainAxisAlignment.START
        weight = "bold" if is_user else "normal"
        self.chat_list.controls.append(
            ft.Container(
                content=ft.Row(
                    alignment=align,
                    controls=[
                        ft.Container(
                            content=ft.Column([
                                ft.Text(str(text), color=color, size=13, weight=weight),
                            ]),
                            bgcolor=ft.Colors.GREY_900,
                            border_radius=10,
                            padding=ft.Padding.all(10),
                        ),
                    ],
                ),
                margin=ft.Margin.symmetric(vertical=2),
            )
        )
        self.chat_list.auto_scroll = True

    def _add_turn_widget(self, thought, tools, reply):
        """Accordion-style TurnWidget for agent thought process."""
        reply_text = reply if reply and reply.strip() and reply != "..." else "Executing task..."
        is_expanded = [False]

        toggle_btn = ft.IconButton(
            icon=ft.Icons.EXPAND_MORE,
            icon_size=18,
            tooltip="Toggle details",
        )
        reply_label = ft.Text(
            f"D.A.V.E.: {reply_text}",
            color=ft.Colors.GREEN_400,
            weight="bold",
            size=13,
        )
        header = ft.Row(
            controls=[toggle_btn, reply_label],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        is_expanded = [False]

        details_parts = []
        if thought:
            details_parts.append(ft.Text("Thinking:", weight="bold", size=11, color=ft.Colors.GREY_400))
            details_parts.append(ft.Text(thought, size=11, color=ft.Colors.GREY_300))
        if tools:
            details_parts.append(ft.Text("Tools Planned:", weight="bold", size=11, color=ft.Colors.GREY_400))
            for t in tools:
                details_parts.append(
                    ft.Text(f"  • {t}", size=11, color=ft.Colors.GREEN_ACCENT_400, font_family="Consolas")
                )

        details_container = ft.Container(
            content=ft.Column(details_parts, spacing=4),
            bgcolor="#1a1a1a",
            border_radius=6,
            padding=ft.Padding.all(10),
            visible=False,
            margin=ft.Margin.only(top=4),
        )

        def on_toggle(e):
            is_expanded[0] = not is_expanded[0]
            details_container.visible = is_expanded[0]
            toggle_btn.icon = ft.Icons.EXPAND_LESS if is_expanded[0] else ft.Icons.EXPAND_MORE
            self.page.update()

        toggle_btn.on_click = on_toggle

        card = ft.Container(
            content=ft.Column(
                controls=[
                    header,
                    ft.Divider(height=1, color=ft.Colors.GREY_800),
                    details_container,
                ],
                spacing=4,
            ),
            bgcolor=ft.Colors.GREY_900,
            border_radius=10,
            padding=ft.Padding.all(12),
            margin=ft.Margin.symmetric(vertical=4),
        )
        self.chat_list.controls.append(card)

    def _update_telemetry_ui(self, phase, conf, retries):
        # Phase badge
        phase_colors = {
            "Scout": ft.Colors.BLUE_400,
            "Chat": ft.Colors.BLUE_400,
            "Plan": ft.Colors.AMBER_400,
            "Execute": ft.Colors.GREEN_400,
        }
        phase_color = phase_colors.get(phase, ft.Colors.BLUE_400)
        self.phase_container.content = ft.Text(
            f"Phase: {phase}", color=phase_color, weight="bold", size=13
        )

        # Confidence badge
        conf_pct = int(conf * 100)
        conf_color = ft.Colors.GREEN_400 if conf >= 0.8 else ft.Colors.RED_400
        self.conf_container.content = ft.Text(
            f"Conf: {conf_pct}%", color=conf_color, weight="bold", size=13
        )

        # Retries
        retry_color = ft.Colors.RED_400 if retries > 0 else ft.Colors.GREY_500
        self.retry_text.value = f"Retries: {retries}/3"
        self.retry_text.color = retry_color

        # Target Tracker Sync
        current_target = self.TaskState["system_state"].get("current_target", "None")
        self.target_label.value = f"Target: {current_target}"

    def _show_toast(self, message, level="info"):
        color_map = {
            "green": ft.Colors.GREEN_900,
            "red": ft.Colors.RED_900,
            "blue": ft.Colors.BLUE_900,
            "yellow": ft.Colors.AMBER_900,
            "info": ft.Colors.GREY_800,
        }
        bg = color_map.get(level, ft.Colors.GREY_800)
        snack = ft.SnackBar(
            content=ft.Text(str(message), size=13),
            bgcolor=bg,
            duration=4000,
        )
        self.page.show_dialog(snack)

    def _force_unlock_ui(self, task_id=None):
        if task_id is not None and task_id != self.active_task_id:
            return
        self.is_processing = False
        self.input_field.disabled = False
        self.input_field.hint_text = "Give D.A.V.E. a task..."
        self.send_btn.disabled = False
        self.mode_switch.disabled = False
        self.llm_dropdown.disabled = False
        self.appearance_switch.disabled = False
        self.demo_switch.disabled = False
        self.page.update()

        # ═══════════════════════════════════════════════════════════════════
        #  BACKEND METHODS (identical logic to gui.py, Flet UI mutations)
        # ═══════════════════════════════════════════════════════════════════

    def stop_agent(self):
        self.stop_flag = True
        if self.cancel_event:
            self.cancel_event.set()
        self.active_task_id += 1
        self.status_text.value = "Status: Halted"
        self.status_text.color = ft.Colors.RED_400
        self._add_chat_message("SYSTEM: Execution halted by user. Awaiting manual input.", ft.Colors.RED_400)
        self._force_unlock_ui()

    def send_message(self):
        if self.is_processing:
            return
        user_input = self.input_field.value.strip()
        if not user_input:
            return
        self.input_field.value = ""
        self._add_chat_message(f"You: {user_input}", ft.Colors.BLUE_400)

        if user_input.lower() in ("exit", "quit"):
            self.page.window_destroy()
            return
        if user_input.lower().startswith("/toggle"):
            flag = user_input.split(" ")[-1].strip().lower()
            valid_flags = ["confidence", "debug", "tests"]
            if flag in valid_flags:
                key = f"flag_{flag}"
                current = self.TaskState["system_state"].get(key, False)
                self.TaskState["system_state"][key] = not current
                state_str = "ON" if not current else "OFF"
                self._add_chat_message(f"SYSTEM: {flag.upper()} is now {state_str}.", ft.Colors.YELLOW_400)
            else:
                self._add_chat_message("Usage: /toggle <confidence|debug|tests>", ft.Colors.RED_400)
            self.page.update()
            return

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
                "file_heat": self.TaskState["system_state"].get("file_heat", {}),
            }
            self.chat_list.controls.clear()
            self._add_chat_message("Memory reset. File heat decayed.", ft.Colors.GREEN_400)
            self._update_telemetry()
            self.page.update()
            return

        self.stop_flag = False
        self.active_task_id += 1
        task_id = self.active_task_id
        self.cancel_event = threading.Event()
        cancel_event = self.cancel_event
        self.is_processing = True
        self.input_field.disabled = True
        self.input_field.hint_text = "D.A.V.E. is thinking..."
        self.send_btn.disabled = True
        self.mode_switch.disabled = True
        self.llm_dropdown.disabled = True
        self.appearance_switch.disabled = True
        self.demo_switch.disabled = True
        self.status_text.value = "Status: Running"
        self.status_text.color = ft.Colors.GREEN_400
        self.page.update()

        threading.Thread(
            target=self._safe_process_message,
            args=(task_id, user_input, cancel_event),
            daemon=True,
        ).start()
        return

    def _safe_process_message(self, task_id, user_input, cancel_event):
        try:
            self.process_message(user_input, task_id, cancel_event)
        except Exception as e:
            self.llm_queue.put(("terminal", f"[SYSTEM CRASH] The agent thread encountered a fatal error: {str(e)}", "red"))
        finally:
            if task_id == self.active_task_id:
                self.llm_queue.put(("status", "Idle", "gray"))
                self._update_telemetry()
                self.llm_queue.put(("unlock", task_id, "white"))

    def _update_telemetry(self):
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
            with open(full_path, "r", encoding="utf-8") as f:
                self.edit_history.append({"file": filename, "before": f.read()})
                if len(self.edit_history) > 30:
                    self.edit_history.pop(0)

    def undo_last_edit(self):
        if not self.edit_history:
            self._show_toast("Undo stack empty.", "yellow")
            return
        entry = self.edit_history.pop()
        result = rewrite_file(entry["file"], entry["before"], self.target_directory, "Undo")
        self._add_terminal_msg(f"Undo: restored {entry['file']}.", ft.Colors.YELLOW_400)
        self._add_chat_message(f"Undo completed: restored {entry['file']}.", ft.Colors.YELLOW_400)
        self._refresh_workspace_async()

        # ── State persistence ──────────────────────────────────────────

    def _save_observation_memory(self):
        cache_dir = os.path.join(self.target_directory, ".dave_cache")
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, "observation_memory.json")
        try:
            with open(cache_path, "w") as f:
                json.dump(self.TaskState["system_state"].get("file_heat", {}), f)
        except Exception:
            pass

    def _load_observation_memory(self):
        cache_path = os.path.join(self.target_directory, ".dave_cache", "observation_memory.json")
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

        # ── Workspace refresh ──────────────────────────────────────────

    def _refresh_workspace_async(self, task_id=None):
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
                self.ast_skeleton = new_ast if isinstance(new_ast, str) else ""
                self.refresh_in_progress = False
                self._update_ast_display()
                self.page.update()
            except Exception as e:
                self.refresh_in_progress = False
                self.llm_queue.put(("terminal", f"[Refresh Error] {e}", "red"))

        threading.Thread(target=worker, daemon=True).start()

    def _update_ast_display(self):
        """Build custom TreeView of the project structure."""
        self.tree_container.controls.clear()
        valid_exts = (".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json", ".md")
        ignore = {"node_modules", ".git", ".next", "dist", "build", "__pycache__"}
        heat_dict = self.TaskState["system_state"].get("file_heat", {})
        expanded_folders = set()

        def get_heat_bar(fname):
            score = heat_dict.get(fname, 0)
            if score >= 10:
                return "[███]"
            elif score >= 5:
                return "[██░]"
            elif score >= 1:
                return "[█░░]"
            return "[░░░]"

        def build_folder_row(folder_name, folder_path, depth, is_exp):
            indent = "  " * depth
            icon = ft.Icons.FOLDER_OPEN if is_exp else ft.Icons.FOLDER
            return ft.Row(
                controls=[
                    ft.Container(width=depth * 16),
                    ft.IconButton(
                        icon=ft.Icons.ARROW_DROP_DOWN if is_exp else ft.Icons.ARROW_RIGHT,
                        icon_size=14,
                        on_click=lambda e, p=folder_path: _toggle_folder(p),
                    ),
                    ft.Icon(icon, size=16, color=ft.Colors.YELLOW_600),
                    ft.Text(f"{indent}{folder_name}", size=12, color=ft.Colors.GREY_300),
                ],
                spacing=2,
            )

        def build_file_row(filename, rel_path, depth):
            indent = "  " * depth
            bar = get_heat_bar(rel_path)
            return ft.Row(
                controls=[
                    ft.Container(width=depth * 16 + 30),
                    ft.Icon(ft.Icons.INSERT_DRIVE_FILE, size=14, color=ft.Colors.BLUE_300),
                    ft.Text(f"{bar} {filename}", size=11, color=ft.Colors.GREY_400, font_family="Consolas"),
                ],
                spacing=2,
            )

        def _toggle_folder(folder_path):
            if folder_path in expanded_folders:
                expanded_folders.discard(folder_path)
            else:
                expanded_folders.add(folder_path)
            self._update_ast_display()

        def add_folder_contents(dir_path, depth):
            try:
                entries = sorted(os.listdir(dir_path))
            except PermissionError:
                return
            folders = [e for e in entries if os.path.isdir(os.path.join(dir_path, e)) and e not in ignore]
            files = [e for e in entries if os.path.isfile(os.path.join(dir_path, e)) and e.endswith(valid_exts)]
            for folder in folders:
                fpath = os.path.join(dir_path, folder)
                rel_fpath = os.path.relpath(fpath, self.target_directory)
                is_exp = rel_fpath in expanded_folders
                self.tree_container.controls.append(build_folder_row(folder, rel_fpath, depth, is_exp))
                if is_exp:
                    add_folder_contents(fpath, depth + 1)
            for f in files:
                fpath = os.path.join(dir_path, f)
                rel_fpath = os.path.relpath(fpath, self.target_directory)
                self.tree_container.controls.append(build_file_row(f, rel_fpath, depth))

        # Root entry
        root_name = os.path.basename(self.target_directory) if self.target_directory else "workspace"
        self.tree_container.controls.append(
            ft.Row(
                controls=[
                    ft.Icon(ft.Icons.FOLDER_SPECIAL, size=16, color=ft.Colors.YELLOW_600),
                    ft.Text(f"📁 {root_name}", size=12, weight="bold", color=ft.Colors.GREY_200),
                ],
                spacing=4,
            )
        )
        if self.target_directory:
            add_folder_contents(self.target_directory, 1)

        # AST Skeleton metadata rendering engine sync
        if hasattr(self, "ast_skeleton") and self.ast_skeleton:
            self.ast_meta_text.value = str(self.ast_skeleton)
        else:
            self.ast_meta_text.value = "No AST map available."

                    # ── Helper methods (identical to gui.py) ───────────────────────

    def normalize_project_path(self, filename):
        if not filename:
            return None
        if os.path.isabs(filename):
            try:
                filename = os.path.relpath(filename, self.target_directory)
            except ValueError:
                pass
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
            for rel_path, meta in self.workspace_index.get("files", {}).items():
                normalized = self.normalize_project_path(rel_path)
                if not normalized:
                    continue
                if not preferred_exts or normalized.lower().endswith(preferred_exts):
                    files.append(normalized)
        except Exception:
            pass
        if not files:
            valid_exts = preferred_exts or (
                ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json", ".md", ".txt"
            )
            ignore = {"node_modules", ".git", ".next", "dist", "build", "__pycache__", ".dave_cache", "venv", ".venv", "env"}
            try:
                for root, dirs, filenames in os.walk(self.target_directory):
                    dirs[:] = [d for d in dirs if d not in ignore]
                    for fname in filenames:
                        if fname.lower().endswith(valid_exts):
                            rel_path = os.path.relpath(os.path.join(root, fname), self.target_directory)
                            files.append(self.normalize_project_path(rel_path))
            except Exception:
                pass
        return sorted(set(files))

    def _resolve_target_filename(self, action=None, user_input="", preferred_exts=None):
        action = action or {}
        filename = (
            action.get("filename") or action.get("filepath") or action.get("file_path")
            or action.get("path") or action.get("file") or action.get("target_file")
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
        candidates = self._get_workspace_file_candidates(preferred_exts=preferred_exts)
        if len(candidates) == 1:
            return candidates[0]
        py_candidates = self._get_workspace_file_candidates(preferred_exts=(".py",))
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
            action.get("filename") or action.get("filepath") or action.get("file_path")
            or action.get("path") or action.get("file") or action.get("target_file")
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

    def _append_with_reminder(self, history, role, content, reminder=None):
        history.append({"role": role, "content": content})
        if reminder:
            history.append({"role": "system", "content": f"<system-reminder>{reminder}</system-reminder>"})

    # ── Guided demo & chat shortcuts (identical to gui.py) ────────

    def _wants_guided_demo_recipe(self, user_input):
        text = user_input.lower()
        demo_words = [
            "make this site", "make the site", "make this landing page",
            "modernize", "make it premium", "look premium", "look professional",
            "make it beautiful", "improve the design", "polish the page",
            "apply demo recipe", "run demo recipe",
        ]
        return any(phrase in text for phrase in demo_words)

    def _try_guided_demo_recipe(self, user_input, active_history, task_target_directory):
        if not self.guided_demo_mode:
            return False
        if not self._wants_guided_demo_recipe(user_input):
            return False
        recipe_path = os.path.join(task_target_directory, "demo_recipe.json")
        if not os.path.exists(recipe_path):
            reply = "Guided Demo Mode is enabled, but I could not find demo_recipe.json in the selected workspace."
            self.llm_queue.put(("agent_turn", {"thought": "Guided Demo Mode was enabled, but no recipe file was found.", "tools": ["apply_demo_recipe"], "reply": reply}, "white"))
            self.llm_queue.put(("terminal", "Error: demo_recipe.json not found.", "red"))
            self._append_with_reminder(active_history, "user", user_input)
            self._append_with_reminder(active_history, "assistant", reply)
            self._refresh_workspace_async()
            return True
        self.llm_queue.put(("agent_turn", {"thought": "Guided Demo Mode matched the user request. Applying deterministic recipe.", "tools": ["apply_demo_recipe"], "reply": "I found a matching guided demo recipe. Applying it now."}, "white"))
        self.llm_queue.put(("terminal", "Applying guided demo recipe...", "yellow"))
        result = apply_demo_recipe(task_target_directory)
        if "STATUS: SUCCESS" in result:
            reply = "Done. I applied the guided demo recipe and updated the project files."
        else:
            reply = f"The guided demo recipe could not be completed:\n\n{result}"
            self.llm_queue.put(("terminal", result, "red"))

        self.llm_queue.put(("agent_turn", {"thought": "Guided demo recipe execution finished.", "tools": ["apply_demo_recipe"], "reply": reply}, "white"))
        self._append_with_reminder(active_history, "user", user_input)
        self._append_with_reminder(active_history, "assistant", reply)
        self._refresh_workspace_async()
        return True

    def _try_direct_chat_read(self, user_input, active_history, task_target_directory, task_llm_mode):
        filename = self._sniff_filename_from_text(user_input)
        if not filename:
            return False
        if not self._wants_direct_file_read(user_input):
            return False
        self.llm_queue.put(("terminal", f"Reading {filename}...", "yellow"))
        file_result = read_file_with_lines(filename, task_target_directory)
        if file_result.startswith("Error:"):
            reply = f"I tried to read `{filename}`, but got this error:\n\n{file_result}"
            self.llm_queue.put(("agent_turn", {"thought": "Direct chat read attempted, but the file reader returned an error.", "tools": ["read_file"], "reply": reply}, "white"))
            self._append_with_reminder(active_history, "user", user_input)
            self._append_with_reminder(active_history, "assistant", reply)
            self.llm_queue.put(("terminal", "Direct file read failed.", "red"))
            return True
        wants_explanation = self._wants_file_explanation(user_input)
        if not wants_explanation:
            reply = f"Here are the contents of `{filename}`:\n\n{file_result}"
            self.llm_queue.put(("agent_turn", {"thought": "Direct chat read shortcut used.", "tools": ["read_file"], "reply": reply}, "white"))
            self._append_with_reminder(active_history, "user", user_input)
            self._append_with_reminder(active_history, "assistant", f"Displayed contents of {filename}.")
            self.llm_queue.put(("terminal", "Direct file read complete.", "green"))
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
        reply = response.get("reply") if response.get("valid") and response.get("reply") else self._fallback_file_report(filename, file_result)
        self.llm_queue.put(("agent_turn", {"thought": "Direct file read plus explanation shortcut used.", "tools": ["read_file"], "reply": reply}, "white"))
        self._append_with_reminder(active_history, "user", user_input)
        self._append_with_reminder(active_history, "assistant", reply)
        self.llm_queue.put(("terminal", "Direct file read and explanation complete.", "green"))
        return True

    # ── Core agent loop (identical to gui.py) ──────────────────────

    def process_message(self, user_input, task_id=None, cancel_event=None):
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

        initial_target = self._resolve_target_filename(
            action={}, user_input=user_input,
            preferred_exts=(".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css"),
        )
        if initial_target:
            self.TaskState["system_state"]["current_target"] = initial_target

        while not self.stop_flag and not (cancel_event and cancel_event.is_set()):
            turn_count += 1
            if turn_count > max_turns:
                self.llm_queue.put(("terminal", f"[LOOP GUARD] Max turns reached ({max_turns}). Stopping task safely.", "red"))
                break
            self.llm_queue.put(("status", "Thinking...", "yellow"))
            self._update_telemetry()

            # ---- 1. Helmet Injection ----
            try:
                phase = self.TaskState["system_state"].get("current_phase", "Scout")
                helmet_prompt = f"\n=== ACTIVE HELMET PHASE: {phase.upper()} ===\n"
                if phase in ["Scout", "Chat"] and current_input == user_input:
                    if phase == "Chat":
                        context = f"Project: {os.path.basename(self.target_directory)}\nFiles: {list(self.workspace_index.get('files', {}).keys())}\nSkeleton:\n{self.ast_skeleton}"
                        helmet_prompt += f"\n[WORKSPACE CONTEXT]\n{context}\n"
                    try:
                        _, index_data = get_project_skeleton(self.target_directory, self.TaskState["system_state"].get("file_heat", {}))
                        auto_context = semantic_search(user_input, index_data, top_n=2)
                        if "No strong matches" not in auto_context and "Error" not in auto_context:
                            helmet_prompt += f"\n[AUTO-RETRIEVED CONTEXT based on your task]\n{auto_context}\n"
                    except Exception:
                        pass
                if phase == "Scout":
                    helmet_prompt += (
                        "MODE: EXPLORE. Use read_file, scan_directory, search_in_file, or semantic_search only. "
                        "You must identify the exact target filename before planning an edit. "
                        "When ready, use update_state to transition to PLAN.\n"
                    )
                elif phase == "Chat":
                    helmet_prompt += (
                        "MODE: CHAT. You are a conversational codebase assistant. "
                        "DO NOT use edit tools. Put your answer in the 'reply' field.\n"
                    )
                elif phase == "Plan":
                    helmet_prompt += (
                        "MODE: PLAN. Return JSON with actions array containing exactly one update_state action. "
                        "The update_state must include: analysis, options, decision, reason, confidence. "
                        "Do not output code in PLAN.\n"
                    )
                elif phase == "Execute":
                    helmet_prompt += (
                        "MODE: EXECUTE. You must perform the edit now. "
                        "If using an edit tool, you MUST include complete new_code. "
                        "Allowed edit tools: replace_lines, rewrite_file, replace_named_block, "
                        "insert_before_symbol, insert_after_symbol, create_file.\n"
                    )
                augmented_input = f"{helmet_prompt}\n[SYSTEM EVENT / USER INPUT]\n{current_input}"
            except Exception:
                augmented_input = current_input

            # ---- 2. Call LLM ----
            response = get_llm_response(
                augmented_input, active_history, task_target_directory, task_llm_mode,
                is_write_operation=(task_mode == "agent"), task_state=self.TaskState,
                chat_mode=(task_mode == "chat"),
            )
            if self.stop_flag or (cancel_event and cancel_event.is_set()):
                break

            # ---- 3. Handle Invalid format ----
            if not response.get("valid", False):
                self.TaskState["system_state"]["retry_count"] += 1
                err = response.get("error", "Parse error.")
                err_lower = err.lower()
                self.llm_queue.put(("terminal", f"Brain Error ({self.TaskState['system_state']['retry_count']}/2): {err}", "red"))
                fatal_write_format_error = (
                    "write action" in err_lower or "missing 'new_code'" in err_lower
                    or "missing new_code" in err_lower or "rewrite nuke ban" in err_lower
                    or "proposed implementation" in err_lower
                )
                if fatal_write_format_error:
                    reply = "I stopped because the model attempted a write action without valid replacement code. No file was changed."
                    self.llm_queue.put(("agent_turn", {"thought": "Write validation failed.", "tools": ["write_validation"], "reply": reply}, "white"))
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
            planned_tools = [a.get("tool") for a in actions if a.get("tool") and a.get("tool") != "none"]
            self.llm_queue.put(("agent_turn", {"thought": thought, "tools": planned_tools, "reply": agent_reply}, "white"))

            # ---- 4. Check for task completion ----
            if not actions or any(a.get("tool") in ("none", "task_complete") for a in actions):
                if task_mode == "agent" and self.TaskState["system_state"].get("flag_tests", False):
                    if not (last_used_tool == "run_command" and last_run_success):
                        self._append_with_reminder(active_history, "assistant", "SYSTEM: Task cannot be completed. You have not executed 'run_command' to verify.")
                        current_input = "SYSTEM: Task cannot be completed. You have not executed 'run_command' to verify your code works."
                        continue
                self._append_with_reminder(active_history, "user", current_input)
                self._append_with_reminder(active_history, "assistant", agent_reply, RECURRING_REMINDER if task_mode == "agent" else None)
                self.llm_queue.put(("terminal", "Task/Chat Complete.", "green"))
                if task_id == self.active_task_id:
                    self.llm_queue.put(("unlock", task_id, "white"))
                if task_mode == "agent":
                    self.decay_file_heat(refresh=False)
                    self._refresh_workspace_async(task_id)
                break

            # ---- Pre-read all files ----
            read_results = {}
            fatal_missing_filename = False
            for a in actions:
                if a.get("tool") == "read_file":
                    filename = self._resolve_target_filename(
                        action=a, user_input=user_input,
                        preferred_exts=(".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json", ".md", ".txt"),
                    )
                    if filename:
                        a["filename"] = filename
                        self.TaskState["system_state"]["current_target"] = filename
                    if not filename:
                        fatal_missing_filename = True
                        self.llm_queue.put(("terminal", "[FATAL] read_file requested without filename.", "red"))
                        break
                    self.llm_queue.put(("terminal", f"Reading {filename}...", "yellow"))
                    read_results[filename] = read_file_with_lines(
                        filename, task_target_directory, a.get("start_line"), a.get("end_line"),
                    )
            if fatal_missing_filename:
                self.llm_queue.put(("agent_turn", {"thought": "Read failed: No filename.", "tools": ["read_file"], "reply": "I couldn't determine which file to read."}, "white"))
                break

            # ---- 5. Execute Tools ----
            force_agent_break = False
            guardrail_triggered = False

            def _increase_heat(fname, amount):
                if fname:
                    if os.path.isabs(fname):
                        try:
                            fname = os.path.relpath(fname, self.target_directory)
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
                if task_mode == "chat" and tool_req not in ["read_file", "scan_directory", "search_in_file", "semantic_search", "none", "task_complete"]:
                    action_result = f"[ERR-READ-ONLY] You are in Chat Mode. Edit tool '{tool_req}' is blocked."
                    self.llm_queue.put(("warning", action_result, "red"))
                    self._append_with_reminder(active_history, "assistant", f"{agent_reply}\n[TOOL_RESULT]: {action_result}")
                    guardrail_triggered = True
                    break

                # Phase Guard
                try:
                    if self.TaskState["system_state"]["current_phase"] == "Plan" and tool_req not in ["update_state", "none"]:
                        action_result = "[ERR-PHASE-VIOLATION] You are in PLAN phase. You MUST use 'update_state'."
                        self.llm_queue.put(("warning", action_result, "red"))
                        self._append_with_reminder(active_history, "assistant", f"{agent_reply}\n[TOOL_RESULT]: {action_result}")
                        guardrail_triggered = True
                        break
                except KeyError:
                    pass

                # Idempotency Guard
                code_hash = hashlib.md5(new_code.encode()).hexdigest()[:8] if new_code else "none"
                action_signature = f"{tool_req}_{filename}_{a.get('func_name','')}_{command}_{code_hash}"
                if action_signature == self.TaskState["system_state"].get("last_failing_signature") and tool_req not in ["read_file", "scan_directory"]:
                    action_result = "[ERR-IDEMPOTENCY] Blocked. You just tried this EXACT action and it failed."
                    self.llm_queue.put(("warning", action_result, "red"))
                    self.TaskState["system_state"]["confidence"] = 0.0
                    self.TaskState["system_state"]["current_phase"] = "Chat" if self.mode == "chat" else "Scout"
                    self._append_with_reminder(active_history, "assistant", f"{agent_reply}\n[TOOL_RESULT]: {action_result}")
                    guardrail_triggered = True
                    break

                action_tracker.append(action_signature)
                if len(action_tracker) > 5:
                    action_tracker.pop(0)
                if len(action_tracker) == 5 and len(set(action_tracker)) == 1:
                    self.llm_queue.put(("terminal", "BEHAVIORAL LOOP DETECTED. Pausing agent.", "red"))
                    action_tracker = []
                    force_agent_break = True
                    break

                # ---- Execute Action ----
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
                            action_result = read_file_with_lines(filename, task_target_directory, a.get("start_line"), a.get("end_line"))
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
                        action_result = "Error: Missing arguments."

                elif tool_req == "semantic_search":
                    query = a.get("query")
                    if query:
                        self.llm_queue.put(("terminal", f"Semantic search: '{query}'", "yellow"))
                        try:
                            _, index_data = get_project_skeleton(self.target_directory, self.TaskState["system_state"].get("file_heat", {}))
                            search_res = semantic_search(query, index_data)
                            action_result = f"Semantic search results:\n{search_res['context_string']}"
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
                            self.TaskState["system_state"]["pinned_snippets"].append({
                                "filename": filename, "description": a.get("description", ""), "content": content,
                            })
                            action_result = "Pinned to Working Memory."
                            self.llm_queue.put(("terminal", f"Pinned memory from {filename}", "green"))
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
                        action_result = rename_file(old_fn, new_fn, self.target_directory)
                        self.llm_queue.put(("terminal", f"Renamed {old_fn} to {new_fn}", "yellow"))
                    else:
                        action_result = "Error: Missing arguments for rename_file."

                elif tool_req == "delete_file":
                    if filename:
                        action_result = delete_file(filename, self.target_directory)
                        self.llm_queue.put(("terminal", f"Deleted {filename}", "red"))
                        self._update_ast_display()
                    else:
                        action_result = "Error: Missing filename for delete_file."

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
                    _increase_heat(filename, 5)
                    if tool_req == "create_file":
                        action_result = create_file(filename, new_code, self.target_directory)
                    elif tool_req == "replace_lines":
                        action_result = replace_lines(filename, a.get("start_line"), a.get("end_line"), new_code, self.target_directory, edit_intent)
                    elif tool_req == "rewrite_file":
                        action_result = rewrite_file(filename, new_code, self.target_directory, edit_intent)
                    elif tool_req == "replace_named_block":
                        action_result = replace_named_block(filename, a.get("symbol_name"), new_code, self.target_directory, edit_intent)
                    elif tool_req == "insert_before_symbol":
                        action_result = insert_before_symbol(filename, a.get("symbol_name"), new_code, self.target_directory, edit_intent)
                    elif tool_req == "insert_after_symbol":
                        action_result = insert_after_symbol(filename, a.get("symbol_name"), new_code, self.target_directory, edit_intent)
                    if isinstance(action_result, str) and (
                        action_result.startswith("Successfully") or "successfully" in action_result.lower()
                        or "applied safe edit" in action_result.lower()
                    ):
                        edit_applied = True
                        self.llm_queue.put(("terminal", f"Edited {filename} via {tool_req}", "green"))
                        self._refresh_workspace_async(task_id)
                    else:
                        self.llm_queue.put(("terminal", f"Edit failed: {str(action_result)[:120]}", "red"))

                elif tool_req == "update_state":
                    self.TaskState["llm_notes"] = {
                        "analysis": a.get("analysis", ""), "options": a.get("options", []),
                        "decision": a.get("decision", ""), "reason": a.get("reason", ""),
                        "confidence": a.get("confidence", 0.0),
                    }
                    plan_str = f"DECISION: {a.get('decision')}\nCONFIDENCE: {a.get('confidence')}\n\nREASONING:\n{a.get('analysis')}\n{a.get('reason')}"
                    self.llm_queue.put(("update_plan", plan_str, "white"))
                    action_result = "State updated successfully."

                elif tool_req == "manage_plan":
                    act = a.get("action")
                    data = a.get("data", "")
                    if act:
                        self.llm_queue.put(("terminal", f"Accessing Master Plan ({act})", "yellow"))
                        try:
                            action_result = manage_plan(act, data, task_target_directory)
                        except Exception as e:
                            action_result = f"Error managing plan: {e}"
                    else:
                        action_result = "Error: Missing 'action' argument."

                elif tool_req == "run_command":
                    if task_mode == "agent" and not edit_applied:
                        action_result = "[ERR-RUN-BEFORE-EDIT] The agent tried to run a command before any edit was applied."
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
                        action_result = "[ERR-NO-COMMAND-FATAL] run_command was requested without a command."
                        force_agent_break = True

                else:
                    action_result = f"Error: Unknown tool {tool_req}"

                # Error handling
                if isinstance(action_result, str) and (
                    action_result.startswith("Error:") or action_result.startswith("[ERR-")
                    or action_result.startswith("CRITICAL:") or "STATUS: FAILURE" in action_result
                ):
                    self.TaskState["system_state"]["last_failing_signature"] = action_signature
                    self.TaskState["system_state"]["confidence"] = max(0.0, self.TaskState["system_state"].get("confidence", 1.0) - 0.5)
                    self.llm_queue.put(("terminal", f"Failed: {str(action_result)[:100]}...", "red"))
                else:
                    if tool_req not in ["read_file", "scan_directory", "update_state", "none"]:
                        self.TaskState["system_state"]["last_failing_signature"] = None
                        self.TaskState["system_state"]["confidence"] = 1.0

                self._append_with_reminder(active_history, "user", current_input)
                self._append_with_reminder(active_history, "assistant", f'{agent_reply}\n[TOOL_RESULT]: {action_result}', RECURRING_REMINDER if self.mode == "agent" else None)
                last_used_tool = tool_req
                if tool_req != "run_command":
                    last_run_success = False

            if force_agent_break:
                break

            # ---- 6. Routing ----
            current_phase = self.TaskState["system_state"]["current_phase"]
            sys_confidence = self.TaskState["system_state"].get("confidence", 1.0)
            next_input = ""
            tools_used = [a.get("tool") for a in actions]

            if guardrail_triggered:
                self.llm_queue.put(("terminal", "🛑 SYSTEM GUARDRAIL TRIGGERED: Rerouting agent...", "red"))
                next_input = "System Guardrail Triggered. Read the warning and correct your action."
            elif sys_confidence < 0.6 and current_phase not in ["Scout", "Chat"] and self.TaskState["system_state"].get("flag_confidence", True):
                self.TaskState["system_state"]["current_phase"] = "Scout"
                self.TaskState["system_state"]["confidence"] = 1.0
                next_input = "[SYSTEM WARNING] Confidence critically low. Forced to SCOUT phase."
            elif current_phase == "Chat":
                next_input = f"Deliver your final answer in the 'reply' field and leave 'actions' empty: []."
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
                    next_input = "Tool execution finished. If done, use 'task_complete'."

            if any(a.get("tool") == "manage_plan" and a.get("action") in ["create", "read"] for a in actions):
                next_input += " (SYSTEM COMMAND: Plan accessed. DO NOT use 'manage_plan' again on your next turn.)"

            current_input = next_input
            if len(active_history) > 10:
                if self.mode == "agent":
                    self.chat_history = self.chat_history[-10:]
                else:
                    self.ask_history = self.ask_history[-10:]

    # ── Workspace index builder ────────────────────────────────────

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


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

def main(page: ft.Page):
    DAVEApp(page)


if __name__ == "__main__":
    ft.run(main)