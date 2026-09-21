.PHONY: install run example dev test clean

DEMO_PORT ?= 3000

install:
	python3.12 -m venv .venv
	.venv/bin/pip install -r requirements.txt

run:
	.venv/bin/python main.py

example:
	.venv/bin/python -m http.server $(DEMO_PORT) -d examples/browser

dev:
	@trap 'kill 0' EXIT INT TERM; \
	.venv/bin/python main.py & \
	.venv/bin/python -m http.server $(DEMO_PORT) -d examples/browser & \
	wait

test:
	.venv/bin/python -m unittest discover -s tests

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
