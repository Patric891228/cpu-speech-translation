# CPU 中英語音翻譯（離線）

用麥克風說中文就即時翻成英文，說英文就翻成繁體中文（台灣用語）。全部在本機 CPU 執行，不需要 GPU；裝好之後也不需要網路。

- 語音辨識：Whisper small（[faster-whisper](https://github.com/SYSTRAN/faster-whisper)，CPU int8）
- 翻譯：Google TranslateGemma 4B Q4（[llama.cpp](https://github.com/ggml-org/llama.cpp) 官方預編 CPU 版）
- 介面：桌面視窗（tkinter），有「語音翻譯」和「文字翻譯」兩個分頁

## 需求

- Windows 10 以上，或 Linux（Ubuntu 22.04 以上）
- 記憶體 16GB（程式約佔 4GB）、硬碟約 4GB
- Python 3.10 以上（Windows 從 [python.org](https://www.python.org/downloads/) 安裝；Ubuntu 要先執行 `sudo apt install python3-tk python3-venv`）

## 使用

| 系統 | 啟動方式 |
|---|---|
| Windows | 雙擊 `啟動翻譯.bat` |
| Linux | 執行 `./啟動翻譯.sh` |

第一次會自動安裝 Python 套件，並下載 llama.cpp 和模型（約 3GB），需要網路，時間依網速而定。之後就可以離線使用。下載中斷時重新執行即可，會從中斷的地方接著下載。

語音翻譯：
1. 等下方狀態列顯示「就緒」
2. 選麥克風、說話語言（預設「自動判斷」：在中文、英文之間判斷）
3. 按「開始」說話。每句停頓約 0.6 秒後送出辨識，原文以灰色小字顯示、譯文以大字顯示
4. 「存檔」可以把整段內容存成文字檔

## 名詞對照表

機關名稱、人名、專有名詞的指定譯法放在 `名詞對照表.csv`，可以用 Excel 編輯，也可以按視窗上的「名詞對照表」按鈕開啟。存檔後下一句就會套用，不用重開程式。

| 中文 | 英文 | 辨識常錯成（可空白，多個用 / 隔開） |
|---|---|---|
| 疾管署 | Taiwan CDC | 機關處/機關署 |

- 中翻英：句子裡有「疾管署」就譯成 Taiwan CDC；英翻中：有「Taiwan CDC」就譯成中文欄的詞
- 同一個詞出現在好幾列時，用最上面那一列
- 「辨識常錯成」：語音辨識完馬上改正，例如把「機關處」改成「疾管署」
- 英文縮寫（WHO、MOHW）要大小寫完全相同才算，小寫的 who 不會被當成 WHO

## 檔案

| 檔案 | 說明 |
|---|---|
| `translator.py` | 主程式（視窗、翻譯引擎） |
| `speech.py` | 收音、切句、語音辨識、過濾 Whisper 幻覺 |
| `glossary.py`、`名詞對照表.csv` | 名詞對照表 |
| `zhconv.py` | 簡體轉繁體（不誤改本來就對的繁體字） |
| `install.py` | 第一次執行的安裝與下載 |
| `bin/`、`models/`、`.venv/` | 安裝時產生，不放進 git |

## 授權

- 程式：自用
- TranslateGemma：[Gemma 使用條款](https://ai.google.dev/gemma/terms)
- Whisper：MIT；llama.cpp：MIT
