# NEXUS IDE — Installation & Setup Guide

> **NEXUS IDE** is an AI-powered coding assistant that runs entirely on your own PC.
> It can write code, fix bugs, build full projects, and explain every step along the way —
> using your choice of AI provider (free or paid, local or cloud).

---

## What you get

- A full coding IDE that runs in your web browser (at `http://localhost:5000`)
- Support for 6 AI providers: NVIDIA (free), OpenAI, OpenRouter, Groq, Ollama (local), or any custom server
- 6 work modes: Auto, Builder, Debugger, Refactorer, Researcher, Reviewer
- Full file explorer, code editor, terminal, and Git panel
- Your files stay on YOUR machine — nothing is uploaded anywhere

---

## Choose your installation method

| Method | Best for | Python needed? |
|--------|----------|----------------|
| **A — Windows .exe** | Windows users who want the easiest setup | No |
| **B — Windows with Python** | Windows users who already have Python | Yes |
| **C — Mac or Linux** | Mac and Linux users | Yes |

---

## METHOD A — Windows .exe (Easiest — No Python Required)

This creates a single `.exe` file you can run on any Windows PC without installing anything.

> **One-time setup:** You need Python once to BUILD the exe. After that, the exe works on any machine.

### Step 1 — Download and install Python (for building only)

1. Go to **https://python.org/downloads**
2. Click the big yellow **"Download Python 3.x.x"** button
3. Run the installer
4. **IMPORTANT:** On the first screen, tick the box that says **"Add Python to PATH"**
5. Click **Install Now**
6. When it finishes, click **Close**

### Step 2 — Download NEXUS IDE

Download this project as a ZIP file and extract it to a folder like `C:\NEXUS_IDE\`

### Step 3 — Build the .exe

1. Open the `NEXUS_IDE` folder
2. Double-click **`build_exe.bat`**
3. A black window will open — this is normal. It is installing dependencies and building the exe
4. Wait for it to finish (takes 1–3 minutes). You will see:

```
════════════════════════════════════════════════════════
  BUILD COMPLETE
  Executable: dist\NEXUS_IDE.exe
════════════════════════════════════════════════════════
```

5. Press any key to close the window

### Step 4 — Run NEXUS IDE

1. Open the `dist` folder inside your NEXUS_IDE folder
2. Double-click **`NEXUS_IDE.exe`**
3. A black terminal window opens (keep it open — this is the server)
4. Your browser opens automatically to **http://localhost:5000**
5. NEXUS IDE is running!

> **To stop:** Close the black terminal window, or press `Ctrl+C` inside it.

> **To share with someone else:** Copy the entire `dist\` folder to their PC.
> They just double-click `NEXUS_IDE.exe` — no Python needed on their machine.

---

## METHOD B — Windows with Python

Use this if you already have Python installed.

### Step 1 — Check your Python version

Open Command Prompt (press `Win+R`, type `cmd`, press Enter) and run:

```
python --version
```

You need **Python 3.11 or newer**. If you see an older version or an error,
download Python from **https://python.org/downloads** and make sure to tick
**"Add Python to PATH"** during installation.

### Step 2 — Download NEXUS IDE

Download this project as a ZIP file and extract it to a folder like `C:\NEXUS_IDE\`

### Step 3 — Run the start script

1. Open the `NEXUS_IDE` folder
2. Double-click **`start.bat`**
3. The first time, it will install all dependencies automatically (takes 1–2 minutes)
4. When you see `Starting on http://localhost:5000`, open your browser and go to:

```
http://localhost:5000
```

> **To stop:** Close the command prompt window, or press `Ctrl+C` inside it.

> **Next time:** Just double-click `start.bat` again. It starts in a few seconds.

---

## METHOD C — Mac or Linux

### Step 1 — Check your Python version

Open Terminal and run:

```bash
python3 --version
```

You need **Python 3.11 or newer**.

- **Mac:** Install from https://python.org/downloads or run `brew install python`
- **Linux (Ubuntu/Debian):** `sudo apt install python3.11 python3.11-venv`
- **Linux (Fedora/RHEL):** `sudo dnf install python3.11`

### Step 2 — Download NEXUS IDE

```bash
# Option 1: If you have git installed
git clone https://github.com/your-repo/nexus-ide.git
cd nexus-ide

# Option 2: Download the ZIP and extract it, then open Terminal in that folder
```

### Step 3 — Make the start script executable (first time only)

```bash
chmod +x start.sh
```

### Step 4 — Run NEXUS IDE

```bash
./start.sh
```

The first time, it installs all dependencies automatically (takes 1–2 minutes).
When it's ready, open your browser and go to:

```
http://localhost:5000
```

> **To stop:** Press `Ctrl+C` in the terminal.

> **Next time:** Just run `./start.sh` again.

---

## Setting up your AI provider (required)

NEXUS IDE needs an AI provider to power the agent. Here are your options:

### Option 1 — NVIDIA (Free, recommended for beginners)

NVIDIA gives you free access to powerful AI models including Llama 4, DeepSeek, and Mistral.

1. Go to **https://build.nvidia.com**
2. Click **"Get API Key"** in the top right
3. Sign up for a free account (no credit card needed)
4. Copy your API key — it starts with `nvapi-`
5. In NEXUS IDE, click **Settings** (gear icon, top right)
6. Find the **NVIDIA** card
7. Paste your key into the API Key field
8. Click **Save & Test**
9. The dot next to NVIDIA should turn green — you're connected!

### Option 2 — Ollama (100% free, runs on your PC, no internet needed)

Ollama runs AI models entirely on your own computer. No API key, no internet, no cost.

**Requirements:** A PC with at least 8 GB of RAM (16 GB recommended for larger models)

1. Go to **https://ollama.com** and download Ollama for your operating system
2. Install and run Ollama
3. Open a terminal and download a model:
   ```
   ollama pull llama3.2
   ```
   (This downloads ~2 GB — do this once)
4. In NEXUS IDE, click **Settings** → find the **Ollama** card → click **Connect**
5. It will automatically detect your running models
6. Select a model and click **Set as default**

### Option 3 — Groq (Free, very fast)

Groq offers blazing-fast AI inference for free.

1. Go to **https://console.groq.com** and sign up (free)
2. Go to **API Keys** → Create a new key
3. Copy the key (starts with `gsk_`)
4. In NEXUS IDE: Settings → Groq card → paste key → Save & Test

### Option 4 — OpenRouter (Access to 200+ models)

OpenRouter gives you access to Claude, Gemini, GPT-4, DeepSeek and more.

1. Go to **https://openrouter.ai** and sign up
2. Go to **Keys** → Create a key (you get free credits to start)
3. Copy the key (starts with `sk-or-`)
4. In NEXUS IDE: Settings → OpenRouter card → paste key → Save & Test

### Option 5 — OpenAI

1. Go to **https://platform.openai.com** and sign up
2. Go to **API Keys** → Create new secret key
3. Copy the key (starts with `sk-`)
4. In NEXUS IDE: Settings → OpenAI card → paste key → Save & Test

---

## Using NEXUS IDE — Quick Start

Once you have an AI provider connected, here's how to get started:

### Start a task

1. Type what you want in the chat box at the bottom
2. Press **Enter** or click **Send**
3. The agent will create a plan, then execute it step by step
4. Watch the steps appear in real time with status indicators

### Example tasks to try

```
Build me a Python web scraper that gets product prices from Amazon
```

```
Create a simple todo app with HTML, CSS and JavaScript
```

```
Fix the bug in my code — here is the error: [paste your error]
```

```
Explain how this code works: [paste your code]
```

### Choose a mode

Click the mode selector above the chat box to choose how the agent approaches your task:

| Mode | Best for |
|------|----------|
| **Auto** | Let NEXUS decide the best approach |
| **Builder** | Building new features or projects from scratch |
| **Debugger** | Fixing errors and bugs |
| **Refactorer** | Cleaning up and improving existing code |
| **Researcher** | Answering questions and explaining concepts |
| **Reviewer** | Reviewing code for quality and security |

### Your files

- All files the agent creates are saved in the **`agent_workspace/`** folder
- Use the **File Explorer** panel on the left to browse and open files
- Use the built-in **Code Editor** to view or edit files manually

---

## Troubleshooting

### "The browser doesn't open" or "http://localhost:5000 doesn't load"

- Make sure the terminal/command prompt is still open (closing it stops the server)
- Try opening your browser manually and going to `http://localhost:5000`
- Check that no other program is using port 5000. If so, close it

### "The AI isn't responding" or "Connection error"

- Go to **Settings** and check that your API key is saved (the dot should be green)
- Make sure you have an internet connection (except for Ollama, which is local)
- Try clicking **Save & Test** again in the Settings panel

### "pip is not recognized" (Windows)

- You need to reinstall Python and make sure to tick **"Add Python to PATH"**
- After reinstalling, close and reopen Command Prompt

### "Permission denied" (Mac/Linux)

Run this command in your terminal:
```bash
chmod +x start.sh
```

### "The .exe crashes immediately" (Windows)

- Make sure you have the full `dist\` folder, not just the .exe file
- Try running it from Command Prompt to see the error message:
  ```
  cd path\to\dist
  NEXUS_IDE.exe
  ```

### "Ollama models aren't showing up"

- Make sure Ollama is running: open a terminal and type `ollama serve`
- Make sure you've pulled at least one model: `ollama pull llama3.2`
- In NEXUS IDE Settings → Ollama → click **Connect** to refresh the model list

---

## Frequently Asked Questions

**Is this free to use?**
NEXUS IDE itself is free. Some AI providers charge for API usage (OpenAI, OpenRouter with credits used up). NVIDIA, Groq, and Ollama are free.

**Are my files and code sent anywhere?**
Your files stay on your machine. Only the specific text you send in the chat (your instructions and relevant code) is sent to the AI provider you chose.

**Can I use it without internet?**
Yes — use Ollama, which runs AI models entirely on your own PC.

**Can I use multiple AI providers?**
Yes — you can set up all of them. Switch between them anytime in Settings → choose a different default model.

**Where are my files saved?**
Everything the agent creates is saved in the `agent_workspace/` folder inside the NEXUS IDE directory.

**How do I update NEXUS IDE?**
Download the new version and run `start.bat` (or `start.sh`). It will update dependencies automatically.

---

## System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| OS | Windows 10, macOS 11, Ubuntu 20.04 | Windows 11, macOS 14, Ubuntu 22.04 |
| RAM | 4 GB | 8 GB+ |
| Disk space | 2 GB free | 5 GB+ free |
| Python | 3.11+ (not needed for .exe) | 3.12+ |
| Internet | Required (except Ollama) | Broadband |
| Browser | Chrome, Firefox, Edge, Safari | Chrome or Edge |

**For Ollama (local AI):**
| Model size | RAM needed |
|-----------|------------|
| 3B models (llama3.2) | 8 GB |
| 7B models (mistral) | 16 GB |
| 13B models | 32 GB |

---

## File Reference

| File | What it does |
|------|-------------|
| `start.bat` | Run NEXUS IDE on Windows (with Python) |
| `start.sh` | Run NEXUS IDE on Mac or Linux |
| `build_exe.bat` | Build a standalone .exe for Windows |
| `build_exe.sh` | Build a standalone binary for Mac/Linux |
| `requirements.txt` | List of Python packages needed |
| `agent_workspace/` | Folder where the agent saves all your files |

---

*NEXUS IDE — Build anything, with no limits.*
