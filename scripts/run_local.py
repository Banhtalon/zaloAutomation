"""Explicit local application entrypoint.

The shared configuration module remains environment-only so tests and isolated
workers cannot load the repository ``.env`` as an import side effect.  This
launcher is the single opt-in boundary for the Owner's normal local startup.
"""

from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_local_environment(env_file: Path | None = None) -> None:
    load_dotenv(dotenv_path=env_file or PROJECT_ROOT / ".env", override=False)


def main() -> None:
    load_local_environment()
    import sys
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, app_dir=str(PROJECT_ROOT))


if __name__ == "__main__":
    main()
