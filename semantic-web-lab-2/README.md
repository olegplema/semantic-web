# Semantic Web — Laboratory 2: GraphRAG vs vector-RAG

Самостійний Python-проєкт для побудови графа знань із текстових документів, виявлення спільнот методом Louvain та порівняння GraphRAG із векторним RAG. Мова документів і запитань — українська. Предметна область — надійність подієвої мікросервісної системи.

## Вміст

- `data/documents/` — шість авторських вихідних документів (по одній темі в кожному);
- `data/questions.json` — три локальних і одне глобальне запитання;
- `data/fixtures/facts.json` — 21 вручну перевірений приклад факту для unit-тестів;
- `src/graph_rag_lab/core.py` — чанкінг, видобування фактів, пошук, граф, Louvain, GraphRAG і vector-RAG;
- `src/graph_rag_lab/llm.py` — підтримка Ollama та Gemini API;
- `src/graph_rag_lab/__main__.py` — CLI та експорт результатів;
- `tests/` — автоматичні тести;
- `docs/DEFENSE_NOTES.md` — технічні пояснення для демонстрації.

## Запуск (Linux Mint / Ubuntu)

Запускайте команди з папки `semantic-web-lab-2`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Повний запуск з LLM

Основний пайплайн вимагає Ollama або Gemini: витягує факти, будує граф, обчислює спільноти, формує резюме та дві відповіді.

Результати в папці `output/`:

| Файл | Призначення |
| --- | --- |
| `knowledge_graph.png` | Візуалізація мережі сутностей із кольорами спільнот |
| `knowledge_graph.json` | Вузли, ребра, зв'язки й походження фактів |
| `communities.json` | Належність кожної сутності до спільноти Louvain |
| `facts.json` | Факти з прив'язкою до документів |
| `retrieval_comparison.json` | Топ-N знайдених чанків: keyword vs vector |
| `answers.json` | Питання, резюме спільнот, відповіді та джерела |
| `comparison.md` | Читабельне порівняння GraphRAG і vector-RAG |

Відкрити зображення:

```bash
xdg-open output/knowledge_graph.png
```

### Ollama (локально)

Потрібні встановлена Ollama, запущений Ollama-сервер та завантажена модель. Приклад:

```bash
ollama pull gemma3:4b
PYTHONPATH=src python -m graph_rag_lab run --provider ollama --model gemma3:4b
```

У цьому режимі **та сама LLM** видобуває факти з кожного чанка, створює резюме кожної спільноти, синтезує відповідь GraphRAG і генерує відповідь vector-RAG. Для Ollama за замовчуванням використовується `http://localhost:11434` (можна змінити змінною `OLLAMA_BASE_URL`). Модель і швидкість залежать від потужності комп'ютера. Перевірте, що сервер запущено (`ollama list`).

### Gemini API

```bash
read -rsp 'Gemini API key: ' GEMINI_API_KEY; echo; export GEMINI_API_KEY
PYTHONPATH=src python -m graph_rag_lab run --provider gemini --model gemini-2.5-flash
```

Потрібні доступ до API, відповідний ключ та інтернет. Ключ не зберігайте у репозиторії. Доступність конкретної моделі та квоти залежать від вашого облікового запису.

## Retrieval: keyword vs vector

```bash
PYTHONPATH=src python -m graph_rag_lab search
```

- Keyword baseline: кількість спільних токенів між запитанням і чанком.
- TF-IDF vector baseline: `TfidfVectorizer` (unigrams + bigrams), cosine similarity. Працює без завантаження моделей.
- Dense semantic retrieval (необов'язково): multilingual SentenceTransformer. Потрібно встановити додаткові залежності та завантажити модель під час першого запуску:

```bash
python -m pip install -r requirements-semantic.txt
PYTHONPATH=src python -m graph_rag_lab search --search semantic
PYTHONPATH=src python -m graph_rag_lab run --provider ollama --search semantic
```

Параметри `--top-k 3` та `--chunk-size 1100` змінюють число фрагментів і довжину чанків. Поділ відбувається за межами речень, зі збереженням ID `document:chunk_index`.

## Архітектура

```text
*.txt -> chunks -> LLM: JSON facts -> networkx.DiGraph
                                  -> Louvain (undirected projection)
                                  -> LLM: community summaries
                                  -> LLM: global synthesis -> GraphRAG answer

*.txt -> chunks -> TF-IDF / dense embeddings -> top-N -> LLM -> vector-RAG answer
                -> keyword overlap -> top-N (baseline comparison)
```

Кожен факт має `doc`, `subject`, `predicate`, `object`. Для Louvain використовується `community_louvain.best_partition()` над неорієнтованим представленням графа (сам граф лишається спрямованим). У повторних ребрах зберігаються усі предикати та джерела.

Зовнішня LLM може повернути неточні або неуніфіковані факти. Формат і наявність документа перевіряються програмно, але достовірність кожного факту слід звіряти з текстами. `data/fixtures/facts.json` використовується для тестування графа; основний запуск завжди отримує факти від LLM.

## Порівняння відповідей

`output/comparison.md` містить дві відповіді на однакове глобальне питання. `answers.json` також містить проміжні резюме кожної спільноти, ідентифікатори фрагментів і перелік джерел. Зіставте:

1. Чи згадано ідемпотентність, outbox, retries/DLQ, моніторинг, трасування й сумісність схем?
2. Чи пояснено зв'язки між практиками з різних документів?
3. Чи підтверджуються твердження джерелами? Чи є непідтверджені узагальнення?

Автоматичне порівняння покриття документів — **не** оцінка якості або фактологічної точності. Для звіту оцініть зміст відповідей вручну після реального запуску моделі.

## Перевірка

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Звіт у проєкт не включено. У звіті за умовами завдання треба окремо описати фактичну помилку, яку ви зустріли під час виконання, та її виправлення, а також зазначити, на яких етапах застосовано AI.

## GitHub: безпечна публікація в спільний репозиторій

Розпакуйте архів **у корені** Git-репозиторію `semantic-web`, поруч із `semantic-web-lab-1`. У ZIP присутня папка `.github/workflows` у корені — вона запускає тести другої лабораторної на GitHub Actions. Папка `output/`, `.venv/`, кеші та справжні секрети у ZIP відсутні.

Перед комітом виконайте з кореня репозиторію:

```bash
git add semantic-web-lab-2/ .github/workflows/semantic-web-lab-2.yml
git diff --cached --name-only
git diff --cached --check
git diff --cached --unified=0 | grep -Ein 'AIza[[:alnum:]_-]{20,}|BEGIN .*PRIVATE KEY' && echo 'STOP: перевірте секрети' || true
```

Якщо серед staged-файлів немає секретів, виконайте:

```bash
git commit -m "feat: implement GraphRAG and vector retrieval lab"
git push origin main
```

Для Gemini рекомендовано встановлювати ключ тимчасово в поточній сесії через `read -rsp 'Gemini API key: ' GEMINI_API_KEY; echo; export GEMINI_API_KEY` — це не записує ключ у shell history чи Git. Не комітьте `.env` із ключем. `.env.example` містить лише порожнє поле.
