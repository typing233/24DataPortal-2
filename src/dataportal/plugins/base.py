"""Plugin base classes and protocols."""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.routing import Route
    from dataportal.context import AppContext


class PluginState(enum.Enum):
    DISCOVERED = "discovered"
    VALIDATED = "validated"
    LOADED = "loaded"
    INITIALIZED = "initialized"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    FAILED = "failed"
    UNLOADED = "unloaded"
    DISABLED = "disabled"


@dataclass
class PluginMeta:
    name: str
    version: str
    dataportal_version: str = ">=1.0.0"
    python_version: str = ">=3.10"
    conflicts_with: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    author: str = ""
    description: str = ""


class BasePlugin(ABC):
    meta: PluginMeta

    @abstractmethod
    async def initialize(self, ctx: AppContext) -> None:
        ...

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        ...


class OutputPlugin(BasePlugin):
    """Adds output format (JSON, CSV, XML, etc.)."""

    @abstractmethod
    def format_name(self) -> str:
        ...

    @abstractmethod
    def content_type(self) -> str:
        ...

    @abstractmethod
    def render(self, columns: list[str], rows: list[list], metadata: dict) -> bytes:
        ...


class HTMLBlockPlugin(BasePlugin):
    """Injects HTML blocks into existing pages."""

    @abstractmethod
    def injection_point(self) -> str:
        ...

    @abstractmethod
    async def render_block(self, request: Request, context: dict) -> str:
        ...


class PagePlugin(BasePlugin):
    """Registers new routes/pages."""

    @abstractmethod
    def get_routes(self) -> list[Route]:
        ...

    @abstractmethod
    def nav_items(self) -> list[dict]:
        ...


class SQLFilterPlugin(BasePlugin):
    """AST-based SQL pre/post processing for permission filtering and desensitization."""

    @abstractmethod
    def pre_process(self, sql: str, context: dict) -> str:
        ...

    @abstractmethod
    def post_process(
        self, columns: list[str], rows: list[list], context: dict
    ) -> tuple[list[str], list[list]]:
        ...
