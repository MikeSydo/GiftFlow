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

## Media storage

Uploaded media defaults to local filesystem storage in `MEDIA_ROOT`. Set
`MEDIA_STORAGE_BACKEND=s3` to store new uploads in an S3-compatible bucket
instead. This supports AWS S3, Cloudflare R2, and compatible providers.

Required S3-compatible settings:

```env
MEDIA_STORAGE_BACKEND=s3
MEDIA_S3_BUCKET_NAME=giftflow-media
MEDIA_S3_ACCESS_KEY_ID=replace-me
MEDIA_S3_SECRET_ACCESS_KEY=replace-me
MEDIA_S3_REGION_NAME=auto
MEDIA_S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
MEDIA_S3_CUSTOM_DOMAIN=media.example.com
MEDIA_S3_LOCATION=media
MEDIA_S3_CACHE_CONTROL=max-age=86400
```

For AWS S3, set the real bucket region in `MEDIA_S3_REGION_NAME` and leave
`MEDIA_S3_ENDPOINT_URL` empty. For Cloudflare R2, use `auto` as the region and
set the R2 S3 API endpoint. `MEDIA_S3_CUSTOM_DOMAIN` is optional, but recommended
for public image URLs.

Existing database values for `Gift.image`, `GiftImage.image`, and `Shop.logo`
are relative file paths. After switching `MEDIA_STORAGE_BACKEND=s3`, copy the
existing local files into the bucket without changing database rows:

```powershell
python manage.py copy_media_to_storage --dry-run
python manage.py copy_media_to_storage
```

The command reads from local `MEDIA_ROOT` and writes to the active default
storage. It does not copy external Hotline URLs from `Gift.image_url` or
`ProductLink.image_url`.

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

## Dev readiness checklist

Before planning deployment, verify the full development flow from an empty or
refreshed local database:

1. Start Postgres and Redis.
2. Apply migrations.
3. Seed the default gift categories.
4. Start Django, the Celery worker, and Celery beat.
5. Queue the initial Hotline catalog bootstrap.
6. Confirm that `IngestionRun` records move to `completed` in Django admin.
7. Confirm that `/search/` loads categories and returns DB-backed results.
8. Confirm that `/search/api/` returns gifts with `detail_url` and `best_offer`.
9. Open a `/gifts/<slug>/` page and confirm merchant offers are sorted by live cheapest price first.
10. Click an offer and confirm the `/api/shops/products/<id>/click/` endpoint records a `ShopClick`.

Useful commands:

```powershell
python manage.py migrate
python manage.py seed_gift_categories
python manage.py bootstrap_hotline_catalog
```

Keep deploy work separate from this checklist. Deployment can start after the
checks above pass and the test commands in the verification section are green.
