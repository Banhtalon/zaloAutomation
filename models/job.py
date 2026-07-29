from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class JobStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class JobCreate(BaseModel):
    job_type: str

class Job(BaseModel):
    id: int
    job_type: str
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    progress: int = 0
    message: Optional[str] = None
    created_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    cancel_requested: bool = False
    attempt: int = 1
    retry_of_job_id: Optional[int] = None
    retryable: bool = False
    recoverable: bool = False
    mutates_external: bool = False
