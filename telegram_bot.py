from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class TelegramError(Exception):
    def __init__(self, method: str, status: int, description: str, retry_after: float | None = None) -> None:
        self.method = method
        self.status = status
        self.description = description
        self.retry_after = retry_after
        super().__init__(f"{method}: {status} {description}")


class TelegramApi:
    def __init__(self, token: str, http: httpx.AsyncClient | None = None) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=10.0))

    async def close(self) -> None:
        await self._http.aclose()

    async def call(self, method: str, **params: Any) -> Any:
        payload = {k: v for k, v in params.items() if v is not None}
        try:
            resp = await self._http.post(f"{self._base}/{method}", json=payload)
        except httpx.HTTPError as exc:
            raise TelegramError(method, 0, type(exc).__name__) from None
        try:
            body = resp.json()
        except ValueError:
            raise TelegramError(method, resp.status_code, "invalid JSON") from None
        if not body.get("ok"):
            retry = (body.get("parameters") or {}).get("retry_after")
            raise TelegramError(method, resp.status_code, str(body.get("description", "")), retry)
        return body.get("result")

    async def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict[str, Any]]:
        return await self.call(
            "getUpdates",
            offset=offset,
            timeout=timeout,
            allowed_updates=["message", "callback_query", "my_chat_member"],
        ) or []

    async def send(self, chat_id: int, text: str, markup: dict | None = None) -> dict[str, Any] | None:
        try:
            return await self.call(
                "sendMessage",
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=markup,
            )
        except TelegramError as exc:
            logger.error("Telegram send failed: %s", exc)
            return None

    async def edit(self, chat_id: int, message_id: int, text: str, markup: dict | None = None) -> bool:
        try:
            await self.call(
                "editMessageText",
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=markup,
            )
            return True
        except TelegramError as exc:
            if "message is not modified" in exc.description:
                return True
            logger.warning("Telegram edit failed: %s", exc)
            return False

    async def answer(self, callback_id: str, text: str | None = None) -> None:
        try:
            await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)
        except TelegramError as exc:
            logger.debug("answerCallbackQuery failed: %s", exc)

    async def delete(self, chat_id: int, message_id: int) -> None:
        try:
            await self.call("deleteMessage", chat_id=chat_id, message_id=message_id)
        except TelegramError:
            pass

    async def leave(self, chat_id: int) -> None:
        try:
            await self.call("leaveChat", chat_id=chat_id)
        except TelegramError as exc:
            logger.debug("leaveChat failed: %s", exc)


class OwnerGate:
    def __init__(self, config_owner_id: int | None, owner_username: str) -> None:
        self._config_owner_id = config_owner_id
        self._owner_username = owner_username.lower()

    def resolve(self, update: dict[str, Any], pinned_owner_id: int | None) -> tuple[bool, int | None]:
        actor, chat, text = _actor(update)
        if actor is None or chat is None or chat.get("type") != "private":
            return False, None
        if actor.get("is_bot"):
            return False, None
        user_id = actor.get("id")
        if chat.get("id") != user_id:
            return False, None
        owner = self._config_owner_id or pinned_owner_id
        if owner is not None:
            return user_id == owner, None
        username = str(actor.get("username") or "").lower()
        if self._owner_username and username == self._owner_username and (text or "").startswith("/start"):
            return True, user_id
        return False, None


def _actor(update: dict[str, Any]) -> tuple[dict | None, dict | None, str | None]:
    if "message" in update:
        msg = update["message"]
        return msg.get("from"), msg.get("chat"), msg.get("text")
    if "callback_query" in update:
        cq = update["callback_query"]
        return cq.get("from"), (cq.get("message") or {}).get("chat"), None
    return None, None, None
