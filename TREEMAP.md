```bash
Direct_Agentic_Versioning_Engine (DAVE)/
├── gui.py                          # Main GUI application (customtkinter)
├── main.py                         # CLI entrypoint
├── run.bat                         # Windows launcher script
│
├── core/
│   ├── brain.py                    # LLM routing, system prompts, response parsing
│   ├── config.py                   # Configuration management
│   └── memory.py                   # Knowledge base loading, caching
│
├── knowledge_base/
│   ├── global_rules.md             # Agent behavior constraints
│   ├── project_context.txt         # Project metadata
│   └── languages/
│       ├── css.md                  # CSS language knowledge
│       ├── html.md                 # HTML language knowledge
│       ├── javascript.md           # JavaScript language knowledge
│       └── python.md               # Python language knowledge
│
├── tools/
│   ├── code_editor.py              # Safe file edits (libcst, regex fallback)
│   ├── file_creator.py             # Create new files
│   ├── file_manager.py             # Rename, delete files
│   ├── file_reader.py              # Read files with line ranges
│   ├── mapper.py                   # Codebase mapping, AST skeleton, semantic search
│   ├── planner.py                  # Task planning utilities
│   ├── scanner.py                  # Directory scanning
│   ├── search_engine.py            # Text search in files
│   └── terminal_runner.py          # Execute commands
│
├── .env                            # Environment config (DO NOT COMMIT)
├── .gitignore                      # Git ignore rules
└──  requirements.txt                # Python dependencies
