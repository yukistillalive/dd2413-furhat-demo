"""把用户列表转换成稳定状态。这里只处理数据，不连接机器人，也不控制动作。"""
from collections import deque
from dataclasses import asdict, dataclass
import math
from statistics import median
import time


@dataclass
class PerceptionConfig:
    # 以下是开发起点，必须在实体机器人上校准。
    enter_distance_m: float = 1.2
    exit_distance_m: float = 1.5
    confirm_seconds: float = 0.8
    leave_seconds: float = 2.0
    stale_seconds: float = 3.0
    distance_unit: str = "unknown"  # 真实数据单位待确认；模拟数据明确使用 m

    def __post_init__(self):
        values = (self.enter_distance_m, self.exit_distance_m,
                  self.confirm_seconds, self.leave_seconds, self.stale_seconds)
        if any(not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError("Distances and time limits must be positive finite numbers")
        if self.exit_distance_m <= self.enter_distance_m:
            raise ValueError("Exit distance must be greater than enter distance")
        if self.distance_unit not in ("unknown", "m", "cm"):
            raise ValueError("distance_unit must be unknown, m, or cm")


class PerceptionTracker:
    """update() 接收一次观察；tick() 在没有新数据时检查是否过期。"""

    def __init__(self, config=None, clock=time.monotonic):
        self.config = config or PerceptionConfig()
        self.clock = clock
        self.started_at = clock()
        self.last_updated = None
        self.status = "starting"
        self.error = None
        self.users = []
        self.target_id = None
        self._candidate_id = None
        self._candidate_since = None
        self._lost_since = None
        self._zone = "none"
        self._distances = deque(maxlen=3)
        self.events = deque(maxlen=30)
        self._event_id = 0

    def _emit(self, name, now, **details):
        self._event_id += 1
        self.events.append(dict(sequence=self._event_id, name=name,
                                at=now, **details))

    @staticmethod
    def _user_id(user):
        value = user.get("id")
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            return str(value)
        return None

    @staticmethod
    def _raw_distance(user):
        value = user.get("distance")
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value > 0):
            return float(value)
        return None

    def _metres(self, user):
        raw = self._raw_distance(user)
        if raw is None or self.config.distance_unit == "unknown":
            return None
        return raw / 100 if self.config.distance_unit == "cm" else raw

    def _clear_pending(self):
        self._candidate_id = None
        self._candidate_since = None
        self._lost_since = None
        self._distances.clear()

    def update(self, data):
        now = self.clock()
        # 缺少 users 不是空列表：不能把坏数据解释成“无人”。
        if (not isinstance(data, dict) or not isinstance(data.get("users"), list)
                or any(not isinstance(u, dict) for u in data["users"])):
            self.mark_error("Invalid user response: expected a users list")
            return self.snapshot()

        self.tick()
        old_status = self.status
        self.users = [dict(u) for u in data["users"]]
        self.last_updated = now
        self.status = "ok"
        self.error = None
        if old_status in ("stale", "error"):
            self._emit("perception_recovered", now)

        target = next((u for u in self.users
                       if self.target_id is not None
                       and self._user_id(u) == self.target_id), None)
        if self.target_id is not None and target is None:
            # 目标暂时丢失时先保留 ID，不马上切换到其他人。
            if self._lost_since is None:
                self._lost_since = now
            self._distances.clear()
            if now - self._lost_since >= self.config.leave_seconds:
                self._emit("person_left", now, user_id=self.target_id)
                self.target_id = None
                self._zone = "none"
                self._clear_pending()
        elif target is not None:
            self._lost_since = None

        if self.target_id is None:
            candidates = [u for u in self.users if self._user_id(u) is not None]
            # 已知单位时选最近者，否则保留官方返回的近到远顺序。
            if self.config.distance_unit != "unknown":
                candidates.sort(key=lambda u: self._metres(u) or float("inf"))
            candidate = candidates[0] if candidates else None
            candidate_id = self._user_id(candidate) if candidate else None
            if candidate_id != self._candidate_id:
                self._candidate_id = candidate_id
                self._candidate_since = now if candidate_id is not None else None
            if (candidate_id is not None and self._candidate_since is not None
                    and now - self._candidate_since >= self.config.confirm_seconds):
                self.target_id = candidate_id
                target = candidate
                self._clear_pending()
                self._emit("person_entered", now, user_id=self.target_id)

        if target is not None and self.target_id is not None:
            distance = self._metres(target)
            old_zone = self._zone
            if distance is None:
                self._distances.clear()
                self._zone = "unknown"
            else:
                self._distances.append(distance)
                smoothed = median(self._distances)
                # 两个边界形成缓冲区，避免人在边界附近时反复切换。
                boundary = (self.config.exit_distance_m if self._zone == "interaction"
                            else self.config.enter_distance_m)
                self._zone = "interaction" if smoothed <= boundary else "far"
            if self._zone != old_zone:
                self._emit("zone_changed", now, previous=old_zone, zone=self._zone)
        return self.snapshot()

    def mark_error(self, message):
        if self.status != "error":
            self._emit("perception_error", self.clock())
        self.status = "error"
        self.error = message
        self._clear_pending()
        self._zone = "unknown"

    def tick(self):
        reference = self.last_updated if self.last_updated is not None else self.started_at
        if (self.status != "stopped" and self.clock() - reference >= self.config.stale_seconds
                and self.status != "stale"):
            self.status = "stale"
            self._clear_pending()
            self._zone = "unknown"
            self._emit("perception_stale", self.clock())
        return self.snapshot()

    def snapshot(self):
        now = self.clock()
        valid = self.status == "ok"
        target = next((u for u in self.users
                       if self.target_id is not None
                       and self._user_id(u) == self.target_id), None) if valid else None
        visible = target is not None
        if not valid:
            tracking = "unknown"
        elif self.target_id is not None:
            tracking = "tracking" if visible else "temporarily_lost"
        elif self.users:
            tracking = "confirming" if self._candidate_id is not None else "missing_id"
        else:
            tracking = "none"
        return dict(
            status=self.status, error=self.error, tracking=tracking,
            person_present=(bool(self.users) or self.target_id is not None) if valid else None,
            user_count=len(self.users) if valid else None,
            target_user_id=self.target_id if valid else None,
            target_visible=visible if valid else None,
            distance_raw=self._raw_distance(target) if target else None,
            distance_m=self._metres(target) if target else None,
            smoothed_distance_m=median(self._distances) if visible and self._distances else None,
            zone=(self._zone if visible else "unknown" if self.users or self.target_id else "none")
                 if valid else "unknown",
            updated_at=self.last_updated,
            data_age_s=round(now - self.last_updated, 2) if self.last_updated is not None else None,
            users=self.users if valid else [], config=asdict(self.config),
        )
