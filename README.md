# Async Web Crawler

Учебный проект асинхронного веб-краулера на Python. Реализованы базовый
асинхронный HTTP-клиент и извлечение структурированных данных из HTML.

## Возможности Day 1

- конкурентная загрузка URL через `asyncio.gather`;
- ограничение конкурентности через `asyncio.Semaphore`;
- переиспользование одной `aiohttp.ClientSession` и пула соединений;
- отдельные таймауты подключения и чтения;
- обработка HTTP-ошибок, сетевых ошибок и таймаутов;
- логирование начала и результата каждого запроса;
- корректное закрытие HTTP-сессии;
- тесты без обращения к реальной сети.

## Установка

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
```

## Возможности Day 2

- извлечение текста, метаданных, ссылок, изображений и заголовков;
- извлечение таблиц, нумерованных и маркированных списков;
- преобразование относительных ссылок в абсолютные;
- фильтрация некорректных, повторных и внешних ссылок;
- устойчивое восстановление данных из битого HTML;
- совместная загрузка и обработка через `fetch_and_parse()`.

## Возможности Day 3

- приоритетная очередь уникальных URL и несколько асинхронных workers;
- глобальные и отдельные для каждого домена ограничения конкурентности;
- ограничение глубины, количества страниц и фильтрация URL;
- состояние успешных, неудачных и посещённых страниц;
- агрегированная статистика и скорость обработки в страницах в секунду;
- вывод прогресса в реальном времени через `CrawlReporter`;
- асинхронное сохранение полного отчёта в JSON через `aiofiles`.

## Возможности Day 4

- глобальное или отдельное для каждого домена ограничение частоты запросов;
- минимальный интервал между запросами и поддержка `Crawl-delay`;
- асинхронная загрузка и кэширование `robots.txt` по origin;
- проверка `Allow`/`Disallow` для настраиваемого `User-Agent`;
- отдельный учёт URL, заблокированных правилами сайта.
- типизированные результаты запросов через `FetchResult`;
- отдельный `HttpTransport`, владеющий сессией, пулом и семафорами.
- единый `PolitenessManager` для rate limiting и соблюдения `robots.txt`.
- настраиваемый случайный `jitter` для интервалов между запросами;
- единый `RetryStrategy` с классификацией ошибок и exponential backoff;
- `RequestExecutor`, повторно применяющий правила вежливости перед каждой попыткой.
- увеличиваемые для каждой попытки HTTP-таймауты с верхней границей;
- типизированные `ParseError` без автоматического retry и общая статистика ошибок;
- статистика реальных HTTP-попыток, задержек, retry и robots.txt.
- расширенный live-отчёт: страницы/с, запросы/с, задержки и блокировки.

## Возможности Day 5

- классификация временных, сетевых, постоянных и parsing-ошибок;
- единый `RetryStrategy` с ограничениями по типам ошибок;
- exponential backoff и увеличиваемые таймауты отдельных попыток;
- retry для timeout, HTTP 429 и HTTP 5xx без retry для 403/404;
- четыре HTTP-попытки по умолчанию: одна начальная и до трёх повторов;
- явный `max_attempts=1` для полного отключения request retry;
- учёт типов ошибок, успешных retry и среднего времени ожидания;
- список URL с постоянными ошибками и структурированные `final_errors`;
- отдельный `ErrorTracker`, владеющий error records и их агрегацией;
- асинхронное сохранение отдельного error report через `aiofiles`;
- расширенный `CrawlReporter` со статистикой ошибок и retry.

## Возможности Day 6

- стандартная модель сохраняемой страницы `CrawlRecord`;
- минимальный асинхронный контракт `DataStorage`, принимающий `CrawlRecord`
  или его точное восьмиполевое словарное представление;
- масштабируемый JSON Lines через `aiofiles` с безопасной конкурентной записью;
- потоковое чтение JSON Lines и экспорт читаемого pretty JSON snapshot;
- CSV в настраиваемой кодировке с корректным экранированием специальных символов;
- буферизированные batch-вставки, транзакции и индексы через `aiosqlite`;
- повтор временных ошибок записи и отдельная storage-статистика;
- необязательное автоматическое сохранение после `fetch_and_parse()`;
- `CompositeStorage` для одновременной записи в несколько backends;
- ошибки сохранения не останавливают workers и не попадают в `failed_urls`.

Нормализация входных данных выполняется один раз на публичной границе
`DataStorage`: `save()` и `save_many()` принимают готовый `CrawlRecord` либо
словарь с теми же восемью полями, которые возвращает `CrawlRecord.to_dict()`.
Конкретные JSON, CSV и SQLite backends всегда получают только проверенные
`CrawlRecord`:

```python
record_data = record.to_dict()
await json_storage.save(record_data)
await csv_storage.save(record_data)
await sqlite_storage.save(record_data)
```

Перед передачей batch в backend `save_many()` сначала проверяет все элементы;
невалидный элемент не приводит к частичной записи этого batch.

### JSON Lines и pretty JSON

`JSONStorage` использует JSON Lines (`.jsonl`) как основной формат хранения:
каждая запись занимает одну строку, новые записи добавляются в конец файла, а
чтение выполняется последовательно без загрузки всего набора в память. Такой
формат подходит для долгого обхода и большого количества страниц.

Pretty JSON (`.json`) — отдельный читаемый snapshot в виде одного JSON-массива.
Он создаётся явно методом `export_pretty()` и не меняет исходный JSONL-файл:

```python
from src.json_storage import JSONStorage

json_storage = JSONStorage("pages.jsonl")
# ... await json_storage.save(record)
await json_storage.export_pretty("pages.json", indent=2)
await json_storage.close()
```

Перед экспортом сбрасывается буфер открытого writer. Экспорт читает JSONL и
пишет snapshot асинхронно и последовательно, сохраняет Unicode и создаёт `[]`
для пустого storage.

### CSV и SQLite

`CSVStorage` записывает стабильную схему `CrawlRecord`, поддерживает выбранную
кодировку, а стандартный CSV writer корректно обрабатывает Unicode, запятые,
кавычки и переносы строк. Поля `links` и `metadata` сохраняются как JSON:

```python
from src.csv_storage import CSVStorage

csv_storage = CSVStorage("pages.csv", encoding="utf-8")
# ... await csv_storage.save(record)
await csv_storage.close()
```

`SQLiteStorage` создаёт таблицу и индексы через `init_db()` при явном вызове
или лениво при первой операции. Записи буферизуются до `batch_size`, затем
сохраняются одной транзакцией через `executemany()`; `flush()` фиксирует остаток
буфера. Повторный URL обновляет существующую строку:

```python
from src.sqlite_storage import SQLiteStorage

sqlite_storage = SQLiteStorage("pages.db", batch_size=100)
await sqlite_storage.init_db()
# ... await sqlite_storage.save(record)
await sqlite_storage.flush()
await sqlite_storage.close()
```

### Одновременное сохранение

`CompositeStorage` отправляет каждую запись во все настроенные backends. У
каждого дочернего storage собственные retry и статистика, поэтому повтор одной
ошибочной записи в одном backend не дублирует её в остальных:

```python
from src.composite_storage import CompositeStorage
from src.crawler import AsyncCrawler
from src.csv_storage import CSVStorage
from src.json_storage import JSONStorage
from src.sqlite_storage import SQLiteStorage

storage = CompositeStorage([
    JSONStorage("pages.jsonl"),
    CSVStorage("pages.csv"),
    SQLiteStorage("pages.db"),
])

async with AsyncCrawler(storage=storage) as crawler:
    await crawler.crawl(["https://example.com"])
```

Параметры вежливости Day 4 включаются явно. Request retry при этом уже включён
по умолчанию: `AsyncCrawler()` выполняет не более четырёх попыток для временной
ошибки, а `max_attempts=1` полностью отключает повторы:

```python
crawler = AsyncCrawler(
    requests_per_second=2.0,
    respect_robots=True,
    min_delay=0.5,
    jitter=0.3,
    user_agent="MyBot/1.0",
    total_timeout=30.0,
    timeout_multiplier=2.0,
    max_timeout=120.0,
    max_attempts=4,
    retry_base_delay=0.5,
    retry_max_delay=10.0,
)
```

## Демонстрации

Day 1 — сравнение последовательной и конкурентной загрузки:

```bash
python -m src.main
```

Day 2 — загрузка и разбор нескольких страниц со статистикой:

```bash
python -m src.day2_demo
```

Day 3 — рекурсивный обход сайта с прогрессом и сохранением результата:

```bash
python -m src.day3_demo
```

Результат будет записан в `day3_results.json` в текущем каталоге.

Day 4 — локальная воспроизводимая демонстрация rate limiting, robots.txt,
jitter, retry и статистики запросов:

```bash
python -m src.day4_demo
```

Демонстрация сама запускает временный локальный сайт и не требует доступа к
интернету.

Day 5 — локальные endpoints для восстанавливающегося HTTP 429, постоянных
HTTP 503/404 и timeout с итоговым JSON-отчётом:

```bash
python -m src.day5_demo
```

Результат будет записан в `day5_error_report.json`. Доступ к интернету не
требуется, но WSL должен разрешать соединения с `127.0.0.1`.

Day 6 — один обход локального сайта с одновременным сохранением в JSON Lines,
CSV и SQLite, созданием pretty JSON и сравнением прочитанных данных:

```bash
./venv/bin/python -m src.day6_demo
```

Демонстрация использует только временный локальный сайт на `127.0.0.1` и не
обращается во внешний интернет. По умолчанию `run_demo(reset_output=True)` перед
обходом удаляет только известные файлы Day 6, поэтому последовательные запуски
дают одинаковые три записи. При `reset_output=False` сохраняется обычное
append/upsert-поведение backends.

Результаты находятся в каталоге `day6_results`:

- `pages.jsonl` — основной JSON Lines storage;
- `pages.json` — форматированный JSON snapshot;
- `pages.csv` — CSV-представление;
- `pages.db` — база SQLite.

После записи demo читает все четыре представления обратно, сравнивает полную
схему и значения `CrawlRecord` и завершает работу с ошибкой при нарушении
целостности.

## Тесты

```bash
./venv/bin/python -m pytest -q
```

Обычные тесты без socket-сценариев можно запустить отдельно:

```bash
./venv/bin/python -m pytest -q -m "not socket"
```

Полные локальные socket-сценарии Day 5 и Day 6:

```bash
./venv/bin/python -m pytest -q tests/test_day5_demo_socket.py
./venv/bin/python -m pytest -q tests/test_day6_demo_socket.py
```

Socket-тест Day 6 запускает настоящий pipeline `aiohttp` → `AsyncCrawler` →
parser → `CrawlRecord` → три storage backend-а → чтение и сравнение, но всё
равно использует только `127.0.0.1`.
