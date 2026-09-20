"""循环冲突检测：在严格胜负边中寻找最短有向环。

仅严格的胜负判断构成有向边（胜者 → 败者）；
平局与未比较的组合均不成边。每条判断记录只产生单向边，
因此正常数据下不存在自环与 2-环，最短环长至少为 3。
"""
from __future__ import annotations

from collections import deque


def _win_graph(candidates: list[str], judgments: list[dict]) -> list[list[int]]:
    """构建「胜者 → 败者」的有向邻接表，邻居按候选录入顺序排列。"""
    n = len(candidates)
    idx = {name: i for i, name in enumerate(candidates)}
    edges: list[set[int]] = [set() for _ in range(n)]
    for j in judgments:
        a, b = idx[j["a"]], idx[j["b"]]
        if j["result"] == "a":
            edges[a].add(b)
        elif j["result"] == "b":
            edges[b].add(a)
        # tie：平局不成边
    return [sorted(e) for e in edges]


def shortest_cycle(candidates: list[str], judgments: list[dict]) -> list[str] | None:
    """返回最短有向环上的候选项名称（按环的走向排列），无环时返回 None。

    例如 ["甲", "乙", "丙"] 表示 甲→乙→丙→甲。
    存在多个同长度环时，把每个环规范化为「以其最小录入序成员开头」
    的下标序列，取字典序最小者，保证选择稳定且只依赖候选录入顺序。
    """
    n = len(candidates)
    adj = _win_graph(candidates, judgments)

    # 全源 BFS 距离，用于 DFS 剪枝（无法回到起点的分支直接跳过）
    dist: list[list[int]] = []
    for src in range(n):
        d = [-1] * n
        d[src] = 0
        q = deque([src])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if d[v] == -1:
                    d[v] = d[u] + 1
                    q.append(v)
        dist.append(d)

    best_len = n + 1
    best: tuple[int, ...] | None = None

    def dfs(s: int, u: int, path: list[int], on_path: set[int]) -> None:
        nonlocal best_len, best
        for v in adj[u]:
            if v == s and len(path) >= 2:
                # 闭环：环长 = 路径节点数；path 已以环上最小下标 s 开头
                if len(path) < best_len or (len(path) == best_len and tuple(path) < best):
                    best_len = len(path)
                    best = tuple(path)
            elif v not in on_path and v > s:
                back = dist[v][s]
                # 经 v 的环长至少为 len(path) + back，无望更优则剪枝
                if back >= 1 and len(path) + back <= best_len:
                    path.append(v)
                    on_path.add(v)
                    dfs(s, v, path, on_path)
                    path.pop()
                    on_path.discard(v)

    # 只枚举「以 s 为最小录入序节点」的环，每个环恰好被枚举一次
    for s in range(n):
        if best_len == 2:  # 理论最短（正常数据最短为 3）
            break
        dfs(s, s, [s], {s})

    if best is None:
        return None
    return [candidates[i] for i in best]


def _edge_set_less(a: int, b: int) -> bool:
    """两个等大小的边号位掩码：按升序编号序列做字典序比较，a < b 时为真。

    即取两者对称差中编号最小的边，它属于 a 则 a 的序列更小。
    """
    xor = a ^ b
    return bool(xor) and bool(a & (xor & -xor))


def minimum_tie_resolution(
    candidates: list[str], judgments: list[dict]
) -> list[tuple[str, str]]:
    """「最少改判方案」：把最少多少条严格胜负判断改为平局，可使胜负图无环。

    把一条严格胜负边改成平局等价于从有向图中删除该边，因此问题即严格胜负
    有向图上的**最小反馈弧集**。平局与未比较组合不成边，不参与计算。

    利用等价刻画：最小反馈弧集的边数 = 所有顶点排列中「逆向边」数的最小值
    （删除排列中的逆向边后即为无环图；任一无环剩余图也都存在使其边全向前的
    拓扑排列）。n ≤ 12，用子集 DP 精确求解，复杂度 O(2^n · n^2)：
    dp[S] = 排列好子集 S 所需删除的最少边数；把 v 放在 S 最后时，v 指向
    S\\{v} 中顶点的边全部为逆向边。

    多个最优方案时的稳定选择：严格边按**判断记录顺序**（judgments 已按记录
    id 升序传入）依次编号，取编号集合字典序最小的方案——即在同样最少的改动
    数下，优先改判更早记录的组合。该比较在 DP 的每个子集状态上局部保留，
    故求得的全局方案仍是字典序最小者。

    返回需要改判的存储组合 (a, b) 列表（按判断记录顺序排列）；
    无环（不存在冲突）时返回空列表。
    """
    n = len(candidates)
    idx = {name: i for i, name in enumerate(candidates)}

    # 严格胜负边：顶点出边掩码 + 边在记录顺序下的优先级位号
    out_vertices = [0] * n
    edge_bit = [[0] * n for _ in range(n)]  # edge_bit[tail][head] = 1 << priority
    pairs: list[tuple[str, str]] = []  # priority -> 存储组合 (a, b)
    priority = 0
    for j in judgments:
        if j["result"] == "tie":
            continue  # 平局不成边，也不占用编号
        x, y = idx[j["a"]], idx[j["b"]]
        tail, head = (x, y) if j["result"] == "a" else (y, x)
        if edge_bit[tail][head]:
            continue  # 正常数据下同组合只有一条记录，防御性跳过
        out_vertices[tail] |= 1 << head
        edge_bit[tail][head] = 1 << priority
        pairs.append((j["a"], j["b"]))
        priority += 1

    total = 1 << n
    dp_cost = [0] * total
    dp_edges = [0] * total
    for s in range(1, total):
        best_cost = n * n + 1
        best_edges = 0
        rest = s
        while rest:
            bit_v = rest & -rest
            v = bit_v.bit_length() - 1
            prev = s ^ bit_v
            backward_v = out_vertices[v] & prev  # v 指向已排列顶点的逆向边
            cand_cost = dp_cost[prev] + backward_v.bit_count()
            cand_edges = dp_edges[prev]
            heads = backward_v
            while heads:
                bit_u = heads & -heads
                u = bit_u.bit_length() - 1
                cand_edges |= edge_bit[v][u]
                heads ^= bit_u
            if cand_cost < best_cost or (
                cand_cost == best_cost and _edge_set_less(cand_edges, best_edges)
            ):
                best_cost, best_edges = cand_cost, cand_edges
            rest ^= bit_v
        dp_cost[s], dp_edges[s] = best_cost, best_edges

    chosen = dp_edges[total - 1]
    return [pair for p, pair in enumerate(pairs) if chosen & (1 << p)]
