# Loom Console — 测试凭据

## 管理员账户
- Email: admin@loom.dev
- Password: LoomAdmin2026!
- Role: admin

## 认证端点
- POST /api/auth/register  (email, password, name)
- POST /api/auth/login     (email, password)
- POST /api/auth/logout
- GET  /api/auth/me
- POST /api/auth/refresh

认证方式: httpOnly Cookie (access_token / refresh_token)，同时支持 Authorization: Bearer 头。

## LLM
- EMERGENT_LLM_KEY 已配置在 /app/backend/.env，默认 provider: emergent (openai/gpt-5.4)
