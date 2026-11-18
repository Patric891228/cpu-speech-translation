"""中英語音翻譯（離線、CPU）

語音：麥克風 → Whisper small（faster-whisper，CPU int8）→ TranslateGemma 4B（llama.cpp）
翻譯引擎在這裡；視窗在 ui.py、懸浮字幕在 overlay.py。Linux / Windows 共用同一個資料夾。

資料夾：
  translator.py, speech.py
  models/translategemma-4b-it.Q4_K_M.gguf
  models/whisper-small/
  bin/linux/llama-server, bin/windows/llama-server.exe
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from glossary import Glossary
from zhconv import to_traditional

# 打包成 exe 時（PyInstaller），模型、設定、名詞對照表放在 exe 旁邊，不在暫存的程式資料夾
FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
IS_WIN = sys.platform.startswith("win")
SERVER = APP_DIR / "bin" / ("windows/llama-server.exe" if IS_WIN else "linux/llama-server")
MODEL_DIR = APP_DIR / "models"
WHISPER_DIR = MODEL_DIR / "whisper-small"
GLOSSARY = Glossary(APP_DIR / "名詞對照表.csv")

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


def build_prompt(text: str, src: str, tgt: str, terms: list[tuple[str, str]] | None = None) -> str:
    """TranslateGemma 訓練時用的提示詞格式；有名詞對照時，原文先換成指定譯名，前面再加上對照說明。"""
    head = ""
    if terms:
        text = Glossary.apply(text, terms, quote=tgt == "zh")
        head = "Refer to the following translations:\n" + \
               "\n".join(f"{s} is translated as {t}" for s, t in terms) + "\n\n"
    sl, sc = LANGS[src][0], LANGS[src][1]
    tl, tc = LANGS[tgt][2], LANGS[tgt][3]
    extra = " Use the vocabulary and phrasing customary in Taiwan." if tgt == "zh" else ""
    return head + (f"You are a professional {sl} ({sc}) to {tl} ({tc}) translator. Your goal is to accurately "
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
        terms = GLOSSARY.terms(text, src)
        prompt = f"<start_of_turn>user\n{build_prompt(text, src, tgt, terms)}<end_of_turn>\n<start_of_turn>model\n"
        body = {"prompt": prompt, "temperature": 0, "n_predict": min(2048, len(text) * 4 + 64),
                "stop": ["<end_of_turn>"], "cache_prompt": True}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/completion",
                                     data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with self.lock, urllib.request.urlopen(req, timeout=600) as r:
            out = json.load(r)["content"].strip()
        out = Glossary.unquote(out, terms)
        return to_traditional(out) if tgt == "zh" else out

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def selftest() -> None:
    """打包後的自我檢查（GitHub Actions 的 Windows 主機上執行）：每個用到的元件都要載得起來。
    結果寫到 exe 旁邊的 selftest.txt（視窗程式沒有主控台輸出）。"""
    lines, ok = [], True

    def check(name, fn, required=True):
        nonlocal ok
        try:
            lines.append(f"OK   {name}: {fn()}")
        except Exception as e:  # noqa: BLE001
            ok = ok and not required
            lines.append(f"{'FAIL' if required else 'WARN'} {name}: {type(e).__name__}: {e}")

    check("tkinter", lambda: __import__("tkinter").TkVersion)
    check("ctranslate2", lambda: __import__("ctranslate2").__version__)
    check("faster_whisper", lambda: __import__("faster_whisper").__version__)
    # Linux 沒裝 PortAudio 時改用 arecord，不算失敗；Windows 一定要有
    check("sounddevice/PortAudio", lambda: __import__("sounddevice").get_portaudio_version()[1], required=IS_WIN)
    check("opencc", lambda: to_traditional("贊成代表比例的产生"))
    check("speech", lambda: __import__("speech").SR)
    check("ui", lambda: __import__("ui").DIRECTIONS[0][1])
    check("glossary", lambda: len(GLOSSARY.terms("疾病管制署今天宣布", "zh")))
    check("llama-server", lambda: subprocess.run([str(SERVER), "--version"], capture_output=True, text=True,
                                                 timeout=60, cwd=str(SERVER.parent)).stderr.strip().splitlines()[0])
    lines.append("RESULT " + ("PASS" if ok else "FAIL"))
    (APP_DIR / "selftest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.exit(0 if ok else 1)


def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
    try:
        if IS_WIN:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 高解析度螢幕字不要糊（要在建立視窗前）
    except Exception:  # noqa: BLE001
        pass
    import ui
    ui.run()


if __name__ == "__main__":
    main()
