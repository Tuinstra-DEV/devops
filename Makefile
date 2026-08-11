.PHONY: lint test test-runner

lint:
	./scripts/lint.sh

test:
	./scripts/test.sh

test-runner:
	./scripts/test-runner-platform.sh
