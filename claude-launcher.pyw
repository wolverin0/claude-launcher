#!/usr/bin/env python3
"""Claude Code Session Launcher - Quick resume sessions after restart."""

import os
import sys
import subprocess
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from datetime import datetime, timedelta
import threading
import time
import platform
import shutil
import ctypes

PROJECTS_DIR = Path.home() / ".claude" / "projects"
CONFIG_FILE = Path.home() / ".claude" / "launcher-config.json"
STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

_path_cache = {}


# ── Config ──

def load_config() -> dict:
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"pinned": [], "auto_start": False, "hidden": [], "sort": "recent",
                "compact": False, "notes": {}, "custom_flags": {},
                "geometry": "", "launch_history": {}, "last_seen": {}}


def save_config(config: dict):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except OSError:
        pass


# ── Helpers ──

def _relative_time(dt):
    if dt is None:
        return ""
    diff = (datetime.now() - dt).total_seconds()
    if diff < 60:
        return "just now"
    elif diff < 3600:
        return f"{int(diff / 60)}m ago"
    elif diff < 86400:
        return f"{int(diff / 3600)}h ago"
    elif diff < 604800:
        return f"{int(diff / 86400)}d ago"
    else:
        return dt.strftime("%b %d")


def _project_emoji(name):
    n = name.lower()
    for keywords, emoji in [
        (['bot', 'whatsapp', 'chat'], '\U0001f916'),
        (['hospital', 'health', 'medical', 'clinic'], '\U0001f3e5'),
        (['gym', 'fitness', 'fit', 'gimnasio'], '\U0001f4aa'),
        (['shop', 'store', 'ecommerce', 'tienda', 'sales'], '\U0001f6d2'),
        (['dashboard', 'admin', 'panel', 'monitor'], '\U0001f4ca'),
        (['api', 'server', 'backend'], '\u26a1'),
        (['mobile', 'expo', 'native'], '\U0001f4f1'),
        (['game', 'play', 'snake', 'memory'], '\U0001f3ae'),
        (['photo', 'image', 'picture', 'camera'], '\U0001f4f8'),
        (['video', 'stream'], '\U0001f3ac'),
        (['doc', 'wiki', 'blog', 'write', 'note'], '\U0001f4dd'),
        (['test', 'spec'], '\U0001f9ea'),
        (['money', 'pay', 'finance', 'bank', 'mutual', 'contab'], '\U0001f4b0'),
        (['food', 'restaurant', 'cafe', 'kitchen', 'braserito', 'resto'], '\U0001f37d'),
        (['school', 'edu', 'learn'], '\U0001f393'),
        (['home', 'house', 'smart', 'domo'], '\U0001f3e0'),
        (['observer', 'watch', 'session'], '\U0001f441'),
        (['launcher', 'claude', 'tool'], '\U0001f680'),
        (['haig', 'sport', 'club', 'futbol'], '\u26bd'),
    ]:
        if any(kw in n for kw in keywords):
            return emoji
    return '\U0001f4c1'


def _session_size_label(sessions):
    total = sum(s.get('size', 0) for s in sessions)
    if total < 500_000:
        return "tiny"
    elif total < 5_000_000:
        return "med"
    else:
        return "large"


def _token_estimate(size_bytes):
    """Rough token estimate from JSONL size (~6 bytes per token with JSON overhead)."""
    tokens = size_bytes // 6
    if tokens < 1000:
        return f"{tokens}t"
    elif tokens < 1_000_000:
        return f"{tokens // 1000}k"
    else:
        return f"{tokens / 1_000_000:.1f}M"


def _duration_str(seconds):
    """Format seconds to human-readable duration."""
    if seconds < 60:
        return "<1m"
    elif seconds < 3600:
        return f"{int(seconds / 60)}m"
    elif seconds < 86400:
        h = int(seconds / 3600)
        m = int((seconds % 3600) / 60)
        return f"{h}h{m}m" if m else f"{h}h"
    else:
        return f"{int(seconds / 86400)}d"


def get_real_path(project_dir: Path, encoded_name: str) -> str:
    if encoded_name in _path_cache:
        return _path_cache[encoded_name]
    jsonl_files = sorted(project_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    for jf in jsonl_files[:3]:
        try:
            with open(jf, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if 'cwd' in data:
                            _path_cache[encoded_name] = data['cwd']
                            return data['cwd']
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            continue
    _path_cache[encoded_name] = encoded_name
    return encoded_name


def get_session_preview(session_file: Path) -> list:
    try:
        fsize = session_file.stat().st_size
        with open(session_file, 'rb') as f:
            f.seek(max(0, fsize - 120_000))
            data = f.read().decode('utf-8', errors='ignore')
        turns = []
        for line in data.strip().split('\n'):
            try:
                d = json.loads(line)
                dtype = d.get('type', '')
                if dtype in ('progress', 'file-history-snapshot', 'queue-operation'):
                    continue
                msg = d.get('message', {})
                role = msg.get('role', '')
                content = msg.get('content', '')
                text = ""
                tools_used = []
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get('type') == 'text':
                            t = block.get('text', '').strip()
                            if t:
                                text = t
                        elif block.get('type') == 'tool_use':
                            tools_used.append(block.get('name', ''))
                elif isinstance(content, str):
                    text = content.strip()
                if text and text.startswith(('<system', '<teammate', '<local-command', '<command-name', '<hook')):
                    continue
                if role == 'user' and text and len(text) > 3:
                    turns.append({'role': 'user', 'text': text})
                elif role == 'assistant':
                    if text:
                        turns.append({'role': 'assistant', 'text': text})
                    if tools_used:
                        turns.append({'role': 'tool', 'text': ', '.join(_friendly_tool(t) for t in tools_used)})
            except (json.JSONDecodeError, KeyError):
                continue
        merged = []
        for t in turns:
            if merged and merged[-1]['role'] == 'tool' and t['role'] == 'tool':
                merged[-1]['text'] += ', ' + t['text']
            else:
                merged.append(t)
        result = []
        for t in reversed(merged):
            if len(result) >= 6:
                break
            entry = {**t, 'text': t['text'].replace('\n', ' ').strip()[:90]}
            if len(entry['text']) >= 90:
                entry['text'] += '...'
            result.append(entry)
        result.reverse()
        return result
    except OSError:
        return []


def _friendly_tool(name: str) -> str:
    return {'Read': 'Read', 'Write': 'Write', 'Edit': 'Edit', 'Bash': 'Terminal',
            'Glob': 'Search', 'Grep': 'Search', 'Task': 'Agent',
            'WebFetch': 'Web', 'WebSearch': 'Web'}.get(name, name)


def get_session_cost(session_file: Path) -> dict:
    """Extract cost/token metrics from session JSONL."""
    result = {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}
    try:
        with open(session_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get('type') == 'summary':
                        result["cost_usd"] = d.get('costUSD', d.get('cost_usd', 0)) or 0
                        result["input_tokens"] = d.get('inputTokens', d.get('input_tokens', 0)) or 0
                        result["output_tokens"] = d.get('outputTokens', d.get('output_tokens', 0)) or 0
                    elif d.get('costUSD') or d.get('cost_usd'):
                        result["cost_usd"] = d.get('costUSD', d.get('cost_usd', 0)) or 0
                except (json.JSONDecodeError, KeyError):
                    continue
    except OSError:
        pass
    return result


def export_session_markdown(session_file: Path) -> str:
    """Export a session as clean markdown text."""
    lines = []
    try:
        with open(session_file, 'r', encoding='utf-8', errors='ignore') as f:
            for raw in f:
                try:
                    d = json.loads(raw)
                    msg = d.get('message', {})
                    role = msg.get('role', '')
                    content = msg.get('content', '')
                    text = ""
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get('type') == 'text':
                                t = block.get('text', '').strip()
                                if t:
                                    text = t
                    elif isinstance(content, str):
                        text = content.strip()
                    if not text or text.startswith(('<system', '<teammate', '<local-command', '<command-name', '<hook')):
                        continue
                    if role == 'user' and len(text) > 3:
                        lines.append(f"\n## User\n\n{text}\n")
                    elif role == 'assistant' and text:
                        lines.append(f"\n## Assistant\n\n{text}\n")
                except (json.JSONDecodeError, KeyError):
                    continue
    except OSError:
        pass
    return "\n".join(lines)


def get_session_health(session_file: Path) -> str:
    try:
        fsize = session_file.stat().st_size
        with open(session_file, 'rb') as f:
            f.seek(max(0, fsize - 10_000))
            data = f.read().decode('utf-8', errors='ignore')
        last_type = ""
        for line in data.strip().split('\n'):
            try:
                last_type = json.loads(line).get('type', '')
            except (json.JSONDecodeError, KeyError):
                continue
        if last_type in ('system', 'queue-operation'):
            return 'clean'
        elif last_type in ('assistant', 'progress'):
            return 'interrupted'
        return 'unknown'
    except OSError:
        return 'unknown'


def get_session_files(session_file: Path) -> list:
    """Extract files modified during a session from tool calls."""
    try:
        fsize = session_file.stat().st_size
        with open(session_file, 'rb') as f:
            f.seek(max(0, fsize - 200_000))
            data = f.read().decode('utf-8', errors='ignore')
        files = set()
        for line in data.strip().split('\n'):
            try:
                d = json.loads(line)
                msg = d.get('message', {})
                for block in (msg.get('content', []) if isinstance(msg.get('content'), list) else []):
                    if isinstance(block, dict) and block.get('type') == 'tool_use':
                        name = block.get('name', '')
                        inp = block.get('input', {})
                        if name in ('Write', 'Edit', 'Read') and 'file_path' in inp:
                            files.add(inp['file_path'])
                        elif name == 'NotebookEdit' and 'notebook_path' in inp:
                            files.add(inp['notebook_path'])
            except (json.JSONDecodeError, KeyError):
                continue
        return sorted(files)
    except OSError:
        return []


def get_sessions(project_dir: Path) -> list:
    sessions = []
    for f in project_dir.glob("*.jsonl"):
        try:
            stat = f.stat()
            duration = max(0, stat.st_mtime - stat.st_ctime)
            sessions.append({
                'id': f.stem, 'file': f,
                'modified': datetime.fromtimestamp(stat.st_mtime),
                'created': datetime.fromtimestamp(stat.st_ctime),
                'size': stat.st_size,
                'duration': duration,
                'preview': get_session_preview(f),
                'health': get_session_health(f),
            })
        except OSError:
            continue
    sessions.sort(key=lambda s: s['modified'], reverse=True)
    return sessions


def get_projects() -> list:
    if not PROJECTS_DIR.exists():
        return []
    config = load_config()
    pinned = set(config.get("pinned", []))
    projects = []
    for d in PROJECTS_DIR.iterdir():
        if not d.is_dir() or d.name == 'memory':
            continue
        decoded_path = get_real_path(d, d.name)
        sessions = get_sessions(d)
        projects.append({
            'encoded_name': d.name, 'decoded_path': decoded_path, 'dir': d,
            'sessions': sessions,
            'last_active': sessions[0]['modified'] if sessions else None,
            'pinned': d.name in pinned,
            'preview': sessions[0]['preview'] if sessions else [],
            'health': sessions[0]['health'] if sessions else 'unknown',
        })
    projects.sort(key=lambda p: (0 if p['pinned'] else 1,
                                  -(p['last_active'].timestamp() if p['last_active'] else 0)))
    return projects


def launch_session(decoded_path: str, skip_permissions: bool, mode: str,
                   session_id: str = None, extra_flags: str = "",
                   remote_control: bool = False, model: str = "",
                   agent_teams: bool = False):
    args = ["claude"]
    if remote_control:
        args.append("remote-control")
    elif mode == "continue":
        args.append("--continue")
    elif mode == "resume" and session_id:
        args.extend(["-r", session_id])
    if skip_permissions:
        args.append("--dangerously-skip-permissions")
    if model:
        args.extend(["--model", model])
    if extra_flags:
        args.extend(extra_flags.split())
    cmd_str = " ".join(args)
    env_prefix = "set CLAUDECODE="
    if agent_teams:
        env_prefix += " && set CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1"
    quoted_path = decoded_path.replace('"', '')
    if IS_WINDOWS:
        launch_cmd = f"{env_prefix} && {cmd_str}"
        try:
            subprocess.Popen(["wt", "-d", quoted_path, "cmd", "/k", launch_cmd])
        except FileNotFoundError:
            subprocess.Popen(f'start cmd /k "cd /d "{quoted_path}" && {launch_cmd}"', shell=True)
    elif IS_MAC:
        teams_env = "export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 && " if agent_teams else ""
        script = (f'tell application "Terminal"\n'
                  f'  do script "cd \\"{quoted_path}\\" && unset CLAUDECODE && {teams_env}{cmd_str}"\n'
                  f'  activate\nend tell')
        subprocess.Popen(["osascript", "-e", script])
    else:
        teams_env = "export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 && " if agent_teams else ""
        launch_cmd = f"cd '{quoted_path}' && unset CLAUDECODE && {teams_env}{cmd_str}"
        for term in ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]:
            try:
                if term == "gnome-terminal":
                    subprocess.Popen([term, "--", "bash", "-c", launch_cmd])
                else:
                    subprocess.Popen([term, "-e", "bash", "-c", launch_cmd])
                break
            except FileNotFoundError:
                continue


def toggle_autostart(enable: bool):
    if not IS_WINDOWS:
        return
    vbs_path = STARTUP_DIR / "claude-launcher.vbs"
    if enable:
        script_path = str(Path(__file__).resolve()).replace("'", "''")
        try:
            STARTUP_DIR.mkdir(parents=True, exist_ok=True)
            with open(vbs_path, 'w') as f:
                f.write(f'Set ws = CreateObject("WScript.Shell")\n'
                        f'ws.Run "pythonw ""{script_path}""", 0, False\n')
        except OSError:
            pass
    else:
        try:
            vbs_path.unlink(missing_ok=True)
        except OSError:
            pass


# ── Color palette ──
BG = "#0f0e17"
SURFACE = "#1a1932"
SURFACE2 = "#232046"
BORDER = "#2e2b4a"
ACCENT = "#7f5af0"
ACCENT_HOVER = "#6b46d6"
GREEN = "#2cb67d"
GREEN_HOVER = "#24a06d"
GOLD = "#ffd369"
RED = "#e53170"
YELLOW = "#fbbf24"
TEXT = "#fffffe"
TEXT_DIM = "#94a1b2"
TEXT_MUTED = "#72757e"
PREVIEW = "#a78bfa"
CYAN = "#22d3ee"


def _hover_btn(widget, normal_bg, hover_bg):
    widget.bind("<Enter>", lambda e: widget.config(bg=hover_bg))
    widget.bind("<Leave>", lambda e: widget.config(bg=normal_bg))


class SessionLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Claude Code Session Launcher")
        self.geometry("850x650")
        self.minsize(700, 460)
        self.configure(bg=BG)
        try:
            self.iconbitmap(default='')
        except Exception:
            pass

        self.config_data = load_config()
        self.skip_perms = tk.BooleanVar(value=True)
        self.remote_control = tk.BooleanVar(value=self.config_data.get("remote_control", False))
        self.remote_control.trace_add("write", self._on_remote_control_toggle)
        self.mode = tk.StringVar(value="continue")
        self.auto_start = tk.BooleanVar(value=self.config_data.get("auto_start", False))
        self.auto_start.trace_add("write", self._on_autostart_toggle)
        self.compact_mode = tk.BooleanVar(value=self.config_data.get("compact", False))
        self.compact_mode.trace_add("write", self._on_compact_toggle)
        self.always_on_top = tk.BooleanVar(value=False)
        self.always_on_top.trace_add("write", self._on_topmost_toggle)
        self.project_checks = {}
        self.project_data = {}
        self.project_dropdowns = {}
        self._card_frames = {}
        self._card_order = []
        self._card_widgets = {}
        self._last_checked_key = None
        self._focused_idx = -1
        self._last_refresh = None

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search_change)
        self.sort_var = tk.StringVar(value=self.config_data.get("sort", "recent"))
        self.sort_var.trace_add("write", self._on_sort_change)
        self._search_placeholder_on = True
        self._loading = False
        self._watcher_running = True

        self._setup_styles()
        self._build_ui()

        # Restore window geometry
        saved_geo = self.config_data.get("geometry", "")
        if saved_geo:
            try:
                self.geometry(saved_geo)
            except Exception:
                self._center_window()
        else:
            self._center_window()

        # Keyboard shortcuts
        self.bind("<Control-a>", lambda e: self._select_all())
        self.bind("<Control-A>", lambda e: self._select_all())
        self.bind("<Escape>", lambda e: self._on_escape())
        self.bind("<Return>", lambda e: self._launch_selected())
        self.bind("<Control-r>", lambda e: self._refresh_projects())
        self.bind("<Control-R>", lambda e: self._refresh_projects())
        self.bind("<Control-f>", lambda e: self._focus_search())
        self.bind("<Control-F>", lambda e: self._focus_search())
        self.bind("<Control-p>", lambda e: self._show_command_palette())
        self.bind("<Control-P>", lambda e: self._show_command_palette())
        self.bind("<Control-e>", lambda e: self._export_config())
        self.bind("<Control-g>", lambda e: self._show_session_search())
        self.bind("<Control-G>", lambda e: self._show_session_search())
        self.bind("<Down>", self._on_arrow_down)
        self.bind("<Up>", self._on_arrow_up)
        self.bind("<space>", self._on_space_key)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._start_file_watcher()
        self._update_last_seen()
        self._setup_tray()

    def _center_window(self):
        self.update_idletasks()
        x = (self.winfo_screenwidth() // 2) - (self.winfo_reqwidth() // 2)
        y = (self.winfo_screenheight() // 2) - (self.winfo_reqheight() // 2)
        self.geometry(f"+{x}+{y}")

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=SURFACE2, background=SURFACE2,
                        foreground=TEXT_DIM, selectbackground=ACCENT, arrowcolor=TEXT_DIM, borderwidth=0)
        style.map("TCombobox", fieldbackground=[("readonly", SURFACE2)],
                  selectbackground=[("readonly", ACCENT)], foreground=[("readonly", TEXT_DIM)])
        style.configure("Dark.TNotebook", background=BG, borderwidth=0)
        style.configure("Dark.TNotebook.Tab", background=SURFACE, foreground=TEXT_MUTED,
                        padding=[14, 6], font=("Segoe UI", 10))
        style.map("Dark.TNotebook.Tab",
                  background=[("selected", ACCENT), ("active", SURFACE2)],
                  foreground=[("selected", TEXT), ("active", TEXT)],
                  expand=[("selected", [0, 0, 0, 2])])
        style.layout("Dark.TNotebook", [("Dark.TNotebook.client", {"sticky": "nswe"})])
        self.option_add("*TCombobox*Listbox.background", SURFACE2)
        self.option_add("*TCombobox*Listbox.foreground", TEXT_DIM)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", TEXT)

    def _build_ui(self):
        # ── Header ──
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=20, pady=(18, 0))
        tk.Label(header, text="\u2728", bg=BG, font=("Segoe UI", 18)).pack(side="left")
        title_frame = tk.Frame(header, bg=BG)
        title_frame.pack(side="left", padx=(8, 0))
        tk.Label(title_frame, text="Claude Code Launcher",
                 bg=BG, fg=TEXT, font=("Segoe UI", 17, "bold")).pack(anchor="w")
        self.subtitle_label = tk.Label(title_frame, text="Resume your sessions in one click",
                                        bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 9))
        self.subtitle_label.pack(anchor="w")

        btn_frame = tk.Frame(header, bg=BG)
        btn_frame.pack(side="right")
        quit_btn = tk.Button(btn_frame, text="\u2715 Quit", bg=RED, fg=TEXT,
                             font=("Segoe UI", 9), relief="flat", padx=10, pady=5,
                             activebackground="#c42860", cursor="hand2", command=self._quit_app)
        quit_btn.pack(side="right", padx=(6, 0), pady=(4, 0))
        _hover_btn(quit_btn, RED, "#c42860")
        refresh_btn = tk.Button(btn_frame, text="\u21bb  Refresh", bg=SURFACE2, fg=TEXT_DIM,
                                font=("Segoe UI", 9), relief="flat", padx=12, pady=5,
                                activebackground=BORDER, cursor="hand2", command=self._refresh_projects)
        refresh_btn.pack(side="right", pady=(4, 0))
        _hover_btn(refresh_btn, SURFACE2, BORDER)

        # ── Notebook (tabs) ──
        self.notebook = ttk.Notebook(self, style="Dark.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=0, pady=(8, 0))
        projects_tab = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(projects_tab, text="  Projects  ")

        # ── Options panel ──
        opts_outer = tk.Frame(projects_tab, bg=BORDER)
        opts_outer.pack(fill="x", padx=16, pady=(8, 0), ipady=1)
        opts = tk.Frame(opts_outer, bg=SURFACE)
        opts.pack(fill="x", padx=1, pady=1, ipady=8)
        left_opts = tk.Frame(opts, bg=SURFACE)
        left_opts.pack(side="left", padx=(14, 0))
        tk.Checkbutton(left_opts, text="Skip permissions", variable=self.skip_perms,
                       bg=SURFACE, fg=TEXT, selectcolor=ACCENT, activebackground=SURFACE,
                       activeforeground=TEXT, font=("Segoe UI", 10),
                       highlightthickness=0, bd=0).pack(side="left", padx=(0, 12))
        tk.Checkbutton(left_opts, text="Remote control", variable=self.remote_control,
                       bg=SURFACE, fg=TEXT, selectcolor=ACCENT, activebackground=SURFACE,
                       activeforeground=TEXT, font=("Segoe UI", 10),
                       highlightthickness=0, bd=0).pack(side="left", padx=(0, 20))
        tk.Frame(left_opts, bg=BORDER, width=1).pack(side="left", fill="y", padx=(0, 16), pady=2)
        tk.Radiobutton(left_opts, text="--continue", variable=self.mode, value="continue",
                       bg=SURFACE, fg=TEXT, selectcolor=ACCENT, activebackground=SURFACE,
                       activeforeground=TEXT, font=("Segoe UI", 10),
                       highlightthickness=0, bd=0).pack(side="left", padx=(0, 8))
        tk.Radiobutton(left_opts, text="-r (pick session)", variable=self.mode, value="resume",
                       bg=SURFACE, fg=TEXT, selectcolor=ACCENT, activebackground=SURFACE,
                       activeforeground=TEXT, font=("Segoe UI", 10),
                       highlightthickness=0, bd=0).pack(side="left")
        right_opts = tk.Frame(opts, bg=SURFACE)
        right_opts.pack(side="right", padx=(0, 14))
        tk.Checkbutton(right_opts, text="Auto-start", variable=self.auto_start,
                       bg=SURFACE, fg=TEXT_MUTED, selectcolor=ACCENT, activebackground=SURFACE,
                       activeforeground=TEXT_MUTED, font=("Segoe UI", 9),
                       highlightthickness=0, bd=0).pack(side="right", padx=(8, 0))
        tk.Checkbutton(right_opts, text="Compact", variable=self.compact_mode,
                       bg=SURFACE, fg=TEXT_MUTED, selectcolor=ACCENT, activebackground=SURFACE,
                       activeforeground=TEXT_MUTED, font=("Segoe UI", 9),
                       highlightthickness=0, bd=0).pack(side="right")
        tk.Checkbutton(right_opts, text="On Top", variable=self.always_on_top,
                       bg=SURFACE, fg=TEXT_MUTED, selectcolor=ACCENT, activebackground=SURFACE,
                       activeforeground=TEXT_MUTED, font=("Segoe UI", 9),
                       highlightthickness=0, bd=0).pack(side="right")

        # ── Search + Sort bar ──
        search_bar = tk.Frame(projects_tab, bg=BG)
        search_bar.pack(fill="x", padx=16, pady=(8, 0))
        search_frame = tk.Frame(search_bar, bg=SURFACE2)
        search_frame.pack(side="left", fill="x", expand=True, padx=(0, 8))
        tk.Label(search_frame, text=" \U0001f50d", bg=SURFACE2, fg=TEXT_MUTED,
                 font=("Segoe UI", 10)).pack(side="left", padx=(6, 0))
        self.search_entry = tk.Entry(search_frame, textvariable=self.search_var,
                                      bg=SURFACE2, fg=TEXT_MUTED, font=("Segoe UI", 10),
                                      insertbackground=TEXT, relief="flat", bd=0)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        self.search_entry.insert(0, "Search projects...")
        self.search_entry.bind("<FocusIn>", self._search_focus_in)
        self.search_entry.bind("<FocusOut>", self._search_focus_out)
        # Session search button
        ss_btn = tk.Button(search_bar, text="\U0001f50e Deep", bg=SURFACE2, fg=TEXT_MUTED,
                           font=("Segoe UI", 8), relief="flat", padx=8, pady=3,
                           activebackground=BORDER, cursor="hand2", command=self._show_session_search)
        ss_btn.pack(side="left", padx=(0, 8))
        _hover_btn(ss_btn, SURFACE2, BORDER)

        sort_frame = tk.Frame(search_bar, bg=BG)
        sort_frame.pack(side="right")
        tk.Label(sort_frame, text="Sort:", bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        ttk.Combobox(sort_frame, textvariable=self.sort_var,
                     values=["recent", "name", "sessions"], width=10, state="readonly").pack(side="left")

        # ── Toolbar ──
        toolbar = tk.Frame(projects_tab, bg=BG)
        toolbar.pack(fill="x", padx=16, pady=(6, 4))
        sel_all = tk.Button(toolbar, text="Select All", bg=SURFACE2, fg=TEXT_DIM,
                            font=("Segoe UI", 9), relief="flat", padx=10, pady=4,
                            activebackground=BORDER, cursor="hand2", command=self._select_all)
        sel_all.pack(side="left", padx=(0, 4))
        _hover_btn(sel_all, SURFACE2, BORDER)
        sel_none = tk.Button(toolbar, text="Deselect", bg=SURFACE2, fg=TEXT_DIM,
                             font=("Segoe UI", 9), relief="flat", padx=10, pady=4,
                             activebackground=BORDER, cursor="hand2", command=self._select_none)
        sel_none.pack(side="left", padx=(0, 12))
        _hover_btn(sel_none, SURFACE2, BORDER)
        legend = tk.Frame(toolbar, bg=BG)
        legend.pack(side="left", padx=(6, 0))
        for sym, color, label in [("\u2605", GOLD, "pinned"), ("\u2713", GREEN, "clean"),
                                  ("\u26a0", YELLOW, "interrupted"), ("\u25cf", CYAN, "new")]:
            tk.Label(legend, text=sym, bg=BG, fg=color, font=("Segoe UI", 9)).pack(side="left")
            tk.Label(legend, text=label, bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(1, 8))
        self.project_count_label = tk.Label(toolbar, text="", bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 8))
        self.project_count_label.pack(side="left", padx=(8, 0))
        self.bulk_launch_btn = tk.Button(toolbar, text="  Launch Selected (0)  ", bg="#4a4a4a", fg=TEXT,
                                          font=("Segoe UI", 10, "bold"), relief="flat", padx=20, pady=5,
                                          activebackground=GREEN_HOVER, cursor="hand2", state="disabled",
                                          command=self._launch_selected)
        self.bulk_launch_btn.pack(side="right")

        # ── Loading indicator ──
        self.loading_label = tk.Label(projects_tab, text="\u23f3 Loading projects...",
                                       bg=BG, fg=ACCENT, font=("Segoe UI", 11))

        # ── Scrollable project list ──
        container = tk.Frame(projects_tab, bg=BG)
        container.pack(fill="both", expand=True, padx=16, pady=(0, 0))
        self.canvas = tk.Canvas(container, bg=BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(container, orient="vertical", command=self.canvas.yview,
                                 bg=BG, troughcolor=BG, width=8, relief="flat")
        self.scrollable = tk.Frame(self.canvas, bg=BG)
        self.scrollable.bind("<Configure>",
                             lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width))
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.bind_all("<MouseWheel>",
                      lambda e: self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        # ── Status bar ──
        self.status_bar = tk.Frame(self, bg=SURFACE)
        self.status_bar.pack(fill="x", side="bottom")
        self.status_label = tk.Label(self.status_bar, text="", bg=SURFACE, fg=TEXT_MUTED,
                                      font=("Consolas", 8), anchor="w")
        self.status_label.pack(side="left", padx=12, pady=3)
        self.status_right = tk.Label(self.status_bar, text="", bg=SURFACE, fg=TEXT_MUTED,
                                      font=("Consolas", 8), anchor="e")
        self.status_right.pack(side="right", padx=12, pady=3)

        # ── Additional tabs ──
        self._build_sessions_tab()
        self._build_hooks_tab()
        self._build_mcp_tab()
        self._build_plugins_tab()
        self._build_profiles_tab()
        self._build_claudemd_tab()

        self._refresh_projects()

    # ── Search placeholder ──

    def _search_focus_in(self, e):
        if self._search_placeholder_on:
            self._search_placeholder_on = False
            self.search_entry.delete(0, tk.END)
            self.search_entry.config(fg=TEXT)

    def _search_focus_out(self, e):
        if not self.search_entry.get().strip():
            self._search_placeholder_on = True
            self.search_entry.config(fg=TEXT_MUTED)
            self.search_entry.insert(0, "Search projects...")

    # ── Window management ──

    def _on_close(self):
        self._save_geometry()
        self.withdraw()

    def _quit_app(self):
        self._remove_tray()
        self._save_geometry()
        self._watcher_running = False
        self.destroy()

    def _save_geometry(self):
        try:
            self.config_data["geometry"] = self.geometry()
            save_config(self.config_data)
        except Exception:
            pass

    # ── System Tray (Windows) ──

    def _setup_tray(self):
        if not IS_WINDOWS:
            return
        try:
            from ctypes import wintypes, WINFUNCTYPE, byref, sizeof, c_wchar, c_int, c_long

            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            kernel32 = ctypes.windll.kernel32

            WM_TRAYICON = 0x0420
            WM_COMMAND = 0x0111
            WM_LBUTTONUP = 0x0202
            WM_LBUTTONDBLCLK = 0x0203
            WM_RBUTTONUP = 0x0205

            WNDPROC = WINFUNCTYPE(c_long, wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM)

            class WNDCLASSEXW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", c_int), ("cbWndExtra", c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                    ("hIconSm", wintypes.HICON),
                ]

            class NOTIFYICONDATAW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                    ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                    ("szTip", c_wchar * 128),
                ]

            app = self

            def _wnd_proc(hwnd, msg, wp, lp):
                try:
                    if msg == WM_TRAYICON:
                        if lp in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                            app.after(0, app._tray_restore)
                        elif lp == WM_RBUTTONUP:
                            menu = user32.CreatePopupMenu()
                            user32.InsertMenuW(menu, 0, 0x0, 1, "Show")
                            user32.InsertMenuW(menu, 1, 0x0, 2, "Quit")
                            pt = wintypes.POINT()
                            user32.GetCursorPos(byref(pt))
                            user32.SetForegroundWindow(hwnd)
                            user32.TrackPopupMenu(menu, 0, pt.x, pt.y, 0, hwnd, None)
                            user32.DestroyMenu(menu)
                    elif msg == WM_COMMAND:
                        if (wp & 0xFFFF) == 1:
                            app.after(0, app._tray_restore)
                        elif (wp & 0xFFFF) == 2:
                            app.after(0, app._tray_quit)
                except Exception:
                    pass
                return user32.DefWindowProcW(hwnd, msg, wp, lp)

            self._tray_wndproc = WNDPROC(_wnd_proc)

            def _create():
                try:
                    hinst = kernel32.GetModuleHandleW(None)
                    wc = WNDCLASSEXW()
                    wc.cbSize = sizeof(WNDCLASSEXW)
                    wc.lpfnWndProc = self._tray_wndproc
                    wc.hInstance = hinst
                    wc.lpszClassName = "ClaudeLauncherTray"
                    if not user32.RegisterClassExW(byref(wc)):
                        return
                    hwnd = user32.CreateWindowExW(
                        0, "ClaudeLauncherTray", "Tray", 0,
                        0, 0, 0, 0, None, None, hinst, None)
                    if not hwnd:
                        return
                    self._tray_hwnd = hwnd
                    icon = user32.LoadIconW(0, 32512)
                    nid = NOTIFYICONDATAW()
                    nid.cbSize = sizeof(NOTIFYICONDATAW)
                    nid.hWnd = hwnd
                    nid.uID = 1
                    nid.uFlags = 0x1 | 0x2 | 0x4
                    nid.uCallbackMessage = WM_TRAYICON
                    nid.hIcon = icon
                    nid.szTip = "Claude Code Launcher"
                    shell32.Shell_NotifyIconW(0, byref(nid))
                    self._tray_nid = nid
                    msg = wintypes.MSG()
                    while user32.GetMessageW(byref(msg), None, 0, 0) > 0:
                        user32.TranslateMessage(byref(msg))
                        user32.DispatchMessageW(byref(msg))
                except Exception:
                    pass

            threading.Thread(target=_create, daemon=True).start()
        except Exception:
            pass

    def _remove_tray(self):
        if hasattr(self, '_tray_nid'):
            try:
                ctypes.windll.shell32.Shell_NotifyIconW(
                    0x2, ctypes.byref(self._tray_nid))
            except Exception:
                pass

    def _tray_restore(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _tray_quit(self):
        self._remove_tray()
        self._save_geometry()
        self._watcher_running = False
        self.destroy()

    def _on_escape(self):
        if self.search_var.get() and not self._search_placeholder_on:
            self.search_var.set("")
            self.focus_set()
        else:
            self._select_none()
            self._focused_idx = -1
            self._highlight_focused()

    def _focus_search(self):
        self.search_entry.focus_set()
        if not self._search_placeholder_on:
            self.search_entry.select_range(0, tk.END)

    # ── Always on top ──

    def _on_topmost_toggle(self, *_a):
        self.attributes('-topmost', self.always_on_top.get())

    # ── Keyboard card navigation ──

    def _on_arrow_down(self, e):
        if isinstance(self.focus_get(), (tk.Entry, ttk.Combobox, tk.Listbox,
                                         tk.Checkbutton, tk.Radiobutton)):
            return
        if not self._card_order:
            return
        self._focused_idx = min(self._focused_idx + 1, len(self._card_order) - 1)
        self._highlight_focused()
        return "break"

    def _on_arrow_up(self, e):
        if isinstance(self.focus_get(), (tk.Entry, ttk.Combobox, tk.Listbox,
                                         tk.Checkbutton, tk.Radiobutton)):
            return
        if not self._card_order:
            return
        self._focused_idx = max(self._focused_idx - 1, 0)
        self._highlight_focused()
        return "break"

    def _on_space_key(self, e):
        focused = self.focus_get()
        if isinstance(focused, (tk.Entry, ttk.Combobox, tk.Listbox, tk.Text,
                                tk.Checkbutton, tk.Radiobutton)):
            return
        if 0 <= self._focused_idx < len(self._card_order):
            key = self._card_order[self._focused_idx]
            if key in self.project_checks:
                self.project_checks[key].set(not self.project_checks[key].get())
                self._update_bulk_count()
            return "break"

    def _highlight_focused(self):
        for key, widget in self._card_widgets.items():
            widget.config(highlightthickness=0)
        if 0 <= self._focused_idx < len(self._card_order):
            key = self._card_order[self._focused_idx]
            if key in self._card_widgets:
                widget = self._card_widgets[key]
                widget.config(highlightbackground=ACCENT, highlightcolor=ACCENT, highlightthickness=2)
                self.canvas.update_idletasks()
                widget.update_idletasks()
                y = widget.winfo_y()
                h = widget.winfo_height()
                scroll_h = self.scrollable.winfo_height()
                if scroll_h > 0:
                    canvas_h = self.canvas.winfo_height()
                    top = self.canvas.canvasy(0)
                    if y < top:
                        self.canvas.yview_moveto(y / scroll_h)
                    elif y + h > top + canvas_h:
                        self.canvas.yview_moveto((y + h - canvas_h) / scroll_h)

    # ── Sessions tab ──

    def _build_sessions_tab(self):
        frame = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(frame, text="  Sessions  ")
        search_row = tk.Frame(frame, bg=BG)
        search_row.pack(fill="x", padx=14, pady=(10, 0))
        sf = tk.Frame(search_row, bg=SURFACE2)
        sf.pack(side="left", fill="x", expand=True)
        tk.Label(sf, text=" \U0001f50d", bg=SURFACE2, fg=TEXT_MUTED, font=("Segoe UI", 10)).pack(side="left")
        self.sessions_search_var = tk.StringVar()
        self.sessions_search_var.trace_add("write", lambda *_: self._sessions_apply_filter())
        tk.Entry(sf, textvariable=self.sessions_search_var, bg=SURFACE2, fg=TEXT,
                 font=("Segoe UI", 10), insertbackground=TEXT, relief="flat", bd=0
                 ).pack(side="left", fill="x", expand=True, padx=6, pady=6)
        self.sessions_stats_label = tk.Label(frame, text="Loading...", bg=BG, fg=TEXT_MUTED,
                                              font=("Consolas", 8), anchor="w")
        self.sessions_stats_label.pack(fill="x", padx=16, pady=(4, 2))
        hdr = tk.Frame(frame, bg=SURFACE)
        hdr.pack(fill="x", padx=14)
        for txt, w in [("Project", 18), ("Session ID", 14), ("Date", 10), ("Dur", 7), ("Tokens", 7), ("Cost", 7), ("Health", 8)]:
            tk.Label(hdr, text=txt, bg=SURFACE, fg=TEXT_MUTED, font=("Consolas", 8, "bold"),
                     width=w, anchor="w").pack(side="left", padx=(4, 0), pady=3)
        list_frame = tk.Frame(frame, bg=BG)
        list_frame.pack(fill="both", expand=True, padx=14)
        self.sessions_listbox = tk.Listbox(list_frame, bg=SURFACE, fg=TEXT, font=("Consolas", 9),
            selectbackground=ACCENT, selectforeground=TEXT, relief="flat", bd=0,
            activestyle="none", highlightthickness=0)
        slb_sb = tk.Scrollbar(list_frame, orient="vertical", command=self.sessions_listbox.yview,
                               bg=BG, troughcolor=BG, width=8, relief="flat")
        self.sessions_listbox.configure(yscrollcommand=slb_sb.set)
        self.sessions_listbox.pack(side="left", fill="both", expand=True)
        slb_sb.pack(side="right", fill="y")
        self.sessions_listbox.bind("<<ListboxSelect>>", self._on_session_select)
        preview_outer = tk.Frame(frame, bg=BORDER)
        preview_outer.pack(fill="x", padx=14, pady=(4, 6), ipady=1)
        preview_inner = tk.Frame(preview_outer, bg=SURFACE2)
        preview_inner.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(preview_inner, text="Conversation preview", bg=SURFACE2, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", padx=8, pady=(4, 0))
        self.sessions_preview = tk.Text(preview_inner, bg=SURFACE2, fg=TEXT_DIM,
                                         font=("Consolas", 8), relief="flat", bd=0,
                                         height=5, state="disabled", wrap="word", highlightthickness=0)
        self.sessions_preview.pack(fill="x", padx=8, pady=(2, 4))
        self.sessions_preview.tag_configure("user", foreground=CYAN)
        self.sessions_preview.tag_configure("assistant", foreground=TEXT_DIM)
        self.sessions_preview.tag_configure("tool", foreground=TEXT_MUTED)
        self.sessions_preview.tag_configure("files_hdr", foreground=GOLD)
        btn_row = tk.Frame(frame, bg=BG)
        btn_row.pack(fill="x", padx=14, pady=(0, 4))
        export_btn = tk.Button(btn_row, text="Export MD", bg=SURFACE2, fg=CYAN,
                               font=("Segoe UI", 9), relief="flat", padx=10, pady=3,
                               cursor="hand2", command=self._sessions_export)
        export_btn.pack(side="left")
        _hover_btn(export_btn, SURFACE2, ACCENT)
        self._sessions_all = []
        self._sessions_filtered = []
        threading.Thread(target=self._sessions_load_bg, daemon=True).start()

    def _sessions_load_bg(self):
        rows, total_bytes, total_cost = [], 0, 0.0
        try:
            projects = get_projects()
        except Exception:
            projects = []
        for p in projects:
            name = p['decoded_path'].split('/')[-1].split('\\')[-1] or p['encoded_name']
            for s in p.get('sessions', []):
                cost_info = get_session_cost(s['file'])
                s['cost_usd'] = cost_info['cost_usd']
                total_cost += cost_info['cost_usd']
                rows.append((name, s))
                total_bytes += s.get('size', 0)
        rows.sort(key=lambda x: x[1]['modified'], reverse=True)
        n_proj = len({r[0] for r in rows})
        cost_str = f"${total_cost:.2f}" if total_cost > 0 else "$0"
        stats = f"{len(rows)} sessions across {n_proj} projects  |  ~{_token_estimate(total_bytes)} tokens  |  {cost_str} total cost"
        self._sessions_all = rows
        self._sessions_filtered = rows[:]
        self.after(0, lambda: self._sessions_render(stats))

    def _sessions_apply_filter(self):
        q = self.sessions_search_var.get().lower().strip()
        if not q:
            self._sessions_filtered = self._sessions_all[:]
        else:
            self._sessions_filtered = [
                (name, s) for name, s in self._sessions_all
                if q in name.lower() or q in s['id'].lower()
                or any(q in t['text'].lower() for t in s.get('preview', []))
            ]
        self._sessions_render(None)

    def _sessions_render(self, stats):
        lb = self.sessions_listbox
        lb.delete(0, tk.END)
        for name, s in self._sessions_filtered:
            proj = name[:16].ljust(16)
            sid = s['id'][:12].ljust(12)
            date = _relative_time(s['modified']).ljust(8)
            dur = _duration_str(s['duration']).ljust(5)
            tok = _token_estimate(s['size']).ljust(5)
            cost = s.get('cost_usd', 0)
            cost_s = f"${cost:.2f}".ljust(5) if cost > 0 else "  -  "
            hmark = "clean" if s['health'] == "clean" else "intrp" if s['health'] == "interrupted" else "  ?  "
            lb.insert(tk.END, f"  {proj}  {sid}  {date}  {dur}  {tok}  {cost_s}  {hmark}")
            color = GREEN if s['health'] == "clean" else GOLD if s['health'] == "interrupted" else TEXT_MUTED
            lb.itemconfig(tk.END - 1, foreground=color)
        if stats:
            self.sessions_stats_label.config(text=stats)
        elif not self._sessions_all:
            self.sessions_stats_label.config(text="No sessions found")
        else:
            self.sessions_stats_label.config(text=f"Showing {len(self._sessions_filtered)} of {len(self._sessions_all)} sessions")

    def _on_session_select(self, _event=None):
        sel = self.sessions_listbox.curselection()
        if not sel or sel[0] >= len(self._sessions_filtered):
            return
        _name, s = self._sessions_filtered[sel[0]]
        turns = s.get('preview') or []
        files = get_session_files(s['file']) if s.get('file') else []
        pv = self.sessions_preview
        pv.config(state="normal")
        pv.delete("1.0", tk.END)
        for t in turns:
            prefix = "You: " if t['role'] == "user" else "AI:  " if t['role'] == "assistant" else "   > "
            pv.insert(tk.END, prefix, t['role'])
            pv.insert(tk.END, t['text'][:120] + "\n")
        if files:
            pv.insert(tk.END, "\nFiles touched: ", "files_hdr")
            pv.insert(tk.END, "  ".join(f.split('/')[-1].split('\\')[-1] for f in files[:8]) + "\n", "tool")
        pv.config(state="disabled")

    def _sessions_export(self):
        sel = self.sessions_listbox.curselection()
        if not sel or sel[0] >= len(self._sessions_filtered):
            self._show_toast("Select a session first")
            return
        _name, s = self._sessions_filtered[sel[0]]
        path = filedialog.asksaveasfilename(
            defaultextension=".md", filetypes=[("Markdown", "*.md")],
            initialfile=f"session-{s['id'][:8]}.md")
        if path:
            md = export_session_markdown(s['file'])
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(f"# Session {s['id'][:12]}\n\n**Project:** {_name}\n**Date:** {s['modified']}\n\n---\n{md}")
                self._show_toast(f"Exported to {Path(path).name}")
            except OSError as e:
                self._show_toast(f"Export failed: {e}")

    # ── Hooks tab ──

    def _build_hooks_tab(self):
        frame = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(frame, text="  Hooks  ")
        settings_file = Path.home() / ".claude" / "settings.json"
        self._hooks_data = {}
        self._hooks_unsaved = False
        try:
            if settings_file.exists():
                raw = json.loads(settings_file.read_text(encoding="utf-8"))
                self._hooks_data = raw.get("hooks", {})
        except Exception:
            pass
        HOOK_EVENTS = ["SessionStart", "PreToolUse", "PostToolUse",
                       "UserPromptSubmit", "Stop", "PreCompact"]
        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(top, text="Claude Hooks", bg=BG, fg=ACCENT, font=("Segoe UI", 13, "bold")).pack(side="left")
        self._hooks_status = tk.Label(top, text="", bg=BG, fg=GOLD, font=("Segoe UI", 9))
        self._hooks_status.pack(side="left", padx=10)
        save_btn = tk.Button(top, text="Save", bg=ACCENT, fg=TEXT, relief="flat", padx=12, pady=3,
                             font=("Segoe UI", 9, "bold"), cursor="hand2",
                             command=lambda: self._hooks_save(settings_file))
        save_btn.pack(side="right")
        _hover_btn(save_btn, ACCENT, GREEN)
        body = tk.Frame(frame, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=10)
        left = tk.Frame(body, bg=SURFACE, width=160, bd=0, highlightthickness=1, highlightbackground=BORDER)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)
        tk.Label(left, text="Events", bg=SURFACE, fg=TEXT_DIM, font=("Segoe UI", 8, "bold")).pack(pady=(8, 4))
        self._hooks_event_btns = {}
        self._hooks_selected_event = tk.StringVar(value="")
        right_scroll = tk.Frame(body, bg=BG)
        right_scroll.pack(side="left", fill="both", expand=True)
        hk_canvas = tk.Canvas(right_scroll, bg=BG, highlightthickness=0)
        hk_sb = ttk.Scrollbar(right_scroll, orient="vertical", command=hk_canvas.yview)
        hk_canvas.configure(yscrollcommand=hk_sb.set)
        hk_sb.pack(side="right", fill="y")
        hk_canvas.pack(side="left", fill="both", expand=True)
        right_inner = tk.Frame(hk_canvas, bg=BG)
        hk_win = hk_canvas.create_window((0, 0), window=right_inner, anchor="nw")
        right_inner.bind("<Configure>", lambda e: hk_canvas.configure(scrollregion=hk_canvas.bbox("all")))
        hk_canvas.bind("<Configure>", lambda e: hk_canvas.itemconfig(hk_win, width=e.width))
        add_btn = tk.Button(top, text="+ Add Hook", bg=SURFACE2, fg=TEXT, relief="flat", padx=10, pady=3,
                            font=("Segoe UI", 9), cursor="hand2",
                            command=lambda: self._hooks_add_dialog(HOOK_EVENTS, right_inner))
        add_btn.pack(side="right", padx=(0, 8))
        def _select_event(evt):
            self._hooks_selected_event.set(evt)
            for e, b in self._hooks_event_btns.items():
                b.configure(bg=ACCENT if e == evt else SURFACE, fg=TEXT if e == evt else TEXT_DIM)
            self._hooks_render_groups(evt, right_inner)
        for evt in HOOK_EVENTS:
            b = tk.Button(left, text=evt, bg=SURFACE, fg=TEXT_DIM, relief="flat", anchor="w",
                          padx=8, pady=5, font=("Segoe UI", 9), cursor="hand2", width=17,
                          command=lambda e=evt: _select_event(e))
            b.pack(fill="x", padx=4, pady=1)
            self._hooks_event_btns[evt] = b
        if HOOK_EVENTS:
            _select_event(HOOK_EVENTS[0])

    def _hooks_render_groups(self, event, container):
        for w in container.winfo_children():
            w.destroy()
        groups = self._hooks_data.get(event, [])
        tk.Label(container, text=event, bg=BG, fg=ACCENT, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(4, 8))
        if not groups:
            tk.Label(container, text="No hooks configured for this event.",
                     bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(anchor="w")
            return
        for idx, group in enumerate(groups):
            is_disabled = group.get("_disabled", False)
            card = tk.Frame(container, bg=SURFACE2, bd=0, highlightthickness=1, highlightbackground=BORDER)
            card.pack(fill="x", pady=4, padx=2)
            header = tk.Frame(card, bg=SURFACE2)
            header.pack(fill="x", padx=8, pady=(6, 2))
            matcher = group.get("matcher", "")
            label_text = f"matcher: {matcher}" if matcher else "(no matcher)"
            strike_font = ("Segoe UI", 9, "overstrike") if is_disabled else ("Segoe UI", 9, "bold")
            fg_col = TEXT_MUTED if is_disabled else TEXT
            tk.Label(header, text=label_text, bg=SURFACE2, fg=fg_col, font=strike_font).pack(side="left")
            tog_text = "Enable" if is_disabled else "Disable"
            tog_fg = GREEN if is_disabled else GOLD
            def _toggle(i=idx, e=event, c=container):
                self._hooks_data[e][i]["_disabled"] = not self._hooks_data[e][i].get("_disabled", False)
                self._hooks_mark_unsaved()
                self._hooks_render_groups(e, c)
            tk.Button(header, text=tog_text, bg=SURFACE2, fg=tog_fg, relief="flat",
                      font=("Segoe UI", 8), cursor="hand2", command=_toggle).pack(side="right", padx=(4, 0))
            def _delete(i=idx, e=event, c=container):
                self._hooks_data.setdefault(e, []).pop(i)
                self._hooks_mark_unsaved()
                self._hooks_render_groups(e, c)
            tk.Button(header, text="\u2715", bg=SURFACE2, fg=RED, relief="flat",
                      font=("Segoe UI", 8), cursor="hand2", command=_delete).pack(side="right")
            for h in group.get("hooks", []):
                cmd = h.get("command", "")
                short = ("\u2026" + cmd[-48:]) if len(cmd) > 50 else cmd
                timeout = h.get("timeout", "")
                info = f"  cmd: {short}" + (f"  |  timeout: {timeout}s" if timeout else "")
                tk.Label(card, text=info, bg=SURFACE2, fg=TEXT_MUTED if is_disabled else TEXT_DIM,
                         font=("Segoe UI", 8), anchor="w").pack(fill="x", padx=8, pady=(0, 4))

    def _hooks_mark_unsaved(self):
        self._hooks_unsaved = True
        self._hooks_status.configure(text="\u25cf unsaved changes")

    def _hooks_save(self, settings_file):
        try:
            raw = {}
            if settings_file.exists():
                raw = json.loads(settings_file.read_text(encoding="utf-8"))
            clean = {}
            for evt, groups in self._hooks_data.items():
                clean[evt] = [{k: v for k, v in g.items() if k != "_disabled"} for g in groups]
            raw["hooks"] = clean
            settings_file.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
            self._hooks_unsaved = False
            self._hooks_status.configure(text="Saved", fg=GREEN)
            self._hooks_status.after(3000, lambda: self._hooks_status.configure(
                text="" if not self._hooks_unsaved else "\u25cf unsaved changes", fg=GOLD))
        except Exception as ex:
            self._hooks_status.configure(text=f"Error: {ex}", fg=RED)

    def _hooks_add_dialog(self, events, container):
        dlg = tk.Toplevel(bg=SURFACE)
        dlg.title("Add Hook")
        dlg.geometry("460x300")
        dlg.resizable(False, False)
        fields = {}
        rows = [("Event", events[0], events), ("Matcher (optional)", "", None),
                ("Command", "", None), ("Timeout (s, optional)", "", None)]
        for i, (label, default, choices) in enumerate(rows):
            tk.Label(dlg, text=label, bg=SURFACE, fg=TEXT_DIM, font=("Segoe UI", 9)
                     ).grid(row=i, column=0, sticky="w", padx=14, pady=6)
            if choices:
                var = tk.StringVar(value=default)
                ttk.Combobox(dlg, textvariable=var, values=choices, state="readonly", width=34
                             ).grid(row=i, column=1, padx=14, pady=6)
                fields[label] = var
            else:
                var = tk.StringVar(value=default)
                tk.Entry(dlg, textvariable=var, bg=SURFACE2, fg=TEXT, insertbackground=TEXT, relief="flat", width=36
                         ).grid(row=i, column=1, padx=14, pady=6)
                fields[label] = var
        def _confirm():
            evt = fields["Event"].get()
            matcher = fields["Matcher (optional)"].get().strip()
            cmd = fields["Command"].get().strip()
            if not cmd:
                return
            timeout_s = fields["Timeout (s, optional)"].get().strip()
            hook = {"type": "command", "command": cmd}
            if timeout_s.isdigit():
                hook["timeout"] = int(timeout_s)
            group = {"hooks": [hook]}
            if matcher:
                group["matcher"] = matcher
            self._hooks_data.setdefault(evt, []).append(group)
            self._hooks_mark_unsaved()
            if self._hooks_selected_event.get() == evt:
                self._hooks_render_groups(evt, container)
            dlg.destroy()
        tk.Button(dlg, text="Add", bg=ACCENT, fg=TEXT, relief="flat", padx=14, pady=4,
                  font=("Segoe UI", 9, "bold"), cursor="hand2", command=_confirm
                  ).grid(row=4, column=1, sticky="e", padx=14, pady=10)

    # ── MCP Servers tab ──

    def _build_mcp_tab(self):
        frame = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(frame, text="  MCP Servers  ")
        claude_json = Path.home() / ".claude.json"
        try:
            raw = json.loads(claude_json.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        self._mcp_data = raw
        servers = raw.get("mcpServers", {})
        disabled_set = set()
        for proj in raw.get("projects", {}).values():
            for d in proj.get("disabledMcpServers", []):
                disabled_set.add(d.split(":")[-1])
        self._mcp_unsaved = tk.BooleanVar(value=False)
        self._mcp_selected = tk.StringVar(value="")
        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(top, text="MCP Servers", font=("Segoe UI", 13, "bold"), bg=BG, fg=TEXT).pack(side="left")
        unsaved_lbl = tk.Label(top, text="\u25cf unsaved changes", font=("Segoe UI", 9), bg=BG, fg=YELLOW)
        def _show_unsaved(*_):
            if self._mcp_unsaved.get():
                unsaved_lbl.pack(side="left", padx=8)
            else:
                unsaved_lbl.pack_forget()
        self._mcp_unsaved.trace_add("write", _show_unsaved)
        pane = tk.Frame(frame, bg=BG)
        pane.pack(fill="both", expand=True, padx=12, pady=8)
        left = tk.Frame(pane, bg=SURFACE, bd=0, highlightthickness=1, highlightbackground=BORDER)
        left.pack(side="left", fill="both", expand=False, padx=(0, 8))
        left.configure(width=220)
        left.pack_propagate(False)
        tk.Label(left, text="Servers", font=("Segoe UI", 9, "bold"), bg=SURFACE, fg=TEXT_DIM).pack(anchor="w", padx=8, pady=(8, 4))
        cards_frame = tk.Frame(left, bg=SURFACE)
        cards_frame.pack(fill="both", expand=True, padx=4)
        right = tk.Frame(pane, bg=SURFACE, bd=0, highlightthickness=1, highlightbackground=BORDER)
        right.pack(side="left", fill="both", expand=True)
        detail_title = tk.Label(right, text="Select a server", font=("Segoe UI", 11, "bold"), bg=SURFACE, fg=TEXT)
        detail_title.pack(anchor="w", padx=12, pady=(10, 4))
        detail_body = tk.Frame(right, bg=SURFACE)
        detail_body.pack(fill="both", expand=True, padx=12)
        entry_refs = {}
        def show_detail(name):
            for w in detail_body.winfo_children():
                w.destroy()
            entry_refs.clear()
            cfg = servers.get(name, {})
            detail_title.config(text=name)
            for label, val in [("type", cfg.get("type", "")), ("command", cfg.get("command", "")),
                               ("args", " ".join(cfg.get("args", []))), ("url", cfg.get("url", "")),
                               ("env", json.dumps(cfg.get("env", {})))]:
                row = tk.Frame(detail_body, bg=SURFACE)
                row.pack(fill="x", pady=2)
                tk.Label(row, text=label, width=9, anchor="w", font=("Segoe UI", 9), bg=SURFACE, fg=TEXT_DIM).pack(side="left")
                e = tk.Entry(row, font=("Segoe UI", 9), bg=SURFACE2, fg=TEXT, insertbackground=TEXT,
                             relief="flat", highlightthickness=1, highlightbackground=BORDER)
                e.insert(0, val)
                e.pack(side="left", fill="x", expand=True, ipady=3, padx=(4, 0))
                e.bind("<Key>", lambda _: self._mcp_unsaved.set(True))
                entry_refs[label] = e
        def refresh_cards():
            for w in cards_frame.winfo_children():
                w.destroy()
            for name, cfg in servers.items():
                enabled = name not in disabled_set
                dot_color = GREEN if enabled else RED
                stype = cfg.get("type", "stdio")
                hint = cfg.get("url", cfg.get("command", ""))[:28]
                card = tk.Frame(cards_frame, bg=SURFACE2, cursor="hand2", highlightthickness=1, highlightbackground=BORDER)
                card.pack(fill="x", pady=2)
                tk.Label(card, text="\u25cf", font=("Segoe UI", 8), bg=SURFACE2, fg=dot_color).pack(side="left", padx=(6, 2))
                info = tk.Frame(card, bg=SURFACE2)
                info.pack(side="left", fill="x", expand=True, pady=4)
                tk.Label(info, text=name, font=("Segoe UI", 9, "bold"), bg=SURFACE2, fg=TEXT, anchor="w").pack(anchor="w")
                tk.Label(info, text=f"{stype}  {hint}", font=("Segoe UI", 8), bg=SURFACE2, fg=TEXT_MUTED, anchor="w").pack(anchor="w")
                for w in (card, info):
                    w.bind("<Button-1>", lambda _, n=name: [self._mcp_selected.set(n), show_detail(n)])
        refresh_cards()
        def save_servers():
            if self._mcp_selected.get() and entry_refs:
                name = self._mcp_selected.get()
                cfg = servers.setdefault(name, {})
                cfg["type"] = entry_refs["type"].get()
                if cfg["type"] == "http":
                    cfg["url"] = entry_refs["url"].get()
                else:
                    cfg["command"] = entry_refs["command"].get()
                    cfg["args"] = entry_refs["args"].get().split()
                try:
                    cfg["env"] = json.loads(entry_refs["env"].get() or "{}")
                except Exception:
                    pass
            self._mcp_data["mcpServers"] = servers
            claude_json.write_text(json.dumps(self._mcp_data, indent=2), encoding="utf-8")
            self._mcp_unsaved.set(False)
        def add_server(prefill=None):
            d = tk.Toplevel(frame)
            d.title("Add MCP Server")
            d.configure(bg=BG)
            d.geometry("420x300")
            d.resizable(False, False)
            pf = prefill or {}
            flds = {"Name": pf.get("name", ""), "Type": pf.get("type", "stdio"),
                    "Command": pf.get("command", ""), "Args": " ".join(pf.get("args", [])),
                    "URL": pf.get("url", ""), "Env (JSON)": "{}"}
            entries = {}
            for i, (lbl, default) in enumerate(flds.items()):
                tk.Label(d, text=lbl, bg=BG, fg=TEXT_DIM, font=("Segoe UI", 9)
                         ).grid(row=i, column=0, sticky="w", padx=12, pady=4)
                if lbl == "Type":
                    var = tk.StringVar(value=default)
                    ttk.Combobox(d, textvariable=var, values=["stdio", "http", "sse"],
                                 state="readonly", width=28).grid(row=i, column=1, padx=8, pady=4)
                    entries[lbl] = var
                else:
                    e = tk.Entry(d, bg=SURFACE2, fg=TEXT, insertbackground=TEXT, relief="flat",
                                 highlightthickness=1, highlightbackground=BORDER, width=30)
                    e.insert(0, default)
                    e.grid(row=i, column=1, padx=8, pady=4, ipady=3)
                    entries[lbl] = e
            def confirm():
                nm = entries["Name"].get().strip() if isinstance(entries["Name"], tk.Entry) else entries["Name"].get()
                if not nm:
                    return
                cfg = {"type": entries["Type"].get()}
                if cfg["type"] == "http":
                    cfg["url"] = entries["URL"].get() if isinstance(entries["URL"], tk.Entry) else entries["URL"].get()
                else:
                    cfg["command"] = entries["Command"].get() if isinstance(entries["Command"], tk.Entry) else entries["Command"].get()
                    args_val = entries["Args"].get() if isinstance(entries["Args"], tk.Entry) else entries["Args"].get()
                    cfg["args"] = args_val.split()
                try:
                    env_val = entries["Env (JSON)"].get() if isinstance(entries["Env (JSON)"], tk.Entry) else entries["Env (JSON)"].get()
                    cfg["env"] = json.loads(env_val or "{}")
                except Exception:
                    cfg["env"] = {}
                servers[nm] = cfg
                self._mcp_unsaved.set(True)
                refresh_cards()
                d.destroy()
            tk.Button(d, text="Add", bg=ACCENT, fg=TEXT, relief="flat",
                      font=("Segoe UI", 9, "bold"), command=confirm).grid(row=len(flds), column=1, sticky="e", padx=8, pady=8)
        def remove_server():
            name = self._mcp_selected.get()
            if name and name in servers:
                del servers[name]
                self._mcp_selected.set("")
                for w in detail_body.winfo_children():
                    w.destroy()
                detail_title.config(text="Select a server")
                self._mcp_unsaved.set(True)
                refresh_cards()
        btn_bar = tk.Frame(frame, bg=BG)
        btn_bar.pack(fill="x", padx=12, pady=(0, 6))
        for txt, cmd, color in [("+ Add Server", lambda: add_server(), ACCENT),
                                ("- Remove", remove_server, RED), ("Save", save_servers, GREEN)]:
            b = tk.Button(btn_bar, text=txt, command=cmd, bg=color, fg=TEXT, relief="flat",
                          font=("Segoe UI", 9, "bold"), padx=10, pady=4, cursor="hand2")
            b.pack(side="left", padx=(0, 6))
            _hover_btn(b, color, SURFACE2)
        qa = tk.Frame(frame, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        qa.pack(fill="x", padx=12, pady=(0, 10))
        tk.Label(qa, text="Quick Add:", font=("Segoe UI", 9, "bold"), bg=SURFACE, fg=TEXT_DIM).pack(side="left", padx=8, pady=6)
        for label, pf in [
            ("GitHub MCP", {"name": "github", "type": "http", "url": "https://api.githubcopilot.com/mcp/"}),
            ("Playwright", {"name": "playwright", "type": "stdio", "command": "npx.cmd", "args": ["-y", "@playwright/mcp@latest"]}),
            ("Filesystem", {"name": "filesystem", "type": "stdio", "command": "npx.cmd", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}),
        ]:
            b = tk.Button(qa, text=label, bg=SURFACE2, fg=CYAN, relief="flat", font=("Segoe UI", 9),
                          padx=8, pady=3, cursor="hand2", command=lambda p=pf: add_server(p))
            b.pack(side="left", padx=4, pady=6)
            _hover_btn(b, SURFACE2, ACCENT)

    # ── Plugins & Skills tab ──

    def _build_plugins_tab(self):
        frame = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(frame, text="  Plugins & Skills  ")
        def parse_frontmatter(text):
            meta = {}
            if not text.startswith("---"):
                return meta
            parts = text.split("---", 2)
            if len(parts) < 3:
                return meta
            for line in parts[1].splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            return meta
        def load_data():
            plugins, skills, agents = [], [], []
            pf = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
            if pf.exists():
                try:
                    with open(pf) as f:
                        plugins = json.load(f)
                except Exception:
                    pass
            sd = Path.home() / ".claude" / "skills"
            if sd.is_dir():
                for d in sorted(sd.iterdir()):
                    sm = d / "SKILL.md"
                    if d.is_dir() and sm.exists():
                        try:
                            text = sm.read_text(encoding="utf-8", errors="ignore")
                            meta = parse_frontmatter(text)
                            skills.append({"name": meta.get("name", d.name), "description": meta.get("description", ""), "_text": text})
                        except Exception:
                            pass
            ad = Path.home() / ".claude" / "agents"
            if ad.is_dir():
                for af in sorted(ad.glob("*.md")):
                    try:
                        text = af.read_text(encoding="utf-8", errors="ignore")
                        meta = parse_frontmatter(text)
                        agents.append({"name": meta.get("name", af.stem), "description": meta.get("description", ""),
                                       "model": meta.get("model", "inherit"), "_text": text})
                    except Exception:
                        pass
            return plugins, skills, agents
        top = tk.Frame(frame, bg=SURFACE, pady=6, padx=10)
        top.pack(fill="x")
        stats_var = tk.StringVar(value="Loading...")
        tk.Label(top, textvariable=stats_var, bg=SURFACE, fg=CYAN, font=("Consolas", 10)).pack(side="left")
        paned = tk.PanedWindow(frame, orient="horizontal", bg=BG, sashwidth=4, sashrelief="flat")
        paned.pack(fill="both", expand=True, pady=(4, 0))
        left_pane = tk.Frame(paned, bg=BG)
        paned.add(left_pane, minsize=260)
        pl_canvas = tk.Canvas(left_pane, bg=BG, highlightthickness=0)
        pl_sb = ttk.Scrollbar(left_pane, orient="vertical", command=pl_canvas.yview)
        pl_canvas.configure(yscrollcommand=pl_sb.set)
        pl_sb.pack(side="right", fill="y")
        pl_canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(pl_canvas, bg=BG)
        pl_win = pl_canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: pl_canvas.configure(scrollregion=pl_canvas.bbox("all")))
        pl_canvas.bind("<Configure>", lambda e: pl_canvas.itemconfig(pl_win, width=e.width))
        right_pane = tk.Frame(paned, bg=SURFACE, padx=12, pady=12)
        paned.add(right_pane, minsize=220)
        detail_title = tk.Label(right_pane, text="Select an item", bg=SURFACE, fg=ACCENT,
                                font=("Consolas", 12, "bold"), anchor="w", wraplength=240)
        detail_title.pack(fill="x")
        detail_text = tk.Text(right_pane, bg=SURFACE2, fg=TEXT, relief="flat", font=("Consolas", 9),
                              wrap="word", state="disabled", bd=0, padx=6, pady=6)
        detail_text.pack(fill="both", expand=True, pady=(8, 0))
        def show_detail(name, body):
            detail_title.config(text=name)
            detail_text.config(state="normal")
            detail_text.delete("1.0", "end")
            detail_text.insert("end", body)
            detail_text.config(state="disabled")
        def make_card(parent, label, sub, color, click_cb=None):
            card = tk.Frame(parent, bg=SURFACE2, padx=10, pady=6, cursor="hand2" if click_cb else "")
            card.pack(fill="x", padx=8, pady=2)
            tk.Label(card, text=label, bg=SURFACE2, fg=color, font=("Consolas", 10, "bold"), anchor="w").pack(fill="x")
            if sub:
                tk.Label(card, text=sub, bg=SURFACE2, fg=TEXT_DIM, font=("Consolas", 9), anchor="w",
                         wraplength=220, justify="left").pack(fill="x")
            if click_cb:
                for w in (card, *card.winfo_children()):
                    w.bind("<Button-1>", lambda e, cb=click_cb: cb())
        def section_header(text):
            tk.Label(inner, text=text, bg=BG, fg=GOLD, font=("Consolas", 10, "bold"), anchor="w", padx=8).pack(fill="x", pady=(10, 2))
        def refresh():
            for w in inner.winfo_children():
                w.destroy()
            plugins, skills, agents = load_data()
            stats_var.set(f"{len(plugins)} plugins  |  {len(skills)} skills  |  {len(agents)} agents")
            section_header("Plugins")
            if plugins:
                for p in plugins:
                    name = p.get("name", "Unknown") if isinstance(p, dict) else str(p)
                    ver = p.get("version", "") if isinstance(p, dict) else ""
                    desc = p.get("description", "") if isinstance(p, dict) else ""
                    make_card(inner, f"{name}  {ver}".strip(), desc, PREVIEW)
            else:
                tk.Label(inner, text="  No plugins installed", bg=BG, fg=TEXT_MUTED, font=("Consolas", 9)).pack(anchor="w", padx=12)
            section_header("Skills")
            if skills:
                for s in skills:
                    make_card(inner, s["name"], s["description"], GREEN,
                              click_cb=lambda s=s: show_detail(s["name"], s["_text"]))
            else:
                tk.Label(inner, text="  No skills found", bg=BG, fg=TEXT_MUTED, font=("Consolas", 9)).pack(anchor="w", padx=12)
            section_header("Agents")
            if agents:
                for a in agents:
                    sub = f"{a['description']}  [model: {a['model']}]".strip(" []")
                    make_card(inner, a["name"], sub, CYAN,
                              click_cb=lambda a=a: show_detail(a["name"], a["_text"]))
            else:
                tk.Label(inner, text="  No agents found", bg=BG, fg=TEXT_MUTED, font=("Consolas", 9)).pack(anchor="w", padx=12)
        refresh_btn = tk.Button(top, text="\u27f3 Refresh", bg=SURFACE2, fg=TEXT, relief="flat", padx=8, command=refresh)
        refresh_btn.pack(side="right")
        _hover_btn(refresh_btn, SURFACE2, ACCENT)
        refresh()

    # ── Profiles tab ──

    def _build_profiles_tab(self):
        frame = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(frame, text="  Profiles  ")
        prof_canvas = tk.Canvas(frame, bg=BG, highlightthickness=0)
        prof_sb = ttk.Scrollbar(frame, orient="vertical", command=prof_canvas.yview)
        inner = tk.Frame(prof_canvas, bg=BG)
        inner.bind("<Configure>", lambda e: prof_canvas.configure(scrollregion=prof_canvas.bbox("all")))
        prof_win = prof_canvas.create_window((0, 0), window=inner, anchor="nw")
        prof_canvas.configure(yscrollcommand=prof_sb.set)
        prof_canvas.bind("<Configure>", lambda e: prof_canvas.itemconfig(prof_win, width=e.width))
        prof_canvas.pack(side="left", fill="both", expand=True)
        prof_sb.pack(side="right", fill="y")
        def section_label(parent, text):
            tk.Label(parent, text=text, bg=BG, fg=ACCENT, font=("Consolas", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
            tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(0, 6))
        section_label(inner, "Global Settings")
        gs = tk.Frame(inner, bg=SURFACE, bd=0, highlightbackground=BORDER, highlightthickness=1)
        gs.pack(fill="x", padx=12, pady=4)
        row1 = tk.Frame(gs, bg=SURFACE)
        row1.pack(fill="x", padx=10, pady=6)
        tk.Label(row1, text="Model:", bg=SURFACE, fg=TEXT_DIM, width=22, anchor="w").pack(side="left")
        model_var = tk.StringVar(value=self.config_data.get("default_model", "sonnet"))
        ttk.Combobox(row1, textvariable=model_var, values=["sonnet", "opus", "haiku", "sonnet[1m]", "opus[1m]"], state="readonly", width=16).pack(side="left")
        row2 = tk.Frame(gs, bg=SURFACE)
        row2.pack(fill="x", padx=10, pady=(0, 4))
        tk.Label(row2, text="Permission Mode:", bg=SURFACE, fg=TEXT_DIM, width=22, anchor="w").pack(side="left")
        perm_var = tk.StringVar(value=self.config_data.get("default_permission_mode", "default"))
        ttk.Combobox(row2, textvariable=perm_var, values=["plan", "default", "bypassPermissions"], state="readonly", width=16).pack(side="left")
        row3 = tk.Frame(gs, bg=SURFACE)
        row3.pack(fill="x", padx=10, pady=(0, 8))
        agent_teams_var = tk.BooleanVar(value=self.config_data.get("agent_teams", False))
        tk.Checkbutton(row3, text="Enable Agent Teams (experimental)", variable=agent_teams_var,
                       bg=SURFACE, fg=TEXT, selectcolor=ACCENT, activebackground=SURFACE,
                       activeforeground=TEXT, font=("Segoe UI", 10), highlightthickness=0, bd=0).pack(side="left")
        section_label(inner, "Quick Profiles")
        qf = tk.Frame(inner, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        qf.pack(fill="x", padx=12, pady=4)
        bf = tk.Frame(qf, bg=SURFACE)
        bf.pack(fill="x", padx=10, pady=8)
        for name, mdl, prm, color in [("Development", "sonnet", "bypassPermissions", GREEN),
                                       ("Review", "opus", "plan", GOLD), ("Quick", "haiku", "bypassPermissions", CYAN)]:
            btn = tk.Button(bf, text=name, bg=SURFACE2, fg=color, relief="flat", padx=10, cursor="hand2",
                            command=lambda m=mdl, p=prm: (model_var.set(m), perm_var.set(p)))
            btn.pack(side="left", padx=4)
            _hover_btn(btn, SURFACE2, BORDER)
        section_label(inner, "Per-Project Overrides")
        pp = tk.Frame(inner, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        pp.pack(fill="x", padx=12, pady=4)
        proj_names = []
        try:
            for p in get_projects():
                proj_names.append(p['decoded_path'].split('/')[-1].split('\\')[-1] or p['encoded_name'])
        except Exception:
            pass
        proj_var = tk.StringVar()
        ttk.Combobox(pp, textvariable=proj_var, values=proj_names, state="readonly", width=52).pack(padx=10, pady=(8, 4), anchor="w")
        ov_model_var = tk.StringVar()
        ov_perm_var = tk.StringVar()
        ov_flags_var = tk.StringVar()
        ov_frame = tk.Frame(pp, bg=SURFACE)
        ov_frame.pack(fill="x", padx=10, pady=4)
        for lbl, var, vals in [("Model override:", ov_model_var, ["", "sonnet", "opus", "haiku", "sonnet[1m]", "opus[1m]"]),
                               ("Perm override:", ov_perm_var, ["", "plan", "default", "bypassPermissions"])]:
            r = tk.Frame(ov_frame, bg=SURFACE)
            r.pack(fill="x", pady=1)
            tk.Label(r, text=lbl, bg=SURFACE, fg=TEXT_DIM, width=18, anchor="w").pack(side="left")
            ttk.Combobox(r, textvariable=var, values=vals, state="readonly", width=22).pack(side="left")
        rf = tk.Frame(ov_frame, bg=SURFACE)
        rf.pack(fill="x", pady=1)
        tk.Label(rf, text="Custom flags:", bg=SURFACE, fg=TEXT_DIM, width=18, anchor="w").pack(side="left")
        tk.Entry(rf, textvariable=ov_flags_var, bg=SURFACE2, fg=TEXT, insertbackground=TEXT, relief="flat", width=34).pack(side="left")
        def on_proj_select(event=None):
            key = proj_var.get()
            ov = self.config_data.get("project_overrides", {}).get(key, {})
            ov_model_var.set(ov.get("model", ""))
            ov_perm_var.set(ov.get("permission_mode", ""))
            ov_flags_var.set(ov.get("flags", ""))
        proj_var.trace_add("write", lambda *_: on_proj_select())
        sf = tk.Frame(inner, bg=BG)
        sf.pack(fill="x", padx=12, pady=10)
        def save_all():
            self.config_data["default_model"] = model_var.get()
            self.config_data["default_permission_mode"] = perm_var.get()
            self.config_data["agent_teams"] = agent_teams_var.get()
            key = proj_var.get()
            if key:
                self.config_data.setdefault("project_overrides", {})[key] = {
                    "model": ov_model_var.get(), "permission_mode": ov_perm_var.get(), "flags": ov_flags_var.get()}
            save_config(self.config_data)
        save_btn = tk.Button(sf, text="Save Profiles", bg=ACCENT, fg=TEXT, relief="flat", padx=14, pady=5,
                             cursor="hand2", command=save_all)
        save_btn.pack(side="right")
        _hover_btn(save_btn, ACCENT, "#6b47d6")

    # ── CLAUDE.md Editor tab ──

    def _build_claudemd_tab(self):
        frame = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(frame, text="  CLAUDE.md  ")
        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(top, text="CLAUDE.md Editor", bg=BG, fg=ACCENT, font=("Segoe UI", 13, "bold")).pack(side="left")
        self._claudemd_status = tk.Label(top, text="", bg=BG, fg=GOLD, font=("Segoe UI", 9))
        self._claudemd_status.pack(side="left", padx=10)
        save_btn = tk.Button(top, text="Save", bg=ACCENT, fg=TEXT, relief="flat", padx=12, pady=3,
                             font=("Segoe UI", 9, "bold"), cursor="hand2", command=self._claudemd_save)
        save_btn.pack(side="right")
        _hover_btn(save_btn, ACCENT, GREEN)
        sel_row = tk.Frame(frame, bg=BG)
        sel_row.pack(fill="x", padx=12, pady=(8, 4))
        tk.Label(sel_row, text="Scope:", bg=BG, fg=TEXT_DIM, font=("Segoe UI", 9)).pack(side="left")
        self._claudemd_scope = tk.StringVar(value="global")
        scopes = ["global"]
        try:
            for p in get_projects():
                name = p['decoded_path'].split('/')[-1].split('\\')[-1] or p['encoded_name']
                scopes.append(name)
        except Exception:
            pass
        self._claudemd_scope_map = {}
        self._claudemd_scope_map["global"] = Path.home() / "CLAUDE.md"
        try:
            for p in get_projects():
                name = p['decoded_path'].split('/')[-1].split('\\')[-1] or p['encoded_name']
                self._claudemd_scope_map[name] = Path(p['decoded_path']) / "CLAUDE.md"
        except Exception:
            pass
        scope_combo = ttk.Combobox(sel_row, textvariable=self._claudemd_scope,
                                   values=scopes, state="readonly", width=40)
        scope_combo.pack(side="left", padx=8)
        self._claudemd_scope.trace_add("write", lambda *_: self._claudemd_load())
        self._claudemd_editor = tk.Text(frame, bg=SURFACE, fg=TEXT, font=("Consolas", 10),
                                         relief="flat", bd=0, insertbackground=TEXT,
                                         wrap="word", undo=True, padx=10, pady=8)
        self._claudemd_editor.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self._claudemd_editor.bind("<Key>", lambda _: self._claudemd_status.configure(
            text="\u25cf unsaved changes", fg=GOLD))
        self._claudemd_load()

    def _claudemd_load(self):
        scope = self._claudemd_scope.get()
        path = self._claudemd_scope_map.get(scope)
        self._claudemd_editor.delete("1.0", tk.END)
        self._claudemd_status.configure(text="", fg=GOLD)
        if path and path.exists():
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                self._claudemd_editor.insert("1.0", content)
                self._claudemd_status.configure(text=f"Loaded: {path}", fg=TEXT_MUTED)
            except OSError:
                self._claudemd_editor.insert("1.0", "# Error reading file")
        else:
            self._claudemd_editor.insert("1.0", f"# CLAUDE.md\n\n<!-- File will be created at {path} -->\n")
            self._claudemd_status.configure(text="New file (will be created on save)", fg=CYAN)

    def _claudemd_save(self):
        scope = self._claudemd_scope.get()
        path = self._claudemd_scope_map.get(scope)
        if not path:
            return
        content = self._claudemd_editor.get("1.0", tk.END).rstrip()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content + "\n", encoding="utf-8")
            self._claudemd_status.configure(text="Saved", fg=GREEN)
            self._claudemd_status.after(3000, lambda: self._claudemd_status.configure(text="", fg=GOLD))
        except OSError as e:
            self._claudemd_status.configure(text=f"Error: {e}", fg=RED)

    # ── Launch helpers ──

    def _resolve_launch_opts(self, encoded_name=""):
        ov = self.config_data.get("project_overrides", {}).get(encoded_name, {}) if encoded_name else {}
        model = ov.get("model", "") or self.config_data.get("default_model", "")
        agent_teams = self.config_data.get("agent_teams", False)
        return model, agent_teams

    # ── Event handlers ──

    def _on_remote_control_toggle(self, *_a):
        self.config_data["remote_control"] = self.remote_control.get()
        save_config(self.config_data)

    def _on_autostart_toggle(self, *_a):
        self.config_data["auto_start"] = self.auto_start.get()
        save_config(self.config_data)
        toggle_autostart(self.auto_start.get())

    def _on_compact_toggle(self, *_a):
        self.config_data["compact"] = self.compact_mode.get()
        save_config(self.config_data)
        self.after(50, self._rerender_projects)

    def _on_search_change(self, *_a):
        if not self._search_placeholder_on:
            self._rerender_projects()

    def _on_sort_change(self, *_a):
        self.config_data["sort"] = self.sort_var.get()
        save_config(self.config_data)
        self._rerender_projects()

    def _toggle_pin(self, encoded_name: str):
        pinned = list(self.config_data.get("pinned", []))
        if encoded_name in pinned:
            pinned.remove(encoded_name)
        else:
            pinned.insert(0, encoded_name)
        self.config_data["pinned"] = pinned
        save_config(self.config_data)
        self._rerender_projects()

    def _move_pinned(self, encoded_name, direction):
        pinned = list(self.config_data.get("pinned", []))
        if encoded_name not in pinned:
            return
        idx = pinned.index(encoded_name)
        new_idx = idx + direction
        if 0 <= new_idx < len(pinned):
            pinned[idx], pinned[new_idx] = pinned[new_idx], pinned[idx]
            self.config_data["pinned"] = pinned
            save_config(self.config_data)
            self._rerender_projects()

    def _hide_project(self, encoded_name: str):
        hidden = self.config_data.get("hidden", [])
        if encoded_name not in hidden:
            hidden.append(encoded_name)
        self.config_data["hidden"] = hidden
        save_config(self.config_data)
        self._rerender_projects()

    def _unhide_project(self, encoded_name: str):
        hidden = self.config_data.get("hidden", [])
        if encoded_name in hidden:
            hidden.remove(encoded_name)
        self.config_data["hidden"] = hidden
        save_config(self.config_data)
        self._rerender_projects()

    def _show_hidden_projects(self):
        hidden = self.config_data.get("hidden", [])
        if not hidden:
            return
        popup = tk.Toplevel(self)
        popup.title("Hidden Projects")
        popup.geometry("500x400")
        popup.configure(bg=BG)
        popup.transient(self)
        popup.bind("<Escape>", lambda e: popup.destroy())
        popup.update_idletasks()
        popup.geometry(f"+{self.winfo_x() + 160}+{self.winfo_y() + 100}")
        tk.Label(popup, text="Hidden Projects", bg=BG, fg=TEXT,
                 font=("Segoe UI", 13, "bold")).pack(pady=(12, 8))
        for name in list(hidden):
            row = tk.Frame(popup, bg=SURFACE)
            row.pack(fill="x", padx=12, pady=2)
            tk.Label(row, text=name[:40], bg=SURFACE, fg=TEXT_DIM,
                     font=("Consolas", 9)).pack(side="left", padx=8, pady=6, fill="x", expand=True)
            def _do(n=name, p=popup):
                self._unhide_project(n)
                p.destroy()
                if self.config_data.get("hidden"):
                    self._show_hidden_projects()
            tk.Button(row, text="Unhide", bg=ACCENT, fg=TEXT, font=("Segoe UI", 8),
                      relief="flat", padx=8, pady=2, cursor="hand2", command=_do).pack(side="right", padx=8, pady=4)
        btn_row = tk.Frame(popup, bg=BG)
        btn_row.pack(pady=10)
        tk.Button(btn_row, text="Unhide All", bg=SURFACE2, fg=TEXT_DIM, font=("Segoe UI", 9),
                  relief="flat", padx=14, pady=4, cursor="hand2",
                  command=lambda: self._unhide_all(popup)).pack(side="left", padx=4)
        tk.Button(btn_row, text="Delete Hidden Data", bg=RED, fg=TEXT, font=("Segoe UI", 9),
                  relief="flat", padx=14, pady=4, cursor="hand2",
                  command=lambda: self._delete_hidden_data(popup)).pack(side="left", padx=4)
        tk.Button(btn_row, text="Close", bg=SURFACE2, fg=TEXT_DIM, font=("Segoe UI", 9),
                  relief="flat", padx=14, pady=4, cursor="hand2", command=popup.destroy).pack(side="left", padx=4)

    def _unhide_all(self, popup):
        self.config_data["hidden"] = []
        save_config(self.config_data)
        popup.destroy()
        self._rerender_projects()
        self._show_toast("All projects unhidden")

    def _delete_hidden_data(self, popup):
        hidden = self.config_data.get("hidden", [])
        if not hidden:
            return
        if not messagebox.askyesno("Delete Hidden Data",
                f"Permanently delete session data for {len(hidden)} hidden project(s)?\n\n"
                "This removes JSONL files from ~/.claude/projects/.\n"
                "This cannot be undone.", icon="warning"):
            return
        deleted = 0
        for name in list(hidden):
            proj_dir = PROJECTS_DIR / name
            if proj_dir.exists():
                try:
                    shutil.rmtree(proj_dir)
                    deleted += 1
                except OSError:
                    pass
        self.config_data["hidden"] = []
        save_config(self.config_data)
        popup.destroy()
        self._refresh_projects()
        self._show_toast(f"Deleted {deleted} project(s)")

    # ── Clipboard operations ──

    def _copy_to_clipboard(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self._show_toast(f"Copied: {text[:40]}...")

    # ── Session deletion ──

    def _delete_session(self, session_file: Path, encoded_name: str):
        name = session_file.stem[:12]
        if not messagebox.askyesno("Delete Session",
                f"Delete session {name}...?\n\nThis removes the JSONL file permanently.",
                icon="warning"):
            return
        try:
            session_file.unlink()
            self._show_toast(f"Session {name}... deleted")
            self._refresh_projects()
        except OSError as e:
            self._show_toast(f"Error: {e}")

    # ── Toast notification ──

    def _show_toast(self, message, duration=3000):
        toast = tk.Label(self, text=f"  {message}  ", bg=ACCENT, fg=TEXT,
                         font=("Segoe UI", 10), padx=16, pady=6)
        toast.place(relx=0.5, rely=0.95, anchor="s")
        self.after(duration, toast.destroy)

    # ── Export/Import config ──

    def _export_config(self):
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                             filetypes=[("JSON", "*.json")],
                                             initialfile="launcher-config.json")
        if path:
            try:
                with open(path, 'w') as f:
                    json.dump(self.config_data, f, indent=2)
                self._show_toast(f"Config exported to {Path(path).name}")
            except OSError:
                pass

    def _import_config(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if path:
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                self.config_data.update(data)
                save_config(self.config_data)
                self._show_toast("Config imported!")
                self._refresh_projects()
            except (OSError, json.JSONDecodeError):
                pass

    # ── Custom flags ──

    def _set_custom_flags(self, encoded_name):
        flags = self.config_data.get("custom_flags", {}).get(encoded_name, "")
        popup = tk.Toplevel(self)
        popup.title("Custom Launch Flags")
        popup.geometry("420x150")
        popup.configure(bg=BG)
        popup.transient(self)
        popup.bind("<Escape>", lambda e: popup.destroy())
        popup.update_idletasks()
        popup.geometry(f"+{self.winfo_x() + 200}+{self.winfo_y() + 200}")
        tk.Label(popup, text="Additional claude arguments:", bg=BG, fg=TEXT,
                 font=("Segoe UI", 10)).pack(pady=(12, 4), padx=16, anchor="w")
        entry = tk.Entry(popup, bg=SURFACE2, fg=TEXT, font=("Consolas", 10),
                         insertbackground=TEXT, relief="flat")
        entry.pack(fill="x", padx=16, pady=4)
        entry.insert(0, flags)
        entry.focus_set()
        tk.Label(popup, text="e.g. --verbose --model sonnet", bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8)).pack(padx=16, anchor="w")
        def _save():
            val = entry.get().strip()
            cf = self.config_data.get("custom_flags", {})
            if val:
                cf[encoded_name] = val
            elif encoded_name in cf:
                del cf[encoded_name]
            self.config_data["custom_flags"] = cf
            save_config(self.config_data)
            popup.destroy()
            self._show_toast("Custom flags saved")
        entry.bind("<Return>", lambda e: _save())
        btn_row = tk.Frame(popup, bg=BG)
        btn_row.pack(pady=8)
        tk.Button(btn_row, text="Save", bg=ACCENT, fg=TEXT, font=("Segoe UI", 9),
                  relief="flat", padx=14, pady=4, cursor="hand2", command=_save).pack(side="left", padx=4)
        tk.Button(btn_row, text="Cancel", bg=SURFACE2, fg=TEXT_DIM, font=("Segoe UI", 9),
                  relief="flat", padx=14, pady=4, cursor="hand2", command=popup.destroy).pack(side="left", padx=4)

    # ── Session notes ──

    def _edit_session_note(self, session_id):
        notes = self.config_data.get("notes", {})
        note = notes.get(session_id, "")
        popup = tk.Toplevel(self)
        popup.title("Session Note")
        popup.geometry("420x140")
        popup.configure(bg=BG)
        popup.transient(self)
        popup.bind("<Escape>", lambda e: popup.destroy())
        popup.update_idletasks()
        popup.geometry(f"+{self.winfo_x() + 200}+{self.winfo_y() + 200}")
        tk.Label(popup, text=f"Note for {session_id[:12]}...:", bg=BG, fg=TEXT,
                 font=("Segoe UI", 10)).pack(pady=(12, 4), padx=16, anchor="w")
        entry = tk.Entry(popup, bg=SURFACE2, fg=TEXT, font=("Segoe UI", 10),
                         insertbackground=TEXT, relief="flat")
        entry.pack(fill="x", padx=16, pady=4)
        entry.insert(0, note)
        entry.focus_set()
        def _save():
            val = entry.get().strip()
            n = self.config_data.get("notes", {})
            if val:
                n[session_id] = val
            elif session_id in n:
                del n[session_id]
            self.config_data["notes"] = n
            save_config(self.config_data)
            popup.destroy()
            self._show_toast("Note saved")
        entry.bind("<Return>", lambda e: _save())
        btn_row = tk.Frame(popup, bg=BG)
        btn_row.pack(pady=8)
        tk.Button(btn_row, text="Save", bg=ACCENT, fg=TEXT, font=("Segoe UI", 9),
                  relief="flat", padx=14, pady=4, cursor="hand2", command=_save).pack(side="left", padx=4)
        tk.Button(btn_row, text="Cancel", bg=SURFACE2, fg=TEXT_DIM, font=("Segoe UI", 9),
                  relief="flat", padx=14, pady=4, cursor="hand2", command=popup.destroy).pack(side="left", padx=4)

    # ── Command palette (Ctrl+P) ──

    def _show_command_palette(self):
        if not hasattr(self, '_cached_projects'):
            return
        palette = tk.Toplevel(self)
        palette.overrideredirect(True)
        palette.configure(bg=BORDER)
        w, h = 500, 340
        palette.geometry(f"{w}x{h}+{self.winfo_x() + (self.winfo_width() - w) // 2}+"
                         f"{self.winfo_y() + 80}")
        palette.grab_set()

        inner = tk.Frame(palette, bg=BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        search = tk.Entry(inner, bg=SURFACE2, fg=TEXT, font=("Consolas", 12),
                          insertbackground=TEXT, relief="flat")
        search.pack(fill="x", padx=8, pady=(8, 4))
        search.focus_set()

        listbox = tk.Listbox(inner, bg=BG, fg=TEXT, font=("Segoe UI", 10),
                             selectbackground=ACCENT, selectforeground=TEXT,
                             highlightthickness=0, bd=0, activestyle="none")
        listbox.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        hidden = set(self.config_data.get("hidden", []))
        all_projects = [p for p in self._cached_projects if p['encoded_name'] not in hidden]

        def _refresh_list(*_a):
            q = search.get().lower().strip()
            listbox.delete(0, tk.END)
            for p in all_projects:
                name = Path(p['decoded_path']).name
                if not q or q in name.lower() or q in p['decoded_path'].lower():
                    emoji = _project_emoji(name)
                    t = _relative_time(p['last_active'])
                    listbox.insert(tk.END, f" {emoji}  {name}   {t}")
            if listbox.size() > 0:
                listbox.selection_set(0)

        def _launch_selected(*_a):
            sel = listbox.curselection()
            if not sel:
                palette.destroy()
                return
            q = search.get().lower().strip()
            filtered = [p for p in all_projects
                        if not q or q in Path(p['decoded_path']).name.lower()
                        or q in p['decoded_path'].lower()]
            if sel[0] < len(filtered):
                proj = filtered[sel[0]]
                flags = self.config_data.get("custom_flags", {}).get(proj['encoded_name'], "")
                mdl, teams = self._resolve_launch_opts(proj['encoded_name'])
                launch_session(proj['decoded_path'], self.skip_perms.get(), self.mode.get(),
                               extra_flags=flags, remote_control=self.remote_control.get(),
                               model=mdl, agent_teams=teams)
                self._record_launch(proj['encoded_name'])
                self._flash_card(proj['encoded_name'])
            palette.destroy()

        def _on_key(e):
            if e.keysym == 'Down':
                idx = listbox.curselection()
                if idx and idx[0] < listbox.size() - 1:
                    listbox.selection_clear(0, tk.END)
                    listbox.selection_set(idx[0] + 1)
                    listbox.see(idx[0] + 1)
            elif e.keysym == 'Up':
                idx = listbox.curselection()
                if idx and idx[0] > 0:
                    listbox.selection_clear(0, tk.END)
                    listbox.selection_set(idx[0] - 1)
                    listbox.see(idx[0] - 1)

        search.bind("<KeyRelease>", _refresh_list)
        search.bind("<Return>", _launch_selected)
        search.bind("<Down>", _on_key)
        search.bind("<Up>", _on_key)
        palette.bind("<Escape>", lambda e: palette.destroy())
        palette.bind("<FocusOut>", lambda e: palette.after(100, palette.destroy))
        listbox.bind("<Double-Button-1>", _launch_selected)
        _refresh_list()

    # ── Session search (Ctrl+G) ──

    def _show_session_search(self):
        popup = tk.Toplevel(self)
        popup.title("Search Sessions")
        popup.geometry("700x480")
        popup.configure(bg=BG)
        popup.transient(self)
        popup.bind("<Escape>", lambda e: popup.destroy())
        popup.update_idletasks()
        popup.geometry(f"+{self.winfo_x() + 60}+{self.winfo_y() + 60}")

        sf = tk.Frame(popup, bg=BG)
        sf.pack(fill="x", padx=12, pady=(12, 4))
        tk.Label(sf, text="\U0001f50e", bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 11)).pack(side="left")
        entry = tk.Entry(sf, bg=SURFACE2, fg=TEXT, font=("Consolas", 11),
                         insertbackground=TEXT, relief="flat")
        entry.pack(side="left", fill="x", expand=True, padx=8, pady=4)
        entry.focus_set()

        results_frame = tk.Frame(popup, bg=BG)
        results_frame.pack(fill="both", expand=True, padx=12, pady=4)
        results_list = tk.Listbox(results_frame, bg=SURFACE, fg=TEXT, font=("Consolas", 9),
                                  selectbackground=ACCENT, selectforeground=TEXT,
                                  highlightthickness=0, bd=0, activestyle="none")
        results_list.pack(fill="both", expand=True)

        status = tk.Label(popup, text="Type 3+ chars to search across all sessions...",
                          bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 8))
        status.pack(pady=(0, 8))

        _results_data = []
        _search_gen = [0]
        _search_timer = [None]

        def _schedule_search(*_a):
            if _search_timer[0]:
                popup.after_cancel(_search_timer[0])
            _search_timer[0] = popup.after(400, _do_search)

        def _do_search():
            query = entry.get().strip()
            if len(query) < 3:
                results_list.delete(0, tk.END)
                _results_data.clear()
                status.config(text="Type 3+ chars to search...")
                return
            gen = _search_gen[0] + 1
            _search_gen[0] = gen
            results_list.delete(0, tk.END)
            _results_data.clear()
            status.config(text="Searching...")

            def _search():
                matches = []
                q = query.lower()
                try:
                    for d in PROJECTS_DIR.iterdir():
                        if not d.is_dir() or d.name == 'memory':
                            continue
                        if _search_gen[0] != gen:
                            return
                        proj_name = get_real_path(d, d.name)
                        for jf in d.glob("*.jsonl"):
                            try:
                                with open(jf, 'rb') as f:
                                    raw = f.read().decode('utf-8', errors='ignore')
                                for line in raw.split('\n'):
                                    if q not in line.lower():
                                        continue
                                    try:
                                        ld = json.loads(line)
                                        msg = ld.get('message', {})
                                        content = msg.get('content', '')
                                        text = ""
                                        if isinstance(content, list):
                                            for block in content:
                                                if isinstance(block, dict) and block.get('type') == 'text':
                                                    text = block.get('text', '')
                                                    break
                                        elif isinstance(content, str):
                                            text = content
                                        if text and q in text.lower():
                                            idx = text.lower().index(q)
                                            start = max(0, idx - 30)
                                            end = min(len(text), idx + len(query) + 30)
                                            snippet = text[start:end].replace('\n', ' ').strip()
                                            matches.append({
                                                'project': Path(proj_name).name,
                                                'session_id': jf.stem,
                                                'snippet': snippet,
                                                'encoded_name': d.name,
                                            })
                                            if len(matches) >= 50:
                                                break
                                    except (json.JSONDecodeError, KeyError):
                                        continue
                            except OSError:
                                continue
                            if len(matches) >= 50:
                                break
                        if len(matches) >= 50:
                            break
                except Exception:
                    pass

                if _search_gen[0] != gen:
                    return
                def _update():
                    _results_data.clear()
                    _results_data.extend(matches)
                    results_list.delete(0, tk.END)
                    for m in matches:
                        results_list.insert(tk.END,
                            f"  {_project_emoji(m['project'])} {m['project']}  |  ...{m['snippet'][:60]}...")
                    status.config(text=f"{len(matches)} result{'s' if len(matches) != 1 else ''}")
                try:
                    popup.after(0, _update)
                except Exception:
                    pass
            threading.Thread(target=_search, daemon=True).start()

        def _on_dbl(e):
            sel = results_list.curselection()
            if sel and sel[0] < len(_results_data):
                m = _results_data[sel[0]]
                for p in getattr(self, '_cached_projects', []):
                    if p['encoded_name'] == m['encoded_name']:
                        sl = p['sessions'][:15]
                        for i, s in enumerate(sl):
                            if s['id'] == m['session_id']:
                                popup.destroy()
                                self._show_preview_popup(p, None, sl, i)
                                return
                        break

        entry.bind("<KeyRelease>", _schedule_search)
        results_list.bind("<Double-Button-1>", _on_dbl)

    # ── Launch history ──

    def _record_launch(self, encoded_name):
        hist = self.config_data.get("launch_history", {})
        launches = hist.get(encoded_name, [])
        launches.append(datetime.now().isoformat())
        launches = launches[-20:]
        hist[encoded_name] = launches
        self.config_data["launch_history"] = hist
        save_config(self.config_data)

    def _get_launch_count(self, encoded_name):
        return len(self.config_data.get("launch_history", {}).get(encoded_name, []))

    # ── Last seen / notification dot ──

    def _update_last_seen(self):
        """Record current time so we can detect new activity later."""
        ls = self.config_data.get("last_seen", {})
        ls["_app_opened"] = datetime.now().timestamp()
        self.config_data["last_seen"] = ls
        save_config(self.config_data)

    def _is_new_activity(self, encoded_name, last_active):
        """Check if project has new activity since last app open."""
        if last_active is None:
            return False
        ls = self.config_data.get("last_seen", {})
        last_opened = ls.get("_app_opened", 0)
        prev_seen = ls.get(encoded_name, last_opened)
        return last_active.timestamp() > prev_seen

    def _mark_seen(self, encoded_name, last_active):
        if last_active:
            ls = self.config_data.get("last_seen", {})
            ls[encoded_name] = last_active.timestamp()
            self.config_data["last_seen"] = ls

    # ── Bulk operations ──

    def _update_bulk_count(self, *_a):
        count = sum(1 for v in self.project_checks.values()
                    if v.get() and os.path.isdir(self.project_data.get(
                        self._key_for_var(v), {}).get('decoded_path', '')))
        self.bulk_launch_btn.config(text=f"  Launch Selected ({count})  ")
        if count > 0:
            self.bulk_launch_btn.config(bg=GREEN, state="normal", cursor="hand2")
            _hover_btn(self.bulk_launch_btn, GREEN, GREEN_HOVER)
        else:
            self.bulk_launch_btn.config(bg="#4a4a4a", state="disabled", cursor="arrow")

    def _key_for_var(self, var):
        for k, v in self.project_checks.items():
            if v is var:
                return k
        return ""

    def _select_all(self):
        for key, var in self.project_checks.items():
            if os.path.isdir(self.project_data.get(key, {}).get('decoded_path', '')):
                var.set(True)
        self._update_bulk_count()

    def _select_none(self):
        for var in self.project_checks.values():
            var.set(False)
        self._update_bulk_count()

    def _shift_select(self, key):
        if not self._last_checked_key or self._last_checked_key not in self._card_order:
            return
        if key not in self._card_order:
            return
        a = self._card_order.index(self._last_checked_key)
        b = self._card_order.index(key)
        for k in self._card_order[min(a, b):max(a, b) + 1]:
            if k in self.project_checks and os.path.isdir(self.project_data.get(k, {}).get('decoded_path', '')):
                self.project_checks[k].set(True)

    def _launch_selected(self):
        mode = self.mode.get()
        skip = self.skip_perms.get()
        to_launch = []
        for key, var in self.project_checks.items():
            if not var.get():
                continue
            proj = self.project_data.get(key)
            if not proj or not os.path.isdir(proj['decoded_path']):
                continue
            session_id = None
            if mode == "resume":
                dd = self.project_dropdowns.get(key)
                sids = [s['id'] for s in proj['sessions'][:15]]
                if dd and sids:
                    idx = dd.current() if dd.current() >= 0 else 0
                    session_id = sids[idx]
            flags = self.config_data.get("custom_flags", {}).get(key, "")
            to_launch.append((key, proj, session_id, flags))

        if not to_launch:
            return

        # Confirm if launching 3+
        if len(to_launch) >= 3:
            names = "\n".join(f"  \u2022 {Path(t[1]['decoded_path']).name}" for t in to_launch[:10])
            if len(to_launch) > 10:
                names += f"\n  ... and {len(to_launch) - 10} more"
            if not messagebox.askyesno("Bulk Launch",
                    f"Launch {len(to_launch)} sessions?\n\n{names}\n\n"
                    f"Sessions will be staggered by 2 seconds."):
                return

        # Launch with stagger for 3+
        for i, (key, proj, session_id, flags) in enumerate(to_launch):
            delay = i * 2000 if len(to_launch) >= 3 else 0
            def _do(p=proj, s=session_id, f=flags, k=key):
                mdl, teams = self._resolve_launch_opts(k)
                launch_session(p['decoded_path'], skip, mode, s, extra_flags=f,
                               remote_control=self.remote_control.get(),
                               model=mdl, agent_teams=teams)
                self._record_launch(k)
                self._flash_card(k)
            if delay:
                self.after(delay, _do)
            else:
                _do()

    def _flash_card(self, key):
        if key in self._card_frames:
            stripe = self._card_frames[key]
            orig = stripe.cget('bg')
            stripe.config(bg=GREEN)
            self.after(600, lambda s=stripe, c=orig: s.config(bg=c))

    # ── Project loading (threaded) ──

    def _refresh_projects(self):
        if self._loading:
            return
        self._loading = True
        self.loading_label.pack(pady=20)
        def _load():
            _path_cache.clear()
            projects = get_projects()
            self.after(0, lambda: self._on_projects_loaded(projects))
        threading.Thread(target=_load, daemon=True).start()

    def _on_projects_loaded(self, projects):
        self._cached_projects = projects
        self._loading = False
        self._last_refresh = datetime.now()
        self.loading_label.pack_forget()
        self._render_project_list()
        self._update_status_bar()

    def _rerender_projects(self):
        if not hasattr(self, '_cached_projects') or not self._cached_projects:
            self._refresh_projects()
            return
        pinned = set(self.config_data.get("pinned", []))
        for proj in self._cached_projects:
            proj['pinned'] = proj['encoded_name'] in pinned
        self._sort_projects()
        self._render_project_list()

    def _sort_projects(self):
        pinned_order = {n: i for i, n in enumerate(self.config_data.get("pinned", []))}
        sort = self.sort_var.get()
        if sort == "name":
            self._cached_projects.sort(key=lambda p: (
                0 if p['pinned'] else 1,
                pinned_order.get(p['encoded_name'], 999) if p['pinned'] else 0,
                Path(p['decoded_path']).name.lower()))
        elif sort == "sessions":
            self._cached_projects.sort(key=lambda p: (
                0 if p['pinned'] else 1,
                pinned_order.get(p['encoded_name'], 999) if p['pinned'] else 0,
                -len(p['sessions'])))
        else:
            self._cached_projects.sort(key=lambda p: (
                0 if p['pinned'] else 1,
                pinned_order.get(p['encoded_name'], 999) if p['pinned'] else 0,
                -(p['last_active'].timestamp() if p['last_active'] else 0)))

    def _get_filtered_projects(self):
        query = "" if self._search_placeholder_on else self.search_var.get().lower().strip()
        hidden = set(self.config_data.get("hidden", []))
        projects = [p for p in self._cached_projects if p['encoded_name'] not in hidden]
        if query:
            projects = [p for p in projects
                        if query in p['decoded_path'].lower()
                        or query in Path(p['decoded_path']).name.lower()
                        or query in p['encoded_name'].lower()]
        return projects

    def _render_project_list(self):
        for w in self.scrollable.winfo_children():
            w.destroy()
        self.project_checks.clear()
        self.project_data.clear()
        self.project_dropdowns.clear()
        self._card_frames.clear()
        self._card_order.clear()
        self._card_widgets.clear()
        self._focused_idx = -1

        if not hasattr(self, '_cached_projects') or not self._cached_projects:
            tk.Label(self.scrollable, text="No projects found in ~/.claude/projects/",
                     bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 12)).pack(pady=40)
            self._update_project_count(0, 0)
            return

        filtered = self._get_filtered_projects()
        total = len([p for p in self._cached_projects
                     if p['encoded_name'] not in set(self.config_data.get("hidden", []))])

        if not filtered:
            msg = "No projects match your search" if not self._search_placeholder_on and self.search_var.get() else "All projects are hidden"
            tk.Label(self.scrollable, text=msg, bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 11)).pack(pady=30)
            hidden = self.config_data.get("hidden", [])
            if hidden:
                tk.Button(self.scrollable, text=f"Show {len(hidden)} hidden projects",
                          bg=SURFACE2, fg=TEXT_DIM, font=("Segoe UI", 9),
                          relief="flat", padx=12, pady=6, cursor="hand2",
                          command=self._show_hidden_projects).pack()
            self._update_project_count(0, total)
            return

        for proj in filtered:
            self._add_project_card(proj)

        hidden = self.config_data.get("hidden", [])
        if hidden:
            fr = tk.Frame(self.scrollable, bg=BG)
            fr.pack(fill="x", pady=(8, 0))
            tk.Button(fr, text=f"{len(hidden)} hidden \u2014 manage", bg=BG, fg=TEXT_MUTED,
                      font=("Segoe UI", 8), relief="flat", cursor="hand2", bd=0,
                      activeforeground=ACCENT, command=self._show_hidden_projects).pack()

        self._update_project_count(len(filtered), total)
        self._update_bulk_count()

        # Save last_seen for all visible projects
        for proj in filtered:
            self._mark_seen(proj['encoded_name'], proj['last_active'])
        save_config(self.config_data)

    def _update_project_count(self, shown, total):
        if shown == total:
            self.project_count_label.config(text=f"{total} project{'s' if total != 1 else ''}")
        else:
            self.project_count_label.config(text=f"{shown} of {total} projects")

    def _update_status_bar(self):
        if not hasattr(self, '_cached_projects'):
            return
        total_p = len(self._cached_projects)
        total_s = sum(len(p['sessions']) for p in self._cached_projects)
        total_sz = sum(s['size'] for p in self._cached_projects for s in p['sessions'])
        if total_sz < 1_000_000:
            sz = f"{total_sz // 1024}KB"
        else:
            sz = f"{total_sz / 1_000_000:.1f}MB"
        tok = _token_estimate(total_sz)
        self.status_label.config(
            text=f"  {total_p} projects \u00b7 {total_s} sessions \u00b7 {sz} (~{tok})")
        ref = _relative_time(self._last_refresh) if self._last_refresh else ""
        shortcuts = "\u2191\u2193 navigate \u00b7 Space select \u00b7 Ctrl+P palette \u00b7 Ctrl+G search"
        self.status_right.config(text=f"{shortcuts}  \u00b7  {ref}  ")

    def _add_project_card(self, proj):
        key = proj['encoded_name']
        path_exists = os.path.isdir(proj['decoded_path'])
        is_pinned = proj.get('pinned', False)
        compact = self.compact_mode.get()
        has_new = self._is_new_activity(key, proj['last_active'])
        launch_count = self._get_launch_count(key)
        self.project_data[key] = proj
        self._card_order.append(key)

        card_outer = tk.Frame(self.scrollable, bg=BG, highlightthickness=0)
        card_outer.pack(fill="x", pady=2 if compact else 3)
        self._card_widgets[key] = card_outer
        stripe_color = GOLD if is_pinned else (ACCENT if path_exists else RED)
        stripe = tk.Frame(card_outer, bg=stripe_color, width=4)
        stripe.pack(side="left", fill="y")
        stripe.pack_propagate(False)
        self._card_frames[key] = stripe

        card = tk.Frame(card_outer, bg=SURFACE)
        card.pack(side="left", fill="x", expand=True, ipady=4 if compact else 8)

        # Double-click to launch
        def _dbl(e, p=proj):
            if path_exists:
                flags = self.config_data.get("custom_flags", {}).get(p['encoded_name'], "")
                mdl, teams = self._resolve_launch_opts(p['encoded_name'])
                launch_session(p['decoded_path'], self.skip_perms.get(), self.mode.get(), extra_flags=flags,
                               remote_control=self.remote_control.get(), model=mdl, agent_teams=teams)
                self._record_launch(p['encoded_name'])
                self._flash_card(p['encoded_name'])
        card.bind("<Double-Button-1>", _dbl)

        # Right-click context menu
        def _ctx(e, p=proj):
            menu = tk.Menu(self, tearoff=0, bg=SURFACE, fg=TEXT,
                          activebackground=ACCENT, activeforeground=TEXT, font=("Segoe UI", 9))
            if path_exists:
                menu.add_command(label="Launch", command=lambda: (_dbl(None, p)))
            pin_label = "Unpin" if is_pinned else "Pin to top"
            menu.add_command(label=pin_label, command=lambda: self._toggle_pin(p['encoded_name']))
            if is_pinned:
                menu.add_command(label="\u2191 Move up", command=lambda: self._move_pinned(p['encoded_name'], -1))
                menu.add_command(label="\u2193 Move down", command=lambda: self._move_pinned(p['encoded_name'], 1))
            menu.add_separator()
            menu.add_command(label="Custom flags...", command=lambda: self._set_custom_flags(p['encoded_name']))
            if p['sessions']:
                menu.add_command(label="Add note...", command=lambda: self._edit_session_note(p['sessions'][0]['id']))
            menu.add_separator()
            # Clipboard operations
            menu.add_command(label="Copy path", command=lambda: self._copy_to_clipboard(p['decoded_path']))
            if p['sessions']:
                menu.add_command(label="Copy session ID",
                                command=lambda: self._copy_to_clipboard(p['sessions'][0]['id']))
            menu.add_separator()
            if path_exists:
                menu.add_command(label="Open in editor",
                                command=lambda: subprocess.Popen(["code", p['decoded_path']],
                                                                  **({'creationflags': 0x08000000} if IS_WINDOWS else {})))
                if IS_WINDOWS:
                    menu.add_command(label="Open folder", command=lambda: subprocess.Popen(["explorer", p['decoded_path']]))
                elif IS_MAC:
                    menu.add_command(label="Open folder", command=lambda: subprocess.Popen(["open", p['decoded_path']]))
                else:
                    menu.add_command(label="Open folder", command=lambda: subprocess.Popen(["xdg-open", p['decoded_path']]))
            menu.add_separator()
            if p['sessions']:
                menu.add_command(label="Delete latest session...",
                                command=lambda: self._delete_session(p['sessions'][0]['file'], p['encoded_name']))
            menu.add_command(label="Hide / Archive", command=lambda: self._hide_project(p['encoded_name']))
            menu.tk_popup(e.x_root, e.y_root)
        card.bind("<Button-3>", _ctx)

        # ── Left: checkbox + info ──
        left = tk.Frame(card, bg=SURFACE)
        left.pack(side="left", fill="x", expand=True, padx=(8, 4), pady=2)
        check_var = tk.BooleanVar(value=False)
        check_var.trace_add("write", self._update_bulk_count)
        self.project_checks[key] = check_var

        cb = tk.Checkbutton(left, variable=check_var, bg=SURFACE, fg=TEXT,
                            selectcolor=ACCENT, activebackground=SURFACE,
                            highlightthickness=0, bd=0, state="normal" if path_exists else "disabled")
        cb.pack(side="left", padx=(4, 0))
        # Shift+click for range select
        def _shift_check(e, k=key):
            if e.state & 0x1:
                self._shift_select(k)
            self._last_checked_key = k
        cb.bind("<Button-1>", _shift_check, add="+")

        info = tk.Frame(left, bg=SURFACE)
        info.pack(side="left", fill="x", expand=True, padx=(6, 0))
        info.bind("<Double-Button-1>", _dbl)

        name_row = tk.Frame(info, bg=SURFACE)
        name_row.pack(fill="x")
        name_row.bind("<Double-Button-1>", _dbl)
        name = Path(proj['decoded_path']).name or proj['encoded_name']
        emoji = _project_emoji(name)

        if is_pinned:
            tk.Label(name_row, text="\u2605", bg=SURFACE, fg=GOLD, font=("Segoe UI", 11)).pack(side="left")

        # Notification dot for new activity
        if has_new:
            tk.Label(name_row, text="\u25cf", bg=SURFACE, fg=CYAN, font=("Segoe UI", 9)).pack(side="left", padx=(2, 0))

        tk.Label(name_row, text=f" {emoji} {name}", bg=SURFACE, fg=TEXT,
                 font=("Segoe UI", 11 if compact else 12, "bold"), anchor="w").pack(side="left")

        # Health badge
        health = proj.get('health', 'unknown')
        if health == 'clean':
            tk.Label(name_row, text=" \u2713 ", bg="#1a3a2a", fg=GREEN,
                     font=("Segoe UI", 8, "bold")).pack(side="left", padx=(6, 0))
        elif health == 'interrupted':
            tk.Label(name_row, text=" \u26a0 ", bg="#3a2a1a", fg=YELLOW,
                     font=("Segoe UI", 8, "bold")).pack(side="left", padx=(6, 0))

        # Session size badge
        if proj['sessions']:
            size_label = _session_size_label(proj['sessions'])
            size_fg = TEXT_MUTED if size_label == "tiny" else (TEXT_DIM if size_label == "med" else YELLOW)
            tk.Label(name_row, text=f" {size_label} ", bg=SURFACE, fg=size_fg,
                     font=("Segoe UI", 7)).pack(side="left", padx=(4, 0))

        # Token estimate
        if proj['sessions']:
            total_sz = sum(s['size'] for s in proj['sessions'])
            tk.Label(name_row, text=f" ~{_token_estimate(total_sz)} ", bg=SURFACE, fg=TEXT_MUTED,
                     font=("Segoe UI", 7)).pack(side="left", padx=(2, 0))

        # Custom flags indicator
        if self.config_data.get("custom_flags", {}).get(key):
            tk.Label(name_row, text=" \u2699", bg=SURFACE, fg=TEXT_MUTED,
                     font=("Segoe UI", 8)).pack(side="left", padx=(4, 0))

        # Launch count
        if launch_count > 0:
            tk.Label(name_row, text=f" {launch_count}\u00d7", bg=SURFACE, fg=TEXT_MUTED,
                     font=("Segoe UI", 7)).pack(side="left", padx=(4, 0))

        if not compact:
            # Pin toggle
            pin_text = "\u2605 unpin" if is_pinned else "\u2606 pin"
            pin_btn = tk.Button(name_row, text=pin_text, bg=SURFACE, fg=TEXT_MUTED,
                                font=("Segoe UI", 8), relief="flat", padx=6, pady=0, bd=0,
                                activebackground=SURFACE, activeforeground=ACCENT, cursor="hand2",
                                command=lambda k=key: self._toggle_pin(k))
            pin_btn.pack(side="left", padx=(10, 0))

            # Path
            tk.Label(info, text=proj['decoded_path'], bg=SURFACE,
                     fg=TEXT_DIM if path_exists else RED,
                     font=("Consolas", 9), anchor="w").pack(fill="x", pady=(2, 0))

            # Session note
            note = self.config_data.get("notes", {}).get(
                proj['sessions'][0]['id'] if proj['sessions'] else "", "")
            if note:
                tk.Label(info, text=f'\U0001f4cc {note}', bg=SURFACE, fg=GOLD,
                         font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(1, 0))

            # Last message snippet
            preview_turns = proj.get('preview', [])
            if preview_turns:
                last_user = next((t['text'] for t in reversed(preview_turns) if t['role'] == 'user'), "")
                if last_user:
                    snippet = last_user.replace('\n', ' ').strip()[:80]
                    if len(snippet) >= 80:
                        snippet += '...'
                    tk.Label(info, text=f'\u201c{snippet}\u201d', bg=SURFACE, fg=PREVIEW,
                             font=("Segoe UI", 8, "italic"), anchor="w").pack(fill="x", pady=(1, 0))

            # Metadata with relative time + duration
            if proj['last_active']:
                rel = _relative_time(proj['last_active'])
                n = len(proj['sessions'])
                dur = ""
                if proj['sessions'] and proj['sessions'][0].get('duration', 0) > 60:
                    dur = f"  \u00b7  {_duration_str(proj['sessions'][0]['duration'])}"
                tk.Label(info, text=f"{rel}  \u00b7  {n} session{'s' if n != 1 else ''}{dur}",
                         bg=SURFACE, fg=TEXT_MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(2, 0))
        else:
            # Compact: just relative time + duration
            if proj['last_active']:
                dur = ""
                if proj['sessions'] and proj['sessions'][0].get('duration', 0) > 60:
                    dur = f" \u00b7 {_duration_str(proj['sessions'][0]['duration'])}"
                tk.Label(name_row, text=f"  {_relative_time(proj['last_active'])}{dur}",
                         bg=SURFACE, fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(8, 0))

        # ── Right: dropdown + buttons ──
        right = tk.Frame(card, bg=SURFACE)
        right.pack(side="right", padx=(4, 12), pady=4 if compact else 6)
        session_ids = [s['id'] for s in proj['sessions'][:15]]
        session_list = proj['sessions'][:15]
        dropdown = None
        if proj['sessions']:
            labels = []
            for s in proj['sessions'][:15]:
                mark = "\u26a0" if s.get('health') == 'interrupted' else ""
                dur = _duration_str(s['duration']) if s.get('duration', 0) > 60 else ""
                labels.append(f"{mark}{s['id'][:8]}.. {s['modified'].strftime('%m/%d %H:%M')} {dur}")
            sv = tk.StringVar(value=labels[0] if labels else "")
            dropdown = ttk.Combobox(right, textvariable=sv, values=labels, width=24, state="readonly")
            dropdown.pack(pady=(0, 4))
            self.project_dropdowns[key] = dropdown

        btn_row = tk.Frame(right, bg=SURFACE)
        btn_row.pack(fill="x")
        if proj['sessions']:
            pbtn = tk.Button(btn_row, text="\u25b6 Preview", bg=SURFACE2, fg=TEXT_DIM,
                             font=("Segoe UI", 8), relief="flat", padx=8, pady=5,
                             activebackground=BORDER, cursor="hand2",
                             command=lambda p=proj, d=dropdown, sl=session_list: self._show_preview_popup(p, d, sl))
            pbtn.pack(side="left", padx=(0, 4))
            _hover_btn(pbtn, SURFACE2, BORDER)

        def on_launch(p=proj, d=dropdown, sids=session_ids):
            mode = self.mode.get()
            sid = None
            if mode == "resume" and d and sids:
                idx = d.current() if d.current() >= 0 else 0
                sid = sids[idx]
            flags = self.config_data.get("custom_flags", {}).get(p['encoded_name'], "")
            mdl, teams = self._resolve_launch_opts(p['encoded_name'])
            launch_session(p['decoded_path'], self.skip_perms.get(), mode, sid, extra_flags=flags,
                          remote_control=self.remote_control.get(), model=mdl, agent_teams=teams)
            self._record_launch(p['encoded_name'])
            self._flash_card(p['encoded_name'])

        bg = ACCENT if path_exists else "#4a4a4a"
        lbtn = tk.Button(btn_row, text="  Launch  ", bg=bg, fg=TEXT,
                         font=("Segoe UI", 10, "bold"), relief="flat", padx=20, pady=5,
                         activebackground=ACCENT_HOVER, cursor="hand2", command=on_launch)
        lbtn.pack(side="left")
        if path_exists:
            _hover_btn(lbtn, ACCENT, ACCENT_HOVER)
        else:
            lbtn.config(state="disabled", cursor="arrow")

    # ── File watcher ──

    def _start_file_watcher(self):
        def _watch():
            last = self._dir_snapshot()
            while self._watcher_running:
                time.sleep(15)
                if not self._watcher_running:
                    break
                try:
                    cur = self._dir_snapshot()
                    if cur != last:
                        last = cur
                        self.after(0, lambda: (self._refresh_projects(), self._show_toast("Projects updated")))
                except Exception:
                    pass
        threading.Thread(target=_watch, daemon=True).start()

    def _dir_snapshot(self):
        try:
            if not PROJECTS_DIR.exists():
                return set()
            entries = set()
            for d in PROJECTS_DIR.iterdir():
                if d.is_dir() and d.name != 'memory':
                    jsonls = list(d.glob("*.jsonl"))
                    latest = max((f.stat().st_mtime for f in jsonls), default=0) if jsonls else 0
                    entries.add((d.name, len(jsonls), int(latest)))
            return entries
        except Exception:
            return set()

    # ── Preview popup ──

    def _show_preview_popup(self, proj, dropdown, session_list, start_idx=0):
        idx = start_idx
        if dropdown and start_idx == 0:
            sel = dropdown.current()
            if sel >= 0:
                idx = sel
        if idx >= len(session_list):
            return
        session = session_list[idx]
        session_file = session['file']
        name = Path(proj['decoded_path']).name or proj['encoded_name']
        turns = self._load_full_preview(session_file)
        files = get_session_files(session_file)
        note = self.config_data.get("notes", {}).get(session['id'], "")
        dur = _duration_str(session['duration']) if session.get('duration', 0) > 60 else ""
        tok = _token_estimate(session['size'])

        popup = tk.Toplevel(self)
        popup.title(f"Session Preview \u2014 {name}")
        popup.geometry("720x520")
        popup.configure(bg="#0a0a14")
        popup.minsize(500, 300)
        popup.transient(self)
        popup.bind("<Escape>", lambda e: popup.destroy())
        popup.update_idletasks()
        popup.geometry(f"+{self.winfo_x() + (self.winfo_width() - 720) // 2}+"
                       f"{self.winfo_y() + (self.winfo_height() - 520) // 2}")

        # Title bar
        tb = tk.Frame(popup, bg="#18162a")
        tb.pack(fill="x")
        tk.Label(tb, text=" \u25cf \u25cf \u25cf", bg="#18162a", fg="#ff5f56",
                 font=("Consolas", 10)).pack(side="left", padx=(10, 0), pady=6)
        tk.Label(tb, text=f"  {_project_emoji(name)} {name} \u2014 {session['id'][:12]}...",
                 bg="#18162a", fg=TEXT_DIM, font=("Consolas", 10)).pack(side="left", padx=(4, 0), pady=6)

        ri = tk.Frame(tb, bg="#18162a")
        ri.pack(side="right", padx=10, pady=6)
        h = session.get('health', 'unknown')
        ht = "\u2713 clean" if h == 'clean' else ("\u26a0 interrupted" if h == 'interrupted' else "")
        hf = GREEN if h == 'clean' else (YELLOW if h == 'interrupted' else TEXT_MUTED)
        tk.Label(ri, text=ht, bg="#18162a", fg=hf, font=("Consolas", 9, "bold")).pack(side="right", padx=(8, 0))
        if dur:
            tk.Label(ri, text=f"{dur} \u00b7 ", bg="#18162a", fg=TEXT_MUTED,
                     font=("Consolas", 9)).pack(side="right")
        tk.Label(ri, text=f"~{tok} \u00b7 ", bg="#18162a", fg=TEXT_MUTED,
                 font=("Consolas", 9)).pack(side="right")
        tk.Label(ri, text=_relative_time(session['modified']), bg="#18162a", fg=TEXT_MUTED,
                 font=("Consolas", 9)).pack(side="right")

        # Path bar + note
        pb = tk.Frame(popup, bg="#12101e")
        pb.pack(fill="x")
        tk.Label(pb, text=f"  \u276f {proj['decoded_path']}", bg="#12101e", fg=TEXT_MUTED,
                 font=("Consolas", 9), anchor="w").pack(fill="x", padx=10, pady=4)
        if note:
            tk.Label(pb, text=f"  \U0001f4cc {note}", bg="#12101e", fg=GOLD,
                     font=("Consolas", 9), anchor="w").pack(fill="x", padx=10, pady=(0, 4))

        # Scrollable conversation
        cf = tk.Frame(popup, bg="#0a0a14")
        cf.pack(fill="both", expand=True)
        cc = tk.Canvas(cf, bg="#0a0a14", highlightthickness=0)
        cs = tk.Scrollbar(cf, orient="vertical", command=cc.yview, bg="#18162a", troughcolor="#0a0a14", width=8)
        ci = tk.Frame(cc, bg="#0a0a14")
        ci.bind("<Configure>", lambda e: cc.configure(scrollregion=cc.bbox("all")))
        cw = cc.create_window((0, 0), window=ci, anchor="nw")
        cc.configure(yscrollcommand=cs.set)
        def _resize(e):
            cc.itemconfig(cw, width=e.width)
            self._update_wraplength(ci, max(300, e.width - 80))
        cc.bind("<Configure>", _resize)
        cc.pack(side="left", fill="both", expand=True)
        cs.pack(side="right", fill="y")
        def _ps(e):
            cc.yview_scroll(int(-1 * (e.delta / 120)), "units")
        cc.bind("<MouseWheel>", _ps)
        ci.bind("<MouseWheel>", _ps)
        def _bcs(w):
            w.bind("<MouseWheel>", _ps)
            for c in w.winfo_children():
                _bcs(c)

        if not turns:
            tk.Label(ci, text="No conversation data found", bg="#0a0a14", fg=TEXT_MUTED,
                     font=("Consolas", 10)).pack(pady=30)
        else:
            for turn in turns:
                self._render_turn(ci, turn)

        # Files modified section
        if files:
            sep = tk.Frame(ci, bg=BORDER, height=1)
            sep.pack(fill="x", padx=12, pady=(10, 4))
            tk.Label(ci, text=f"\U0001f4c4 {len(files)} files touched", bg="#0a0a14", fg=TEXT_DIM,
                     font=("Consolas", 9, "bold")).pack(anchor="w", padx=14, pady=(4, 2))
            for fp in files[:20]:
                short = Path(fp).name
                tk.Label(ci, text=f"  {short}", bg="#0a0a14", fg=TEXT_MUTED,
                         font=("Consolas", 8), anchor="w").pack(fill="x", padx=24)

        popup.after(200, lambda: _bcs(ci))
        popup.after(100, lambda: cc.yview_moveto(1.0))

        # Bottom bar
        bot = tk.Frame(popup, bg="#18162a")
        bot.pack(fill="x")

        # Navigation: Prev / Next
        nav_frame = tk.Frame(bot, bg="#18162a")
        nav_frame.pack(side="left", padx=10, pady=6)
        if idx > 0:
            prev_btn = tk.Button(nav_frame, text="\u25c0 Prev", bg=SURFACE2, fg=TEXT_DIM,
                                 font=("Segoe UI", 8), relief="flat", padx=8, pady=3,
                                 activebackground=BORDER, cursor="hand2",
                                 command=lambda: (popup.destroy(),
                                     self._show_preview_popup(proj, None, session_list, idx - 1)))
            prev_btn.pack(side="left", padx=(0, 4))
            _hover_btn(prev_btn, SURFACE2, BORDER)
        tk.Label(nav_frame, text=f"{idx + 1}/{len(session_list)}", bg="#18162a", fg=TEXT_MUTED,
                 font=("Consolas", 8)).pack(side="left", padx=4)
        if idx < len(session_list) - 1:
            next_btn = tk.Button(nav_frame, text="Next \u25b6", bg=SURFACE2, fg=TEXT_DIM,
                                 font=("Segoe UI", 8), relief="flat", padx=8, pady=3,
                                 activebackground=BORDER, cursor="hand2",
                                 command=lambda: (popup.destroy(),
                                     self._show_preview_popup(proj, None, session_list, idx + 1)))
            next_btn.pack(side="left", padx=(4, 0))
            _hover_btn(next_btn, SURFACE2, BORDER)

        tk.Label(bot, text=f"{len(turns)} messages  \u00b7  {len(files)} files",
                 bg="#18162a", fg=TEXT_MUTED, font=("Consolas", 8)).pack(side="left", padx=(8, 0), pady=6)

        right_btns = tk.Frame(bot, bg="#18162a")
        right_btns.pack(side="right", padx=10, pady=6)
        cbtn = tk.Button(right_btns, text="Close", bg=SURFACE2, fg=TEXT_DIM, font=("Segoe UI", 9),
                         relief="flat", padx=14, pady=4, activebackground=BORDER,
                         cursor="hand2", command=popup.destroy)
        cbtn.pack(side="right", padx=2)
        _hover_btn(cbtn, SURFACE2, BORDER)
        nbtn = tk.Button(right_btns, text="\U0001f4cc Note", bg=SURFACE2, fg=TEXT_DIM, font=("Segoe UI", 9),
                         relief="flat", padx=10, pady=4, activebackground=BORDER,
                         cursor="hand2", command=lambda: self._edit_session_note(session['id']))
        nbtn.pack(side="right", padx=2)
        _hover_btn(nbtn, SURFACE2, BORDER)
        cpbtn = tk.Button(right_btns, text="Copy ID", bg=SURFACE2, fg=TEXT_DIM, font=("Segoe UI", 9),
                          relief="flat", padx=10, pady=4, activebackground=BORDER,
                          cursor="hand2", command=lambda: self._copy_to_clipboard(session['id']))
        cpbtn.pack(side="right", padx=2)
        _hover_btn(cpbtn, SURFACE2, BORDER)

        # Keyboard nav in preview
        popup.bind("<Left>", lambda e: (popup.destroy(),
            self._show_preview_popup(proj, None, session_list, max(0, idx - 1))) if idx > 0 else None)
        popup.bind("<Right>", lambda e: (popup.destroy(),
            self._show_preview_popup(proj, None, session_list, min(len(session_list) - 1, idx + 1))) if idx < len(session_list) - 1 else None)

    def _update_wraplength(self, container, wrap):
        for child in container.winfo_children():
            for sub in child.winfo_children():
                if isinstance(sub, tk.Frame):
                    for label in sub.winfo_children():
                        if isinstance(label, tk.Label):
                            try:
                                if label.cget('wraplength'):
                                    label.config(wraplength=wrap)
                            except Exception:
                                pass

    def _render_turn(self, parent, turn):
        role = turn.get('role', '')
        text = turn.get('text', '')
        if role == 'user':
            f = tk.Frame(parent, bg="#0a0a14")
            f.pack(fill="x", padx=12, pady=(8, 2))
            h = tk.Frame(f, bg="#0a0a14")
            h.pack(fill="x")
            tk.Label(h, text="\u276f You", bg="#0a0a14", fg=GREEN,
                     font=("Consolas", 10, "bold")).pack(side="left")
            m = tk.Frame(f, bg="#1a2332")
            m.pack(fill="x", pady=(2, 0))
            tk.Label(m, text=text, bg="#1a2332", fg="#e8e8e8", font=("Consolas", 9),
                     anchor="w", justify="left", wraplength=620).pack(fill="x", padx=10, pady=6)
        elif role == 'assistant':
            f = tk.Frame(parent, bg="#0a0a14")
            f.pack(fill="x", padx=12, pady=(6, 2))
            h = tk.Frame(f, bg="#0a0a14")
            h.pack(fill="x")
            tk.Label(h, text="\u2726 Claude", bg="#0a0a14", fg=ACCENT,
                     font=("Consolas", 10, "bold")).pack(side="left")
            m = tk.Frame(f, bg="#14122a")
            m.pack(fill="x", pady=(2, 0))
            tk.Label(m, text=text, bg="#14122a", fg=TEXT_DIM, font=("Consolas", 9),
                     anchor="w", justify="left", wraplength=620).pack(fill="x", padx=10, pady=6)
        elif role == 'tool':
            f = tk.Frame(parent, bg="#0a0a14")
            f.pack(fill="x", padx=24, pady=(1, 1))
            tk.Label(f, text=f"\u2502 \u2699 {text}", bg="#0a0a14", fg=TEXT_MUTED,
                     font=("Consolas", 8), anchor="w").pack(fill="x")

    def _load_full_preview(self, session_file: Path) -> list:
        try:
            fsize = session_file.stat().st_size
            with open(session_file, 'rb') as f:
                f.seek(max(0, fsize - 200_000))
                data = f.read().decode('utf-8', errors='ignore')
            turns = []
            for line in data.strip().split('\n'):
                try:
                    d = json.loads(line)
                    if d.get('type', '') in ('progress', 'file-history-snapshot', 'queue-operation'):
                        continue
                    msg = d.get('message', {})
                    role = msg.get('role', '')
                    content = msg.get('content', '')
                    text = ""
                    tools_used = []
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            if block.get('type') == 'text':
                                t = block.get('text', '').strip()
                                if t:
                                    text = t
                            elif block.get('type') == 'tool_use':
                                tools_used.append(block.get('name', ''))
                    elif isinstance(content, str):
                        text = content.strip()
                    if text and text.startswith(('<system', '<teammate', '<local-command', '<command-name', '<hook')):
                        continue
                    if role == 'user' and text and len(text) > 3:
                        turns.append({'role': 'user', 'text': text})
                    elif role == 'assistant':
                        if text:
                            turns.append({'role': 'assistant', 'text': text})
                        if tools_used:
                            turns.append({'role': 'tool', 'text': ', '.join(_friendly_tool(t) for t in tools_used)})
                except (json.JSONDecodeError, KeyError):
                    continue
            merged = []
            for t in turns:
                if merged and merged[-1]['role'] == 'tool' and t['role'] == 'tool':
                    merged[-1]['text'] += ', ' + t['text']
                else:
                    merged.append(t)
            for t in merged:
                if len(t['text']) > 500:
                    t['text'] = t['text'][:500] + '...'
            return merged[-30:]
        except OSError:
            return []


if __name__ == "__main__":
    app = SessionLauncher()
    app.mainloop()
