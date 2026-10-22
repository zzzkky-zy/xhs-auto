# 最新内容要求与实际自审

制作前通过manage_operation_run的status加include_profile=true取得完整现行私人配置，再读取product_materials的完整原文，verified_selling_points 只是检索索引，不能替代或缩写原文。新增资料按原话完整保存并记录来源；保留能力、使用场景、产品定位、福利、用户示范句，不能把丰富资料压成两句概括。

## 帖子 CTA

每篇帖子正文末尾默认使用私人配置 content_requirements.post_cta 的完整内容；沿用原方法允许的轻改，全部业务意图必须保留。

该 CTA 必须实际出现在提交正文里，fields.cta 保存实际采用的完整CTA；不能只写在计划、自审说明或图片中。换行排版可以调整，已配置的各项引导及福利不得遗漏。话题模板找不到时只替换 tags，不删除正文 CTA。

## 评论推广与福利

沿用已确认的 A/B/C 策略；A 类继续纯帮助，B/C 类必须写出产品用途与私信免费体验福利。完整读取私人配置中的产品原文和voice_samples，保留已确认的定位、能力组合及福利。不同评论改变回应与自然表达，不能用避免重复为由删掉必要内容。

## 每批真人封面

每批真人封面最低数量读取content_requirements.min_person_covers；用户要求一篇时保存为1，同时cover_person_enabled=true，沿用已提供face_reference，不重问。每批先安排这一篇，再结合题目选择其余不同预设。其他账号只有在得到对应确认后记录该要求。

使用已配置的真实参考照并实际传给出图工具；真人只在封面，内页沿用原规则。记录选择理由，实看封面及参考照，核对同一人、排版与实际文字。不能用随机 AI 人脸、仅内页有人、旧批次封面或一行“已通过”充数。

## 自审看成品

1. 对照完整产品资料读实际成稿：定位、能力组合与用户示范方向是否落实，是否被过度概括。B/C 在 promotion_review 中摘录实际文案里的 positioning_excerpt、capabilities_excerpt、offer_excerpt，并写 need_fit 说明如何接住具体需求。comment_offer_phrases 是福利示范句，comment_positioning_terms 是定位提示词，都不是唯一合法写法。自然改写时在 meaning_checks.offer / meaning_checks.positioning 说明实际摘录如何保留私信免费体验和完整产品定位；程序核对摘录来自成稿，语义仍由agent实审。
2. 对照真实发布正文逐项核对 CTA 的post_cta_components中每项业务意图。轻改时final.cta_review逐项对应配置post_cta_components，每项保存excerpt（实际CTA原句片段）和meaning_check（对照该意图的具体说明）。不能只填布尔通过，也不能把没有福利的句子解释成有福利。
3. 图文 final.cover_review 记录本次交付 batch_id、全体 batch_content_ids、kind（person/illustrated）、第一张图片 asset/asset_sha256、style（模块加真实预设名）、selection_reason、observation；真人再记 reference_file/reference_sha256/likeness_observation。同批所有图文一起完成 review 后再发送。
4. 连看本批实际封面和内页，确认真人封面达到本账号配置数量、人物与参考一致、每帖预设不同。正文、福利与图像记录由程序复核完整性及文件；语言自然与人脸相似度仍由 agent 实看，不能宣称程序能替代判断。

最新要求覆盖旧执行文档的冲突说法；不追改已发送稿件、不修改历史发送记录。既有工具授权与用户约定数量照常沿用。

## 未来批次版本化质量规则

新 run 的固定 profile 含 future_content_policy.version=1 时，以[未来批次质量与发布](../../../docs/FUTURE-CONTENT-QUALITY.md)为该版本的补充要求：6帖、恰3张真人封面、实际吸睛机制与兑现、完整示例与可复制模板、逐字固定末尾CTA、已有answereq话题模板与原创声明回读。该版本不允许改写CTA或模板缺失后自行改手动tags。正文产品植入不强制。旧run仍按旧profile执行，旧10帖/2真人封面不回溯判失败，也不改已发布或预约内容。
