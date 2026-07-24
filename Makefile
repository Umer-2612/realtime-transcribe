.PHONY: install run example test clean

install:
	python3.12 -m venv .venv
	.venv/bin/pip install -r requirements.txt

run:
	.venv/bin/python main.py

example:
	python -m http.server 3000 -d examples/browser

test:
	.venv/bin/python -m unittest discover -s tests

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
