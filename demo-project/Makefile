.PHONY: help test test-api test-web test-worker run seed

help:
	@echo "Nimbus — make targets:"
	@echo "  make test         run all available test suites"
	@echo "  make test-api     python api tests"
	@echo "  make test-web     web (node) tests"
	@echo "  make test-worker  go worker tests"
	@echo "  make run          describe/start the dev loop"
	@echo "  make seed         insert demo links"

test:
	./scripts/run_tests.sh

test-api:
	cd services/api && python -m unittest discover -s tests -p "test_*.py"

test-web:
	node web/test/format.test.js

test-worker:
	cd services/worker && go test ./...

run:
	./scripts/dev.sh

seed:
	./scripts/seed_data.sh
