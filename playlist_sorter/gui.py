"""Tkinter desktop interface for the YouTube playlist sorter.

One main screen: sign in, see how many videos are waiting, Preview, Sort now.
Topics, playlist links and options live in a separate Settings window.
"""

import os
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import core

APP_TITLE = "Playlist Sorter"
UNMAPPED = "(not linked)"
SETUP_URL = "https://console.cloud.google.com/apis/credentials"

BG = "#f5f6f8"
CARD = "#ffffff"
BORDER = "#e3e5e8"
TEXT = "#1f2328"
MUTED = "#6b7280"
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
ACCENT_DISABLED = "#a9c0f3"
NEW = "#7c3aed"
BAD = "#b91c1c"
FONT = "Segoe UI"


def setup_style(root):
    root.configure(bg=BG)
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=BG, foreground=TEXT, font=(FONT, 10), bordercolor=BORDER)
    s.configure("TFrame", background=BG)
    s.configure("Card.TFrame", background=CARD)
    s.configure("TLabel", background=BG, foreground=TEXT)
    s.configure("Muted.TLabel", foreground=MUTED)
    s.configure("Error.TLabel", foreground=BAD)
    s.configure("Card.TLabel", background=CARD)
    s.configure("CardMuted.TLabel", background=CARD, foreground=MUTED)
    s.configure("Title.TLabel", font=(FONT, 20, "bold"))
    s.configure("Lead.TLabel", font=(FONT, 11), foreground=MUTED)
    s.configure("Header.TLabel", font=(FONT, 13, "bold"))
    s.configure("Big.TLabel", background=CARD, font=(FONT, 32, "bold"))

    s.configure("Primary.TButton", background=ACCENT, foreground="white", font=(FONT, 11, "bold"),
                padding=(20, 9), borderwidth=0, focuscolor=ACCENT)
    s.map("Primary.TButton", background=[("disabled", ACCENT_DISABLED), ("active", ACCENT_HOVER)],
          foreground=[("disabled", "white")])
    s.configure("Secondary.TButton", background=CARD, foreground=TEXT, font=(FONT, 11),
                padding=(20, 8), borderwidth=1, focuscolor=CARD)
    s.map("Secondary.TButton", background=[("active", "#eef0f3")], foreground=[("disabled", MUTED)])
    s.configure("Link.TButton", background=BG, foreground=ACCENT, borderwidth=0, padding=(4, 2),
                focuscolor=BG)
    s.map("Link.TButton", background=[("active", BG)], foreground=[("active", ACCENT_HOVER)])
    s.configure("TButton", padding=(10, 4))

    s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                rowheight=28, borderwidth=0, font=(FONT, 10))
    s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # no border
    s.configure("Horizontal.TProgressbar", troughcolor=BORDER, background=ACCENT,
                borderwidth=0, thickness=6, lightcolor=ACCENT, darkcolor=ACCENT)
    s.configure("TNotebook", background=BG, borderwidth=0)
    s.configure("TNotebook.Tab", padding=(16, 7), background="#e9ebee", foreground=MUTED,
                bordercolor=BORDER, lightcolor=BG)
    s.map("TNotebook.Tab", background=[("selected", BG)], foreground=[("selected", TEXT)],
          expand=[("selected", (0, 0, 0, 0))])
    s.configure("Vertical.TScrollbar", background="#d5d8dd", troughcolor=CARD, bordercolor=CARD,
                lightcolor="#d5d8dd", darkcolor="#d5d8dd", arrowcolor=MUTED, gripcount=0, arrowsize=12)
    s.map("Vertical.TScrollbar", background=[("active", "#b8bcc4")])
    s.configure("TCheckbutton", background=BG)
    s.map("TCombobox", fieldbackground=[("readonly", CARD)], selectbackground=[("readonly", CARD)],
          selectforeground=[("readonly", TEXT)])
    s.configure("TLabelframe", background=BG)
    s.configure("TLabelframe.Label", background=BG, foreground=MUTED)


def card(parent, padding=18):
    """A white panel with a thin border. Returns the inner frame to put widgets in."""
    outer = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    inner = ttk.Frame(outer, style="Card.TFrame", padding=padding)
    inner.pack(fill="both", expand=True)
    inner.outer = outer
    return inner


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("820x640")
        self.minsize(640, 500)
        setup_style(self)

        self.config_data = core.load_config()
        self.topics = core.load_topics()
        self.youtube = None
        self.playlists = []          # [{'id', 'title'}] from the account
        self.worker = None
        self.busy = False
        self.stop_event = threading.Event()
        self.events = queue.Queue()  # worker thread -> UI thread
        self.settings_window = None
        self.last_preview = None
        self.groups = {}             # result group key -> [tree iid, count, label]

        auto = self.config_data["auto_create"]
        self.secrets_var = tk.StringVar(value=self.config_data.get("client_secrets_file")
                                        or core.DEFAULT_CLIENT_SECRETS)
        self.source_var = tk.StringVar(value=self.config_data.get("source_playlist_name")
                                       or core.DEFAULT_SOURCE_PLAYLIST)
        self.remove_var = tk.BooleanVar(value=self.config_data.get("remove_from_source", True))
        self.max_var = tk.StringVar(value=str(self.config_data.get("max_videos_per_run", 50)))
        self.auto_var = tk.BooleanVar(value=auto["enabled"])
        self.auto_min_var = tk.StringVar(value=str(auto["min_videos"]))
        self.auto_max_new_var = tk.StringVar(value=str(auto["max_new_per_run"]))
        self.auto_max_topics_var = tk.StringVar(value=str(auto["max_topics"]))

        self.account_var = tk.StringVar(value="")
        self.count_var = tk.StringVar(value="–")
        self.count_caption = tk.StringVar(value="")
        self.progress_text = tk.StringVar(value="")
        self.results_title = tk.StringVar(value="")
        self.results_note = tk.StringVar(value="")

        self._build_header()
        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True)
        self.welcome = self._build_welcome()
        self.main = self._build_main()
        self._show(self.welcome)
        self._update_secrets_label()

        self.after(100, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Return>", lambda _e: self.sign_in() if not self.youtube else None)
        if os.path.exists(core.TOKEN_FILE):
            self.after(200, self.sign_in)

    # ------------------------------------------------------------- layout --

    def _build_header(self):
        header = ttk.Frame(self, padding=(20, 14, 20, 4))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="Header.TLabel").pack(side="left")
        ttk.Button(header, text="Settings", style="Link.TButton",
                   command=self.open_settings).pack(side="right")
        ttk.Label(header, textvariable=self.account_var, style="Muted.TLabel").pack(side="right", padx=12)

    def _show(self, screen):
        for child in self.body.winfo_children():
            child.pack_forget()
        screen.pack(fill="both", expand=True)

    def _build_welcome(self):
        frame = ttk.Frame(self.body)
        box = ttk.Frame(frame)
        box.place(relx=0.5, rely=0.42, anchor="center")
        ttk.Label(box, text="Tidy up your YouTube playlists", style="Title.TLabel").pack()
        ttk.Label(box, style="Lead.TLabel", justify="center",
                  text="Add videos to a playlist called “To Sort”.\n"
                       "This app files each one into the right topic playlist for you.").pack(pady=(8, 26))
        self.signin_btn = ttk.Button(box, text="Sign in with Google", style="Primary.TButton",
                                     command=self.sign_in)
        self.signin_btn.pack()
        self.secrets_label = ttk.Label(box, style="Muted.TLabel")
        self.secrets_label.pack(pady=(18, 0))
        links = ttk.Frame(box)
        links.pack()
        ttk.Button(links, text="Choose credentials file…", style="Link.TButton",
                   command=self._browse_secrets).pack(side="left")
        ttk.Button(links, text="How do I get one?", style="Link.TButton",
                   command=lambda: webbrowser.open(SETUP_URL)).pack(side="left")
        return frame

    def _build_main(self):
        frame = ttk.Frame(self.body, padding=(20, 8, 20, 14))

        # Status card: count + actions
        status = card(frame)
        status.outer.pack(fill="x")
        top = ttk.Frame(status, style="Card.TFrame")
        top.pack(fill="x")
        left = ttk.Frame(top, style="Card.TFrame")
        left.pack(side="left")
        ttk.Label(left, textvariable=self.count_var, style="Big.TLabel").pack(anchor="w")
        ttk.Label(left, textvariable=self.count_caption, style="CardMuted.TLabel").pack(anchor="w")

        actions = ttk.Frame(top, style="Card.TFrame")
        actions.pack(side="right")
        self.preview_btn = ttk.Button(actions, text="Preview", style="Secondary.TButton",
                                      command=lambda: self.start_run(dry_run=True))
        self.sort_btn = ttk.Button(actions, text="Sort now", style="Primary.TButton",
                                   command=lambda: self.start_run(dry_run=False))
        self.stop_btn = ttk.Button(actions, text="Stop", style="Secondary.TButton", command=self.stop_run)
        self.preview_btn.pack(side="left", padx=(0, 8))
        self.sort_btn.pack(side="left")

        self.progress_row = ttk.Frame(status, style="Card.TFrame")
        self.progress = ttk.Progressbar(self.progress_row, mode="determinate")
        self.progress.pack(fill="x", pady=(14, 4))
        ttk.Label(self.progress_row, textvariable=self.progress_text, style="CardMuted.TLabel").pack(anchor="w")

        # Results
        head = ttk.Frame(frame)
        head.pack(fill="x", pady=(18, 6))
        ttk.Label(head, textvariable=self.results_title, style="Header.TLabel").pack(side="left")
        ttk.Label(head, textvariable=self.results_note, style="Muted.TLabel").pack(side="left", padx=10)

        results = card(frame, padding=(10, 8))
        results.outer.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(results, show="tree", selectmode="none")
        scroll = ttk.Scrollbar(results, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        bold = (FONT, 10, "bold")
        self.tree.tag_configure("group", font=bold)
        self.tree.tag_configure("new", font=bold, foreground=NEW)
        self.tree.tag_configure("stay", font=bold, foreground=MUTED)
        self.tree.tag_configure("error", font=bold, foreground=BAD)
        self.tree.tag_configure("video", foreground=TEXT)
        self.tree.bind("<Button-1>", self._toggle_group)
        self.empty_label = ttk.Label(results, style="CardMuted.TLabel", justify="center",
                                     text="Click Preview to see where each video would go.\n"
                                          "Nothing changes in your account until you click Sort now.")
        self.empty_label.place(relx=0.5, rely=0.45, anchor="center")

        # Details (log), hidden by default
        foot = ttk.Frame(frame)
        foot.pack(fill="x", pady=(8, 0))
        self.details_btn = ttk.Button(foot, text="Show details", style="Link.TButton",
                                      command=self._toggle_details)
        self.details_btn.pack(side="left")
        self.log_text = scrolledtext.ScrolledText(frame, height=7, state="disabled", font=("Consolas", 9),
                                                  relief="flat", highlightthickness=1,
                                                  highlightbackground=BORDER)
        return frame

    def _toggle_group(self, event):
        """Expand or collapse a group by clicking anywhere on its row, not just the arrow."""
        row = self.tree.identify_row(event.y)
        if row and not self.tree.parent(row) and "indicator" not in self.tree.identify_element(event.x, event.y):
            self.tree.item(row, open=not self.tree.item(row, "open"))
            return "break"
        return None

    def _toggle_details(self):
        if self.log_text.winfo_ismapped():
            self.log_text.pack_forget()
            self.details_btn.configure(text="Show details")
        else:
            self.log_text.pack(fill="x", pady=(4, 0))
            self.details_btn.configure(text="Hide details")

    def _set_busy(self, busy, dry_run=False):
        self.busy = busy
        if busy:
            self.preview_btn.pack_forget()
            self.sort_btn.pack_forget()
            self.stop_btn.configure(state="normal")
            self.stop_btn.pack(side="left")
            self.progress.configure(value=0, maximum=1)
            self.progress_text.set("Reading your playlist…")
            self.progress_row.pack(fill="x")
        else:
            self.stop_btn.pack_forget()
            self.preview_btn.pack(side="left", padx=(0, 8))
            self.sort_btn.pack(side="left")
            self.progress_row.pack_forget()

    # ------------------------------------------------------------ helpers --

    def log(self, msg):
        """Thread-safe logging."""
        self.events.put(("log", msg))

    def _append_log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _drain_events(self):
        try:
            while True:
                kind, *payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(payload[0])
                elif kind == "result":
                    self._add_result(*payload)
                elif kind == "progress":
                    done, total = payload
                    self.progress.configure(maximum=max(total, 1), value=done)
                    self.progress_text.set(f"{self._run_verb} {done} of {total} videos…")
                elif kind == "call":
                    payload[0]()
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _in_background(self, fn, on_done=None, on_error=None):
        """Run ``fn`` on a worker thread; deliver the result or error on the UI thread."""
        def target():
            try:
                result = fn()
                if on_done:
                    self.events.put(("call", lambda: on_done(result)))
            except Exception as e:  # noqa: BLE001 - surface any failure to the user
                msg = str(e)
                self.log(f"Error: {msg}")

                def report():
                    if on_error:
                        on_error()
                    messagebox.showerror(APP_TITLE, msg, parent=self)
                self.events.put(("call", report))
        threading.Thread(target=target, daemon=True).start()

    def persist(self):
        """Write all settings held in the UI to the config file."""
        c = self.config_data
        c["client_secrets_file"] = self.secrets_var.get()
        c["source_playlist_name"] = self.source_var.get().strip() or core.DEFAULT_SOURCE_PLAYLIST
        c["remove_from_source"] = self.remove_var.get()
        c["max_videos_per_run"] = _positive_int(self.max_var, c.get("max_videos_per_run", 50))
        auto = c["auto_create"]
        auto["enabled"] = self.auto_var.get()
        auto["min_videos"] = _positive_int(self.auto_min_var, auto["min_videos"])
        auto["max_new_per_run"] = _positive_int(self.auto_max_new_var, auto["max_new_per_run"])
        auto["max_topics"] = _positive_int(self.auto_max_topics_var, auto["max_topics"])
        core.save_config(c)

    def playlist_title(self, playlist_id):
        for pl in self.playlists:
            if pl["id"] == playlist_id:
                return pl["title"]
        return None

    def topic_display_name(self, topic):
        pid = self.config_data.get("topic_playlists", {}).get(topic)
        return self.playlist_title(pid) or topic.title()

    @property
    def source_name(self):
        return self.source_var.get().strip() or core.DEFAULT_SOURCE_PLAYLIST

    # ------------------------------------------------------------ account --

    def _update_secrets_label(self):
        path = self.secrets_var.get()
        if os.path.exists(path):
            self.secrets_label.configure(text=f"Using credentials from {os.path.basename(path)}",
                                         style="Muted.TLabel")
        else:
            self.secrets_label.configure(text="No credentials file found (client_secret.json)",
                                         style="Error.TLabel")

    def _browse_secrets(self):
        path = filedialog.askopenfilename(title="Select client_secret.json",
                                          filetypes=[("JSON files", "*.json")])
        if path:
            self.secrets_var.set(path)
            self.persist()
            self._update_secrets_label()

    def sign_in(self):
        if self.busy:
            return
        self.persist()
        self.signin_btn.configure(state="disabled", text="Waiting for browser…")

        def done(youtube):
            self.youtube = youtube
            self.account_var.set("Signed in")
            self.log("Signed in.")
            self._show(self.main)
            self.refresh()

        def failed():
            self.signin_btn.configure(state="normal", text="Sign in with Google")

        def work():
            return core.get_authenticated_service(self.secrets_var.get(), log=self.log)

        self._in_background(work, lambda yt: (failed(), done(yt)), failed)

    def sign_out(self):
        core.sign_out()
        self.youtube = None
        self.playlists = []
        self.account_var.set("")
        self.log("Signed out; saved token removed.")
        self.signin_btn.configure(state="normal", text="Sign in with Google")
        self._show(self.welcome)

    # ---------------------------------------------------------- playlists --

    def refresh(self, on_done=None):
        """Reload playlists, resolve the source and topic links, and count waiting videos."""
        if not self.youtube:
            return
        self.persist()
        self.count_var.set("…")
        self.count_caption.set("Loading your playlists…")
        name = self.source_name

        def work():
            playlists = core.list_my_playlists(self.youtube)
            source_id = core.find_playlist(playlists, name)
            videos = core.get_playlist_videos(self.youtube, source_id) if source_id else []
            return playlists, source_id, videos

        def done(result):
            playlists, source_id, videos = result
            self.playlists = playlists
            self.config_data["source_playlist_id"] = source_id
            valid_ids = {p["id"] for p in playlists}
            mapping = {t: pid for t, pid in self.config_data.get("topic_playlists", {}).items()
                       if pid in valid_ids and t in self.topics}
            others = [p for p in playlists if p["id"] != source_id]
            for topic, pid in core.auto_map_topics(others, self.topics).items():
                mapping.setdefault(topic, pid)
            self.config_data["topic_playlists"] = mapping
            core.save_config(self.config_data)
            self.log(f"Loaded {len(playlists)} playlists; {len(mapping)} topics linked.")

            if source_id:
                processed = set(self.config_data.get("processed_videos", []))
                waiting = sum(1 for v in videos if v["id"] not in processed)
                self.count_var.set(str(waiting))
                self.count_caption.set(f"video{'s' if waiting != 1 else ''} waiting in “{name}”")
            else:
                self.count_var.set("0")
                self.count_caption.set(f"No playlist called “{name}” yet")
                if messagebox.askyesno(APP_TITLE, f"You don't have a playlist called “{name}” yet.\n\n"
                                                  "Create it now? (It will be private.)", parent=self):
                    self._create_source_playlist(name)
            if self.settings_window:
                self.settings_window.refresh_mapping()
            if on_done:
                on_done()

        self._in_background(work, done, lambda: self.count_caption.set("Couldn't load playlists"))

    def _create_source_playlist(self, name):
        def done(pid):
            self.playlists.append({"id": pid, "title": name})
            self.config_data["source_playlist_id"] = pid
            core.save_config(self.config_data)
            self.count_caption.set(f"videos waiting in “{name}” — add some on YouTube, then come back")
            self.log(f"Created playlist '{name}'.")
        self._in_background(lambda: core.create_playlist(
            self.youtube, name, "Videos pending categorization"), done)

    # ---------------------------------------------------------------- run --

    def start_run(self, dry_run):
        if self.busy or not self.youtube:
            return
        if self.settings_window:
            self.settings_window.commit()
        self.persist()
        if not self.config_data.get("source_playlist_id"):
            messagebox.showinfo(APP_TITLE, f"Create a playlist called “{self.source_name}” first.", parent=self)
            return
        if not self.config_data.get("topic_playlists") and not self.auto_var.get():
            messagebox.showinfo(APP_TITLE, "None of your topics is linked to a playlist yet.\n"
                                           "Open Settings → Playlists to link them.", parent=self)
            return
        if not dry_run and not messagebox.askyesno(APP_TITLE, self._confirm_text(), parent=self):
            return

        self._clear_results()
        self._known_topics = set(self.config_data.get("topic_playlists", {}))
        self._run_verb = "Checked" if dry_run else "Sorted"
        self.stop_event.clear()
        self._set_busy(True)
        self.log("Previewing…" if dry_run else "Sorting…")

        topics = {t: list(k) for t, k in self.topics.items()}
        auto = dict(self.config_data["auto_create"])
        remove = self.remove_var.get()
        max_videos = self.config_data["max_videos_per_run"]

        def work():
            return core.sort_playlist(
                self.youtube, self.config_data, topics, dry_run=dry_run,
                remove_from_source=remove, max_videos=max_videos, auto_create=auto, log=self.log,
                on_result=lambda v, t, a: self.events.put(("result", v, t, a)),
                on_progress=lambda d, n: self.events.put(("progress", d, n)),
                should_stop=self.stop_event.is_set,
            )

        def done(summary):
            self._set_busy(False)
            self._merge_new_topics(summary)
            self._show_summary(summary)
            if summary["dry_run"]:
                self.last_preview = summary
            else:
                self.last_preview = None
                self.refresh()

        self._in_background(work, done, lambda: self._set_busy(False))

    def _confirm_text(self):
        verb = "Move" if self.remove_var.get() else "Copy"
        p = self.last_preview
        if p:
            if not p["sorted"]:
                return "The last preview found nothing to sort. Run anyway?"
            text = f"{verb} {p['sorted']} video{'s' if p['sorted'] != 1 else ''} into their playlists"
            if p["new_topics"]:
                n = len(p["new_topics"])
                text += f" and create {n} new playlist{'s' if n != 1 else ''}"
            return text + "?\n\nThis changes your YouTube account."
        return f"{verb} videos from “{self.source_name}” into your topic playlists?\n\n" \
               "This changes your YouTube account. Tip: click Preview first to see what will happen."

    def stop_run(self):
        self.stop_event.set()
        self.stop_btn.configure(state="disabled")
        self.progress_text.set("Stopping after the current video…")

    def _merge_new_topics(self, summary):
        new = summary.get("new_topics") or []
        if not new or summary["dry_run"]:
            return
        for t in new:
            self.topics[t["name"]] = list(t["keywords"])
            if not self.playlist_title(t["playlist_id"]):
                self.playlists.append({"id": t["playlist_id"], "title": t["title"]})
        if self.settings_window:
            self.settings_window.refresh_topics()
            self.settings_window.refresh_mapping()

    # ------------------------------------------------------------ results --

    def _clear_results(self):
        self.tree.delete(*self.tree.get_children())
        self.groups = {}
        self.empty_label.place_forget()
        self.results_title.set("")
        self.results_note.set("")

    def _group(self, key, label, tag):
        if key not in self.groups:
            iid = self.tree.insert("", "end", text=label, open=False, tags=(tag,))
            self.groups[key] = [iid, 0, label]
            # Keep the "left behind" groups at the bottom.
            for tail in ("stay", "error", "skipped"):
                if tail in self.groups and tail != key:
                    self.tree.move(self.groups[tail][0], "", "end")
        group = self.groups[key]
        group[1] += 1
        self.tree.item(group[0], text=f"{group[2]}     {group[1]} video{'s' if group[1] != 1 else ''}")
        return group[0]

    def _add_result(self, video, topics, action):
        title = video["title"]
        if action.startswith(("would", "moved", "copied")):
            for topic in topics:
                if topic in self._known_topics:
                    parent = self._group(("topic", topic), self.topic_display_name(topic), "group")
                else:
                    parent = self._group(("topic", topic), f"★  {topic.title()}  (new playlist)", "new")
                self.tree.insert(parent, "end", text=title, tags=("video",))
        elif action == "already processed":
            parent = self._group("skipped", "Already sorted before", "stay")
            self.tree.insert(parent, "end", text=title, tags=("video",))
        elif action == "error":
            parent = self._group("error", "Couldn't be moved (see details)", "error")
            self.tree.insert(parent, "end", text=title, tags=("video",))
        else:
            parent = self._group("stay", f"Staying in “{self.source_name}”", "stay")
            if action == "no playlist mapped":
                title += f"   — matches {', '.join(topics)}, but no playlist is linked"
            self.tree.insert(parent, "end", text=title, tags=("video",))

    def _show_summary(self, s):
        if s["total"] == 0:
            self.results_title.set("Nothing to sort")
            self.results_note.set(f"“{self.source_name}” is empty.")
            return
        stopped = " (stopped early)" if s["sorted"] + s["unmatched"] + s["skipped"] + s["errors"] < s["total"] else ""
        if s["dry_run"]:
            self.results_title.set("Preview" + stopped)
            self.results_note.set(f"{s['sorted']} would be sorted, {s['unmatched']} would stay. "
                                  "Nothing has changed yet — click Sort now to apply.")
        else:
            self.results_title.set("Done" + stopped)
            note = f"{s['sorted']} sorted, {s['unmatched']} left in “{self.source_name}”."
            if s["errors"]:
                note += f" {s['errors']} couldn't be moved."
            self.results_note.set(note)
        self.log(f"Done. Quota used ≈ {s.get('quota_used', 0)} of {core.DAILY_QUOTA:,} daily units.")

    # ----------------------------------------------------------- settings --

    def open_settings(self):
        if self.settings_window:
            self.settings_window.lift()
            self.settings_window.focus_set()
        else:
            self.settings_window = SettingsWindow(self)

    def settings_closed(self, source_changed):
        self.settings_window = None
        self.persist()
        self._update_secrets_label()
        if source_changed and self.youtube:
            self.refresh()

    def _on_close(self):
        if self.busy and not messagebox.askyesno(APP_TITLE, "Sorting is still running. Quit anyway?",
                                                 parent=self):
            return
        self.stop_event.set()
        try:
            if self.settings_window:
                self.settings_window.commit()
            core.save_topics(self.topics)
            self.persist()
        finally:
            self.destroy()


class SettingsWindow(tk.Toplevel):
    """Topics & keywords, playlist links, and options."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Settings")
        self.geometry("720x560")
        self.minsize(600, 440)
        self.configure(bg=BG)
        self.transient(app)
        self._source_before = app.source_name
        self._current_topic = None

        bar = ttk.Frame(self, padding=14)
        bar.pack(side="bottom", fill="x")
        ttk.Button(bar, text="Done", style="Primary.TButton", command=self.close).pack(side="right")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=14, pady=(14, 0))
        notebook.add(self._build_topics(notebook), text="Topics")
        notebook.add(self._build_playlists(notebook), text="Playlists")
        notebook.add(self._build_options(notebook), text="Options")

        self.refresh_topics()
        self.refresh_mapping()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _e: self.close())

    # ---------------------------------------------------------- topics tab --

    def _build_topics(self, parent):
        frame = ttk.Frame(parent, padding=14)
        ttk.Label(frame, style="Muted.TLabel", wraplength=640, justify="left",
                  text="A video goes to a topic when its title or description contains one of the "
                       "topic's keywords.").pack(anchor="w", pady=(0, 10))
        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        self.topic_list = tk.Listbox(left, width=22, exportselection=False, activestyle="none",
                                     relief="flat", highlightthickness=1, highlightbackground=BORDER,
                                     highlightcolor=ACCENT, selectbackground=ACCENT,
                                     font=(FONT, 10), bg=CARD)
        self.topic_list.pack(fill="y", expand=True)
        self.topic_list.bind("<<ListboxSelect>>", lambda _e: self._select_topic())
        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="+ Add", command=self._add_topic).pack(side="left")
        ttk.Button(btns, text="Rename", command=self._rename_topic).pack(side="left", padx=4)
        ttk.Button(btns, text="Delete", command=self._delete_topic).pack(side="left")

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(14, 0))
        ttk.Label(right, text="Keywords — one per line").pack(anchor="w")
        self.keywords_text = scrolledtext.ScrolledText(right, wrap="word", height=8, font=(FONT, 10),
                                                       undo=True, relief="flat", highlightthickness=1,
                                                       highlightbackground=BORDER, highlightcolor=ACCENT)
        self.keywords_text.pack(fill="both", expand=True, pady=(4, 10))
        self.keywords_text.bind("<FocusOut>", lambda _e: self.commit())

        ttk.Label(right, text="Try a video title").pack(anchor="w")
        self.test_var = tk.StringVar()
        self.test_result = tk.StringVar()
        ttk.Entry(right, textvariable=self.test_var).pack(fill="x", pady=(4, 2))
        ttk.Label(right, textvariable=self.test_result, style="Muted.TLabel").pack(anchor="w")
        self.test_var.trace_add("write", lambda *_: self._update_test())
        return frame

    def refresh_topics(self, select=None):
        select = select or self._current_topic
        self.topic_list.delete(0, "end")
        names = list(self.app.topics)
        for topic in names:
            self.topic_list.insert("end", topic)
        if names:
            idx = names.index(select) if select in names else 0
            self.topic_list.selection_set(idx)
            self._current_topic = names[idx]
        else:
            self._current_topic = None
        self.keywords_text.delete("1.0", "end")
        if self._current_topic:
            self.keywords_text.insert("1.0", "\n".join(self.app.topics[self._current_topic]))

    def commit(self):
        """Store the keywords currently being edited."""
        topic = self._current_topic
        if topic and topic in self.app.topics:
            raw = self.keywords_text.get("1.0", "end").replace(",", "\n")
            self.app.topics[topic] = [k.strip() for k in raw.splitlines() if k.strip()]
        self._update_test()

    def _select_topic(self):
        self.commit()
        sel = self.topic_list.curselection()
        if sel:
            self.refresh_topics(select=self.topic_list.get(sel[0]))

    def _ask_name(self, title, initial=""):
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.configure(bg=BG)
        dialog.transient(self)
        dialog.resizable(False, False)
        var = tk.StringVar(value=initial)
        result = {}
        box = ttk.Frame(dialog, padding=16)
        box.pack()
        ttk.Label(box, text="Topic name").pack(anchor="w")
        entry = ttk.Entry(box, textvariable=var, width=32)
        entry.pack(pady=(4, 12))
        entry.focus_set()
        entry.select_range(0, "end")

        def ok(_e=None):
            result["name"] = var.get().strip().lower()
            dialog.destroy()
        ttk.Button(box, text="OK", style="Primary.TButton", command=ok).pack(anchor="e")
        dialog.bind("<Return>", ok)
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.grab_set()
        self.wait_window(dialog)
        return result.get("name")

    def _add_topic(self):
        self.commit()
        name = self._ask_name("Add topic")
        if not name:
            return
        if name in self.app.topics:
            messagebox.showinfo("Settings", f"“{name}” already exists.", parent=self)
            return
        self.app.topics[name] = [name]
        self.refresh_topics(select=name)
        self.refresh_mapping()
        self.keywords_text.focus_set()

    def _rename_topic(self):
        old = self._current_topic
        if not old:
            return
        self.commit()
        new = self._ask_name("Rename topic", old)
        if not new or new == old or new in self.app.topics:
            return
        self.app.topics = {(new if t == old else t): k for t, k in self.app.topics.items()}
        mapping = self.app.config_data.get("topic_playlists", {})
        if old in mapping:
            mapping[new] = mapping.pop(old)
        self.refresh_topics(select=new)
        self.refresh_mapping()

    def _delete_topic(self):
        topic = self._current_topic
        if topic and messagebox.askyesno("Settings", f"Delete the topic “{topic}”?\n\n"
                                                     "Its playlist on YouTube is not touched.", parent=self):
            del self.app.topics[topic]
            self.app.config_data.get("topic_playlists", {}).pop(topic, None)
            self._current_topic = None
            self.refresh_topics()
            self.refresh_mapping()

    def _update_test(self):
        title = self.test_var.get().strip()
        if not title:
            self.test_result.set("")
            return
        topics = core.categorize_video({"title": title, "description": ""}, self.app.topics)
        self.test_result.set("→ " + (", ".join(topics) if topics else "no topic (stays in To Sort)"))

    # ------------------------------------------------------- playlists tab --

    def _build_playlists(self, parent):
        frame = ttk.Frame(parent, padding=14)
        ttk.Label(frame, style="Muted.TLabel", wraplength=640, justify="left",
                  text="Each topic sends videos to one playlist. Playlists whose name contains the "
                       "topic are linked automatically.").pack(anchor="w", pady=(0, 10))
        self.mapping_tree = ttk.Treeview(frame, columns=("playlist",), show="tree headings",
                                         selectmode="browse", height=10)
        self.mapping_tree.heading("#0", text="Topic", anchor="w")
        self.mapping_tree.heading("playlist", text="Playlist", anchor="w")
        self.mapping_tree.column("#0", width=200, stretch=False)
        self.mapping_tree.pack(fill="both", expand=True)
        self.mapping_tree.bind("<<TreeviewSelect>>", lambda _e: self._sync_combo())
        self.mapping_tree.tag_configure("unlinked", foreground=MUTED)

        edit = ttk.Frame(frame)
        edit.pack(fill="x", pady=(10, 0))
        ttk.Label(edit, text="Send selected topic to:").pack(side="left")
        self.combo = ttk.Combobox(edit, state="readonly", width=34)
        self.combo.pack(side="left", padx=6)
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_combo())
        ttk.Button(edit, text="Create new playlist", command=self._create_playlist).pack(side="left")
        self.signin_hint = ttk.Label(frame, style="Muted.TLabel",
                                     text="Sign in to see and link your playlists.")
        return frame

    def refresh_mapping(self):
        app = self.app
        selected = self._selected()
        self.mapping_tree.delete(*self.mapping_tree.get_children())
        mapping = app.config_data.get("topic_playlists", {})
        for topic in app.topics:
            title = app.playlist_title(mapping.get(topic))
            self.mapping_tree.insert("", "end", iid=topic, text=topic, values=(title or UNMAPPED,),
                                     tags=() if title else ("unlinked",))
        source_id = app.config_data.get("source_playlist_id")
        self.combo.configure(values=[UNMAPPED] + [p["title"] for p in app.playlists if p["id"] != source_id])
        if selected in app.topics:
            self.mapping_tree.selection_set(selected)
        if app.youtube:
            self.signin_hint.pack_forget()
        else:
            self.signin_hint.pack(anchor="w", pady=(8, 0))

    def _selected(self):
        sel = self.mapping_tree.selection()
        return sel[0] if sel else None

    def _sync_combo(self):
        topic = self._selected()
        if topic:
            pid = self.app.config_data.get("topic_playlists", {}).get(topic)
            self.combo.set(self.app.playlist_title(pid) or UNMAPPED)

    def _apply_combo(self):
        topic = self._selected()
        if not topic:
            return
        title = self.combo.get()
        mapping = self.app.config_data.setdefault("topic_playlists", {})
        if title == UNMAPPED:
            mapping.pop(topic, None)
        else:
            mapping[topic] = next(p["id"] for p in self.app.playlists if p["title"] == title)
        core.save_config(self.app.config_data)
        self.refresh_mapping()

    def _create_playlist(self):
        topic = self._selected()
        if not topic:
            messagebox.showinfo("Settings", "Select a topic first.", parent=self)
            return
        if not self.app.youtube:
            messagebox.showinfo("Settings", "Sign in first.", parent=self)
            return
        title = topic.title()
        if not messagebox.askyesno("Settings", f"Create a private playlist called “{title}”?", parent=self):
            return

        def done(pid):
            self.app.playlists.append({"id": pid, "title": title})
            self.app.config_data.setdefault("topic_playlists", {})[topic] = pid
            core.save_config(self.app.config_data)
            if self.winfo_exists():
                self.refresh_mapping()
            self.app.log(f"Created playlist '{title}'.")
        self.app._in_background(lambda: core.create_playlist(self.app.youtube, title), done)

    # --------------------------------------------------------- options tab --

    def _build_options(self, parent):
        app = self.app
        frame = ttk.Frame(parent, padding=14)

        sorting = ttk.LabelFrame(frame, text="Sorting", padding=12)
        sorting.pack(fill="x")
        row = ttk.Frame(sorting)
        row.pack(fill="x")
        ttk.Label(row, text="Playlist to sort from:").pack(side="left")
        ttk.Entry(row, textvariable=app.source_var, width=28).pack(side="left", padx=6)
        ttk.Checkbutton(sorting, text="Remove videos from it once they're sorted",
                        variable=app.remove_var).pack(anchor="w", pady=(8, 0))
        row = ttk.Frame(sorting)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Sort at most").pack(side="left")
        ttk.Spinbox(row, from_=1, to=5000, textvariable=app.max_var, width=6).pack(side="left", padx=6)
        ttk.Label(row, text=f"videos per run  (YouTube allows about {core.DAILY_QUOTA // 100} a day)",
                  style="Muted.TLabel").pack(side="left")

        auto = ttk.LabelFrame(frame, text="New playlists", padding=12)
        auto.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(auto, text="Create new playlists for videos that don't fit any topic",
                        variable=app.auto_var).pack(anchor="w")
        row = ttk.Frame(auto)
        row.pack(fill="x", pady=(8, 0))
        for label, var in (("Only when at least", app.auto_min_var),
                           ("similar videos  ·  at most", app.auto_max_new_var),
                           ("new per run  ·  stop at", app.auto_max_topics_var)):
            ttk.Label(row, text=label).pack(side="left")
            ttk.Spinbox(row, from_=1, to=200, textvariable=var, width=4).pack(side="left", padx=5)
        ttk.Label(row, text="topics").pack(side="left")
        ttk.Label(auto, style="Muted.TLabel", wraplength=640, justify="left",
                  text="The minimum grows automatically with a bigger backlog and more topics, "
                       "so you won't get lots of tiny playlists.").pack(anchor="w", pady=(6, 0))

        account = ttk.LabelFrame(frame, text="Account", padding=12)
        account.pack(fill="x", pady=(12, 0))
        row = ttk.Frame(account)
        row.pack(fill="x")
        ttk.Label(row, text="Credentials file:").pack(side="left")
        ttk.Entry(row, textvariable=app.secrets_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Browse…", command=app._browse_secrets).pack(side="left")
        ttk.Button(row, text="Sign out", command=self._sign_out).pack(side="left", padx=(6, 0))
        return frame

    def _sign_out(self):
        if messagebox.askyesno("Settings", "Sign out of your Google account?", parent=self):
            self.app.sign_out()
            self.refresh_mapping()

    # -------------------------------------------------------------- close --

    def close(self):
        self.commit()
        core.save_topics(self.app.topics)
        source_changed = self.app.source_name != self._source_before
        self.destroy()
        self.app.settings_closed(source_changed)


def _positive_int(var, fallback):
    try:
        value = int(var.get())
        if value >= 1:
            return value
    except ValueError:
        pass
    var.set(str(fallback))
    return fallback


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
