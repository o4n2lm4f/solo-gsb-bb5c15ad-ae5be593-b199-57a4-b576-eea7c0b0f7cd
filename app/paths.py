"""排名依据：在现有 Schulze 计算中重建**可验证**的最强路径。

与 ``schulze.strongest_paths`` 的 Floyd–Warshall 结果完全一致：

- 边强度：直接胜负 = 1.0（胜者 → 败者），直接平局 = 0.5（双向），
  未比较不成边；
- 路径强度 = 路径上最弱一环（瓶颈边强度）；最强路径取强度最大者；
- **同强度路径优先边数更少者**；再相同则按路径节点的候选录入序序列
  取字典序最小者（仅依赖候选录入顺序，稳定可复现）；
- 路径不重复经过任何节点（简单路径）。

重建分两步：先用与排名相同的 Floyd–Warshall 变体求出最强瓶颈强度，
再在「边强度不低于该瓶颈」的子图上按层 BFS 选出边数最少、字典序最小
的简单路径，结果与 Floyd–Warshall 强度矩阵交叉核对。
"""
from __future__ import annotations

from collections import deque
from typing import Any

STRENGTH_WIN = 1.0
STRENGTH_TIE = 0.5


def _edge_matrix(
    candidates: list[str], judgments: list[dict]
) -> tuple[list[list[float]], dict[str, int]]:
    """构建直接比较边权矩阵（与 schulze.strongest_paths 的初始化一致）。"""
    n = len(candidates)
    idx = {name: i for i, name in enumerate(candidates)}
    g = [[0.0] * n for _ in range(n)]
    for j in judgments:
        a, b, result = idx[j["a"]], idx[j["b"]], j["result"]
        if result == "a":
            g[a][b] = STRENGTH_WIN
        elif result == "b":
            g[b][a] = STRENGTH_WIN
        else:  # tie：平局为双向 0.5 的边
            g[a][b] = g[b][a] = STRENGTH_TIE
    return g, idx


def strongest_path(
    candidates: list[str], judgments: list[dict], src: str, dst: str
) -> dict[str, Any]:
    """重建 src → dst 的最强简单路径。

    分两步，保证决胜规则可直接验证：

    1. 用与 ``schulze.strongest_paths`` 相同的 Floyd–Warshall 变体求出
       最强瓶颈强度 S；S = 0 表示无可达路径；
    2. 在「边强度 ≥ S」的子图中做 BFS，取边数最少者；同一 BFS 层内
       存在多个前驱时，按完整节点下标序列的字典序取最小者。由于前驱
       都在上一层、其序列已定稿，逐层比较即可得到**同强度下边数最少、
       再按候选录入序字典序最小**的路径。BFS 每个节点只定值一次，
       路径天然不重复节点。

    返回：
      - ``exists``：是否存在可达路径；
      - ``strength``：路径瓶颈强度（无路径时为 0.0）；
      - ``hops``：边数（无路径时为 None）；
      - ``nodes``：路径节点名称（含首尾，无路径时为空）；
      - ``edges``：按路径走向排列的边 ``{a, b, kind, strength}``，
        ``kind`` 为 ``"win"``（胜负边）或 ``"tie"``（平局边）。
    """
    g, idx = _edge_matrix(candidates, judgments)
    n = len(candidates)
    s, t = idx[src], idx[dst]

    # 第一步：Floyd–Warshall 求最强瓶颈强度 S（与排名计算完全一致）
    p = [row[:] for row in g]
    for k in range(n):
        for i in range(n):
            pik = p[i][k]
            if pik == 0.0 or i == k:
                continue
            for j in range(n):
                weakest = pik if pik < p[k][j] else p[k][j]
                if p[i][j] < weakest:
                    p[i][j] = weakest
    strength = p[s][t]
    if strength == 0.0:
        return {"exists": False, "strength": 0.0, "hops": None, "nodes": [], "edges": []}

    # 第二步：边强度 ≥ S 的子图上 BFS，逐层按 (距离, 节点序列字典序) 定值
    dist = [-1] * n
    label: list[tuple[int, ...] | None] = [None] * n
    dist[s], label[s] = 0, (s,)
    q = deque([s])
    while q:
        u = q.popleft()
        for v in range(n):  # 邻居按录入序枚举，同层字典序决胜见下方比较
            if g[u][v] < strength or v == s:
                continue
            cand = label[u] + (v,)  # type: ignore[operator]
            if dist[v] == -1:
                dist[v] = dist[u] + 1
                label[v] = cand
                q.append(v)
            elif dist[v] == dist[u] + 1 and cand < label[v]:  # type: ignore[operator]
                label[v] = cand  # 同层更优前驱：完整序列字典序更小
        if u == t:
            break

    tup = label[t]
    edges = []
    for x, y in zip(tup, tup[1:]):
        w = g[x][y]
        edges.append(
            {
                "a": candidates[x],
                "b": candidates[y],
                "kind": "win" if w == STRENGTH_WIN else "tie",
                "strength": w,
            }
        )
    return {
        "exists": True,
        "strength": strength,
        "hops": len(tup) - 1,
        "nodes": [candidates[i] for i in tup],
        "edges": edges,
    }


def pair_explanation(
    candidates: list[str], judgments: list[dict], a: str, b: str
) -> dict[str, Any]:
    """两个候选项的双向最强路径与最终相对判定。

    ``verdict`` 相对请求顺序解释：``"a"`` = a 领先、``"b"`` = b 领先、
    ``"tie"`` = 并列（含双向均无路径的情形）。

    重建结果与 ``schulze.strongest_paths`` 的 Floyd–Warshall 强度矩阵
    逐向交叉核对，不一致即视为内部错误——保证面板展示的路径可验证。
    """
    from .schulze import strongest_paths

    order = {name: i for i, name in enumerate(candidates)}
    p = strongest_paths(candidates, judgments)

    path_ab = strongest_path(candidates, judgments, a, b)
    path_ba = strongest_path(candidates, judgments, b, a)

    s_ab, s_ba = path_ab["strength"], path_ba["strength"]
    if p[order[a]][order[b]] != s_ab or p[order[b]][order[a]] != s_ba:
        raise RuntimeError("最强路径重建结果与 Schulze 矩阵不一致")  # 防御性校验

    if s_ab > s_ba:
        verdict = "a"
    elif s_ba > s_ab:
        verdict = "b"
    else:
        verdict = "tie"

    return {
        "a": a,
        "b": b,
        "verdict": verdict,
        "strength_ab": s_ab,
        "strength_ba": s_ba,
        "path_ab": path_ab,
        "path_ba": path_ba,
    }
