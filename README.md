# ci

组织级**公开 CI 库**。把私有仓库里"重"的 CI 挪到这里跑，用公开库免费不限量的
GitHub 托管 runner，给私有仓库的自托管 runner 减压。

本库**不存放任何业务代码**，只放 workflow 与触发约定。

## 为什么这么做

| | 私有库自托管 runner | 公开库 GitHub 托管 runner |
|---|---|---|
| 资源 | 一台机器，所有 job 抢队列 | 每个 job 独占一台干净 VM，并行无限 |
| 计费 | 0 | 公开库免费不限量 |
| 代价 | —— | 需要一次跨库触发 + 结果回写 |

`THPF` 只有一台 `[self-hosted, thpf-test]`，backend unit + integration + frontend
三个 job 在上面互相排队，已经成为合并检查的瓶颈。所以把合并检查搬到本库。

## 架构（与 `outsidee-Inc/gds-data → gds-data-ci` 同构）

```
私有库 tonghui-inc/THPF                     公开库 tonghui-inc/ci（本库）
─────────────────────────                   ─────────────────────────
trigger-backend-ci.yml   ──dispatch──▶      thpf-backend-ci.yml
  on: push/pull_request                      on: repository_dispatch
  paths: backend/** …                          types: [thpf-backend-ci]
  └ 铸 App token → POST /dispatches            ├ unit        → ci/backend-unit
                                               └ integration → ci/backend-integration
trigger-frontend-ci.yml  ──dispatch──▶      thpf-frontend-ci.yml
  on: push/pull_request                      on: repository_dispatch
  paths: frontend/**                           types: [thpf-frontend-ci]
                                               └ frontend    → ci/frontend
```

要点：

1. **路径过滤留在私有库的 trigger 里**（`on.<event>.paths`），由 GitHub 原生完成，
   零成本，语义与迁移前逐字一致（"只改 frontend 不触发后端 CI"）。
2. **本库的 workflow 不注册任何 `pull_request` 事件**，只接 `repository_dispatch` /
   `workflow_dispatch`。所以本库的 PR（含 fork）拿不到任何 secret，也无法触发这些 job。
3. 私有码用 GitHub App 铸的**降权 token** 拉取，且 checkout 一律
   `persist-credentials: false` —— 不可信的 PR 代码就要在同一 workspace 里跑测试，
   不能让它从 `.git/config` 读到 token。
4. 结果以 **commit status** 回写到私有库的 head sha，PR 上照旧显示 ✓/✗ 和跳转链接。

## 前置配置（一次性）

### GitHub App

App：`tonghui-bot`（app id `4947044`，client id `Iv23liH7gFjh7TXDBMHJ`，
installation `161769402`，repo_selection = all）。

所需权限（新增权限后需在 installation 页面重新批准）：

| 权限 | 级别 | 用途 |
|---|---|---|
| Contents | Read and write | 发 `repository_dispatch`（write）+ 拉私有码（降权成 read） |
| Commit statuses | Read and write | 回写结果到私有库 sha |
| Pull requests | Read and write | PR 评论（可选） |
| Issues | Read and write | 同上兜底（issue comment 端点允许二者任一） |
| Actions | Read and write | 备用通道 `workflow_dispatch` |
| Metadata | Read | 强制 |

### Secrets

| 仓库 | Secret | 说明 |
|---|---|---|
| `tonghui-inc/ci` | `TONGHUI_BOT_CLIENT_ID` | `Iv23liH7gFjh7TXDBMHJ` |
| `tonghui-inc/ci` | `TONGHUI_BOT_PRIVATE_KEY` | App 私钥（.pem 全文） |
| `tonghui-inc/ci` | `CODECOV_TOKEN` | 可选；缺失时自动跳过覆盖率上传并告警，不影响测试结论 |
| `tonghui-inc/THPF` | `TONGHUI_BOT_CLIENT_ID` | 同值 |
| `tonghui-inc/THPF` | `TONGHUI_BOT_PRIVATE_KEY` | 同值 |

不需要 GitHub Environment：这些凭据都是全库唯一一份，环境只会带来
"每个 job 等人工批准"的副作用。详见 git 历史里的讨论。

### 未搬过来的部分（有意为之）

| Workflow | 为什么留在 THPF |
|---|---|
| `backend-deploy.yml` / `frontend-deploy.yml` | 要动宿主机 docker compose 与 `/data`，必须 `[self-hosted, Linux, X64]`；部署语义也该在目标仓库 |
| `backend-docker-tests.yml`（nightly） | 真容器用例依赖宿主机 `/usr/local/lib/hermes-agent`（`container_runtime.py` 里硬编码的挂载源）、`hermes-agent:test` 镜像与测试机目录结构，无法离开自托管测试机 |

## 约定

- **命名**：`thpf-*.yml` 对应 `tonghui-inc/THPF`；以后接入别的私有库，用
  `<repo>-*.yml` 前缀区分，并在 trigger 侧 `STATUS_CONTEXTS` 对齐。
- **context 名**：`ci/<repo>/<job>` 风格见上表；trigger 与本库 workflow 里的
  清单必须成对修改（trigger 负责先挂 pending，并在派发失败时改红灯）。
- **action 固定 SHA**：本库是公开库且持有 App 私钥，第三方 action 一律钉到 commit SHA，
  由 `.github/dependabot.yml` 自动提 PR 升级。
- **手动重跑**：直接在本库 Actions 页面 `workflow_dispatch`，`sha` 留空 = 私有库默认分支最新。
- **取消语义**：同一 PR 的新 push 会取消上一轮；被取消的那一轮**不回写状态**，
  避免和取代它的那一轮抢时序留下假红灯。