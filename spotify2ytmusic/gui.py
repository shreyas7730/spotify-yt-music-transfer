#!/usr/bin/env python3
#
# SyncWave — Spotify → YouTube Music Transfer Tool
# GUI built with CustomTkinter, Apple Vision Pro / macOS Sequoia glassmorphism style.

import os
import sys
import json
import math
import queue
import time
import threading
import subprocess
import webbrowser
import tkinter as tk
import customtkinter as ctk

from . import backend
from . import spotify_backup
from . import cli

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ─────────────────────────────────────────────────────────────
# Colour Palette
# ─────────────────────────────────────────────────────────────
BG          = "#0a0a0f"
PANEL       = "#0e0e17"
CARD        = "#13131f"
CARD2       = "#181826"
BORDER      = "#1e1e2e"
ACCENT1     = "#7c3aed"
ACCENT2     = "#2563eb"
SP_GREEN    = "#1db954"
YT_RED      = "#ff0000"
TEXT        = "#ffffff"
TEXT2       = "#8888aa"
SUCCESS_CLR = "#50fa7b"
ERROR_CLR   = "#ff5555"
WARN_CLR    = "#ffb86c"
INFO_CLR    = "#8be9fd"


# ─────────────────────────────────────────────────────────────
# Gradient Canvas (horizontal purple→blue)
# ─────────────────────────────────────────────────────────────
class GradientCanvas(tk.Canvas):
    def __init__(self, parent, c1=ACCENT1, c2=ACCENT2, radius=14, **kw):
        super().__init__(parent, bd=0, highlightthickness=0, bg=BG, **kw)
        self.c1, self.c2, self.radius = c1, c2, radius
        self.bind("<Configure>", lambda e: self._draw())

    def _hex_to_rgb(self, h):
        h = h.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return
        r1, g1, b1 = self._hex_to_rgb(self.c1)
        r2, g2, b2 = self._hex_to_rgb(self.c2)
        r = self.radius
        for x in range(w):
            t = x / w
            R = int(r1 + (r2 - r1) * t)
            G = int(g1 + (g2 - g1) * t)
            B = int(b1 + (b2 - b1) * t)
            col = f"#{R:02x}{G:02x}{B:02x}"
            if x < r:
                offset = r - math.sqrt(max(0, r**2 - (r - x)**2))
            elif x > w - r:
                offset = r - math.sqrt(max(0, r**2 - (x - (w - r))**2))
            else:
                offset = 0
            self.create_line(x, offset, x, h - offset, fill=col)


# ─────────────────────────────────────────────────────────────
# Circular Progress Ring
# ─────────────────────────────────────────────────────────────
class CircularProgress(tk.Canvas):
    def __init__(self, parent, size=100, bw=10, **kw):
        super().__init__(parent, width=size, height=size, bd=0,
                         highlightthickness=0, bg=CARD, **kw)
        self.size, self.bw = size, bw
        self._pct = 0
        self._draw()

    def set(self, val):
        self._pct = max(0.0, min(1.0, val))
        self._draw()

    def _draw(self):
        self.delete("all")
        p = 8
        bbox = (p, p, self.size - p, self.size - p)
        self.create_oval(bbox, outline=CARD2, width=self.bw)
        if self._pct > 0:
            self.create_arc(bbox, start=90, extent=-360 * self._pct,
                            outline=ACCENT1, width=self.bw, style="arc")
        self.create_text(self.size // 2, self.size // 2,
                         text=f"{int(self._pct*100)}%",
                         fill=TEXT, font=("Segoe UI Variable", 13, "bold"))


# ─────────────────────────────────────────────────────────────
# Toast Notification
# ─────────────────────────────────────────────────────────────
class Toast(ctk.CTkToplevel):
    def __init__(self, root, msg, error=False):
        super().__init__(root)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.0)
        bg = "#2a1515" if error else "#152515"
        bc = ERROR_CLR if error else SUCCESS_CLR
        icon = "✘" if error else "✔"
        fr = ctk.CTkFrame(self, fg_color=bg, border_color=bc,
                          border_width=1, corner_radius=12)
        fr.pack(fill="both", expand=True, padx=2, pady=2)
        ctk.CTkLabel(fr, text=f"  {icon}  {msg}",
                     font=("Segoe UI Variable", 12, "bold"),
                     text_color=bc).pack(padx=16, pady=10)
        root.update_idletasks()
        tw, th = 320, 52
        tx = root.winfo_x() + root.winfo_width() - tw - 24
        ty = root.winfo_y() + root.winfo_height() - th - 28
        self.geometry(f"{tw}x{th}+{tx}+{ty}")
        self._fade(0.0, 0.95, 18, lambda: self.after(2800, self._dismiss))

    def _fade(self, cur, target, step, done=None):
        nxt = cur + (0.08 if target > cur else -0.08)
        if (target > cur and nxt >= target) or (target < cur and nxt <= target):
            self.attributes("-alpha", target)
            if done:
                done()
            return
        self.attributes("-alpha", nxt)
        self.after(16, lambda: self._fade(nxt, target, step, done))

    def _dismiss(self):
        self._fade(self.attributes("-alpha"), 0.0, 16,
                   lambda: self.destroy())


# ─────────────────────────────────────────────────────────────
# Thread-safe stdout → CTkTextbox
# ─────────────────────────────────────────────────────────────
class LogRedirector:
    def __init__(self, textbox, root):
        self.q = queue.Queue()
        self.tb = textbox
        self.root = root
        self._poll()

    def write(self, s):
        self.q.put(s)

    def flush(self):
        pass

    def _poll(self):
        while not self.q.empty():
            try:
                s = self.q.get_nowait()
            except queue.Empty:
                break
            tag = "info"
            sl = s.lower()
            if "error" in sl or "failed" in sl or "✘" in s:
                tag = "error"
            elif "warning" in sl or "warn" in sl:
                tag = "warn"
            elif any(k in sl for k in ("success", "complete", "done", "added", "✔")):
                tag = "ok"
            self.tb.configure(state="normal")
            self.tb.insert("end", s, tag)
            self.tb.configure(state="disabled")
            self.tb.see("end")
        self.root.after(40, self._poll)


# ─────────────────────────────────────────────────────────────
# Glass card helpers
# ─────────────────────────────────────────────────────────────
def glass_frame(parent, **kw):
    kw.setdefault("fg_color", CARD)
    kw.setdefault("border_color", BORDER)
    kw.setdefault("border_width", 1)
    kw.setdefault("corner_radius", 16)
    return ctk.CTkFrame(parent, **kw)


def h_label(parent, text, size=22, **kw):
    kw.setdefault("text_color", TEXT)
    kw.setdefault("anchor", "w")
    return ctk.CTkLabel(parent, text=text,
                        font=("Segoe UI Variable", size, "bold"), **kw)


def sub_label(parent, text, size=11, **kw):
    kw.setdefault("text_color", TEXT2)
    kw.setdefault("anchor", "w")
    return ctk.CTkLabel(parent, text=text,
                        font=("Segoe UI Variable", size), **kw)


# ─────────────────────────────────────────────────────────────
# Main Window
# ─────────────────────────────────────────────────────────────
class Window(ctk.CTk):

    # ── init ──────────────────────────────────────────────────
    def __init__(self):
        super().__init__()
        self.title("SyncWave")
        self.configure(fg_color=BG)
        self.geometry("1100x700")
        self.resizable(False, False)
        self.overrideredirect(True)
        self.attributes("-alpha", 0.0)

        self._center()

        # State
        self._dx = self._dy = 0
        self._active_nav = None
        self._stat_t = self._stat_e = self._stat_d = 0

        self._build_ui()
        self._select_nav("dashboard")
        self.after(80, self._fade_in)
        self.after(600, self._auto_yt_login)
        self.after(700, self._refresh_data)

    def _center(self):
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"1100x700+{(sw-1100)//2}+{(sh-700)//2}")

    def _fade_in(self):
        a = self.attributes("-alpha")
        if a < 1.0:
            self.attributes("-alpha", min(a + 0.06, 1.0))
            self.after(14, self._fade_in)

    def toast(self, msg, err=False):
        Toast(self, msg, err)

    # ── drag ──────────────────────────────────────────────────
    def _drag_start(self, e):
        self._dx, self._dy = e.x, e.y

    def _drag(self, e):
        self.geometry(f"+{self.winfo_x()+e.x-self._dx}+{self.winfo_y()+e.y-self._dy}")

    # ── build top-level layout ────────────────────────────────
    def _build_ui(self):
        self._build_titlebar()
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)
        self._build_sidebar(body)
        self._build_right_panel(body)
        self._build_center(body)

    # ── titlebar ──────────────────────────────────────────────
    def _build_titlebar(self):
        tb = ctk.CTkFrame(self, fg_color=PANEL, height=40, corner_radius=0)
        tb.pack(fill="x")
        tb.pack_propagate(False)
        tb.bind("<Button-1>", self._drag_start)
        tb.bind("<B1-Motion>", self._drag)

        dots = ctk.CTkFrame(tb, fg_color="transparent")
        dots.place(x=14, rely=0.5, anchor="w")
        for col, cmd in [("#ff5f56", self.destroy),
                         ("#ffbd2e", self.iconify),
                         ("#27c93f", lambda: None)]:
            b = ctk.CTkButton(dots, text="", width=13, height=13,
                              corner_radius=7, fg_color=col,
                              hover_color=col, border_width=0, command=cmd)
            b.pack(side="left", padx=3)

        ctk.CTkLabel(tb, text="SyncWave",
                     font=("Segoe UI Variable", 13, "bold"),
                     text_color=TEXT2).place(relx=0.5, rely=0.5, anchor="center")

    # ── left sidebar ──────────────────────────────────────────
    def _build_sidebar(self, parent):
        sb = ctk.CTkFrame(parent, width=200, fg_color=PANEL,
                          border_color=BORDER, border_width=1, corner_radius=0)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        # Logo area
        logo_fr = ctk.CTkFrame(sb, fg_color=CARD2, width=52, height=52,
                               corner_radius=14)
        logo_fr.pack(pady=(28, 6))
        logo_fr.pack_propagate(False)
        ctk.CTkLabel(logo_fr, text="♫→", font=("Segoe UI", 20, "bold"),
                     text_color=ACCENT1).place(relx=0.5, rely=0.5, anchor="center")

        h_label(sb, "SyncWave", size=17, anchor="center").pack()
        sub_label(sb, "Music Transfer Engine", size=10,
                  anchor="center").pack(pady=(2, 20))

        # Nav pills
        self._nav_btns = {}
        for nid, icon, label in [
            ("dashboard", "⊞", "Dashboard"),
            ("transfer",  "⇄", "Transfer"),
            ("playlists", "≡", "Playlists"),
            ("settings",  "⚙", "Settings"),
        ]:
            fr = ctk.CTkFrame(sb, fg_color="transparent", height=40)
            fr.pack(fill="x", padx=12, pady=4)
            fr.pack_propagate(False)

            btn = ctk.CTkButton(
                fr, text=f"  {icon}   {label}",
                font=("Segoe UI Variable", 12, "bold"),
                anchor="w", corner_radius=10, border_width=0,
                fg_color="transparent", text_color=TEXT2,
                hover_color=CARD2,
                command=lambda n=nid: self._select_nav(n)
            )
            btn.pack(fill="both", expand=True)
            self._nav_btns[nid] = (fr, btn)

        # Footer
        foot = ctk.CTkFrame(sb, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=15, pady=20)
        sub_label(foot, "SyncWave v1.0.0", anchor="center").pack()
        lnk = ctk.CTkLabel(foot, text="github.com/syncwave",
                            font=("Segoe UI Variable", 10, "underline"),
                            text_color=ACCENT1, cursor="hand2")
        lnk.pack(pady=(4, 0))
        lnk.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/syncwave"))

    def _select_nav(self, nid):
        if self._active_nav and self._active_nav in self._nav_btns:
            _, old_btn = self._nav_btns[self._active_nav]
            old_btn.configure(fg_color="transparent", text_color=TEXT2)
        self._active_nav = nid
        _, btn = self._nav_btns[nid]
        btn.configure(fg_color=ACCENT1, text_color=TEXT)

        for vid, vframe in self._views.items():
            if vid == nid:
                vframe.pack(fill="both", expand=True, padx=22, pady=22)
            else:
                vframe.pack_forget()

    # ── right panel ───────────────────────────────────────────
    def _build_right_panel(self, parent):
        rp = ctk.CTkFrame(parent, width=255, fg_color=PANEL,
                          border_color=BORDER, border_width=1, corner_radius=0)
        rp.pack(side="right", fill="y")
        rp.pack_propagate(False)

        sub_label(rp, "QUICK ACTIONS", size=10).pack(
            anchor="w", padx=20, pady=(26, 10))

        def action_btn(text, cmd, color=CARD2, hcolor="#23233a"):
            b = ctk.CTkButton(rp, text=text,
                              font=("Segoe UI Variable", 12, "bold"),
                              height=38, corner_radius=10,
                              fg_color=color, hover_color=hcolor,
                              text_color=TEXT, border_width=1,
                              border_color=BORDER, command=cmd)
            b.pack(fill="x", padx=18, pady=5)
            return b

        self._btn_backup   = action_btn("⬇  Backup Spotify",   self._run_backup)
        self._btn_login_sp = action_btn("🔑  Login Spotify",    self._run_sp_login,
                                        SP_GREEN, "#178a3b")
        self._btn_login_yt = action_btn("🔑  Login YT Music",   self._run_yt_login,
                                        "#c00000", "#8a0000")
        action_btn("📁  Open Folder", lambda: webbrowser.open(
            os.path.abspath(os.getcwd())))

        sub_label(rp, "LIVE STATS", size=10).pack(
            anchor="w", padx=20, pady=(22, 8))

        self._stat_labels = {}
        for key, label, color in [
            ("t", "Tracks Added",      ACCENT1),
            ("e", "Errors",            ERROR_CLR),
            ("d", "Duplicates Skipped", WARN_CLR),
        ]:
            card = glass_frame(rp, fg_color=CARD2, corner_radius=12, height=50)
            card.pack(fill="x", padx=18, pady=4)
            card.pack_propagate(False)
            num = ctk.CTkLabel(card, text="0",
                               font=("Segoe UI Variable", 18, "bold"),
                               text_color=color)
            num.pack(side="left", padx=(14, 8))
            sub_label(card, label).pack(side="left")
            self._stat_labels[key] = num

        self._lbl_backup_time = sub_label(rp, "Last backup: —", size=10)
        self._lbl_backup_time.pack(side="bottom", pady=16)

    # ── center panel + views ──────────────────────────────────
    def _build_center(self, parent):
        center = ctk.CTkFrame(parent, fg_color="transparent")
        center.pack(side="left", fill="both", expand=True)

        self._views = {}
        self._views["dashboard"] = self._make_dashboard(center)
        self._views["transfer"]  = self._make_transfer(center)
        self._views["playlists"] = self._make_playlists_view(center)
        self._views["settings"]  = self._make_settings(center)

    # ── VIEW: Dashboard ───────────────────────────────────────
    def _make_dashboard(self, parent):
        v = ctk.CTkFrame(parent, fg_color="transparent")

        h_label(v, "Dashboard").pack(fill="x", pady=(0, 16))

        # Welcome card
        wc = glass_frame(v)
        wc.pack(fill="x", pady=(0, 14))
        h_label(wc, "Welcome to SyncWave", size=15).pack(
            fill="x", padx=20, pady=(18, 4))
        sub_label(
            wc,
            "Transfer your Spotify liked songs and playlists to YouTube Music "
            "with a single click. Start by logging in with the Quick Actions panel.",
            size=11
        ).pack(fill="x", padx=20, pady=(0, 16))

        # Status cards row
        row = ctk.CTkFrame(v, fg_color="transparent")
        row.pack(fill="x", pady=4)

        # Spotify status card
        self._sp_card = glass_frame(row, height=120)
        self._sp_card.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._sp_card.pack_propagate(False)
        self._lbl_sp_status = ctk.CTkLabel(
            self._sp_card, text="●  Spotify",
            font=("Segoe UI Variable", 12, "bold"), text_color=SP_GREEN, anchor="w")
        self._lbl_sp_status.pack(fill="x", padx=16, pady=(16, 4))
        self._lbl_sp_user = h_label(self._sp_card, "No Local Backup", size=14)
        self._lbl_sp_user.pack(fill="x", padx=16)
        self._lbl_sp_detail = sub_label(self._sp_card, "Run Backup to begin", size=11)
        self._lbl_sp_detail.pack(fill="x", padx=16, pady=(3, 0))

        # YT Music status card
        self._yt_card = glass_frame(row, height=120)
        self._yt_card.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self._yt_card.pack_propagate(False)
        self._lbl_yt_status = ctk.CTkLabel(
            self._yt_card, text="●  YouTube Music",
            font=("Segoe UI Variable", 12, "bold"), text_color=YT_RED, anchor="w")
        self._lbl_yt_status.pack(fill="x", padx=16, pady=(16, 4))
        self._lbl_yt_user = h_label(self._yt_card, "Disconnected", size=14)
        self._lbl_yt_user.pack(fill="x", padx=16)
        self._lbl_yt_detail = sub_label(self._yt_card, "Click Login YT Music", size=11)
        self._lbl_yt_detail.pack(fill="x", padx=16, pady=(3, 0))

        # Tip card
        tip = glass_frame(v, fg_color=CARD2)
        tip.pack(fill="x", pady=14)
        sub_label(
            tip,
            "💡  Tip: Run 'Backup Spotify' first → then 'Login YT Music' → then press "
            "Transfer on the Transfer tab.",
            size=11
        ).pack(padx=20, pady=12)

        return v

    # ── VIEW: Transfer ────────────────────────────────────────
    def _make_transfer(self, parent):
        v = ctk.CTkFrame(parent, fg_color="transparent")
        h_label(v, "Library Sync Engine").pack(fill="x", pady=(0, 10))

        tabs = ctk.CTkTabview(
            v, fg_color="transparent",
            segmented_button_fg_color=CARD,
            segmented_button_selected_color=ACCENT1,
            segmented_button_unselected_color=CARD,
            text_color=TEXT, corner_radius=14
        )
        tabs.pack(fill="both", expand=True)

        t1 = tabs.add("Transfer")
        t2 = tabs.add("Logs")

        # ─ Transfer sub-tab ─
        # Toggle switches
        tog = glass_frame(t1)
        tog.pack(fill="x", pady=(8, 10))
        trow = ctk.CTkFrame(tog, fg_color="transparent")
        trow.pack(fill="x", padx=20, pady=14)

        self._sw_liked = ctk.CTkSwitch(
            trow, text="Liked Songs", progress_color=ACCENT1,
            font=("Segoe UI Variable", 12))
        self._sw_liked.select()
        self._sw_liked.pack(side="left", expand=True)

        self._sw_pls = ctk.CTkSwitch(
            trow, text="Playlists", progress_color=ACCENT1,
            font=("Segoe UI Variable", 12))
        self._sw_pls.select()
        self._sw_pls.pack(side="left", expand=True)

        self._sw_skip = ctk.CTkSwitch(
            trow, text="Skip Duplicates", progress_color=ACCENT1,
            font=("Segoe UI Variable", 12))
        self._sw_skip.select()
        self._sw_skip.pack(side="left", expand=True)

        # Big transfer button (gradient canvas wrapping CTkButton)
        btn_host = ctk.CTkFrame(t1, fg_color="transparent", height=52)
        btn_host.pack(fill="x", pady=10)
        btn_host.pack_propagate(False)
        grad = GradientCanvas(btn_host, c1=ACCENT1, c2=ACCENT2, radius=26)
        grad.pack(fill="both", expand=True)
        self._btn_transfer = ctk.CTkButton(
            grad, text="▶   START SYNCHRONIZATION",
            font=("Segoe UI Variable", 13, "bold"), height=52,
            corner_radius=26, fg_color="transparent",
            hover_color="#4f46e5", text_color=TEXT,
            command=self._start_transfer
        )
        self._btn_transfer.pack(fill="both", expand=True)

        # Progress section
        prog_card = glass_frame(t1)
        prog_card.pack(fill="both", expand=True, pady=(4, 0))

        self._prog_ring = CircularProgress(prog_card)
        self._prog_ring.pack(side="left", padx=22, pady=18)

        pinfo = ctk.CTkFrame(prog_card, fg_color="transparent")
        pinfo.pack(side="left", fill="both", expand=True, pady=18)
        self._lbl_sync_status = h_label(pinfo, "Status: Idle", size=13)
        self._lbl_sync_status.pack(fill="x")
        self._lbl_sync_track = sub_label(
            pinfo, "Awaiting transfer trigger…", size=11)
        self._lbl_sync_track.pack(fill="x", pady=(4, 0))

        # ─ Logs sub-tab ─
        self._log_tb = ctk.CTkTextbox(
            t2, font=("Consolas", 11), fg_color="#05050a",
            text_color="#f8f8f2", corner_radius=14,
            border_color=BORDER, border_width=1
        )
        self._log_tb.pack(fill="both", expand=True, pady=8)
        self._log_tb.configure(state="disabled")
        self._log_tb.tag_config("error",   foreground=ERROR_CLR)
        self._log_tb.tag_config("warn",    foreground=WARN_CLR)
        self._log_tb.tag_config("ok",      foreground=SUCCESS_CLR)
        self._log_tb.tag_config("info",    foreground=INFO_CLR)

        sys.stdout.write = LogRedirector(self._log_tb, self).write

        return v

    # ── VIEW: Playlists ───────────────────────────────────────
    def _make_playlists_view(self, parent):
        v = ctk.CTkFrame(parent, fg_color="transparent")
        h_label(v, "Spotify Playlists").pack(fill="x", pady=(0, 12))

        self._pl_scroll = ctk.CTkScrollableFrame(
            v, fg_color=CARD, border_color=BORDER,
            border_width=1, corner_radius=16
        )
        self._pl_scroll.pack(fill="both", expand=True)

        return v

    def _populate_playlists(self, pls):
        for w in self._pl_scroll.winfo_children():
            w.destroy()
        if not pls:
            sub_label(self._pl_scroll,
                      "No playlists found in backup.",
                      size=12).pack(pady=40)
            return
        for pl in pls:
            name = pl.get("name", "Unnamed")
            cnt  = len(pl.get("tracks", []))
            row  = ctk.CTkFrame(self._pl_scroll, fg_color=CARD2,
                                corner_radius=12, height=60)
            row.pack(fill="x", padx=10, pady=5)
            row.pack_propagate(False)

            ctk.CTkLabel(row, text="🎵", font=("Segoe UI", 18),
                         fg_color="#202030", width=44, height=44,
                         corner_radius=10).pack(
                side="left", padx=(10, 12), pady=8)

            meta = ctk.CTkFrame(row, fg_color="transparent")
            meta.pack(side="left", fill="y", pady=10)
            h_label(meta, name[:38], size=12).pack(anchor="w")
            sub_label(meta, f"{cnt} tracks", size=10).pack(anchor="w")

            sv = tk.BooleanVar(value=True)
            pl["_sw"] = sv
            ctk.CTkSwitch(row, text="", variable=sv,
                          progress_color=ACCENT1, width=36).pack(
                side="right", padx=16)

    # ── VIEW: Settings ────────────────────────────────────────
    def _make_settings(self, parent):
        v = ctk.CTkFrame(parent, fg_color="transparent")
        h_label(v, "Settings").pack(fill="x", pady=(0, 16))

        box = glass_frame(v)
        box.pack(fill="both", expand=True)

        # Sleep delay slider
        h_label(box, "API Throttle Delay (seconds)", size=13).pack(
            fill="x", padx=20, pady=(20, 4))
        self._slider = ctk.CTkSlider(
            box, from_=0.0, to=5.0, number_of_steps=50,
            button_color=ACCENT1, progress_color=ACCENT1,
            button_hover_color=ACCENT2
        )
        self._slider.set(0.1)
        self._slider.pack(fill="x", padx=20, pady=4)
        self._lbl_slider = sub_label(box, "0.10 s  (default)")
        self._lbl_slider.pack(fill="x", padx=20, pady=(0, 16))
        self._slider.configure(
            command=lambda v: self._lbl_slider.configure(text=f"{float(v):.2f} s"))

        # Algorithm selector
        h_label(box, "Song Matching Algorithm", size=13).pack(
            fill="x", padx=20, pady=(8, 4))
        self._var_algo = ctk.StringVar(value="0 — Exact album match (recommended)")
        ctk.CTkOptionMenu(
            box, variable=self._var_algo,
            values=["0 — Exact album match (recommended)",
                    "1 — Extended album + song match",
                    "2 — Fuzzy match + video search"],
            fg_color=CARD2, button_color=ACCENT1,
            button_hover_color=ACCENT2, text_color=TEXT,
            dropdown_fg_color=CARD2, dropdown_hover_color=CARD
        ).pack(fill="x", padx=20, pady=(0, 16))

        # Credentials
        h_label(box, "Spotify Credentials (optional override)", size=13).pack(
            fill="x", padx=20, pady=(8, 4))
        self._entry_cid = ctk.CTkEntry(
            box, placeholder_text="Client ID",
            fg_color=CARD2, border_color=BORDER, height=34)
        self._entry_cid.pack(fill="x", padx=20, pady=4)
        self._entry_secret = ctk.CTkEntry(
            box, placeholder_text="Client Secret",
            fg_color=CARD2, border_color=BORDER, height=34, show="*")
        self._entry_secret.pack(fill="x", padx=20, pady=(4, 20))

        return v

    # ─────────────────────────────────────────────────────────
    # Data Refresh
    # ─────────────────────────────────────────────────────────
    def _refresh_data(self):
        # YT Music status
        yt_ok = os.path.exists("oauth.json")
        if yt_ok:
            self._lbl_yt_status.configure(
                text="●  YouTube Music", text_color=SUCCESS_CLR)
            self._lbl_yt_user.configure(text="Session Active")
            self._lbl_yt_detail.configure(text="Ready to synchronize")
        else:
            self._lbl_yt_status.configure(
                text="●  YouTube Music", text_color=YT_RED)
            self._lbl_yt_user.configure(text="Disconnected")
            self._lbl_yt_detail.configure(text="Click Login YT Music")

        # Spotify / playlists.json status
        if not os.path.exists("playlists.json"):
            self._lbl_sp_user.configure(text="No Local Backup")
            self._lbl_sp_detail.configure(text="Run Backup to begin")
            self._populate_playlists([])
            return

        try:
            with open("playlists.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            pls = data.get("playlists", [])
            liked_cnt = 0
            normal = []
            for pl in pls:
                if pl.get("name") == "Liked Songs":
                    liked_cnt = len(pl.get("tracks", []))
                else:
                    normal.append(pl)

            self._lbl_sp_user.configure(text="Backup Loaded")
            self._lbl_sp_detail.configure(
                text=f"{liked_cnt} liked  •  {len(normal)} playlists")
            self._populate_playlists(normal)

            mtime = os.path.getmtime("playlists.json")
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
            self._lbl_backup_time.configure(text=f"Last backup: {ts}")
        except Exception as ex:
            print(f"ERROR reading playlists.json: {ex}")

    # ─────────────────────────────────────────────────────────
    # Quick Actions
    # ─────────────────────────────────────────────────────────
    def _run_backup(self):
        self._btn_backup.configure(state="disabled", text="Backing up…")
        self.toast("Connecting to Spotify…")

        def work():
            try:
                spotify_backup.main()
                self.toast("Backup complete!")
                self.after(400, self._refresh_data)
            except Exception as e:
                print(f"ERROR: Backup failed: {e}")
                self.toast("Backup failed.", True)
            finally:
                self.after(0, lambda: self._btn_backup.configure(
                    state="normal", text="⬇  Backup Spotify"))

        threading.Thread(target=work, daemon=True).start()

    def _run_sp_login(self):
        self.toast("Opening Spotify browser auth…")
        def work():
            try:
                spotify_backup.main()
                self.toast("Spotify authorized!")
            except Exception as e:
                print(f"ERROR: Spotify login: {e}")
                self.toast("Spotify login failed.", True)
        threading.Thread(target=work, daemon=True).start()

    def _run_yt_login(self):
        self.toast("Opening YT Music OAuth setup…")
        def work():
            try:
                if os.name == "nt":
                    subprocess.Popen(
                        ["ytmusicapi", "oauth",
                         "--file", os.path.abspath("oauth.json")],
                        creationflags=subprocess.CREATE_NEW_CONSOLE
                    ).communicate()
                else:
                    subprocess.call(
                        ["python3", "-m", "ytmusicapi", "oauth",
                         "--file", os.path.abspath("oauth.json")])
                self.toast("YT Music session saved!")
                self.after(400, self._refresh_data)
            except Exception as e:
                print(f"ERROR: YT Music login: {e}")
                self.toast("YT Music login failed.", True)
        threading.Thread(target=work, daemon=True).start()

    def _auto_yt_login(self):
        """Silently check for existing oauth.json on startup."""
        if os.path.exists("oauth.json"):
            print("oauth.json detected — YT Music session active.")
        else:
            print("No oauth.json found. Click 'Login YT Music' to authenticate.")
        self._refresh_data()

    # ─────────────────────────────────────────────────────────
    # Transfer Engine
    # ─────────────────────────────────────────────────────────
    def _get_algo(self):
        return int(self._var_algo.get()[0])

    def _update_stat(self, key, delta=1):
        if key == "t":
            self._stat_t += delta
            self._stat_labels["t"].configure(text=str(self._stat_t))
        elif key == "e":
            self._stat_e += delta
            self._stat_labels["e"].configure(text=str(self._stat_e))
        elif key == "d":
            self._stat_d += delta
            self._stat_labels["d"].configure(text=str(self._stat_d))

    def _start_transfer(self):
        if not os.path.exists("playlists.json"):
            self.toast("Run Backup Spotify first.", True)
            return
        if not os.path.exists("oauth.json"):
            self.toast("Login to YT Music first.", True)
            return

        self._btn_transfer.configure(state="disabled")
        self._lbl_sync_status.configure(text="Status: Running…")
        self._prog_ring.set(0.0)
        self._stat_t = self._stat_e = self._stat_d = 0
        for k in ("t", "e", "d"):
            self._stat_labels[k].configure(text="0")

        include_liked = bool(self._sw_liked.get())
        include_pls   = bool(self._sw_pls.get())
        algo          = self._get_algo()
        delay         = float(self._slider.get())

        def work():
            try:
                yt = backend.get_ytmusic()
                with open("playlists.json", "r", encoding="utf-8") as f:
                    data = json.load(f)
                pls = data.get("playlists", [])

                tasks = []   # list of (type, playlist_dict)
                for pl in pls:
                    if pl.get("name") == "Liked Songs":
                        if include_liked:
                            tasks.append(("liked", pl))
                    else:
                        if include_pls:
                            # Respect per-row toggle
                            active = pl.get("_sw", tk.BooleanVar(value=True)).get()
                            if active:
                                tasks.append(("playlist", pl))

                total = sum(len(t[1].get("tracks", [])) for t in tasks)
                if total == 0:
                    self.toast("No tracks matched current filters.", True)
                    return

                done = 0
                for kind, pl in tasks:
                    pl_name = pl.get("name", "Unknown")
                    print(f"\n== Syncing: {pl_name}")

                    yt_pl_id = None
                    if kind == "playlist":
                        yt_pl_id = backend.get_playlist_id_by_name(yt, pl_name)
                        if not yt_pl_id:
                            yt_pl_id = backend._ytmusic_create_playlist(
                                yt, pl_name, pl_name, "PRIVATE")

                    track_list = pl.get("tracks", [])
                    if kind == "liked":
                        track_list = list(reversed(track_list))
                    for item in track_list:
                        done += 1
                        self.after(0, lambda p=done/total:
                                   self._prog_ring.set(p))

                        t = item.get("track")
                        if not t:
                            continue
                        title  = t.get("name", "")
                        artist = t["artists"][0]["name"] if t.get("artists") else ""
                        album  = t["album"]["name"] if t.get("album") else ""

                        self.after(0, lambda s=f"Syncing: {title} — {artist}":
                                   self._lbl_sync_track.configure(text=s))

                        try:
                            dst = backend.lookup_song(yt, title, artist, album, algo)
                            vid = dst["videoId"]
                            if kind == "liked":
                                yt.rate_song(vid, "LIKE")
                            else:
                                yt.add_playlist_items(
                                    playlistId=yt_pl_id,
                                    videoIds=[vid],
                                    duplicates=False)
                            self.after(0, lambda: self._update_stat("t"))
                            print(f"  ✔ {title}")
                        except Exception as ex:
                            msg = str(ex).lower()
                            if "duplicate" in msg or "already" in msg:
                                self.after(0, lambda: self._update_stat("d"))
                            else:
                                self.after(0, lambda: self._update_stat("e"))
                                print(f"  ✘ {title}: {ex}")

                        if delay:
                            time.sleep(delay)

                self.toast(
                    f"Done! {self._stat_t} added, "
                    f"{self._stat_d} dupes, {self._stat_e} errors.")
                print(f"\nSyncWave complete: {self._stat_t} added, "
                      f"{self._stat_d} duplicates, {self._stat_e} errors.")

            except Exception as e:
                print(f"ERROR: Transfer engine exception: {e}")
                self.toast("Transfer failed — see Logs.", True)
            finally:
                self.after(0, lambda: self._lbl_sync_status.configure(
                    text="Status: Completed."))
                self.after(0, lambda: self._lbl_sync_track.configure(
                    text="Libraries synchronized."))
                self.after(0, lambda: self._btn_transfer.configure(
                    state="normal"))

        threading.Thread(target=work, daemon=True).start()


# ─────────────────────────────────────────────────────────────
def main():
    app = Window()
    app.mainloop()


if __name__ == "__main__":
    main()