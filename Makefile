.PHONY: lint test test-runner release-tag

lint:
	./scripts/lint.sh

test:
	./scripts/test.sh

test-runner:
	./scripts/test-runner-platform.sh

# Usage: make release-tag TAG=v1
# Moves an existing tag to current HEAD and force-pushes it.
release-tag:
	@if [ -z "$(TAG)" ]; then echo "Usage: make release-tag TAG=v1"; exit 1; fi
	git tag -f $(TAG) HEAD
	git push origin $(TAG) --force
	@echo "Tag $(TAG) now points to $$(git rev-parse --short HEAD)"
