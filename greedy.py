"""
Greedy 第一次课程-教室分配逻辑。

设计原则：
- AssignmentManager 是唯一主状态
- Building._temp_usage 仅作为镜像状态
- 同一 time_slot 内，每完成一门课程分配后，基于动态剩余量重新计算优先级
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

try:
    from .constants import NUM_TIME_SLOTS, CAPACITY_LEVELS
    from .models import Course, Room, StudentGroup
except ImportError:  # pragma: no cover - 仅在脚本直跑时触发
    from constants import NUM_TIME_SLOTS, CAPACITY_LEVELS
    from models import Course, Room, StudentGroup


LOGGER = logging.getLogger(__name__)


class GreedyMixin:
    """
    以 Mixin 形式挂到 Scheduler 上。

    这样做的好处：
    - 保持调用方式仍然是 scheduler.greedy_assign()
    - 不需要大规模重写现有 Scheduler 数据结构
    """

    # ---------- Greedy 初始化 ----------
    def _reset_greedy_state(self) -> None:
        self.assignment_manager.reset()
        self.greedy_unassigned_courses = []

        for building in self.buildings:
            building.reset_temp_usage()

        self.remaining_rooms_by_capacity = {
            ts: dict(self.total_rooms_by_capacity) for ts in range(NUM_TIME_SLOTS)
        }

        self._course_importance_cache = {
            c.id: self._compute_course_importance(c) for c in self.courses
        }
        self._course_chain_impact_cache = {
            c.id: self._compute_time_chain_impact(c) for c in self.courses
        }

        # 默认锚点缓存
        self._default_anchor_cache = None

    # ---------- 状态一致性检查 ----------
    def _room_is_consistent_and_available(self, time_slot: int, room: Room) -> bool:
        """
        检查某个房间在当前 time_slot 下，Building 镜像状态与 AssignmentManager 主状态
        是否一致，并判断该房间是否可用。

        返回:
            True  -> 两侧状态一致，且当前房间空闲
            False -> 两侧状态一致，但当前房间已被占用

        异常:
            若两侧状态不一致，直接抛出 RuntimeError，避免静默漂移。
        """
        building = self.building_map[room.building_id]
        building._ensure_slot(time_slot)

        mirror_used = room.id in building._temp_usage[time_slot][room.capacity]
        manager_used = bool(self.assignment_manager.room_usage_map.get((time_slot, room.id)))

        if mirror_used != manager_used:
            raise RuntimeError(
                "State drift detected before commit: "
                f"time_slot={time_slot}, room_id={room.id}, building_id={room.building_id}, "
                f"capacity={room.capacity}, mirror_used={mirror_used}, manager_used={manager_used}"
            )

        return not mirror_used

    # ---------- 排序与优先级 ----------
    def _compute_course_importance(self, course: Course) -> float:
        return float(sum(group.weight for group in self.groups_by_course[course.id]))

    def _compute_time_chain_impact(self, course: Course) -> float:
        impact = 0.0
        for group in self.groups_by_course[course.id]:
            for idx in group.transition_indices.get(course.id, []):
                prev_cid, curr_cid, dt = group.transitions[idx]
                if prev_cid == course.id or curr_cid == course.id:
                    impact += group.weight * self.evaluator.decay_factor[dt]
        return impact

    def _count_global_feasible_rooms(self, time_slot: int, needed_capacity: int) -> int:
        return sum(
            self.remaining_rooms_by_capacity[time_slot][cap]
            for cap in CAPACITY_LEVELS
            if cap >= needed_capacity
        )

    def _course_priority_key(self, course: Course) -> Tuple[float, float, float, float, int]:
        feasible_rooms = self._count_global_feasible_rooms(course.time_slot, course.stu_num)
        importance = self._course_importance_cache[course.id]
        chain_impact = self._course_chain_impact_cache[course.id]
        return (
            feasible_rooms,     # 越少越优先
            -course.stu_num,    # 人数越多越优先
            -importance,
            -chain_impact,
            course.id,          # 最终确定性
        )

    # ---------- 组选择 ----------
    def _group_relevance_to_course(self, group: StudentGroup, course_id: int) -> int:
        """
        衡量一个学生组在当前课程上的“路径重要性”。
        使用该课程在 group.transitions 中出现的次数作为轻量代理。
        """
        return len(group.transition_indices.get(course_id, []))

    def _get_effective_groups(self, course: Course) -> List[StudentGroup]:
        groups = list(self.groups_by_course[course.id])
        if not groups:
            return []

        groups.sort(
            key=lambda g: (
                -g.weight,
                -self._group_relevance_to_course(g, course.id),
                g.dorm_id,
                g.first_course_id if g.first_course_id is not None else 10**18,
                len(g.transitions),
            )
        )

        total_weight = sum(max(0, g.weight) for g in groups)
        if total_weight <= 0:
            return groups[: self.config.greedy_group_limit]

        threshold = total_weight * self.config.greedy_group_coverage_ratio
        selected: List[StudentGroup] = []
        current = 0.0

        for group in groups:
            selected.append(group)
            current += max(0, group.weight)
            if len(selected) >= self.config.greedy_group_limit:
                break
            if current >= threshold:
                break

        return selected

    # ---------- transition 查询 ----------
    def _get_prev_transition(self, group: StudentGroup, course_id: int) -> Optional[Tuple[int, int]]:
        for idx in group.transition_indices.get(course_id, []):
            prev_cid, curr_cid, dt = group.transitions[idx]
            if curr_cid == course_id:
                return prev_cid, dt
        return None

    def _get_next_transition(self, group: StudentGroup, course_id: int) -> Optional[Tuple[int, int]]:
        for idx in group.transition_indices.get(course_id, []):
            prev_cid, curr_cid, dt = group.transitions[idx]
            if prev_cid == course_id:
                return curr_cid, dt
        return None

    # ---------- 前驱 / 后继来源 ----------
    def _get_prev_source(self, group: StudentGroup, course: Course) -> Tuple[int, int, bool]:
        """
        返回当前课程的前驱来源估计：(source_loc, dt, is_from_dorm)

        规则：
        1. 若该课程没有真实前驱 -> 从宿舍出发，dt=0
        2. 若真实前驱已分配       -> 使用前驱教学楼，dt 为真实 transition 的 dt
        3. 若真实前驱未分配       -> 直接退化为宿舍，dt=0

        这样可以避免误用“非连续前驱”造成来源位置和 dt 语义污染。
        """
        prev_info = self._get_prev_transition(group, course.id)
        if prev_info is None:
            return group.dorm_id, 0, True

        prev_cid, dt = prev_info
        prev_room = self.assignment_manager.get_room_id(prev_cid)
        if prev_room is None:
            return group.dorm_id, 0, True

        prev_bid = self.room_map[prev_room].building_id
        return prev_bid, dt, False

    def _get_default_anchor_buildings(self, limit: int = 3) -> List[int]:
        """
        当无法从下一门课的有效组中提取锚点时，返回默认锚点建筑。
        默认锚点倾向于：
        - 房间供给更多
        - 到其他楼的平均距离更居中
        """
        if self._default_anchor_cache is not None:
            return self._default_anchor_cache[:limit]

        if not self.buildings:
            self._default_anchor_cache = []
            return []

        room_supply = {
            b.id: sum(b.total_by_capacity.values()) for b in self.buildings
        }
        total_weight = sum(max(1, room_supply[b.id]) for b in self.buildings)

        ranked: List[Tuple[float, int, int]] = []
        for candidate in self.buildings:
            avg_dist = 0.0
            for other in self.buildings:
                weight = max(1, room_supply[other.id])
                avg_dist += self.distance_provider.get_dist(candidate.id, other.id) * weight
            avg_dist /= max(1, total_weight)

            ranked.append((
                avg_dist,                    # 越小越“居中”
                -room_supply[candidate.id], # 房间越多越优先
                candidate.id,
            ))

        ranked.sort(key=lambda x: (x[0], x[1], x[2]))
        self._default_anchor_cache = [bid for _, _, bid in ranked]
        return self._default_anchor_cache[:limit]

    def _estimate_next_anchor_buildings(self, course: Course) -> List[int]:
        """
        对尚未分配的下一门课做轻量锚点预测。

        若能从该课 effective_groups 中提取锚点，则按“锚点得票权重”排序；
        若提取失败，则回退到默认锚点建筑，而不是返回空列表。
        """
        limit = max(1, self.config.greedy_source_expand)
        effective_groups = self._get_effective_groups(course)
        if not effective_groups:
            return self._get_default_anchor_buildings(limit=limit)

        anchor_votes: Dict[int, float] = defaultdict(float)

        for group in effective_groups:
            source_loc, _dt, is_from_dorm = self._get_prev_source(group, course)
            ordered = (
                self.sorted_from_dorm[source_loc]
                if is_from_dorm else
                self.sorted_buildings[source_loc]
            )
            if ordered:
                anchor_votes[ordered[0]] += max(1.0, float(group.weight))

        if not anchor_votes:
            return self._get_default_anchor_buildings(limit=limit)

        ranked = sorted(anchor_votes.items(), key=lambda x: (-x[1], x[0]))
        return [bid for bid, _ in ranked[:limit]]

    # ---------- 打分项 ----------
    def _estimate_prev_cost_for_group(self, course: Course, group: StudentGroup, candidate_bid: int) -> float:
        source_loc, dt, is_from_dorm = self._get_prev_source(group, course)
        dist = self.distance_provider.get_dist(source_loc, candidate_bid, is_from_dorm=is_from_dorm)
        if is_from_dorm:
            return group.weight * dist
        return group.weight * dist * self.evaluator.decay_factor[dt]

    def _estimate_next_cost_for_group(self, course: Course, group: StudentGroup, candidate_bid: int) -> float:
        next_info = self._get_next_transition(group, course.id)
        if next_info is None:
            return 0.0

        next_cid, dt = next_info
        next_room = self.assignment_manager.get_room_id(next_cid)

        if next_room is not None:
            next_bid = self.room_map[next_room].building_id
            dist = self.distance_provider.get_dist(candidate_bid, next_bid)
            return group.weight * dist * self.evaluator.decay_factor[dt]

        next_course = self.course_map[next_cid]
        anchor_buildings = self._estimate_next_anchor_buildings(next_course)
        if not anchor_buildings:
            return 0.0

        best_dist = min(self.distance_provider.get_dist(candidate_bid, bid) for bid in anchor_buildings)
        return group.weight * best_dist * self.evaluator.decay_factor[dt]

    def _estimate_waste(self, course: Course, building_id: int) -> float:
        """
        使用当前镜像状态下可见的最优房间估计容量浪费。

        注意：
        这是一个局部近似量，不引入 reservation / lock 机制；
        在当前单线程顺序 Greedy 中，该近似是可接受的。
        """
        room = self.building_map[building_id].peek_best_room(course.time_slot, course.stu_num)
        if room is None:
            return float("inf")
        return float(room.capacity - course.stu_num)

    def _estimate_rarity_penalty(self, course: Course, building_id: int) -> float:
        building = self.building_map[building_id]
        room = building.peek_best_room(course.time_slot, course.stu_num)
        if room is None:
            return float("inf")

        smallest_fit_cap = None
        for cap in CAPACITY_LEVELS:
            if cap >= course.stu_num:
                smallest_fit_cap = cap
                break

        selected_remaining = building.get_remaining_count(course.time_slot, room.capacity)
        smallest_remaining = (
            building.get_remaining_count(course.time_slot, smallest_fit_cap)
            if smallest_fit_cap is not None else 0
        )

        rarity = 1.0 / max(1, selected_remaining)

        # 被迫使用更大房间时加额外惩罚
        if room.capacity != smallest_fit_cap:
            rarity += 1.0 + (room.capacity - course.stu_num) / max(1, room.capacity)

        if smallest_fit_cap is not None and smallest_remaining == 0 and room.capacity > smallest_fit_cap:
            rarity += 1.0

        return rarity

    def _estimate_congestion_penalty(self, course: Course, building_id: int) -> float:
        return self.building_map[building_id].get_used_ratio(course.time_slot)

    def _compute_comprehensive_score(
        self,
        course: Course,
        effective_groups: List[StudentGroup],
        building_id: int,
    ) -> float:
        """
        计算课程分配到某栋楼的综合代价。

        组成：
        - 前驱移动代价
        - 后继预估代价
        - 容量浪费惩罚
        - 容量档稀缺惩罚
        - 楼宇拥挤惩罚

        若当前建筑不可行，或任一关键项非有限，则直接返回 inf。
        """
        building = self.building_map[building_id]
        if not building.has_feasible_capacity(course.time_slot, course.stu_num):
            return float("inf")

        prev_cost = 0.0
        next_cost = 0.0
        for group in effective_groups:
            prev_cost += self._estimate_prev_cost_for_group(course, group, building_id)
            next_cost += self._estimate_next_cost_for_group(course, group, building_id)

        distance_term = (
            self.config.greedy_prev_weight * prev_cost
            + self.config.greedy_next_lambda * next_cost
        )
        waste_penalty = self._estimate_waste(course, building_id)
        rarity_penalty = self._estimate_rarity_penalty(course, building_id)
        congestion_penalty = self._estimate_congestion_penalty(course, building_id)

        terms = (distance_term, waste_penalty, rarity_penalty, congestion_penalty)
        if not all(math.isfinite(v) for v in terms):
            return float("inf")

        return (
            self.config.greedy_distance_weight * distance_term
            + self.config.greedy_waste_weight * waste_penalty
            + self.config.greedy_rarity_weight * rarity_penalty
            + self.config.greedy_congestion_weight * congestion_penalty
        )

    # ---------- 候选建筑 ----------
    def _get_candidate_buildings(self, course: Course, effective_groups: List[StudentGroup]) -> List[int]:
        if not effective_groups:
            return self._get_global_fallback_buildings(course)

        candidates: Set[int] = set()
        expand_n = max(1, self.config.greedy_source_expand)

        for group in effective_groups:
            source_loc, _dt, is_from_dorm = self._get_prev_source(group, course)
            ordered = self.sorted_from_dorm[source_loc] if is_from_dorm else self.sorted_buildings[source_loc]
            candidates.update(ordered[:expand_n])

            next_info = self._get_next_transition(group, course.id)
            if next_info is None:
                continue

            next_cid, _ = next_info
            next_room = self.assignment_manager.get_room_id(next_cid)
            if next_room is not None:
                candidates.add(self.room_map[next_room].building_id)
            else:
                next_course = self.course_map[next_cid]
                anchors = self._estimate_next_anchor_buildings(next_course)
                candidates.update(anchors[:1])

        ordered_candidates = sorted(candidates)
        feasible = [
            bid for bid in ordered_candidates
            if self.building_map[bid].has_feasible_capacity(course.time_slot, course.stu_num)
        ]
        return feasible if feasible else ordered_candidates

    def _get_all_feasible_buildings(self, course: Course) -> List[int]:
        time_slot = course.time_slot
        feasible = []
        for building in self.buildings:
            if not building.has_feasible_capacity(time_slot, course.stu_num):
                continue
            room = building.peek_best_room(time_slot, course.stu_num)
            waste = float("inf") if room is None else room.capacity - course.stu_num
            feasible.append((waste, building.id))
        feasible.sort(key=lambda x: (x[0], x[1]))
        return [bid for _, bid in feasible]

    def _get_global_fallback_buildings(self, course: Course) -> List[int]:
        """
        第三层兜底：按最小可容纳容量优先，再按浪费，再按 building.id。
        """
        time_slot = course.time_slot
        ranked = []
        for building in self.buildings:
            room = building.peek_best_room(time_slot, course.stu_num)
            if room is None:
                ranked.append((float("inf"), float("inf"), building.id))
                continue
            ranked.append((room.capacity, room.capacity - course.stu_num, building.id))
        ranked.sort(key=lambda x: (x[0], x[1], x[2]))
        return [bid for _, _, bid in ranked]

    # ---------- 唯一提交入口 ----------
    def _commit_room_assignment(self, course_id: int, room: Room) -> None:
        """
        唯一提交入口：
        - AssignmentManager 是主状态
        - Building._temp_usage 是镜像状态
        - 提交前先检查主/镜像状态一致性
        """
        course = self.course_map[course_id]
        time_slot = course.time_slot
        new_building = self.building_map[room.building_id]

        old_room_id = self.assignment_manager.get_room_id(course_id)
        if old_room_id == room.id:
            return

        if not self._room_is_consistent_and_available(time_slot, room):
            raise RuntimeError(
                "Target room is not available at commit time: "
                f"course_id={course_id}, time_slot={time_slot}, room_id={room.id}, "
                f"building_id={room.building_id}, capacity={room.capacity}"
            )

        # 课程若已有旧房间，先释放
        if old_room_id is not None:
            old_room = self.room_map[old_room_id]
            old_building = self.building_map[old_room.building_id]

            if self._room_is_consistent_and_available(time_slot, old_room):
                raise RuntimeError(
                    "Old room unexpectedly appears free before release: "
                    f"course_id={course_id}, time_slot={time_slot}, old_room_id={old_room_id}, "
                    f"old_building_id={old_room.building_id}"
                )

            self.assignment_manager.assign(course_id, None)
            old_building.unmark_temp_assigned(time_slot, old_room_id)
            self.remaining_rooms_by_capacity[time_slot][old_room.capacity] += 1

        # 写主状态，再同步镜像
        self.assignment_manager.assign(course_id, room.id)
        new_building.mark_temp_assigned(time_slot, room.id)
        self.remaining_rooms_by_capacity[time_slot][room.capacity] -= 1

        if self.remaining_rooms_by_capacity[time_slot][room.capacity] < 0:
            raise RuntimeError(
                "Negative remaining room count after commit: "
                f"course_id={course_id}, time_slot={time_slot}, room_id={room.id}, "
                f"building_id={room.building_id}, capacity={room.capacity}, "
                f"remaining={self.remaining_rooms_by_capacity[time_slot][room.capacity]}"
            )

    # ---------- 单课分配 ----------
    def _assign_course_without_groups(self, course: Course) -> Optional[Room]:
        for bid in self._get_global_fallback_buildings(course):
            building = self.building_map[bid]
            if not building.has_feasible_capacity(course.time_slot, course.stu_num):
                continue
            room = building.peek_best_room(course.time_slot, course.stu_num)
            if room is None:
                continue
            self._commit_room_assignment(course.id, room)
            return room
        return None

    def _assign_single_course(self, course: Course) -> Optional[Room]:
        effective_groups = self._get_effective_groups(course)
        if not effective_groups:
            return self._assign_course_without_groups(course)

        building_scores: List[Tuple[float, float, int]] = []
        for bid in self._get_candidate_buildings(course, effective_groups):
            if not self.building_map[bid].has_feasible_capacity(course.time_slot, course.stu_num):
                continue

            score = self._compute_comprehensive_score(course, effective_groups, bid)
            waste = self._estimate_waste(course, bid)

            if not math.isfinite(score) or not math.isfinite(waste):
                continue

            building_scores.append((score, waste, bid))

        building_scores.sort(key=lambda x: (x[0], x[1], x[2], course.id))
        candidate_buildings = [bid for _, _, bid in building_scores[: self.config.greedy_max_candidates]]

        layer_sequences = [
            candidate_buildings,
            self._get_all_feasible_buildings(course),
            self._get_global_fallback_buildings(course),
        ]

        seen: Set[int] = set()
        for building_ids in layer_sequences:
            for bid in building_ids:
                if bid in seen:
                    continue
                seen.add(bid)

                building = self.building_map[bid]
                if not building.has_feasible_capacity(course.time_slot, course.stu_num):
                    continue

                room = building.peek_best_room(course.time_slot, course.stu_num)
                if room is None:
                    continue

                self._commit_room_assignment(course.id, room)
                return room

        return None

    # ---------- 报告与校验 ----------
    def _post_greedy_validation(self) -> Dict[str, float]:
        total_cost, distance_cost, penalty_cost = self.evaluator.calculate_total_cost()

        capacity_violations = 0
        conflict_count = 0
        for (ts, room_id), cids in self.assignment_manager.room_usage_map.items():
            room = self.room_map[room_id]
            total_students = sum(self.course_map[cid].stu_num for cid in cids)
            if total_students > room.capacity:
                capacity_violations += 1
            if len(cids) > 1:
                conflict_count += len(cids) - 1

        group_costs = list(self.evaluator.group_cost_cache.values())
        assigned_courses = sum(1 for rid in self.assignment_manager.assignment if rid is not None)
        total_courses = len(self.courses)
        report = {
            "assigned_courses": assigned_courses,
            "unassigned_courses": len(self.greedy_unassigned_courses),
            "unassigned_course_ids": list(self.greedy_unassigned_courses),
            "assignment_rate": (assigned_courses / total_courses) if total_courses else 1.0,
            "total_cost": total_cost,
            "distance_cost": distance_cost,
            "penalty_cost": penalty_cost,
            "avg_group_distance": (sum(group_costs) / len(group_costs)) if group_costs else 0.0,
            "max_group_distance": max(group_costs) if group_costs else 0.0,
            "capacity_violation_count": capacity_violations,
            "conflict_count": conflict_count,
        }
        return report

    # ---------- Greedy 主流程 ----------
    def greedy_assign(self) -> Dict[str, float]:
        """
        第一次课程-教室贪心分配主流程。

        返回：
            report: Dict[str, float]
                包含分配结果与基线统计。

        说明：
        - AssignmentManager 为唯一主状态
        - Building._temp_usage 为镜像状态
        - 同一 time_slot 内，每分配完一门课，会重新基于动态剩余量排序
        """
        self._reset_greedy_state()

        courses_by_slot: Dict[int, List[Course]] = defaultdict(list)
        for course in self.courses:
            courses_by_slot[course.time_slot].append(course)

        for time_slot in sorted(courses_by_slot):
            pending = list(courses_by_slot[time_slot])

            while pending:
                # 每轮只需要取当前最优课程，全量排序会引入额外 O(n log n) 开销。
                best_idx = min(
                    range(len(pending)),
                    key=lambda idx: self._course_priority_key(pending[idx]),
                )
                course = pending.pop(best_idx)

                room = self._assign_single_course(course)
                if room is None:
                    self.greedy_unassigned_courses.append(course.id)
                    LOGGER.warning(
                        "course=%s, slot=%s, stu_num=%s cannot be assigned under current greedy state.",
                        course.id,
                        time_slot,
                        course.stu_num,
                    )

        # Greedy 结束后必须重建 evaluator 缓存
        self.evaluator.rebuild_cache()
        return self._post_greedy_validation()
