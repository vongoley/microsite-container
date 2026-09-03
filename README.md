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
- Runtime Data：微站点通过统一 SDK 读写站点级 JSON Document，支持 Schema、版本冲突和历史留存
- 自动数据更新：为服务器 cron/worker 签发单站点、单 Document 的机器写入 Token，无需重部署前端
- 站点管理后台：集中查看当前激活部署，并导出完整站点资源与 Runtime Data ZIP
- Codex/Claude Skill：扫描目录、生成 manifest、上传缺失资源、finalize 并 activate
- 旧单文件接口暂时保留，便于迁移；新站点应使用 `/api/sites` 接口

静态文件数据面仍是公开访问；部署 API Token 保护创建、发布和 Runtime Writer Token 管理。
不要把私密资源直接放进 deployment。Runtime Data 可以声明为公开读取或仅 owner 读取；
写入使用浏览器登录会话，或绑定到单个站点和 Document 的机器写入 Token。

## 架构

```text
deploy CLI / CI
      │  manifest、认证、发布控制
      ▼
FastAPI + SQLite                         控制面
      │  active_deployment_id 原子切换
      ▼
content-addressed local blobs + Nginx   数据面

browser Runtime SDK / scheduled worker
      │  登录会话或 scoped writer token、revision
      ▼
FastAPI Runtime Data API + SQLite       可变数据面
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

登录后的 Admin 是部署站点管理页，不提供手动文件上传入口。站点应由 Codex、部署 CLI 或
CI 发布；页面会展示当前激活 deployment、资源规模、Runtime Document 数量和最近部署时间。

### Admin 完整导出

已激活站点可以从 Admin 下载 ZIP。导出结构如下：

```text
{slug}/
├── export.json                         # 站点与当前 deployment 元信息
├── site/                               # 当前激活部署的全部前端和资源文件
└── runtime-data/
    ├── documents/{key}.json            # Runtime Data 当前值、revision 与配置
    └── history/{key}/rev-{n}.json       # 服务端当前保留的版本历史
```

导出仅允许站点 owner 或 super admin 操作。Admin 根据静态资源、Runtime Data 和 ZIP 目录开销
估算导出大小；超过 50 MB 时会先弹窗说明内容和耗时，用户确认后才开始生成下载。ZIP 在服务端
临时目录生成，响应完成后自动删除，不会长期占用导出副本空间。

### Docker

```bash
cp .env.example .env
# 编辑 .env
docker compose --env-file .env up -d --build
```

Docker 将宿主机 `./data` 挂载到容器的 `app/data`，因此数据库和 blob 会持久化。

## 示例站点

- [`examples/training-log`](examples/training-log/)：移动端优先的训练日志微站点。演示 Runtime
  Data 初始化、跨设备保存、revision 冲突保护，以及固定月份的公网只读分享链接。原单文件
  HTML 的内嵌训练数据通过 seed 完整迁移，后续 deployment 不会覆盖线上数据。

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

## Runtime Data：微站点通用可变数据

Deployment 中的 HTML、JavaScript、CSS 和数据文件仍然不可变。需要由页面直接编辑并跨终端
读取的数据，应声明为 Runtime Data，由 SQLite 单独保存。这样发布版本与运行时数据各自
拥有清晰的生命周期：更新代码走 deployment，保存用户数据走 Runtime Data API。

### 站点目录

```text
training-calendar/
├── index.html
├── assets/
│   ├── app.js
│   └── styles.css
├── data/
│   └── plan.json
├── schemas/
│   └── training-plan.schema.json
└── microsite.json
```

`microsite.json` 在 finalize 时校验，在 deployment 激活时原子注册：

```json
{
  "runtimeData": {
    "documents": {
      "training-plan": {
        "scope": "site",
        "read": "public",
        "write": "owner",
        "schemaVersion": 1,
        "schema": "schemas/training-plan.schema.json",
        "seed": "data/plan.json",
        "maxBytes": 1048576
      }
    }
  }
}
```

当前 MVP 支持：

- `scope: "site"`：所有终端读取同一份站点数据
- `read: "public" | "owner"`
- `write: "owner"`：owner/super admin 使用浏览器登录会话，或受限机器 Token 写入
- 可选 JSON Schema 和 `schemaVersion`
- 可选 `seed`：仅在文档尚不存在时初始化；重新部署不会覆盖已保存数据
- `revision` 乐观锁；过期写入返回 `409 Conflict`
- 默认保留最近 100 个版本，用于审计和后续数据回滚能力

Schema 示例：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": {
    "type": "array",
    "items": { "type": "string" },
    "uniqueItems": true
  }
}
```

### 浏览器 SDK

微站点页面不应包含 deployment API Token。引入平台提供的 SDK，写入时复用 HttpOnly
浏览器登录会话：

```html
<script src="/_microsite/sdk/v1.js"></script>
<script>
  const planDocument = MicrositeData.document("training-plan");

  async function loadPlan() {
    const result = await planDocument.get();
    return result.value || {};
  }

  async function savePlan(plan) {
    // SDK 自动携带最近一次 get() 得到的 revision。
    return planDocument.save(plan);
  }
</script>
```

SDK 还提供显式、不会自动覆盖服务器数据的本地草稿：

```js
planDocument.saveDraft(editedPlan, planDocument.revision);
const draft = planDocument.loadDraft();
planDocument.clearDraft();
```

当两个终端同时基于 revision 12 编辑时，第一个保存得到 revision 13；第二个继续保存会收到
`MicrositeData.ConflictError`，页面应提示刷新或合并，不能静默覆盖。
当前 MVP 在页面加载或主动调用 `get()` 时同步最新数据；尚未提供 SSE/WebSocket，因此已经
打开的其他终端不会被实时推送刷新。

### Runtime Data API

```http
GET /api/runtime/sites/{slug}/documents/{document_key}

PUT /api/runtime/sites/{slug}/documents/{document_key}
If-Match: "rev-12"
Content-Type: application/json

{"value":{"2026-08-25":["shoulders","core"]}}
```

GET 返回 `ETag: "rev-N"` 和当前 `revision`。新建但尚未 seed 的文档返回 `value: null`、
`revision: 0`；第一次 PUT 必须携带 `If-Match: "rev-0"`。

### 服务器定时更新

需要每日更新行情或报告数据时，前端只部署一次。计算脚本在服务器 cron、systemd timer、CI、
云函数或独立 worker 中运行，然后使用精确绑定到一个站点和一个 Document 的 Writer Token
更新 Runtime Data。平台只保存 Token 的 SHA-256 摘要，明文只在创建时返回一次。

```bash
# 使用部署凭证创建最小权限 Token，并安全保存为 mode-0600 环境文件
python3 ~/.codex/skills/microsite-container/scripts/deploy.py runtime-token create \
  --slug investment-report --document latest-analysis \
  --name daily-market-job \
  --save-token /secure/path/investment-report-runtime.env

# 定时任务生成 latest.json 后发布；CLI 自动读取当前 revision 并发送 If-Match
set -a
. /secure/path/investment-report-runtime.env
set +a
python3 ~/.codex/skills/microsite-container/scripts/deploy.py runtime put \
  --slug investment-report --document latest-analysis \
  --file /srv/investment-report/latest.json
```

Writer Token 不能部署代码，也不能写入其他站点或 Document。撤销与审计命令：

```bash
python3 ~/.codex/skills/microsite-container/scripts/deploy.py runtime-token list \
  --slug investment-report
python3 ~/.codex/skills/microsite-container/scripts/deploy.py runtime-token revoke \
  --slug investment-report --token-id TOKEN_ID
```

Microsite Container 不执行 deployment 中携带的 Python/Node 文件，也不内置任意代码调度器；
任务隔离、第三方密钥、重试、日志和告警由独立 worker 或宿主机调度器负责。

代码回滚与数据回滚相互独立：激活旧 deployment 会恢复旧前端代码及配置，但不会覆盖当前
Runtime Data。激活前会使用目标 deployment 的 Schema 验证现有数据；不兼容时拒绝激活，
避免旧代码读取无法理解的数据。

当前 Runtime Data 是面向受信任微站点的 MVP。所有站点仍共享同一个内容 Origin；开放给
不受信任的多租户内容前，应先完成独立站点 Origin 或等价的浏览器隔离。

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
| `MICROSITE_RUNTIME_MAX_DOCUMENT_BYTES` | `1048576` | 单个 Runtime Document 的平台级上限 |
| `MICROSITE_RUNTIME_MAX_VERSIONS` | `100` | 每个 Runtime Document 保留的最近版本数 |
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
本项目的主发布协议，也不再显示于 Admin 站点列表。新的多文件站点不要把资源内联到一个
巨大 HTML 中。

## License

MIT
