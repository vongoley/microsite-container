# Microsite Container

面向多文件静态网站、SPA、大型 HTML、数据文件及音视频资源的增量部署与托管服务。

Microsite Container 从 HTML Container 演进而来，但两者定位不同：HTML Container
继续负责单个 HTML/Markdown 文件的轻量分享；本项目将一个站点视为由 manifest 描述的
文件集合，通过内容哈希复用资源，并以不可变 deployment 和原子指针完成发布。

## 当前能力

- 多站点：每个用户可维护多个独立 slug 的静态站点
- 不可变部署：deployment 完成后不再修改，旧版本保留稳定访问地址
- 增量同步：客户端先发送 manifest，服务端仅返回缺失的 SHA-256 blob
- 流式大文件上传：不把音频、视频或大型数据文件一次性载入内存
- 内容寻址存储：相同文件跨部署复用，本地磁盘目录可迁移到 S3/R2
- 原子激活：SQLite 事务一次切换站点的 active deployment，不暴露半成品
- 静态资源服务：支持 MIME、ETag、Range、跨域资源访问和 SPA fallback
- Nginx 数据面：可通过 `X-Accel-Redirect` 将文件传输卸载给 Nginx
- Codex/Claude Skill：扫描目录、生成 manifest、上传缺失资源、finalize 并 activate
- 旧单文件接口暂时保留，便于迁移；新站点应使用 `/api/sites` 接口

当前第一阶段的 microsite 数据面是公开访问；API Token 只保护创建和发布操作。不要把
私密资源部署为 microsite。站点级访问控制将在与独立资源 Origin 的鉴权策略一起加入。

## 架构

```text
deploy CLI / CI
      │  manifest、认证、发布控制
      ▼
FastAPI + SQLite                         控制面
      │  active_deployment_id 原子切换
      ▼
content-addressed local blobs + Nginx   数据面
```

数据默认位于 `app/data/microsites/`：

```text
microsites/
├── blobs/
│   └── ab/abcdef...    # SHA-256 分片目录
└── tmp/                 # 上传校验完成前的临时文件
```

SQLite 只保存站点、部署、文件映射和 blob 元数据。静态文件不存入数据库。

## 快速开始

### 本地开发

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

访问 `http://localhost:8000/admin/login`。首次本地启动在未配置密码哈希时仍会创建
`admin/admin123`，生产环境必须覆盖该默认值。

### Docker

```bash
cp .env.example .env
# 编辑 .env
docker compose --env-file .env up -d --build
```

Docker 将宿主机 `./data` 挂载到容器的 `app/data`，因此数据库和 blob 会持久化。

## Manifest 部署协议

### 1. 创建站点

```http
POST /api/sites
Authorization: Bearer TOKEN
Content-Type: application/json

{"slug":"vietnamese-learning","title":"Vietnamese Learning"}
```

slug 只接受小写字母、数字、点、短横线和下划线。

### 2. 创建 staging deployment

```http
POST /api/sites/vietnamese-learning/deployments
Authorization: Bearer TOKEN
Content-Type: application/json

{
  "entrypoint": "index.html",
  "spa_fallback": true,
  "files": [
    {
      "path": "index.html",
      "sha256": "<64-char sha256>",
      "size": 9427088,
      "content_type": "text/html"
    }
  ]
}
```

响应中的 `missing_blobs` 是本次真正需要上传的哈希列表。

### 3. 上传缺失 blob

```http
PUT /api/sites/{slug}/deployments/{deployment_id}/blobs/{sha256}
Authorization: Bearer TOKEN
Content-Type: application/octet-stream
Content-Length: ...
```

服务端在落盘前同时校验声明长度、实际长度和 SHA-256。一个 deployment 只能上传其
manifest 引用的 blob。

### 4. 校验并原子激活

```http
POST /api/sites/{slug}/deployments/{deployment_id}/finalize
POST /api/sites/{slug}/deployments/{deployment_id}/activate
```

finalize 确认所有 blob 都存在后将部署冻结为 `ready`；activate 在一个 SQLite 写事务中
把旧版本标为 `superseded`，并切换站点的 active deployment。对任一 `superseded`
deployment 再次调用 activate 即可原子回滚。

访问地址：

- 当前版本：`/sites/{slug}/`
- 不可变版本：`/_deployments/{deployment_id}/`

## Skill

登录后台生成个人 API Token 后安装：

```bash
curl -fsSL "https://your-domain.com/api/install-skill?token=YOUR_TOKEN" | bash
```

安装位置为 `~/.codex/skills/microsite-container`，配置位于
`~/.config/microsite-container/credentials.env`。

发布站点目录：

```bash
python3 ~/.codex/skills/microsite-container/scripts/deploy.py check
python3 ~/.codex/skills/microsite-container/scripts/deploy.py deploy \
  --slug vietnamese-learning \
  --title "Vietnamese Learning" \
  --dir ./dist \
  --entrypoint index.html
```

CLI 会自动创建不存在的站点，并完成 hash、manifest、增量上传、finalize 和 activate。
重复发布相同资源时不会再次上传已有 blob。

## Nginx 数据面

FastAPI 默认直接返回文件，适合本地开发。生产环境设置：

```text
MICROSITE_ACCEL_PREFIX=/_protected_microsite_blobs
```

并在反向代理中配置与 blob 目录对应的 internal location：

```nginx
location /_protected_microsite_blobs/ {
    internal;
    alias /absolute/path/to/data/microsites/blobs/;
}
```

FastAPI 仍负责 deployment/path 到 hash 的解析、权限边界和响应头；Nginx 负责 Range 与
文件传输。`MICROSITE_PUBLIC_BASE_URL` 可让 API/Skill 返回独立数据 Origin 的公开地址，
`MICROSITE_CORS_ORIGIN` 控制资源响应的跨域来源。

## 配额环境变量

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `MICROSITE_DATA_DIR` | `app/data/microsites` | blob 与临时文件根目录 |
| `MICROSITE_MAX_FILES` | `100000` | 单次 deployment 文件数上限 |
| `MICROSITE_MAX_TOTAL_BYTES` | `53687091200` | manifest 总逻辑大小上限（50 GiB） |
| `MICROSITE_MAX_BLOB_BYTES` | `5368709120` | 单个 blob 上限（5 GiB） |
| `MICROSITE_PUBLIC_BASE_URL` | 空 | 静态站点的独立公开 Origin |
| `MICROSITE_ACCEL_PREFIX` | 空 | Nginx internal location；空表示 FastAPI 直出 |
| `MICROSITE_CORS_ORIGIN` | `*` | 公共静态资源 CORS 来源 |

## 越南语学习页验证

真实验证对象为：
`https://html.orcacalf.site/view/53cd5401`。

当前样本大小 9,427,088 字节，包含 6,895 个音频占位。测试不把大型源文件提交到仓库；
下载到临时目录后执行：

```bash
VIETNAMESE_LEARNING_HTML=/path/to/vietnamese-learning.html pytest -q
```

`tests/test_vietnamese_fixture.py` 会验证真实页面规模和音频槽位，并生成外部
`audio-manifest.json` 测试站点；核心集成测试还会验证 manifest 上传、哈希复用、原子
激活、SPA fallback 与音频 Range 请求。

## 兼容的旧接口

继承的 `/api/pages/{slug}` 与 `/view/{slug}` 仍可托管单个 HTML/Markdown 文件，但不再是
本项目的主发布协议。新的多文件站点不要把资源内联到一个巨大 HTML 中。

## License

MIT
