"""Worker entry point: `python -m app.worker`."""

from app.scheduler.runner import main

if __name__ == "__main__":
    main()
