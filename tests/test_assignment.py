import unittest

from backend.assignment import CapacityOption, solve_capacity_assignment


class CapacityAssignmentTest(unittest.TestCase):
    def test_respects_hard_resource_capacities(self) -> None:
        options = [
            [
                CapacityOption(resource_id=0, utility=1.0),
                CapacityOption(resource_id=index + 1, utility=0.9),
            ]
            for index in range(6)
        ]
        capacities = {0: 2, **{index: 1 for index in range(1, 7)}}

        result = solve_capacity_assignment(options, capacities, reuse_penalty=0.1)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertLessEqual(result.count(0), 2)
        self.assertEqual(len(result), 6)

    def test_marginal_reuse_cost_prefers_diverse_equal_quality_resources(self) -> None:
        options = [
            [
                CapacityOption(resource_id=0, utility=0.8),
                CapacityOption(resource_id=1, utility=0.8),
                CapacityOption(resource_id=2, utility=0.8),
            ]
            for _ in range(3)
        ]

        result = solve_capacity_assignment(
            options,
            {0: 3, 1: 3, 2: 3},
            reuse_penalty=0.1,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(len(set(result)), 3)

    def test_returns_none_when_candidate_graph_is_infeasible(self) -> None:
        result = solve_capacity_assignment(
            [
                [CapacityOption(resource_id=0, utility=1.0)],
                [CapacityOption(resource_id=0, utility=0.9)],
            ],
            {0: 1},
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
