"""实例生成主逻辑。"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict

from .config import GeneratorConfig
from .exceptions import GenerationError, InfeasibleError
from .exporter import assemble_output_json
from .models import BuildingSpec, CourseSpec, DormSpec, GroupSpec, RoomSpec
from .validators import validate_all


SCENARIO_FILL_OVERRIDES = {
    "balanced": (0.55, 0.88),
    "tight": (0.72, 0.95),
    "optimize_showcase": (0.60, 0.84),
}


def generate_instance(cfg: GeneratorConfig) -> dict:
    _validate_generation_feasibility(cfg)

    last_error: Exception | None = None
    for attempt in range(cfg.max_global_retry):
        rng = random.Random(cfg.random_seed + attempt)
        try:
            buildings = generate_buildings_and_rooms(cfg, rng)
            dorms = generate_dorms(cfg, buildings, rng)
            dist_building, dist_dorm = build_distance_matrices(buildings, dorms)
            active_slots = choose_active_time_slots(cfg, len(_all_rooms(buildings)), rng)
            courses = build_course_skeleton(buildings, active_slots, cfg, rng)
            course_clusters = build_course_clusters(courses, cfg, rng)
            groups = generate_student_groups(
                dorms=dorms,
                buildings=buildings,
                courses=courses,
                course_clusters=course_clusters,
                active_slots=active_slots,
                dist_dorm=dist_dorm,
                cfg=cfg,
                rng=rng,
            )
            data = assemble_output_json(cfg, buildings, courses, groups, dist_building, dist_dorm)
            validate_all(data, cfg)
            return data
        except GenerationError as exc:
            last_error = exc

    if last_error is None:
        raise GenerationError("generation failed for an unknown reason")
    raise GenerationError(
        f"failed after {cfg.max_global_retry} attempts: {last_error}"
    ) from last_error


def generate_buildings_and_rooms(cfg: GeneratorConfig, rng: random.Random) -> list[BuildingSpec]:
    building_positions = _generate_positions(
        count=cfg.num_buildings,
        campus_size=cfg.campus_size,
        min_distance=cfg.building_min_distance,
        rng=rng,
    )
    room_counts = [rng.randint(*cfg.rooms_per_building_range) for _ in range(cfg.num_buildings)]
    total_rooms = sum(room_counts)
    capacities = _allocate_capacity_levels(total_rooms, cfg.room_ratio_map)

    buildings: list[BuildingSpec] = []
    capacity_cursor = 0
    for building_id, room_count in enumerate(room_counts, start=1):
        rooms: list[RoomSpec] = []
        for room_index in range(1, room_count + 1):
            capacity = capacities[capacity_cursor]
            capacity_cursor += 1
            room_id = building_id * 100 + room_index
            rooms.append(RoomSpec(room_id=room_id, building_id=building_id, capacity=capacity))
        buildings.append(
            BuildingSpec(building_id=building_id, rooms=rooms, pos=building_positions[building_id - 1])
        )
    return buildings


def generate_dorms(
    cfg: GeneratorConfig,
    buildings: list[BuildingSpec],
    rng: random.Random,
) -> list[DormSpec]:
    dorm_positions = _generate_positions(
        count=cfg.num_dorms,
        campus_size=cfg.campus_size,
        min_distance=cfg.dorm_min_distance,
        rng=rng,
        forbidden=[building.pos for building in buildings],
        forbidden_min_distance=cfg.dorm_building_min_distance,
    )
    return [
        DormSpec(dorm_id=(index + 1) * 10, pos=position)
        for index, position in enumerate(dorm_positions)
    ]


def build_distance_matrices(
    buildings: list[BuildingSpec],
    dorms: list[DormSpec],
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    dist_building: dict[str, dict[str, float]] = {}
    for src in buildings:
        row: dict[str, float] = {}
        for dst in buildings:
            row[str(dst.building_id)] = round(_distance(src.pos, dst.pos), 1)
        dist_building[str(src.building_id)] = row

    dist_dorm: dict[str, dict[str, float]] = {}
    for dorm in dorms:
        row = {
            str(building.building_id): round(_distance(dorm.pos, building.pos), 1)
            for building in buildings
        }
        dist_dorm[str(dorm.dorm_id)] = row

    return dist_building, dist_dorm


def choose_active_time_slots(
    cfg: GeneratorConfig,
    total_room_count: int,
    rng: random.Random,
) -> list[int]:
    min_slots, max_slots = cfg.used_time_slots_range
    min_required = math.ceil(cfg.num_courses / total_room_count)
    used_slot_count = max(min_required, min(cfg.num_courses, rng.randint(min_slots, max_slots)))
    if used_slot_count > cfg.total_time_slots:
        raise InfeasibleError("not enough time slots for the requested number of courses")

    block_length = min(5, used_slot_count)
    block_start = rng.randint(0, cfg.total_time_slots - block_length)
    slots = set(range(block_start, block_start + block_length))
    remaining = used_slot_count - len(slots)
    candidates = [slot for slot in range(cfg.total_time_slots) if slot not in slots]
    rng.shuffle(candidates)
    slots.update(candidates[:remaining])
    return sorted(slots)


def build_course_skeleton(
    buildings: list[BuildingSpec],
    active_slots: list[int],
    cfg: GeneratorConfig,
    rng: random.Random,
) -> list[CourseSpec]:
    all_rooms = _all_rooms(buildings)
    slot_course_counts = _distribute_courses(cfg.num_courses, len(active_slots), len(all_rooms), rng)
    fill_ratio_range = SCENARIO_FILL_OVERRIDES.get(cfg.scenario_mode, cfg.course_fill_ratio_range)
    courses: list[CourseSpec] = []
    next_course_id = 1

    for time_slot, course_count in zip(active_slots, slot_course_counts):
        reserved_rooms = rng.sample(all_rooms, course_count)
        for reserved_room in reserved_rooms:
            tier = _choose_target_capacity(reserved_room.capacity, cfg, rng)
            target_fill = rng.uniform(*fill_ratio_range)
            target_enrollment = max(1, int(round(tier * target_fill)))
            preferred_buildings = _choose_preferred_buildings(
                buildings,
                reserved_room.building_id,
                cfg.preferred_buildings_range,
                rng,
            )
            courses.append(
                CourseSpec(
                    course_id=next_course_id,
                    time_slot=time_slot,
                    target_capacity_tier=tier,
                    home_building_id=reserved_room.building_id,
                    preferred_buildings=preferred_buildings,
                    target_enrollment=target_enrollment,
                )
            )
            next_course_id += 1
    return courses


def build_course_clusters(
    courses: list[CourseSpec],
    cfg: GeneratorConfig,
    rng: random.Random,
) -> dict[int, list[int]]:
    cluster_count = max(1, math.ceil(len(courses) / 20))
    slot_histories = [Counter() for _ in range(cluster_count)]
    cluster_sizes = [0 for _ in range(cluster_count)]
    clusters: dict[int, list[int]] = {cluster_id: [] for cluster_id in range(cluster_count)}

    ordered_courses = list(courses)
    rng.shuffle(ordered_courses)
    ordered_courses.sort(key=lambda course: (course.time_slot, course.course_id))

    for course in ordered_courses:
        candidate_cluster_ids = list(range(cluster_count))
        rng.shuffle(candidate_cluster_ids)
        chosen_cluster = min(
            candidate_cluster_ids,
            key=lambda cluster_id: (
                slot_histories[cluster_id][course.time_slot],
                cluster_sizes[cluster_id],
                abs((cluster_id % cfg.num_buildings) + 1 - course.home_building_id),
            ),
        )
        course.cluster_id = chosen_cluster
        slot_histories[chosen_cluster][course.time_slot] += 1
        cluster_sizes[chosen_cluster] += 1
        clusters[chosen_cluster].append(course.course_id)

    return clusters


def generate_student_groups(
    dorms: list[DormSpec],
    buildings: list[BuildingSpec],
    courses: list[CourseSpec],
    course_clusters: dict[int, list[int]],
    active_slots: list[int],
    dist_dorm: dict[str, dict[str, float]],
    cfg: GeneratorConfig,
    rng: random.Random,
) -> list[GroupSpec]:
    groups = _initialize_groups(dorms, courses, course_clusters, dist_dorm, cfg, rng)
    course_map = {course.course_id: course for course in courses}
    courses_by_slot: dict[int, list[CourseSpec]] = defaultdict(list)
    for course in courses:
        courses_by_slot[course.time_slot].append(course)

    for course in sorted(
        courses,
        key=lambda item: (-item.target_capacity_tier, len(item.preferred_buildings), item.time_slot),
    ):
        assigned = False
        for _ in range(cfg.max_local_retry):
            group = _pick_group_for_course_seed(
                course=course,
                groups=groups,
                dist_dorm=dist_dorm,
                rng=rng,
            )
            if group is None:
                continue
            if _assign_group_to_course(group, course):
                assigned = True
                break
        if not assigned:
            raise InfeasibleError(f"unable to seed course {course.course_id} with at least one group")

    while True:
        pending_groups = [group for group in groups if group.remaining_course_slots > 0]
        if not pending_groups:
            break

        group = min(
            pending_groups,
            key=lambda item: (
                _count_feasible_courses_for_group(item, courses_by_slot, active_slots),
                -item.remaining_course_slots,
                -item.weight,
                item.group_id,
            ),
        )
        if _count_feasible_courses_for_group(group, courses_by_slot, active_slots) <= 0:
            raise InfeasibleError(
                f"group {group.group_id} could not reach desired schedule size {group.desired_course_count}"
            )

        course = _pick_course_for_group(
            group=group,
            courses_by_slot=courses_by_slot,
            active_slots=active_slots,
            dist_dorm=dist_dorm,
            buildings=buildings,
            rng=rng,
        )
        if course is None:
            raise InfeasibleError(
                f"group {group.group_id} could not reach desired schedule size {group.desired_course_count}"
            )
        if not _assign_group_to_course(group, course):
            raise InfeasibleError(
                f"group {group.group_id} failed to bind course {course.course_id} despite prior feasibility"
            )

    for course in course_map.values():
        if course.enrolled_weight <= 0:
            raise InfeasibleError(f"course {course.course_id} ended with no enrollment")
    return groups


def _initialize_groups(
    dorms: list[DormSpec],
    courses: list[CourseSpec],
    course_clusters: dict[int, list[int]],
    dist_dorm: dict[str, dict[str, float]],
    cfg: GeneratorConfig,
    rng: random.Random,
) -> list[GroupSpec]:
    dorm_group_counts = _allocate_groups_to_dorms(cfg, rng)
    cluster_anchor_building = _build_cluster_anchor_lookup(course_clusters, courses, cfg)
    groups: list[GroupSpec] = []
    next_group_id = 1
    min_weight, max_weight = cfg.group_weight_range
    min_courses, max_courses = cfg.group_course_count_range

    for dorm, group_count in zip(dorms, dorm_group_counts):
        cluster_order = _rank_clusters_for_dorm(dorm.dorm_id, cluster_anchor_building, dist_dorm)
        for _ in range(group_count):
            major_cluster = _choose_cluster_for_group(cluster_order, rng)
            groups.append(
                GroupSpec(
                    group_id=next_group_id,
                    weight=min_weight,
                    dorm_id=dorm.dorm_id,
                    desired_course_count=min_courses,
                    major_cluster=major_cluster,
                )
            )
            next_group_id += 1

    minimum_weighted_demand = sum(group.weight * group.desired_course_count for group in groups)
    target_weighted_budget = sum(course.target_enrollment for course in courses)
    hard_capacity_budget = sum(course.target_capacity_tier for course in courses)
    usable_budget = min(hard_capacity_budget, max(target_weighted_budget, minimum_weighted_demand))

    if usable_budget < minimum_weighted_demand:
        raise InfeasibleError(
            "course supply is insufficient for the minimum group weight/course-count requirements"
        )

    if sum(group.desired_course_count for group in groups) < cfg.num_courses:
        raise InfeasibleError(
            "minimum group course counts cannot cover the requested number of distinct courses"
        )

    remaining_budget = usable_budget - minimum_weighted_demand
    groups_by_id = {group.group_id: group for group in groups}
    expandable_courses = {
        group.group_id
        for group in groups
        if group.desired_course_count < max_courses
    }
    expandable_weights = {
        group.group_id
        for group in groups
        if group.weight < max_weight
    }

    while remaining_budget > 0 and (expandable_courses or expandable_weights):
        can_grow_courses = [
            groups_by_id[group_id]
            for group_id in expandable_courses
            if groups_by_id[group_id].weight <= remaining_budget
        ]
        can_grow_weights = [
            groups_by_id[group_id]
            for group_id in expandable_weights
            if groups_by_id[group_id].desired_course_count <= remaining_budget
        ]
        if not can_grow_courses and not can_grow_weights:
            break

        grow_courses = False
        if can_grow_courses and can_grow_weights:
            grow_courses = rng.random() < 0.65
        elif can_grow_courses:
            grow_courses = True

        if grow_courses:
            group = _weighted_choice(
                [
                    (
                        max(1.0, (max_courses - item.desired_course_count + 1) / max(1, item.weight)),
                        item,
                    )
                    for item in can_grow_courses
                ],
                rng,
            )
            group.desired_course_count += 1
            remaining_budget -= group.weight
            if group.desired_course_count >= max_courses:
                expandable_courses.discard(group.group_id)
        else:
            group = _weighted_choice(
                [
                    (
                        max(1.0, (max_weight - item.weight + 1) / max(1, item.desired_course_count)),
                        item,
                    )
                    for item in can_grow_weights
                ],
                rng,
            )
            group.weight += 1
            remaining_budget -= group.desired_course_count
            if group.weight >= max_weight:
                expandable_weights.discard(group.group_id)

    return groups


def _count_feasible_courses_for_group(
    group: GroupSpec,
    courses_by_slot: dict[int, list[CourseSpec]],
    active_slots: list[int],
) -> int:
    count = 0
    for time_slot in active_slots:
        if group.has_slot(time_slot):
            continue
        for course in courses_by_slot[time_slot]:
            if group.weight <= course.remaining_capacity:
                count += 1
    return count


def _pick_group_for_course_seed(
    course: CourseSpec,
    groups: list[GroupSpec],
    dist_dorm: dict[str, dict[str, float]],
    rng: random.Random,
) -> GroupSpec | None:
    candidates: list[tuple[float, GroupSpec]] = []
    for group in groups:
        if group.remaining_course_slots <= 0 or group.has_slot(course.time_slot):
            continue
        if group.weight > course.remaining_capacity:
            continue
        distance = _preferred_distance(course, group.dorm_id, dist_dorm)
        score = 0.0
        if group.major_cluster == course.cluster_id:
            score += 6.0
        score += 2.5 / (1.0 + distance)
        score += 0.3 * group.remaining_course_slots
        score += 0.2 * (course.target_gap + 1) / course.target_capacity_tier
        candidates.append((score, group))

    if not candidates:
        return None
    return _weighted_choice(candidates, rng)


def _pick_course_for_group(
    group: GroupSpec,
    courses_by_slot: dict[int, list[CourseSpec]],
    active_slots: list[int],
    dist_dorm: dict[str, dict[str, float]],
    buildings: list[BuildingSpec],
    rng: random.Random,
) -> CourseSpec | None:
    free_slots = [time_slot for time_slot in active_slots if not group.has_slot(time_slot)]
    slot_candidates: list[tuple[float, int]] = []
    for time_slot in free_slots:
        feasible_count = sum(
            1
            for course in courses_by_slot[time_slot]
            if group.weight <= course.remaining_capacity
        )
        if feasible_count:
            slot_candidates.append((float(feasible_count), time_slot))

    if not slot_candidates or not buildings:
        return None

    chosen_slot = _weighted_choice(slot_candidates, rng)
    candidates: list[tuple[float, CourseSpec]] = []
    for course in courses_by_slot[chosen_slot]:
        if group.weight > course.remaining_capacity:
            continue
        distance = _preferred_distance(course, group.dorm_id, dist_dorm)
        score = 1.0
        if group.major_cluster == course.cluster_id:
            score += 5.0
        score += 2.5 / (1.0 + distance)
        score += 1.2 * ((course.target_gap + group.weight) / course.target_capacity_tier)
        score += 0.6 * (course.remaining_capacity / course.target_capacity_tier)
        if course.target_gap <= 0:
            score *= 0.4
        candidates.append((score, course))

    if not candidates:
        alternate_slots = [slot for slot in free_slots if slot != chosen_slot]
        for time_slot in alternate_slots:
            for course in courses_by_slot[time_slot]:
                if group.weight <= course.remaining_capacity:
                    candidates.append((1.0, course))
            if candidates:
                break

    if not candidates:
        return None
    return _weighted_choice(candidates, rng)


def _assign_group_to_course(group: GroupSpec, course: CourseSpec) -> bool:
    if group.has_slot(course.time_slot):
        return False
    if group.weight > course.remaining_capacity:
        return False
    group.add_course(course.time_slot, course.course_id)
    course.enrolled_weight += group.weight
    course.enrolled_group_ids.append(group.group_id)
    return True


def _allocate_groups_to_dorms(cfg: GeneratorConfig, rng: random.Random) -> list[int]:
    if cfg.groups_per_dorm_range is not None:
        low, high = cfg.groups_per_dorm_range
    else:
        average = max(1, cfg.num_groups // cfg.num_dorms)
        low = max(1, average - max(1, average // 3))
        high = average + max(1, average // 3)
    minimum_total = low * cfg.num_dorms
    maximum_total = high * cfg.num_dorms
    if not minimum_total <= cfg.num_groups <= maximum_total:
        raise InfeasibleError(
            "num_groups is incompatible with groups_per_dorm_range; "
            f"expected total in [{minimum_total}, {maximum_total}]"
        )

    counts = [low for _ in range(cfg.num_dorms)]
    remaining = cfg.num_groups - sum(counts)
    expandable = list(range(cfg.num_dorms))
    while remaining > 0 and expandable:
        dorm_index = rng.choice(expandable)
        counts[dorm_index] += 1
        remaining -= 1
        if counts[dorm_index] >= high:
            expandable.remove(dorm_index)
    if remaining != 0:
        raise InfeasibleError("failed to distribute groups across dorms")
    rng.shuffle(counts)
    return counts


def _build_cluster_anchor_lookup(
    course_clusters: dict[int, list[int]],
    courses: list[CourseSpec],
    cfg: GeneratorConfig,
) -> dict[int, int]:
    course_map = {course.course_id: course for course in courses}
    anchor_lookup: dict[int, int] = {}
    for cluster_id, course_ids in course_clusters.items():
        building_counter = Counter(course_map[course_id].home_building_id for course_id in course_ids)
        if building_counter:
            anchor_lookup[cluster_id] = building_counter.most_common(1)[0][0]
        else:
            anchor_lookup[cluster_id] = (cluster_id % cfg.num_buildings) + 1
    return anchor_lookup


def _rank_clusters_for_dorm(
    dorm_id: int,
    cluster_anchor_building: dict[int, int],
    dist_dorm: dict[str, dict[str, float]],
) -> list[int]:
    return sorted(
        cluster_anchor_building,
        key=lambda cluster_id: (
            dist_dorm[str(dorm_id)][str(cluster_anchor_building[cluster_id])],
            cluster_id,
        ),
    )


def _choose_cluster_for_group(cluster_order: list[int], rng: random.Random) -> int:
    weighted_clusters = [(1.0 / (index + 1), cluster_id) for index, cluster_id in enumerate(cluster_order)]
    return _weighted_choice(weighted_clusters, rng)


def _distribute_courses(
    total_courses: int,
    slot_count: int,
    max_per_slot: int,
    rng: random.Random,
) -> list[int]:
    if total_courses < slot_count:
        raise InfeasibleError("num_courses must be at least the number of active time slots")
    counts = [1 for _ in range(slot_count)]
    remaining = total_courses - slot_count
    while remaining > 0:
        expandable = [index for index, count in enumerate(counts) if count < max_per_slot]
        if not expandable:
            raise InfeasibleError("slot capacity is insufficient for requested course count")
        index = rng.choice(expandable)
        counts[index] += 1
        remaining -= 1
    rng.shuffle(counts)
    return counts


def _allocate_capacity_levels(total: int, ratio_map: dict[int, float]) -> list[int]:
    raw_counts = {capacity: total * ratio for capacity, ratio in ratio_map.items()}
    counts = {capacity: int(math.floor(value)) for capacity, value in raw_counts.items()}
    remainder = total - sum(counts.values())
    ranked = sorted(
        ratio_map,
        key=lambda capacity: (raw_counts[capacity] - counts[capacity], capacity),
        reverse=True,
    )
    for index in range(remainder):
        counts[ranked[index % len(ranked)]] += 1
    if total >= 3:
        for capacity in (50, 100, 200):
            if counts[capacity] == 0:
                donor = max(counts, key=lambda item: counts[item])
                counts[donor] -= 1
                counts[capacity] += 1
    capacities: list[int] = []
    for capacity in (50, 100, 200):
        capacities.extend([capacity] * counts[capacity])
    random.Random(total + int(sum(capacities))).shuffle(capacities)
    return capacities


def _choose_target_capacity(room_capacity: int, cfg: GeneratorConfig, rng: random.Random) -> int:
    allowed = {capacity: ratio for capacity, ratio in cfg.course_ratio_map.items() if capacity <= room_capacity}
    choices = list(allowed.items())
    total_weight = sum(weight for _capacity, weight in choices)
    pick = rng.random() * total_weight
    cumulative = 0.0
    for capacity, weight in choices:
        cumulative += weight
        if cumulative >= pick:
            return capacity
    return choices[-1][0]


def _choose_preferred_buildings(
    buildings: list[BuildingSpec],
    home_building_id: int,
    count_range: tuple[int, int],
    rng: random.Random,
) -> list[int]:
    home_building = next(building for building in buildings if building.building_id == home_building_id)
    ordered = sorted(
        buildings,
        key=lambda building: (_distance(home_building.pos, building.pos), building.building_id),
    )
    desired_count = min(len(ordered), rng.randint(*count_range))
    return [building.building_id for building in ordered[:desired_count]]


def _preferred_distance(
    course: CourseSpec,
    dorm_id: int,
    dist_dorm: dict[str, dict[str, float]],
) -> float:
    distances = [
        dist_dorm[str(dorm_id)][str(building_id)]
        for building_id in course.preferred_buildings
        if str(building_id) in dist_dorm[str(dorm_id)]
    ]
    if not distances:
        return float("inf")
    return min(distances)


def _generate_positions(
    count: int,
    campus_size: float,
    min_distance: float,
    rng: random.Random,
    forbidden: list[tuple[float, float]] | None = None,
    forbidden_min_distance: float = 0.0,
) -> list[tuple[float, float]]:
    forbidden = forbidden or []
    positions: list[tuple[float, float]] = []
    for _ in range(count):
        for _attempt in range(500):
            candidate = (rng.uniform(0, campus_size), rng.uniform(0, campus_size))
            if any(_distance(candidate, point) < min_distance for point in positions):
                continue
            if any(_distance(candidate, point) < forbidden_min_distance for point in forbidden):
                continue
            positions.append(candidate)
            break
        else:
            raise InfeasibleError("unable to place all campus nodes under current distance constraints")
    return positions


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.dist(left, right)


def _all_rooms(buildings: list[BuildingSpec]) -> list[RoomSpec]:
    rooms: list[RoomSpec] = []
    for building in buildings:
        rooms.extend(building.rooms)
    return rooms


def _weighted_choice(weighted_items: list[tuple[float, object]], rng: random.Random):
    positive_items = [(max(weight, 0.0001), item) for weight, item in weighted_items]
    total_weight = sum(weight for weight, _item in positive_items)
    threshold = rng.random() * total_weight
    cumulative = 0.0
    for weight, item in positive_items:
        cumulative += weight
        if cumulative >= threshold:
            return item
    return positive_items[-1][1]


def _validate_generation_feasibility(cfg: GeneratorConfig) -> None:
    min_rooms = cfg.num_buildings * cfg.rooms_per_building_range[0]
    max_courses_per_group = cfg.group_course_count_range[1]
    if cfg.num_courses > cfg.total_time_slots * min_rooms:
        raise InfeasibleError(
            "requested num_courses exceeds the minimum guaranteed room-time capacity implied by the config"
        )
    if cfg.num_groups * max_courses_per_group < cfg.num_courses:
        raise InfeasibleError(
            "requested num_courses exceeds the maximum course selections available across all groups"
        )
