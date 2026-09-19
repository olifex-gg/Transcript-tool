"""PyInstaller entry point for the packaged desktop app."""

import multiprocessing
import sys

from yt_transcript.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
