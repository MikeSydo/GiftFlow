# GiftFlow

GiftFlow now runs with a single active ingestion architecture:

- `Celery beat` schedules Hotline catalog refreshes
- `Celery worker` ingests Hotline seed results and product offers
- `Gift` stores canonical Hotline products
- `Shop` and `ProductLink` store merchant offers parsed from Hotline product pages
- `/search/api/` reads only the local database
- `/gifts/<slug>/` shows the store list for one gift

## Local setup

### 1. Start infrastructure

```powershell
docker compose up -d postgres redis
```

### 2. Activate the virtual environment

```powershell
.\.venv312\Scripts\Activate.ps1
```

### 3. Apply migrations

```powershell
python manage.py migrate
```

### 4. Start Django

```powershell
python manage.py runserver
```

### 5. Start the Celery worker

On Windows use `-P solo` to avoid `billiard` pool failures.

```powershell
python -m celery -A gift_idea_generator worker -l info -Q discovery,prices,verification -P solo
```

### 6. Start Celery beat

```powershell
python -m celery -A gift_idea_generator beat -l info
```

## First catalog bootstrap

Queue the configured Hotline seeds once after the services are running:

```powershell
python manage.py bootstrap_hotline_catalog
```

This command only enqueues seed refresh tasks. The worker then:

1. pulls Hotline seed/category results
2. upserts canonical `Gift` records
3. enqueues product refresh tasks
4. parses merchant offers from Hotline product pages
5. upserts `Shop`, `ProductLink`, and `PriceHistory`

## Active scheduled flow

`Celery beat` drives two recurring jobs:

- seed refresh every 6 hours
- stale product refresh every hour in batches of 100

## Search and detail flow

- `/search/api/` returns only DB-backed gift results
- search result cards open `/gifts/<slug>/`
- the gift detail page shows sorted merchant offers and outbound shop links

If the catalog is empty, run the bootstrap command and wait for the worker to finish the first ingestion cycle.
