from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import (
    ROOT, build_graph, communities, evaluate_search, extract_facts, graph_rag_answer,
    graph_stats, keyword_search, load_chunks, vector_rag_answer,
    vector_search, visualize,
)
from .llm import DEFAULT_CACHE_DIR, HTTPModel


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    chunks = load_chunks(max_chars=args.chunk_size)
    questions = json.loads((ROOT / "data" / "questions.json").read_text(encoding="utf-8"))
    question = questions["global"][0]
    output = Path(args.output).resolve()
    llm = HTTPModel(args.provider, args.model, cache_dir=args.cache_dir, max_attempts=args.max_attempts)
    facts = extract_facts(chunks, llm)
    origin = f"extracted using {args.provider}/{llm.model}"
    graph = build_graph(facts)
    partitions = communities(graph)
    stats = graph_stats(graph, partitions)
    write_json(output / "facts.json", facts)
    write_json(output / "communities.json", partitions)
    visualize(graph, partitions, output / "knowledge_graph.png")
    graph_data = {
        "nodes": [{"id": node, "community": partitions[node]} for node in graph.nodes],
        "edges": [{"source": source, "target": target, **props} for source, target, props in graph.edges(data=True)],
    }
    write_json(output / "knowledge_graph.json", graph_data)
    search = evaluate_search(chunks, questions["local"] + questions["global"], args.top_k, args.search)
    write_json(output / "retrieval_comparison.json", search)
    graph_result = graph_rag_answer(graph, partitions, question, llm)
    vector_result = vector_rag_answer(chunks, question, llm, args.top_k, args.search)
    evidence = {
        "graph_documents": sorted({doc for group in graph_result["summaries"] for doc in group["documents"]}),
        "vector_documents": sorted({item["chunk_id"].split(":")[0] for item in vector_result["chunks"]}),
        "note": "Покриття джерел не є автоматичною оцінкою фактичної правильності чи якості синтезу.",
    }
    final = {
        "provider": args.provider,
        "model": llm.model,
        "facts_origin": origin,
        "vector_method": args.search,
        "global_question": question,
        "stats": stats,
        "graph_rag": graph_result,
        "vector_rag": vector_result,
        "comparison": evidence,
    }
    write_json(output / "answers.json", final)
    lines = [
        "# Порівняння GraphRAG та vector-RAG",
        "",
        f"Режим: `{args.provider}`; факти: `{origin}`; векторний метод: `{args.search}`.",
        "",
        f"**Питання:** {question}",
        "",
        "## GraphRAG",
        graph_result["answer"],
        "",
        "## Vector-RAG",
        vector_result["answer"],
        "",
        "## Перевірювані відмінності",
        f"Джерела GraphRAG: {', '.join(evidence['graph_documents']) or 'немає'}.",
        f"Джерела vector-RAG: {', '.join(evidence['vector_documents']) or 'немає'}.",
        "",
        evidence["note"],
        "",
    ]
    (output / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Документів: {len(set(chunk.doc for chunk in chunks))}; чанків: {len(chunks)}")
    print(f"Фактів: {stats['facts']}; сутностей: {stats['entities']}; зв'язків: {stats['edges']}; спільнот: {stats['communities']}")
    print(f"Факти: {origin}; LLM: {args.provider}; вектори: {args.search}")
    print(f"GraphRAG джерела: {len(evidence['graph_documents'])}; vector-RAG джерела: {len(evidence['vector_documents'])}")
    print(f"Граф: {output / 'knowledge_graph.png'}")
    print(f"Відповіді: {output / 'comparison.md'}")
    print(f"Пошук: {output / 'retrieval_comparison.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge graph, community summaries, GraphRAG vs vector-RAG")
    parser.add_argument("command", choices=["run", "search"], help="run: повний пайплайн з LLM, search: порівняння retrieval")
    parser.add_argument("--provider", choices=["ollama", "gemini"], default=None)
    parser.add_argument("--model", help="Назва моделі (типово: gemma3:4b для Ollama, gemini-3.5-flash-lite для Gemini)")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="Каталог постійного кешу успішних LLM-відповідей")
    parser.add_argument("--max-attempts", type=int, default=5, help="Максимальна кількість спроб одного API-запиту, включно з першою (типово: 5)")
    parser.add_argument("--search", choices=["tfidf", "semantic"], default="tfidf")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--chunk-size", type=int, default=1100)
    parser.add_argument("--output", default=str(ROOT / "output"))
    args = parser.parse_args()
    if args.top_k < 1 or args.chunk_size < 100:
        parser.error("--top-k має бути >= 1, --chunk-size має бути >= 100")
    if args.max_attempts < 1:
        parser.error("--max-attempts має бути >= 1")
    if args.provider is None:
        args.provider = "ollama"
    if args.command == "search":
        chunks = load_chunks(max_chars=args.chunk_size)
        questions = json.loads((ROOT / "data" / "questions.json").read_text(encoding="utf-8"))
        rows = evaluate_search(chunks, questions["local"] + questions["global"], args.top_k, args.search)
        for row in rows:
            print(f"\n{row['method']}: {row['question']}")
            for result in row["results"]:
                print(f"  {result['chunk']}: {result['score']:.4f}")
    else:
        try:
            run(args)
        except (RuntimeError, ValueError, OSError) as error:
            parser.exit(1, f"Помилка: {error}\nУспішні LLM-запити збережено в кеші {args.cache_dir}. "
                        "Повторіть ту саму команду після усунення причини збою.\n")


if __name__ == "__main__":
    main()
