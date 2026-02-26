# Changelog

## [2.0.0] - 2026-02-26

### Added

**Tabbed Interface**
- Reorganized UI from single-screen to 7-tab notebook with custom dark theme

**Sessions Tab**
- Cross-project session browser with search, filtering, and sorting
- Per-session cost tracking (USD) parsed from session JSONL data
- Total cost dashboard across all sessions
- Session export to Markdown format
- Conversation preview pane with syntax-colored roles
- Files touched display per session

**Hooks Tab**
- Visual editor for Claude Code hooks in `~/.claude/settings.json`
- Browse hooks by event type (SessionStart, PreToolUse, PostToolUse, etc.)
- Enable/disable individual hooks without deletion
- Add new hooks with event, matcher, command, and timeout
- Delete hooks with one click
- Save changes back to settings.json

**MCP Servers Tab**
- MCP server manager for `~/.claude.json`
- Server list with enabled/disabled status indicators
- Detail editor for type, command, args, URL, and env
- Add/remove server configurations
- Quick Add templates for GitHub MCP, Playwright, Filesystem
- Save changes to .claude.json

**Plugins & Skills Tab**
- Browse installed plugins from `~/.claude/plugins/`
- Browse skills with YAML frontmatter parsing
- Browse agents with model information
- Detail view showing full content of any item
- Stats bar with counts

**Profiles Tab**
- Global default model selection (sonnet, opus, haiku, sonnet[1m], opus[1m])
- Default permission mode (plan, default, bypassPermissions)
- Agent Teams experimental toggle
- Quick profile buttons (Development, Review, Quick)
- Per-project model, permission mode, and flag overrides

**CLAUDE.md Editor Tab**
- Edit CLAUDE.md files for any project or global scope
- Scope selector dropdown with all detected projects
- Full text editor with undo support
- Create new CLAUDE.md files on save

**Launch Enhancements**
- Remote control mode toggle (uses `claude remote-control` subcommand)
- Model flag passed to `claude --model` from profile settings
- Agent Teams environment variable set when enabled
- Extended context models (sonnet[1m], opus[1m]) support

**Session Cost Tracking**
- `get_session_cost()` function parses cost/token data from JSONL
- Cost displayed per-session in Sessions tab
- Total cost summary in stats bar

**Session Export**
- `export_session_markdown()` converts sessions to clean Markdown
- Export button in Sessions tab with file save dialog

### Changed
- Existing project list UI moved into "Projects" tab
- All launch call sites now pass model and agent_teams from config
- Session list columns adjusted to fit cost column

## [1.0.0] - 2026-02-25

### Initial Release
- Single-screen project launcher
- Bulk launch with staggered timing
- Search, filter, and sort projects
- Session preview popups
- Pin/favorites and hide/archive
- Session health detection
- Auto-start with Windows
- Command palette (Ctrl+P)
- Deep session search (Ctrl+G)
- System tray integration (Windows)
- Background file watcher for auto-refresh
- Cross-platform support (Windows, macOS, Linux)
