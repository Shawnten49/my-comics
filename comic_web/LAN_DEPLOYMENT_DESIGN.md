# 局域网发布开发方案

> 文档状态：核心功能已实施  
> schema 无关  
> 适用范围：`comic_web/app.py`、`start_site.sh`、`stop_site.sh`

## 1. 目标

增加局域网发布模式：

```bash
python3 comic_web/app.py --lan
```

或：

```bash
./start_site.sh --lan
```

要求：

1. 局域网设备可以访问网站。
2. 应用层拒绝来自公网地址的请求。
3. 默认使用 HTTPS，保护登录密码和会话 Cookie。
4. 保留游客、普通用户、管理员权限体系。
5. 管理功能只允许管理员使用。
6. 防止 DNS Rebinding、CSRF、目录穿越和未授权媒体访问。
7. 不启用 UPnP、不修改路由器端口映射。

## 2. “外网不可访问”的边界

应用不能直接控制路由器是否进行端口转发。

因此采用多层约束：

```text
仅绑定一个私有局域网地址
+ 应用层仅允许私有网段客户端
+ macOS 防火墙限制
+ 路由器不配置端口转发
```

只要没有在路由器或公网网关上设置端口转发，外部网络无法直接访问。

即使误配置端口转发，应用层私有网段白名单仍会拒绝公网来源请求。

## 3. 网络绑定

### 3.1 本机模式

不指定 `--lan` 时保持：

```text
127.0.0.1:8767
```

### 3.2 局域网模式

`--lan` 自动检测一个 RFC1918 私有 IPv4 地址：

```text
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
```

检测失败或存在多个局域网地址时要求：

```bash
python3 comic_web/app.py --lan --lan-address 192.168.1.25
```

服务只绑定该具体地址：

```text
192.168.1.25:8767
```

不绑定公网 IP，不使用 `0.0.0.0` 作为默认局域网模式。

局域网模式会同时启动：

```text
LAN_IP:8767   HTTPS
127.0.0.1:8767 HTTP
```

本机可通过 `http://127.0.0.1:8767/` 访问，局域网设备必须通过
`https://LAN_IP:8767/` 访问。

## 4. 客户端网段白名单

默认允许：

```text
127.0.0.0/8
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
169.254.0.0/16
::1/128
fc00::/7
fe80::/10
```

支持环境变量收紧：

```bash
export COMIC_LAN_ALLOWED_CIDRS="192.168.1.0/24"
```

每个请求先解析客户端 IP：

```python
client_ip = ipaddress.ip_address(self.client_address[0])
```

IPv4-mapped IPv6 需要先标准化。

不在白名单时返回：

```http
403 lan_access_denied
```

默认不信任：

```text
X-Forwarded-For
X-Real-IP
CF-Connecting-IP
```

避免客户端伪造来源 IP。

## 5. Host 校验

允许的 Host：

```text
192.168.1.25:8767
localhost:8767
127.0.0.1:8767
配置的局域网主机名
```

其他 Host 返回：

```http
421 invalid_host
```

该措施用于防止 DNS Rebinding 攻击，避免恶意网页把域名解析到局域网服务器后访问内部接口。

## 6. HTTPS

### 6.1 默认策略

局域网模式默认启用 HTTPS。

首次启动生成自签名证书：

```text
run/tls/lan-key.pem
run/tls/lan-cert.pem
```

证书 SAN 包含：

```text
局域网 IP
localhost
127.0.0.1
配置的 LAN 主机名
```

### 6.2 启动参数

```bash
python3 comic_web/app.py \
  --lan \
  --tls-cert run/tls/lan-cert.pem \
  --tls-key run/tls/lan-key.pem
```

如果已经由可信内部 CA 或 mkcert 签发证书，可以直接使用。

### 6.3 HTTP 降级

必须显式允许：

```bash
python3 comic_web/app.py --lan --lan-allow-http
```

启动时打印高风险警告：

```text
局域网 HTTP 会暴露登录密码和会话 Cookie，仅用于临时调试。
```

生产使用应保持 HTTPS。

### 6.4 Cookie

HTTPS 模式：

```text
Secure
HttpOnly
SameSite=Lax
```

HTTP 模式：

```text
HttpOnly
SameSite=Lax
```

## 7. 管理功能

当前管理功能在非回环地址时会全部禁用。

调整后：

```text
本机模式：管理员可用
局域网 HTTPS 模式：管理员可用
未知 Host 或非白名单网段：全部拒绝
```

仅管理员可访问：

- 漫画管理。
- 下载管理。
- 批量下载。
- 增量拉取。
- 删除任务。
- 迁移记录。
- 用户管理。

游客和普通用户行为保持不变。

管理员敏感操作继续要求 CSRF Token。

## 8. 安全响应头

所有页面和 API 返回：

```text
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: same-origin
Permissions-Policy: camera=(), microphone=(), geolocation=()
Cross-Origin-Resource-Policy: same-origin
```

建议增加 CSP：

```text
default-src 'self';
img-src 'self' data: blob:;
style-src 'self' 'unsafe-inline';
script-src 'self';
connect-src 'self';
frame-ancestors 'none';
```

现有内联脚本需要迁移到独立 JS 文件后才能启用严格 CSP。

## 9. API 和缓存

### 9.1 禁止跨域

不返回允许跨域的响应头。

不处理第三方站点的 CORS 预检为允许状态。

### 9.2 JSON 和认证响应

```text
Cache-Control: no-store
```

### 9.3 漫画媒体

私有漫画资源：

```text
Cache-Control: private, max-age=3600
```

不允许共享代理缓存：

```text
public
```

### 9.4 请求大小

JSON 请求体限制为：

```text
1 MB
```

超过限制返回：

```http
413 request_too_large
```

## 10. 防攻击措施

### 登录

- 用户名和 IP 双维度限流。
- 10 分钟最多 5 次失败。
- 通用登录失败提示。
- 会话轮换。
- 密码 PBKDF2。

### CSRF

所有非 GET 请求校验：

```text
X-CSRF-Token
```

### 路径

- 禁止绝对路径。
- 禁止 `..`。
- 文件必须属于索引中的漫画。
- 资源路径不能越过 storage root。

### 会话

- Session Token 只保存 SHA-256 哈希。
- HttpOnly Cookie。
- 24 小时空闲超时。
- 最长 7 天。
- 用户禁用后撤销全部会话。

### 管理接口

- 仅管理员。
- 本机或私有局域网来源。
- 严格 Host 检查。
- 审计日志记录权限、删除和迁移操作。

## 11. 启动脚本调整

`start_site.sh` 增加：

```bash
./start_site.sh --lan
```

行为：

1. 检测局域网 IP。
2. 生成或定位 TLS 证书。
3. 启动 Python 服务。
4. 健康检查使用：
   ```text
   https://<LAN_IP>:8767/api/health
   ```
5. 输出：
   ```text
   局域网地址：https://192.168.1.25:8767/
   ```
6. 写入 PID 和日志文件。

`stop_site.sh --lan` 根据 PID 文件停止服务，不需要重复检测网络。

## 12. 本机防火墙

方案不自动修改 macOS 防火墙配置。

建议：

1. 开启 macOS 防火墙。
2. 只允许 Python 接受局域网连接。
3. 不开放公网端口。
4. 不在路由器设置本机 `8767` 的端口转发。
5. 不启用 UPnP。

## 13. 配置项

```text
--lan
--lan-address
--lan-allow-http
--tls-cert
--tls-key
--lan-allowed-cidrs
```

环境变量：

```text
COMIC_LAN_ALLOWED_CIDRS
COMIC_LAN_HOSTNAME
```

## 14. 错误码

| 错误码 | HTTP | 场景 |
| --- | --- | --- |
| `lan_access_denied` | 403 | 来源不在私有网段 |
| `invalid_host` | 421 | Host 不在白名单 |
| `tls_required` | 503 | 局域网模式未配置 HTTPS |
| `tls_certificate_invalid` | 503 | 证书或私钥不可用 |
| `request_too_large` | 413 | 请求体过大 |

## 15. 测试方案

### 网络

- 本机模式仍只监听 127.0.0.1。
- `--lan` 只监听指定私有 IP。
- 公网 IP 来源请求被拒绝。
- 非允许 Host 请求被拒绝。
- 伪造 X-Forwarded-For 不生效。

### HTTPS

- 证书 SAN 包含局域网 IP。
- HTTPS 登录正常。
- Secure Cookie 生效。
- HTTP 模式必须显式开启。

### 权限

- 游客只能访问公开漫画。
- 普通用户可访问 level 0/1 并收藏。
- 管理员可在局域网使用管理功能。
- 普通用户和游客访问管理 API 被拒绝。

### 安全

- CSRF 缺失时写请求失败。
- 私有漫画媒体不能通过直接 URL 访问。
- 请求体超过限制被拒绝。
- 登录失败限流有效。
- 路径穿越被拒绝。
- 响应头包含安全策略。

## 16. 实施顺序

1. 增加 LAN 地址检测和 `--lan` 参数。
2. 增加私有 CIDR 白名单中间件。
3. 增加 Host 校验。
4. 增加 HTTPS 包装和安全 Cookie。
5. 调整管理权限，使局域网管理员可用。
6. 增加安全响应头和请求大小限制。
7. 更新 `start_site.sh`、`stop_site.sh`。
8. 更新 README 和防火墙说明。
9. 完成局域网设备、游客、普通用户和管理员测试。
