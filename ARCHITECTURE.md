# Running the Workspace

## 🖥️ Running the GUI Front-End

Launch the interactive window environment directly from the project root directory:

```bash
python gui.py
```

### GUI Core Characteristics

- **Target Prompt:** Upon launching, the interface explicitly prompts you to select a target local workspace folder to inspect or modify.
- **Agent Mode:** Fully autonomous execution loop. D.A.V.E. runs scouts, registers structural plans, applies file changes (create, replace, overwrite), and verifies stability.
- **Chat Mode:** A safe, completely read-only conversational assistant mode. Useful for querying code structure, finding patterns, or discussing architecture without modifying code files.
- **Proxy Lifecycle Integration:** Switching the LLM Mode picker to `freellmapi` prompts the Python backend to automatically spin up your local Node background server silently on `localhost:3001`.

---

## 📟 Running the CLI Entry Point

For a purely terminal-driven developer experience, launch the command-line engine:

```bash
# Run the Python script directly
python main.py

# Or utilize the Windows helper shortcut
run.bat
```

### CLI Core Characteristics

- Provides a clean, text-based alternative to the GUI application.
- Utilizes the exact same `.env` values, state machine routing logic, and guardrails.
- Presents interactive terminal prompts tracking each phase change (`Scout ➔ Plan ➔ Execute`).

---

## 🔄 Typical Agentic Workflows

### GUI Workflow Example

1. Run `python gui.py` and select your sandbox or development folder when prompted.
2. Toggle the upper switch to **Agent Mode**.
3. In the input entry bar, assign a clear task:

   > Add a validation check to the login function inside `auth.py`

4. Watch the **Agent Comm Stream** expand. D.A.V.E. will:
   - Read `auth.py`
   - Enter the Plan state and log its strategy
   - Update the right sidebar telemetry panel
   - Execute the syntax-safe edit
   - Verify completion and finish the cycle

### CLI Workflow Example

1. Execute:

```bash
python main.py
```

Or:

```bash
run.bat
```

2. Select your targeted engine (**Local**, **Cloud**, or **Proxy**) via the interactive numbered menu.
3. Input your design task.

The terminal will log clear `[brain thinking]` streams and structured execution steps sequentially until it reports:

```text
Task Complete
```

or

```text
Chat Complete
```

---

## 📂 Core Architecture Map

| File | Purpose |
|--------|----------|
| `gui.py` | Primary CustomTkinter 3-pane observability dashboard application |
| `main.py` | Bimodal terminal-based loop application entry point |
| `run.bat` | One-click Windows startup batch script launcher |
| `core/brain.py` | High-speed parsing engine, brace counters, validation profiles, and system prompts |
| `core/memory.py` | Context budget management, deduplication layers, and on-demand language rule routers |
| `tools/` | Code modification, repo mapping, and filesystem tool logic |
| `.env` | Critical application secrets and local configuration parameters (**NEVER commit to source control**) |

---

## 🔒 Security & Git Best Practices

### Keep Secrets Secret

Ensure your `.gitignore` includes:

```gitignore
.env
.dave_cache/
__pycache__/
```

Double-check before pushing modifications to your repository.

### Review Agentic Edits

Always inspect:

- Tool execution output
- Generated diffs
- Modified files

before adding changes to a Git staging area.

### Isolate Agent Environments

It is highly recommended to:

- Run D.A.V.E. inside isolated testing sandboxes
- Use dedicated feature branches
- Run local test suites before merging agent-generated changes into production branches

### Common Git Workflow

```bash
# Check modified files
git status

# Review changes
git diff

# Stage changes
git add .

# Commit changes
git commit -m "Describe your changes"

# Push to remote
git push origin main
```

---

## 🔍 Troubleshooting Guide

### GUI Crash / CustomTkinter Import Failures

Ensure your virtual environment is activated and reinstall required GUI packages:

```bash
pip install customtkinter Pillow
```

### Missing `libcst` Warnings

If D.A.V.E. reports that `libcst` is unavailable:

```bash
pip install libcst
```

This enables strict syntax-tree structural edits instead of falling back to regex-based replacements.

### Local Connection Refused Errors

If D.A.V.E. gets stuck waiting for responses:

- Verify your local Ollama server is running.
- Confirm API keys are correctly defined in `.env`.
- Check proxy services are listening on expected ports.

### Permission Errors on Windows

If background execution commands or proxy connections are blocked:

- Run your terminal as **Administrator**
- Review Windows Firewall settings
- Verify antivirus software is not blocking local services

---

## 🔄 1. The Bimodal Unified State Machine
Traditional agents fail because they allow models to simultaneously choose strategy and execute edits, leading to cascading context hallucination. D.A.V.E. splits cognitive workloads into decoupled deterministic phases:

```text
       [ User Prompt ]
              │
              ▼
    ┌───────────────────┐
    │    SCOUT PHASE    │ ◄─── (Read-only discovery tools)
    └─────────┬─────────┘
              │ ───► If edits planned, force update_state
              ▼
    ┌───────────────────┐
    │    PLAN PHASE     │ ◄─── (Logs strategy, sets tool goals)
    └─────────┬─────────┘
              │ ───► Transition locked
              ▼
    ┌───────────────────┐
    │   EXECUTE PHASE   │ ◄─── (Applies modifications & runs verification tests)
    └───────────────────┘
