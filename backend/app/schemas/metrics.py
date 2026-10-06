"""Pydantic schemas for the metrics endpoints."""
from pydantic import BaseModel, ConfigDict, Field


class ObjectMetricsOut(BaseModel):
    """Metrics of one object (file or directory) within a commit set."""

    model_config = ConfigDict(from_attributes=True)

    path: str
    added_lines: int
    removed_lines: int
    growth: int
    churn: int
    modifications: int
    modification_frequency: float
    churn_rate: float


class AuthorMetricsOut(BaseModel):
    """Per-author metrics on one object within a commit set."""

    model_config = ConfigDict(from_attributes=True)

    author_id: int
    display_name: str
    modifications: int
    churn: int
    ownership: float


class FileMetricsResponse(BaseModel):
    commit_count: int
    files: list[ObjectMetricsOut] = Field(default_factory=list)


class DirectoryMetricsResponse(BaseModel):
    commit_count: int
    directories: list[ObjectMetricsOut] = Field(default_factory=list)


class RepositoryMetricsResponse(BaseModel):
    """Repository metrics: directory metrics on the root of the commit tree."""

    commit_count: int
    repository: ObjectMetricsOut


class AuthorMetricsResponse(BaseModel):
    """Author metrics for one object (root by default)."""

    commit_count: int
    path: str
    authors: list[AuthorMetricsOut] = Field(default_factory=list)
