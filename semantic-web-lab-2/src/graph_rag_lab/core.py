from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import community as community_louvain
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = ROOT / "data" / "documents"
FIXTURES = ROOT / "data" / "fixtures"


@dataclass(frozen=True)
class Chunk:
    id: str
    doc: str
    text: str


def load_chunks(directory: Path = DOCUMENTS, max_chars: int = 1100) -> list[Chunk]:
    chunks = []
    for file in sorted(directory.glob("*.txt")):
        sentences = re.split(r"(?<=[.!?])\s+", file.read_text(encoding="utf-8").strip())
        current, index = [], 0
        for sentence in sentences:
            if current and len(" ".join(current)) + len(sentence) + 1 > max_chars:
                chunks.append(Chunk(f"{file.stem}:{index}", file.stem, " ".join(current)))
                current, index = [], index + 1
            current.append(sentence)
        if current:
            chunks.append(Chunk(f"{file.stem}:{index}", file.stem, " ".join(current)))
    if not chunks:
        raise ValueError("У data/documents немає текстових документів")
    return chunks


def keyword_search(chunks: list[Chunk], query: str, top_k: int = 3) -> list[tuple[Chunk, float]]:
    words = lambda text: set(re.findall(r"\w+", text.lower(), flags=re.UNICODE))
    q = words(query)
    ranked = [(chunk, float(len(q & words(chunk.text)))) for chunk in chunks]
    return sorted(ranked, key=lambda item: (-item[1], item[0].id))[:top_k]


def vector_search(chunks: list[Chunk], query: str, top_k: int = 3, method: str = "tfidf") -> list[tuple[Chunk, float]]:
    texts = [chunk.text for chunk in chunks]
    if method == "tfidf":
        model = TfidfVectorizer(ngram_range=(1, 2), lowercase=True)
        matrix = model.fit_transform(texts + [query])
        scores = cosine_similarity(matrix[:-1], matrix[-1:]).ravel()
    elif method == "semantic":
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RuntimeError("Встановіть pip install -r requirements-semantic.txt") from error
        encoder = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        vectors = encoder.encode(texts + [query], normalize_embeddings=True)
        scores = cosine_similarity(vectors[:-1], vectors[-1:]).ravel()
    else:
        raise ValueError(f"Невідомий метод пошуку: {method}")
    return sorted(zip(chunks, [float(x) for x in scores]), key=lambda item: (-item[1], item[0].id))[:top_k]


def validate_facts(facts: list[dict[str, str]], chunk_docs: set[str]) -> list[dict[str, str]]:
    valid = []
    for fact in facts:
        if not all(isinstance(fact.get(key), str) and fact[key].strip() for key in ("doc", "subject", "predicate", "object")):
            raise ValueError(f"Некоректний факт: {fact}")
        if fact["doc"] not in chunk_docs:
            raise ValueError(f"Джерело факту відсутнє в корпусі: {fact['doc']}")
        valid.append({key: fact[key].strip() for key in ("doc", "subject", "predicate", "object")})
    return valid


def load_fixture_facts(chunks: list[Chunk]) -> list[dict[str, str]]:
    facts = json.loads((FIXTURES / "facts.json").read_text(encoding="utf-8"))
    return validate_facts(facts, {chunk.doc for chunk in chunks})


def extract_facts(chunks: list[Chunk], llm: Any) -> list[dict[str, str]]:
    facts = []
    for chunk in chunks:
        prompt = (
            "Виділи лише факти, явно підтримані текстом. Поверни лише JSON-масив об'єктів "
            'з полями "subject", "predicate", "object". Предикат — коротка дія або відношення. '
            "Не вигадуй фактів; назви однакових сутностей нормалізуй. Не більше 8 фактів на фрагмент.\n\n"
            f"Документ: {chunk.doc}\nТекст: {chunk.text}"
        )
        items = parse_json_array(llm.generate(prompt))
        for item in items:
            facts.append({"doc": chunk.doc, **item})
    return validate_facts(facts, {chunk.doc for chunk in chunks})


def parse_json_array(raw: str) -> list[dict[str, str]]:
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    try:
        result = json.loads(clean)
    except json.JSONDecodeError:
        start, end = clean.find("["), clean.rfind("]")
        if start < 0 or end <= start:
            raise ValueError("LLM не повернула JSON-масив") from None
        result = json.loads(clean[start : end + 1])
    if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
        raise ValueError("LLM повернула не масив об'єктів")
    return result


def build_graph(facts: list[dict[str, str]]) -> nx.DiGraph:
    graph = nx.DiGraph()
    for fact in facts:
        source, target = fact["subject"], fact["object"]
        graph.add_node(source)
        graph.add_node(target)
        if graph.has_edge(source, target):
            graph[source][target]["relations"].append({"predicate": fact["predicate"], "doc": fact["doc"]})
        else:
            graph.add_edge(source, target, relations=[{"predicate": fact["predicate"], "doc": fact["doc"]}])
    return graph


def communities(graph: nx.DiGraph, seed: int = 42) -> dict[str, int]:
    if graph.number_of_nodes() == 0:
        return {}
    undirected = nx.Graph()
    undirected.add_nodes_from(graph.nodes)
    for source, target in graph.edges:
        if source != target:
            undirected.add_edge(source, target)
    if undirected.number_of_edges() == 0:
        return {node: idx for idx, node in enumerate(sorted(graph.nodes))}
    raw = community_louvain.best_partition(undirected, random_state=seed)
    groups = defaultdict(list)
    for node, group in raw.items():
        groups[group].append(node)
    order = {group: idx for idx, (group, _) in enumerate(sorted(groups.items(), key=lambda item: min(item[1])))}
    return {node: order[group] for node, group in raw.items()}


def community_context(graph: nx.DiGraph, partitions: dict[str, int]) -> dict[int, dict[str, Any]]:
    result = defaultdict(lambda: {"entities": [], "relations": [], "documents": set()})
    for node, group in partitions.items():
        result[group]["entities"].append(node)
    for source, target, props in graph.edges(data=True):
        if partitions[source] == partitions[target]:
            group = partitions[source]
            for relation in props["relations"]:
                result[group]["relations"].append({"source": source, "predicate": relation["predicate"], "target": target, "doc": relation["doc"]})
                result[group]["documents"].add(relation["doc"])
    return {group: {"entities": sorted(data["entities"]), "relations": data["relations"], "documents": sorted(data["documents"])} for group, data in sorted(result.items())}


def graph_rag_answer(graph: nx.DiGraph, partitions: dict[str, int], question: str, llm: Any) -> dict[str, Any]:
    contexts = community_context(graph, partitions)
    summaries = []
    for group, data in contexts.items():
        prompt = (
            "Ти аналізуєш спільноту графа знань. Узагальни наведені сутності й зв'язки "
            "у 3–5 реченнях українською. Спирайся лише на факти; не приписуй даним відсутні висновки. "
            "Познач документи-джерела.\n"
            f"Спільнота {group}: {json.dumps(data, ensure_ascii=False)}"
        )
        summaries.append({"community": group, "documents": data["documents"], "summary": llm.generate(prompt)})
    final_prompt = (
        "Дай відповідь українською на глобальне питання, об'єднуючи висновки з усіх спільнот. "
        "Поясни взаємозалежності, вкажи документи-джерела, відокрем факти від висновків. "
        "Не додавай тверджень, яких немає в резюме.\n"
        f"Питання: {question}\nРезюме спільнот: {json.dumps(summaries, ensure_ascii=False)}"
    )
    return {"summaries": summaries, "answer": llm.generate(final_prompt)}


def vector_rag_answer(chunks: list[Chunk], question: str, llm: Any, top_k: int = 3, method: str = "tfidf") -> dict[str, Any]:
    ranked = vector_search(chunks, question, top_k, method)
    context = [{"chunk_id": chunk.id, "text": chunk.text, "score": round(score, 4)} for chunk, score in ranked]
    prompt = (
        "Дай відповідь українською лише за наведеними фрагментами документів. "
        "Поясни взаємозв'язки, наскільки дозволяє контекст, зазнач джерела chunk_id. "
        "Не вигадуй фактів. Якщо фактів бракує, скажи про це.\n"
        f"Питання: {question}\nФрагменти: {json.dumps(context, ensure_ascii=False)}"
    )
    return {"chunks": context, "answer": llm.generate(prompt)}


def visualize(graph: nx.DiGraph, partitions: dict[str, int], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(18, 11))
    positions = nx.spring_layout(graph, seed=42, k=1.1 / math.sqrt(max(1, graph.number_of_nodes())))
    nx.draw_networkx_nodes(graph, positions, node_size=850, node_color=[partitions[node] for node in graph.nodes], cmap=plt.get_cmap("tab20"), ax=ax)
    nx.draw_networkx_edges(graph, positions, edge_color="#888888", arrows=True, arrowsize=13, alpha=0.65, ax=ax)
    nx.draw_networkx_labels(graph, positions, font_size=7, ax=ax)
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def evaluate_search(chunks: list[Chunk], questions: list[str], top_k: int, method: str) -> list[dict[str, Any]]:
    rows = []
    for question in questions:
        for label, ranked in [("keyword", keyword_search(chunks, question, top_k)), ("vector", vector_search(chunks, question, top_k, method))]:
            rows.append({"question": question, "method": label, "results": [{"chunk": chunk.id, "score": round(score, 4)} for chunk, score in ranked]})
    return rows


def graph_stats(graph: nx.DiGraph, partitions: dict[str, int]) -> dict[str, Any]:
    docs = set()
    facts = 0
    for _, _, attrs in graph.edges(data=True):
        for relation in attrs["relations"]:
            docs.add(relation["doc"])
            facts += 1
    return {"entities": graph.number_of_nodes(), "edges": graph.number_of_edges(), "facts": facts, "communities": len(set(partitions.values())), "documents_with_facts": sorted(docs)}
