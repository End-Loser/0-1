# 项目上传检查清单

本清单用于向本仓库提交项目、实验、代码、数据或文档前的自检。目标是让内容**可审核、可复现、许可清晰、无敏感信息、仓库体积可控**。

提交前请先阅读 [投稿与审核指南](../.github/CONTRIBUTING.md)、[行为准则](../.github/CODE_OF_CONDUCT.md) 和 [安全说明](../.github/SECURITY.md)。

## 一、先判断内容类型与落点

| 内容 | 放在哪里 |
|---|---|
| 想法、问题、批评、反例 | `ideas/`，文件名建议 `topic-short-title.md` |
| 实验、原型、复现说明 | `experiments/`，文件名建议 `topic-experiment-name.md` |
| 代码 | `experiments/<项目>/`，或独立仓库并在条目中给出稳定链接 |
| 原始大数据、模型、安装包 | 不进 git；用 Release / LFS / Zenodo，并在条目中给稳定链接 |

一个项目通常拆成三部分并互相链接：

1. `ideas/` 中的想法条目（为什么做、怎么验证、别人如何接手）；
2. `experiments/` 中的实验条目（实际结果、复现步骤、局限）；
3. `experiments/<项目>/` 或外部仓库中的代码与数据摘要。

不要把代码塞进 `ideas/`；不要只提交代码而不写条目。

## 二、体积与 GitHub 限制

- 单文件 **100MB** 是硬限制，**50MB** 会警告；仓库建议控制在 **1GB** 以内。
- 不提交可再生成的大文件：`.npz`、`.csv`、`.cir`、`.raw`、日志、编译产物、依赖目录、缓存。
- 大文件替代方案：
  - GitHub Release 附件；
  - Git LFS（注意免费配额与带宽）；
  - Zenodo / OSF / 机构存储等外部归档。
- 在条目中写清：哪些文件未入库、如何生成、外部归档的链接与版本 / commit。

## 三、安全与隐私红线（提交前必须扫描）

**绝对不要提交**：

- 账号、密码、Token、API Key、私钥、`.env`、浏览器 Cookie；
- 身份证号、手机号、住址、客户名单、未公开的个人数据；
- 内部地址、未授权抓取的数据、商业秘密；
- 含个人信息的截图、PDF、日志。

**检查方法**：

- 文件名与路径：`账户`、`密码`、`secret`、`token`、`credential`、`.env`、`.pem`、`.key`；
- 内容扫描：`password`、`passwd`、`api_key`、`secret`、`token`、`BEGIN PRIVATE KEY`、`ghp_`、`sk-`；
- 人工检查截图、PDF、示例配置和日志。

**如果已经泄露**：

1. 不要继续提交；
2. 立即轮换该凭据；
3. 从工作区与 git 历史中移除（如 `git filter-repo` / BFG），必要时联系维护者；
4. 在 Issue / PR 中说明影响范围，不要贴出凭据本身。

## 四、第三方材料与许可

- 只提交你有权公开的内容。
- **不要提交**：厂商数据手册 / PDF、SPICE 模型、工具安装包、厂商源码副本、未授权论文全文、网页快照，除非许可证明确允许并保留声明。
- **可以提交**：引用链接、书目信息、公开许可证下的少量引用（注明来源与许可）。
- 本仓库默认许可：
  - 原创代码：MIT（[LICENSE](../LICENSE)）；
  - 文字、文档、图片和整理的数据：CC BY 4.0（[LICENSE-CONTENT.md](../LICENSE-CONTENT.md)）。
- 第三方依赖保留各自许可证（例如 GPL-2.0、GPLv3、BSD）；在条目中列出依赖与许可证，不要默认所有内容适用同一许可。

## 五、内容与状态要求

- 区分**事实、推断、猜测、未验证**；给出实际结果、失败记录、已知反例和局限。
- 使用状态标签：
  - `status:draft`：草案 / 待审核
  - `status:discussion`：讨论中
  - `status:experiment`：验证中
  - `status:verified`：有可复现证据支持
  - `status:revised`：已修正
  - `status:refuted`：当前证据不支持原假设
  - `needs-info`：需要补充信息
  - `risk:needs-review`：需要额外安全或隐私审核
- 没有可复现证据时，不得使用 `status:verified`。
- “子项通过”不等于“整机完成”；“代码能跑”不等于“物理结论成立”。
- 条目至少包含：对应想法 / 假设、目标、环境与依赖、数据来源与许可、复现步骤、实际结果、局限 / 反例、风险、作者与许可。

## 六、复现性要求

- 写明操作系统、语言 / 库版本、硬件 / 工具依赖、外部模型或数据来源。
- 给出代表性命令、预期输出和失败分支。
- 注明哪些文件未入库、如何重新生成；不要只给作者本机的绝对路径。
- 缺件要明确写“缺件”和获取路径，不要用估算或假设代替。

## 七、提交流程

1. 在 Issues 用对应模板说明想法或实验；或由维护者整理。
2. 从最新 `main` 建分支，例如 `experiment/<topic>`。
3. 只提交筛选后的文件；先写 `.gitignore`，再 `git add`。
4. 检查 `git status`、`git diff --cached --stat`、文件数、体积和敏感信息。
5. 提交信息用中文说明范围；PR 描述按模板填写并关联 Issue。
6. 维护者审核后合并。
7. 合并后更新索引：`README.md`、`ideas/README.md`、`experiments/README.md`。
8. 把 Issue / 条目中的临时分支链接改为 `main`；按需删除已合并分支。

## 八、提交前检查清单（可复制）

- [ ] 已确定内容类型与落点（`ideas/` / `experiments/` / 外部仓库）。
- [ ] 已写明对应想法、实验目标与状态。
- [ ] 已写明环境、依赖、复现命令与预期结果。
- [ ] 已写明数据来源、许可与脱敏情况。
- [ ] 已写明实际结果、失败记录、局限与反例。
- [ ] 已列出第三方依赖与许可证。
- [ ] 已扫描并排除账号、密码、Token、私钥、个人信息。
- [ ] 已排除 >50MB 文件、原始大数据、第三方资料、依赖目录、日志。
- [ ] `.gitignore` 已覆盖本地凭据与大文件。
- [ ] 已检查 `git diff --cached --stat` 与文件数 / 体积。
- [ ] 已关联 Issue，并按模板填写 PR。
- [ ] 合并后已更新索引与链接。

## 九、快速检查命令

```bash
# 将提交的文件与体积
git status --short
git diff --cached --stat
git diff --cached --name-only | wc -l

# 敏感信息扫描（按需扩展）
# 注意：本清单自身包含示例关键词，扫描时会命中；可按需排除本文件。
git grep -n -I -E "password|passwd|api[_-]?key|secret|token|BEGIN [A-Z ]*PRIVATE KEY|ghp_|sk-" --cached

# 查找大于 50MB 的文件（本地工作区）
find . -type f -size +50M -not -path "./.git/*"
```

Windows PowerShell 示例：

```powershell
Get-ChildItem -Recurse -File | Where-Object { $_.Length -gt 50MB } | Select-Object FullName, Length
```

## 十、例外处理

- 任何例外（必须提交第三方材料、超大文件、未公开数据）都要在 Issue / PR 中说明：
  - 为什么必须提交；
  - 许可或授权依据；
  - 体积与替代方案；
  - 谁批准、有效期与撤回方式。
- 没有批准的例外不要提交。
- 拿不到关键输入时，写“缺件”并给出获取路径；不要把假设写成结果。

---

本清单不替代 [投稿与审核指南](../.github/CONTRIBUTING.md) 与 [安全说明](../.github/SECURITY.md)；如有冲突，以维护者审核意见为准。
