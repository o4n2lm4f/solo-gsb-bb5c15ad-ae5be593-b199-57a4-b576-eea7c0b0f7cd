"""两两决策台 —— FastAPI 后端。

逐对比较候选项，按 Schulze 最强路径法实时排名。
进度持久化在 SQLite 中，刷新页面不丢失。
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from itertools import combinations
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .conflict import minimum_tie_resolution, shortest_cycle
from .paths import pair_explanation
from .priority import recommend_next_pair
from .schulze import rank_groups, strongest_paths

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DB_PATH = os.environ.get("DECISION_DB", str(BASE_DIR / "data" / "decision.db"))

MIN_CANDIDATES = 3
MAX_CANDIDATES = 12
MAX_NAME_LEN = 80

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    candidates TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS judgments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    a TEXT NOT NULL,
    b TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('a', 'b', 'tie'))
);
CREATE INDEX IF NOT EXISTS idx_judgments_session ON judgments(session_id);
"""


def get_conn() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


init_db()  # 模块加载即建库建表（幂等），lifespan 中再执行一次以确保任意部署方式下可用


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="两两决策台", lifespan=lifespan)


# ---------- 请求模型 ----------

class SessionCreate(BaseModel):
    candidates: list[str] = Field(min_length=MIN_CANDIDATES, max_length=MAX_CANDIDATES)


class JudgmentCreate(BaseModel):
    a: str
    b: str
    result: Literal["a", "b", "tie"]


class SuggestionApply(BaseModel):
    # 可选：客户端回传待改判组合用于一致性校验；缺省时服务器直接采用当前建议
    pairs: list[dict[str, str]] | None = None


# ---------- 状态组装 ----------

def load_state(conn: sqlite3.Connection, session_id: str) -> dict:
    row = conn.execute("SELECT candidates FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="会话不存在或已被删除")
    candidates: list[str] = json.loads(row["candidates"])

    history: list[dict] = []
    judged: dict[tuple[str, str], str] = {}
    for r in conn.execute(
        "SELECT a, b, result FROM judgments WHERE session_id = ? ORDER BY id", (session_id,)
    ):
        judged[(r["a"], r["b"])] = r["result"]
        history.append({"a": r["a"], "b": r["b"], "result": r["result"]})

    pairs, pending = [], []
    for a, b in combinations(candidates, 2):
        result = judged.get((a, b))
        pairs.append({"a": a, "b": b, "result": result})
        if result is None:
            pending.append({"a": a, "b": b})

    p = strongest_paths(candidates, history)
    groups = rank_groups(candidates, p)

    # 胜负矩阵：行 vs 列
    n = len(candidates)
    matrix: list[list[str]] = []
    for i in range(n):
        cells = []
        for j in range(n):
            if i == j:
                cells.append("self")
                continue
            a, b = (candidates[i], candidates[j]) if i < j else (candidates[j], candidates[i])
            result = judged.get((a, b))
            if result is None:
                cells.append("pending")
            elif result == "tie":
                cells.append("tie")
            elif (result == "a") == (i < j):
                cells.append("win")
            else:
                cells.append("lose")
        matrix.append(cells)

    # 循环冲突：严格胜负边中的最短有向环（平局与未比较不成边）
    conflict = None
    cycle = shortest_cycle(candidates, history)
    if cycle is not None:
        order = {name: i for i, name in enumerate(candidates)}
        edges = []
        for i, winner in enumerate(cycle):
            loser = cycle[(i + 1) % len(cycle)]
            a, b = (winner, loser) if order[winner] < order[loser] else (loser, winner)
            edges.append({"a": a, "b": b, "result": "a" if winner == a else "b"})
        conflict = {"cycle": cycle, "edges": edges}

    # 最少改判方案：把最少数量的严格胜负组合改为平局即可消除全部有向环。
    # 无冲突时不生成建议；平局与未比较组合不参与计算。
    suggestion = None
    if conflict is not None:
        change_pairs = minimum_tie_resolution(candidates, history)
        change_set = {(a, b) for a, b in change_pairs}
        affected = []
        preview_history = []
        for h in history:
            a, b, result = h["a"], h["b"], h["result"]
            if (a, b) in change_set:
                affected.append(
                    {
                        "a": a,
                        "b": b,
                        "from": result,  # 严格胜负（"a"/"b"，相对存储顺序的 a）
                        "to": "tie",
                    }
                )
                preview_history.append({"a": a, "b": b, "result": "tie"})
            else:
                preview_history.append({"a": a, "b": b, "result": result})
        preview_groups = rank_groups(
            candidates, strongest_paths(candidates, preview_history)
        )
        suggestion = {
            # 组合按判断记录顺序排列，stable 选择结果对前端确定可见
            "pairs": [{"a": a, "b": b} for a, b in change_pairs],
            "change_count": len(change_pairs),
            "affected": affected,
            "preview_ranking": preview_groups,
        }

    return {
        "session_id": session_id,
        "candidates": candidates,
        "progress": {"done": len(history), "total": len(pairs)},
        "current_pair": pending[0] if pending else None,
        "pending": pending,
        "ranking": groups,
        "matrix": matrix,
        "conflict": conflict,
        "suggestion": suggestion,
        # 下一对优先级推荐：纯内存模拟，不写库、不影响进度
        "next_pair": recommend_next_pair(candidates, history, pending),
        "can_undo": bool(history),
    }


# ---------- API ----------

@app.post("/api/sessions", status_code=201)
def create_session(payload: SessionCreate):
    names, seen = [], set()
    for raw in payload.candidates:
        name = " ".join(raw.split())
        if not name:
            continue
        if len(name) > MAX_NAME_LEN:
            raise HTTPException(422, f"候选项名称过长（>{MAX_NAME_LEN} 字符）：{name[:20]}…")
        if name in seen:
            raise HTTPException(422, f"候选项重复：{name}")
        seen.add(name)
        names.append(name)
    if not (MIN_CANDIDATES <= len(names) <= MAX_CANDIDATES):
        raise HTTPException(422, f"有效候选项数量须为 {MIN_CANDIDATES}～{MAX_CANDIDATES} 个")

    sid = uuid.uuid4().hex[:12]
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO sessions (id, candidates) VALUES (?, ?)",
            (sid, json.dumps(names, ensure_ascii=False)),
        )
        conn.commit()
        return load_state(conn, sid)
    finally:
        conn.close()


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    conn = get_conn()
    try:
        return load_state(conn, sid)
    finally:
        conn.close()


@app.get("/api/sessions/{sid}/ranking-basis")
def ranking_basis(sid: str, a: str, b: str):
    """「排名依据」：两个候选项之间的双向最强路径与最终相对判定。

    路径在与排名相同的 Schulze 计算中重建（并在内部与 Floyd–Warshall
    强度矩阵交叉核对）：同强度路径优先边数更少，再按候选录入顺序稳定
    选择，路径不重复节点；任一方向不可达时对应路径明确标记为无有效路径。
    纯只读接口，不写库、不影响任何现有接口；面板随各写操作返回的状态刷新。
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT candidates FROM sessions WHERE id = ?", (sid,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="会话不存在或已被删除")
        candidates: list[str] = json.loads(row["candidates"])
        if a == b or a not in candidates or b not in candidates:
            raise HTTPException(422, "无效的候选项组合")
        history = [
            {"a": r["a"], "b": r["b"], "result": r["result"]}
            for r in conn.execute(
                "SELECT a, b, result FROM judgments WHERE session_id = ? ORDER BY id",
                (sid,),
            )
        ]
        return pair_explanation(candidates, history, a, b)
    finally:
        conn.close()


@app.post("/api/sessions/{sid}/judgments", status_code=201)
def judge(sid: str, payload: JudgmentCreate):
    """记录一对组合的判断。

    默认按「当前待比较组合」依次评审；为支持「下一对优先级」推荐的一键跳转，
    也接受任意尚未比较的组合。与复核接口一致：组合统一按候选录入顺序存储，
    result 始终相对请求中先出现的 a 解释。已判定或无效组合分别返回 409/422。
    """
    conn = get_conn()
    try:
        state = load_state(conn, sid)
        order = {name: i for i, name in enumerate(state["candidates"])}
        if payload.a == payload.b or payload.a not in order or payload.b not in order:
            raise HTTPException(422, "无效的比较组合")
        # 统一按候选录入顺序存储；result 始终相对请求中先出现的 a 解释
        a, b = sorted((payload.a, payload.b), key=order.__getitem__)
        if not any(p["a"] == a and p["b"] == b for p in state["pending"]):
            if state["current_pair"] is None:
                raise HTTPException(409, "本轮所有组合均已比较完毕")
            raise HTTPException(409, "该组合已判定，请刷新后重试")
        if payload.result == "tie":
            result = "tie"
        else:
            winner = payload.a if payload.result == "a" else payload.b
            result = "a" if winner == a else "b"
        conn.execute(
            "INSERT INTO judgments (session_id, a, b, result) VALUES (?, ?, ?, ?)",
            (sid, a, b, result),
        )
        conn.commit()
        return load_state(conn, sid)
    finally:
        conn.close()


@app.put("/api/sessions/{sid}/judgments")
def revise(sid: str, payload: JudgmentCreate):
    """循环冲突复核：原位替换一条已判定组合的记录。

    UPDATE 不改变行 id 与记录条数，因此进度总数、已完成数与
    撤销顺序均保持不变；返回重算后的完整状态（排名、待比较、下一冲突）。
    """
    conn = get_conn()
    try:
        state = load_state(conn, sid)
        order = {name: i for i, name in enumerate(state["candidates"])}
        if payload.a == payload.b or payload.a not in order or payload.b not in order:
            raise HTTPException(422, "无效的比较组合")
        # 统一按候选录入顺序存储；result 始终相对请求中先出现的 a 解释
        a, b = sorted((payload.a, payload.b), key=order.__getitem__)
        if payload.result == "tie":
            result = "tie"
        else:
            winner = payload.a if payload.result == "a" else payload.b
            result = "a" if winner == a else "b"
        cur = conn.execute(
            "UPDATE judgments SET result = ? WHERE session_id = ? AND a = ? AND b = ?",
            (result, sid, a, b),
        )
        if cur.rowcount == 0:
            raise HTTPException(409, "该组合尚未判定，请通过正常流程完成首次比较")
        conn.commit()
        return load_state(conn, sid)
    finally:
        conn.close()


@app.post("/api/sessions/{sid}/resolve-conflicts")
def resolve_conflicts(sid: str, payload: SuggestionApply | None = None):
    """一次确认后批量应用「最少改判方案」：把建议组合全部改为平局。

    全部 UPDATE 与后续状态读取在**单个 SQLite 事务**中完成，任一更新未命中或
    发生异常即整体回滚。UPDATE 不新增/删除判断记录，因此判断记录 id、
    进度统计（已完成数/总数）与撤销顺序均保持不变。提交后重算排名与冲突，
    返回完整状态（成功时 conflict/suggestion 均应为空）。
    """
    conn = get_conn()
    try:
        with conn:  # 单事务：无异常提交，异常自动回滚
            state = load_state(conn, sid)
            suggestion = state["suggestion"]
            if suggestion is None:
                raise HTTPException(409, "当前不存在循环冲突，无需应用改判方案")

            expected = [(p["a"], p["b"]) for p in suggestion["pairs"]]
            if payload is not None and payload.pairs is not None:
                submitted = []
                for p in payload.pairs:
                    if set(p.keys()) != {"a", "b"}:
                        raise HTTPException(422, "组合字段须为 a、b")
                    submitted.append((p["a"], p["b"]))
                if submitted != expected:
                    raise HTTPException(
                        409, "建议方案已过期或与当前冲突不符，请刷新后重试"
                    )

            for a, b in expected:
                # 仅把严格胜负记录改为平局；平局行 rowcount 仍为 1 但语义上不该出现
                cur = conn.execute(
                    "UPDATE judgments SET result = 'tie' "
                    "WHERE session_id = ? AND a = ? AND b = ? AND result != 'tie'",
                    (sid, a, b),
                )
                if cur.rowcount != 1:
                    raise HTTPException(
                        409, f"组合「{a} vs {b}」当前不是严格胜负判定，方案已失效"
                    )
            return load_state(conn, sid)
    finally:
        conn.close()


@app.post("/api/sessions/{sid}/undo")
def undo(sid: str):
    conn = get_conn()
    try:
        if conn.execute("SELECT 1 FROM sessions WHERE id = ?", (sid,)).fetchone() is None:
            raise HTTPException(404, "会话不存在或已被删除")
        cur = conn.execute(
            "DELETE FROM judgments WHERE id = "
            "(SELECT MAX(id) FROM judgments WHERE session_id = ?)",
            (sid,),
        )
        if cur.rowcount == 0:
            raise HTTPException(409, "没有可撤销的判断")
        conn.commit()
        return load_state(conn, sid)
    finally:
        conn.close()


@app.post("/api/sessions/{sid}/reset")
def reset(sid: str):
    conn = get_conn()
    try:
        if conn.execute("SELECT 1 FROM sessions WHERE id = ?", (sid,)).fetchone() is None:
            raise HTTPException(404, "会话不存在或已被删除")
        conn.execute("DELETE FROM judgments WHERE session_id = ?", (sid,))
        conn.commit()
        return load_state(conn, sid)
    finally:
        conn.close()


# ---------- 静态页面 ----------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
