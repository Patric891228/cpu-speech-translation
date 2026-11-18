"""名詞對照表（名詞對照表.csv，可以用 Excel 編輯）

欄位：中文, 英文, 辨識常錯成（可空白，多個用 / 隔開）
- 中翻英：句子裡出現「中文」欄的詞 → 指定譯成「英文」欄
- 英翻中：句子裡出現「英文」欄的詞 → 指定譯成「中文」欄
  同一個詞出現在好幾列時，用最上面那一列
- 辨識常錯成：語音辨識完馬上改正（「機關處」→「疾管署」），顯示、翻譯、存檔都用改正後的字

做法同 P1（2026-10 實測）：TranslateGemma 只靠提示詞時會忽略部分名詞，
所以同時把原文裡的名詞先換成指定譯名，再加上提示詞（6/6 對；只用提示詞 4/6）。
檔案存檔後下一句就生效，不用重開程式。
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

CJK = re.compile(r"[㐀-鿿豈-﫿]")


def _latin(term: str) -> re.Pattern:
    """英文詞要整個字對上。一般單字不分大小寫；縮寫（一個字裡有兩個以上大寫：WHO、COVID-19）
    要大小寫完全一樣，免得把 who、his 當成 WHO、HIS。"""
    flags = 0 if any(sum(c.isupper() for c in w) >= 2 for w in term.split()) else re.I
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", flags)


def _has(text: str, term: str) -> bool:
    return term in text if CJK.search(term) else _latin(term).search(text) is not None


def _sub(text: str, term: str, repl: str) -> str:
    return text.replace(term, repl) if CJK.search(term) else _latin(term).sub(lambda _m: repl, text)


class Glossary:
    def __init__(self, path: Path):
        self.path = path
        self.mtime = None
        self.rows: list[tuple[str, str, list[str]]] = []

    def _reload(self) -> None:
        try:
            m = self.path.stat().st_mtime
        except OSError:
            self.rows, self.mtime = [], None
            return
        if m == self.mtime:
            return
        raw = self.path.read_bytes()
        for enc in ("utf-8-sig", "cp950"):  # Windows Excel 直接存 CSV 會是 Big5
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return
        rows = []
        for r in csv.reader(io.StringIO(text)):
            r = [c.strip() for c in r] + ["", "", ""]
            zh, en, wrong = r[0], r[1], r[2]
            if not zh or not en or zh.startswith("#") or zh == "中文":
                continue
            rows.append((zh, en, [w.strip() for w in re.split(r"[/／]", wrong) if w.strip()]))
        self.rows, self.mtime = rows, m

    def terms(self, text: str, src: str) -> list[tuple[str, str]]:
        """這句話用得到的 (原文詞, 指定譯名)，長的在前。"""
        self._reload()
        seen, out = set(), []
        for zh, en, _ in self.rows:
            s, t = (zh, en) if src == "zh" else (en, zh)
            key = s if CJK.search(s) else s.lower()
            if key in seen:
                continue
            seen.add(key)
            if _has(text, s):
                out.append((s, t))
        return sorted(out, key=lambda p: -len(p[0]))

    def correct(self, text: str) -> str:
        """修正語音辨識常聽錯的詞（中英都可以）。"""
        self._reload()
        pairs = [(w, zh if CJK.search(w) else en) for zh, en, ws in self.rows for w in ws]
        for wrong, right in sorted(pairs, key=lambda p: -len(p[0])):
            text = _sub(text, wrong, right)
        return text

    @staticmethod
    def apply(text: str, terms: list[tuple[str, str]], quote: bool = False) -> str:
        """把原文裡的名詞先換成指定譯名（長的先換：「疾病管制署」在「管制署」之前）。
        quote：英翻中時加上「」：只換字的話模型還是會改寫（"The 疾病管制署 will…"→「衛生署將…」，
        11-18 實測 4 句錯 2），加了引號 4/4 照抄，翻完再用 unquote 拿掉。"""
        for s, t in terms:
            text = _sub(text, s, f"「{t}」" if quote else t)
        return text

    @staticmethod
    def unquote(text: str, terms: list[tuple[str, str]]) -> str:
        for _, t in terms:
            text = text.replace(f"「{t}」", t)
        return text
