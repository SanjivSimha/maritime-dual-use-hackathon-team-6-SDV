.PHONY: install lint fmt test guard check

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
