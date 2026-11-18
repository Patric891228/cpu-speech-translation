"""語音部分：麥克風收音 → 切句 → Whisper 辨識（faster-whisper，CPU int8）。"""
from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import zlib
from pathlib import Path
from typing import Callable

import numpy as np

from zhconv import to_traditional

SR = 16000
FRAME = 480  # 30 ms


# ---------- 收音 ----------

def list_mics() -> list[tuple[int | None, str]]:
    """[(裝置編號, 名稱)]；第一個一定是系統預設。"""
    out: list[tuple[int | None, str]] = [(None, "系統預設麥克風")]
    try:
        import sounddevice as sd
        seen = set()
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and d["name"] not in seen:
                # Windows 同一支麥克風會在 MME／DirectSound／WASAPI 各列一次，只留第一個
                seen.add(d["name"])
                out.append((i, d["name"]))
    except Exception:  # noqa: BLE001  沒有 PortAudio（Linux 未裝 libportaudio2）就只有系統預設
        pass
    return out


class Mic:
    """把 16 kHz 單聲道 float32 音訊一塊一塊交給 on_audio。
    優先用 sounddevice；Linux 沒裝 PortAudio 時改用 arecord（系統內建）。"""

    def __init__(self, device: int | None, on_audio: Callable[[np.ndarray], None]):
        self.device, self.on_audio = device, on_audio
        self.stream = None
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        try:
            import sounddevice as sd
            self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=FRAME * 4,
                                         device=self.device,
                                         callback=lambda data, *_: self.on_audio(data[:, 0].copy()))
            self.stream.start()
            return
        except Exception as e:  # noqa: BLE001
            if sys.platform.startswith("win") or not shutil.which("arecord"):
                raise RuntimeError(f"無法開啟麥克風：{e}") from e
        self.proc = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", str(SR), "-c", "1", "-t", "raw"],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._read_arecord, args=(self.proc,), daemon=True).start()

    def _read_arecord(self, proc: subprocess.Popen) -> None:
        while proc.stdout:
            buf = proc.stdout.read(FRAME * 4 * 2)
            if not buf:
                break
            self.on_audio(np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768)

    def stop(self) -> None:
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if self.proc:
            self.proc.terminate()
            self.proc = None


# ---------- 切句（以音量判斷有沒有人在講話） ----------

class Segmenter:
    """停頓 0.6 秒就切一句；一句最長 12 秒；有聲音的部分不到 0.3 秒不算（敲桌子、呼吸、碰到麥克風）。"""

    def __init__(self, pause_s: float = 0.6, max_s: float = 12.0, min_voiced_s: float = 0.3, pre_s: float = 0.3):
        self.pause_n = int(pause_s * SR / FRAME)
        self.max_n = int(max_s * SR / FRAME)
        self.min_voiced = int(min_voiced_s * SR / FRAME)
        self.pre_n = int(pre_s * SR / FRAME)
        self.buf = np.zeros(0, np.float32)
        self.floor = 0.003           # 背景噪音（自動追蹤）
        self.frames: list[np.ndarray] = []
        self.pre: list[np.ndarray] = []
        self.voiced = 0
        self.silent = 0
        self.level = 0.0             # 給畫面的音量表（0–1）

    def feed(self, audio: np.ndarray) -> list[np.ndarray]:
        out = []
        self.buf = np.concatenate([self.buf, audio])
        while len(self.buf) >= FRAME:
            f, self.buf = self.buf[:FRAME], self.buf[FRAME:]
            rms = float(np.sqrt(np.mean(f * f)) + 1e-9)
            self.level = min(1.0, max(0.0, (20 * np.log10(rms) + 60) / 60))
            speech = rms > max(self.floor * 3, 0.006)
            if not speech:  # 安靜時慢慢跟上背景噪音
                self.floor = 0.95 * self.floor + 0.05 * rms
            if self.frames:
                self.frames.append(f)
                self.voiced += speech
                self.silent = 0 if speech else self.silent + 1
                if self.silent >= self.pause_n or len(self.frames) >= self.max_n:
                    if self.voiced >= self.min_voiced:
                        out.append(np.concatenate(self.frames))
                    self.frames, self.voiced, self.silent = [], 0, 0
            elif speech:
                self.frames = self.pre + [f]
                self.voiced, self.silent = 1, 0
                self.pre = []
            else:
                self.pre = (self.pre + [f])[-self.pre_n:]
        return out


# ---------- 辨識 ----------

# Whisper 在安靜、雜音時常寫出影片結尾的套話，或同一個字重複
STOCK = {"謝謝大家", "謝謝觀看", "感謝觀看", "謝謝收看", "感謝收看", "謝謝", "谢谢观看", "谢谢大家",
         "字幕由amaraorg社區提供", "字幕由amaraorg社区提供", "中文字幕志願者", "中文字幕志愿者",
         "請不吝點贊訂閱轉發打賞支持明鏡與點點欄目", "请不吝点赞订阅转发打赏支持明镜与点点栏目",
         "thankyouforwatching", "thanksforwatching", "thankyou", "pleasesubscribe", "you", "bye",
         "subtitlesbytheamaraorgcommunity"}
# 只會出現在影片結尾的話：夾在句子裡也一定是 Whisper 自己編的，整段丟掉
ALWAYS_FAKE = ("thanksforwatching", "thankyouforwatching", "endofthisvideo", "pleasesubscribe",
               "subscribetomychannel", "amaraorg", "謝謝觀看", "感謝觀看", "謝謝收看", "谢谢观看", "字幕志願者",
               "字幕志愿者", "點贊訂閱", "点赞订阅")
FILLERS = {"嗯", "嗯嗯", "啊", "呃", "欸", "喔", "哦", "uh", "um", "umm", "hmm", "mm", "ah", "oh"}
LOOP = re.compile(r"(.{1,12}?)(?:\s*\1){4,}", re.S)


def _key(t: str) -> str:
    return re.sub(r"[\W_]+", "", t.lower())


def is_junk(text: str) -> bool:
    k = _key(text)
    if not k or k in STOCK or k in FILLERS or any(f in k for f in ALWAYS_FAKE):
        return True
    data = text.encode()
    if LOOP.search(text) or (len(data) > 40 and len(data) / len(zlib.compress(data)) > 2.4):
        return True
    return False


class Recognizer:
    def __init__(self, model_dir: Path):
        from faster_whisper import WhisperModel
        threads = max(2, min(8, (os.cpu_count() or 4) // 2))
        self.model = WhisperModel(str(model_dir), device="cpu", compute_type="int8", cpu_threads=threads)

    def transcribe(self, audio: np.ndarray, lang: str | None) -> tuple[str, str]:
        """回傳 (語言, 文字)；判斷是雜音、幻覺時文字為空。"""
        def run(language):
            return self.model.transcribe(
                audio, language=language, beam_size=1, without_timestamps=True,
                condition_on_previous_text=False,
                # 預設會在信心低時用 0.2、0.4…1.0 重試六次：雜音一段要 10 秒，CPU 上會塞車；只重試一次
                temperature=(0.0, 0.6),
                # 提示詞只用同一種語言（10-28 P1 實測：跨語言提示詞會讓 Whisper 漏句）
                initial_prompt="以下是台灣的會議發言，使用繁體中文。" if language == "zh" else None)

        if lang:
            segs, _ = run(lang)
        else:  # 自動：辨識時順便判斷語言；判斷成中英以外才重跑一次
            segs, info = run(None)
            lang = info.language
            if lang not in ("zh", "en"):
                p = dict(info.all_language_probs or [])
                lang = "zh" if p.get("zh", 0) >= p.get("en", 0) else "en"
                segs, _ = run(lang)
        parts = []
        for s in segs:
            if s.no_speech_prob > 0.6 and s.avg_logprob < -0.8:
                continue
            if s.avg_logprob < -1.2 or is_junk(s.text):
                continue
            parts.append(s.text.strip())
        text = ("" if lang == "zh" else " ").join(parts).strip()
        if lang == "zh":
            text = to_traditional(text)
        return lang, text


class Pipeline:
    """收音 → 切句 → 辨識，在背景執行緒跑；辨識結果用 on_text(語言, 文字) 交出去。"""

    def __init__(self, rec: Recognizer, on_text: Callable[[str, str], None], on_error: Callable[[str], None]):
        self.rec, self.on_text, self.on_error = rec, on_text, on_error
        self.seg = Segmenter()
        self.audio_q: queue.Queue = queue.Queue()
        self.utter_q: queue.Queue = queue.Queue()
        self.mic: Mic | None = None
        self.running = False
        self.lang: str | None = None

    @property
    def backlog(self) -> int:
        return self.utter_q.qsize()

    def start(self, device: int | None, lang: str | None) -> None:
        self.lang = lang
        self.running = True
        self.seg = Segmenter()
        threading.Thread(target=self._cut, daemon=True).start()
        threading.Thread(target=self._asr, daemon=True).start()
        self.mic = Mic(device, self.audio_q.put)
        self.mic.start()

    def stop(self) -> None:
        self.running = False
        if self.mic:
            self.mic.stop()
            self.mic = None
        self.audio_q.put(None)
        self.utter_q.put(None)

    def _cut(self) -> None:
        while self.running:
            a = self.audio_q.get()
            if a is None:
                break
            for u in self.seg.feed(a):
                self.utter_q.put(u)

    def _asr(self) -> None:
        while self.running:
            u = self.utter_q.get()
            if u is None:
                break
            try:
                lang, text = self.rec.transcribe(u, self.lang)
                if text:
                    self.on_text(lang, text)
            except Exception as e:  # noqa: BLE001
                self.on_error(f"辨識失敗：{e}")
