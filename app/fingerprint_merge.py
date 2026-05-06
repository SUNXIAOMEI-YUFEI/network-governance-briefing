"""议题指纹归并：LLM 给出的 fingerprint 同议题不同角度会跑偏，这里做归并。

例子：
    LLM 输出：
      - EU-AI-Act-GPAI-CodeOfPractice-2026-04    （DG 立法）
      - EU-AI-Act-GPAI-CodeOfPractice-2026-04    （Lawfare 观点）
      - EU-GPAI-Code-Critique-2026-05            （TPP 观点，跑偏了）

    归并后 3 个 fingerprint 会统一为最规范的那个。

算法（简单但可靠）：
1. 归一化：拆分 → 转小写 → 拆合成词 → 去停用词（年份/国家/太泛化的词）
2. 每个 fingerprint 得到一个"核心 token 集合"
3. 两两计算 Jaccard 相似度，≥ 阈值的归为一组（并查集）
4. 组内选规范 fingerprint：出现频次最高；平手时选 token 集合最丰富的

不调 LLM，纯启发式，0 成本，确定性行为（同样的输入总是同样的输出）。
"""
from __future__ import annotations

import re
from collections import Counter

# 停用词：这些词过于泛化、单独出现不足以判别议题
STOPWORDS = {
    # 年份 / 季度 / 时间戳
    *{str(y) for y in range(2020, 2040)},
    "q1", "q2", "q3", "q4",
    "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12",

    # 地区代码 / 国家代码（保留 eu/us/uk/cn 这些是因为议题确实经常地域相关；
    # 但单独看"us"是不够的——us 下还有 ftc/nist/coppa 等分议题，所以地区词保留）
    # 纯泛化的全球词干掉
    "global", "international", "misc",

    # 泛化技术词
    "ai", "ml", "tech", "digital", "online", "cyber",

    # 泛化制度词
    "act", "law", "rule", "code", "regime", "framework",
    "update", "final", "draft", "new",
    "guidance", "report",

    # 泛化动作词
    "publishes", "releases", "signed", "enacted", "enforces",
    "critique", "analysis",
}

# 合成词 / 词形拆分与标准化：
# - 把 "AgenticAI"、"agenticai" 这类合成词拆开
# - 把"同一概念的不同词形"标准化到一个 canonical token
#   （agentic/agent → agent；gpai/genai/aigc → gpai 等）
TOKEN_NORMALIZE: dict[str, list[str]] = {
    # 合成词拆分 + 标准化
    "agenticai":    ["agent"],
    "agentic":      ["agent"],        # 词形标准化
    "genai":        ["gpai"],         # 概念统一
    "aigc":         ["gpai"],
    # "aiact" 保留（EU AI Act 是具体法律的缩写）
}


def _normalize(fp: str) -> set[str]:
    """把 fingerprint 拆成归一化的 token 集合。"""
    # 1. 按 - / _ / 空格拆
    raw = re.split(r"[-_\s/]+", fp.strip())
    tokens: list[str] = []

    for t in raw:
        t = t.lower().strip()
        if not t:
            continue
        # 2. 拆 camelCase：CodeOfPractice → code of practice
        # 注意：只拆首字母已经大写过的模式；全小写 token 不动
        if any(c.isupper() for c in fp):
            camel_parts = re.findall(r"[A-Z]?[a-z0-9]+", t)
            # re 在全小写 token 上返回整个 token，不影响
            if camel_parts and len("".join(camel_parts)) == len(t):
                tokens.extend(p.lower() for p in camel_parts if p)
            else:
                tokens.append(t)
        else:
            tokens.append(t)

    # 3. 词形标准化（同概念不同写法）
    expanded: list[str] = []
    for t in tokens:
        mapped = TOKEN_NORMALIZE.get(t)
        if mapped is not None:
            expanded.extend(mapped)
        else:
            expanded.append(t)

    # 4. 去停用词 + 去空 + 去短词（< 2 字符）
    core = {t for t in expanded if t and t not in STOPWORDS and len(t) >= 2}
    return core


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = a & b
    union = a | b
    return len(inter) / len(union) if union else 0.0


def merge_fingerprints(
    fps: list[str],
    *,
    threshold: float = 0.5,
    min_shared_tokens: int = 2,
) -> dict[str, str]:
    """对一批 fingerprint 做归并。

    Args:
        fps: 所有文章的 fingerprint 列表（可重复）
        threshold: Jaccard 相似度阈值
        min_shared_tokens: 除相似度外，还要求两个指纹核心 token 交集 >= 此值

    Returns:
        {原 fingerprint → 归并后的规范 fingerprint} 映射。
        没有归并的 fingerprint 也会出现在 map 里（映射到自己）。
    """
    unique_fps = list(set(fps))
    n = len(unique_fps)
    if n <= 1:
        return {fp: fp for fp in unique_fps}

    tokens = {fp: _normalize(fp) for fp in unique_fps}

    # 并查集
    parent = {fp: fp for fp in unique_fps}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    # 两两比较
    for i in range(n):
        for j in range(i + 1, n):
            a, b = unique_fps[i], unique_fps[j]
            ta, tb = tokens[a], tokens[b]
            if len(ta & tb) < min_shared_tokens:
                continue
            if _jaccard(ta, tb) >= threshold:
                union(a, b)

    # 每组选规范 fingerprint
    groups: dict[str, list[str]] = {}
    for fp in unique_fps:
        root = find(fp)
        groups.setdefault(root, []).append(fp)

    freq = Counter(fps)
    canonical_map: dict[str, str] = {}
    for _, members in groups.items():
        if len(members) == 1:
            canonical_map[members[0]] = members[0]
            continue
        # 选规范：优先出现频次高的；平手时选 token 集合最丰富
        def score(fp: str) -> tuple[int, int]:
            return (freq[fp], len(tokens[fp]))
        winner = max(members, key=score)
        for m in members:
            canonical_map[m] = winner

    return canonical_map


# ============================================================
# CLI 小工具：肉眼验证归并效果
# ============================================================

def _demo() -> None:
    samples = [
        "EU-AI-Act-GPAI-CodeOfPractice-2026-04",
        "EU-AI-Act-GPAI-CodeOfPractice-2026-04",
        "EU-GPAI-Code-Critique-2026-05",
        "Global-AI-Agent-Liability-2026-04",
        "US-AgenticAI-Liability-2026-05",
        "US-FTC-COPPA-2026-Update",
        "UK-ICO-AgeAssurance-2026-05",
        "IT-Garante-OpenAI-GDPR-Fine-2026-05",
    ]
    print("[demo] normalize:")
    for s in samples:
        print(f"  {s:50s} → {sorted(_normalize(s))}")

    print("\n[demo] merge map:")
    m = merge_fingerprints(samples)
    for orig, canon in m.items():
        marker = "  →→" if orig != canon else "    "
        print(f"  {marker} {orig:50s} → {canon}")


if __name__ == "__main__":
    _demo()
