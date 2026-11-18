"""第一次執行時的安裝（只用 Python 內建函式庫）：
1. 建立 .venv，安裝 requirements.txt（faster-whisper、sounddevice）
2. 下載 llama.cpp 官方 CPU 版到 bin/<系統>/
3. 下載模型：TranslateGemma 4B Q4（約 2.5GB）、Whisper small（約 0.5GB）
已經有的會跳過；下載中斷可以重新執行，會接著下載。之後就不需要網路。
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
import urllib.request
import venv
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parent
IS_WIN = sys.platform.startswith("win")
VENV = APP / ".venv"
VPY = VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")

LLAMA_BUILD = "b12494"
LLAMA_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BUILD}/llama-{LLAMA_BUILD}-bin-" + \
            ("win-cpu-x64.zip" if IS_WIN else "ubuntu-x64.tar.gz")
BIN = APP / "bin" / ("windows" if IS_WIN else "linux")
SERVER = BIN / ("llama-server.exe" if IS_WIN else "llama-server")

HF = "https://huggingface.co"
MODELS = [
    (APP / "models/translategemma-4b-it.Q4_K_M.gguf",
     f"{HF}/mradermacher/translategemma-4b-it-GGUF/resolve/main/translategemma-4b-it.Q4_K_M.gguf"),
] + [
    (APP / "models/whisper-small" / f, f"{HF}/Systran/faster-whisper-small/resolve/main/{f}")
    for f in ("config.json", "tokenizer.json", "vocabulary.txt", "model.bin")
]


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        if have and r.status != 206:  # 伺服器不支援續傳：重來
            have = 0
        total = have + int(r.headers.get("Content-Length") or 0)
        with open(part, "ab" if have else "wb") as f:
            done = have
            while chunk := r.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {dest.name}：{done / 2**20:,.0f} / {total / 2**20:,.0f} MB", end="", flush=True)
    print()
    part.replace(dest)


def setup_venv() -> None:
    marker = VENV / ".installed"
    req = (APP / "requirements.txt").read_text(encoding="utf-8")
    if marker.exists() and marker.read_text(encoding="utf-8") == req:
        return  # 套件清單沒變：不用重裝
    print("建立 Python 虛擬環境、安裝套件（第一次需要幾分鐘）…")
    if not VPY.exists():
        try:
            venv.create(VENV, with_pip=True)
        except Exception as e:  # noqa: BLE001
            sys.exit(f"無法建立虛擬環境：{e}\nUbuntu 請先執行：sudo apt install python3-venv python3-tk")
    subprocess.check_call([str(VPY), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                           "-r", str(APP / "requirements.txt")])
    marker.write_text(req, encoding="utf-8")


def setup_llama() -> None:
    if SERVER.exists():
        return
    print(f"下載 llama.cpp {LLAMA_BUILD}…")
    with urllib.request.urlopen(LLAMA_URL, timeout=60) as r:
        data = r.read()
    BIN.mkdir(parents=True, exist_ok=True)
    if IS_WIN:
        zipfile.ZipFile(io.BytesIO(data)).extractall(BIN)
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for m in tar.getmembers():  # 拿掉最上層的 llama-bXXXX/ 資料夾
                parts = Path(m.name).parts
                if len(parts) < 2 or ".." in parts:
                    continue
                m.name = str(Path(*parts[1:]))
                tar.extract(m, BIN)
        SERVER.chmod(0o755)


def main() -> None:
    os.chdir(APP)
    setup_venv()
    setup_llama()
    for dest, url in MODELS:
        if not dest.exists():
            print(f"下載模型 {dest.relative_to(APP)}…")
            download(url, dest)
    print("安裝完成。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("已中斷，下次執行會接著下載。")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"安裝失敗：{e}\n請確認網路後重新執行。")
