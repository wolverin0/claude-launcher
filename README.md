# Claude Code Session Launcher

A lightweight desktop GUI to quickly resume [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions after a restart, crash, or context switch.

![Python](https://img.shields.io/badge/python-3.8+-blue) ![Platform](https://img.shields.io/badge/platform-Windows-lightgrey) ![License](https://img.shields.io/badge/license-MIT-green)

## The Problem

When working with Claude Code across multiple projects, a computer restart means you lose track of which sessions you had open and in which directories. You're left manually browsing `~/.claude/projects/`, mentally mapping encoded folder names back to real paths, and reconstructing the right `claude --continue` commands for each one.

## The Solution

A single-file Python/tkinter app that:

1. **Scans** `~/.claude/projects/` for all Claude Code project folders
2. **Reads** the actual working directory from session JSONL files (no guessing from encoded folder names)
3. **Shows** each project with its real path, last active timestamp, and session count
4. **Launches** Claude Code in a new terminal window with one click

### Features

- **Bulk launch**: checkbox each project, then "Launch Selected" to reopen everything at once
- **Select All / Select None**: quick bulk selection for the post-restart workflow
- **Session preview**: shows the last user message so you know what you were working on
- **Pin / favorites**: pin projects to always show at top (persisted across restarts)
- **Session health**: **OK** = clean exit, **!!** = interrupted/crashed — know which sessions need attention
- **Auto-start with Windows**: optional checkbox to launch automatically on boot
- **`--continue`** mode: resume the most recent session (default)
- **`-r` mode**: pick a specific session from a dropdown (sorted by recency)
- **`--dangerously-skip-permissions`** toggle (on by default)
- **Path validation**: purple border = path exists, red = missing (disabled), gold = pinned
- **Windows Terminal** support with cmd.exe fallback
- Automatically clears the `CLAUDECODE` env var to avoid nested-session errors

## Screenshot

```
┌──────────────────────────────────────────────────────────────────────┐
│  Claude Code Session Launcher                             [Refresh]  │
├──────────────────────────────────────────────────────────────────────┤
│  [x] --dangerously-skip-permissions                                  │
│  (o) --continue    ( ) -r (pick)    [ ] Auto-start with Windows      │
├──────────────────────────────────────────────────────────────────────┤
│  [Select All] [Select None]  *=pinned OK=clean !!=int  [Launch (3)]  │
├──────────────────────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ [x] * my-saas-app  OK                  [a1b2c3.. ▼] [Launch] │  │
│  │       C:\Projects\my-saas-app                                 │  │
│  │       "fix the payment webhook for sandbox mode..."           │  │
│  │       Last: 2026-02-24 15:39  |  12 session(s)        [pin]  │  │
│  ├────────────────────────────────────────────────────────────────┤  │
│  │ [x]   backend-api  !!                  [d4e5f6.. ▼] [Launch] │  │
│  │       D:\Work\backend-api                                     │  │
│  │       "add rate limiting to the /auth endpoints..."           │  │
│  │       Last: 2026-02-24 14:20  |  8 session(s)         [pin]  │  │
│  ├────────────────────────────────────────────────────────────────┤  │
│  │ [x]   landing-page  OK                [f7g8h9.. ▼] [Launch]  │  │
│  │       C:\Projects\landing-page                                │  │
│  │       "make the hero section responsive at 375px..."          │  │
│  │       Last: 2026-02-24 12:05  |  3 session(s)         [pin]  │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

## Installation

### Requirements

- Python 3.8+ (tkinter included with standard Python on Windows)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI installed and on PATH

### Setup

```bash
git clone https://github.com/wolverin0/claude-launcher.git
cd claude-launcher
```

**Option A: Double-click**
- Run `claude-launcher.pyw` directly (Python must be associated with `.pyw` files)

**Option B: Desktop shortcut**
- Create a `.bat` file on your desktop:
```bat
@echo off
start "" pythonw "C:\path\to\claude-launcher.pyw"
```

**Option C: From terminal**
```bash
python claude-launcher.pyw
```

No dependencies beyond the Python standard library.

## How It Works

1. Lists all subdirectories in `~/.claude/projects/`
2. For each project, reads the most recent `.jsonl` session file and extracts the `cwd` field — this is the **real filesystem path** Claude Code was running in
3. Displays projects sorted by last activity
4. On "Launch", opens a new Windows Terminal (or cmd) in the project directory and runs:
   ```
   set CLAUDECODE= && claude --continue --dangerously-skip-permissions
   ```

### Why read JSONL instead of decoding folder names?

Claude Code encodes project paths by replacing every non-alphanumeric character with `-`. This is a lossy encoding — `Py Apps` (space), `_OneDrive` (underscore), and `my-project` (hyphen) all produce the same `-` character. Rather than attempting to reverse this ambiguous encoding, we read the original path directly from session data.

## Platform Support

Currently **Windows only** (Windows Terminal + cmd.exe launchers). PRs welcome for macOS/Linux support — the main change needed is in `launch_session()` to use the appropriate terminal emulator.

## License

MIT
