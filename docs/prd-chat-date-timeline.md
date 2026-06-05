# PRD：Crush 心电图按聊天记录实际时间统计

## 1. 背景与问题

当前系统中，所有分析记录的时间维度（`created_at`）都是**分析触发时间**——即用户按下「开始解码」按钮的日期。

这导致一个明显的产品逻辑问题：
- 用户 6 月 5 日上传了 3 月 15 日的聊天记录
- Crush 心电图（趋势图 / 时间轴）会在 6 月 5 日出现一个数据点
- 用户无法通过趋势图看到「3 月 15 日那天你们的关系状态」
- 所有历史记录都挤在分析当天，完全不能反映关系发展的真实时间线

## 2. 目标

让 **Crush 心电图 / 时间轴** 按**聊天记录实际发生的时间**进行聚合和展示，同时保留 **分析历史列表** 按分析触发时间排序的语义（"我什么时候做了这个分析"）。

## 3. 需求范围

### 3.1 必须做（P0）
- [ ] 数据库增加 `chat_date` 字段，存储聊天记录的代表日期
- [ ] AI Prompt 增加指令，让模型从聊天记录中提取 `chat_date`
- [ ] 后端 `save_analysis` 接收并保存 `chat_date`
- [ ] 后端 `get_trend` 和 `get_timeline` 改用 `chat_date` 分组排序
- [ ] 前端趋势图和时间轴接口适配

### 3.2 优化项（P1）
- [ ] `chat_date` 提取失败时，使用 AI 返回的分析日期，最后 fallback 到 `created_at`
- [ ] 截图 OCR 中识别到的时间戳也参与日期推断
- [ ] 历史分析卡片上显示 "📅 聊天日期: 2024-03-15"（如果有）

### 3.3 不做
- 精确到小时的聊天记录时间线（当前只到天级别）
- 聊天记录跨多天的拆分分析（当前取最早/代表性日期即可）

## 4. 技术方案

### 4.1 数据库变更

```sql
-- 新增 chat_date 字段
ALTER TABLE analyses ADD COLUMN chat_date TEXT;

-- 旧数据迁移：用 created_at 的日期部分填充
UPDATE analyses SET chat_date = date(created_at) WHERE chat_date IS NULL;
```

### 4.2 Prompt 修改

在 `ANALYSIS_PROMPT` 中增加：

```
# 聊天记录日期提取
请从聊天记录中识别这段对话发生的日期：
- 如果聊天记录中有明确的时间戳（如 "2024-03-15 20:30"），提取该日期
- 如果有相对时间（如 "昨天"、"今天"），结合当前时间推断
- 如果完全没有时间信息，返回 null
- 只返回日期，格式为 YYYY-MM-DD
```

输出 JSON 中增加 `"chat_date": "2024-03-15"` 字段（或 `"chat_date": null`）。

### 4.3 后端逻辑

**analyze API：**
1. AI 返回结果中包含 `chat_date`
2. 如果 AI 返回了有效日期，直接使用
3. 如果 AI 返回 null 或无效，fallback 到 `created_at` 的日期部分
4. `save_analysis` 新增 `chat_date` 参数

**get_trend：**
- ORDER BY `chat_date` DESC
- 返回字段中增加 `chat_date`

**get_timeline：**
- GROUP BY `date(chat_date)`（天）
- GROUP BY `strftime('%Y-%W', chat_date)`（周）

**get_history：**
- 保持 `ORDER BY created_at DESC` 不变（历史列表的语义是"我什么时候分析的"）
- 返回字段中增加 `chat_date`，前端可选择性展示

### 4.4 前端逻辑

**趋势图（/trend）：**
- 横轴标签从 `created_at` 改为 `chat_date`
- 数据点按 `chat_date` 从左到右排列

**时间轴（/timeline）：**
- 横轴已经是聚合后的 period，无需改动
- 但后端数据源的 GROUP BY 字段已改变

**历史列表（/history）：**
- 排序不变（仍按分析时间）
- 可额外显示 "聊天日期" 标签

## 5. 接口变更

### 5.1 POST /analyze

**请求不变**（后端从 AI 结果中提取 `chat_date`）

**响应新增字段：**
```json
{
  "heart_rate": 78,
  "level": "暖味期",
  "chat_date": "2024-03-15",
  "dimensions": {...},
  ...
}
```

### 5.2 GET /trend

**响应新增字段：**
```json
[
  {
    "id": 1,
    "heart_rate": 78,
    "level": "暖味期",
    "chat_date": "2024-03-15",
    "created_at": "2024-06-05T14:30:00"
  }
]
```

### 5.3 GET /timeline

**响应不变**（后端内部改 GROUP BY 字段，返回格式一致）

### 5.4 GET /history

**响应新增字段：**
```json
[
  {
    "id": 1,
    "crush_name": "小美",
    "chat_date": "2024-03-15",
    "created_at": "2024-06-05T14:30:00",
    ...
  }
]
```

## 6. 兼容性与迁移

- **旧数据**：`chat_date` 默认用 `date(created_at)` 填充，保证趋势图不会断裂
- **Mock 模式**：`generate_mock_result` 从聊天记录中随机提取一个日期，或返回 null
- **截图 OCR**：OCR 结果中的时间戳行（`--- 2024-03-15 ---`）在合并文本中保留，供 AI 识别

## 7. 测试场景

| 场景 | 预期 |
|------|------|
| 文本粘贴带时间戳 "2024-03-15" | AI 提取 chat_date="2024-03-15"，趋势图显示在 3 月 15 日 |
| 文本粘贴无时间戳 | AI 返回 null，chat_date 取 created_at 日期，趋势图显示在分析当天 |
| 截图 OCR 含日期 | OCR 保留时间戳行，AI 提取，趋势图正确 |
| 旧数据迁移 | 所有旧记录 chat_date = date(created_at)，趋势图不丢失 |
| 同一天分析多条不同日期的记录 | 趋势图按 chat_date 分散到各自日期 |

## 8. 实现优先级

1. 数据库迁移（增加字段 + 旧数据回填）
2. Prompt 修改 + AI 返回字段解析
3. 后端 API 适配（save / trend / timeline / history）
4. 前端趋势图横轴标签适配
5. 历史列表可选显示聊天日期

---

**作者：** Hermione  
**日期：** 2026-06-05  
**版本：** v1.0
