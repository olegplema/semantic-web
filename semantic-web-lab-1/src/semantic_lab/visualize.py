"""Експорт у DOT, HTML; SVG через Graphviz (якщо встановлений)."""
import html
import shutil
import subprocess
from pathlib import Path
from rdflib import URIRef, Literal
from rdflib.namespace import RDFS, RDF, SKOS
from .core import EX, LOCAL, EXTERNAL, load_dataset, ROOT


def short(term):
    s = str(term)
    if s.startswith(str(EX)):
        return "ex:" + s[len(str(EX)):]
    if s.startswith("http://www.wikidata.org/entity/"):
        return "wd:" + s.split("/")[-1]
    if s.startswith(str(SKOS)):
        return "skos:" + s.split("#")[-1]
    if s.startswith(str(RDFS)):
        return "rdfs:" + s.split("#")[-1]
    if s.startswith(str(RDF)):
        return "rdf:" + s.split("#")[-1]
    if s.startswith("http://www.wikidata.org/prop/direct/"):
        return "wdt:" + s.split("/")[-1]
    if isinstance(term, Literal):
        return str(term)
    return s


def quote(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ') + '"'


def render(output_dir: Path):
    dataset = load_dataset()
    output_dir.mkdir(parents=True, exist_ok=True)
    # Візуалізуємо сутності і зв'язки, а не технічні трійки схемних класів.
    local = dataset.graph(LOCAL)
    external = dataset.graph(EXTERNAL)
    projects = sorted(local.subjects(RDF.type, EX.SoftwareProject), key=str)
    langs = sorted(local.subjects(RDF.type, EX.ProgrammingLanguage), key=str)
    entities = sorted({obj for lang in langs for obj in local.objects(lang, SKOS.exactMatch)}, key=str)
    lines = ['digraph RDF {', '  graph [rankdir=LR, bgcolor="white", pad="0.3"];',
             '  node [shape=box, style="rounded,filled", fontname="Arial", color="#8a96a3"];',
             '  edge [fontname="Arial", fontsize=10];']
    for node in projects:
        lines.append(f'  {quote(node)} [label={quote(short(node))}, fillcolor="#dceafe"];')
    for node in langs:
        lines.append(f'  {quote(node)} [label={quote(short(node))}, fillcolor="#d9f3e6"];')
    for node in entities:
        label = next((str(x) for x in external.objects(node, RDFS.label)), short(node))
        lines.append(f'  {quote(node)} [label={quote(short(node) + "\\n" + label)}, fillcolor="#fef1cd"];')
    for project in projects:
        for lang in local.objects(project, EX.usesLanguage):
            lines.append(f'  {quote(project)} -> {quote(lang)} [label="ex:usesLanguage"];')
    for lang in langs:
        for entity in local.objects(lang, SKOS.exactMatch):
            lines.append(f'  {quote(lang)} -> {quote(entity)} [label="skos:exactMatch", color="#6b5bb3"];')
    lines.append('}')
    dot_path = output_dir / "graph.dot"
    dot_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    svg_path = output_dir / "graph.svg"
    svg = None
    if shutil.which("dot"):
        subprocess.run(["dot", "-Tsvg", str(dot_path), "-o", str(svg_path)], check=True)
        svg = svg_path.read_text(encoding="utf-8")
    triples = []
    for graph_name, graph in (("Локальний", local), ("Wikidata (snapshot)", external)):
        for s, p, o in sorted(graph, key=lambda t: tuple(map(str, t))):
            triples.append(f"<tr><td>{html.escape(graph_name)}</td><td>{html.escape(short(s))}</td><td>{html.escape(short(p))}</td><td>{html.escape(short(o))}</td></tr>")
    graphic = svg if svg else "<p>Для SVG встановіть Graphviz і запустіть visualize повторно. Файл graph.dot уже створено.</p>"
    page = '<!doctype html><html lang="uk"><meta charset="utf-8"><title>RDF Graph</title><style>body{font:16px system-ui;max-width:1200px;margin:30px auto;padding:0 15px}svg{max-width:100%;height:auto}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #bbb;text-align:left;overflow-wrap:anywhere}tr:nth-child(even){background:#f5f6f7}</style><h1>Граф навчальних проєктів</h1><p>Синій — проєкти, зелений — локальні мови, жовтий — ресурси Wikidata. Візуалізація показує ключові зв’язки, таблиця — всі трійки.</p>' + graphic + '<h2>Усі RDF-трійки</h2><table><thead><tr><th>Граф</th><th>Суб’єкт</th><th>Предикат</th><th>Об’єкт</th></tr></thead><tbody>' + ''.join(triples) + '</tbody></table></html>'
    (output_dir / "graph.html").write_text(page, encoding="utf-8")
    return dot_path, svg_path if svg else None, output_dir / "graph.html"
