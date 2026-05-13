from datetime import timedelta

from django.db import models
from django.utils import timezone


class IngestionRun(models.Model):
    TASK_TYPE_SEED_REFRESH = "seed_refresh"
    TASK_TYPE_PRODUCT_REFRESH = "product_refresh"

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    TASK_TYPE_CHOICES = [
        (TASK_TYPE_SEED_REFRESH, "Seed refresh"),
        (TASK_TYPE_PRODUCT_REFRESH, "Product refresh"),
    ]

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    task_type = models.CharField(
        max_length=30,
        choices=TASK_TYPE_CHOICES,
        db_index=True,
    )
    seed_key = models.CharField(max_length=100, blank=True, default="", db_index=True)
    gift = models.ForeignKey(
        "gifts.Gift",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ingestion_runs",
    )
    source_product_id = models.CharField(
        max_length=200,
        blank=True,
        default="",
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    discovered_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    queued_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [
            models.Index(
                fields=["task_type", "status"],
                name="search_inge_task_ty_5f059e_idx",
            ),
            models.Index(
                fields=["task_type", "seed_key", "-updated_at"],
                name="search_inge_task_ty_6bb6cc_idx",
            ),
            models.Index(
                fields=["task_type", "source_product_id", "-updated_at"],
                name="search_inge_task_ty_850850_idx",
            ),
        ]

    def __str__(self):
        target = self.seed_key or self.source_product_id or self.gift_id or "-"
        return f"{self.task_type}:{target} [{self.status}]"


class SearchIngestionJob(models.Model):
    SOURCE_HOTLINE = "hotline"

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    SOURCE_CHOICES = [
        (SOURCE_HOTLINE, "Hotline"),
    ]

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default=SOURCE_HOTLINE,
    )
    query = models.CharField(max_length=255)
    normalized_query = models.CharField(max_length=255, db_index=True)
    request_filters = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    result_count = models.PositiveIntegerField(default=0)
    gift_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    last_requested_at = models.DateTimeField(default=timezone.now)
    queued_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    gifts = models.ManyToManyField(
        "gifts.Gift",
        blank=True,
        related_name="search_ingestion_jobs",
    )

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "normalized_query"],
                name="unique_source_normalized_query",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "status"]),
            models.Index(fields=["source", "-updated_at"]),
        ]

    def __str__(self):
        return f"{self.source}:{self.normalized_query} [{self.status}]"

    def is_fresh(self, ttl: timedelta | None = None) -> bool:
        ttl = ttl or timedelta(hours=6)
        if self.status != self.STATUS_COMPLETED or self.finished_at is None:
            return False
        return self.finished_at >= timezone.now() - ttl
