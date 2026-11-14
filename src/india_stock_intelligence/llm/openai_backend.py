from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class OpenAIBackendError(RuntimeError):
    pass


class PaidAPICallNotApproved(PermissionError):
    pass


@dataclass(frozen=True)
class BackendResult:
    output: dict
    request_id: str | None
    model: str


class OpenAIResponsesBackend:
    """OpenAI Responses API adapter, disabled unless enabled and approved per run."""

    def __init__(self, *, approved_for_this_run: bool = False, model: str | None = None,
                 api_key: str | None = None, client=None):
        self.approved_for_this_run = approved_for_this_run
        self.enabled = os.environ.get("INDIA_STOCK_OPENAI_ENABLED", "false").lower() == "true"
        self.model = model or os.environ.get("INDIA_STOCK_OPENAI_MODEL")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._client = client

    def generate_json(self, *, agent_id: str, prompt: str, payload: dict, schema: dict) -> BackendResult:
        if not self.approved_for_this_run:
            raise PaidAPICallNotApproved(
                "No OpenAI request was sent. Pass --confirm-paid-api-calls for this run after approving API use."
            )
        if not self.enabled:
            raise PaidAPICallNotApproved("Set INDIA_STOCK_OPENAI_ENABLED=true to opt into the live backend.")
        if not self.api_key:
            raise OpenAIBackendError("OPENAI_API_KEY is not set; the key is never stored in project files.")
        if not self.model:
            raise OpenAIBackendError("Set INDIA_STOCK_OPENAI_MODEL to the model you have chosen; no model is selected by default.")
        client = self._client
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise OpenAIBackendError("Optional OpenAI SDK is not installed. Review and install the [openai] extra first.") from exc
            # SDK-level retries are off so only the orchestrator controls additional billable attempts.
            client = OpenAI(api_key=self.api_key, max_retries=0, timeout=90.0)
            self._client = client
        response_format = {
            "type": "json_schema",
            "name": agent_id,
            "strict": True,
            "schema": schema,
        }
        try:
            response = client.responses.create(
                model=self.model,
                instructions=prompt,
                input=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                text={"format": response_format},
                max_output_tokens=8000,
                store=False,
            )
            text = getattr(response, "output_text", None)
            if not text:
                raise OpenAIBackendError("OpenAI returned no structured output text (possible refusal or incomplete response).")
            try:
                output = json.loads(text)
            except json.JSONDecodeError as exc:
                raise OpenAIBackendError("OpenAI output was not valid JSON despite the requested schema.") from exc
            request_id = getattr(response, "_request_id", None) or getattr(response, "id", None)
            logger.info("Received structured response for %s (request_id=%s)", agent_id, request_id)
            return BackendResult(output=output, request_id=request_id, model=self.model)
        except OpenAIBackendError:
            raise
        except Exception as exc:
            message = str(exc)
            if self.api_key:
                message = message.replace(self.api_key, "[redacted]")
            raise OpenAIBackendError(f"OpenAI request failed for {agent_id}: {message}") from exc
