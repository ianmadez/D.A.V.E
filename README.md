<img width="50%" alt="D A V E" src="https://github.com/user-attachments/assets/a77c5c12-878e-4be4-99fe-e7e4947bd3a8" />

# D.A.V.E.
### **Direct Agentic Versioning Engine**

D.A.V.E. is an autonomous, local-first AI coding agent engineered to run seamlessly on consumer hardware. Unlike traditional chat interfaces or erratic autonomous loops, D.A.V.E. treats code generation as a highly observable engineering pipeline managed by a strict, bimodal state machine.

---
<img width="50%" alt="gui" src="https://github.com/user-attachments/assets/9677ff95-f40b-48b4-9f36-23774ae6e7c9" />
<img width="50%" alt="cli" src="https://github.com/user-attachments/assets/5419318f-6af6-4dac-b108-79fde71e19cd" />

## Core Features

* **Bimodal Unified State Machine:** Operates across independent `Scout`, `Plan`, `Execute`, and `Chat` phases to enforce structured decision-making.
* **UI Observability X-Ray:** A 4-panel diagnostic suite featuring a live AST skeleton explorer, a unified tool execution stream, context viewers, and a collapsible UI turn accordion to track the agent's exact thought process.
* **Bulletproof JSON Parser:** Uses a custom stack-based brace counter to perfectly isolate valid JSON payloads, neutralizing the conversational garbage and double-JSON blocks common in smaller models.
* **Context Budgeting & Heat Decay:** Tracks file interaction metrics ("heat bars") and automatically decays scores on task completion or conversation reset, keeping your context windows lean and pristine.
* **On-Demand Memory Router:** Dynamically ranks, caps, and injects dense, structural language rule schemas matching the active file type, preventing token bloat.

---

## 🛠️ Quick Start & Usage

Direct Agentic Versioning Engine (D.A.V.E.) setup guide, system requirements, and environment configuration for GUI and CLI environments.

### 1. Prerequisites
- **Python:** Version 3.10 or higher recommended.
- **Git:** Installed and configured for version control.
- **Environment:** PowerShell/Command Prompt (Windows) or Terminal (macOS/Linux).
- **Optional:** Node.js & npm (Required only if utilizing the built-in local `freellmapi` aggregation proxy).

### 2. Installation & Dependency Setup
Clone your repository directly from GitHub and configure the local virtual environment:

```bash
# Clone the repository
git clone [https://github.com/ianmadez/D.A.V.E.git](https://github.com/ianmadez/D.A.V.E.git)
cd D.A.V.E

# Initialize and activate the virtual environment
python -m venv .venv

# Windows (Command Prompt/PowerShell)
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# Install required core framework dependencies
pip install requests python-dotenv openai customtkinter libcst Pillow
```
---
# **Environment Config (.env)**
## **Create a file named .env in the project root directory. Populate it using your target endpoints and key strings:**

```bash
OLLAMA_URL=http://localhost:11434
MODEL=qwen2.5-coder:7b
FREELLMAPI_KEY=your_free_llm_api_key_here
OPENROUTER_API_KEY=your_openrouter_key_here
OPENAI_API_KEY=your_openai_key_if_used
```
### Adjust these values depending on whether you are executing prompts via local Ollama inference, the local FreeLLMAPI proxy server, OpenRouter fallback loops, or direct cloud APIs.
