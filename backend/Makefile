.PHONY: setup run test
setup:
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
run:
	.venv/bin/uvicorn main:app --reload --port 8000
test:
	.venv/bin/pytest -q
