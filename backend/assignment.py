from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass


_COST_SCALE = 100_000


@dataclass(frozen=True)
class CapacityOption:
    """One feasible item-to-resource assignment with a larger-is-better utility."""

    resource_id: int
    utility: float


@dataclass
class _Edge:
    to: int
    reverse: int
    capacity: int
    cost: int


def solve_capacity_assignment(
    options_by_item: Sequence[Sequence[CapacityOption]],
    resource_capacities: dict[int, int],
    *,
    reuse_penalty: float = 0.08,
) -> list[int] | None:
    """Return the minimum-cost complete assignment, or ``None`` when infeasible.

    The network is source -> item -> resource -> sink. Each item supplies one unit,
    while resource-to-sink slot edges encode both a hard capacity and an increasing
    marginal reuse cost. This is the standard capacitated assignment reduction to
    minimum-cost flow.
    """
    if not options_by_item:
        return []
    normalized: list[list[CapacityOption]] = []
    resource_ids: set[int] = set()
    for raw_options in options_by_item:
        best_by_resource: dict[int, CapacityOption] = {}
        for option in raw_options:
            if resource_capacities.get(option.resource_id, 0) <= 0:
                continue
            previous = best_by_resource.get(option.resource_id)
            if previous is None or option.utility > previous.utility:
                best_by_resource[option.resource_id] = option
        options = sorted(best_by_resource.values(), key=lambda item: item.resource_id)
        if not options:
            return None
        normalized.append(options)
        resource_ids.update(option.resource_id for option in options)

    ordered_resources = sorted(resource_ids)
    item_count = len(normalized)
    source = 0
    item_offset = 1
    resource_offset = item_offset + item_count
    resource_node = {
        resource_id: resource_offset + index
        for index, resource_id in enumerate(ordered_resources)
    }
    sink = resource_offset + len(ordered_resources)
    graph: list[list[_Edge]] = [[] for _ in range(sink + 1)]

    def add_edge(start: int, end: int, capacity: int, cost: int) -> int:
        forward_index = len(graph[start])
        reverse_index = len(graph[end])
        graph[start].append(_Edge(end, reverse_index, capacity, cost))
        graph[end].append(_Edge(start, forward_index, 0, -cost))
        return forward_index

    assignment_edges: list[list[tuple[int, int]]] = [[] for _ in range(item_count)]
    for item_index, options in enumerate(normalized):
        item_node = item_offset + item_index
        add_edge(source, item_node, 1, 0)
        for option in options:
            # Negative utility is safe because shortest paths use SPFA over the
            # residual graph. A tiny resource-id term makes ties deterministic.
            cost = -round(option.utility * _COST_SCALE) + option.resource_id
            edge_index = add_edge(
                item_node,
                resource_node[option.resource_id],
                1,
                cost,
            )
            assignment_edges[item_index].append((option.resource_id, edge_index))

    for resource_id in ordered_resources:
        capacity = max(0, resource_capacities.get(resource_id, 0))
        for slot in range(capacity):
            add_edge(
                resource_node[resource_id],
                sink,
                1,
                round(slot * reuse_penalty * _COST_SCALE),
            )

    for _ in range(item_count):
        distance = [10**30] * len(graph)
        parent_node = [-1] * len(graph)
        parent_edge = [-1] * len(graph)
        in_queue = [False] * len(graph)
        distance[source] = 0
        queue: deque[int] = deque([source])
        in_queue[source] = True
        while queue:
            node = queue.popleft()
            in_queue[node] = False
            for edge_index, edge in enumerate(graph[node]):
                if edge.capacity <= 0:
                    continue
                candidate_distance = distance[node] + edge.cost
                if candidate_distance >= distance[edge.to]:
                    continue
                distance[edge.to] = candidate_distance
                parent_node[edge.to] = node
                parent_edge[edge.to] = edge_index
                if not in_queue[edge.to]:
                    queue.append(edge.to)
                    in_queue[edge.to] = True
        if parent_node[sink] < 0:
            return None
        node = sink
        while node != source:
            previous = parent_node[node]
            edge_index = parent_edge[node]
            edge = graph[previous][edge_index]
            edge.capacity -= 1
            graph[node][edge.reverse].capacity += 1
            node = previous

    result: list[int] = []
    for item_index, edges in enumerate(assignment_edges):
        item_node = item_offset + item_index
        selected = next(
            (
                resource_id
                for resource_id, edge_index in edges
                if graph[item_node][edge_index].capacity == 0
            ),
            None,
        )
        if selected is None:
            return None
        result.append(selected)
    return result
