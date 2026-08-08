"""Small GitHub Contents API client used for durable application state."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests
import streamlit as st


class GitHubSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubSettings:
    token: str
    owner: str
    repo: str
    branch: str = "main"

    @classmethod
    def from_secrets(cls) -> "GitHubSettings":
        try:
            cfg = st.secrets["github"]
            return cls(str(cfg["token"]), str(cfg["owner"]), str(cfg["repo"]), str(cfg.get("branch", "main")))
        except Exception as exc:
            raise GitHubSyncError("Missing [github] settings in Streamlit secrets.") from exc


class GitHubStore:
    def __init__(self, settings: GitHubSettings | None = None) -> None:
        self.settings = settings or GitHubSettings.from_secrets()
        self.base = f"https://api.github.com/repos/{self.settings.owner}/{self.settings.repo}/contents"
        self.headers = {
            "Authorization": f"Bearer {self.settings.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _url(self, path: str) -> str:
        return f"{self.base}/{quote(path.strip('/'), safe='/')}"

    def get_file(self, path: str) -> tuple[bytes | None, str | None]:
        response = requests.get(self._url(path), headers=self.headers,
                                params={"ref": self.settings.branch}, timeout=30)
        if response.status_code == 404:
            return None, None
        if not response.ok:
            raise GitHubSyncError(f"GitHub read failed ({response.status_code}): {response.text[:300]}")
        payload = response.json()
        return base64.b64decode(payload["content"]), payload["sha"]

    def put_file(self, path: str, content: bytes | str, message: str) -> None:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        _, sha = self.get_file(path)
        payload: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(raw).decode("ascii"),
            "branch": self.settings.branch,
        }
        if sha:
            payload["sha"] = sha
        response = requests.put(self._url(path), headers=self.headers, json=payload, timeout=30)
        if not response.ok:
            raise GitHubSyncError(f"GitHub write failed ({response.status_code}): {response.text[:300]}")

    def delete_file(self, path: str, message: str) -> bool:
        _, sha = self.get_file(path)
        if not sha:
            return False
        response = requests.delete(self._url(path), headers=self.headers,
                                   json={"message": message, "sha": sha, "branch": self.settings.branch}, timeout=30)
        if not response.ok:
            raise GitHubSyncError(f"GitHub delete failed ({response.status_code}): {response.text[:300]}")
        return True

    def list_directory(self, path: str) -> list[dict[str, Any]]:
        response = requests.get(self._url(path), headers=self.headers,
                                params={"ref": self.settings.branch}, timeout=30)
        if response.status_code == 404:
            return []
        if not response.ok:
            raise GitHubSyncError(f"GitHub listing failed ({response.status_code}): {response.text[:300]}")
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def read_json(self, path: str, default: Any = None) -> Any:
        content, _ = self.get_file(path)
        return default if content is None else json.loads(content.decode("utf-8"))

    def write_json(self, path: str, value: Any, message: str) -> None:
        self.put_file(path, json.dumps(value, indent=2, ensure_ascii=False), message)

