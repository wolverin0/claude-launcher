#!/usr/bin/env python3
"""Claude Code Session Launcher - Quick resume sessions after restart."""

import os
import sys
import subprocess
import json
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from datetime import datetime, timedelta
import threading
import time
import platform

PROJECTS_DIR = Path.home() / ".claude" / "projects"
CONFIG_FILE = Path.home() / ".claude" / "launcher-config.json"
STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

_path_cache = {}


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"pinned": [], "auto_start": False, "hidden": [], "sort": "recent"}


def save_config(config: dict):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except OSError:
        pass


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
                            cwd = data['cwd']
                            _path_cache[encoded_name] = cwd
                            return cwd
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            continue

    _path_cache[encoded_name] = encoded_name
    return encoded_name


def get_session_preview(session_file: Path) -> list:
    """Reconstruct last few conversation turns from session JSONL."""
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
                        tool_names = [_friendly_tool(t) for t in tools_used]
                        turns.append({'role': 'tool', 'text': ', '.join(tool_names)})
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
            entry = {**t}
            entry['text'] = entry['text'].replace('\n', ' ').strip()[:90]
            if len(entry['text']) >= 90:
                entry['text'] += '...'
            result.append(entry)
        result.reverse()
        return result
    except OSError:
        pass
    return []


def _friendly_tool(name: str) -> str:
    mapping = {
        'Read': 'Read', 'Write': 'Write', 'Edit': 'Edit',
        'Bash': 'Terminal', 'Glob': 'Search', 'Grep': 'Search',
        'Task': 'Agent', 'WebFetch': 'Web', 'WebSearch': 'Web',
    }
    return mapping.get(name, name)


def get_session_health(session_file: Path) -> str:
    try:
        fsize = session_file.stat().st_size
        with open(session_file, 'rb') as f:
            f.seek(max(0, fsize - 10_000))
            data = f.read().decode('utf-8', errors='ignore')

        last_type = ""
        for line in data.strip().split('\n'):
            try:
                d = json.loads(line)
                last_type = d.get('type', '')
            except (json.JSONDecodeError, KeyError):
                continue

        if last_type in ('system', 'queue-operation'):
            return 'clean'
        elif last_type in ('assistant', 'progress'):
            return 'interrupted'
        else:
            return 'unknown'
    except OSError:
        return 'unknown'


def get_sessions(project_dir: Path) -> list:
    sessions = []
    for f in project_dir.glob("*.jsonl"):
        try:
            stat = f.stat()
            preview = get_session_preview(f)
            health = get_session_health(f)
            sessions.append({
                'id': f.stem,
                'file': f,
                'modified': datetime.fromtimestamp(stat.st_mtime),
                'size': stat.st_size,
                'preview': preview,
                'health': health,
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
        if not d.is_dir():
            continue
        if d.name in ('memory',):
            continue

        decoded_path = get_real_path(d, d.name)
        sessions = get_sessions(d)
        last_active = sessions[0]['modified'] if sessions else None
        preview = sessions[0]['preview'] if sessions else []
        health = sessions[0]['health'] if sessions else "unknown"

        projects.append({
            'encoded_name': d.name,
            'decoded_path': decoded_path,
            'dir': d,
            'sessions': sessions,
            'last_active': last_active,
            'pinned': d.name in pinned,
            'preview': preview,
            'health': health,
        })

    projects.sort(key=lambda p: (
        0 if p['pinned'] else 1,
        -(p['last_active'].timestamp() if p['last_active'] else 0)
    ))
    return projects


def launch_session(decoded_path: str, skip_permissions: bool, mode: str, session_id: str = None):
    args = ["claude"]

    if mode == "continue":
        args.append("--continue")
    elif mode == "resume" and session_id:
        args.extend(["-r", session_id])

    if skip_permissions:
        args.append("--dangerously-skip-permissions")

    cmd_str = " ".join(args)
    quoted_path = decoded_path.replace('"', '')

    if IS_WINDOWS:
        launch_cmd = f"set CLAUDECODE= && {cmd_str}"
        try:
            subprocess.Popen(["wt", "-d", quoted_path, "cmd", "/k", launch_cmd])
        except FileNotFoundError:
            subprocess.Popen(
                f'start cmd /k "cd /d "{quoted_path}" && {launch_cmd}"',
                shell=True
            )
    elif IS_MAC:
        script = (
            f'tell application "Terminal"\n'
            f'  do script "cd \\"{quoted_path}\\" && unset CLAUDECODE && {cmd_str}"\n'
            f'  activate\n'
            f'end tell'
        )
        subprocess.Popen(["osascript", "-e", script])
    else:
        # Linux: try common terminal emulators
        launch_cmd = f"cd '{quoted_path}' && unset CLAUDECODE && {cmd_str}"
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
        vbs_content = (
            f'Set ws = CreateObject("WScript.Shell")\n'
            f'ws.Run "pythonw ""{script_path}""", 0, False\n'
        )
        try:
            STARTUP_DIR.mkdir(parents=True, exist_ok=True)
            with open(vbs_path, 'w') as f:
                f.write(vbs_content)
        except OSError:
            pass
    else:
        try:
            vbs_path.unlink(missing_ok=True)
        except OSError:
            pass


# -- Color palette --
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


def _hover_btn(widget, normal_bg, hover_bg):
    widget.bind("<Enter>", lambda e: widget.config(bg=hover_bg))
    widget.bind("<Leave>", lambda e: widget.config(bg=normal_bg))


class SessionLauncher(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Claude Code Session Launcher")
        self.geometry("820x600")
        self.minsize(700, 460)
        self.configure(bg=BG)

        try:
            self.iconbitmap(default='')
        except Exception:
            pass

        self.config_data = load_config()
        self.skip_perms = tk.BooleanVar(value=True)
        self.mode = tk.StringVar(value="continue")
        self.auto_start = tk.BooleanVar(value=self.config_data.get("auto_start", False))
        self.auto_start.trace_add("write", self._on_autostart_toggle)
        self.project_checks = {}
        self.project_data = {}
        self.project_dropdowns = {}
        self._card_frames = {}

        # Search & sort state
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search_change)
        self.sort_var = tk.StringVar(value=self.config_data.get("sort", "recent"))
        self.sort_var.trace_add("write", self._on_sort_change)

        self._loading = False
        self._watcher_running = True

        self._setup_styles()
        self._build_ui()

        # Center window
        self.update_idletasks()
        x = (self.winfo_screenwidth() // 2) - (self.winfo_reqwidth() // 2)
        y = (self.winfo_screenheight() // 2) - (self.winfo_reqheight() // 2)
        self.geometry(f"+{x}+{y}")

        # Keyboard shortcuts
        self.bind("<Control-a>", lambda e: self._select_all())
        self.bind("<Control-A>", lambda e: self._select_all())
        self.bind("<Escape>", lambda e: self._on_escape())
        self.bind("<Return>", lambda e: self._launch_selected())
        self.bind("<Control-r>", lambda e: self._refresh_projects())
        self.bind("<Control-R>", lambda e: self._refresh_projects())
        self.bind("<Control-f>", lambda e: self._focus_search())
        self.bind("<Control-F>", lambda e: self._focus_search())

        # Minimize-on-close (X button minimizes, Quit button exits)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Start file watcher
        self._start_file_watcher()

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox",
                        fieldbackground=SURFACE2,
                        background=SURFACE2,
                        foreground=TEXT_DIM,
                        selectbackground=ACCENT,
                        arrowcolor=TEXT_DIM,
                        borderwidth=0)
        style.map("TCombobox",
                  fieldbackground=[("readonly", SURFACE2)],
                  selectbackground=[("readonly", ACCENT)],
                  foreground=[("readonly", TEXT_DIM)])
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
        tk.Label(title_frame, text="Resume your sessions in one click",
                 bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(anchor="w")

        # Header buttons
        btn_frame = tk.Frame(header, bg=BG)
        btn_frame.pack(side="right")

        quit_btn = tk.Button(btn_frame, text="\u2715 Quit", bg=RED, fg=TEXT,
                             font=("Segoe UI", 9), relief="flat", padx=10, pady=5,
                             activebackground="#c42860", cursor="hand2",
                             command=self._quit_app)
        quit_btn.pack(side="right", padx=(6, 0), pady=(4, 0))
        _hover_btn(quit_btn, RED, "#c42860")

        refresh_btn = tk.Button(btn_frame, text="\u21bb  Refresh", bg=SURFACE2, fg=TEXT_DIM,
                                font=("Segoe UI", 9), relief="flat", padx=12, pady=5,
                                activebackground=BORDER, cursor="hand2",
                                command=self._refresh_projects)
        refresh_btn.pack(side="right", pady=(4, 0))
        _hover_btn(refresh_btn, SURFACE2, BORDER)

        # ── Options panel ──
        opts_outer = tk.Frame(self, bg=BORDER)
        opts_outer.pack(fill="x", padx=20, pady=(12, 0), ipady=1)
        opts = tk.Frame(opts_outer, bg=SURFACE)
        opts.pack(fill="x", padx=1, pady=1, ipady=8)

        left_opts = tk.Frame(opts, bg=SURFACE)
        left_opts.pack(side="left", padx=(14, 0))

        tk.Checkbutton(left_opts, text="Skip permissions",
                       variable=self.skip_perms, bg=SURFACE, fg=TEXT,
                       selectcolor=ACCENT, activebackground=SURFACE,
                       activeforeground=TEXT, font=("Segoe UI", 10),
                       highlightthickness=0, bd=0).pack(side="left", padx=(0, 20))

        tk.Frame(left_opts, bg=BORDER, width=1).pack(side="left", fill="y", padx=(0, 16), pady=2)

        tk.Radiobutton(left_opts, text="--continue", variable=self.mode, value="continue",
                       bg=SURFACE, fg=TEXT, selectcolor=ACCENT,
                       activebackground=SURFACE, activeforeground=TEXT,
                       font=("Segoe UI", 10), highlightthickness=0, bd=0).pack(side="left", padx=(0, 8))

        tk.Radiobutton(left_opts, text="-r (pick session)", variable=self.mode, value="resume",
                       bg=SURFACE, fg=TEXT, selectcolor=ACCENT,
                       activebackground=SURFACE, activeforeground=TEXT,
                       font=("Segoe UI", 10), highlightthickness=0, bd=0).pack(side="left")

        tk.Checkbutton(opts, text="Auto-start",
                       variable=self.auto_start, bg=SURFACE, fg=TEXT_MUTED,
                       selectcolor=ACCENT, activebackground=SURFACE,
                       activeforeground=TEXT_MUTED, font=("Segoe UI", 9),
                       highlightthickness=0, bd=0).pack(side="right", padx=(0, 14))

        # ── Search + Sort bar ──
        search_bar = tk.Frame(self, bg=BG)
        search_bar.pack(fill="x", padx=20, pady=(10, 0))

        search_frame = tk.Frame(search_bar, bg=SURFACE2)
        search_frame.pack(side="left", fill="x", expand=True, padx=(0, 8))
        tk.Label(search_frame, text=" \U0001f50d", bg=SURFACE2, fg=TEXT_MUTED,
                 font=("Segoe UI", 10)).pack(side="left", padx=(6, 0))
        self.search_entry = tk.Entry(search_frame, textvariable=self.search_var,
                                      bg=SURFACE2, fg=TEXT, font=("Segoe UI", 10),
                                      insertbackground=TEXT, relief="flat", bd=0)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=6, pady=6)

        sort_frame = tk.Frame(search_bar, bg=BG)
        sort_frame.pack(side="right")
        tk.Label(sort_frame, text="Sort:", bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        sort_combo = ttk.Combobox(sort_frame, textvariable=self.sort_var,
                                   values=["recent", "name", "sessions"],
                                   width=10, state="readonly")
        sort_combo.pack(side="left")

        # ── Toolbar ──
        toolbar = tk.Frame(self, bg=BG)
        toolbar.pack(fill="x", padx=20, pady=(8, 6))

        sel_all = tk.Button(toolbar, text="Select All", bg=SURFACE2, fg=TEXT_DIM,
                            font=("Segoe UI", 9), relief="flat", padx=10, pady=4,
                            activebackground=BORDER, cursor="hand2",
                            command=self._select_all)
        sel_all.pack(side="left", padx=(0, 4))
        _hover_btn(sel_all, SURFACE2, BORDER)

        sel_none = tk.Button(toolbar, text="Deselect", bg=SURFACE2, fg=TEXT_DIM,
                             font=("Segoe UI", 9), relief="flat", padx=10, pady=4,
                             activebackground=BORDER, cursor="hand2",
                             command=self._select_none)
        sel_none.pack(side="left", padx=(0, 12))
        _hover_btn(sel_none, SURFACE2, BORDER)

        legend = tk.Frame(toolbar, bg=BG)
        legend.pack(side="left", padx=(6, 0))
        for sym, color, label in [("\u2605", GOLD, "pinned"), ("\u2713", GREEN, "clean"), ("\u26a0", YELLOW, "interrupted")]:
            tk.Label(legend, text=sym, bg=BG, fg=color, font=("Segoe UI", 9)).pack(side="left")
            tk.Label(legend, text=label, bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(1, 8))

        # Shortcut hints
        tk.Label(toolbar, text="Ctrl+F search \u00b7 Ctrl+A select \u00b7 Enter launch",
                 bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 7)).pack(side="left", padx=(12, 0))

        self.bulk_launch_btn = tk.Button(
            toolbar, text="  Launch Selected (0)  ", bg="#4a4a4a", fg=TEXT,
            font=("Segoe UI", 10, "bold"), relief="flat", padx=20, pady=5,
            activebackground=GREEN_HOVER, cursor="hand2", state="disabled",
            command=self._launch_selected)
        self.bulk_launch_btn.pack(side="right")

        # ── Loading indicator ──
        self.loading_label = tk.Label(self, text="\u23f3 Loading projects...",
                                       bg=BG, fg=ACCENT, font=("Segoe UI", 11))

        # ── Scrollable project list ──
        container = tk.Frame(self, bg=BG)
        container.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        self.canvas = tk.Canvas(container, bg=BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(container, orient="vertical", command=self.canvas.yview,
                                 bg=SURFACE2, troughcolor=BG, width=8)
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

        self._refresh_projects()

    # ── Window management ──

    def _on_close(self):
        """Minimize to taskbar instead of quitting."""
        self.iconify()

    def _quit_app(self):
        """Actually quit the application."""
        self._watcher_running = False
        self.destroy()

    def _on_escape(self):
        """Escape: clear search first, then deselect all."""
        if self.search_var.get():
            self.search_var.set("")
            self.focus_set()
        else:
            self._select_none()

    def _focus_search(self):
        self.search_entry.focus_set()
        self.search_entry.select_range(0, tk.END)

    # ── Event handlers ──

    def _on_autostart_toggle(self, *_args):
        enabled = self.auto_start.get()
        self.config_data["auto_start"] = enabled
        save_config(self.config_data)
        toggle_autostart(enabled)

    def _on_search_change(self, *_args):
        self._rerender_projects()

    def _on_sort_change(self, *_args):
        self.config_data["sort"] = self.sort_var.get()
        save_config(self.config_data)
        self._rerender_projects()

    def _toggle_pin(self, encoded_name: str):
        pinned = set(self.config_data.get("pinned", []))
        if encoded_name in pinned:
            pinned.discard(encoded_name)
        else:
            pinned.add(encoded_name)
        self.config_data["pinned"] = list(pinned)
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
        popup.geometry("450x350")
        popup.configure(bg=BG)
        popup.transient(self)
        popup.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 225
        y = self.winfo_y() + (self.winfo_height() // 2) - 175
        popup.geometry(f"+{x}+{y}")
        popup.bind("<Escape>", lambda e: popup.destroy())

        tk.Label(popup, text="Hidden Projects", bg=BG, fg=TEXT,
                 font=("Segoe UI", 13, "bold")).pack(pady=(12, 8))

        list_frame = tk.Frame(popup, bg=BG)
        list_frame.pack(fill="both", expand=True, padx=12)

        for name in list(hidden):
            row = tk.Frame(list_frame, bg=SURFACE)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=name[:40], bg=SURFACE, fg=TEXT_DIM,
                     font=("Consolas", 9), anchor="w").pack(side="left", padx=8, pady=6, fill="x", expand=True)

            def _do_unhide(n=name, p=popup):
                self._unhide_project(n)
                p.destroy()
                if self.config_data.get("hidden"):
                    self._show_hidden_projects()

            unhide = tk.Button(row, text="Unhide", bg=ACCENT, fg=TEXT,
                               font=("Segoe UI", 8), relief="flat", padx=8, pady=2,
                               cursor="hand2", command=_do_unhide)
            unhide.pack(side="right", padx=8, pady=4)

        close_btn = tk.Button(popup, text="Close", bg=SURFACE2, fg=TEXT_DIM,
                              font=("Segoe UI", 9), relief="flat", padx=14, pady=4,
                              cursor="hand2", command=popup.destroy)
        close_btn.pack(pady=10)

    # ── Bulk operations ──

    def _update_bulk_count(self, *_args):
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
            proj = self.project_data.get(key, {})
            if os.path.isdir(proj.get('decoded_path', '')):
                var.set(True)
        self._update_bulk_count()

    def _select_none(self):
        for var in self.project_checks.values():
            var.set(False)
        self._update_bulk_count()

    def _launch_selected(self):
        mode = self.mode.get()
        skip = self.skip_perms.get()
        launched = []

        for key, var in self.project_checks.items():
            if not var.get():
                continue
            proj = self.project_data.get(key)
            if not proj or not os.path.isdir(proj['decoded_path']):
                continue

            session_id = None
            if mode == "resume":
                dropdown = self.project_dropdowns.get(key)
                sids = [s['id'] for s in proj['sessions'][:15]]
                if dropdown and sids:
                    idx = dropdown.current() if dropdown.current() >= 0 else 0
                    session_id = sids[idx]

            launch_session(proj['decoded_path'], skip, mode, session_id)
            launched.append(key)

        for key in launched:
            self._flash_card(key)

    def _flash_card(self, key):
        """Brief green flash on card stripe to confirm launch."""
        if key in self._card_frames:
            stripe = self._card_frames[key]
            original_bg = stripe.cget('bg')
            stripe.config(bg=GREEN)
            self.after(600, lambda s=stripe, bg=original_bg: s.config(bg=bg))

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
        self.loading_label.pack_forget()
        self._render_project_list()

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
        sort = self.sort_var.get()
        if sort == "name":
            self._cached_projects.sort(key=lambda p: (
                0 if p['pinned'] else 1,
                Path(p['decoded_path']).name.lower()
            ))
        elif sort == "sessions":
            self._cached_projects.sort(key=lambda p: (
                0 if p['pinned'] else 1,
                -len(p['sessions'])
            ))
        else:  # recent
            self._cached_projects.sort(key=lambda p: (
                0 if p['pinned'] else 1,
                -(p['last_active'].timestamp() if p['last_active'] else 0)
            ))

    def _get_filtered_projects(self):
        query = self.search_var.get().lower().strip()
        hidden = set(self.config_data.get("hidden", []))

        projects = [p for p in self._cached_projects if p['encoded_name'] not in hidden]
        if query:
            projects = [p for p in projects
                        if query in p['decoded_path'].lower()
                        or query in Path(p['decoded_path']).name.lower()
                        or query in p['encoded_name'].lower()]
        return projects

    def _render_project_list(self):
        for widget in self.scrollable.winfo_children():
            widget.destroy()

        self.project_checks.clear()
        self.project_data.clear()
        self.project_dropdowns.clear()
        self._card_frames.clear()

        if not hasattr(self, '_cached_projects') or not self._cached_projects:
            tk.Label(self.scrollable, text="No projects found in ~/.claude/projects/",
                     bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 12)).pack(pady=40)
            return

        filtered = self._get_filtered_projects()

        if not filtered:
            msg = "No projects match your search" if self.search_var.get() else "All projects are hidden"
            tk.Label(self.scrollable, text=msg,
                     bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 11)).pack(pady=30)
            hidden = self.config_data.get("hidden", [])
            if hidden:
                unhide_btn = tk.Button(self.scrollable, text=f"Show {len(hidden)} hidden projects",
                                       bg=SURFACE2, fg=TEXT_DIM, font=("Segoe UI", 9),
                                       relief="flat", padx=12, pady=6, cursor="hand2",
                                       command=self._show_hidden_projects)
                unhide_btn.pack()
            return

        for proj in filtered:
            self._add_project_card(proj)

        # Show hidden count at bottom
        hidden = self.config_data.get("hidden", [])
        if hidden:
            hidden_row = tk.Frame(self.scrollable, bg=BG)
            hidden_row.pack(fill="x", pady=(8, 0))
            unhide_btn = tk.Button(hidden_row,
                                    text=f"{len(hidden)} hidden \u2014 manage",
                                    bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 8),
                                    relief="flat", cursor="hand2", bd=0,
                                    activeforeground=ACCENT,
                                    command=self._show_hidden_projects)
            unhide_btn.pack()

        self._update_bulk_count()

    def _add_project_card(self, proj):
        key = proj['encoded_name']
        path_exists = os.path.isdir(proj['decoded_path'])
        is_pinned = proj.get('pinned', False)

        self.project_data[key] = proj

        # Card with left accent stripe
        card_outer = tk.Frame(self.scrollable, bg=BG)
        card_outer.pack(fill="x", pady=3)

        stripe_color = GOLD if is_pinned else (ACCENT if path_exists else RED)
        stripe = tk.Frame(card_outer, bg=stripe_color, width=4)
        stripe.pack(side="left", fill="y")
        stripe.pack_propagate(False)
        self._card_frames[key] = stripe

        card = tk.Frame(card_outer, bg=SURFACE)
        card.pack(side="left", fill="x", expand=True, ipady=8)

        # Double-click to launch
        def _dbl_click(e, p=proj):
            if path_exists:
                launch_session(p['decoded_path'], self.skip_perms.get(), self.mode.get())
                self._flash_card(p['encoded_name'])
        card.bind("<Double-Button-1>", _dbl_click)

        # Right-click context menu
        def _show_context(e, p=proj):
            menu = tk.Menu(self, tearoff=0, bg=SURFACE, fg=TEXT,
                          activebackground=ACCENT, activeforeground=TEXT,
                          font=("Segoe UI", 9))
            if path_exists:
                menu.add_command(label="Launch", command=lambda: (
                    launch_session(p['decoded_path'], self.skip_perms.get(), self.mode.get()),
                    self._flash_card(p['encoded_name'])))
            pin_label = "Unpin" if is_pinned else "Pin to top"
            menu.add_command(label=pin_label, command=lambda: self._toggle_pin(p['encoded_name']))
            menu.add_separator()
            menu.add_command(label="Hide / Archive", command=lambda: self._hide_project(p['encoded_name']))
            if path_exists:
                if IS_WINDOWS:
                    menu.add_command(label="Open folder",
                                    command=lambda: subprocess.Popen(["explorer", p['decoded_path']]))
                elif IS_MAC:
                    menu.add_command(label="Open folder",
                                    command=lambda: subprocess.Popen(["open", p['decoded_path']]))
                else:
                    menu.add_command(label="Open folder",
                                    command=lambda: subprocess.Popen(["xdg-open", p['decoded_path']]))
            menu.tk_popup(e.x_root, e.y_root)
        card.bind("<Button-3>", _show_context)

        # ── Left: checkbox + info ──
        left = tk.Frame(card, bg=SURFACE)
        left.pack(side="left", fill="x", expand=True, padx=(8, 4), pady=2)

        check_var = tk.BooleanVar(value=False)
        check_var.trace_add("write", self._update_bulk_count)
        self.project_checks[key] = check_var

        cb = tk.Checkbutton(left, variable=check_var, bg=SURFACE, fg=TEXT,
                            selectcolor=ACCENT, activebackground=SURFACE,
                            highlightthickness=0, bd=0,
                            state="normal" if path_exists else "disabled")
        cb.pack(side="left", padx=(4, 0))

        info = tk.Frame(left, bg=SURFACE)
        info.pack(side="left", fill="x", expand=True, padx=(6, 0))
        info.bind("<Double-Button-1>", _dbl_click)

        # Name row
        name_row = tk.Frame(info, bg=SURFACE)
        name_row.pack(fill="x")
        name_row.bind("<Double-Button-1>", _dbl_click)

        name = Path(proj['decoded_path']).name or proj['encoded_name']

        if is_pinned:
            tk.Label(name_row, text="\u2605 ", bg=SURFACE, fg=GOLD,
                     font=("Segoe UI", 12)).pack(side="left")

        tk.Label(name_row, text=name, bg=SURFACE, fg=TEXT,
                 font=("Segoe UI", 12, "bold"), anchor="w").pack(side="left")

        # Health badge
        health = proj.get('health', 'unknown')
        if health == 'clean':
            tk.Label(name_row, text=" \u2713 clean ", bg="#1a3a2a", fg=GREEN,
                     font=("Segoe UI", 8, "bold")).pack(side="left", padx=(8, 0))
        elif health == 'interrupted':
            tk.Label(name_row, text=" \u26a0 interrupted ", bg="#3a2a1a", fg=YELLOW,
                     font=("Segoe UI", 8, "bold")).pack(side="left", padx=(8, 0))

        # Pin toggle
        pin_text = "\u2605 unpin" if is_pinned else "\u2606 pin"
        pin_btn = tk.Button(name_row, text=pin_text,
                            bg=SURFACE, fg=TEXT_MUTED, font=("Segoe UI", 8),
                            relief="flat", padx=6, pady=0, bd=0,
                            activebackground=SURFACE, activeforeground=ACCENT,
                            cursor="hand2",
                            command=lambda k=key: self._toggle_pin(k))
        pin_btn.pack(side="left", padx=(10, 0))

        # Path
        path_color = TEXT_DIM if path_exists else RED
        tk.Label(info, text=proj['decoded_path'], bg=SURFACE, fg=path_color,
                 font=("Consolas", 9), anchor="w").pack(fill="x", pady=(2, 0))

        # Last message snippet
        preview_turns = proj.get('preview', [])
        if preview_turns:
            last_user = ""
            for t in reversed(preview_turns):
                if t['role'] == 'user':
                    last_user = t['text']
                    break
            if last_user:
                snippet = last_user.replace('\n', ' ').strip()[:80]
                if len(snippet) >= 80:
                    snippet += '...'
                tk.Label(info, text=f'\u201c{snippet}\u201d', bg=SURFACE, fg=PREVIEW,
                         font=("Segoe UI", 8, "italic"), anchor="w").pack(fill="x", pady=(1, 0))

        # Metadata
        if proj['last_active']:
            time_str = proj['last_active'].strftime("%b %d, %H:%M")
            n = len(proj['sessions'])
            tk.Label(info, text=f"{time_str}  \u00b7  {n} session{'s' if n != 1 else ''}",
                     bg=SURFACE, fg=TEXT_MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(2, 0))

        # ── Right: dropdown + buttons ──
        right = tk.Frame(card, bg=SURFACE)
        right.pack(side="right", padx=(4, 12), pady=6)

        session_ids = [s['id'] for s in proj['sessions'][:15]]
        session_list = proj['sessions'][:15]
        dropdown = None

        if proj['sessions']:
            session_labels = []
            for s in proj['sessions'][:15]:
                mark = "\u26a0" if s.get('health') == 'interrupted' else ""
                label = f"{mark}{s['id'][:8]}.. {s['modified'].strftime('%m/%d %H:%M')}"
                session_labels.append(label)

            session_var = tk.StringVar(value=session_labels[0] if session_labels else "")
            dropdown = ttk.Combobox(right, textvariable=session_var,
                                    values=session_labels, width=20, state="readonly")
            dropdown.pack(pady=(0, 4))
            self.project_dropdowns[key] = dropdown

        # Button row
        btn_row = tk.Frame(right, bg=SURFACE)
        btn_row.pack(fill="x")

        # Preview button
        if proj['sessions']:
            preview_btn = tk.Button(btn_row, text="\u25b6 Preview", bg=SURFACE2, fg=TEXT_DIM,
                                    font=("Segoe UI", 8), relief="flat", padx=8, pady=5,
                                    activebackground=BORDER, cursor="hand2",
                                    command=lambda p=proj, d=dropdown, sl=session_list:
                                        self._show_preview_popup(p, d, sl))
            preview_btn.pack(side="left", padx=(0, 4))
            _hover_btn(preview_btn, SURFACE2, BORDER)

        def on_launch(p=proj, d=dropdown, sids=session_ids):
            mode = self.mode.get()
            session_id = None
            if mode == "resume" and d and sids:
                idx = d.current() if d.current() >= 0 else 0
                session_id = sids[idx]
            launch_session(p['decoded_path'], self.skip_perms.get(), mode, session_id)
            self._flash_card(p['encoded_name'])

        btn_bg = ACCENT if path_exists else "#4a4a4a"
        launch_btn = tk.Button(btn_row, text="  Launch  ", bg=btn_bg, fg=TEXT,
                               font=("Segoe UI", 10, "bold"), relief="flat", padx=20, pady=5,
                               activebackground=ACCENT_HOVER, cursor="hand2", command=on_launch)
        launch_btn.pack(side="left")

        if path_exists:
            _hover_btn(launch_btn, ACCENT, ACCENT_HOVER)
        else:
            launch_btn.config(state="disabled", cursor="arrow")

    # ── File watcher ──

    def _start_file_watcher(self):
        def _watch():
            last_snapshot = self._dir_snapshot()
            while self._watcher_running:
                time.sleep(15)
                if not self._watcher_running:
                    break
                try:
                    current = self._dir_snapshot()
                    if current != last_snapshot:
                        last_snapshot = current
                        self.after(0, self._refresh_projects)
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

    def _show_preview_popup(self, proj, dropdown, session_list):
        idx = 0
        if dropdown:
            sel = dropdown.current()
            if sel >= 0:
                idx = sel

        if idx >= len(session_list):
            return

        session = session_list[idx]
        session_file = session['file']
        name = Path(proj['decoded_path']).name or proj['encoded_name']

        turns = self._load_full_preview(session_file)

        popup = tk.Toplevel(self)
        popup.title(f"Session Preview \u2014 {name}")
        popup.geometry("700x500")
        popup.configure(bg="#0a0a14")
        popup.minsize(500, 300)
        popup.transient(self)

        popup.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 350
        y = self.winfo_y() + (self.winfo_height() // 2) - 250
        popup.geometry(f"+{x}+{y}")

        # Escape to close
        popup.bind("<Escape>", lambda e: popup.destroy())

        # ── Title bar ──
        title_bar = tk.Frame(popup, bg="#18162a")
        title_bar.pack(fill="x")

        tk.Label(title_bar, text=" \u25cf \u25cf \u25cf", bg="#18162a", fg="#ff5f56",
                 font=("Consolas", 10)).pack(side="left", padx=(10, 0), pady=6)

        title_text = f"  {name} \u2014 {session['id'][:12]}..."
        tk.Label(title_bar, text=title_text, bg="#18162a", fg=TEXT_DIM,
                 font=("Consolas", 10)).pack(side="left", padx=(4, 0), pady=6)

        time_str = session['modified'].strftime("%b %d, %H:%M")
        health = session.get('health', 'unknown')
        health_txt = "\u2713 clean" if health == 'clean' else ("\u26a0 interrupted" if health == 'interrupted' else "")
        health_fg = GREEN if health == 'clean' else (YELLOW if health == 'interrupted' else TEXT_MUTED)

        right_info = tk.Frame(title_bar, bg="#18162a")
        right_info.pack(side="right", padx=10, pady=6)
        tk.Label(right_info, text=health_txt, bg="#18162a", fg=health_fg,
                 font=("Consolas", 9, "bold")).pack(side="right", padx=(8, 0))
        tk.Label(right_info, text=time_str, bg="#18162a", fg=TEXT_MUTED,
                 font=("Consolas", 9)).pack(side="right")

        # ── Path bar ──
        path_bar = tk.Frame(popup, bg="#12101e")
        path_bar.pack(fill="x")
        tk.Label(path_bar, text=f"  \u276f {proj['decoded_path']}", bg="#12101e", fg=TEXT_MUTED,
                 font=("Consolas", 9), anchor="w").pack(fill="x", padx=10, pady=4)

        # ── Scrollable conversation ──
        conv_frame = tk.Frame(popup, bg="#0a0a14")
        conv_frame.pack(fill="both", expand=True)

        conv_canvas = tk.Canvas(conv_frame, bg="#0a0a14", highlightthickness=0)
        conv_scroll = tk.Scrollbar(conv_frame, orient="vertical", command=conv_canvas.yview,
                                   bg="#18162a", troughcolor="#0a0a14", width=8)
        conv_inner = tk.Frame(conv_canvas, bg="#0a0a14")

        conv_inner.bind("<Configure>",
                        lambda e: conv_canvas.configure(scrollregion=conv_canvas.bbox("all")))
        cw = conv_canvas.create_window((0, 0), window=conv_inner, anchor="nw")
        conv_canvas.configure(yscrollcommand=conv_scroll.set)

        # Dynamic wraplength on resize
        def _on_resize(e):
            conv_canvas.itemconfig(cw, width=e.width)
            wrap = max(300, e.width - 80)
            self._update_wraplength(conv_inner, wrap)
        conv_canvas.bind("<Configure>", _on_resize)

        conv_canvas.pack(side="left", fill="both", expand=True)
        conv_scroll.pack(side="right", fill="y")

        # Bind mousewheel only within popup
        def _popup_scroll(e):
            conv_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        conv_canvas.bind("<MouseWheel>", _popup_scroll)
        conv_inner.bind("<MouseWheel>", _popup_scroll)

        def _bind_children_scroll(widget):
            widget.bind("<MouseWheel>", _popup_scroll)
            for child in widget.winfo_children():
                _bind_children_scroll(child)

        # Render conversation turns
        if not turns:
            tk.Label(conv_inner, text="No conversation data found",
                     bg="#0a0a14", fg=TEXT_MUTED, font=("Consolas", 10)).pack(pady=30)
        else:
            for turn in turns:
                self._render_turn(conv_inner, turn)

        popup.after(200, lambda: _bind_children_scroll(conv_inner))
        popup.after(100, lambda: conv_canvas.yview_moveto(1.0))

        # ── Bottom bar ──
        bottom = tk.Frame(popup, bg="#18162a")
        bottom.pack(fill="x")

        close_btn = tk.Button(bottom, text="Close", bg=SURFACE2, fg=TEXT_DIM,
                              font=("Segoe UI", 9), relief="flat", padx=14, pady=4,
                              activebackground=BORDER, cursor="hand2",
                              command=popup.destroy)
        close_btn.pack(side="right", padx=10, pady=6)
        _hover_btn(close_btn, SURFACE2, BORDER)

        tk.Label(bottom, text=f"{len(turns)} messages shown",
                 bg="#18162a", fg=TEXT_MUTED, font=("Consolas", 8)).pack(side="left", padx=10, pady=6)

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
            frame = tk.Frame(parent, bg="#0a0a14")
            frame.pack(fill="x", padx=12, pady=(8, 2))

            header = tk.Frame(frame, bg="#0a0a14")
            header.pack(fill="x")
            tk.Label(header, text="\u276f You", bg="#0a0a14", fg=GREEN,
                     font=("Consolas", 10, "bold")).pack(side="left")

            msg_bg = "#1a2332"
            msg = tk.Frame(frame, bg=msg_bg)
            msg.pack(fill="x", pady=(2, 0))
            tk.Label(msg, text=text, bg=msg_bg, fg="#e8e8e8",
                     font=("Consolas", 9), anchor="w", justify="left",
                     wraplength=620).pack(fill="x", padx=10, pady=6)

        elif role == 'assistant':
            frame = tk.Frame(parent, bg="#0a0a14")
            frame.pack(fill="x", padx=12, pady=(6, 2))

            header = tk.Frame(frame, bg="#0a0a14")
            header.pack(fill="x")
            tk.Label(header, text="\u2726 Claude", bg="#0a0a14", fg=ACCENT,
                     font=("Consolas", 10, "bold")).pack(side="left")

            msg_bg = "#14122a"
            msg = tk.Frame(frame, bg=msg_bg)
            msg.pack(fill="x", pady=(2, 0))
            tk.Label(msg, text=text, bg=msg_bg, fg=TEXT_DIM,
                     font=("Consolas", 9), anchor="w", justify="left",
                     wraplength=620).pack(fill="x", padx=10, pady=6)

        elif role == 'tool':
            frame = tk.Frame(parent, bg="#0a0a14")
            frame.pack(fill="x", padx=24, pady=(1, 1))
            tk.Label(frame, text=f"\u2502 \u2699 {text}", bg="#0a0a14", fg=TEXT_MUTED,
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
                            tool_names = [_friendly_tool(t) for t in tools_used]
                            turns.append({'role': 'tool', 'text': ', '.join(tool_names)})
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
