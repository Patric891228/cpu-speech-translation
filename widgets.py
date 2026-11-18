"""自己畫的介面元件（tkinter），配色同 ui.py。

Tabs（分頁）、Dropdown（下拉選單）、按鈕波紋的動作移植自 Motion Prompt：形狀、時間、緩動曲線照原樣，
只換文字和配色。Tk 的元件沒有透明度，「淡入淡出」用顏色往底色混合來做。
"""
# Motion Prompt #049-072 · MIT License (c) 2026 Shohei Ohtaki · https://showheyohtaki.github.io/motion-prompt/#049
# Motion Prompt #057 · MIT License (c) 2026 Shohei Ohtaki · https://showheyohtaki.github.io/motion-prompt/#057
# Motion Prompt #073-096 · MIT License (c) 2026 Shohei Ohtaki · https://showheyohtaki.github.io/motion-prompt/#073
# Motion Prompt #085 · MIT License (c) 2026 Shohei Ohtaki · https://showheyohtaki.github.io/motion-prompt/#085
# Motion Prompt #053 · MIT License (c) 2026 Shohei Ohtaki · https://showheyohtaki.github.io/motion-prompt/#053
from __future__ import annotations

import math
import time
import tkinter as tk
from typing import Callable

INK = "#13202A"
DECK = "#0E1820"
RAISED = "#1C2E3B"
LINE = "#2A4051"
CAPTION = "#F2F5F7"
SOURCE = "#8FA3B3"
MUTED = "#7D93A3"
TALLY = "#E5484D"
SIGNAL = "#4FB8A8"
SIGNAL_DK = "#0E2A27"
FOCUS = "#9CC9FF"
HILITE = "#1F3E3A"   # 下拉選單滑過的那一項（原作的淡藍）


# ---------- Motion Prompt kit 的緩動函式（照抄） ----------

def clamp(v: float, a: float = 0.0, b: float = 1.0) -> float:
    return max(a, min(b, v))


def seg(t: float, a: float, b: float) -> float:
    return clamp((t - a) / (b - a))


def eo(x: float) -> float:          # easeOutCubic
    return 1 - (1 - x) ** 3


def eio(x: float) -> float:         # easeInOutCubic
    return 4 * x * x * x if x < .5 else 1 - (-2 * x + 2) ** 3 / 2


def lerp(a: float, b: float, k: float) -> float:
    return a + (b - a) * k


def mix(c1: str, c2: str, a: float) -> str:
    """c1 → c2 的顏色，a=0 是 c1、a=1 是 c2。"""
    a = clamp(a)
    p = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    q = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * a):02x}" for x, y in zip(p, q))


class Anim:
    """用 after() 每 15 毫秒畫一格；fn(t) 的 t 是開始後經過的秒數。"""

    def __init__(self, widget: tk.Misc, duration: float, fn: Callable[[float], None],
                 done: Callable[[], None] | None = None):
        self.w, self.d, self.fn, self.done = widget, duration, fn, done
        self.t0 = time.perf_counter()
        self.alive = True
        self._step()

    def _step(self) -> None:
        if not self.alive:
            return
        t = time.perf_counter() - self.t0
        self.fn(min(t, self.d))
        if t < self.d:
            self.w.after(15, self._step)
        else:
            self.alive = False
            if self.done:
                self.done()

    def stop(self) -> None:
        self.alive = False


def rrect(cv: tk.Canvas, x: float, y: float, w: float, h: float, r: float, **kw) -> int:
    """圓角矩形（多邊形 smooth 畫法）。"""
    pts = [x + r, y, x + w - r, y, x + w, y, x + w, y + r, x + w, y + h - r, x + w, y + h,
           x + w - r, y + h, x + r, y + h, x, y + h, x, y + h - r, x, y + r, x, y]
    return cv.create_polygon(pts, smooth=True, **kw)


class Fader:
    """把一整塊介面的顏色往底色混合，做出淡入淡出（Tk 元件沒有透明度）。"""

    OPTS = ("bg", "fg", "highlightbackground", "insertbackground")

    def __init__(self, root: tk.Misc, base: str = INK):
        self.root, self.base = root, base
        self.saved: list = []

    def capture(self) -> None:
        self.saved = []

        def walk(w):
            if isinstance(w, tk.Canvas):
                items = []
                for it in w.find_all():
                    o = {}
                    for k in ("fill", "outline"):
                        try:
                            v = w.itemcget(it, k)
                        except tk.TclError:
                            continue
                        if v.startswith("#") and len(v) == 7:
                            o[k] = v
                    items.append((it, o))
                self.saved.append(("cv", w, items, w.cget("bg")))
            elif isinstance(w, (tk.Frame, tk.Label, tk.Text)):
                o = {}
                for k in self.OPTS:
                    try:
                        v = str(w.cget(k))
                    except tk.TclError:
                        continue
                    if v.startswith("#") and len(v) == 7:
                        o[k] = v
                self.saved.append(("w", w, o, None))
            for c in w.winfo_children():
                walk(c)
        walk(self.root)

    def apply(self, a: float) -> None:
        for kind, w, data, cvbg in self.saved:
            try:
                if kind == "cv":
                    w.configure(bg=mix(self.base, cvbg, a)) if cvbg.startswith("#") else None
                    for it, o in data:
                        w.itemconfigure(it, **{k: mix(self.base, v, a) for k, v in o.items()})
                else:
                    w.configure(**{k: mix(self.base, v, a) for k, v in data.items()})
            except tk.TclError:
                pass


# ---------- 按鈕 ----------

class Btn(tk.Canvas):
    """圓角按鈕（Canvas 自己畫）：滑鼠移上變亮、Tab 可以選到（有焦點框）、Enter／空白鍵觸發。
    按下時有波紋（Motion Prompt #085 Ripple effect）：以按下的點為圓心，0.6 秒（easeOutCubic）
    擴大到按鈕最遠的角；白色 0.4 不透明度，按下後 0.3–0.8 秒淡到 0；圓圈裁在按鈕裡，文字在上面。
    """

    STYLES = {
        "primary": (SIGNAL, SIGNAL_DK, "#6FCBBE"),
        "danger": (TALLY, "#ffffff", "#F06A6E"),
        "ghost": (RAISED, CAPTION, "#25394A"),
        "on": ("#23463F", "#BFEFE7", "#2A5249"),
    }
    RADIUS, DUR, LIFE = 10, 0.6, 0.8

    def __init__(self, master, text: str, command, kind: str = "ghost", font=None, padx=14, pady=7, bg=None, **kw):
        from tkinter import font as tkfont
        self.pbg = bg or master.cget("bg")
        # 不用 Tk 內建的焦點框（它會讓畫布座標偏 2 px），焦點時自己畫圓角外框
        super().__init__(master, bg=self.pbg, highlightthickness=0, borderwidth=0, takefocus=1,
                         cursor="hand2", **kw)
        self.focused = False
        self.fnt = tkfont.Font(font=font) if font else tkfont.nametofont("TkDefaultFont")
        self.padx, self.pady = padx, pady
        self.text, self.command, self.kind, self.enabled = text, command, kind, True
        self.hover = False
        self.ripples: list[tuple[float, float, float]] = []   # (按下時間, x, y)
        self._size()
        self._paint()
        self.bind("<Button-1>", lambda e: self.invoke(e.x, e.y))
        self.bind("<Return>", lambda e: self.invoke())
        self.bind("<space>", lambda e: self.invoke())
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<FocusIn>", lambda e: (setattr(self, "focused", True), self._paint()))
        self.bind("<FocusOut>", lambda e: (setattr(self, "focused", False), self._paint()))

    def _size(self) -> None:
        self.bw = self.fnt.measure(self.text) + 2 * self.padx
        self.bh = self.fnt.metrics("linespace") + 2 * self.pady
        self.configure(width=self.bw, height=self.bh)

    def _hover(self, on: bool) -> None:
        self.hover = on
        self._paint()

    def _colors(self) -> tuple[str, str]:
        bg, fg, hv = self.STYLES[self.kind]
        if not self.enabled:
            return RAISED, "#4E6272"
        return (hv if self.hover else bg), fg

    def _paint(self) -> None:
        bg, fg = self._colors()
        w, h, r = self.bw, self.bh, self.RADIUS
        self.delete("all")
        self.create_rectangle(0, 0, w, h, fill=bg, outline="")
        now = time.perf_counter()
        for tc, px, py in self.ripples:
            d = now - tc
            if d < 0 or d > self.LIFE:
                continue
            rm = max(math.hypot(px - cx, py - cy) for cx in (0, w) for cy in (0, h))
            rad = rm * eo(seg(d, 0, self.DUR))
            a = 0.4 * (1 - seg(d, 0.3, 0.8))
            self.create_oval(px - rad, py - rad, px + rad, py + rad, fill=mix(bg, "#ffffff", a), outline="")
        # 圓角：四個角用父層底色蓋掉圓弧外面（等於把波紋裁在圓角按鈕裡）
        for cx, cy, sx, sy in ((r, r, -1, -1), (w - r, r, 1, -1), (r, h - r, -1, 1), (w - r, h - r, 1, 1)):
            pts = [cx + sx * r, cy + sy * r]
            for i in range(9):
                t = i / 8 * math.pi / 2
                pts += [cx + sx * r * math.cos(t), cy + sy * r * math.sin(t)]
            self.create_polygon(pts, fill=self.pbg, outline="")
        self.create_text(w / 2, h / 2 + 1, text=self.text, font=self.fnt, fill=fg)
        if self.focused and self.enabled:
            rrect(self, 1, 1, w - 2, h - 2, r, fill="", outline=FOCUS, width=2)
        self.configure(cursor="hand2" if self.enabled else "arrow")

    def _animate(self) -> None:
        now = time.perf_counter()
        self.ripples = [rp for rp in self.ripples if now - rp[0] <= self.LIFE]
        self._paint()
        if self.ripples:
            self.after(15, self._animate)

    def set(self, text: str | None = None, kind: str | None = None, enabled: bool | None = None) -> None:
        if text is not None and text != self.text:
            self.text = text
            self._size()
        if kind is not None:
            self.kind = kind
        if enabled is not None:
            self.enabled = enabled
            self.configure(takefocus=1 if enabled else 0)
        self._paint()

    def invoke(self, x: float | None = None, y: float | None = None) -> None:
        if not self.enabled:
            return
        busy = bool(self.ripples)
        self.ripples.append((time.perf_counter(), self.bw / 2 if x is None else x, self.bh / 2 if y is None else y))
        if not busy:
            self._animate()
        if self.command:
            self.command()


# ---------- 分頁（Motion Prompt #057 Tabs） ----------

class Tabs(tk.Canvas):
    """點標籤切換下面顯示的面板。
    底線：0.25 秒、easeInOutCubic 移到新標籤（位置和寬度一起變）
    標籤：點下後 0.1 秒切換顏色（選中的粗體、主色，其他灰）
    內容：舊面板 0.2 秒線性淡出；0.1 秒後新面板 0.2 秒淡入並上升 8 px
    """

    def __init__(self, master, labels: list[str], panels: list[tk.Frame], holder: tk.Frame, font_family: str,
                 on_change: Callable[[int], None] | None = None, bg: str = INK, index: int = 0):
        self.fam, self.bg = font_family, bg
        f_on, f_off = (font_family, 12, "bold"), (font_family, 12)
        widths = [tkfont_measure(self, s, f_on) + 28 for s in labels]
        xs, x = [], 0
        for wdt in widths:
            xs.append(x)
            x += wdt + 20
        super().__init__(master, height=46, bg=bg, highlightthickness=0, takefocus=1)
        self.labels, self.panels, self.holder, self.on_change = labels, panels, holder, on_change
        self.xs, self.ws, self.f_on, self.f_off = xs, widths, f_on, f_off
        self.active = index
        self.ux, self.uw = xs[index], widths[index]
        self.divider = self.create_rectangle(0, 42, 4000, 44, fill=LINE, outline="")
        self.under = self.create_rectangle(0, 0, 0, 0, fill=SIGNAL, outline="")
        self.texts = [self.create_text(xs[i] + widths[i] / 2, 21, text=s) for i, s in enumerate(labels)]
        self._draw()
        self.bind("<Button-1>", self._click)
        self.bind("<Left>", lambda e: self.select((self.active - 1) % len(labels)))
        self.bind("<Right>", lambda e: self.select((self.active + 1) % len(labels)))
        self.bind("<FocusIn>", lambda e: self.itemconfigure(self.divider, fill=FOCUS))
        self.bind("<FocusOut>", lambda e: self.itemconfigure(self.divider, fill=LINE))
        self.anim: Anim | None = None
        for i, p in enumerate(panels):
            if i == index:
                p.place(in_=holder, x=0, y=0, relwidth=1, relheight=1)

    def _draw(self) -> None:
        for i, t in enumerate(self.texts):
            on = i == self.active
            self.itemconfigure(t, fill=SIGNAL if on else MUTED, font=self.f_on if on else self.f_off)
        # 4 px 底線，壓在 2 px 分隔線上（原作：分隔線 y=50、底線 y=48）
        self.coords(self.under, self.ux, 40, self.ux + self.uw, 44)

    def _click(self, e) -> None:
        self.focus_set()
        for i, (x, w) in enumerate(zip(self.xs, self.ws)):
            if x <= e.x <= x + w:
                self.select(i)
                return

    def select(self, i: int) -> None:
        if i == self.active or (self.anim and self.anim.alive):
            return
        old, new = self.panels[self.active], self.panels[i]
        fx, fw, tx, tw = self.ux, self.uw, self.xs[i], self.ws[i]
        f_old, f_new = Fader(old), Fader(new)
        f_old.capture()
        state = {"label": False, "new": False}

        def frame(t: float) -> None:
            k = eio(seg(t, 0, .25))
            self.ux, self.uw = lerp(fx, tx, k), lerp(fw, tw, k)
            if t >= .1 and not state["label"]:
                state["label"] = True
                self.active = i
            f_old.apply(1 - seg(t, 0, .2))
            if t >= .1:
                if not state["new"]:
                    state["new"] = True
                    new.place(in_=self.holder, x=0, y=8, relwidth=1, relheight=1)
                    new.lift()
                    f_new.capture()
                a = seg(t, .1, .3)
                f_new.apply(a)
                new.place_configure(y=round(8 * (1 - a)))
            self._draw()

        def done() -> None:
            old.place_forget()
            f_old.apply(1)
            new.place_configure(y=0)
            if self.on_change:
                self.on_change(i)

        self.anim = Anim(self, .3, frame, done)


def tkfont_measure(widget, text: str, font) -> int:
    from tkinter import font as tkfont
    return tkfont.Font(font=font).measure(text)


# ---------- 下拉選單（Motion Prompt #053 Dropdown） ----------

class Dropdown(tk.Canvas):
    """點欄位，下方展開選項；選一個就收起並填入欄位。
    展開：0.25 秒 easeOutCubic；清單淡入並下降 6 px；箭頭轉 180°；欄位外框變 2 px 主色
    選取：清單 0.15 秒收起（1 − easeInOutCubic）；舊值 0.1 秒淡出，0.02 秒後新值 0.1 秒淡入
    鍵盤：Enter／空白鍵展開，上下鍵移動，Enter 選取，Esc 關閉
    """

    W, H, R, ROW = 260, 38, 8, 26

    def __init__(self, master, options: list[tuple[str, str]], value: str, on_change: Callable[[str], None],
                 font_family: str, bg: str = INK, width: int | None = None):
        from tkinter import font as tkfont
        self.W = width or self.W
        # 原作是 38 px 高的欄位、每列 26 px；高解析度螢幕上中文字比 26 px 還高，照字型高度等比放大
        ls = tkfont.Font(font=(font_family, 11)).metrics("linespace")
        self.ROW = max(self.ROW, ls + 8)
        self.H = max(self.H, ls + 16)
        super().__init__(master, width=self.W, height=self.H, bg=bg, highlightthickness=0, takefocus=1,
                         cursor="hand2")
        self.options, self.value, self.on_change, self.fam, self.bg = options, value, on_change, font_family, bg
        self.font, self.font_b = (font_family, 11), (font_family, 11, "bold")
        self.open_amt = 0.0
        self.hi = -1
        self.text_alpha = 1.0
        self.shown = value
        self.focused = False
        self.list: tk.Canvas | None = None
        self.anim: Anim | None = None
        self._draw()
        self.bind("<Button-1>", lambda e: (self.focus_set(), self.toggle()))
        self.bind("<Return>", self._key_enter)
        self.bind("<space>", self._key_enter)
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<Down>", lambda e: self._move(1))
        self.bind("<Up>", lambda e: self._move(-1))
        self.bind("<FocusIn>", lambda e: self._focus(True))
        self.bind("<FocusOut>", lambda e: self._focus(False))

    def label_of(self, v: str) -> str:
        return next((s for k, s in self.options if k == v), v)

    def _focus(self, on: bool) -> None:
        self.focused = on
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        o = self.open_amt
        border = SIGNAL if o > .1 else (FOCUS if self.focused else LINE)
        lw = 2 if o > .1 or self.focused else 1.5
        rrect(self, 1, 1, self.W - 2, self.H - 2, self.R, fill=RAISED, outline=border, width=lw)
        self.create_text(14, self.H / 2, text=self.label_of(self.shown), anchor="w", font=self.font,
                         fill=mix(RAISED, CAPTION, self.text_alpha))
        # 箭頭：隨展開量轉 180°
        cx, cy, s, rot = self.W - 20, self.H / 2, 5, math.pi * o
        pts = []
        for px, py in ((-s, -s / 2), (0, s / 2), (s, -s / 2)):
            pts += [cx + px * math.cos(rot) - py * math.sin(rot), cy + px * math.sin(rot) + py * math.cos(rot)]
        self.create_line(*pts, fill=SOURCE, width=2.2, capstyle="round", joinstyle="round")

    # ---- 清單 ----

    def _ensure_list(self) -> tk.Canvas:
        if self.list is None:
            top = self.winfo_toplevel()
            n = len(self.options)
            self.lh = n * self.ROW + 8
            self.list = tk.Canvas(top, width=self.W + 8, height=self.lh + 14, bg=self.bg, highlightthickness=0)
            self.list.bind("<Motion>", self._hover)
            self.list.bind("<Leave>", lambda e: self._set_hi(-1))
            self.list.bind("<Button-1>", self._pick_click)
        return self.list

    def _list_pos(self) -> tuple[int, int]:
        top = self.winfo_toplevel()
        x = self.winfo_rootx() - top.winfo_rootx()
        y = self.winfo_rooty() - top.winfo_rooty() + self.H + 6  # 原作：欄位 y34+38，清單 y78
        return x, y

    def _draw_list(self) -> None:
        cv, o = self.list, self.open_amt
        if not cv:
            return
        cv.delete("all")
        cv.configure(bg=self.bg)
        oy = -6 * (1 - o)   # 清單從上方 6 px 落下到定位
        # 陰影（原作 blur 18）：用兩層偏暗的色塊模擬
        rrect(cv, 3, oy + 6, self.W, self.lh, self.R, fill=mix(self.bg, "#050a0e", .55 * o), outline="")
        rrect(cv, 1, oy + 3, self.W + 2, self.lh + 1, self.R, fill=mix(self.bg, "#070d12", .35 * o), outline="")
        rrect(cv, 0, oy, self.W, self.lh, self.R, fill=mix(self.bg, RAISED, o), outline=mix(self.bg, LINE, o))
        for i, (_, s) in enumerate(self.options):
            yy = oy + 4 + i * self.ROW
            if i == self.hi:
                rrect(cv, 4, yy, self.W - 8, self.ROW, 5, fill=mix(self.bg, HILITE, o), outline="")
            on = i == self.hi
            cv.create_text(16, yy + self.ROW / 2, text=s, anchor="w", font=self.font_b if on else self.font,
                           fill=mix(self.bg, SIGNAL if on else CAPTION, o))

    def _set_hi(self, i: int) -> None:
        if i != self.hi:
            self.hi = i
            self._draw_list()

    def _hover(self, e) -> None:
        i = int((e.y + 6 * (1 - self.open_amt) - 4) // self.ROW)
        self._set_hi(i if 0 <= i < len(self.options) and 0 <= e.x <= self.W else -1)

    def toggle(self) -> None:
        self.close() if self.open_amt > 0 else self.open()

    def open(self) -> None:
        if self.anim and self.anim.alive:
            return
        cv = self._ensure_list()
        x, y = self._list_pos()
        cv.place(x=x, y=y)
        cv.tk.call("raise", cv._w)  # Canvas.lift 是排畫布裡的物件，不是排視窗
        self.hi = next((i for i, (k, _) in enumerate(self.options) if k == self.value), -1)
        self.winfo_toplevel().bind("<Button-1>", self._outside, add="+")

        def frame(t: float) -> None:
            self.open_amt = eo(seg(t, 0, .25))
            self._draw()
            self._draw_list()
        self.anim = Anim(self, .25, frame)

    def close(self, then: Callable[[], None] | None = None) -> None:
        if not self.list or self.open_amt <= 0:
            return
        if self.anim:
            self.anim.stop()
        start = self.open_amt

        def frame(t: float) -> None:
            self.open_amt = start * (1 - eio(seg(t, 0, .15)))
            self._draw()
            self._draw_list()

        def done() -> None:
            self.open_amt = 0
            self.list.place_forget()
            self._draw()
            if then:
                then()
        self.anim = Anim(self, .15, frame, done)

    def _outside(self, e) -> None:
        if self.open_amt <= 0 or e.widget in (self, self.list):
            return
        self.close()

    def _pick_click(self, e) -> None:
        if 0 <= self.hi < len(self.options):
            self.pick(self.options[self.hi][0])

    def _move(self, d: int) -> None:
        if self.open_amt <= 0:
            self.open()
            return
        self._set_hi((max(self.hi, 0) + d) % len(self.options) if self.hi >= 0 else 0)

    def _key_enter(self, e) -> None:
        if self.open_amt > 0 and 0 <= self.hi < len(self.options):
            self.pick(self.options[self.hi][0])
        else:
            self.toggle()

    def pick(self, v: str) -> None:
        """清單收起；舊值 0.1 秒淡出，0.02 秒後新值 0.1 秒淡入（兩個字不重疊）。"""
        self.close()
        old = self.value
        self.value = v

        def frame(t: float) -> None:
            if t < .1:
                self.shown, self.text_alpha = old, 1 - seg(t, 0, .1)
            else:
                self.shown, self.text_alpha = v, seg(t, .12, .22)
            self._draw()
        Anim(self, .22, frame, lambda: (setattr(self, "text_alpha", 1.0), self._draw()))
        if v != old:
            self.on_change(v)


# ---------- 收音燈、音量 ----------

class Lamp(tk.Canvas):
    COLORS = {"idle": "#3C5262", "loading": "#D9A441", "ready": SIGNAL, "live": TALLY, "error": TALLY}

    def __init__(self, master, bg):
        super().__init__(master, width=24, height=24, bg=bg, highlightthickness=0)
        self.halo = self.create_oval(1, 1, 23, 23, outline="", fill=bg)
        self.dot = self.create_oval(6, 6, 18, 18, outline="", fill=self.COLORS["idle"])
        self.state, self.phase, self.bg = "idle", 0, bg

    def set(self, state: str) -> None:
        self.state = state
        self.itemconfigure(self.dot, fill=self.COLORS[state])
        self.itemconfigure(self.halo, fill=self.bg)
        if state == "live":
            self._pulse()

    def _pulse(self) -> None:
        if self.state != "live":
            return
        self.phase = (self.phase + 1) % 20
        self.itemconfigure(self.halo, fill=mix(self.bg, "#5A222A", abs(10 - self.phase) / 10))
        self.after(80, self._pulse)


class Meter(tk.Canvas):
    N = 12

    def __init__(self, master, bg):
        super().__init__(master, width=self.N * 7, height=16, bg=bg, highlightthickness=0)
        self.cells = [self.create_rectangle(i * 7, 3, i * 7 + 4, 13, outline="", fill=RAISED) for i in range(self.N)]

    def set(self, level: float) -> None:
        lit = round(level * self.N)
        for i, c in enumerate(self.cells):
            self.itemconfigure(c, fill=(("#D9A441" if i >= self.N - 2 else SIGNAL) if i < lit else RAISED))
