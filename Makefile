.PHONY: install run example clean

install:
	python3.12 -m venv .venv
	.venv/bin/pip install -r requirements.txt

run:
	.venv/bin/python main.py

example:
	python -m http.server 3000 -d examples/browser

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
