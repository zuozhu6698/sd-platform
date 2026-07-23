# 11 APP-SRV 部署与运维手册

当前应用机已确认，但尚未做资产/连通性检查。本手册描述 Gate 4 的标准步骤，不是“已部署”证明。

## 1. 首次连接前

1. 轮换曾通过对话传递的密码。
2. 为实施人员生成独立 SSH 密钥，服务器只安装公钥。
3. 验证密钥和应急控制台都可用，再关闭 root/密码 SSH 登录。
4. 限制 `sdjg` sudo 到运维所需命令；不要共享私钥。

真实主机地址、账号、口令放企业资产/密钥系统，不进 Git。

## 2. 只读资产检查

```bash
cat /etc/os-release
uname -a
nproc
free -h
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS
df -hT
docker version
docker compose version
ss -lntup
timedatectl
```

同时测试到 OA、观远、Harbor、Nexus、DNS、NTP、SMTP、GPU 服务的连通性。输出脱敏后归档为 O0 证据。

最低候选：4 vCPU、16GB RAM、独立数据盘、可用空间≥200GB。最终以 Teable/PG/日志/备份容量测算为准。

## 3. 主机基线

- 受支持 Linux，安全补丁、NTP、企业 DNS/CA。
- Docker Engine + Compose v2 来自批准软件源。
- 只开放 22（管理网）、80/443（业务网）；5432 仅观远来源。Redis/Teable/API 不对 host 暴露。
- Docker 日志轮转；数据、WAL、备份与系统盘分开监控。
- 安装企业 EDR/主机审计，不关闭防火墙和 SELinux/AppArmor 保护来“解决”问题。

## 4. 目录与权限

```text
/opt/sd-platform/        compose、release manifest、非密配置
/etc/sd-platform/        .env、证书引用（root:sd-platform 0640）
/var/lib/sd-platform/    数据卷/挂载
/var/backups/sd-platform/本地备份缓存（还需离机副本）
/var/log/sd-platform/    受控日志
```

容器优先非 root、只读 rootfs、drop capabilities；确有写入的目录单独挂载。

## 5. 部署前检查

- Gate 0–3 已通过；变更单、责任人、维护窗口、回滚负责人明确。
- `.env` 必填值、域名/证书、镜像 digest、Alembic revision、Teable release 已核对。
- 备份成功且恢复点可读；磁盘余量≥30%。
- `docker compose config`、镜像签名/SBOM/漏洞门禁通过。
- OA/GPU/扫描服务不可用时的降级已被业务接受。

## 6. 部署顺序

```text
pull by digest
→ backup + record release marker
→ postgres/redis-teable/redis-app
→ Teable health + compatibility smoke
→ Alembic preflight/upgrade sd_app+bi
→ sd-api + sd-worker
→ web/nginx
→ readiness + critical smoke
→ enable worker schedule
→ 观察 30 分钟
→ 标记 deployment verified
```

首次部署 `CRON_ENABLED=false`。数据/权限/时钟/日历验证后再开启，避免一启动就批量催办。

## 7. Smoke

1. `/healthz` 200，`/readyz` required dependencies ready。
2. OA TEST 用户 SSO 登录、登出、ticket 重放拒绝。
3. 本人读取/跨范围 403；测试任务填报，刷新可见且不重复。
4. 手动触发无外发的 dry-run 规则任务；再对测试用户发一条 OA 待办并回写已办。
5. LLM schema smoke；模型不可用时降级提示。
6. 观远只读视图可查，写和非 bi schema 被拒绝。
7. 上传安全测试文件（如 EICAR 仅在获准 TEST 环境）验证拒绝和审计。

## 8. 备份与恢复

- WAL 连续归档，告警 last successful archive age。
- 每日全量备份，加密并复制到离机存储；保留策略经制度批准。
- 每季度在隔离 TEST 恢复：PG → Alembic/Teable 一致性 → API smoke → 对账。
- 目标 RPO≤15min、RTO≤4h 必须由演练计时证明。
- Redis 不作为灾备真相；可重建缓存。

## 9. 回滚

### 应用回滚

停 scheduler → 切回上一个镜像 digest → readiness/smoke → 恢复 scheduler。配置也要按 release manifest 回退。

### 数据库回滚

优先使用向后兼容代码回滚，不急于 downgrade。若迁移不可兼容，执行 PR 预先批准的 downgrade 或从维护窗口前备份恢复；恢复会丢失窗口内写入，必须由业务批准。

### Teable 回滚

仅按 P0 生成的升级/回退手册执行。禁止在没有内部 schema 兼容验证时降级镜像。

## 10. 日常运维

- 每日：服务、磁盘、备份、outbox/dead letter、job miss、OA/LLM 依赖。
- 每周：失败对账、权限变更、审计异常、漏洞/镜像更新。
- 每月：容量趋势、账号/密钥、恢复点抽检、依赖支持状态。
- 每季度：完整恢复、OA/GPU/Redis/Teable 故障演练和权限复核。

