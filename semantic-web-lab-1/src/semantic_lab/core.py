"""Навантаження і перевірка RDF Dataset з двома іменованими графами."""
from pathlib import Path
from rdflib import Dataset, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, SKOS, OWL

ROOT = Path(__file__).resolve().parents[2]
EX = Namespace("https://example.org/semantic-lab/")
WD = Namespace("http://www.wikidata.org/entity/")
LOCAL = URIRef(EX.local)
EXTERNAL = URIRef(EX.external)


def load_dataset() -> Dataset:
    dataset = Dataset()
    dataset.bind("ex", EX)
    dataset.bind("wd", WD)
    dataset.bind("skos", SKOS)
    dataset.graph(LOCAL).parse(ROOT / "data" / "local.ttl", format="turtle")
    dataset.graph(EXTERNAL).parse(ROOT / "data" / "wikidata_snapshot.ttl", format="turtle")
    return dataset


def check_dataset(dataset: Dataset) -> list[str]:
    issues = []
    local = dataset.graph(LOCAL)
    external = dataset.graph(EXTERNAL)
    projects = set(local.subjects(RDF.type, EX.SoftwareProject))
    languages = set(local.subjects(RDF.type, EX.ProgrammingLanguage))
    if not projects:
        issues.append("Немає жодного проєкту")
    if not languages:
        issues.append("Немає жодної мови")
    if (None, OWL.sameAs, None) in local:
        issues.append("owl:sameAs у локальному графі потребує перевірки; цей приклад використовує skos:exactMatch")
    for project in projects:
        if not list(local.objects(project, EX.usesLanguage)):
            issues.append(f"Проєкт {project} не має мови")
        for language in local.objects(project, EX.usesLanguage):
            if language not in languages:
                issues.append(f"{project} посилається на невідому мову {language}")
    for language in languages:
        links = list(local.objects(language, SKOS.exactMatch))
        if not links:
            issues.append(f"Для {language} відсутній skos:exactMatch")
        for entity in links:
            if not list(external.objects(entity, RDFS.label)):
                issues.append(f"Немає зовнішніх даних для {entity}")
    return issues


def execute_query(dataset: Dataset, query_name: str):
    path = ROOT / "queries" / query_name
    if not path.is_file() or path.suffix != ".rq" or path.parent != ROOT / "queries":
        raise ValueError(f"Невідомий запит: {query_name}")
    return dataset.query(path.read_text(encoding="utf-8"))


def format_results(results) -> str:
    headers = [str(v) for v in results.vars]
    rows = [[str(v) if v is not None else "—" for v in row] for row in results]
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    def line(values):
        return " | ".join(value.ljust(widths[i]) for i, value in enumerate(values))
    return "\n".join([line(headers), "-+-".join("-" * n for n in widths), *(line(r) for r in rows), f"Рядків: {len(rows)}"])
