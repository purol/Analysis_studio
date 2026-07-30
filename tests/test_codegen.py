import unittest

from belleflow.codegen import generate_loader_cpp
from belleflow.model import Graph
from belleflow.registry import NODE_SPECS


class CodeGenerationTests(unittest.TestCase):
    def test_loader_cpp_generation(self):
        graph = Graph(id="main", name="Main", scope="loader")
        load = graph.add_node(NODE_SPECS["load"], 0, 0)
        cut = graph.add_node(NODE_SPECS["cut"], 100, 0)
        cut.properties["condition"] = '(M > 1.5) && (name == "signal")'
        graph.add_edge(load.id, "out", cut.id, "in")

        output = generate_loader_cpp(graph)
        self.assertIn('loader.Load(argv[1], argv[2], "label");', output)
        self.assertIn(
            'loader.Cut("(M > 1.5) && (name == \\"signal\\")");',
            output,
        )
        self.assertIn("loader.end();", output)


if __name__ == "__main__":
    unittest.main()
