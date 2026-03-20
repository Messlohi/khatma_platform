# 🚀 Developer's Guide: Staff-Level AI Workflow

Welcome to this Agent-Ready repository! We use **Google Antigravity** and **Kilo Code** to autonomously build, refactor, and document our codebase. 

Instead of treating the AI as a simple autocomplete tool, we treat it as an autonomous "Staff Engineer." The AI relies on a strict `memory-bank/` directory to manage its own state, plan tasks, verify code, and learn from its mistakes.

---

## 🛠️ Phase 1: Installation (One-Time Setup)

If you are reading this, the setup script has already been run! The `.agent/` and `memory-bank/` folders exist, and the strict behavioral rules are active.

---

## 🧠 Phase 2: The Bootstrap (Giving the AI a Brain)

If the files inside `memory-bank/` are currently empty, you must force the AI to audit the existing codebase and write its own documentation.

1. Open this repository in your IDE (Google Antigravity or VS Code with Kilo Code).
2. Open the AI Chat panel. Ensure the AI's context is focused **only** on this specific repository.
3. **Copy and paste this exact prompt:**
   > *"Please read and execute `memory-bank/BOOTSTRAP.md`"*
4. Wait 1–2 minutes. You will watch the AI read your files and populate `techContext.md`, `map.md`, and `activeContext.md`. 
5. When finished, the AI will automatically delete the `BOOTSTRAP.md` file. Your repo is now fully Agent-Ready.

---

## 🏗️ Phase 3: How the AI Thinks (The Agentic Loop)

Whenever you give the AI a task, it is strictly programmed to follow this lifecycle. Do not interrupt it while it is reading or planning.

1. **Context Gathering:** Reads `lessons.md`, `activeContext.md`, and `map.md`.
2. **Planning Phase:** Writes a checklist to `memory-bank/todo.md`.
3. **Execution & Verification:** Writes code, runs terminal tests/linters, diffs changes against main, and marks items `[x]` in `todo.md`.
4. **Handoff & Learning:** Overwrites `activeContext.md`, appends to `progress.md`, and updates `lessons.md` if needed.

---

## 💻 Phase 4: Daily Development Workflow

### 1. Starting a New Task
Just give the AI the objective. 
**Prompt Example:**
> *"Build the password reset UI component. Connect it to the existing auth service."*

### 2. Autonomous Bug Fixing
Throw the error at the AI and tell it to fix it. It will read `map.md` to find the relevant files.
**Prompt Example:**
> *"The CI pipeline is failing on the new user profile route. Here is the error log: [paste log]. Find the root cause and fix it."*

---

## 🔄 Phase 5: The "Staff Engineer" Feedback Loop

The most powerful feature of this workspace is `memory-bank/lessons.md`. If the AI makes a mistake, **do not just tell it to fix the code.** Tell it to learn from it.

**How to correct the AI:**
> *"You imported the module incorrectly. Fix the code, and then update `memory-bank/lessons.md` with a strict rule about how we import modules in this project."*

Tomorrow, the AI will read `lessons.md` first and format it perfectly on the first try.
