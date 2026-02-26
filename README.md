# Claude Code Launcher v2

A comprehensive desktop companion for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — manage sessions, hooks, MCP servers, plugins, profiles, and CLAUDE.md files from a single GUI.

![Python](https://img.shields.io/badge/python-3.8+-blue) ![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey) ![License](https://img.shields.io/badge/license-MIT-green)

## What Is This?

A single-file Python/Tkinter app that started as a session launcher and evolved into a full control hub for Claude Code. Seven tabs give you visibility and control over every aspect of your Claude Code environment.

## Tabs

### Projects

The original session launcher — browse all your Claude Code projects, launch sessions with one click.

- **Bulk launch**: select multiple projects and launch them all (staggered for 3+)
- **Search & filter**: instant search by name or path (Ctrl+F)
- **Sort**: by recent activity, name, or session count
- **Session preview**: terminal-style popup showing conversation history
- **Pin / favorites**: pin projects to the top
- **Hide / archive**: right-click to hide stale projects
- **Health badges**: clean exit vs interrupted/crashed sessions
- **Launch modes**: `--continue`, `-r` (pick session), remote control
- **Per-project flags**: custom CLI arguments per project
- **Session notes**: annotate sessions for later reference
- **Double-click to launch**, right-click context menu, keyboard navigation

### Sessions

Cross-project session browser with cost tracking.

- **All sessions in one view**: browse every session across all projects
- **Search**: filter by project name, session ID, or conversation content
- **Cost tracking**: per-session cost in USD, total cost across all sessions
- **Token estimates**: rough token counts from JSONL file sizes
- **Health status**: clean/interrupted at a glance
- **Conversation preview**: select a session to see recent messages
- **Export to Markdown**: export any session as a clean `.md` file

### Hooks

Visual editor for Claude Code hooks (`~/.claude/settings.json`).

- **Browse by event**: SessionStart, PreToolUse, PostToolUse, UserPromptSubmit, Stop, PreCompact
- **View hook details**: matcher patterns, commands, timeouts
- **Enable/disable**: toggle individual hooks without deleting them
- **Add/remove**: create new hooks or delete existing ones
- **Save**: writes back to `settings.json`

### MCP Servers

Manage MCP server configurations (`~/.claude.json`).

- **Server list**: all configured MCP servers with status indicators
- **Detail view**: edit type, command, args, URL, env for each server
- **Add/remove**: create or delete server configurations
- **Quick Add**: one-click templates for GitHub MCP, Playwright, Filesystem
- **Save**: writes back to `.claude.json`

### Plugins & Skills

Browse installed plugins, skills, and agents.

- **Plugins**: installed plugins from `~/.claude/plugins/`
- **Skills**: skill directories with YAML frontmatter parsing
- **Agents**: agent markdown files with model info
- **Detail view**: click any item to see its full content
- **Stats**: counts of each type

### Profiles

Configure default launch settings and per-project overrides.

- **Global settings**: default model, permission mode, agent teams toggle
- **Model selection**: sonnet, opus, haiku, sonnet[1m], opus[1m] (extended context)
- **Quick profiles**: Development (sonnet + bypass), Review (opus + plan), Quick (haiku + bypass)
- **Per-project overrides**: set model, permission mode, and custom flags per project
- **Agent Teams**: enable experimental agent teams feature

### CLAUDE.md

Edit CLAUDE.md instruction files for any project.

- **Scope selector**: global (`~/CLAUDE.md`) or per-project
- **Full editor**: syntax-highlighted text editor with undo support
- **Create new**: creates CLAUDE.md if it doesn't exist
- **Save**: writes directly to the selected file

## Installation

### Requirements

- Python 3.8+ (tkinter included with standard Python)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI installed and on PATH

### Setup

```bash
git clone https://github.com/wolverin0/claude-launcher.git
cd claude-launcher
```

**Option A: Double-click** — run `claude-launcher.pyw` directly

**Option B: Desktop shortcut**
```bat
@echo off
start "" pythonw "C:\path\to\claude-launcher.pyw"
```

**Option C: From terminal**
```bash
python claude-launcher.pyw
```

No dependencies beyond the Python standard library.

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+F` | Focus search bar |
| `Ctrl+A` | Select all projects |
| `Ctrl+R` | Refresh project list |
| `Ctrl+P` | Command palette (fuzzy project launcher) |
| `Ctrl+G` | Deep session search (search across all session content) |
| `Ctrl+E` | Export config |
| `Enter` | Launch selected projects |
| `Escape` | Clear search / deselect all |
| `Arrow keys` | Navigate project cards |
| `Space` | Toggle selection on focused card |
| `Double-click` | Launch a single project |
| `Right-click` | Context menu |

## How It Works

1. Scans `~/.claude/projects/` for all Claude Code project folders
2. Reads the actual working directory from session JSONL files (no guessing from encoded folder names)
3. Parses session data for previews, health status, cost metrics, and file tracking
4. Launches Claude Code in a new terminal window with configured flags

### Data Sources

| Data | Source |
|------|--------|
| Projects & Sessions | `~/.claude/projects/*.jsonl` |
| Hooks | `~/.claude/settings.json` |
| MCP Servers | `~/.claude.json` |
| Plugins | `~/.claude/plugins/installed_plugins.json` |
| Skills | `~/.claude/skills/*/SKILL.md` |
| Agents | `~/.claude/agents/*.md` |
| CLAUDE.md | `~/CLAUDE.md` or `<project>/CLAUDE.md` |

## Platform Support

| Platform | Terminal | Status |
|----------|----------|--------|
| Windows | Windows Terminal / cmd.exe | Fully tested |
| macOS | Terminal.app | Supported |
| Linux | gnome-terminal, konsole, xfce4-terminal, xterm | Supported |

## License

MIT
