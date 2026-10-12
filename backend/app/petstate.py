"""宠物喂养系统：饥饿值衰减、食物库存、每日打卡、番茄钟记录。

- 饥饿值 0-100，按时间线性衰减（每小时 -DECAY_PER_HOUR）；
- 每日打卡领取食物（跨天重置）；
- 喂食消耗 1 份食物恢复饥饿；
- 番茄钟完成记录与周报「专注时段」指标联动。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .db.connection import Database
from .logging_setup import get_logger

log = get_logger("pet")

HUNGER_MAX = 100
DECAY_PER_HOUR = 4
PUNCH_REWARD = 3
FEED_RECOVERY = 20
HUNGER_LOW = 30


class PetError(RuntimeError):
    """喂养业务错误（已打卡 / 无食物）。"""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


class PetService:
    """宠物喂养状态服务（单例行 id=1）。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    async def _ensure_row(self) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO pet_state (id, hunger, food, updated_at, last_punch_date) "
            "VALUES (1, ?, 0, ?, '')",
            (HUNGER_MAX, _now_iso()),
        )
        await self._db.commit()

    async def _load(self) -> tuple[int, int, str, str]:
        await self._ensure_row()
        row = await self._db.fetchone(
            "SELECT hunger, food, updated_at, last_punch_date FROM pet_state WHERE id = 1"
        )
        return int(row[0]), int(row[1]), str(row[2]), str(row[3])

    async def _persist(self, hunger: int, food: int, *, last_punch: str | None = None) -> None:
        await self._db.execute(
            "UPDATE pet_state SET hunger = ?, food = ?, updated_at = ?, last_punch_date = ? WHERE id = 1",
            (hunger, food, _now_iso(), last_punch if last_punch is not None else _now_iso().split("T")[0]),
        )
        await self._db.commit()

    @staticmethod
    def _apply_decay(hunger: int, updated_at_iso: str) -> tuple[int, int]:
        """返回 (衰减后饥饿值, 已衰减的整小时数)。"""
        try:
            updated = datetime.fromisoformat(updated_at_iso)
            hours = max(0, (datetime.now().astimezone() - updated).total_seconds() / 3600)
        except (ValueError, TypeError):
            hours = 0.0
        decay_hours = int(hours)
        return max(0, hunger - decay_hours * DECAY_PER_HOUR), decay_hours

    # ------------------------------------------------------------------
    async def get_state(self) -> dict[str, Any]:
        """实时状态（饥饿值已按时间衰减）。"""
        hunger, food, updated_at, last_punch = await self._load()
        current, decay_hours = self._apply_decay(hunger, updated_at)
        if decay_hours > 0:
            await self._persist(current, food)
        row = await self._db.fetchone(
            "SELECT count(*) FROM pomodoro_records WHERE substr(ts, 1, 10) = ?", (_today(),)
        )
        today_pomodoro = int(row[0]) if row else 0
        mood = "hungry" if current <= HUNGER_LOW else "happy" if current >= HUNGER_MAX * 0.6 else "normal"
        return {
            "hunger": current,
            "hunger_max": HUNGER_MAX,
            "food": food,
            "punched_today": last_punch == _today(),
            "today_pomodoro": today_pomodoro,
            "mood": mood,
        }

    async def punch_in(self) -> dict[str, Any]:
        """每日打卡：领取食物。已打卡抛 PetError。"""
        state = await self.get_state()
        if state["punched_today"]:
            raise PetError("今天已经打过卡啦，明天再来吧～")
        new_food = state["food"] + PUNCH_REWARD
        await self._persist(state["hunger"], new_food, last_punch=_today())
        log.info("每日打卡", detail={"reward": PUNCH_REWARD, "food": new_food})
        return {"punched_today": True, "food": new_food, "reward": PUNCH_REWARD}

    async def feed(self) -> dict[str, Any]:
        """喂食：消耗 1 份食物恢复饥饿。无食物抛 PetError。"""
        state = await self.get_state()
        if state["food"] <= 0:
            raise PetError("没有食物了，先去每日打卡领取吧～")
        new_food = state["food"] - 1
        new_hunger = min(HUNGER_MAX, state["hunger"] + FEED_RECOVERY)
        await self._persist(new_hunger, new_food)
        log.info("喂养宠物", detail={"hunger": new_hunger, "food": new_food, "recovery": FEED_RECOVERY})
        return {"hunger": new_hunger, "food": new_food, "recovery": FEED_RECOVERY}

    async def record_pomodoro(self, minutes: int) -> dict[str, Any]:
        """兼容旧接口：记录一个完成的番茄钟，委托 PomodoroService 默认 focus/completed。"""
        return await PomodoroService(self._db).record(minutes)


# ===========================================================================
# 番茄钟（Pomodoro）：完整状态机支持 + 可配置 + 历史统计
# ===========================================================================

POMODORO_DEFAULTS: dict[str, int] = {
    "focus_minutes": 25,
    "short_break_minutes": 5,
    "long_break_minutes": 15,
    "laps_per_cycle": 4,
}
_PHASES = ("focus", "short_break", "long_break")
_STATUSES = ("completed", "interrupted")


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(int(value), hi))


class PomodoroError(RuntimeError):
    """番茄钟业务错误（非法配置等）。"""


class PomodoroService:
    """番茄钟配置 / 记录 / 统计（数据全部落 SQLCipher 加密库）。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    async def _ensure_settings(self) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO pomodoro_settings "
            "(id, focus_minutes, short_break_minutes, long_break_minutes, laps_per_cycle, updated_at) "
            "VALUES (1, 25, 5, 15, 4, ?)",
            (_now_iso(),),
        )
        await self._db.commit()

    async def get_config(self) -> dict[str, Any]:
        """读取番茄钟配置（时长/每轮番茄数），无记录则返回默认值。"""
        await self._ensure_settings()
        row = await self._db.fetchone(
            "SELECT focus_minutes, short_break_minutes, long_break_minutes, laps_per_cycle "
            "FROM pomodoro_settings WHERE id = 1"
        )
        return {
            "focus_minutes": int(row[0]),
            "short_break_minutes": int(row[1]),
            "long_break_minutes": int(row[2]),
            "laps_per_cycle": int(row[3]),
        }

    async def save_config(self, cfg: dict[str, Any]) -> dict[str, Any]:
        """保存番茄钟配置（值范围收紧：分钟 1-120，每轮番茄 1-12）。"""
        await self._ensure_settings()
        focus = _clamp(cfg.get("focus_minutes", 25), 1, 120)
        short_break = _clamp(cfg.get("short_break_minutes", 5), 1, 120)
        long_break = _clamp(cfg.get("long_break_minutes", 15), 1, 120)
        laps = _clamp(cfg.get("laps_per_cycle", 4), 1, 12)
        await self._db.execute(
            "UPDATE pomodoro_settings SET focus_minutes = ?, short_break_minutes = ?, "
            "long_break_minutes = ?, laps_per_cycle = ?, updated_at = ? WHERE id = 1",
            (focus, short_break, long_break, laps, _now_iso()),
        )
        await self._db.commit()
        result = {
            "focus_minutes": focus,
            "short_break_minutes": short_break,
            "long_break_minutes": long_break,
            "laps_per_cycle": laps,
        }
        log.info("番茄钟配置更新", detail=result)
        return result

    # ------------------------------------------------------------------
    # 会话（跨重启恢复进行中的番茄）
    # ------------------------------------------------------------------
    async def _ensure_session(self) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO pomodoro_session "
            "(id, phase, remaining_seconds, running, completed_in_cycle, updated_at) "
            "VALUES (1, 'focus', 1500, 0, 0, ?)",
            (_now_iso(),),
        )
        await self._db.commit()

    async def get_session(self) -> dict[str, Any]:
        """读取进行中的番茄状态（无记录返回默认 focus 未运行）。"""
        await self._ensure_session()
        row = await self._db.fetchone(
            "SELECT phase, remaining_seconds, running, completed_in_cycle, updated_at "
            "FROM pomodoro_session WHERE id = 1"
        )
        return {
            "phase": str(row[0]),
            "remaining_seconds": int(row[1]),
            "running": bool(row[2]),
            "completed_in_cycle": int(row[3]),
            "updated_at": str(row[4]),
        }

    async def save_session(self, phase: str, remaining_seconds: int,
                           running: bool, completed_in_cycle: int) -> dict[str, Any]:
        """保存番茄进行中状态（宠物关闭/重启后可恢复）。"""
        if phase not in _PHASES:
            raise PomodoroError(f"未知阶段: {phase}")
        remaining_seconds = max(0, int(remaining_seconds))
        completed_in_cycle = max(0, int(completed_in_cycle))
        await self._db.execute(
            "UPDATE pomodoro_session SET phase = ?, remaining_seconds = ?, running = ?, "
            "completed_in_cycle = ?, updated_at = ? WHERE id = 1",
            (phase, remaining_seconds, 1 if running else 0, completed_in_cycle, _now_iso()),
        )
        await self._db.commit()
        return {"saved": True, "phase": phase, "remaining_seconds": remaining_seconds,
                "running": running, "completed_in_cycle": completed_in_cycle}

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------
    async def record(self, minutes: int, phase: str = "focus",
                     status: str = "completed") -> dict[str, Any]:
        """记录一个番茄钟（完成或中断），返回今日统计。"""
        minutes = _clamp(minutes, 1, 120)
        if phase not in _PHASES:
            raise PomodoroError(f"未知阶段: {phase}")
        if status not in _STATUSES:
            raise PomodoroError(f"未知状态: {status}")
        await self._db.execute(
            "INSERT INTO pomodoro_records (minutes, phase, status, ts) VALUES (?, ?, ?, ?)",
            (minutes, phase, status, _now_iso()),
        )
        await self._db.commit()
        today = _today()
        row = await self._db.fetchone(
            "SELECT count(*), COALESCE(SUM(minutes), 0) FROM pomodoro_records "
            "WHERE substr(ts, 1, 10) = ? AND status = 'completed'",
            (today,),
        )
        today_count = int(row[0])
        today_minutes = int(row[1])
        log.info("番茄钟记录", detail={"minutes": minutes, "phase": phase,
                                      "status": status, "today_count": today_count})
        return {
            "recorded": True,
            "minutes": minutes,
            "phase": phase,
            "status": status,
            "today_count": today_count,
            "today_minutes": today_minutes,
        }

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    async def stats(self) -> dict[str, Any]:
        """汇总统计：今日/本周/累计、近 14 天分布、阶段分布、中断数、连续天数。"""
        now = datetime.now().astimezone()
        today = now.strftime("%Y-%m-%d")
        week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")

        # 今日（count 含中断，minutes 只算完成）
        row = await self._db.fetchone(
            "SELECT count(*), COALESCE(SUM(CASE WHEN status='completed' THEN minutes END), 0) "
            "FROM pomodoro_records WHERE substr(ts, 1, 10) = ?", (today,),
        )
        today_count, today_minutes = int(row[0]), int(row[1])

        # 本周
        row = await self._db.fetchone(
            "SELECT count(*), COALESCE(SUM(CASE WHEN status='completed' THEN minutes END), 0) "
            "FROM pomodoro_records WHERE substr(ts, 1, 10) BETWEEN ? AND ? AND status='completed'",
            (week_start, today),
        )
        week_count, week_minutes = int(row[0]), int(row[1])

        # 累计
        row = await self._db.fetchone(
            "SELECT count(*), COALESCE(SUM(CASE WHEN status='completed' THEN minutes END), 0) "
            "FROM pomodoro_records WHERE status='completed'",
        )
        total_count, total_minutes = int(row[0]), int(row[1])

        # 近 14 天按天分布
        start_day = (now - timedelta(days=13)).strftime("%Y-%m-%d")
        rows = await self._db.fetchall(
            "SELECT substr(ts,1,10) AS d, count(*), "
            "COALESCE(SUM(CASE WHEN status='completed' THEN minutes END), 0) "
            "FROM pomodoro_records WHERE substr(ts,1,10) >= ? GROUP BY d",
            (start_day,),
        )
        day_map = {r[0]: (int(r[1]), int(r[2])) for r in rows}
        last_14_days = []
        for i in range(13, -1, -1):
            day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            count, minutes = day_map.get(day, (0, 0))
            last_14_days.append({"date": day, "count": count, "minutes": minutes})

        # 阶段分布 + 中断数
        rows = await self._db.fetchall(
            "SELECT phase, count(*) FROM pomodoro_records WHERE status='completed' GROUP BY phase"
        )
        phase_dist = {"focus": 0, "short_break": 0, "long_break": 0}
        for phase, cnt in rows:
            if phase in phase_dist:
                phase_dist[phase] = int(cnt)

        row = await self._db.fetchone(
            "SELECT count(*) FROM pomodoro_records WHERE status='interrupted'"
        )
        interrupted = int(row[0]) if row else 0

        # 连续天数（今天往前，断档即止）
        rows = await self._db.fetchall(
            "SELECT DISTINCT substr(ts,1,10) FROM pomodoro_records "
            "WHERE status='completed' AND substr(ts,1,10) >= ?",
            ((now - timedelta(days=59)).strftime("%Y-%m-%d"),),
        )
        completed_days = {str(r[0]) for r in rows}
        streak = 0
        cursor = today
        for _ in range(60):
            if cursor in completed_days:
                streak += 1
                cursor = (datetime.fromisoformat(cursor) - timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                break

        return {
            "today": {"count": today_count, "minutes": today_minutes},
            "week": {"count": week_count, "minutes": week_minutes, "week_start": week_start},
            "total": {"count": total_count, "minutes": total_minutes},
            "last_14_days": last_14_days,
            "phase_dist": phase_dist,
            "interrupted": interrupted,
            "current_streak_days": streak,
        }
