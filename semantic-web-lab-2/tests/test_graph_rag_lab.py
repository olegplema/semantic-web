import json
import tempfile
import unittest
from pathlib import Path

from graph_rag_lab.core import (
    build_graph, communities, community_context, extract_facts, graph_rag_answer,
    graph_stats, keyword_search, load_chunks, load_fixture_facts, parse_json_array,
    vector_rag_answer, vector_search, visualize,
)


class TestGraphRagLab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = load_chunks()
        cls.facts = load_fixture_facts(cls.chunks)
        cls.graph = build_graph(cls.facts)
        cls.groups = communities(cls.graph)

    def test_documents_and_provenance(self):
        self.assertEqual(len({item.doc for item in self.chunks}), 6)
        self.assertEqual(len(self.facts), 21)
        self.assertTrue(all(fact["doc"] in {chunk.doc for chunk in self.chunks} for fact in self.facts))

    def test_graph_and_communities(self):
        stats = graph_stats(self.graph, self.groups)
        self.assertEqual(stats["facts"], 21)
        self.assertGreaterEqual(stats["communities"], 2)
        self.assertEqual(set(self.groups), set(self.graph.nodes))
        self.assertTrue(community_context(self.graph, self.groups))
        self.assertEqual(communities(self.graph), self.groups)

    def test_retrieval(self):
        query = "Як використовувати correlation ID та distributed tracing?"
        for retrieved in (keyword_search(self.chunks, query), vector_search(self.chunks, query)):
            self.assertEqual(len(retrieved), 3)
            self.assertTrue(all(score >= 0 for _, score in retrieved))
            self.assertEqual(retrieved, sorted(retrieved, key=lambda p: (-p[1], p[0].id)))

    def test_map_reduce_and_vector_paths(self):
        class StubLLM:
            def generate(self, prompt):
                return "Тестова відповідь моделі"
        model = StubLLM()
        question = "Як забезпечити надійність подієвої архітектури?"
        graph_result = graph_rag_answer(self.graph, self.groups, question, model)
        vector_result = vector_rag_answer(self.chunks, question, model)
        self.assertEqual(len(graph_result["summaries"]), len(set(self.groups.values())))
        self.assertEqual(graph_result["answer"], "Тестова відповідь моделі")
        self.assertEqual(vector_result["answer"], "Тестова відповідь моделі")
        self.assertEqual(len(vector_result["chunks"]), 3)

    def test_json_parsing(self):
        result = parse_json_array('```json\n[{"subject":"A","predicate":"uses","object":"B"}]\n```')
        self.assertEqual(result[0]["subject"], "A")
        with self.assertRaises(ValueError):
            parse_json_array("not JSON")

    def test_extraction(self):
        class FakeLLM:
            def generate(self, prompt):
                return json.dumps([{"subject": "A", "predicate": "uses", "object": "B"}])
        results = extract_facts(self.chunks[:1], FakeLLM())
        self.assertEqual(results[0]["doc"], self.chunks[0].doc)

    def test_png_export(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "graph.png"
            visualize(self.graph, self.groups, target)
            self.assertGreater(target.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
