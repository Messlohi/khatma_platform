#!/bin/bash

echo "🚀 Scaffolding Staff Engineer Agent Architecture & Documentation..."

# 1. Create Core Directories
mkdir -p .agent/rules
mkdir -p .agent/skills/memory-bank-sync
mkdir -p memory-bank

# 2. Global Architecture & Staff Rules (Fully Loaded from Image)
cat << 'EOF' > .agent/rules/best_practices.md
# Repository Architecture & Staff Engineer Principles

## Core Principles
* **Simplicity First**: Make every change as simple as possible. Impact minimal code.
* **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
* **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.

## Workflow Orchestration
### 1. Plan Node Default
* Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions).
* If something goes sideways, STOP and re-plan immediately - don't keep pushing.
* Use plan mode for verification steps, not just building.
* Write detailed specs upfront to reduce ambiguity.

### 2. Subagent Strategy
* Use subagents liberally to keep main context window clean.
* Offload research, exploration, and parallel analysis to subagents.
* For complex problems, throw more compute at it via subagents.
* One task per subagent for focused execution.

### 3. Self-Improvement Loop
* After ANY correction from the user: update `memory-bank/lessons.md` with the pattern.
* Write rules for yourself that prevent the same mistake.
* Ruthlessly iterate on these lessons until mistake rate drops.
* Review lessons at session start for relevant project.

### 4. Verification Before Done
* Never mark a task complete without proving it works.
* Diff behavior between main and your changes when relevant.
* Ask yourself: "Would a staff engineer approve this?"
* Run tests, check logs, demonstrate correctness.

### 5. Demand Elegance (Balanced)
* For non-trivial changes: pause and ask "is there a more elegant way?"
* If a fix feels hacky: "Knowing everything I know now, implement the elegant solution."
* Skip this for simple, obvious fixes - don't over-engineer.
* Challenge your own work before presenting it.

### 6. Autonomous Bug Fixing
* When given a bug report: just fix it. Don't ask for hand-holding.
* Point at logs, errors, failing tests - then resolve them.
* Zero context switching required from the user.
* Go fix failing CI tests without being told how.

## Task Management & Memory Bank Rules
1. **Plan First**: Write plan to `memory-bank/todo.md` with checkable items.
2. **Verify Plan**: Check in before starting implementation.
3. **Track Progress**: Mark items complete as you go.
4. **Explain Changes**: High-level summary at each step.
5. **Document Results**: Add review section to `memory-bank/todo.md`.
6. **Capture Lessons**: Update `memory-bank/lessons.md` after corrections.
7. **State Sync**: Always keep `memory-bank/activeContext.md` updated with exact codebase state.
EOF

# 3. Antigravity Skill
cat << 'EOF' > .agent/skills/memory-bank-sync/SKILL.md
---
name: memory-bank-sync
description: Syncs project state. Use BEFORE coding to read the memory bank, and AFTER coding to update the handoff state.
---

# Memory Bank Sync Skill

1. **Read:** Digest `activeContext.md`, `techContext.md`, `map.md`, and `lessons.md`.
2. **Plan:** Write execution steps to `todo.md` (Plan First).
3. **Write:** Overwrite `activeContext.md` with the current state of this repository.
4. **Log:** Append completed high-level work to `progress.md`. If >50 lines, summarize to `archive.md`.
EOF

# 4. Kilo Code Rules (.kilocoderules)
cat << 'EOF' > .kilocoderules
# Kilo Code Rules (Staff Engineer Mode)

1. **Follow Antigravity Rules:** Adhere strictly to `.agent/rules/best_practices.md`.
2. **Repository Isolation:** Only use the `memory-bank/` located in THIS repository.
3. **Lazy Load:** ALWAYS read `activeContext.md`, `techContext.md`, `map.md`, and `lessons.md` before coding.
4. **Task Management:** Plan in `todo.md`, track progress, and verify before completion.
5. **Handoff:** When finished, update `activeContext.md` and `progress.md`.
EOF

# 5. Initialize Empty Memory Bank Files
touch memory-bank/projectBrief.md
touch memory-bank/activeContext.md
touch memory-bank/progress.md
touch memory-bank/archive.md
touch memory-bank/map.md
touch memory-bank/techContext.md
touch memory-bank/todo.md
touch memory-bank/lessons.md

# 6. GENERATE THE BOOTSTRAP PAYLOAD
cat << 'EOF' > memory-bank/BOOTSTRAP.md
# ⚠️ INITIALIZATION PAYLOAD

**Instructions for the AI Agent:**
Act as a Staff Engineer auditing this existing codebase to build our state management memory bank. Scan the codebase (package.json, architecture, directories) and populate these files:

1. **`memory-bank/projectBrief.md`:** A concise summary of what this specific repository does.
2. **`memory-bank/techContext.md`:** The core tech stack used here (languages, frameworks, libraries).
3. **`memory-bank/map.md`:** A high-level index mapping out where core features live (e.g., 'Controllers are in src/api/').
4. **`memory-bank/progress.md`:** A summary of features already built.
5. **`memory-bank/activeContext.md`:** The current state of the code. Note any unfinished work.
6. **`memory-bank/lessons.md`:** Seed this with any obvious architectural quirks or strict rules you noticed while auditing.
7. **`memory-bank/todo.md`:** Leave blank.
8. **`memory-bank/archive.md`:** Leave blank.

Write directly to the files. Do not output in the chat. When finished, delete this `BOOTSTRAP.md` file.
EOF

# 7. GENERATE THE HUMAN DEVELOPER DOCUMENTATION
cat << 'EOF' > GETTING_STARTED_AI.md
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
EOF

echo "✅ All files, memory banks, rules, and documentation generated successfully!"
echo "👉 Check out the new GETTING_STARTED_AI.md file in your repository root."