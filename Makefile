.PHONY: bootstrap run health worker go-build

bootstrap:
	scripts/bootstrap.sh

run:
	scripts/run_webui.sh

health:
	scripts/healthcheck.sh

worker:
	scripts/run_worker.sh

go-build:
	cd go_bridge && go test ./... && go build -o rush_bridge .
