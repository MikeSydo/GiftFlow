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

Hotline seed refreshes cache newly discovered product images into `Gift.image`
through the configured default storage. With `MEDIA_STORAGE_BACKEND=s3`, these
files are uploaded to the S3-compatible bucket under `MEDIA_S3_LOCATION`. The
original Hotline URL remains in `Gift.image_url` as a source reference and
fallback if the download fails.

## First catalog bootstrap

Create the default category, tag, and admin-managed Hotline seed records:

```powershell
python manage.py seed_gift_categories
python manage.py seed_gift_tags
python manage.py seed_hotline_seeds
```

`seed_hotline_seeds` imports the built-in Hotline seed templates into the
database. After that, edit, activate, deactivate, clone, or queue seeds from
Django admin under `Search -> Hotline seeds`.

Queue the active Hotline seeds once after the services are running:

```powershell
python manage.py bootstrap_hotline_catalog
```

This command only enqueues refresh tasks for active admin-managed seeds. The
worker then:

1. pulls Hotline seed/category results
2. upserts canonical `Gift` records
3. enqueues product refresh tasks
4. parses merchant offers from Hotline product pages
5. upserts `Shop`, `ProductLink`, and `PriceHistory`

## Active scheduled flow

`Celery beat` drives these recurring jobs:

- cold-start bootstrap guard every 5 minutes
- seed refresh every 6 hours
- stale product refresh every hour in batches of 100

When `Celery beat` starts, it also queues one cold-start bootstrap check. The
check queues only active admin-managed Hotline seeds that do not already have a
completed seed refresh and do not already have an active pending/running seed
run. This lets an empty database populate itself after beat starts without
requeueing the whole catalog on every normal restart.

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
3. Seed the default gift categories, tags, and Hotline seed templates.
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
python manage.py seed_gift_tags
python manage.py seed_hotline_seeds
python manage.py bootstrap_hotline_catalog
```

## Fresh deploy simulation

Use this local-only reset to check how the app behaves from an empty database
and empty media storage. It intentionally deletes local Docker data and R2 media
objects under the configured `MEDIA_S3_LOCATION` prefix.

First inspect what would be removed from the active media storage:

```powershell
python manage.py purge_media_storage --dry-run
```

If the output is correct, purge only the configured media prefix:

```powershell
python manage.py purge_media_storage --confirm
```

Stop the local Compose stack and remove only the active GiftFlow volumes:

```powershell
docker compose down
docker volume rm giftflow_postgres_data giftflow_media_volume
```

Do not remove older similarly named volumes such as
`gift_idea_generator_postgres_data` or `gift_idea_generator_media_volume` unless
you have separately verified they belong to the data you want to delete.

Start from a clean database, apply migrations, and seed the default category
shell:

```powershell
docker compose up -d postgres redis
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py seed_gift_categories
docker compose run --rm web python manage.py seed_gift_tags
docker compose run --rm web python manage.py seed_hotline_seeds
```

Redis has no named volume in this Compose file, so recreating the container
clears queued tasks. If Redis is still running and you only need to clear its
current queue state, flush the configured local DB:

```powershell
docker compose exec redis redis-cli -n 0 FLUSHDB
```

Start the app, worker, and beat:

```powershell
docker compose up -d web celery_worker celery_beat
```

After beat starts, verify that the cold-start bootstrap check created
`IngestionRun` records without manually running `bootstrap_hotline_catalog`:

```powershell
docker compose logs -f celery_beat celery_worker
```

Then confirm that `/search/`, `/search/api/`, and several `/gifts/<slug>/`
pages show DB-backed gifts and sorted merchant offers across multiple
categories.

Keep deploy work separate from this checklist. Deployment can start after the
checks above pass and the test commands in the verification section are green.
