.PHONY: install lint fmt test guard check demo

# Same gates CI runs. `make check` before every push.
install:
	uv sync --locked

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest

guard:
	uv run python -m tools.check_no_literals

check: lint guard test

# Offline dashboard on http://127.0.0.1:8000/ (run `make install` once while online).
demo:
	uv run --locked --offline python -m uvicorn shipos.api.server:app --host 127.0.0.1 --port 8000 --ws websockets-sansio
