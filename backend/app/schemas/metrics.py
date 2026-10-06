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


class FileMetricsResponse(BaseModel):
    commit_count: int
    files: list[ObjectMetricsOut] = Field(default_factory=list)


class DirectoryMetricsResponse(BaseModel):
    commit_count: int
    directories: list[ObjectMetricsOut] = Field(default_factory=list)
