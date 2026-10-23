# myykps.cn

go.myykps.cn（YKPS Portal）的只读静态存档。站点由原 Drupal 7 门户抓取生成，
以纯静态文件方式提供访问，不包含后端逻辑。

## 目录

| 路径 | 说明 |
| --- | --- |
| `site/` | 生成的静态站点，部署时作为 Web 根目录 |
| `scripts/mirror.py` | 抓取线上站点并生成 `site/` |
| `scripts/serve.py` | 本地预览，兼容 `?q=` 旧链接 |
| `scripts/check.py` | 检查断链、残留动态链接 |
| `deploy/nginx.conf` | 前置 TLS 终止时使用的 Nginx 配置 |
| `deploy/nginx.ssl.conf` | 容器直接处理 HTTPS 的 Nginx 配置 |
| `deploy/Dockerfile` | 部署镜像 |
| `archive/raw/` | 抓取到的原始 HTML，本地存档，不入库 |
| `build/manifest.json` | 最近一次抓取的页面、资源与缺失项清单 |

## 命令

```bash
make mirror    # 抓取并生成 site/
make check     # 检查站内断链与残留动态链接
make serve     # 本地预览 http://127.0.0.1:8080/
make image     # 构建镜像 myykps-portal:0.1.2
```

单独调用脚本时：

```bash
python3 scripts/mirror.py --help
python3 scripts/check.py site
python3 scripts/serve.py --dir site --port 8080
```

## 抓取参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--out` | `site` | 静态站输出目录 |
| `--archive` | `archive/raw` | 原始 HTML 存档目录，传空字符串关闭 |
| `--delay` | `0.2` | 每次请求后的等待秒数 |
| `--keep-forms` | 关闭 | 不禁用页面表单控件 |
| `--noindex` | 关闭 | 给页面加 robots noindex |

## 部署

Web 根目录指向 `site/`，并需要一条改写规则支持旧链接：

```
/?q=xxx  ->  /xxx/index.html
```

### Docker

```bash
docker build -f deploy/Dockerfile --build-arg VERSION=0.1.2 -t myykps-portal:0.1.2 .
docker run -d -p 80:80 myykps-portal:0.1.2
```

镜像自带 `deploy/nginx.conf`，监听 80 端口。若由容器直接处理 HTTPS，
把 `deploy/nginx.ssl.conf` 覆盖到容器内 `/etc/nginx/conf.d/default.conf`，
并将证书挂载到 `/etc/nginx/certs/fullchain.pem` 与 `/etc/nginx/certs/privkey.pem`：

```bash
docker run -d -p 80:80 -p 443:443 \
  -v $PWD/deploy/nginx.ssl.conf:/etc/nginx/conf.d/default.conf:ro \
  -v /path/to/certs:/etc/nginx/certs:ro \
  myykps-portal:0.1.2
```

### 已有 Nginx

将 `site/` 作为站点根目录，参考 `deploy/nginx.ssl.conf` 中的 `server` 配置，
关键是这条兼容规则：

```nginx
if ($arg_q ~ "^(?!.*\.\.)[A-Za-z0-9_./%-]+$") {
    rewrite ^/(?:index\.php)?$ /$arg_q/index.html last;
}

location / {
    try_files $uri $uri/index.html $uri/ =404;
}
```

## 内容说明

- `site/` 中有 76 张图片等资源在抓取时返回 404，线上站点同样缺失，不是抓取遗漏。
  清单见 `build/manifest.json` 的 `known_missing`。
- 登录、评论、邮件转发等动态入口统一指向 `/offline/`，该页说明功能已停用。
