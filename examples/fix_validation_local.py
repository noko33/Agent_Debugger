"""Windows entry point; run with backend on PYTHONPATH and the existing editable SDK."""

import argparse
from pathlib import Path

from app.fix_validation_demo import run_demo


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic catalog fix-validation fixture")
    parser.add_argument("--output", type=Path, default=Path("../.evaluation-artifacts/catalog"))
    args = parser.parse_args()
    result = run_demo(args.output)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
