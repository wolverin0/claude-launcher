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
        self.geometry("750x520")
        self.minsize(600, 400)
        self.configure(bg="#1a1a2e")

        # Icon (optional, skip if not available)
        try:
            self.iconbitmap(default='')
        except Exception:
            pass

        self.skip_perms = tk.BooleanVar(value=True)
        self.mode = tk.StringVar(value="continue")

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

        # Make scrollable frame expand to canvas width
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width))

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mouse wheel
        self.bind_all("<MouseWheel>",
                      lambda e: self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        self._refresh_projects()

    def _refresh_projects(self):
        for widget in self.scrollable.winfo_children():
            widget.destroy()

        projects = get_projects()

        if not projects:
            tk.Label(self.scrollable, text="No projects found in ~/.claude/projects/",
                     bg="#1a1a2e", fg="#94a3b8", font=("Segoe UI", 12)).pack(pady=30)
            return

        for proj in projects:
            self._add_project_card(proj)

    def _add_project_card(self, proj):
        path_exists = os.path.isdir(proj['decoded_path'])
        border_color = "#7c3aed" if path_exists else "#dc2626"

        card = tk.Frame(self.scrollable, bg="#16213e",
                        highlightbackground=border_color, highlightthickness=1)
        card.pack(fill="x", pady=4, ipady=6)

        # Left: info
        info = tk.Frame(card, bg="#16213e")
        info.pack(side="left", fill="x", expand=True, padx=12, pady=4)

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
