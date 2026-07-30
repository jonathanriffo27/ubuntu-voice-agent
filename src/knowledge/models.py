from dataclasses import dataclass, field
from typing import Dict, List

@dataclass
class KnowledgeState:
    version: int = 1
    profile: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
