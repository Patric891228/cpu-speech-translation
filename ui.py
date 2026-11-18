"""視窗介面。

語音翻譯分頁只有操作（開始收音、麥克風、存檔…），字幕一律顯示在懸浮字幕視窗：
按「開始收音」就自動彈出，蓋在簡報、會議軟體上面。主視窗預設釘選在最上層。
"""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

import translator as core
from overlay import Overlay, Settings
from widgets import (CAPTION, DECK, FOCUS, INK, LINE, MUTED, RAISED, SIGNAL, SOURCE, Btn, Dropdown, Lamp,
                     Meter, Tabs)

DIRECTIONS = [("auto", "自動判斷"), ("zh", "中文 → 英文"), ("en", "英文 → 中文")]


def pick_family() -> str:
    fams = set(tkfont.families())
    for f in ("Microsoft JhengHei UI", "Microsoft JhengHei", "Noto Sans CJK TC", "Noto Sans TC",
              "PingFang TC", "Source Han Sans TC"):
        if f in fams:
            return f
    return "TkDefaultFont"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings(core.APP_DIR / "settings.json")
        self.q: queue.Queue = queue.Queue()
        self.engine: core.Engine | None = None
        self.rec = None
        self.pipe = None
        self.trans_q: queue.Queue = queue.Queue()
        self.overlay: Overlay | None = None
        self.records: dict[str, dict] = {}   # 這次收音的內容（存檔用）
        self.n = 0
        self.busy = False
        self.cancel = False
        self.started_at: float | None = None

        root.title("語音翻譯")
        root.configure(bg=INK)
        root.geometry(self.settings.get("main_geom") or "920x540")
        root.minsize(860, 520)
        self.fam = fam = pick_family()
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            try:
                tkfont.nametofont(name).configure(family=fam, size=11)
            except tk.TclError:
                pass
        self.f_ui, self.f_small, self.f_btn = (fam, 11), (fam, 10), (fam, 12, "bold")
        self._style_ttk()
        self.pin = bool(self.settings.get("pin", True))  # 預設釘選在最上層
        root.attributes("-topmost", self.pin)
        self.mode = self.settings.get("v_mode", "auto")

        self.status = tk.StringVar(value="正在載入模型，第一次約需 10–60 秒")
        tk.Label(root, textvariable=self.status, bg=DECK, fg=MUTED, font=self.f_small, anchor="w",
                 padx=24, pady=7).pack(side="bottom", fill="x")

        top = tk.Frame(root, bg=INK, padx=24)
        top.pack(fill="x", pady=(14, 0))
        self.holder = tk.Frame(root, bg=INK)
        self.holder.pack(fill="both", expand=True)
        self.v_panel = self._voice_panel()
        self.t_panel = self._text_panel()

        right = tk.Frame(top, bg=INK)
        right.pack(side="right", anchor="n")
        tk.Label(right, text="翻譯方向", bg=INK, fg=MUTED, font=self.f_small).pack(side="left", padx=(0, 8))
        self.dir_dd = Dropdown(right, DIRECTIONS, self.mode, self._set_mode, fam, bg=INK, width=190)
        self.dir_dd.pack(side="left")
        self.tabs = Tabs(top, ["語音翻譯", "文字翻譯"], [self.v_panel, self.t_panel], self.holder, fam)
        self.tabs.pack(side="left", fill="x", expand=True, padx=(0, 20))

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll)
        root.after(1000, self._tick)
        threading.Thread(target=self.load, daemon=True).start()
        threading.Thread(target=self._translate_loop, daemon=True).start()

    def _style_ttk(self) -> None:
        st = ttk.Style()
        st.theme_use("clam")
        st.configure("Deck.TCombobox", fieldbackground=RAISED, background=RAISED, foreground=CAPTION,
                     arrowcolor=SOURCE, bordercolor=LINE, lightcolor=RAISED, darkcolor=RAISED, padding=6)
        st.map("Deck.TCombobox", fieldbackground=[("readonly", RAISED)], foreground=[("readonly", CAPTION)],
               selectbackground=[("readonly", RAISED)], selectforeground=[("readonly", CAPTION)],
               bordercolor=[("focus", FOCUS)])
        self.root.option_add("*TCombobox*Listbox.background", RAISED)
        self.root.option_add("*TCombobox*Listbox.foreground", CAPTION)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#2F5D57")
        self.root.option_add("*TCombobox*Listbox.font", self.f_ui)

    def _set_mode(self, v: str) -> None:
        self.mode = v
        self.settings.set("v_mode", v)
        if self.pipe:  # 收音中切換：下一句就用新設定
            self.pipe.lang = None if v == "auto" else v

    # ---------- 語音翻譯分頁 ----------

    def _voice_panel(self) -> tk.Frame:
        p = tk.Frame(self.holder, bg=INK, padx=24, pady=22)
        row = tk.Frame(p, bg=INK)
        row.pack(fill="x")
        self.v_btn = Btn(row, "開始收音", self.on_voice, kind="primary", font=self.f_btn, padx=30, pady=12, bg=INK)
        self.v_btn.set(enabled=False)
        self.v_btn.pack(side="left")
        self.lamp = Lamp(row, INK)
        self.lamp.pack(side="left", padx=(22, 6))
        self.lamp.set("loading")
        self.live_lbl = tk.Label(row, text="載入中", bg=INK, fg=SOURCE, font=(self.fam, 14), anchor="w")
        self.live_lbl.pack(side="left")
        self.count_lbl = tk.Label(row, text="", bg=INK, fg=MUTED, font=self.f_small)
        self.count_lbl.pack(side="right")

        hint = tk.Label(p, text="字幕會顯示在懸浮字幕視窗，可以拖到簡報或會議畫面上。",
                        bg=INK, fg=MUTED, font=self.f_small, anchor="w", justify="left")
        hint.pack(fill="x", pady=(16, 18))
        hint.bind("<Configure>", lambda e: hint.configure(wraplength=max(200, e.width - 4)))

        mic = tk.Frame(p, bg=INK)
        mic.pack(fill="x")
        tk.Label(mic, text="麥克風", bg=INK, fg=MUTED, font=self.f_small).pack(side="left", padx=(0, 10))
        self.mics: list = [(None, "系統預設麥克風")]
        self.mic_box = ttk.Combobox(mic, state="readonly", width=28, style="Deck.TCombobox", font=self.f_ui,
                                    values=[m[1] for m in self.mics])
        self.mic_box.current(0)
        self.mic_box.pack(side="left")
        self.meter = Meter(mic, INK)
        self.meter.pack(side="left", padx=14)

        tk.Frame(p, bg=LINE, height=1).pack(fill="x", pady=(22, 14))
        tools = tk.Frame(p, bg=INK)
        tools.pack(fill="x")
        self.ov_btn = Btn(tools, "懸浮字幕", self.on_overlay, font=self.f_ui, bg=INK)
        self.ov_btn.pack(side="left")
        Btn(tools, "名詞對照表", self.on_glossary, font=self.f_ui, bg=INK).pack(side="left", padx=(6, 0))
        self.clear_btn = Btn(tools, "清除", self.on_voice_clear, font=self.f_ui, bg=INK)
        self.clear_btn.pack(side="right")
        self.save_btn = Btn(tools, "存檔", self.on_save, font=self.f_ui, bg=INK)
        self.save_btn.pack(side="right", padx=(0, 6))
        self._sync_record_btns()
        return p

    def _sync_record_btns(self) -> None:
        """存檔、清除：收音結束、而且有內容時才能按。"""
        ok = self.pipe is None and bool(self.records)
        self.save_btn.set(enabled=ok)
        self.clear_btn.set(enabled=ok)
        n = sum(1 for r in self.records.values() if r["tr"])
        self.count_lbl.configure(text=f"已翻譯 {n} 句" if self.records else "")

    # ---------- 文字翻譯分頁 ----------

    def _text_panel(self) -> tk.Frame:
        p = tk.Frame(self.holder, bg=INK, padx=24, pady=16)
        p.columnconfigure(0, weight=1, uniform="c")
        p.columnconfigure(1, weight=1, uniform="c")
        p.rowconfigure(1, weight=1)
        for col, title in ((0, "原文"), (1, "譯文")):
            tk.Label(p, text=title, bg=INK, fg=MUTED, font=self.f_small, anchor="w").grid(
                row=0, column=col, sticky="w", padx=(0 if col == 0 else 8, 0), pady=(0, 6))
        self.src = self._textbox(p, 0)
        self.dst = self._textbox(p, 1)
        self.src.bind("<Control-Return>", lambda e: (self.on_translate(), "break")[1])
        bar = tk.Frame(p, bg=INK)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.t_btn = Btn(bar, "翻譯", self.on_translate, kind="primary", font=self.f_btn, padx=26, pady=9, bg=INK)
        self.t_btn.set(enabled=False)
        self.t_btn.pack(side="left")
        tk.Label(bar, text="Ctrl + Enter", bg=INK, fg=MUTED, font=self.f_small).pack(side="left", padx=12)
        Btn(bar, "清除", self.on_text_clear, font=self.f_ui, bg=INK).pack(side="right")
        Btn(bar, "複製譯文", self.on_copy, font=self.f_ui, bg=INK).pack(side="right", padx=(0, 6))
        Btn(bar, "名詞對照表", self.on_glossary, font=self.f_ui, bg=INK).pack(side="right", padx=(0, 6))
        return p

    def _textbox(self, parent, col: int) -> tk.Text:
        t = tk.Text(parent, wrap="word", font=(self.fam, 14), undo=True, padx=16, pady=12, bg=DECK,
                    fg=CAPTION, relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=LINE,
                    highlightcolor=FOCUS, insertbackground=CAPTION, selectbackground="#2F5D57", spacing3=6,
                    height=6)
        t.grid(row=1, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
        return t

    # ---------- 載入 ----------

    def load(self) -> None:
        model = core.find_model()
        if not core.SERVER.exists():
            self.q.put(("fatal", f"找不到翻譯引擎：\n{core.SERVER}\n\n請重新執行啟動檔完成安裝。"))
            return
        if not model:
            self.q.put(("fatal", f"找不到翻譯模型（.gguf）：\n{core.MODEL_DIR}\n\n請重新執行啟動檔完成安裝。"))
            return
        t0 = time.time()
        try:
            eng = core.Engine(model)
            eng.start()
            self.engine = eng
            self.q.put(("text_ready",))
        except Exception as e:  # noqa: BLE001
            self.q.put(("fatal", str(e)))
            return
        try:
            import speech
            self.q.put(("mics", speech.list_mics()))
            if not (core.WHISPER_DIR / "model.bin").exists():
                raise RuntimeError(f"找不到語音辨識模型：{core.WHISPER_DIR}")
            self.rec = speech.Recognizer(core.WHISPER_DIR)
            self.q.put(("voice_ready", f"就緒。翻譯 {model.stem}，語音辨識 Whisper small，載入 {time.time() - t0:.0f} 秒"))
        except Exception as e:  # noqa: BLE001
            self.q.put(("voice_fail", f"語音辨識無法使用（{e}），文字翻譯可以用"))

    # ---------- 語音 ----------

    def on_voice(self) -> None:
        if self.pipe:
            self.pipe.stop()
            self.pipe = None
            self.started_at = None
            self.v_btn.set("開始收音", kind="primary")
            self.lamp.set("ready")
            self.live_lbl.configure(text="待機", fg=SOURCE)
            self.meter.set(0)
            self.status.set("已停止收音。可以存檔，或清除後重新開始")
            self._sync_record_btns()
            return
        import speech
        dev = self.mics[self.mic_box.current()][0]
        pipe = speech.Pipeline(self.rec, on_text=lambda lang, t: self.q.put(("heard", lang, t)),
                               on_error=lambda m: self.q.put(("status", m)))
        try:
            pipe.start(dev, None if self.mode == "auto" else self.mode)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("麥克風", f"{e}\n\n請確認麥克風已接上，並在系統設定允許使用。")
            return
        self.pipe = pipe
        self.started_at = time.time()
        self.v_btn.set("停止收音", kind="danger")
        self.lamp.set("live")
        self.live_lbl.configure(text="收音中 0:00", fg=CAPTION)
        self.status.set("收音中。每句停頓約 0.6 秒後送出辨識")
        self._sync_record_btns()
        if not self.overlay:
            self.on_overlay()

    def _tick(self) -> None:
        if self.started_at:
            s = int(time.time() - self.started_at)
            t = f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
            self.live_lbl.configure(text=f"收音中 {t}")
        self.root.after(1000, self._tick)

    def _heard(self, lang: str, text: str) -> None:
        text = core.GLOSSARY.correct(text)
        self.n += 1
        tag = f"s{self.n}"
        self.records[tag] = {"time": datetime.now(), "src": text, "tr": None}
        if self.overlay:
            self.overlay.update_entry(tag, text, None)
        self._sync_record_btns()
        self.trans_q.put((tag, lang, text))

    def _translated(self, tag: str, text: str) -> None:
        if tag in self.records:
            self.records[tag]["tr"] = text
        if self.overlay:
            self.overlay.update_entry(tag, self.records.get(tag, {}).get("src", ""), text)
        self._sync_record_btns()

    def _translate_loop(self) -> None:
        while True:
            tag, lang, text = self.trans_q.get()
            try:
                out = self.engine.translate(text, lang, "en" if lang == "zh" else "zh")
            except Exception as e:  # noqa: BLE001
                out = f"（翻譯失敗：{e}）"
            self.q.put(("translated", tag, out))

    def on_overlay(self) -> None:
        if self.overlay:
            self.overlay.close()
            return
        self.overlay = Overlay(self.root, self.settings, self.fam, on_close=self._overlay_closed,
                               live=lambda: self.pipe is not None)
        self.ov_btn.set(kind="on")
        for tag in list(self.records)[-2:]:
            r = self.records[tag]
            self.overlay.update_entry(tag, r["src"], r["tr"])

    def _overlay_closed(self) -> None:
        self.overlay = None
        self.ov_btn.set(kind="ghost")

    def on_glossary(self) -> None:
        path = core.GLOSSARY.path
        try:
            if core.IS_WIN:
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.status.set("已開啟名詞對照表。存檔後，下一句就會套用")
        except Exception as e:  # noqa: BLE001
            messagebox.showinfo("名詞對照表", f"請用 Excel 或記事本開啟：\n{path}\n\n（{e}）")

    def on_voice_clear(self) -> None:
        if self.pipe or not self.records:
            return
        self.records.clear()
        if self.overlay:
            self.overlay.clear()
        self._sync_record_btns()
        self.status.set("已清除")

    def on_save(self) -> None:
        if self.pipe or not self.records:
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("文字檔", "*.txt")],
                                            initialfile=f"語音翻譯_{datetime.now():%Y%m%d_%H%M}.txt")
        if not path:
            return
        lines = []
        for r in self.records.values():
            lines.append(f"{r['time']:%H:%M:%S}  {r['src']}")
            lines.append(f"          {r['tr'] or '（未翻譯）'}")
            lines.append("")
        Path(path).write_text("\n".join(lines), encoding="utf-8-sig")  # 加 BOM：Windows 記事本不會亂碼
        self.status.set(f"已存檔：{path}")

    # ---------- 文字 ----------

    def work(self, text: str, src: str) -> None:
        tgt = "en" if src == "zh" else "zh"
        lines = text.split("\n")
        todo = [i for i, l in enumerate(lines) if l.strip()]
        t0 = time.time()
        try:
            for n, i in enumerate(todo, 1):
                if self.cancel:
                    break
                self.q.put(("status", f"翻譯中，第 {n} 段，共 {len(todo)} 段"))
                lines[i] = self.engine.translate(lines[i].strip(), src, tgt)
                self.q.put(("partial", "\n".join(lines[:i + 1])))
            self.q.put(("done", None if self.cancel else "\n".join(lines),
                        f"已翻譯成{'英文' if src == 'zh' else '中文'}，用了 {time.time() - t0:.1f} 秒"))
        except Exception as e:  # noqa: BLE001
            self.q.put(("done", None, f"翻譯失敗：{e}"))

    def on_translate(self) -> None:
        if not self.engine:
            return
        if self.busy:
            self.cancel = True
            self.t_btn.set(enabled=False)
            return
        text = self.src.get("1.0", "end").strip()
        if not text:
            self.status.set("請先在左邊貼上要翻譯的文字")
            return
        src = core.detect(text) if self.mode == "auto" else self.mode
        self.busy, self.cancel = True, False
        self.t_btn.set("停止", kind="danger")
        self.dst.delete("1.0", "end")
        threading.Thread(target=self.work, args=(text, src), daemon=True).start()

    def _set_dst(self, text: str) -> None:
        self.dst.delete("1.0", "end")
        self.dst.insert("1.0", text)
        self.dst.see("end")

    def on_copy(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.dst.get("1.0", "end").strip())
        self.status.set("已複製譯文")

    def on_text_clear(self) -> None:
        self.src.delete("1.0", "end")
        self.dst.delete("1.0", "end")

    # ---------- 主執行緒 ----------

    def poll(self) -> None:
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "text_ready":
                    self.t_btn.set(enabled=True)
                    self.status.set("文字翻譯可以用了，正在載入語音辨識")
                elif kind == "mics":
                    self.mics = msg[1]
                    self.mic_box.configure(values=[m[1] for m in self.mics])
                    self.mic_box.current(0)
                elif kind == "voice_ready":
                    self.v_btn.set(enabled=True)
                    self.lamp.set("ready")
                    self.live_lbl.configure(text="待機")
                    self.status.set(msg[1])
                elif kind == "voice_fail":
                    self.lamp.set("error")
                    self.live_lbl.configure(text="無法收音")
                    self.status.set(msg[1])
                elif kind == "fatal":
                    self.lamp.set("error")
                    self.live_lbl.configure(text="無法啟動")
                    self.status.set("無法啟動，請看錯誤訊息")
                    messagebox.showerror("無法啟動", msg[1])
                elif kind == "status":
                    self.status.set(msg[1])
                elif kind == "heard":
                    self._heard(msg[1], msg[2])
                elif kind == "translated":
                    self._translated(msg[1], msg[2])
                elif kind == "partial":
                    self._set_dst(msg[1])
                elif kind == "done":
                    if msg[1] is not None:
                        self._set_dst(msg[1])
                    self.status.set(msg[2] if msg[1] is not None or "失敗" in msg[2] else "已停止翻譯")
                    self.busy = False
                    self.t_btn.set("翻譯", kind="primary", enabled=True)
        except queue.Empty:
            pass
        if self.pipe:
            self.meter.set(self.pipe.seg.level)
            waiting = self.pipe.backlog + self.trans_q.qsize()
            if waiting >= 3:
                self.status.set(f"電腦跟不上說話速度，還有 {waiting} 句在排隊")
        self.root.after(100, self.poll)

    def on_close(self) -> None:
        self.cancel = True
        self.settings.set("main_geom", self.root.geometry())
        if self.overlay:
            self.overlay.close()
        if self.pipe:
            self.pipe.stop()
        if self.engine:
            self.engine.stop()
        self.root.destroy()


def run() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
