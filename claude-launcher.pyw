#!/usr/bin/env python3
"""Claude Code Session Launcher - Quick resume sessions after restart."""

import os
import subprocess
import json
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from datetime import datetime, timedelta

PROJECTS_DIR = Path.home() / ".claude" / "projects"
CONFIG_FILE = Path.home() / ".claude" / "launcher-config.json"
STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

_path_cache = {}


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"pinned": [], "auto_start": False}


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
    """Reconstruct last few conversation turns from session JSONL.
    Returns list of dicts: [{'role': 'user'|'assistant'|'tool', 'text': str}, ...]
    """
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

                # Extract text and tool info from content blocks
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

                # Skip noise
                if text and text.startswith(('<system', '<teammate', '<local-command', '<command-name', '<hook')):
                    continue

                if role == 'user' and text and len(text) > 3:
                    turns.append({'role': 'user', 'text': text})
                elif role == 'assistant':
                    if text:
                        turns.append({'role': 'assistant', 'text': text})
                    if tools_used:
                        # Collapse consecutive tool calls
                        tool_names = [_friendly_tool(t) for t in tools_used]
                        turns.append({'role': 'tool', 'text': ', '.join(tool_names)})
            except (json.JSONDecodeError, KeyError):
                continue

        # Deduplicate consecutive tool entries
        merged = []
        for t in turns:
            if merged and merged[-1]['role'] == 'tool' and t['role'] == 'tool':
                merged[-1]['text'] += ', ' + t['text']
            else:
                merged.append(t)

        # Return last N meaningful turns (trim to fit card)
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
    """Convert tool names to friendly short labels."""
    mapping = {
        'Read': 'Read', 'Write': 'Write', 'Edit': 'Edit',
        'Bash': 'Terminal', 'Glob': 'Search', 'Grep': 'Search',
        'Task': 'Agent', 'WebFetch': 'Web', 'WebSearch': 'Web',
    }
    return mapping.get(name, name)


def get_session_health(session_file: Path) -> str:
    """Check if session ended cleanly or was interrupted.
    Returns: 'clean', 'interrupted', or 'unknown'
    """
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
        preview = sessions[0]['preview'] if sessions else ""
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

    # Pinned first, then by last active
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
    launch_cmd = f"set CLAUDECODE= && {cmd_str}"

    try:
        subprocess.Popen(["wt", "-d", quoted_path, "cmd", "/k", launch_cmd])
    except FileNotFoundError:
        subprocess.Popen(
            f'start cmd /k "cd /d "{quoted_path}" && {launch_cmd}"',
            shell=True
        )


def toggle_autostart(enable: bool):
    """Add or remove from Windows startup."""
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
BG = "#0f0e17"           # deep background
SURFACE = "#1a1932"      # card / panel background
SURFACE2 = "#232046"     # raised surface (options bar)
BORDER = "#2e2b4a"       # subtle borders
ACCENT = "#7f5af0"       # primary purple
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
    """Add hover effect to a button."""
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

        self._setup_styles()
        self._build_ui()

        self.update_idletasks()
        x = (self.winfo_screenwidth() // 2) - (self.winfo_reqwidth() // 2)
        y = (self.winfo_screenheight() // 2) - (self.winfo_reqheight() // 2)
        self.geometry(f"+{x}+{y}")

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
        # Remove focus highlight on combobox
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

        refresh_btn = tk.Button(header, text="\u21bb  Refresh", bg=SURFACE2, fg=TEXT_DIM,
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

        # Separator
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

        # ── Toolbar ──
        toolbar = tk.Frame(self, bg=BG)
        toolbar.pack(fill="x", padx=20, pady=(10, 6))

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

        # Compact legend
        legend = tk.Frame(toolbar, bg=BG)
        legend.pack(side="left", padx=(6, 0))
        for sym, color, label in [("\u2605", GOLD, "pinned"), ("\u2713", GREEN, "clean"), ("\u26a0", YELLOW, "interrupted")]:
            tk.Label(legend, text=sym, bg=BG, fg=color, font=("Segoe UI", 9)).pack(side="left")
            tk.Label(legend, text=label, bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(1, 8))

        self.bulk_launch_btn = tk.Button(
            toolbar, text="  Launch Selected (0)  ", bg="#4a4a4a", fg=TEXT,
            font=("Segoe UI", 10, "bold"), relief="flat", padx=20, pady=5,
            activebackground=GREEN_HOVER, cursor="hand2", state="disabled",
            command=self._launch_selected)
        self.bulk_launch_btn.pack(side="right")

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

    def _on_autostart_toggle(self, *_args):
        enabled = self.auto_start.get()
        self.config_data["auto_start"] = enabled
        save_config(self.config_data)
        toggle_autostart(enabled)

    def _toggle_pin(self, encoded_name: str):
        pinned = set(self.config_data.get("pinned", []))
        if encoded_name in pinned:
            pinned.discard(encoded_name)
        else:
            pinned.add(encoded_name)
        self.config_data["pinned"] = list(pinned)
        save_config(self.config_data)
        self._rerender_projects()

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

    def _refresh_projects(self):
        _path_cache.clear()
        self._cached_projects = get_projects()
        self._render_project_list()

    def _rerender_projects(self):
        if not hasattr(self, '_cached_projects') or not self._cached_projects:
            self._refresh_projects()
            return

        pinned = set(self.config_data.get("pinned", []))
        for proj in self._cached_projects:
            proj['pinned'] = proj['encoded_name'] in pinned

        self._cached_projects.sort(key=lambda p: (
            0 if p['pinned'] else 1,
            -(p['last_active'].timestamp() if p['last_active'] else 0)
        ))
        self._render_project_list()

    def _render_project_list(self):
        for widget in self.scrollable.winfo_children():
            widget.destroy()

        self.project_checks.clear()
        self.project_data.clear()
        self.project_dropdowns.clear()

        if not self._cached_projects:
            tk.Label(self.scrollable, text="No projects found in ~/.claude/projects/",
                     bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 12)).pack(pady=40)
            return

        for proj in self._cached_projects:
            self._add_project_card(proj)

        self._update_bulk_count()

    def _add_project_card(self, proj):
        key = proj['encoded_name']
        path_exists = os.path.isdir(proj['decoded_path'])
        is_pinned = proj.get('pinned', False)

        self.project_data[key] = proj

        # Card with left accent stripe
        card_outer = tk.Frame(self.scrollable, bg=BG)
        card_outer.pack(fill="x", pady=3)

        # Left color stripe
        stripe_color = GOLD if is_pinned else (ACCENT if path_exists else RED)
        stripe = tk.Frame(card_outer, bg=stripe_color, width=4)
        stripe.pack(side="left", fill="y")
        stripe.pack_propagate(False)

        card = tk.Frame(card_outer, bg=SURFACE)
        card.pack(side="left", fill="x", expand=True, ipady=8)

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

        # Name row
        name_row = tk.Frame(info, bg=SURFACE)
        name_row.pack(fill="x")

        name = Path(proj['decoded_path']).name or proj['encoded_name']

        if is_pinned:
            tk.Label(name_row, text="\u2605 ", bg=SURFACE, fg=GOLD,
                     font=("Segoe UI", 12)).pack(side="left")

        tk.Label(name_row, text=name, bg=SURFACE, fg=TEXT,
                 font=("Segoe UI", 12, "bold"), anchor="w").pack(side="left")

        # Health badge
        health = proj.get('health', 'unknown')
        if health == 'clean':
            badge = tk.Label(name_row, text=" \u2713 clean ", bg="#1a3a2a", fg=GREEN,
                             font=("Segoe UI", 8, "bold"))
            badge.pack(side="left", padx=(8, 0))
        elif health == 'interrupted':
            badge = tk.Label(name_row, text=" \u26a0 interrupted ", bg="#3a2a1a", fg=YELLOW,
                             font=("Segoe UI", 8, "bold"))
            badge.pack(side="left", padx=(8, 0))

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

        btn_bg = ACCENT if path_exists else "#4a4a4a"
        launch_btn = tk.Button(btn_row, text="  Launch  ", bg=btn_bg, fg=TEXT,
                               font=("Segoe UI", 10, "bold"), relief="flat", padx=20, pady=5,
                               activebackground=ACCENT_HOVER, cursor="hand2", command=on_launch)
        launch_btn.pack(side="left")

        if path_exists:
            _hover_btn(launch_btn, ACCENT, ACCENT_HOVER)
        else:
            launch_btn.config(state="disabled", cursor="arrow")


    def _show_preview_popup(self, proj, dropdown, session_list):
        """Open a popup window showing conversation preview for the selected session."""
        # Determine which session to preview
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

        # Load full preview (more turns for the popup)
        turns = self._load_full_preview(session_file)

        # Create popup window
        popup = tk.Toplevel(self)
        popup.title(f"Session Preview \u2014 {name}")
        popup.geometry("700x500")
        popup.configure(bg="#0a0a14")
        popup.minsize(500, 300)

        # Center relative to main window
        popup.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 350
        y = self.winfo_y() + (self.winfo_height() // 2) - 250
        popup.geometry(f"+{x}+{y}")

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
        conv_canvas.bind("<Configure>",
                         lambda e: conv_canvas.itemconfig(cw, width=e.width))

        conv_canvas.pack(side="left", fill="both", expand=True)
        conv_scroll.pack(side="right", fill="y")

        # Bind mousewheel only within popup
        def _popup_scroll(e):
            conv_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        conv_canvas.bind("<MouseWheel>", _popup_scroll)
        conv_inner.bind("<MouseWheel>", _popup_scroll)
        # Re-bind child widgets to popup scroll as they're created
        def _bind_children_scroll(widget):
            widget.bind("<MouseWheel>", _popup_scroll)
            for child in widget.winfo_children():
                _bind_children_scroll(child)
        popup.after(200, lambda: _bind_children_scroll(conv_inner))

        # Render conversation turns
        if not turns:
            tk.Label(conv_inner, text="No conversation data found",
                     bg="#0a0a14", fg=TEXT_MUTED, font=("Consolas", 10)).pack(pady=30)
        else:
            for turn in turns:
                self._render_turn(conv_inner, turn)

        # Auto-scroll to bottom
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

    def _render_turn(self, parent, turn):
        """Render a single conversation turn in the preview popup."""
        role = turn.get('role', '')
        text = turn.get('text', '')

        if role == 'user':
            # User message bubble
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
            # Claude message
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
            # Tool usage - compact
            frame = tk.Frame(parent, bg="#0a0a14")
            frame.pack(fill="x", padx=24, pady=(1, 1))
            tk.Label(frame, text=f"\u2502 \u2699 {text}", bg="#0a0a14", fg=TEXT_MUTED,
                     font=("Consolas", 8), anchor="w").pack(fill="x")

    def _load_full_preview(self, session_file: Path) -> list:
        """Load more conversation turns for the popup (up to 30)."""
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

            # Deduplicate consecutive tools
            merged = []
            for t in turns:
                if merged and merged[-1]['role'] == 'tool' and t['role'] == 'tool':
                    merged[-1]['text'] += ', ' + t['text']
                else:
                    merged.append(t)

            # Trim long messages and return last 30 turns
            for t in merged:
                if len(t['text']) > 500:
                    t['text'] = t['text'][:500] + '...'
            return merged[-30:]
        except OSError:
            return []


if __name__ == "__main__":
    app = SessionLauncher()
    app.mainloop()
