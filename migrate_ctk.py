"""Transform claude-launcher.pyw from tk to customtkinter widgets."""
import re

with open('claude-launcher.pyw', 'r', encoding='utf-8') as f:
    content = f.read()

# Keep a backup reference
original = content

# ─── 1. Add import ───
content = content.replace(
    "import tkinter as tk\nfrom tkinter import ttk, filedialog, messagebox",
    "import tkinter as tk\nfrom tkinter import ttk, filedialog, messagebox\nimport customtkinter as ctk"
)

# ─── 2. Add appearance mode before color palette ───
content = content.replace(
    '# ── Color palette ──',
    '# ── CustomTkinter setup ──\nctk.set_appearance_mode("dark")\nctk.set_default_color_theme("dark-blue")\n\n# ── Color palette ──'
)

# ─── 3. Remove _hover_btn function ───
content = re.sub(
    r'\ndef _hover_btn\(widget, normal_bg, hover_bg\):\n    widget\.bind\("<Enter>".*?\n    widget\.bind\("<Leave>".*?\n',
    '\n',
    content
)

# ─── 4. Remove all _hover_btn(...) calls ───
# Matches lines like: _hover_btn(btn, SURFACE2, BORDER)
# or:        _hover_btn(save_btn, ACCENT, "#6b47d6")
content = re.sub(r'\s*_hover_btn\([^)]+\)\n', '\n', content)

# ─── 5. Change class inheritance ───
content = content.replace(
    'class SessionLauncher(tk.Tk):',
    'class SessionLauncher(ctk.CTk):'
)

# ─── 6. Change self.configure in __init__ ───
content = content.replace(
    'self.configure(bg=BG)',
    'self.configure(fg_color=BG)'
)

# ─── 7. Replace tk.Button constructors with ctk.CTkButton ───
# This is the most complex transformation - handle multi-line constructors

def transform_button(match):
    """Transform a tk.Button(...) call to ctk.CTkButton(...)."""
    full = match.group(0)
    # Replace class name
    full = full.replace('tk.Button(', 'ctk.CTkButton(', 1)
    # Map parameters
    full = re.sub(r'\brelief="flat"', '', full)
    full = re.sub(r'\brelief="[^"]*"', '', full)
    full = re.sub(r'\bbd=\d+', '', full)
    full = re.sub(r'\bbg=', 'fg_color=', full)
    full = re.sub(r'\bfg=', 'text_color=', full)
    full = re.sub(r'\bactivebackground="[^"]*"', '', full)
    full = re.sub(r'\bactiveforeground="[^"]*"', '', full)
    full = re.sub(r'\bpadx=\d+', '', full)
    full = re.sub(r'\bpady=\d+', '', full)
    full = re.sub(r'\bhighlightthickness=\d+', '', full)
    # Clean up extra commas
    full = re.sub(r',\s*,', ',', full)
    full = re.sub(r',\s*\)', ')', full)
    full = re.sub(r'\(\s*,', '(', full)
    return full

# Match tk.Button( ... ) including multi-line (greedy within parens)
# Use a paren-balancing approach
def replace_widget(content, old_class, new_class, remove_params=None, rename_params=None):
    """Replace widget class and transform parameters."""
    if remove_params is None:
        remove_params = []
    if rename_params is None:
        rename_params = {}

    result = []
    i = 0
    search = f'{old_class}('
    while i < len(content):
        pos = content.find(search, i)
        if pos == -1:
            result.append(content[i:])
            break

        # Check if this is a standalone call (not inside a string or comment)
        line_start = content.rfind('\n', 0, pos) + 1
        line_prefix = content[line_start:pos].lstrip()
        if line_prefix.startswith('#') or line_prefix.startswith('"') or line_prefix.startswith("'"):
            result.append(content[i:pos + len(search)])
            i = pos + len(search)
            continue

        # Find matching closing paren
        depth = 0
        j = pos + len(search) - 1  # at the opening paren
        while j < len(content):
            if content[j] == '(':
                depth += 1
            elif content[j] == ')':
                depth -= 1
                if depth == 0:
                    break
            elif content[j] == '"':
                # Skip string
                j += 1
                while j < len(content) and content[j] != '"':
                    if content[j] == '\\':
                        j += 1
                    j += 1
            elif content[j] == "'":
                j += 1
                while j < len(content) and content[j] != "'":
                    if content[j] == '\\':
                        j += 1
                    j += 1
            j += 1

        # Extract the full constructor call
        constructor = content[pos:j]

        # Replace class name
        new_constructor = constructor.replace(old_class + '(', new_class + '(', 1)

        # Remove params
        for param in remove_params:
            # Match param=value where value can be string, number, variable, or expression
            new_constructor = re.sub(rf',?\s*\b{param}="[^"]*"', '', new_constructor)
            new_constructor = re.sub(rf',?\s*\b{param}=\d+', '', new_constructor)
            new_constructor = re.sub(rf',?\s*\b{param}=[A-Za-z_][A-Za-z_0-9]*', '', new_constructor)

        # Rename params
        for old_param, new_param in rename_params.items():
            new_constructor = re.sub(rf'\b{old_param}=', f'{new_param}=', new_constructor)

        # Clean up comma issues
        # Remove leading comma after opening paren
        new_constructor = re.sub(r'\(\s*,\s*', '(', new_constructor)
        # Remove trailing comma before closing paren
        new_constructor = re.sub(r',\s*\)', ')', new_constructor)
        # Remove double commas
        new_constructor = re.sub(r',\s*,', ',', new_constructor)

        result.append(content[i:pos])
        result.append(new_constructor)
        i = j

    return ''.join(result)

# Transform tk.Button → ctk.CTkButton
content = replace_widget(content, 'tk.Button', 'ctk.CTkButton',
    remove_params=['relief', 'bd', 'activebackground', 'activeforeground',
                   'padx', 'pady', 'highlightthickness'],
    rename_params={'bg': 'fg_color', 'fg': 'text_color'})

# Transform tk.Entry → ctk.CTkEntry
content = replace_widget(content, 'tk.Entry', 'ctk.CTkEntry',
    remove_params=['relief', 'bd', 'insertbackground', 'highlightthickness',
                   'highlightbackground'],
    rename_params={'bg': 'fg_color', 'fg': 'text_color'})

# Transform tk.Checkbutton → ctk.CTkCheckBox
content = replace_widget(content, 'tk.Checkbutton', 'ctk.CTkCheckBox',
    remove_params=['relief', 'bd', 'selectcolor', 'activebackground',
                   'activeforeground', 'highlightthickness'],
    rename_params={'bg': 'fg_color', 'fg': 'text_color'})

# Transform tk.Radiobutton → ctk.CTkRadioButton
content = replace_widget(content, 'tk.Radiobutton', 'ctk.CTkRadioButton',
    remove_params=['relief', 'bd', 'selectcolor', 'activebackground',
                   'activeforeground', 'highlightthickness'],
    rename_params={'bg': 'fg_color', 'fg': 'text_color'})

# Transform tk.Label → ctk.CTkLabel
content = replace_widget(content, 'tk.Label', 'ctk.CTkLabel',
    remove_params=['relief', 'bd', 'highlightthickness'],
    rename_params={'bg': 'fg_color', 'fg': 'text_color'})

# Transform tk.Frame → ctk.CTkFrame
# BUT skip frames that are part of Canvas scrolling (project list container, preview popup)
# We'll do this selectively
content = replace_widget(content, 'tk.Frame', 'ctk.CTkFrame',
    remove_params=['relief', 'bd', 'highlightthickness', 'highlightbackground',
                   'highlightcolor'],
    rename_params={'bg': 'fg_color'})

# Transform tk.Toplevel → ctk.CTkToplevel
content = replace_widget(content, 'tk.Toplevel', 'ctk.CTkToplevel',
    remove_params=['relief', 'bd'],
    rename_params={'bg': 'fg_color'})

# Transform tk.Text → ctk.CTkTextbox
content = replace_widget(content, 'tk.Text', 'ctk.CTkTextbox',
    remove_params=['relief', 'bd', 'insertbackground', 'highlightthickness'],
    rename_params={'bg': 'fg_color', 'fg': 'text_color'})

# ─── 8. Fix .config() and .configure() calls on CTk widgets ───
# These need bg= → fg_color=, fg= → text_color= for CTk widgets
# But we can't easily know which widgets are CTk vs tk in a text-based transform
# So we'll handle specific known patterns

# Fix .config(bg=...) on status/frame widgets - common pattern
content = content.replace('.config(bg=', '.configure(fg_color=')
content = content.replace("stripe.config(bg=", "stripe.configure(fg_color=")

# Fix .config(fg=...)
content = content.replace('.config(fg=', '.configure(text_color=')

# Fix .config(text=...) - this is the same for CTk
content = content.replace('.config(text=', '.configure(text=')

# Fix .config(state=...)
content = content.replace('.config(state=', '.configure(state=')

# Fix popup.configure(bg=...) → popup.configure(fg_color=...)
content = content.replace('popup.configure(bg=', 'popup.configure(fg_color=')
content = content.replace('d.configure(bg=', 'd.configure(fg_color=')

# ─── 9. Fix cget calls ───
content = content.replace("stripe.cget('bg')", "stripe.cget('fg_color')")

# ─── 10. Fix isinstance checks to include CTk types ───
content = content.replace(
    'isinstance(self.focus_get(), (tk.Entry, ttk.Combobox, tk.Listbox,\n                                         tk.Checkbutton, tk.Radiobutton))',
    'isinstance(self.focus_get(), (tk.Entry, ctk.CTkEntry, ttk.Combobox, tk.Listbox,\n                                         ctk.CTkCheckBox, ctk.CTkRadioButton))'
)
content = content.replace(
    'isinstance(focused, (tk.Entry, ttk.Combobox, tk.Listbox, tk.Text,\n                                tk.Checkbutton, tk.Radiobutton))',
    'isinstance(focused, (tk.Entry, ctk.CTkEntry, ttk.Combobox, tk.Listbox, ctk.CTkTextbox,\n                                ctk.CTkCheckBox, ctk.CTkRadioButton))'
)
content = content.replace(
    'isinstance(sub, tk.Frame)',
    'isinstance(sub, (tk.Frame, ctk.CTkFrame))'
)
content = content.replace(
    'isinstance(label, tk.Label)',
    'isinstance(label, (tk.Label, ctk.CTkLabel))'
)

# ─── 11. Fix widget-specific issues ───

# CTkFrame pack_propagate - this still works on CTkFrame

# CTkLabel wraplength is supported

# CTkTextbox doesn't use padx/pady in constructor (uses internal padding)
# But these were already in the Text widget constructor - let's keep them as kwargs

# Fix tk.END references - CTkTextbox uses "end" string or tk.END
# These should work as-is since CTkTextbox wraps tk.Text

# Write the result
with open('claude-launcher.pyw', 'w', encoding='utf-8') as f:
    f.write(content)

print("Migration complete!")
print(f"Original: {len(original)} chars")
print(f"Transformed: {len(content)} chars")
