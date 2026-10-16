---
name: stock-research
description: 对单只 A 股做基本面 + 因子研究并产出研究报告。用户提到"研究某只股票 / 分析某公司 / 看看某代码"时使用。
user-invocable: true
---

# 股票研究流程

输入：一个 6 位 A 股代码（如 600519）。输出：`reports/<code>.md` 和对应回执。

## 步骤

1. **发布计划**：先调用 `muse_plan`，把下面四步作为 plan 提交，再开始执行。
2. **拉数据**：`bash scripts/fetch_fin.py <code>`，输出到 `data/<code>/`。
   - 脚本返回非零或 `data/<code>/meta.json` 里 `ok=false` 时，**不要自己编数据**，调用
     `muse_request_input` 问用户"数据源不可用，换数据源还是稍后重试"，然后结束本轮。
3. **算因子**：`bash scripts/factor_calc.py <code>`，读 `data/<code>/factors.json`。
4. **写报告**：按 `references/report_template.md` 的结构写 `reports/<code>.md`。
   每个因子值旁注明计算窗口和数据源。
5. **交回执**：调用 `muse_receipt`，`kind=report`，`path=reports/<code>.md`，
   `summary` 写一句结论（看多 / 看空 / 中性 + 核心理由）。

## 约束

- 外发动作（发邮件、推送到群）一律先 `muse_request_approval`，拿到批准再做。
- 报告里出现的任何数字必须能在 `data/<code>/` 里找到来源。
- 不要在一轮里重复调用同一个脚本超过两次；失败两次就按步骤 2 的方式请求输入。
