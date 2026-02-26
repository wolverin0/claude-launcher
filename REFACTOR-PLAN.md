# Refactoring Plan: Multi-File Module Split

## Current State
- Single file: `claude-launcher.pyw` (~3050 lines)
- Pure Python/tkinter, no external dependencies
- Deployed by copying single file to home directory

## Proposed Structure

```
claude-launcher/
├── claude-launcher.pyw          # Entry point (~30 lines) - imports and runs
├── launcher/
│   ├── __init__.py              # Package init
│   ├── constants.py             # Color palette, paths, platform detection
│   ├── config.py                # load_config(), save_config()
│   ├── session.py               # Session data: get_sessions, get_projects,
│   │                            #   get_session_preview, get_session_health,
│   │                            #   get_session_cost, export_session_markdown,
│   │                            #   get_session_files, get_real_path
│   ├── launch.py                # launch_session(), toggle_autostart()
│   ├── app.py                   # SessionLauncher class: __init__, _build_ui,
│   │                            #   _setup_styles, window management, tray,
│   │                            #   file watcher, status bar
│   ├── tabs/
│   │   ├── __init__.py
│   │   ├── projects.py          # Projects tab: card rendering, bulk launch,
│   │   │                        #   search, sort, pin, hide, project cards
│   │   ├── sessions.py          # Sessions tab: cross-project browser, cost,
│   │   │                        #   export, preview
│   │   ├── hooks.py             # Hooks tab: visual editor for settings.json
│   │   ├── mcp.py               # MCP Servers tab: server manager
│   │   ├── plugins.py           # Plugins & Skills tab: browser
│   │   ├── profiles.py          # Profiles tab: model, perms, agent teams
│   │   └── claudemd.py          # CLAUDE.md editor tab
│   └── dialogs.py               # Command palette, session search, custom flags,
│                                #   notes, hidden projects, preview popup
```

## Migration Steps

1. Create `launcher/` package directory
2. Extract constants (colors, paths, platform flags) to `constants.py`
3. Extract config functions to `config.py`
4. Extract session data functions to `session.py`
5. Extract launch functions to `launch.py`
6. Move each tab builder method to its own file in `tabs/`
   - Each tab module exports a `build(notebook, app)` function
   - Tabs access app state through the `app` parameter
7. Move dialog methods to `dialogs.py`
8. Keep `app.py` as the main class, importing from all modules
9. Update `claude-launcher.pyw` entry point to:
   ```python
   from launcher.app import SessionLauncher
   if __name__ == "__main__":
       app = SessionLauncher()
       app.mainloop()
   ```

## Approximate Line Counts

| Module | Lines |
|--------|-------|
| constants.py | ~30 |
| config.py | ~30 |
| session.py | ~200 |
| launch.py | ~60 |
| app.py | ~400 |
| tabs/projects.py | ~600 |
| tabs/sessions.py | ~150 |
| tabs/hooks.py | ~180 |
| tabs/mcp.py | ~200 |
| tabs/plugins.py | ~150 |
| tabs/profiles.py | ~120 |
| tabs/claudemd.py | ~80 |
| dialogs.py | ~400 |

## Trade-offs

**Pros:**
- Each file is <400 lines (readable, maintainable)
- Can test modules independently
- Easier to add new tabs
- Git diffs are cleaner (changes isolated to one file)

**Cons:**
- No longer a single-file app (can't just copy one .pyw)
- Need to run from repo directory or install as package
- Slight import overhead on startup

## Deployment Options After Split

1. **Run from repo**: `python claude-launcher.pyw` (current, works if CWD is repo)
2. **Pip install**: Add `setup.py`/`pyproject.toml`, install with `pip install -e .`
3. **PyInstaller**: Bundle into single `.exe` for Windows
4. **Zipapp**: `python -m zipapp launcher -o claude-launcher.pyz` (single file again!)

Option 4 (zipapp) gives the best of both worlds — modular source code
but single-file deployment.
