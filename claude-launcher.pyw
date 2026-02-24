#!/usr/bin/env python3
"""Claude Code Session Launcher - Quick resume sessions after restart."""

import os
import subprocess
import json
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from datetime import datetime


PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Cache: encoded folder name -> real cwd path
_path_cache = {}


def get_real_path(project_dir: Path, encoded_name: str) -> str:
    """Get the real filesystem path by reading cwd from session JSONL files."""
    if encoded_name in _path_cache:
        return _path_cache[encoded_name]

    # Find the most recent .jsonl file
    jsonl_files = sorted(project_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)

    for jf in jsonl_files[:3]:  # check up to 3 most recent
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

    # Fallback: use folder name as-is (won't match but shows something)
    _path_cache[encoded_name] = encoded_name
    return encoded_name


def get_sessions(project_dir: Path) -> list:
    """Get session IDs from a project directory, sorted by recency."""
    sessions = []
    for f in project_dir.glob("*.jsonl"):
        try:
            stat = f.stat()
            sessions.append({
                'id': f.stem,
                'file': f,
                'modified': datetime.fromtimestamp(stat.st_mtime),
                'size': stat.st_size,
            })
        except OSError:
            continue
    sessions.sort(key=lambda s: s['modified'], reverse=True)
    return sessions


def get_projects() -> list:
    """Scan Claude projects directory and return project info."""
    if not PROJECTS_DIR.exists():
        return []

    projects = []
    for d in PROJECTS_DIR.iterdir():
        if not d.is_dir():
            continue
        # Skip memory/ and other non-project dirs
        if d.name in ('memory',):
            continue

        decoded_path = get_real_path(d, d.name)
        sessions = get_sessions(d)
        last_active = sessions[0]['modified'] if sessions else None

        projects.append({
            'encoded_name': d.name,
            'decoded_path': decoded_path,
            'dir': d,
            'sessions': sessions,
            'last_active': last_active,
        })

    projects.sort(key=lambda p: p['last_active'] or datetime.min, reverse=True)
    return projects


def launch_session(decoded_path: str, skip_permissions: bool, mode: str, session_id: str = None):
    """Launch Claude Code in a new terminal window."""
    args = ["claude"]

    if mode == "continue":
        args.append("--continue")
    elif mode == "resume" and session_id:
        args.extend(["-r", session_id])

    if skip_permissions:
        args.append("--dangerously-skip-permissions")

    cmd_str = " ".join(args)
    quoted_path = decoded_path.replace('"', '')
    # Clear CLAUDECODE env var so nested session check doesn't block launch
    launch_cmd = f"set CLAUDECODE= && {cmd_str}"

    # Try Windows Terminal first, fall back to cmd
    try:
        subprocess.Popen(["wt", "-d", quoted_path, "cmd", "/k", launch_cmd])
    except FileNotFoundError:
        subprocess.Popen(
            f'start cmd /k "cd /d "{quoted_path}" && {launch_cmd}"',
            shell=True
        )


class SessionLauncher(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Claude Code Session Launcher")
        self.geometry("780x560")
        self.minsize(650, 420)
        self.configure(bg="#1a1a2e")

        try:
            self.iconbitmap(default='')
        except Exception:
            pass

        self.skip_perms = tk.BooleanVar(value=True)
        self.mode = tk.StringVar(value="continue")
        self.project_checks = {}  # encoded_name -> BooleanVar
        self.project_data = {}    # encoded_name -> proj dict
        self.project_dropdowns = {}  # encoded_name -> dropdown widget

        self._build_ui()

        # Center on screen
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
                       activeforeground="#e0e0e0", font=("Consolas", 10)).pack(side="left", padx=(12, 25))

        tk.Radiobutton(opts, text="--continue (last session)", variable=self.mode, value="continue",
                       bg="#16213e", fg="#e0e0e0", selectcolor="#2d3561",
                       activebackground="#16213e", activeforeground="#e0e0e0",
                       font=("Segoe UI", 10)).pack(side="left", padx=(0, 15))

        tk.Radiobutton(opts, text="-r (pick session)", variable=self.mode, value="resume",
                       bg="#16213e", fg="#e0e0e0", selectcolor="#2d3561",
                       activebackground="#16213e", activeforeground="#e0e0e0",
                       font=("Segoe UI", 10)).pack(side="left")

        # Bulk actions bar
        bulk = tk.Frame(self, bg="#1a1a2e")
        bulk.pack(fill="x", padx=15, pady=(0, 5))

        select_all_btn = tk.Button(bulk, text="Select All", bg="#2d3561", fg="#e0e0e0",
                                   font=("Segoe UI", 9), relief="flat", padx=8, pady=2,
                                   activebackground="#3d4571", cursor="hand2",
                                   command=self._select_all)
        select_all_btn.pack(side="left", padx=(0, 5))

        select_none_btn = tk.Button(bulk, text="Select None", bg="#2d3561", fg="#e0e0e0",
                                    font=("Segoe UI", 9), relief="flat", padx=8, pady=2,
                                    activebackground="#3d4571", cursor="hand2",
                                    command=self._select_none)
        select_none_btn.pack(side="left", padx=(0, 15))

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
        border_color = "#7c3aed" if path_exists else "#dc2626"

        self.project_data[key] = proj

        card = tk.Frame(self.scrollable, bg="#16213e",
                        highlightbackground=border_color, highlightthickness=1)
        card.pack(fill="x", pady=4, ipady=6)

        # Left: checkbox + info
        left = tk.Frame(card, bg="#16213e")
        left.pack(side="left", fill="x", expand=True, padx=4, pady=4)

        check_var = tk.BooleanVar(value=False)
        check_var.trace_add("write", self._update_bulk_count)
        self.project_checks[key] = check_var

        cb = tk.Checkbutton(left, variable=check_var, bg="#16213e",
                            selectcolor="#2d3561", activebackground="#16213e",
                            state="normal" if path_exists else "disabled")
        cb.pack(side="left", padx=(4, 0))

        info = tk.Frame(left, bg="#16213e")
        info.pack(side="left", fill="x", expand=True, padx=(4, 0))

        name = Path(proj['decoded_path']).name or proj['encoded_name']
        tk.Label(info, text=name, bg="#16213e", fg="#c4b5fd",
                 font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x")

        path_color = "#94a3b8" if path_exists else "#ef4444"
        tk.Label(info, text=proj['decoded_path'], bg="#16213e", fg=path_color,
                 font=("Consolas", 9), anchor="w").pack(fill="x")

        if proj['last_active']:
            time_str = proj['last_active'].strftime("%Y-%m-%d %H:%M")
            n = len(proj['sessions'])
            tk.Label(info, text=f"Last: {time_str}  |  {n} session(s)",
                     bg="#16213e", fg="#64748b", font=("Segoe UI", 8), anchor="w").pack(fill="x")

        # Right side: session dropdown + launch button
        right = tk.Frame(card, bg="#16213e")
        right.pack(side="right", padx=12, pady=4)

        # Session picker dropdown
        session_ids = [s['id'] for s in proj['sessions'][:15]]
        dropdown = None

        if proj['sessions']:
            session_labels = []
            for s in proj['sessions'][:15]:
                label = f"{s['id'][:8]}.. {s['modified'].strftime('%m/%d %H:%M')}"
                session_labels.append(label)

            session_var = tk.StringVar(value=session_labels[0] if session_labels else "")
            dropdown = ttk.Combobox(right, textvariable=session_var,
                                    values=session_labels, width=22, state="readonly")
            dropdown.pack(pady=(0, 5))
            self.project_dropdowns[key] = dropdown

        # Launch button
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
