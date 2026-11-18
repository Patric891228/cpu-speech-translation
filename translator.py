"""中英語音翻譯（離線、CPU）

語音：麥克風 → Whisper small（faster-whisper，CPU int8）→ TranslateGemma 4B（llama.cpp）
另有「文字翻譯」分頁。Linux / Windows 共用同一個資料夾。

資料夾：
  translator.py, speech.py
  models/translategemma-4b-it.Q4_K_M.gguf
  models/whisper-small/
  bin/linux/llama-server, bin/windows/llama-server.exe
"""
from __future__ import annotations

import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from zhconv import to_traditional

APP_DIR = Path(__file__).resolve().parent
IS_WIN = sys.platform.startswith("win")
SERVER = APP_DIR / "bin" / ("windows/llama-server.exe" if IS_WIN else "linux/llama-server")
MODEL_DIR = APP_DIR / "models"
WHISPER_DIR = MODEL_DIR / "whisper-small"

LANGS = {
    "zh": ("Chinese", "zh", "Traditional Chinese", "zh-TW"),  # 當來源用前兩個，當目標用後兩個
    "en": ("English", "en", "English", "en"),
}


def find_model() -> Path | None:
    files = [f for f in sorted(MODEL_DIR.glob("*.gguf")) if "mmproj" not in f.name]
    for pref in ("Q4_K_M", "Q6_K", "Q8_0"):  # 語音翻譯時 CPU 要分給辨識，優先用小的
        for f in files:
            if pref in f.name:
                return f
    return files[0] if files else None


def build_prompt(text: str, src: str, tgt: str) -> str:
    """TranslateGemma 訓練時用的提示詞格式。"""
    sl, sc = LANGS[src][0], LANGS[src][1]
    tl, tc = LANGS[tgt][2], LANGS[tgt][3]
    extra = " Use the vocabulary and phrasing customary in Taiwan." if tgt == "zh" else ""
    return (f"You are a professional {sl} ({sc}) to {tl} ({tc}) translator. Your goal is to accurately "
            f"convey the meaning and nuances of the original {sl} text while adhering to {tl} grammar, "
            f"vocabulary, and cultural sensitivities.{extra}\nProduce only the {tl} translation, without any "
            f"additional explanations or commentary. Please translate the following {sl} text into {tl}:"
            f"\n\n\n{text}")


CJK = re.compile(r"[㐀-鿿豈-﫿]")
LATIN = re.compile(r"[A-Za-z]")


def detect(text: str) -> str:
    """中文字多就當中文，否則英文。"""
    c, l = len(CJK.findall(text)), len(LATIN.findall(text))
    return "zh" if c and c * 4 >= l else "en"


class Engine:
    """背景跑 llama-server，用 HTTP 呼叫。"""

    def __init__(self, model: Path):
        self.model = model
        self.port = self._free_port()
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()  # 一次只翻一句（語音和文字分頁共用）

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def start(self) -> None:
        # 執行緒數交給 llama.cpp 自己判斷（13 代以後有大小核，它會挑效能核）
        cmd = [str(SERVER), "-m", str(self.model), "--host", "127.0.0.1", "--port", str(self.port),
               "-c", "4096", "--no-webui"]
        kw: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": str(SERVER.parent)}
        if IS_WIN:
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW：不要跳黑視窗
        else:
            env = dict(os.environ)
            env["LD_LIBRARY_PATH"] = str(SERVER.parent) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
            kw["env"] = env
        self.proc = subprocess.Popen(cmd, **kw)
        deadline = time.time() + 300
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("翻譯引擎啟動失敗（llama-server 結束了），可能是記憶體不足。")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                    if json.load(r).get("status") == "ok":
                        return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        raise RuntimeError("翻譯引擎啟動逾時。")

    def translate(self, text: str, src: str, tgt: str) -> str:
        # 自己套 Gemma 對話格式送 /completion（不靠 llama-server 的 chat template，版本間行為不一）
        prompt = f"<start_of_turn>user\n{build_prompt(text, src, tgt)}<end_of_turn>\n<start_of_turn>model\n"
        body = {"prompt": prompt, "temperature": 0, "n_predict": min(2048, len(text) * 4 + 64),
                "stop": ["<end_of_turn>"], "cache_prompt": True}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/completion",
                                     data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with self.lock, urllib.request.urlopen(req, timeout=600) as r:
            out = json.load(r)["content"].strip()
        return to_traditional(out) if tgt == "zh" else out

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


DIRECTIONS = (("auto", "自動判斷"), ("zh", "中文 → 英文"), ("en", "英文 → 中文"))


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()      # 背景執行緒 → 主執行緒
        self.engine: Engine | None = None
        self.rec = None                           # speech.Recognizer
        self.pipe = None                          # speech.Pipeline
        self.trans_q: queue.Queue = queue.Queue() # 語音辨識完、等翻譯的句子
        self.n = 0
        self.busy = False
        self.cancel = False

        root.title("中英語音翻譯（離線）")
        root.geometry("1100x700")
        root.minsize(700, 450)

        fams = set(tkfont.families())
        fam = next((f for f in ("Microsoft JhengHei UI", "Microsoft JhengHei", "Noto Sans CJK TC",
                                "Noto Sans TC", "PingFang TC") if f in fams), None) or "TkDefaultFont"
        if fam != "TkDefaultFont":
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
                tkfont.nametofont(name).configure(family=fam, size=11)
        self.fam = fam
        self.size = tk.IntVar(value=20)

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self._voice_tab(nb)
        self._text_tab(nb)

        self.status = tk.StringVar(value="正在載入模型…（第一次約 10–60 秒）")
        ttk.Label(root, textvariable=self.status, padding=(10, 6), anchor="w").pack(fill="x")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll)
        threading.Thread(target=self.load, daemon=True).start()
        threading.Thread(target=self._translate_loop, daemon=True).start()

    # ---------- 畫面 ----------

    def _voice_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=8)
        nb.add(tab, text="  語音翻譯  ")
        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        self.v_mode = tk.StringVar(value="auto")
        ttk.Label(bar, text="說話語言：").pack(side="left")
        for val, label in DIRECTIONS:
            ttk.Radiobutton(bar, text=label, value=val, variable=self.v_mode).pack(side="left", padx=3)
        self.v_btn = ttk.Button(bar, text="開始", command=self.on_voice, state="disabled", width=8)
        self.v_btn.pack(side="right")
        ttk.Button(bar, text="存檔", command=self.on_save).pack(side="right", padx=4)
        ttk.Button(bar, text="清除", command=self.on_voice_clear).pack(side="right")

        bar2 = ttk.Frame(tab)
        bar2.pack(fill="x", pady=(6, 6))
        ttk.Label(bar2, text="麥克風：").pack(side="left")
        self.mics: list = [(None, "系統預設麥克風")]
        self.mic_box = ttk.Combobox(bar2, state="readonly", width=40, values=[m[1] for m in self.mics])
        self.mic_box.current(0)
        self.mic_box.pack(side="left")
        ttk.Label(bar2, text="  音量").pack(side="left")
        self.meter = ttk.Progressbar(bar2, length=140, maximum=1.0)
        self.meter.pack(side="left", padx=4)
        ttk.Label(bar2, text="字級").pack(side="right")
        ttk.Spinbox(bar2, from_=12, to=48, width=4, textvariable=self.size,
                    command=self._apply_size).pack(side="right", padx=4)

        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True)
        self.log = tk.Text(frame, wrap="word", padx=10, pady=8, state="disabled", cursor="arrow")
        sb = ttk.Scrollbar(frame, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self._apply_size()

    def _apply_size(self) -> None:
        s = self.size.get()
        self.log.tag_configure("orig", font=(self.fam, max(10, int(s * 0.65))), foreground="#777777",
                               spacing1=10)
        self.log.tag_configure("tr", font=(self.fam, s), spacing3=4)
        self.log.tag_configure("wait", font=(self.fam, s), foreground="#aaaaaa", spacing3=4)

    def _text_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=8)
        nb.add(tab, text="  文字翻譯  ")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 6))
        self.t_mode = tk.StringVar(value="auto")
        ttk.Label(top, text="方向：").pack(side="left")
        for val, label in DIRECTIONS:
            ttk.Radiobutton(top, text=label, value=val, variable=self.t_mode).pack(side="left", padx=3)
        self.t_btn = ttk.Button(top, text="翻譯（Ctrl+Enter）", command=self.on_translate, state="disabled")
        self.t_btn.pack(side="right")
        ttk.Button(top, text="清除", command=self.on_text_clear).pack(side="right", padx=6)
        ttk.Button(top, text="複製譯文", command=self.on_copy).pack(side="right")
        body = ttk.PanedWindow(tab, orient="horizontal")
        body.pack(fill="both", expand=True)
        self.src = self._textbox(body, "原文（貼上要翻譯的文字）")
        self.dst = self._textbox(body, "譯文")
        self.src.bind("<Control-Return>", lambda e: (self.on_translate(), "break")[1])

    def _textbox(self, parent, title: str) -> tk.Text:
        frame = ttk.LabelFrame(parent, text=title, padding=4)
        parent.add(frame, weight=1)
        t = tk.Text(frame, wrap="word", font=(self.fam, 14), undo=True, padx=6, pady=6)
        sb = ttk.Scrollbar(frame, command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        return t

    # ---------- 載入 ----------

    def load(self) -> None:
        model = find_model()
        if not SERVER.exists():
            self.q.put(("fatal", f"找不到翻譯引擎：\n{SERVER}"))
            return
        if not model:
            self.q.put(("fatal", f"找不到翻譯模型（.gguf），請放在：\n{MODEL_DIR}"))
            return
        t0 = time.time()
        try:
            eng = Engine(model)
            eng.start()
            self.engine = eng
            self.q.put(("text_ready",))
        except Exception as e:  # noqa: BLE001
            self.q.put(("fatal", str(e)))
            return
        try:
            import speech
            self.q.put(("mics", speech.list_mics()))
            if not (WHISPER_DIR / "model.bin").exists():
                raise RuntimeError(f"找不到語音辨識模型：{WHISPER_DIR}")
            self.rec = speech.Recognizer(WHISPER_DIR)
            self.q.put(("voice_ready", f"就緒（{model.name}＋Whisper small，載入 {time.time() - t0:.0f} 秒）"))
        except Exception as e:  # noqa: BLE001
            self.q.put(("status", f"文字翻譯可用；語音辨識無法使用：{e}"))

    # ---------- 語音 ----------

    def on_voice(self) -> None:
        if self.pipe:
            self.pipe.stop()
            self.pipe = None
            self.v_btn.configure(text="開始")
            self.status.set("已停止")
            self.meter["value"] = 0
            return
        import speech
        mode = self.v_mode.get()
        dev = self.mics[self.mic_box.current()][0]
        pipe = speech.Pipeline(self.rec, on_text=lambda lang, t: self.q.put(("heard", lang, t)),
                               on_error=lambda m: self.q.put(("status", m)))
        try:
            pipe.start(dev, None if mode == "auto" else mode)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("麥克風", str(e))
            return
        self.pipe = pipe
        self.v_btn.configure(text="停止")
        self.status.set("聆聽中…")

    def _heard(self, lang: str, text: str) -> None:
        """辨識出一句：先顯示原文，譯文位置放「翻譯中…」，交給翻譯執行緒。"""
        self.n += 1
        tag = f"s{self.n}"
        at_end = self.log.yview()[1] > 0.98
        self.log.configure(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S}  {text}\n", ("orig",))
        self.log.insert("end", "翻譯中…\n", ("wait", tag))
        self.log.configure(state="disabled")
        if at_end:
            self.log.see("end")
        self.trans_q.put((tag, lang, text))

    def _translated(self, tag: str, text: str) -> None:
        r = self.log.tag_ranges(tag)
        if not r:
            return
        at_end = self.log.yview()[1] > 0.98
        self.log.configure(state="normal")
        self.log.delete(r[0], r[1])
        self.log.insert(r[0], text + "\n", ("tr", tag))
        self.log.configure(state="disabled")
        if at_end:
            self.log.see("end")

    def _translate_loop(self) -> None:
        while True:
            tag, lang, text = self.trans_q.get()
            try:
                out = self.engine.translate(text, lang, "en" if lang == "zh" else "zh")
            except Exception as e:  # noqa: BLE001
                out = f"（翻譯失敗：{e}）"
            self.q.put(("translated", tag, out))

    def on_voice_clear(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def on_save(self) -> None:
        text = self.log.get("1.0", "end").strip()
        if not text:
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("文字檔", "*.txt")],
                                            initialfile=f"語音翻譯_{datetime.now():%Y%m%d_%H%M}.txt")
        if path:
            Path(path).write_text(text + "\n", encoding="utf-8-sig")  # 加 BOM：Windows 記事本不會亂碼
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
                self.q.put(("status", f"翻譯中… {n}/{len(todo)} 段"))
                lines[i] = self.engine.translate(lines[i].strip(), src, tgt)
                self.q.put(("partial", "\n".join(lines[:i + 1])))
            self.q.put(("done", None if self.cancel else "\n".join(lines),
                        f"完成（{'中→英' if src == 'zh' else '英→中'}，{time.time() - t0:.1f} 秒）"))
        except Exception as e:  # noqa: BLE001
            self.q.put(("done", None, f"翻譯失敗：{e}"))

    def on_translate(self) -> None:
        if not self.engine:
            return
        if self.busy:  # 翻譯中再按一次＝停止
            self.cancel = True
            self.t_btn.configure(state="disabled")
            return
        text = self.src.get("1.0", "end").strip()
        if not text:
            return
        src = self.t_mode.get()
        if src == "auto":
            src = detect(text)
        self.busy, self.cancel = True, False
        self.t_btn.configure(text="停止")
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
                    self.t_btn.configure(state="normal")
                    self.status.set("文字翻譯就緒，正在載入語音辨識…")
                elif kind == "mics":
                    self.mics = msg[1]
                    self.mic_box.configure(values=[m[1] for m in self.mics])
                    self.mic_box.current(0)
                elif kind == "voice_ready":
                    self.v_btn.configure(state="normal")
                    self.status.set(msg[1])
                elif kind == "fatal":
                    self.status.set("無法啟動")
                    messagebox.showerror("錯誤", msg[1])
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
                    self.status.set(msg[2] if msg[1] is not None or "失敗" in msg[2] else "已停止")
                    self.busy = False
                    self.t_btn.configure(text="翻譯（Ctrl+Enter）", state="normal")
        except queue.Empty:
            pass
        if self.pipe:
            self.meter["value"] = self.pipe.seg.level
            waiting = self.pipe.backlog + self.trans_q.qsize()
            if waiting >= 3:
                self.status.set(f"聆聽中…（電腦跟不上，排隊 {waiting} 句）")
        self.root.after(100, self.poll)

    def on_close(self) -> None:
        self.cancel = True
        if self.pipe:
            self.pipe.stop()
        if self.engine:
            self.engine.stop()
        self.root.destroy()


def main() -> None:
    try:
        if IS_WIN:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 高解析度螢幕字不要糊（要在建立視窗前）
    except Exception:  # noqa: BLE001
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
