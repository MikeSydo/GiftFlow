# GiftFlow

GiftFlow now runs with a single active ingestion architecture:

- `Celery beat` schedules Hotline catalog refreshes
- `Celery worker` ingests Hotline seed results and product offers
- `Gift` stores canonical Hotline products
- `Shop` and `ProductLink` store merchant offers parsed from Hotline product pages
- `/search/api/` reads only the local database
- `/gifts/<slug>/` shows the store list for one gift

## Local setup

### 1. Use Python 3.12

The Docker runtime uses `python:3.12-slim`, so local development should use
Python 3.12 as well.

Verify that Python 3.12 is installed:

```powershell
py -3.12 --version
```

If the virtual environment was created with a stale interpreter path, delete it
and recreate it:

```powershell
deactivate
Remove-Item -Recurse -Force .\.venv
py -3.12 -m venv .venv
```

### 2. Activate the virtual environment

```powershell
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe --version
```

### 3. Install dependencies

Always invoke pip through the active Python executable, especially after
recreating the virtual environment:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 4. Start infrastructure

```powershell
docker compose up -d postgres redis
```

### 5. Apply migrations

```powershell
python manage.py migrate
```

### 6. Start Django

```powershell
python manage.py runserver
```

### 7. Start the Celery worker

On Windows use `-P solo` to avoid `billiard` pool failures.

```powershell
python -m celery -A gift_idea_generator worker -l info -Q discovery,prices,verification -P solo
```

### 8. Start Celery beat

```powershell
python -m celery -A gift_idea_generator beat -l info
```

## Verification

Run the local checks before committing development changes:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test gifts search shops -v 1
```

If the local virtual environment is not available, run the same checks in the
Docker web image:

```powershell
docker compose run --rm web python manage.py check
docker compose run --rm web python manage.py makemigrations --check --dry-run
docker compose run --rm web python manage.py test gifts search shops -v 1
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
