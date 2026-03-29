"""
Scheduler.optimize() 接入层。
"""

from __future__ import annotations

try:
    from ..config import OptimizeConfig
    from .engine import LocalSearchEngine
    from .state_ops import AssignmentTransaction
except ImportError:  # pragma: no cover - 仅在脚本直跑时触发
    from config import OptimizeConfig
    from optimizer.engine import LocalSearchEngine
    from optimizer.state_ops import AssignmentTransaction


class OptimizeMixin:
    def _has_any_assignment(self) -> bool:
        return any(room_id is not None for room_id in self.assignment_manager.assignment)

    def optimize(self, config: OptimizeConfig | None = None):
        optimize_config = config or self.config.optimize
        tx = AssignmentTransaction(self)

        if not self._has_any_assignment():
            self.greedy_assign()
        else:
            tx.verify_invariants()
            self.evaluator.rebuild_cache()

        engine = LocalSearchEngine(self, optimize_config)
        report = engine.run()

        if optimize_config.enable_verify:
            tx.verify_invariants()
            final_cached = self.evaluator.get_cost_snapshot()
            final_full = self.evaluator.full_recompute_cost()
            if (
                abs(final_cached.total_cost - final_full.total_cost) > 1e-6
                or abs(final_cached.distance_cost - final_full.distance_cost) > 1e-6
                or abs(final_cached.penalty_cost - final_full.penalty_cost) > 1e-6
            ):
                raise RuntimeError("Final evaluator verification failed after optimization")

        return report
