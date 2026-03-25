import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import os
from typing import List, Dict

from tv_show_tracker import read_input_csv, write_output_csv, process_show, INPUT_CSV, OUTPUT_CSV


class ShowTrackerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TV Show Tracker")
        self.geometry("860x520")

        self.show_names: List[str] = []
        self.build_ui()
        self.reload_shows()

    def build_ui(self):
        frm_top = ttk.Frame(self)
        frm_top.pack(fill="x", padx=10, pady=8)

        btn_refresh = ttk.Button(frm_top, text="Refresh", command=self.reload_shows)
        btn_refresh.pack(side="left", padx=(0, 7))

        btn_add = ttk.Button(frm_top, text="Add Show", command=self.add_show)
        btn_add.pack(side="left", padx=(0, 7))

        btn_remove = ttk.Button(frm_top, text="Remove Selected", command=self.remove_selected)
        btn_remove.pack(side="left", padx=(0, 7))

        btn_update = ttk.Button(frm_top, text="Run Update", command=self.start_update_thread)
        btn_update.pack(side="left")

        self.status_var = tk.StringVar(value="Ready")
        lbl_status = ttk.Label(self, textvariable=self.status_var, anchor="w")
        lbl_status.pack(fill="x", padx=10, pady=(0, 6))

        self.updated_var = tk.StringVar(value="Last update: N/A")
        lbl_updated = ttk.Label(self, textvariable=self.updated_var, anchor="w")
        lbl_updated.pack(fill="x", padx=10, pady=(0, 6))

        columns = ("show_name", "tvmaze_status", "next_known_airdate")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", selectmode="extended")

        self.tree.heading("show_name", text="Show Name")
        self.tree.heading("tvmaze_status", text="Status")
        self.tree.heading("next_known_airdate", text="Next Air Date")

        self.tree.column("show_name", width=380, anchor="w")
        self.tree.column("tvmaze_status", width=120, anchor="center")
        self.tree.column("next_known_airdate", width=120, anchor="center")

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def reload_shows(self):
        # Prefer existing results if available for quick startup with previous status
        if os.path.exists(OUTPUT_CSV):
            try:
                with open(OUTPUT_CSV, 'r', encoding='utf-8', newline='') as f:
                    reader = __import__('csv').DictReader(f)
                    rows = [row for row in reader]
                if rows:
                    self.show_names = [row['show_name'] for row in rows if row.get('show_name')]
                    self.refresh_tree(rows)
                    self.status_var.set(f"Loaded {len(rows)} rows from {OUTPUT_CSV}")
                    self.update_last_modified_label()
                    return
            except Exception:
                # Fallback to input list if parsing fails
                pass

        try:
            self.show_names = read_input_csv(INPUT_CSV)
        except FileNotFoundError:
            self.show_names = []
            messagebox.showinfo("Info", f"{INPUT_CSV} not found. Starting with empty list.")

        self.refresh_tree()
        self.status_var.set(f"Loaded {len(self.show_names)} show(s)")
        self.update_last_modified_label()

    def refresh_tree(self, rows: List[Dict[str, str]] = None):
        for item in self.tree.get_children():
            self.tree.delete(item)

        if rows is None:
            # display bare titles until updated
            for show_name in self.show_names:
                self.tree.insert('', 'end', values=(show_name, '', ''))
        else:
            for r in rows:
                self.tree.insert('', 'end', values=(r.get('show_name', ''), r.get('tvmaze_status', ''), r.get('next_known_airdate', '')))

    def add_show(self):
        value = simpledialog.askstring("Add Show", "Enter show name:", parent=self)
        if not value:
            return
        show_name = value.strip()
        if not show_name:
            return

        if show_name in self.show_names:
            messagebox.showwarning("Warning", f"\"{show_name}\" is already in the list.")
            return

        self.show_names.append(show_name)
        self.save_show_list()
        self.refresh_tree()
        self.status_var.set(f"Added '{show_name}' ({len(self.show_names)} shows)")

    def remove_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Info", "No show selected.")
            return

        to_remove = []
        for item in selected:
            show_name = self.tree.item(item, 'values')[0]
            to_remove.append(show_name)

        if not messagebox.askyesno("Confirm", f"Remove {len(to_remove)} show(s)?"):
            return

        self.show_names = [name for name in self.show_names if name not in to_remove]
        self.save_show_list()
        self.refresh_tree()
        self.status_var.set(f"Removed {len(to_remove)} show(s). {len(self.show_names)} remaining")

    def save_show_list(self):
        with open(INPUT_CSV, 'w', encoding='utf-8', newline='') as f:
            f.write('show_name\n')
            for name in self.show_names:
                f.write(f'{name}\n')

    def update_last_modified_label(self):
        if os.path.exists(OUTPUT_CSV):
            modified_ts = os.path.getmtime(OUTPUT_CSV)
            self.updated_var.set(f"Last update: {__import__('datetime').datetime.fromtimestamp(modified_ts).strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            self.updated_var.set("Last update: N/A")

    def start_update_thread(self):
        thread = threading.Thread(target=self.run_update, daemon=True)
        thread.start()

    def run_update(self):
        self.status_var.set("Updating shows... (this may take a while)")
        rows = []

        for idx, show_name in enumerate(self.show_names, start=1):
            try:
                row = process_show(show_name)
                rows.append(row)
                self.status_var.set(f"{idx}/{len(self.show_names)}: {show_name} -> {row.get('tvmaze_status', '')}")
                self.refresh_tree(rows)
            except Exception as exc:
                messagebox.showerror("Error", f"Error processing {show_name}: {exc}")

        write_output_csv(OUTPUT_CSV, rows)
        self.status_var.set(f"Update complete: {len(rows)} rows written to {OUTPUT_CSV}")
        self.update_last_modified_label()
        self.update_last_modified_label()


if __name__ == '__main__':
    app = ShowTrackerGUI()
    app.mainloop()