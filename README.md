# 校园社团中心

基于 PRD 与 Technical Spec 实现的校园社团公开展示与管理平台。项目采用 React + TypeScript + Vite 前端，以及 Flask + SQLAlchemy 后端；开发环境默认使用 SQLite，不使用 Redis 或任何业务缓存层。

## 快速启动

```bash
# 后端（Python 3.12）
cd backend
python -m pip install -r requirements.txt
python -m flask --app wsgi run --debug

# 新终端：前端
cd frontend
npm install
npm run dev
```

前端开发服务器默认运行在 `http://localhost:5173`，会将 `/api` 和 `/uploads` 代理到后端。首次启动后，系统会自动创建下列初始数据：

| 项目 | 值 |
| --- | --- |
| 管理员用户名 | `admin` |
| 管理员密码 | `Admin123!` |
| 初始类别 | 学术与科创、文化与艺术、体育健康、公益服务、传媒表达、生活兴趣 |

密码按 V1.0 规范以明文保存，仅用于开发环境；所有对外序列化与审计数据均排除密码字段。

## 测试

```bash
cd backend
PYTHONPATH=.deps:. python -m pytest -q

cd ../frontend
npm run build
```

后端测试覆盖注册审核、令牌、公开版本切换、权限、邀请、交接、管理员操作、图片校验及审计脱敏。

## 生产部署

项目包含 `docker-compose.yml`、`backend/Dockerfile`、`frontend/Dockerfile` 与 `nginx/default.conf`。部署前请通过环境变量替换 `SECRET_KEY`、`JWT_SECRET`、数据库连接与初始管理员密码，并按 Spec 第 11.3 节迁移至密码哈希存储。
