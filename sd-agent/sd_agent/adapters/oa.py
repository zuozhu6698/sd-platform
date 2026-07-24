from __future__ import annotations

import httpx
from pydantic import BaseModel, SecretStr

from sd_agent.oa.service import CompletePendingCommand, OaGatewayResult


class HttpOaGateway:
    """OA TEST adapter 骨架；真实字段映射和端点待 EXT-03 contract POC 固化。"""

    def __init__(
        self,
        *,
        base_url: str,
        token: SecretStr,
        http: httpx.AsyncClient,
    ) -> None:
        normalized = base_url.rstrip("/")
        if not normalized.startswith("https://"):
            raise ValueError("OA base URL must use HTTPS")
        if not token.get_secret_value():
            raise ValueError("OA token is required")
        self._base_url = normalized
        self._token = token
        self._http = http

    async def complete_pending(
        self,
        command: CompletePendingCommand,
        *,
        dedup_key: str,
    ) -> OaGatewayResult:
        return await self._post(command, path="/pending/complete", dedup_key=dedup_key)

    async def send_urge(
        self,
        command: BaseModel,
        *,
        dedup_key: str,
    ) -> OaGatewayResult:
        return await self._post(command, path="/messages/urge", dedup_key=dedup_key)

    async def _post(
        self,
        command: BaseModel,
        *,
        path: str,
        dedup_key: str,
    ) -> OaGatewayResult:
        headers = {
            "Authorization": f"Bearer {self._token.get_secret_value()}",
            "X-Idempotency-Key": dedup_key,
        }
        try:
            response = await self._http.post(
                f"{self._base_url}{path}",
                headers=headers,
                json=command.model_dump(mode="json"),
            )
        except httpx.TimeoutException:
            return OaGatewayResult(False, None, error_code="OA_RESULT_UNKNOWN")
        except httpx.HTTPError:
            return OaGatewayResult(False, None, error_code="OA_NETWORK_ERROR")

        if 200 <= response.status_code < 300:
            try:
                body = response.json()
                receipt_id = body.get("receipt_id") if isinstance(body, dict) else None
            except ValueError:
                receipt_id = None
            if not isinstance(receipt_id, str) or not receipt_id or len(receipt_id) > 128:
                return OaGatewayResult(
                    False,
                    response.status_code,
                    error_code="OA_RESPONSE_INVALID",
                    retryable=False,
                )
            return OaGatewayResult(
                True,
                response.status_code,
                receipt_id=receipt_id,
                retryable=False,
            )
        if response.status_code == 429:
            return OaGatewayResult(False, 429, error_code="OA_RATE_LIMITED")
        if response.status_code >= 500:
            return OaGatewayResult(
                False,
                response.status_code,
                error_code="OA_UNAVAILABLE",
            )
        return OaGatewayResult(
            False,
            response.status_code,
            error_code="OA_REQUEST_REJECTED",
            retryable=False,
        )
