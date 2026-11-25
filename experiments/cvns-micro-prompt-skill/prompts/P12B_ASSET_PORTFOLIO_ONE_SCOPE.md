# P12B — 单范围资产组合筛选

**本轮唯一目标：** 判断一个范围内哪些 StoryAsset 值得保留、合并、延期或拒绝
**输入：** 同范围 P12 资产候选、asset_index、叙事容量摘要
**只遵守本任务的限制：**
1. 每条输入领域 asset_id 恰好一种处置，无项用空数组，不造占位 ID；合并组与 selected_assets 的 source_asset_ids 集合一致，不重复延期/拒绝。
2. 合并只处理读者问题/叙事功能实质重复且阶段安排兼容的资产；不得抹掉不同人物的正当诉求、不同 payoff 或不同目标章。selected_assets 必须保留 R-06.2 StoryAsset 的 source_asset_ids，并逐阶段复制原始目标章：SETUP=introduce_chapter、REVEAL=reveal_chapter、PAYOFF=payoff_chapter；不得重新推算或擅自改期。
3. 按叙事容量比较增量收益，不因名称、对象或章期不同就判独特；拒绝资产不删除人物/BASE诉求。只做组合决策，不生成事件；选中资产可跨事件分阶段兑现。
**交付：** AssetPortfolioDecision。
