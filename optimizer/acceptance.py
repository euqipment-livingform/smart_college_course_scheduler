"""
接受策略。
"""

from __future__ import annotations

import math
import random


class BaseAcceptancePolicy:
    def should_accept(self, delta_cost: float) -> bool:
        raise NotImplementedError

    def on_iteration_end(self) -> None:
        pass

    def is_exhausted(self) -> bool:
        return False


class GreedyAcceptPolicy(BaseAcceptancePolicy):
    def should_accept(self, delta_cost: float) -> bool:
        return delta_cost <= 0


class SimulatedAnnealingPolicy(BaseAcceptancePolicy):
    def __init__(
        self,
        initial_temp: float,
        cooling_rate: float,
        min_temp: float,
        rng: random.Random,
    ):
        self.temperature = max(float(initial_temp), 1e-9)
        self.cooling_rate = cooling_rate
        self.min_temp = min_temp
        self.rng = rng

    def should_accept(self, delta_cost: float) -> bool:
        if delta_cost <= 0:
            return True
        if self.temperature <= 0:
            return False
        threshold = math.exp(-delta_cost / self.temperature)
        return self.rng.random() < threshold

    def on_iteration_end(self) -> None:
        self.temperature *= self.cooling_rate

    def is_exhausted(self) -> bool:
        return self.temperature < self.min_temp
