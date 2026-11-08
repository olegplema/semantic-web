import unittest
from rdflib.namespace import SKOS, OWL
from semantic_lab.core import EX, WD, LOCAL, EXTERNAL, load_dataset, check_dataset, execute_query
from semantic_lab.visualize import render
from tempfile import TemporaryDirectory
from pathlib import Path

class SemanticLabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset()

    def test_dataset_validation(self):
        self.assertEqual(check_dataset(self.ds), [])

    def test_exact_match_links(self):
        g = self.ds.graph(LOCAL)
        self.assertIn((EX.Java, SKOS.exactMatch, WD.Q251), g)
        self.assertIn((EX.Python, SKOS.exactMatch, WD.Q28865), g)
        self.assertNotIn((EX.backendApi, OWL.sameAs, WD.Q251), g)

    def test_graphs_are_separate(self):
        self.assertGreater(len(self.ds.graph(LOCAL)), 0)
        self.assertGreater(len(self.ds.graph(EXTERNAL)), 0)
        self.assertNotIn((EX.Java, SKOS.exactMatch, WD.Q251), self.ds.graph(EXTERNAL))

    def test_sparql_queries(self):
        self.assertEqual(len(list(execute_query(self.ds, "01_projects.rq"))), 4)
        joined = list(execute_query(self.ds, "02_federated_local_join.rq"))
        self.assertEqual(len(joined), 4)
        self.assertEqual({str(r.externalName) for r in joined}, {"Java", "Python"})
        self.assertEqual(len(list(execute_query(self.ds, "03_language_usage.rq"))), 2)
        self.assertEqual(len(list(execute_query(self.ds, "04_external_entities.rq"))), 2)

    def test_visualization_output(self):
        with TemporaryDirectory() as tmp:
            dot, svg, html = render(Path(tmp))
            self.assertTrue(dot.exists())
            self.assertTrue(html.exists())
            self.assertIn("skos:exactMatch", dot.read_text(encoding="utf-8"))
            self.assertIn("RDF-трійки", html.read_text(encoding="utf-8"))
            if svg:
                self.assertTrue(svg.exists())

if __name__ == "__main__":
    unittest.main()
