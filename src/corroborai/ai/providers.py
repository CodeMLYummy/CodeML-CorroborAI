"""Fournisseurs LLM : gabarit déterministe, Gemini, API compatible OpenAI.

Aucune dépendance externe : HTTP via ``urllib``. Le transport est injectable
(tests). Aucun fournisseur n'expose d'outil au modèle : un appel = un prompt
→ un texte JSON.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol

from corroborai.ai.policy import ProviderSpec

Transport = Callable[[str, dict[str, str], bytes, float], bytes]


class ProviderError(RuntimeError):
    """Échec d'appel au fournisseur (réseau, authentification, réponse vide…)."""


def http_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — URL issue de la configuration
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise ProviderError(f"HTTP {exc.code} : {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"fournisseur injoignable : {exc}") from exc


class Provider(Protocol):
    spec: ProviderSpec

    @property
    def is_template(self) -> bool: ...

    def complete(self, system: str, user: str, temperature: float, max_tokens: int) -> str: ...


class TemplateProvider:
    """Aucun LLM : le harnais utilise directement le gabarit déterministe."""

    def __init__(self, spec: ProviderSpec) -> None:
        self.spec = spec

    @property
    def is_template(self) -> bool:
        return True

    def complete(self, system: str, user: str, temperature: float, max_tokens: int) -> str:
        raise ProviderError("le fournisseur gabarit ne réalise pas d'appel LLM")


class _HttpProvider:
    def __init__(self, spec: ProviderSpec, transport: Transport | None = None) -> None:
        self.spec = spec
        self.transport = transport or http_post

    @property
    def is_template(self) -> bool:
        return False

    def _key(self, required: bool) -> str | None:
        if not self.spec.api_key_env:
            return None
        key = os.environ.get(self.spec.api_key_env)
        if required and not key:
            raise ProviderError(f"variable d'environnement {self.spec.api_key_env} absente")
        return key

    def _post(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        raw = self.transport(url, {"Content-Type": "application/json", **headers},
                             json.dumps(payload).encode("utf-8"), self.spec.timeout_s)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError(f"réponse du fournisseur illisible : {exc}") from exc


class GeminiProvider(_HttpProvider):
    """API Gemini (generateContent). La clé passe par l'en-tête, jamais par l'URL."""

    def complete(self, system: str, user: str, temperature: float, max_tokens: int) -> str:
        key = self._key(required=True)
        url = f"{self.spec.base_url.rstrip('/')}/models/{self.spec.model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens,
                                 "responseMimeType": "application/json"},
        }
        data = self._post(url, {"x-goog-api-key": key or ""}, payload)
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            reason = (data.get("promptFeedback") or {}).get("blockReason") if isinstance(data, dict) else None
            raise ProviderError(f"réponse Gemini sans contenu{f' ({reason})' if reason else ''}") from exc
        if not text.strip():
            raise ProviderError("réponse Gemini vide")
        return text


class OpenAICompatProvider(_HttpProvider):
    """API compatible OpenAI (/chat/completions) : Ollama, LM Studio, llama.cpp, vLLM."""

    def complete(self, system: str, user: str, temperature: float, max_tokens: int) -> str:
        key = self._key(required=False)
        url = f"{self.spec.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.spec.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        data = self._post(url, {"Authorization": f"Bearer {key}"} if key else {}, payload)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("réponse sans contenu (format compatible OpenAI attendu)") from exc
        if not text or not text.strip():
            raise ProviderError("réponse vide")
        return text


def make_provider(spec: ProviderSpec, transport: Transport | None = None) -> Provider:
    if spec.type == "template":
        return TemplateProvider(spec)
    if spec.type == "gemini":
        return GeminiProvider(spec, transport)
    if spec.type == "openai_compat":
        return OpenAICompatProvider(spec, transport)
    raise ProviderError(f"type de fournisseur inconnu : {spec.type}")
