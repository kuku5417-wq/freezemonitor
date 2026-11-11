"""알림 채널 모듈.

- 채널별 비밀값(토큰·URL)은 환경변수에서만 읽는다. 로그에 비밀값을 남기지 않는다.
- 한 채널이 실패해도 다른 채널과 수집 루프는 계속 동작한다.
- 새 메신저를 붙일 때는 Notifier를 상속해 send()만 구현하면 된다.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import requests

log = logging.getLogger(__name__)
HTTP_TIMEOUT = 10


class Notifier:
    """알림 채널 기본 클래스."""
    name = "base"

    def send(self, text: str) -> None:  # pragma: no cover - 인터페이스
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    """콘솔/로그 출력 (테스트·현장 PC 확인용)."""
    name = "console"

    def send(self, text: str) -> None:
        log.info("[알림] %s", text)


class WebhookNotifier(Notifier):
    """수신 웹훅(JSON POST). 사내 메신저·Teams 워크플로·Slack 호환."""
    name = "webhook"

    def __init__(self, url: str, text_key: str = "text"):
        self.url, self.text_key = url, text_key

    def send(self, text: str) -> None:
        resp = requests.post(self.url, json={self.text_key: text}, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()


class TelegramNotifier(Notifier):
    """텔레그램 봇 메시지."""
    name = "telegram"

    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id

    def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        resp = requests.post(url, data={"chat_id": self.chat_id, "text": text}, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()


class SmartThingsNotifier(Notifier):
    """SmartThings 가상 스위치를 켜서 루틴 알림을 발생시킨다.

    - PAT는 24시간 만료라 OAuth 리프레시 토큰을 사용한다.
    - 토큰 파일(JSON: {"refresh_token": "..."})은 매 갱신 시 새 값으로 덮어쓴다.
    - 루틴 알림 문구는 SmartThings 앱에서 고정되므로 상세 내용은 대시보드에서 확인.
    """
    name = "smartthings"
    API = "https://api.smartthings.com/v1"

    def __init__(self, client_id: str, client_secret: str, device_id: str, token_file: Path):
        self.client_id, self.client_secret = client_id, client_secret
        self.device_id, self.token_file = device_id, token_file

    def _access_token(self) -> str:
        data = json.loads(self.token_file.read_text(encoding="utf-8"))
        resp = requests.post(
            f"{self.API}/oauth/token",
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "refresh_token", "client_id": self.client_id,
                  "refresh_token": data["refresh_token"]},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        tok = resp.json()
        # 새 refresh_token 저장 (이전 토큰은 무효화됨)
        self.token_file.write_text(json.dumps({"refresh_token": tok["refresh_token"]}), encoding="utf-8")
        try:
            os.chmod(self.token_file, 0o600)
        except OSError:
            pass
        return tok["access_token"]

    def _switch(self, token: str, cmd: str) -> None:
        resp = requests.post(
            f"{self.API}/devices/{self.device_id}/commands",
            headers={"Authorization": f"Bearer {token}"},
            json={"commands": [{"component": "main", "capability": "switch", "command": cmd}]},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()

    def send(self, text: str) -> None:
        token = self._access_token()
        self._switch(token, "off")   # 다음 ON이 '변화'로 인식되도록 먼저 OFF
        self._switch(token, "on")    # 루틴: 스위치 ON 시 휴대폰 알림


def _env(name: str | None) -> str | None:
    return os.environ.get(name) if name else None


def build_notifiers(cfg: dict, base_dir: Path) -> list[Notifier]:
    """config의 notifiers 항목으로 활성 채널 목록을 만든다."""
    out: list[Notifier] = []
    if cfg.get("console", {}).get("enabled", True):
        out.append(ConsoleNotifier())

    wh = cfg.get("webhook", {})
    if wh.get("enabled"):
        url = _env(wh.get("url_env"))
        if url:
            out.append(WebhookNotifier(url, wh.get("text_key", "text")))
        else:
            log.error("웹훅 알림: 환경변수 %s 가 비어 있어 비활성화합니다.", wh.get("url_env"))

    tg = cfg.get("telegram", {})
    if tg.get("enabled"):
        token, chat = _env(tg.get("token_env")), _env(tg.get("chat_id_env"))
        if token and chat:
            out.append(TelegramNotifier(token, chat))
        else:
            log.error("텔레그램 알림: 토큰/채팅ID 환경변수가 없어 비활성화합니다.")

    stc = cfg.get("smartthings", {})
    if stc.get("enabled"):
        cid, sec, dev = (_env(stc.get(k)) for k in ("client_id_env", "client_secret_env", "device_id_env"))
        tf = Path(stc.get("token_file", "data/st_token.json"))
        tf = tf if tf.is_absolute() else base_dir / tf
        if cid and sec and dev and tf.exists():
            out.append(SmartThingsNotifier(cid, sec, dev, tf))
        else:
            log.error("SmartThings 알림: 환경변수 또는 토큰 파일이 없어 비활성화합니다.")
    return out


def dispatch(notifiers: list[Notifier], text: str) -> None:
    """모든 채널로 전송. 개별 실패는 로그만 남긴다(비밀값·응답 본문은 기록하지 않음)."""
    for n in notifiers:
        try:
            n.send(text)
        except Exception as exc:
            log.error("알림 전송 실패(%s): %s", n.name, type(exc).__name__)
