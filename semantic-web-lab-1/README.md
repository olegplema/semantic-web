# Семантична павутина — RDF, SPARQL і зв’язування з Wikidata

Готова лабораторна робота (код) на Python. Предметна область: **навчальні програмні проєкти та мови програмування**. Локальні ресурси `ex:Java`, `ex:Python` через `skos:exactMatch` зв’язані з відповідними сутностями Wikidata. Два **окремі іменовані графи** запитуються разом у SPARQL через `GRAPH`, що працює **без інтернету** завдяки маленькому знімку зовнішніх фактів.

## 1. Запуск (Linux/macOS)

Потрібні Python 3.10+ та pip. Graphviz необов’язковий (потрібен лише для SVG-картинки).

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
PYTHONPATH=src python -m semantic_lab demo
```

**Windows PowerShell:**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH="src"
python -m semantic_lab demo
```

Після `demo` в `output/` буде `graph.html` (відкрийте у браузері), `graph.dot` і, якщо є Graphviz, `graph.svg`.

Для SVG у Linux Ubuntu/Mint: `sudo apt install graphviz`. У macOS: `brew install graphviz`. У Windows можна встановити Graphviz із https://graphviz.org/download/ і додати `dot` у PATH. **Відсутність Graphviz не заважає SPARQL та тестам.**

## 2. Перевірити

Linux/macOS:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m semantic_lab validate
PYTHONPATH=src python -m semantic_lab query --name 02_federated_local_join.rq
PYTHONPATH=src python -m semantic_lab bad-example
PYTHONPATH=src python -m semantic_lab triples
PYTHONPATH=src python -m semantic_lab visualize
```

Windows: використовуйте ті самі команди без `PYTHONPATH=src` після того, як задали `$env:PYTHONPATH="src"`.

Очікування: 5 тестів `OK`; у запиті `02_federated_local_join.rq` — **4 рядки** (Backend API/Java, Data Analyzer/Python, Automation Service/Java та Automation Service/Python).

## 3. Структура

```text
semantic-web-lab/
├── data/
│   ├── local.ttl               # Власний RDF-граф
│   ├── wikidata_snapshot.ttl    # Частина зовнішнього RDF-графа Wikidata
│   └── incorrect.ttl           # Навмисно неправильний зв’язок
├── queries/
│   ├── 01_projects.rq          # Власний граф
│   ├── 02_federated_local_join.rq # JOIN власного + зовнішнього графа
│   ├── 03_language_usage.rq    # Агрегація COUNT
│   └── 04_external_entities.rq # Відповідність локальних URI та Wikidata
├── src/semantic_lab/
│   ├── __main__.py             # Консольна програма
│   ├── core.py                 # RDF Dataset, перевірка та виконання SPARQL
│   └── visualize.py            # DOT, SVG, HTML
├── tests/                      # Автоматичні тести
├── docs/                       # Нотатки для захисту, джерела
├── .github/workflows/tests.yml # GitHub Actions
├── requirements.txt
└── README.md
```

## 4. Семантичне зв’язування

| Власний URI | Зовнішній URI | RDF-предикат |
|---|---|---|
| `https://example.org/semantic-lab/Java` | `http://www.wikidata.org/entity/Q251` | `skos:exactMatch` |
| `https://example.org/semantic-lab/Python` | `http://www.wikidata.org/entity/Q28865` | `skos:exactMatch` |

`skos:exactMatch` застосований між **двома ресурсами, що позначають ту саму концепцію мови програмування** в різних словниках/графах. На відміну від `owl:sameAs`, це не настільки сильне онтологічне твердження про абсолютну тотожність індивідів. `ex:usesLanguage` пов’язує проєкт і мову всередині локального графа.

Навмисно помилкове твердження: `ex:backendApi owl:sameAs wd:Q251` — проєкт *не є* мовою Java. Виправлення: `ex:backendApi ex:usesLanguage ex:Java` та `ex:Java skos:exactMatch wd:Q251`. Дивіться `data/incorrect.ttl`.

## 5. Демонстрація зв’язування двох графів

Запит `queries/02_federated_local_join.rq` використовує `GRAPH ex:local` для відомостей про проєкт і мову, а `GRAPH ex:external` — для назви мови та факту `wdt:P31 wd:Q9143` у фрагменті Wikidata. З'єднання виконується за **спільним URI** `?wikidataEntity`, отриманим через `skos:exactMatch`.

Це **локальний JOIN двох іменованих RDF-графів**, а не мережевий SPARQL `SERVICE`-запит. Файли не потребують живого мережевого доступу, тому демонстрація і тести відтворювані. Для поточних даних з Wikidata потрібно окремо звертатися до її SPARQL endpoint.

## 6. GitHub

Спершу створіть порожній репозиторій (без автоматичного README), наприклад `semantic-web-lab`, потім із цієї папки:

```bash
git init
git add .
git commit -m "Implement RDF graph, Wikidata linking and SPARQL demo"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/semantic-web-lab.git
git push -u origin main
```

Замініть `YOUR_USERNAME` на свій GitHub username. Посилання на репозиторій можна буде додати до звіту. У `.gitignore` виключено віртуальне середовище та згенеровані файли `output/`.

## 7. Використання AI

У фінальному звіті зазначте фактичне застосування AI, наприклад: **AI використовувався для підготовки початкової структури проєкту, прикладів RDF/Turtle, SPARQL-запитів, коду візуалізації, тестів і README; автор перевірив виконання та має пояснити семантику графа.**

## Джерела

- https://www.wikidata.org/wiki/Q251 — Java
- https://www.wikidata.org/wiki/Q28865 — Python
- https://www.wikidata.org/wiki/Q9143 — programming language
- https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service — Wikidata SPARQL
- https://www.w3.org/TR/skos-reference/ — SKOS
- https://www.w3.org/TR/sparql11-query/ — SPARQL

Локальні URI `example.org` є **демонстраційними**, вони не опубліковані в інтернеті. У власному публічному проєкті їх слід замінити на URI контрольованого вами домену.

Готові файли для перегляду без запуску програми: `docs/graph.html` і `docs/graph.svg` (збережені у Git). Після запуску `demo` вони також генеруються в `output/`.
