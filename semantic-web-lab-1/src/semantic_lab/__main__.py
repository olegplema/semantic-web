"""CLI: python -m semantic_lab demo|query|validate|visualize|triples|bad-example"""
import argparse
from pathlib import Path
from rdflib.namespace import OWL, SKOS
from .core import ROOT, EX, WD, LOCAL, EXTERNAL, load_dataset, check_dataset, execute_query, format_results
from .visualize import render


def main():
    parser = argparse.ArgumentParser(description="Лабораторна: RDF, Wikidata та SPARQL")
    parser.add_argument("action", nargs="?", default="demo", choices=["demo", "query", "validate", "visualize", "triples", "bad-example"])
    parser.add_argument("--name", help="Файл SPARQL-запиту з queries/, наприклад 02_federated_local_join.rq")
    parser.add_argument("--out", default=str(ROOT / "output"), help="Каталог для згенерованих файлів")
    args = parser.parse_args()
    dataset = load_dataset()
    local, external = dataset.graph(LOCAL), dataset.graph(EXTERNAL)
    if args.action in ("demo", "validate"):
        issues = check_dataset(dataset)
        print(f"Локальний граф: {len(local)} трійок; зовнішній граф: {len(external)} трійок")
        print("Перевірка:", "OK" if not issues else "; ".join(issues))
        if issues:
            raise SystemExit(1)
    if args.action in ("demo", "query"):
        names = [args.name] if args.name else sorted(p.name for p in (ROOT / "queries").glob("*.rq"))
        for name in names:
            print(f"\n=== {name} ===")
            print(format_results(execute_query(dataset, name)))
    if args.action in ("demo", "visualize"):
        dot, svg, html = render(Path(args.out))
        print(f"\nDOT: {dot}\nSVG: {svg or 'Graphviz не встановлений'}\nHTML: {html}")
    if args.action == "triples":
        for title, graph in (("LOCAL", local), ("EXTERNAL", external)):
            print(f"\n=== {title} ===")
            for s, p, o in sorted(graph, key=lambda t: tuple(map(str, t))):
                print(f"<{s}> <{p}> {o.n3()} .")
    if args.action == "bad-example":
        bad = ROOT / "data" / "incorrect.ttl"
        print("ПОМИЛКОВО:", bad.read_text(encoding="utf-8").strip())
        print("\nВИПРАВЛЕНО:")
        print(f"<{EX.backendApi}> <{EX.usesLanguage}> <{EX.Java}> .")
        print(f"<{EX.Java}> <{SKOS.exactMatch}> <{WD.Q251}> .")
        print("owl:sameAs не підходить: програмний проєкт не тотожний мові програмування.")


if __name__ == "__main__":
    main()
