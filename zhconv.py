"""簡體轉繁體（台灣），但不誤改本來就對的繁體字。

OpenCC 的 s2tw 是給「整篇簡體」用的：拿去轉繁體句子會把「台灣、公里、游泳、范先生、了解、干擾」
改成「臺灣、公裡、遊泳、範先生、瞭解、幹擾」。Whisper 輸出的中文多半已經是繁體、偶爾夾一兩個簡體字，
所以：
- 沒有簡體專用字：不動
- 繁體為主、夾幾個簡體字：只換那幾個字
- 簡體為主：整句轉（含「以后→以後」這類詞的轉換）
沒裝 opencc 時原樣傳回。
"""
from __future__ import annotations

# 繁體裡也是正確用字、OpenCC 卻會換掉的字
KEEP = set("台里游范占于余干后准云斗丑征朴冲咸夸采划凶吁伙了周秘只面系制表卷才松板")

try:
    from opencc import OpenCC
    _s2tw, _t2s = OpenCC("s2tw"), OpenCC("t2s")
except Exception:  # noqa: BLE001
    _s2tw = _t2s = None


def to_traditional(text: str) -> str:
    if not _s2tw or not text:
        return text
    conv = _s2tw.convert(text)
    if conv == text:
        return text
    simp = [i for i, (a, b) in enumerate(zip(text, conv)) if a != b and a not in KEEP]
    if not simp:
        return text  # 只有兩岸通用的字被換：本來就是繁體
    trad = sum(1 for ch in text if _t2s.convert(ch) != ch)  # 繁體專用字
    if len(conv) != len(text) or trad <= len(simp):
        return conv  # 簡體為主：整句轉
    out = list(text)
    for i in simp:
        out[i] = conv[i]
    return "".join(out)
