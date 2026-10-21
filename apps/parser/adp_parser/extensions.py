"""Optional OCR / vision provider extension points.

No OCR or vision implementation ships with this service.  The interfaces below
are intentionally small so an operator can register a provider later without
changing the HTTP contract.  ``/v1/engines`` reports the registry truthfully and
scanned PDFs still fail when no provider is registered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OcrPage:
    page: int
    text: str


class OcrProvider(Protocol):
    """A synchronous OCR/vision provider.

    ``extract`` receives only the uploaded bytes (never a client-supplied path)
    and must return one entry per page it could read.  Providers may call a
    configured VLM internally, but must not silently return empty pages.
    """

    name: str
    kind: str  # "ocr" or "vision"

    def extract(self, data: bytes, file_name: str, mime: str | None) -> list[OcrPage]:
        ...


_REGISTRY: dict[str, OcrProvider] = {}


def register_ocr_provider(provider: OcrProvider) -> None:
    if not getattr(provider, "name", None):
        raise ValueError("OCR provider must have a name")
    _REGISTRY[provider.name] = provider


def unregister_ocr_provider(name: str) -> None:
    _REGISTRY.pop(name, None)


def get_ocr_provider() -> OcrProvider | None:
    for provider in _REGISTRY.values():
        return provider
    return None


def list_ocr_providers() -> list[dict[str, str]]:
    return [{"name": p.name, "kind": p.kind} for p in _REGISTRY.values()]
