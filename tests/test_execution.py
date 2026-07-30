from pathlib import Path
import os
import shutil
import unittest

from belleflow.execution import HTCondorExporter, LocalExecutor
from belleflow.model import Project
from belleflow.registry import NODE_SPECS


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(f"/tmp/belleflow_test_{os.getpid()}")
        shutil.rmtree(self.root, ignore_errors=True)
        (self.root / "input").mkdir(parents=True)
        (self.root / "input" / "a.root").write_text("a", encoding="utf-8")
        (self.root / "input" / "b.root").write_text("b", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def make_project(self):
        project = Project.empty("execution test")
        graph = project.workflow
        source = graph.add_node(NODE_SPECS["root_files"], 0, 0)
        source.properties["directory"] = str(self.root / "input")
        stage = graph.add_node(NODE_SPECS["analysis_stage"], 100, 0)
        stage.properties.update(
            {
                "executable": "/bin/cp",
                "arguments": "{input} {output_dir}/{filename}",
                "output_dir": str(self.root / "output"),
                "run_mode": "per_file",
            }
        )
        graph.add_edge(source.id, "out", stage.id, "in")
        project.backend_options["local_workers"] = 2
        return project

    def test_local_file_fanout(self):
        project = self.make_project()
        LocalExecutor(lambda _message: None).run(project)
        self.assertEqual(
            sorted(path.name for path in (self.root / "output").glob("*.root")),
            ["a.root", "b.root"],
        )

    def test_condor_export(self):
        project = self.make_project()
        dag = HTCondorExporter(lambda _message: None).export(
            project, self.root / "condor"
        )
        self.assertTrue(dag.exists())
        self.assertEqual(len(list((self.root / "condor").glob("*.sub"))), 2)


if __name__ == "__main__":
    unittest.main()
