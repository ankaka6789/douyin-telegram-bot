from __future__ import annotations

import argparse
import html
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from flask import Flask, jsonify


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_API_URL = (
    "https://api.tikhub.io/api/v1/douyin/app/v3/fetch_user_post_videos"
)
CONFIG_FILENAME = "cauhinh.txt"
PRIORITY_FILENAME = "kenhuutien.txt"
NORMAL_FILENAME = "kenhthuong.txt"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("douyin-telegram-bot")


class BotError(RuntimeError):
    """Lỗi có thể hiển thị an toàn trong log, không chứa token bí mật."""


@dataclass(frozen=True)
class Config:
    tikhub_token: str
    telegram_token: str
    telegram_chat_id: str
    config_dir: Path
    state_file: Path
    timezone_name: str = "Asia/Ho_Chi_Minh"
    priority_interval_minutes: int = 30
    normal_interval_minutes: int = 60
    videos_to_scan: int = 4
    request_timeout_seconds: int = 30
    channel_delay_seconds: float = 0.35
    notify_on_first_run: bool = False
    scan_start_minutes: int = 0
    scan_end_minutes: int = 0
    activity_report_interval_hours: int = 0
    api_url: str = DEFAULT_API_URL

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def scan_window_label(self) -> str:
        if self.scan_start_minutes == self.scan_end_minutes:
            return "Cả ngày"
        return (
            f"{_format_clock_minutes(self.scan_start_minutes)}-"
            f"{_format_clock_minutes(self.scan_end_minutes)}"
        )


@dataclass(frozen=True)
class Video:
    aweme_id: str
    create_time: int
    description: str
    nickname: str
    unique_id: str
    share_url: str
    is_pinned: bool


def _clean_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_key_value_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise BotError(f"Không tìm thấy file cấu hình: {path}")

    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise BotError(
                f"Dòng {line_number} trong {path.name} thiếu dấu '=': {line}"
            )
        key, value = line.split("=", 1)
        key = key.strip().upper()
        if not key:
            raise BotError(f"Dòng {line_number} trong {path.name} có khóa rỗng")
        result[key] = _clean_value(value)
    return result


def resolve_config_file() -> Path:
    explicit_path = os.getenv("CAUHINH_PATH", "").strip()
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()

    render_secret = Path("/etc/secrets") / CONFIG_FILENAME
    if render_secret.exists():
        return render_secret
    return BASE_DIR / CONFIG_FILENAME


def _env_or_file(values: dict[str, str], *keys: str, default: str = "") -> str:
    for key in keys:
        env_value = os.getenv(key)
        if env_value is not None and env_value.strip():
            return env_value.strip()
    for key in keys:
        file_value = values.get(key)
        if file_value is not None and file_value.strip():
            return file_value.strip()
    return default


def _as_bool(value: str, key: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "co", "có", "bat", "bật"}:
        return True
    if normalized in {"0", "false", "no", "n", "khong", "không", "tat", "tắt"}:
        return False
    raise BotError(f"{key} phải là true hoặc false")


def _as_int(value: str, key: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise BotError(f"{key} phải là số nguyên") from exc
    if not minimum <= parsed <= maximum:
        raise BotError(f"{key} phải nằm trong khoảng {minimum}..{maximum}")
    return parsed


def _as_float(value: str, key: str, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise BotError(f"{key} phải là một số") from exc
    if not minimum <= parsed <= maximum:
        raise BotError(f"{key} phải nằm trong khoảng {minimum}..{maximum}")
    return parsed


def _parse_clock_minutes(value: str, key: str) -> int:
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise BotError(f"{key} phải có dạng HH:MM, ví dụ 07:30")
    hour, minute = (int(part) for part in parts)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise BotError(f"{key} không phải giờ hợp lệ: {value}")
    return hour * 60 + minute


def _format_clock_minutes(value: int) -> str:
    hour, minute = divmod(value, 60)
    return f"{hour:02d}:{minute:02d}"


def is_inside_scan_window(
    local_now: datetime, start_minutes: int, end_minutes: int
) -> bool:
    """Khoảng bắt đầu có hiệu lực, khoảng kết thúc không còn hiệu lực.

    Hai mốc bằng nhau nghĩa là quét cả ngày. Khoảng qua nửa đêm cũng được hỗ trợ,
    ví dụ 22:00-06:00.
    """
    if start_minutes == end_minutes:
        return True
    current_minutes = local_now.hour * 60 + local_now.minute
    if start_minutes < end_minutes:
        return start_minutes <= current_minutes < end_minutes
    return current_minutes >= start_minutes or current_minutes < end_minutes


def _looks_like_placeholder(value: str) -> bool:
    upper = value.strip().upper()
    return not value.strip() or upper.startswith(("DIEN_", "YOUR_", "THAY_"))


def load_config() -> Config:
    config_path = resolve_config_file()
    values = parse_key_value_file(config_path)

    tikhub_token = _env_or_file(values, "TIKHUB_API_TOKEN", "TIKHUB_API_KEY")
    telegram_token = _env_or_file(values, "TELEGRAM_BOT_TOKEN")
    telegram_chat_id = _env_or_file(values, "TELEGRAM_CHAT_ID")

    missing: list[str] = []
    if _looks_like_placeholder(tikhub_token):
        missing.append("TIKHUB_API_TOKEN")
    if _looks_like_placeholder(telegram_token):
        missing.append("TELEGRAM_BOT_TOKEN")
    if _looks_like_placeholder(telegram_chat_id):
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise BotError("Chưa điền cấu hình bắt buộc: " + ", ".join(missing))

    timezone_name = _env_or_file(values, "MUI_GIO", default="Asia/Ho_Chi_Minh")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise BotError(f"MUI_GIO không hợp lệ: {timezone_name}") from exc

    state_value = _env_or_file(values, "STATE_FILE", default="data/trangthai.json")
    state_file = Path(state_value)
    if not state_file.is_absolute():
        state_file = BASE_DIR / state_file

    return Config(
        tikhub_token=tikhub_token,
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
        config_dir=config_path.parent,
        state_file=state_file,
        timezone_name=timezone_name,
        priority_interval_minutes=_as_int(
            _env_or_file(values, "CHU_KY_UU_TIEN_PHUT", default="30"),
            "CHU_KY_UU_TIEN_PHUT",
            1,
            10080,
        ),
        normal_interval_minutes=_as_int(
            _env_or_file(values, "CHU_KY_THUONG_PHUT", default="60"),
            "CHU_KY_THUONG_PHUT",
            1,
            10080,
        ),
        videos_to_scan=_as_int(
            _env_or_file(values, "SO_VIDEO_QUET", default="4"),
            "SO_VIDEO_QUET",
            4,
            20,
        ),
        request_timeout_seconds=_as_int(
            _env_or_file(values, "TIMEOUT_GIAY", default="30"),
            "TIMEOUT_GIAY",
            5,
            180,
        ),
        channel_delay_seconds=_as_float(
            _env_or_file(values, "NGHI_GIUA_CAC_KENH_GIAY", default="0.35"),
            "NGHI_GIUA_CAC_KENH_GIAY",
            0,
            60,
        ),
        notify_on_first_run=_as_bool(
            _env_or_file(values, "THONG_BAO_LAN_DAU", default="false"),
            "THONG_BAO_LAN_DAU",
        ),
        scan_start_minutes=_parse_clock_minutes(
            _env_or_file(values, "GIO_BAT_DAU_QUET", default="00:00"),
            "GIO_BAT_DAU_QUET",
        ),
        scan_end_minutes=_parse_clock_minutes(
            _env_or_file(values, "GIO_KET_THUC_QUET", default="00:00"),
            "GIO_KET_THUC_QUET",
        ),
        activity_report_interval_hours=_as_int(
            _env_or_file(
                values, "THONG_BAO_HOAT_DONG_MOI_GIO", default="0"
            ),
            "THONG_BAO_HOAT_DONG_MOI_GIO",
            0,
            720,
        ),
        api_url=_env_or_file(values, "TIKHUB_API_URL", default=DEFAULT_API_URL),
    )


def load_sec_uids(path: Path) -> list[str]:
    if not path.exists():
        logger.warning("Không tìm thấy %s; nhóm này được xem là rỗng", path)
        return []

    result: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Cho phép ghi chú dễ nhớ theo dạng: sec_uid | tên kênh
        sec_uid = line.split("|", 1)[0].strip()
        if sec_uid and sec_uid not in seen:
            result.append(sec_uid)
            seen.add(sec_uid)
    return result


def load_channel_groups(config_dir: Path) -> tuple[list[str], list[str]]:
    priority = load_sec_uids(config_dir / PRIORITY_FILENAME)
    priority_set = set(priority)
    normal = [
        uid
        for uid in load_sec_uids(config_dir / NORMAL_FILENAME)
        if uid not in priority_set
    ]
    return priority, normal


def _to_timestamp(value: Any) -> int:
    try:
        timestamp = int(float(value))
    except (TypeError, ValueError):
        return 0
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    return max(timestamp, 0)


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def parse_video(item: dict[str, Any]) -> Video | None:
    aweme_id = _first_nonempty(item.get("aweme_id"), item.get("id"))
    if not aweme_id:
        return None

    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    share_info = (
        item.get("share_info") if isinstance(item.get("share_info"), dict) else {}
    )
    share_url = _first_nonempty(
        share_info.get("share_url"),
        item.get("share_url"),
        f"https://www.douyin.com/video/{aweme_id}",
    )
    is_pinned = bool(
        item.get("is_top")
        or item.get("is_pinned")
        or item.get("is_pin")
        or item.get("top")
    )
    return Video(
        aweme_id=aweme_id,
        create_time=_to_timestamp(
            item.get("create_time", item.get("createTime", item.get("create_at")))
        ),
        description=_first_nonempty(
            item.get("desc"), item.get("description"), "(Không có mô tả)"
        ),
        nickname=_first_nonempty(author.get("nickname"), "Không rõ tên kênh"),
        unique_id=_first_nonempty(
            author.get("unique_id"), author.get("short_id"), author.get("uid")
        ),
        share_url=share_url,
        is_pinned=is_pinned,
    )


def _is_video_list(value: Any) -> bool:
    return bool(
        isinstance(value, list)
        and all(isinstance(item, dict) for item in value[:3])
        and any(
            isinstance(item, dict) and ("aweme_id" in item or "create_time" in item)
            for item in value[:3]
        )
    )


def extract_video_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Hỗ trợ cấu trúc App V3 hiện tại và vài lớp bọc tương thích."""
    queue: list[Any] = [payload.get("data"), payload]
    visited = 0
    list_keys = ("aweme_list", "aweme_detail_list", "item_list", "items", "videos")
    container_keys = ("data", "result")

    while queue and visited < 20:
        node = queue.pop(0)
        visited += 1
        if _is_video_list(node):
            return node
        if not isinstance(node, dict):
            continue
        for key in list_keys:
            value = node.get(key)
            if _is_video_list(value):
                return value
        for key in container_keys:
            value = node.get(key)
            if isinstance(value, (dict, list)):
                queue.append(value)
    return []


def select_latest_video(
    items: Iterable[dict[str, Any]], videos_to_scan: int = 4
) -> tuple[Video | None, list[Video]]:
    scanned: list[Video] = []
    for item in list(items)[:videos_to_scan]:
        video = parse_video(item)
        if video:
            scanned.append(video)
    if not scanned:
        return None, []

    # create_time mới nhất thắng. Nếu API thiếu create_time, giữ thứ tự trang chủ.
    latest = max(enumerate(scanned), key=lambda pair: (pair[1].create_time, -pair[0]))[1]
    return latest, scanned


class TikHubClient:
    def __init__(self, config: Config, session: requests.Session):
        self.config = config
        self.session = session

    def fetch_posts(self, sec_uid: str) -> list[dict[str, Any]]:
        try:
            response = self.session.get(
                self.config.api_url,
                headers={
                    "Authorization": f"Bearer {self.config.tikhub_token}",
                    "Accept": "application/json",
                },
                params={
                    "sec_user_id": sec_uid,
                    "max_cursor": 0,
                    "count": self.config.videos_to_scan,
                    "sort_type": 0,
                },
                timeout=self.config.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise BotError(f"Không kết nối được TikHub: {type(exc).__name__}") from exc

        if response.status_code != 200:
            raise BotError(f"TikHub trả HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise BotError("TikHub trả dữ liệu không phải JSON") from exc

        api_code = payload.get("code")
        if api_code not in (None, 0, 200, "0", "200"):
            message = _first_nonempty(
                payload.get("message_zh"), payload.get("message"), "không rõ lỗi"
            )
            raise BotError(f"TikHub báo lỗi code={api_code}: {message[:240]}")

        items = extract_video_items(payload)
        if not items:
            message = _first_nonempty(payload.get("message_zh"), payload.get("message"))
            suffix = f" ({message[:160]})" if message else ""
            raise BotError(f"TikHub không trả về danh sách video{suffix}")
        return items


class TelegramClient:
    def __init__(self, config: Config, session: requests.Session):
        self.config = config
        self.session = session

    def _post(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{self.config.telegram_token}/{method}"
        try:
            response = self.session.post(
                url, json=body, timeout=self.config.request_timeout_seconds
            )
        except requests.RequestException as exc:
            raise BotError(f"Không kết nối được Telegram: {type(exc).__name__}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise BotError(f"Telegram trả HTTP {response.status_code}, không có JSON") from exc
        if response.status_code != 200 or not payload.get("ok"):
            description = _first_nonempty(payload.get("description"), "không rõ lỗi")
            raise BotError(
                f"Telegram trả HTTP {response.status_code}: {description[:240]}"
            )
        return payload

    def send_plain(self, text: str) -> None:
        self._post(
            "sendMessage",
            {
                "chat_id": self.config.telegram_chat_id,
                "text": text,
                "disable_notification": False,
            },
        )

    def send_activity_report(self, status: dict[str, Any]) -> None:
        local_now = datetime.now(self.config.tz).strftime("%d/%m/%Y %H:%M:%S")
        scan_state = (
            "Đang trong khung giờ quét"
            if status.get("inside_scan_window")
            else "Đang nghỉ ngoài khung giờ quét"
        )
        last_check_value = status.get("last_check")
        last_check = "Chưa có"
        if last_check_value:
            try:
                last_check = (
                    datetime.fromisoformat(str(last_check_value))
                    .astimezone(self.config.tz)
                    .strftime("%d/%m/%Y %H:%M:%S")
                )
            except ValueError:
                last_check = str(last_check_value)
        text = (
            "✅ <b>Bot Douyin vẫn hoạt động bình thường</b>\n\n"
            f"🕒 <b>Thời gian:</b> {html.escape(local_now)}\n"
            f"⏰ <b>Khung quét:</b> {html.escape(self.config.scan_window_label)}\n"
            f"📡 <b>Hiện tại:</b> {html.escape(scan_state)}\n"
            f"⭐ <b>Kênh ưu tiên:</b> {int(status.get('priority_channels', 0))}\n"
            f"📁 <b>Kênh thường:</b> {int(status.get('normal_channels', 0))}\n"
            f"🔄 <b>Lượt gọi TikHub:</b> {int(status.get('api_calls', 0))}\n"
            f"⚠️ <b>Số lỗi:</b> {int(status.get('errors', 0))}\n"
            f"🧭 <b>Lần quét gần nhất:</b> {html.escape(str(last_check))}"
        )
        self._post(
            "sendMessage",
            {
                "chat_id": self.config.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "disable_notification": False,
            },
        )

    def send_video_alert(self, video: Video, group_name: str) -> None:
        local_time = (
            datetime.fromtimestamp(video.create_time, tz=timezone.utc)
            .astimezone(self.config.tz)
            .strftime("%d/%m/%Y %H:%M:%S")
            if video.create_time
            else "Không rõ"
        )
        username = f" (@{html.escape(video.unique_id)})" if video.unique_id else ""
        description = video.description
        if len(description) > 1400:
            description = description[:1397] + "..."

        text = (
            f"🔔 <b>{html.escape(group_name)} vừa có video mới</b>\n\n"
            f"👤 <b>{html.escape(video.nickname)}</b>{username}\n"
            f"🕒 {local_time}\n"
            f"🆔 <code>{html.escape(video.aweme_id)}</code>\n\n"
            f"📝 {html.escape(description)}"
        )
        self._post(
            "sendMessage",
            {
                "chat_id": self.config.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "▶️ Mở video trên Douyin", "url": video.share_url}]
                    ]
                },
            },
        )


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "channels": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data.get("channels"), dict):
                raise ValueError("channels không hợp lệ")
            return data
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.error("Không đọc được state %s: %s", self.path, exc)
            return {"version": 1, "channels": {}}

    def get_channel(self, sec_uid: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._data["channels"].get(sec_uid)
            return dict(value) if isinstance(value, dict) else None

    def save_observation(
        self,
        sec_uid: str,
        latest: Video,
        scanned: list[Video],
        preserve_newer_time: int = 0,
    ) -> None:
        with self._lock:
            old = self._data["channels"].get(sec_uid, {})
            old_seen = old.get("seen_ids", []) if isinstance(old, dict) else []
            seen = [latest.aweme_id]
            seen.extend(video.aweme_id for video in scanned)
            seen.extend(str(value) for value in old_seen)
            seen = list(dict.fromkeys(value for value in seen if value))[:30]

            old_time = _to_timestamp(old.get("latest_time")) if isinstance(old, dict) else 0
            latest_time = max(latest.create_time, old_time, preserve_newer_time)
            latest_id = (
                latest.aweme_id
                if latest.create_time >= old_time
                else _first_nonempty(old.get("latest_id"), latest.aweme_id)
            )
            self._data["channels"][sec_uid] = {
                "latest_id": latest_id,
                "latest_time": latest_time,
                "seen_ids": seen,
                "updated_at": int(time.time()),
            }
            self._write_locked()

    def _write_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)


class MonitorService:
    def __init__(
        self,
        config: Config,
        state: StateStore,
        tikhub: TikHubClient,
        telegram: TelegramClient,
    ):
        self.config = config
        self.state = state
        self.tikhub = tikhub
        self.telegram = telegram

    def check_channel(self, sec_uid: str, group_name: str) -> str:
        items = self.tikhub.fetch_posts(sec_uid)
        latest, scanned = select_latest_video(items, self.config.videos_to_scan)
        if latest is None:
            raise BotError("Không tìm thấy video hợp lệ trong 4 mục đầu")

        saved = self.state.get_channel(sec_uid)
        if saved is None:
            if self.config.notify_on_first_run:
                self.telegram.send_video_alert(latest, group_name)
                self.state.save_observation(sec_uid, latest, scanned)
                return "notified-first-run"
            self.state.save_observation(sec_uid, latest, scanned)
            return "baselined"

        saved_time = _to_timestamp(saved.get("latest_time"))
        seen_ids = {str(value) for value in saved.get("seen_ids", [])}
        is_newer = latest.create_time > saved_time
        is_same_second_new_id = (
            latest.create_time == saved_time
            and latest.aweme_id not in seen_ids
            and latest.aweme_id != str(saved.get("latest_id", ""))
        )
        missing_time_new_id = (
            latest.create_time == 0 and latest.aweme_id not in seen_ids
        )

        if is_newer or is_same_second_new_id or missing_time_new_id:
            # Chỉ ghi state sau khi Telegram nhận thành công để lần quét sau còn thử lại.
            self.telegram.send_video_alert(latest, group_name)
            self.state.save_observation(sec_uid, latest, scanned)
            return "notified"

        self.state.save_observation(
            sec_uid, latest, scanned, preserve_newer_time=saved_time
        )
        return "unchanged"


class BotRuntime:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._status_lock = threading.Lock()
        self._status: dict[str, Any] = {
            "state": "starting",
            "last_check": None,
            "last_error": None,
            "priority_channels": 0,
            "normal_channels": 0,
            "api_calls": 0,
            "notifications": 0,
            "activity_reports": 0,
            "last_activity_report": None,
            "errors": 0,
            "scan_window": None,
            "inside_scan_window": None,
            "activity_report_interval_hours": 0,
        }

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(
            target=self._supervise, name="douyin-monitor", daemon=True
        )
        self.thread.start()

    def snapshot(self) -> dict[str, Any]:
        with self._status_lock:
            snapshot = dict(self._status)
        snapshot["scheduler_alive"] = bool(self.thread and self.thread.is_alive())
        return snapshot

    def _update(self, **values: Any) -> None:
        with self._status_lock:
            self._status.update(values)

    def _supervise(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._run_scheduler_session()
            except Exception as exc:  # Giữ web health sống để Render hiển thị được lỗi.
                safe_error = str(exc) if isinstance(exc, BotError) else type(exc).__name__
                logger.exception("Vòng giám sát bị lỗi: %s", safe_error)
                self._update(state="error", last_error=safe_error)
                self.stop_event.wait(30)

    def _run_scheduler_session(self) -> None:
        config = load_config()
        priority, normal = load_channel_groups(config.config_dir)
        self._update(
            state="running",
            last_error=None,
            priority_channels=len(priority),
            normal_channels=len(normal),
            scan_window=config.scan_window_label,
            inside_scan_window=is_inside_scan_window(
                datetime.now(config.tz),
                config.scan_start_minutes,
                config.scan_end_minutes,
            ),
            activity_report_interval_hours=config.activity_report_interval_hours,
        )
        logger.info(
            "Bot sẵn sàng: %d kênh ưu tiên (%d phút), %d kênh thường (%d phút)",
            len(priority),
            config.priority_interval_minutes,
            len(normal),
            config.normal_interval_minutes,
        )
        logger.info(
            "Khung giờ quét: %s (%s); thông báo hoạt động: %s",
            config.scan_window_label,
            config.timezone_name,
            (
                f"mỗi {config.activity_report_interval_hours} giờ"
                if config.activity_report_interval_hours
                else "tắt"
            ),
        )

        state = StateStore(config.state_file)
        session = requests.Session()
        telegram = TelegramClient(config, session)
        monitor = MonitorService(
            config,
            state,
            TikHubClient(config, session),
            telegram,
        )
        next_priority = 0.0
        next_normal = 0.0
        next_activity_report = (
            time.monotonic() + config.activity_report_interval_hours * 3600
            if config.activity_report_interval_hours
            else float("inf")
        )
        outside_window_logged = False

        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                inside_window = is_inside_scan_window(
                    datetime.now(config.tz),
                    config.scan_start_minutes,
                    config.scan_end_minutes,
                )
                self._update(inside_scan_window=inside_window)

                if now >= next_activity_report:
                    self._send_activity_report(telegram)
                    next_activity_report = (
                        time.monotonic()
                        + config.activity_report_interval_hours * 3600
                    )
                    now = time.monotonic()

                if not inside_window:
                    if not outside_window_logged:
                        logger.info(
                            "Ngoài khung giờ %s: tạm dừng quét TikHub",
                            config.scan_window_label,
                        )
                        outside_window_logged = True
                    # Đặt lại để khi bước vào khung giờ, cả hai nhóm được quét ngay.
                    next_priority = 0.0
                    next_normal = 0.0
                    report_wait = next_activity_report - time.monotonic()
                    wait_seconds = max(1.0, min(30.0, report_wait))
                    self.stop_event.wait(wait_seconds)
                    continue

                if outside_window_logged:
                    logger.info(
                        "Đã vào khung giờ %s: tiếp tục quét TikHub",
                        config.scan_window_label,
                    )
                    outside_window_logged = False

                if now >= next_priority:
                    priority, normal = load_channel_groups(config.config_dir)
                    self._update(
                        priority_channels=len(priority), normal_channels=len(normal)
                    )
                    self._check_group(monitor, priority, "Kênh ưu tiên", config)
                    next_priority = time.monotonic() + (
                        config.priority_interval_minutes * 60
                    )

                now = time.monotonic()
                if now >= next_normal:
                    priority, normal = load_channel_groups(config.config_dir)
                    self._update(
                        priority_channels=len(priority), normal_channels=len(normal)
                    )
                    self._check_group(monitor, normal, "Kênh thường", config)
                    next_normal = time.monotonic() + (
                        config.normal_interval_minutes * 60
                    )

                wait_seconds = max(
                    1.0,
                    min(
                        30.0,
                        next_priority - time.monotonic(),
                        next_normal - time.monotonic(),
                        next_activity_report - time.monotonic(),
                    ),
                )
                self.stop_event.wait(wait_seconds)
        finally:
            session.close()

    def _send_activity_report(self, telegram: TelegramClient) -> None:
        try:
            telegram.send_activity_report(self.snapshot())
            sent_at = datetime.now(timezone.utc).isoformat()
            self._update(
                activity_reports=self.snapshot()["activity_reports"] + 1,
                last_activity_report=sent_at,
            )
            logger.info("Đã gửi thông báo bot vẫn hoạt động")
        except BotError as exc:
            self._update(
                errors=self.snapshot()["errors"] + 1,
                last_error=f"Thông báo hoạt động: {exc}",
            )
            logger.error("Không gửi được thông báo hoạt động: %s", exc)

    def _check_group(
        self,
        monitor: MonitorService,
        sec_uids: list[str],
        group_name: str,
        config: Config,
    ) -> None:
        if not sec_uids:
            logger.info("%s: không có sec_uid", group_name)
            self._update(last_check=datetime.now(timezone.utc).isoformat())
            return

        logger.info("Bắt đầu quét %s: %d kênh", group_name.lower(), len(sec_uids))
        for index, sec_uid in enumerate(sec_uids):
            try:
                self._update(api_calls=self.snapshot()["api_calls"] + 1)
                result = monitor.check_channel(sec_uid, group_name)
                if result.startswith("notified"):
                    self._update(
                        notifications=self.snapshot()["notifications"] + 1
                    )
                    logger.info("Đã gửi thông báo cho sec_uid=%s", sec_uid)
                elif result == "baselined":
                    logger.info("Đã tạo mốc lần đầu cho sec_uid=%s", sec_uid)
                else:
                    logger.info("Chưa có video mới: sec_uid=%s", sec_uid)
            except BotError as exc:
                self._update(
                    errors=self.snapshot()["errors"] + 1,
                    last_error=f"sec_uid={sec_uid}: {exc}",
                )
                logger.error("Lỗi sec_uid=%s: %s", sec_uid, exc)
            except Exception as exc:
                self._update(
                    errors=self.snapshot()["errors"] + 1,
                    last_error=f"sec_uid={sec_uid}: {type(exc).__name__}",
                )
                logger.exception("Lỗi bất ngờ khi quét sec_uid=%s", sec_uid)

            if index < len(sec_uids) - 1 and config.channel_delay_seconds:
                self.stop_event.wait(config.channel_delay_seconds)

        self._update(last_check=datetime.now(timezone.utc).isoformat())
        logger.info("Quét xong %s", group_name.lower())


app = Flask(__name__)
runtime = BotRuntime()


@app.get("/")
def index():
    status = runtime.snapshot()
    return jsonify(
        {
            "service": "douyin-telegram-monitor",
            "status": "ok",
            "scheduler": status,
            "health_url": "/health",
        }
    )


@app.get("/health")
def health():
    # Luôn trả 200 để UptimeRobot tiếp tục đánh thức Render; lỗi cấu hình nằm trong JSON/log.
    return jsonify({"status": "ok", "scheduler": runtime.snapshot()})


def test_telegram() -> int:
    try:
        config = load_config()
        with requests.Session() as session:
            TelegramClient(config, session).send_plain(
                "✅ Kết nối Telegram thành công. Bot giám sát Douyin đã đọc đúng cấu hình."
            )
        print("Đã gửi tin nhắn thử tới Telegram.")
        return 0
    except BotError as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Bot giám sát video mới trên Douyin")
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="gửi một tin thử rồi thoát, không gọi TikHub",
    )
    args = parser.parse_args()
    if args.test_telegram:
        return test_telegram()

    runtime.start()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
    return 0


if __name__ != "__main__" and os.getenv("DOUYIN_BOT_DISABLE_AUTOSTART") != "1":
    runtime.start()


if __name__ == "__main__":
    raise SystemExit(main())
