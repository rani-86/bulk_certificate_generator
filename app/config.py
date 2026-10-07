"""Application settings. Everything can be overridden with environment variables."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./data/certificates.db"
    certificates_dir: Path = Path("./data/certificates")
    # Upper bound on recipients in a single request. Protects the server from
    # huge payloads; clients with more recipients should split into several jobs.
    max_recipients_per_job: int = 1000
    # Re-queue jobs left unfinished by a previous crash/restart on startup.
    resume_unfinished_jobs: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            certificates_dir=Path(os.getenv("CERTIFICATES_DIR", str(cls.certificates_dir))),
            max_recipients_per_job=int(os.getenv("MAX_RECIPIENTS_PER_JOB", cls.max_recipients_per_job)),
            resume_unfinished_jobs=os.getenv("RESUME_UNFINISHED_JOBS", "true").lower() == "true",
        )
