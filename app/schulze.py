"""Schulze 最强路径法（Strongest Path / Beatpath）排名计算。

计票约定：
- 甲胜乙：d(甲, 乙) = 1.0，d(乙, 甲) = 0
- 平局：  d(甲, 乙) = d(乙, 甲) = 0.5
- 未比较：双向均为 0

路径强度 = 路径上最弱一环的强度；最强路径取所有路径中的最大值。
候选项 i 严格优于 j 当且仅当 p[i][j] > p[j][i]，相等则并列。
"""
from __future__ import annotations


def strongest_paths(candidates: list[str], judgments: list[dict]) -> list[list[float]]:
    """根据已记录的胜负关系计算最强路径强度矩阵 p。"""
    n = len(candidates)
    idx = {name: i for i, name in enumerate(candidates)}
    p = [[0.0] * n for _ in range(n)]
    for j in judgments:
        a, b, result = idx[j["a"]], idx[j["b"]], j["result"]
        if result == "a":
            p[a][b] = 1.0
        elif result == "b":
            p[b][a] = 1.0
        else:  # tie
            p[a][b] = p[b][a] = 0.5

    # Floyd–Warshall 变体：p[i][j] = max(p[i][j], min(p[i][k], p[k][j]))
    for k in range(n):
        for i in range(n):
            if i == k:
                continue
            pik = p[i][k]
            if pik == 0:
                continue
            row_i, row_k = p[i], p[k]
            for j in range(n):
                weakest = pik if pik < row_k[j] else row_k[j]
                if row_i[j] < weakest:
                    row_i[j] = weakest
    return p


def rank_groups(candidates: list[str], p: list[list[float]]) -> list[list[str]]:
    """按 Schulze 关系把候选项分成有序的并列组（名次最优的组在前）。"""
    n = len(candidates)
    wins = [sum(1 for j in range(n) if j != i and p[i][j] > p[j][i]) for i in range(n)]
    order = sorted(range(n), key=lambda i: -wins[i])
    groups: list[list[int]] = []
    for i in order:
        # Schulze 关系是全预序：与组内任一成员并列即与全组并列
        if groups and all(p[i][m] == p[m][i] for m in groups[-1]):
            groups[-1].append(i)
        else:
            groups.append([i])
    return [[candidates[i] for i in group] for group in groups]
