VERSION ?= 0.1.2
IMAGE   ?= myykps-portal

.PHONY: mirror check serve image

## 抓取线上站并生成 site/
mirror:
	python3 scripts/mirror.py --out site --archive archive/raw

## 检查站内断链与残留动态链接
check:
	python3 scripts/check.py site

## 本地预览（兼容 ?q= 旧链接）
serve:
	python3 scripts/serve.py --dir site --port 8080

## 构建部署镜像
image:
	docker build -f deploy/Dockerfile --build-arg VERSION=$(VERSION) -t $(IMAGE):$(VERSION) .
