"""
调度系统配置对象。

说明：
- 统一管理 Greedy / Evaluator / Optimizer 使用的超参数
- optimize 子配置是优化阶段唯一参数来源
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OptimizeConfig:
    """优化阶段配置。"""

    max_iters: int = 4000
    initial_temp: float = 80.0
    min_temp: float = 1e-3
    cooling_rate: float = 0.995
    stagnation_limit: int = 800
    reheat_ratio: float = 0.0
    candidate_room_topk: int = 6
    hotspot_sample_size: int = 16
    verify_every: int = 200
    use_sa: bool = True
    enable_verify: bool = True
    random_seed: int = 42


@dataclass
class Config:
    """全局可调参数（全部可通过 YAML 外部配置）"""

    alpha: float = 0.8
    k_top_groups: int = 3
    penalty_w_capacity: float = 1000.0
    penalty_w_conflict: float = 5000.0
    ruin_ratio: float = 0.08

    # 兼容旧配置字段：若传入该值，则会覆盖 optimize.initial_temp
    sa_initial_temp: float | None = None
    optimize: OptimizeConfig = field(default_factory=OptimizeConfig)

    # ---------- Greedy 专用参数 ----------
    greedy_group_coverage_ratio: float = 0.75
    greedy_group_limit: int = 8
    greedy_prev_weight: float = 0.7
    greedy_next_lambda: float = 0.3
    greedy_distance_weight: float = 1.0
    greedy_waste_weight: float = 0.15
    greedy_rarity_weight: float = 0.30
    greedy_congestion_weight: float = 0.05
    greedy_max_candidates: int = 6
    greedy_source_expand: int = 3

    def __post_init__(self) -> None:
        if isinstance(self.optimize, dict):
            self.optimize = OptimizeConfig(**self.optimize)
        if self.sa_initial_temp is not None:
            self.optimize.initial_temp = float(self.sa_initial_temp)
