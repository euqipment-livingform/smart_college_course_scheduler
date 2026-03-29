"""
基础层共享类型。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CostSnapshot:
    total_cost: float
    distance_cost: float
    penalty_cost: float
