import unittest

from belleflow.model import Graph
from belleflow.registry import NODE_SPECS


class ModelTests(unittest.TestCase):
    def test_topological_order_and_cycle_rejection(self):
        graph = Graph(id="test", name="test", scope="loader")
        load = graph.add_node(NODE_SPECS["load"], 0, 0)
        cut = graph.add_node(NODE_SPECS["cut"], 100, 0)
        save = graph.add_node(NODE_SPECS["save_root"], 200, 0)

        graph.add_edge(load.id, "out", cut.id, "in")
        graph.add_edge(cut.id, "out", save.id, "in")
        self.assertEqual(
            [node.id for node in graph.topological_order()],
            [load.id, cut.id, save.id],
        )

        with self.assertRaisesRegex(ValueError, "Cycles"):
            graph.add_edge(save.id, "out", cut.id, "in")

    def test_project_graph_validation(self):
        graph = Graph(id="test", name="test", scope="loader")
        graph.add_node(NODE_SPECS["cut"], 0, 0)
        self.assertEqual(graph.validate(NODE_SPECS), [])


if __name__ == "__main__":
    unittest.main()
