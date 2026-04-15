from __future__ import annotations
import base64
import logging
from datetime import datetime, timezone

from github import Github, GithubException
from github.Repository import Repository

import config

logger = logging.getLogger(__name__)


class GitHubClient:
    def __init__(self):
        self._gh = Github(config.GITHUB_TOKEN)
        self._repo: Repository | None = None

    @property
    def repo(self) -> Repository:
        if self._repo is None:
            self._repo = self._gh.get_repo(config.GITHUB_REPO)
        return self._repo

    def ping(self):
        """接続確認"""
        _ = self.repo.full_name

    # ─── 読み取り ─────────────────────────────────────────────────────────────

    def read_file(self, path: str) -> str:
        """ファイル内容を文字列で返す。存在しない場合は空文字。"""
        try:
            contents = self.repo.get_contents(path)
            return contents.decoded_content.decode("utf-8")
        except GithubException as e:
            if e.status == 404:
                return ""
            raise

    def list_files(self, folder: str) -> list[str]:
        """フォルダ内のファイルパス一覧を返す。"""
        try:
            contents = self.repo.get_contents(folder)
            return [c.path for c in contents if c.type == "file"]
        except GithubException as e:
            if e.status == 404:
                return []
            raise

    # ─── 書き込み ─────────────────────────────────────────────────────────────

    def save_file(self, path: str, content: str, commit_msg: str | None = None) -> str:
        """
        ファイルを作成または更新してコミット。
        返り値: コミットされたファイルのURL
        """
        if commit_msg is None:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            commit_msg = f"[bot] save: {path} @ {ts}"

        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")

        try:
            existing = self.repo.get_contents(path)
            result = self.repo.update_file(
                path=path,
                message=commit_msg,
                content=content,
                sha=existing.sha,
            )
        except GithubException as e:
            if e.status == 404:
                result = self.repo.create_file(
                    path=path,
                    message=commit_msg,
                    content=content,
                )
            else:
                raise

        return result["content"].html_url

    def delete_file(self, path: str, commit_msg: str | None = None) -> bool:
        """ファイルを削除。存在しない場合は False を返す。"""
        if commit_msg is None:
            commit_msg = f"[bot] delete: {path}"
        try:
            existing = self.repo.get_contents(path)
            self.repo.delete_file(path=path, message=commit_msg, sha=existing.sha)
            return True
        except GithubException as e:
            if e.status == 404:
                return False
            raise

    # ─── ユーティリティ ────────────────────────────────────────────────────────

    def build_commit_msg(self, command: str, title: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"[bot] {command}: {title} @ {ts}"
