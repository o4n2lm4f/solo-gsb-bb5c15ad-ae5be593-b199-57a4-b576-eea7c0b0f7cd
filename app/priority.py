"""下一对优先级推荐：模拟每个待比较组合的三种结果并评估排名影响。

对每个尚未比较的组合，分别假设「左胜 / 右胜 / 平局」重算 Schulze 排名；
再统计所有候选对 {X, Y} 的相对次序状态（X 前 / X 后 / 并列）在三种
模拟结果之间发生变化的对数，作为该组合的**影响分**。影响分越高，
这一对的判断结果对最终排名的决定性越强。推荐影响分最高的组合，
同分时按原有待比较顺序选取。

全部计算在内存中完成：不写入数据库，也不影响进度统计。
"""
from __future__ import annotations

from itertools import combinations

from .schulze import rank_groups, strongest_paths

OUTCOMES = ("a", "b", "tie")


def _order_states(candidates: list[str], groups: list[list[str]]) -> dict[tuple[str, str], int]:
    """每个候选对在排名中的相对次序状态：-1 = 靠前，0 = 并列，1 = 靠后。"""
    rank: dict[str, int] = {}
    for r, group in enumerate(groups):
        for name in group:
            rank[name] = r
    states: dict[tuple[str, str], int] = {}
    for a, b in combinations(candidates, 2):
        ra, rb = rank[a], rank[b]
        states[(a, b)] = (ra > rb) - (ra < rb)
    return states


def simulate_outcomes(
    candidates: list[str], history: list[dict], a: str, b: str
) -> dict[str, list[list[str]]]:
    """对组合 (a, b) 分别模拟三种判断结果，返回各结果下的排名分组预览。"""
    previews: dict[str, list[list[str]]] = {}
    for result in OUTCOMES:
        simulated = history + [{"a": a, "b": b, "result": result}]
        previews[result] = rank_groups(candidates, strongest_paths(candidates, simulated))
    return previews


def impact_score(candidates: list[str], previews: dict[str, list[list[str]]]) -> int:
    """三种模拟排名之间，相对次序状态（前 / 后 / 并列）发生变化的候选对数量。"""
    state_sets = [_order_states(candidates, previews[result]) for result in OUTCOMES]
    impact = 0
    for key in state_sets[0]:
        if len({states[key] for states in state_sets}) > 1:
            impact += 1
    return impact


def recommend_next_pair(
    candidates: list[str], history: list[dict], pending: list[dict]
) -> dict | None:
    """推荐下一对优先评审的组合；无待比较组合时返回 None。

    返回 {"a", "b", "impact", "candidate_pairs", "previews"}：
    impact 为影响分，candidate_pairs 为候选对总数（影响分上限），
    previews 为三种结果各自的排名预览。同分按 pending 顺序取先者。
    """
    if not pending:
        return None
    best: dict | None = None
    for pair in pending:
        a, b = pair["a"], pair["b"]
        previews = simulate_outcomes(candidates, history, a, b)
        impact = impact_score(candidates, previews)
        if best is None or impact > best["impact"]:  # 严格大于：同分保留先出现者
            best = {"a": a, "b": b, "impact": impact, "previews": previews}
    best["candidate_pairs"] = len(candidates) * (len(candidates) - 1) // 2
    return best
