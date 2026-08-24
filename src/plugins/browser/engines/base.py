from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class SearchResultItem:
    title: str
    url: str
    content: str
    source_engine: str


@dataclass
class SearchResponse:
    query: str
    answer: Optional[str] = None
    results: List[SearchResultItem] = field(default_factory=list)
    engine_used: str = "unknown"
    success: bool = True
    error: Optional[str] = None


class BaseSearchEngine(ABC):
    """Clase base para motores de búsqueda web."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    async def search(self, query: str, max_results: int = 4) -> SearchResponse:
        pass
