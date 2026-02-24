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


def get_session_preview(session_file: Path) -> str:
    """Read last user message from session JSONL as a preview of what was being worked on."""
    try:
        fsize = session_file.stat().st_size
        with open(session_file, 'rb') as f:
            # Read last 100KB to find recent user messages
            f.seek(max(0, fsize - 100_000))
            data = f.read().decode('utf-8', errors='ignore')

        last_user_msg = ""
        for line in data.strip().split('\n'):
            try:
                d = json.loads(line)
                if d.get('type') != 'user':
                    continue
                msg = d.get('message', {})
                if msg.get('role') != 'user':
                    continue
                content = msg.get('content', '')
                text = ""
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'text':
                            text = block.get('text', '').strip()
                            break
                elif isinstance(content, str):
                    text = content.strip()
                # Skip system/hook/teammate/command messages
                if not text or len(text) < 5:
                    continue
                if text.startswith(('<system', '<teammate', '<local-command', '<command-name', '<hook')):
                    continue
                last_user_msg = text
            except (json.JSONDecodeError, KeyError):
                continue

        if last_user_msg:
            # Clean and truncate
            clean = last_user_msg.replace('\n', ' ').strip()
            return clean[:100] + ("..." if len(clean) > 100 else "")
    except OSError:
        pass
    return ""


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


# Health indicator symbols and colors
HEALTH_ICONS = {
    'clean': ('', '#22c55e'),        # green circle
    'interrupted': ('', '#f59e0b'),  # yellow warning
    'unknown': ('', '#6b7280'),      # gray
}


class SessionLauncher(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Claude Code Session Launcher")
        self.geometry("800x580")
        self.minsize(680, 440)
        self.configure(bg="#1a1a2e")

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

        self._build_ui()

        self.update_idletasks()
        x = (self.winfo_screenwidth() // 2) - (self.winfo_reqwidth() // 2)
        y = (self.winfo_screenheight() // 2) - (self.winfo_reqheight() // 2)
        self.geometry(f"+{x}+{y}")

    def _build_ui(self):
        # Header
        header = tk.Frame(self, bg="#1a1a2e")
        header.pack(fill="x", padx=15, pady=(15, 5))

        tk.Label(header, text="Claude Code Session Launcher",
                 bg="#1a1a2e", fg="#c4b5fd", font=("Segoe UI", 16, "bold")).pack(side="left")

        refresh_btn = tk.Button(header, text="Refresh", bg="#2d3561", fg="#e0e0e0",
                                font=("Segoe UI", 9), relief="flat", padx=10, pady=3,
                                activebackground="#3d4571", cursor="hand2",
                                command=self._refresh_projects)
        refresh_btn.pack(side="right")

        # Options bar
        opts = tk.Frame(self, bg="#16213e", highlightbackground="#2d3561", highlightthickness=1)
        opts.pack(fill="x", padx=15, pady=8, ipady=6)

        tk.Checkbutton(opts, text="--dangerously-skip-permissions",
                       variable=self.skip_perms, bg="#16213e", fg="#e0e0e0",
                       selectcolor="#2d3561", activebackground="#16213e",
                       activeforeground="#e0e0e0", font=("Consolas", 10)).pack(side="left", padx=(12, 20))

        tk.Radiobutton(opts, text="--continue", variable=self.mode, value="continue",
                       bg="#16213e", fg="#e0e0e0", selectcolor="#2d3561",
                       activebackground="#16213e", activeforeground="#e0e0e0",
                       font=("Segoe UI", 10)).pack(side="left", padx=(0, 10))

        tk.Radiobutton(opts, text="-r (pick)", variable=self.mode, value="resume",
                       bg="#16213e", fg="#e0e0e0", selectcolor="#2d3561",
                       activebackground="#16213e", activeforeground="#e0e0e0",
                       font=("Segoe UI", 10)).pack(side="left", padx=(0, 15))

        tk.Checkbutton(opts, text="Auto-start with Windows",
                       variable=self.auto_start, bg="#16213e", fg="#94a3b8",
                       selectcolor="#2d3561", activebackground="#16213e",
                       activeforeground="#94a3b8", font=("Segoe UI", 9)).pack(side="right", padx=(0, 12))

        # Bulk actions bar
        bulk = tk.Frame(self, bg="#1a1a2e")
        bulk.pack(fill="x", padx=15, pady=(0, 5))

        tk.Button(bulk, text="Select All", bg="#2d3561", fg="#e0e0e0",
                  font=("Segoe UI", 9), relief="flat", padx=8, pady=2,
                  activebackground="#3d4571", cursor="hand2",
                  command=self._select_all).pack(side="left", padx=(0, 5))

        tk.Button(bulk, text="Select None", bg="#2d3561", fg="#e0e0e0",
                  font=("Segoe UI", 9), relief="flat", padx=8, pady=2,
                  activebackground="#3d4571", cursor="hand2",
                  command=self._select_none).pack(side="left", padx=(0, 15))

        # Legend
        legend = tk.Frame(bulk, bg="#1a1a2e")
        legend.pack(side="left", padx=(10, 0))
        tk.Label(legend, text="*", bg="#1a1a2e", fg="#fbbf24", font=("Segoe UI", 10)).pack(side="left")
        tk.Label(legend, text="=pinned", bg="#1a1a2e", fg="#64748b", font=("Segoe UI", 8)).pack(side="left", padx=(0, 8))
        tk.Label(legend, text="OK", bg="#1a1a2e", fg="#22c55e", font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Label(legend, text="=clean", bg="#1a1a2e", fg="#64748b", font=("Segoe UI", 8)).pack(side="left", padx=(0, 8))
        tk.Label(legend, text="!!", bg="#1a1a2e", fg="#f59e0b", font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Label(legend, text="=interrupted", bg="#1a1a2e", fg="#64748b", font=("Segoe UI", 8)).pack(side="left")

        self.bulk_launch_btn = tk.Button(
            bulk, text="  Launch Selected (0)  ", bg="#059669", fg="white",
            font=("Segoe UI", 11, "bold"), relief="flat", padx=18, pady=5,
            activebackground="#047857", cursor="hand2",
            command=self._launch_selected)
        self.bulk_launch_btn.pack(side="right")

        # Scrollable project list
        container = tk.Frame(self, bg="#1a1a2e")
        container.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        self.canvas = tk.Canvas(container, bg="#1a1a2e", highlightthickness=0)
        scrollbar = tk.Scrollbar(container, orient="vertical", command=self.canvas.yview,
                                 bg="#2d3561", troughcolor="#1a1a2e")
        self.scrollable = tk.Frame(self.canvas, bg="#1a1a2e")

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
        self._refresh_projects()

    def _update_bulk_count(self, *_args):
        count = sum(1 for v in self.project_checks.values()
                    if v.get() and os.path.isdir(self.project_data.get(
                        self._key_for_var(v), {}).get('decoded_path', '')))
        self.bulk_launch_btn.config(text=f"  Launch Selected ({count})  ")
        if count > 0:
            self.bulk_launch_btn.config(bg="#059669", state="normal", cursor="hand2")
        else:
            self.bulk_launch_btn.config(bg="#6b7280", state="disabled", cursor="arrow")

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
        for widget in self.scrollable.winfo_children():
            widget.destroy()

        self.project_checks.clear()
        self.project_data.clear()
        self.project_dropdowns.clear()

        projects = get_projects()

        if not projects:
            tk.Label(self.scrollable, text="No projects found in ~/.claude/projects/",
                     bg="#1a1a2e", fg="#94a3b8", font=("Segoe UI", 12)).pack(pady=30)
            return

        for proj in projects:
            self._add_project_card(proj)

        self._update_bulk_count()

    def _add_project_card(self, proj):
        key = proj['encoded_name']
        path_exists = os.path.isdir(proj['decoded_path'])
        is_pinned = proj.get('pinned', False)

        if is_pinned:
            border_color = "#fbbf24"  # gold for pinned
        elif path_exists:
            border_color = "#7c3aed"
        else:
            border_color = "#dc2626"

        self.project_data[key] = proj

        card = tk.Frame(self.scrollable, bg="#16213e",
                        highlightbackground=border_color, highlightthickness=1)
        card.pack(fill="x", pady=3, ipady=5)

        # Left: checkbox + info
        left = tk.Frame(card, bg="#16213e")
        left.pack(side="left", fill="x", expand=True, padx=4, pady=3)

        check_var = tk.BooleanVar(value=False)
        check_var.trace_add("write", self._update_bulk_count)
        self.project_checks[key] = check_var

        cb = tk.Checkbutton(left, variable=check_var, bg="#16213e", fg="white",
                            selectcolor="#7c3aed", activebackground="#16213e",
                            indicatoron=True,
                            state="normal" if path_exists else "disabled")
        cb.pack(side="left", padx=(4, 0))

        info = tk.Frame(left, bg="#16213e")
        info.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # Name row with pin button and health indicator
        name_row = tk.Frame(info, bg="#16213e")
        name_row.pack(fill="x")

        name = Path(proj['decoded_path']).name or proj['encoded_name']
        pin_text = "* " if is_pinned else ""
        pin_color = "#fbbf24" if is_pinned else "#c4b5fd"

        if is_pinned:
            tk.Label(name_row, text="*", bg="#16213e", fg="#fbbf24",
                     font=("Segoe UI", 14, "bold")).pack(side="left")

        tk.Label(name_row, text=name, bg="#16213e", fg="#c4b5fd",
                 font=("Segoe UI", 12, "bold"), anchor="w").pack(side="left")

        # Health indicator
        health = proj.get('health', 'unknown')
        if health == 'clean':
            tk.Label(name_row, text=" OK", bg="#16213e", fg="#22c55e",
                     font=("Segoe UI", 9, "bold")).pack(side="left", padx=(6, 0))
        elif health == 'interrupted':
            tk.Label(name_row, text=" !!", bg="#16213e", fg="#f59e0b",
                     font=("Segoe UI", 9, "bold")).pack(side="left", padx=(6, 0))

        # Pin toggle button
        pin_btn = tk.Button(name_row, text="unpin" if is_pinned else "pin",
                            bg="#16213e", fg="#94a3b8", font=("Segoe UI", 8),
                            relief="flat", padx=4, pady=0, bd=0,
                            activebackground="#16213e", activeforeground="#c4b5fd",
                            cursor="hand2",
                            command=lambda k=key: self._toggle_pin(k))
        pin_btn.pack(side="left", padx=(8, 0))

        # Path
        path_color = "#94a3b8" if path_exists else "#ef4444"
        tk.Label(info, text=proj['decoded_path'], bg="#16213e", fg=path_color,
                 font=("Consolas", 9), anchor="w").pack(fill="x")

        # Session preview (last user message)
        preview = proj.get('preview', '')
        if preview:
            tk.Label(info, text=f'"{preview}"', bg="#16213e", fg="#8b5cf6",
                     font=("Segoe UI", 9, "italic"), anchor="w").pack(fill="x")

        # Metadata line
        if proj['last_active']:
            time_str = proj['last_active'].strftime("%Y-%m-%d %H:%M")
            n = len(proj['sessions'])
            tk.Label(info, text=f"Last: {time_str}  |  {n} session(s)",
                     bg="#16213e", fg="#64748b", font=("Segoe UI", 8), anchor="w").pack(fill="x")

        # Right side: session dropdown + launch button
        right = tk.Frame(card, bg="#16213e")
        right.pack(side="right", padx=12, pady=4)

        session_ids = [s['id'] for s in proj['sessions'][:15]]
        dropdown = None

        if proj['sessions']:
            session_labels = []
            for s in proj['sessions'][:15]:
                health_mark = "!" if s.get('health') == 'interrupted' else ""
                label = f"{health_mark}{s['id'][:8]}.. {s['modified'].strftime('%m/%d %H:%M')}"
                session_labels.append(label)

            session_var = tk.StringVar(value=session_labels[0] if session_labels else "")
            dropdown = ttk.Combobox(right, textvariable=session_var,
                                    values=session_labels, width=22, state="readonly")
            dropdown.pack(pady=(0, 5))
            self.project_dropdowns[key] = dropdown

        def on_launch(p=proj, d=dropdown, sids=session_ids):
            mode = self.mode.get()
            session_id = None
            if mode == "resume" and d and sids:
                idx = d.current() if d.current() >= 0 else 0
                session_id = sids[idx]
            launch_session(p['decoded_path'], self.skip_perms.get(), mode, session_id)

        btn_bg = "#7c3aed" if path_exists else "#6b7280"
        launch_btn = tk.Button(right, text="  Launch  ", bg=btn_bg, fg="white",
                               font=("Segoe UI", 11, "bold"), relief="flat", padx=18, pady=6,
                               activebackground="#6d28d9", cursor="hand2", command=on_launch)
        launch_btn.pack()

        if not path_exists:
            launch_btn.config(state="disabled", cursor="arrow")


if __name__ == "__main__":
    app = SessionLauncher()
    app.mainloop()
