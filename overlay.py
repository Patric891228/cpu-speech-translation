"""懸浮字幕：沒有邊框、永遠在最上層、半透明，可以蓋在簡報、會議軟體上面。

- 拖曳任何地方移動；右下角拖曳調整大小
- 滑鼠移上去才出現工具列：字級、透明度、原文、關閉
- 最新一句的譯文永遠完整顯示（放不下就往上長高），上一句淡淡地留在上面
- 位置、大小、字級、透明度記在 settings.json
"""
from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from typing import Callable

BG = "#0E1820"
CAPTION = "#F2F5F7"
SOURCE = "#8FA3B3"
PREV = "#5C7283"
BAR = "#1C2E3B"
SIGNAL = "#4FB8A8"
TALLY = "#E5484D"
ALPHAS = (0.9, 0.78, 0.62, 1.0)


class Settings:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass


class Overlay(tk.Toplevel):
    def __init__(self, master: tk.Misc, settings: Settings, family: str, on_close: Callable[[], None],
                 live: Callable[[], bool] = lambda: False):
        super().__init__(master, bg=BG)
        self.s, self.fam, self.on_close_cb, self.live = settings, family, on_close, live
        self.size = int(settings.get("overlay_size", 28))
        self.alpha = float(settings.get("overlay_alpha", ALPHAS[0]))
        self.show_orig = bool(settings.get("overlay_orig", True))
        self.entries: dict[str, tuple[str, str | None]] = {}
        self.order: list[str] = []

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", self.alpha)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(1200, sw - 120), 200
        self.geometry(settings.get("overlay_geom") or f"{w}x{h}+{(sw - w) // 2}+{sh - h - 100}")
        self.minsize(420, 120)

        # 左邊一條細色條：收音中紅、待機灰（字幕蓋在別的程式上面時，也看得出有沒有在收音）
        self.edge = tk.Frame(self, bg=PREV, width=4)
        self.edge.pack(side="left", fill="y")
        self.body = body = tk.Frame(self, bg=BG, padx=28, pady=18)
        body.pack(side="left", fill="both", expand=True)
        self.prev = tk.Label(body, bg=BG, fg=PREV, anchor="w", justify="left")
        self.orig = tk.Label(body, bg=BG, fg=SOURCE, anchor="w", justify="left")
        self.cur = tk.Label(body, bg=BG, fg=CAPTION, anchor="w", justify="left")
        # 由下往上排：最新譯文貼底；空間不夠時被裁掉的是最上面的上一句
        self.cur.pack(side="bottom", fill="x")
        self.orig.pack(side="bottom", fill="x", pady=(8, 2))
        self.prev.pack(side="bottom", fill="x")
        self._fonts()

        self.bar = tk.Frame(self, bg=BAR, padx=4, pady=2)
        for text, cmd in (("A−", lambda: self._font_step(-2)), ("A＋", lambda: self._font_step(2)),
                          ("透明度", self._cycle_alpha), ("原文", self._toggle_orig), ("關閉", self.close)):
            b = tk.Label(self.bar, text=text, bg=BAR, fg=CAPTION, font=(family, 10), padx=10, pady=4,
                         cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, c=cmd: (c(), "break")[1])
            b.bind("<Enter>", lambda e, w=b: w.configure(fg=SIGNAL))
            b.bind("<Leave>", lambda e, w=b: w.configure(fg=CAPTION))
        self.grip = tk.Canvas(self, width=14, height=14, bg=BG, highlightthickness=0, cursor="bottom_right_corner")
        for i in (4, 8, 12):
            self.grip.create_line(i, 13, 13, i, fill=PREV)
        self.grip.bind("<ButtonPress-1>", self._grip_start)
        self.grip.bind("<B1-Motion>", self._grip_move)
        self.grip.bind("<ButtonRelease-1>", lambda e: self._save_geom())

        for w in (self, body, self.prev, self.orig, self.cur, self.edge):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", lambda e: self._save_geom())
        self.bind("<Configure>", self._wrap)
        self._hover = False
        self.after(200, self._watch)
        self._render()

    # ---------- 內容 ----------

    def update_entry(self, tag: str, orig: str, tr: str | None) -> None:
        if tag not in self.entries:
            self.order = (self.order + [tag])[-20:]
        self.entries[tag] = (orig, tr)
        self._render()

    def clear(self) -> None:
        self.entries.clear()
        self.order.clear()
        self._render()

    def _render(self) -> None:
        if not self.order:
            self.prev.configure(text="")
            self.orig.configure(text="")
            self.cur.configure(text="字幕會出現在這裡", fg=PREV)
        else:
            latest = self.order[-1]
            orig, tr = self.entries[latest]
            older = [t for t in self.order[:-1] if self.entries[t][1]]
            self.prev.configure(text=self.entries[older[-1]][1] if older else "")
            self.orig.configure(text=orig if self.show_orig else "")
            self.cur.configure(text=tr or "翻譯中…", fg=CAPTION if tr else PREV)
        self.after(50, self._fit)

    def _fonts(self) -> None:
        self.cur.configure(font=(self.fam, self.size, "bold"))
        self.prev.configure(font=(self.fam, max(11, int(self.size * 0.6))))
        self.orig.configure(font=(self.fam, max(11, int(self.size * 0.55))))

    def _wrap(self, _e=None) -> None:
        w = max(240, self.body.winfo_width() - 60)
        for lbl in (self.prev, self.orig, self.cur):
            lbl.configure(wraplength=w)
        self.after(50, self._fit)

    def _fit(self) -> None:
        """最新一句放不下時，視窗往上長高（底邊不動，字幕通常放在螢幕下方）。"""
        if not self.winfo_ismapped() or self.winfo_width() < 100:
            return  # 還沒顯示：顯示後 <Configure> 會再呼叫
        need = self.cur.winfo_reqheight() + self.orig.winfo_reqheight() + 56
        w, h, x, y = self.winfo_width(), self.winfo_height(), self.winfo_x(), self.winfo_y()
        if need > h:
            self.geometry(f"{w}x{need}+{x}+{y - (need - h)}")
            h = need
        # 上一句放不下就整句不顯示（不要只露出半行字）
        if self.prev.cget("text") and need + self.prev.winfo_reqheight() > h:
            self.prev.configure(text="")

    # ---------- 工具列 ----------

    def _font_step(self, d: int) -> None:
        self.size = max(16, min(72, self.size + d))
        self.s.set("overlay_size", self.size)
        self._fonts()
        self.after(50, self._fit)

    def _cycle_alpha(self) -> None:
        i = ALPHAS.index(self.alpha) if self.alpha in ALPHAS else -1
        self.alpha = ALPHAS[(i + 1) % len(ALPHAS)]
        self.attributes("-alpha", self.alpha)
        self.s.set("overlay_alpha", self.alpha)

    def _toggle_orig(self) -> None:
        self.show_orig = not self.show_orig
        self.s.set("overlay_orig", self.show_orig)
        self._render()

    def _watch(self) -> None:
        """每 150 毫秒：滑鼠在字幕上才顯示工具列（無邊框視窗收不到可靠的 Enter/Leave），並更新收音色條。"""
        try:
            x, y = self.winfo_pointerxy()
            inside = (self.winfo_rootx() <= x < self.winfo_rootx() + self.winfo_width() and
                      self.winfo_rooty() <= y < self.winfo_rooty() + self.winfo_height())
        except tk.TclError:
            return
        if inside != self._hover:
            self._hover = inside
            self.show_tools(inside)
        self.edge.configure(bg=TALLY if self.live() else PREV)
        self.after(150, self._watch)

    def show_tools(self, on: bool) -> None:
        if on:
            self.bar.place(relx=1.0, x=-10, y=10, anchor="ne")
            self.grip.place(relx=1.0, rely=1.0, x=-4, y=-4, anchor="se")
        else:
            self.bar.place_forget()
            self.grip.place_forget()

    # ---------- 拖曳、縮放 ----------

    def _drag_start(self, e) -> None:
        self._dx, self._dy = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()

    def _drag_move(self, e) -> None:
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _grip_start(self, e) -> None:
        self._gx, self._gy = e.x_root, e.y_root
        self._gw, self._gh = self.winfo_width(), self.winfo_height()

    def _grip_move(self, e) -> None:
        w = max(420, self._gw + e.x_root - self._gx)
        h = max(120, self._gh + e.y_root - self._gy)
        self.geometry(f"{w}x{h}")

    def _save_geom(self) -> None:
        self.s.set("overlay_geom", self.geometry())

    def close(self) -> None:
        self._save_geom()
        self.destroy()
        self.on_close_cb()
