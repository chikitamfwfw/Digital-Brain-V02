from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Session:
    channel_id: int
    command: str              # memo / link / research / planning / journal / review
    history: list[dict] = field(default_factory=list)   # Claude会話履歴
    references: list[str] = field(default_factory=list) # 参照URL・ノートIDリスト
    start_time: datetime = field(default_factory=datetime.utcnow)
    active: bool = True
    # 保存済みのノートパス（[🌟 Permanent化] ボタン表示判定に使用）
    saved_path: str | None = None


class SessionManager:
    def __init__(self):
        self._sessions: dict[int, Session] = {}

    def create(self, channel_id: int, command: str) -> Session:
        session = Session(channel_id=channel_id, command=command)
        self._sessions[channel_id] = session
        return session

    def get(self, channel_id: int) -> Session | None:
        return self._sessions.get(channel_id)

    def get_or_create(self, channel_id: int, command: str) -> Session:
        session = self.get(channel_id)
        if session is None or not session.active:
            session = self.create(channel_id, command)
        return session

    def end(self, channel_id: int):
        if channel_id in self._sessions:
            self._sessions[channel_id].active = False

    def delete(self, channel_id: int):
        self._sessions.pop(channel_id, None)

    def add_message(self, channel_id: int, role: str, content: str):
        session = self.get(channel_id)
        if session:
            session.history.append({"role": role, "content": content})

    def add_reference(self, channel_id: int, ref: str):
        session = self.get(channel_id)
        if session and ref not in session.references:
            session.references.append(ref)
