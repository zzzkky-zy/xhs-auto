# 内容配置、阶段留存与审查绑定

这是 `manage_operation_run` 的内容证据契约。程序检查资料完整性、注册先后、字节哈希、范围和策略数量；它不能证明模型确实读过规则、分步生成、看过图片或作出正确语义判断。agent仍须实际审读，不能把客观检查通过写成内容质量通过。

## 私有运营配置

`configure` 新增 `content_profile`。配置及历次版本只存现有私有 `tasks.sqlite3`，不进源码、安装包或公共报告。已有账号、任务、发送事实、授权工具和验收状态保留；有活跃或未知任务也允许仅修订配置。每次修订使活跃批次旧审查失效。缺少确认的配置可以保存，但 `review`/写入授权拒绝使用；不得替用户确认。

```json
{
  "content_profile": {
    "persona": "已确认的身份与表达方式",
    "target_audience": "已确认的人群",
    "objective": "已确认的业务目的",
    "verified_selling_points": [
      {"id": "point-1", "claim": "有依据的能力", "evidence_ref": "依据来源", "evidence_file": "/私有目录/能力核实.md"}
    ],
    "offer": {"description": "无额外福利", "evidence_ref": "用户确认没有额外福利"},
    "keywords": ["已确认关键词"],
    "boundaries": ["已确认禁区"],
    "allow_c": false,
    "strategy": {"A": 40, "B": 30, "C_max": 30, "fallback": {"A": 40, "B": 60, "C": 0}},
    "voice_samples": [{"text": "样稿按反馈修改后获认可的表达", "source_ref": "样稿及用户反馈来源"}],
    "cover_person_enabled": true,
    "face_reference": {
      "file": "/包外私有素材目录/用户上传的真实人脸.png",
      "sha256": "实际文件的64位SHA256",
      "source_ref": "用户上传或明确指定该图用于真人封面的记录"
    },
    "confirmation_sources": {
      "persona": "用户确认位置",
      "target_audience": "用户确认位置",
      "objective": "用户确认位置",
      "verified_selling_points": "用户确认位置",
      "offer": "用户确认位置",
      "keywords": "用户确认位置",
      "boundaries": "用户确认位置",
      "allow_c": "用户确认位置",
      "strategy": "用户确认位置",
      "voice_samples": "样稿反馈位置",
      "cover_person_enabled": "用户允许真人封面的答复",
      "face_reference": "用户上传或明确指定该图的记录"
    }
  }
}
```

读取完整现行配置用 manage_operation_run(operation="status", data={"include_profile":true})，返回完整content_profile及材料路径，不另建版本或改任务。默认status只返回版本和待补项，不能以此替代读取产品原文；拿到路径后完整读取product_materials。

第一次配置仍同时提供原 `authorization_ref`、`quality_standard`、`allowed_tools`；后续仅传 `content_profile` 可保留原授权。`status` 返回配置版本和待确认字段。任何字符串引用只是责任来源记录，程序不替用户认证其真实性。

首次配置统一按[逐步引导](../skills/xiaohongshu-operations/references/first-time-setup.md)：先在onboarding目录制作1篇帖子与5条评论样稿，再将用户反馈认可的最终表达写入voice_samples；没有反馈不能伪造该字段或发送。草稿制作不依赖configure，也不能先用假授权或假表达样例通过检查。

真人封面为可选项，新配置保存 `cover_person_enabled` 和确认来源。false时无需face_reference，可完成配置；true时必须有实际模特照片、路径、正确文件哈希及用户来源，缺项会返回待补，照片实看仍由agent完成。上例展示允许真人的情况；拒绝时改为false并省略face_reference及其来源。旧配置不自动新增选择或授权，只在准备真人封面前补问。原图存包外私有素材目录，每次使用核对来源和准确字节，实际传给制图工具。后续修订仍读取并合并完整content_profile，不能只传照片字段覆盖其他资料。见[真人封面接入](../skills/xiaohongshu-operations/references/visuals/modules/gbro-cover-design/guide.md)。

批次 `plan.comments`/`plan.posts` 是数量上限，原批已成功、待执行和未知提交均计入，不能用新审查覆盖旧发送事实。评论批次另给 `plan.comment_strategy` 的A/B/C整数名额；数量合计必须等于计划评论数，C不得超过配置上限且必须获准。A/B为大致目标，不能程序静默改成全A；偏离约定比例时，计划须记录 `strategy_adjustment_ref` 的实际用户约定。C不适合时按已确认的 `strategy.fallback` 分配A/B；未设fallback时按A:B原比例分配剩余名额，不为凑比例新增C。上例只说明可配置字段，不是预设授权；旧版40/40/20也不是程序硬上限，各账号的实际比例、C权限及fallback从私有确认配置读取。用户明确未设业务禁区时，`boundaries=[]`与该字段确认来源是有效配置。

可以只审原批中新做的一部分，无须补造历史已发内容的阶段证据；例如已确认无合适C用40/60时，5条小批可取2A/3B，但原批数量与剩余配额仍由实际任务决定。成功及unknown仍保留原编号，不重发。技术写开关和直接工具调用仍需原有真实授权，审查不会解除unknown保护或自动恢复队列。

## 按真正形成的阶段注册

文件必须在该批次自有目录内；来源快照可放 `work/sources/`，不同阶段各存独立JSON文件。每次实际完成一个阶段后调用一次 `update`，不一次补齐所有阶段。修改前阶段时先注册新版本，再重新制作后阶段；旧版留存，旧审查失效。

```json
{"run_id":"原编号","content_record":{"id":"comment-01","stage":"source","artifact":"/本批目录/work/comment-01-source.json"}}
```

`source` 文件：

```json
{
  "kind": "comment",
  "target": {"feed_id":"真实原帖编号"},
  "need": "从原文与讨论读到的该条具体需求",
  "sources": [
    {"ref":"原帖URL或原读取任务号","snapshot":"/本批目录/work/sources/detail.json","coverage":["body","images","comments"],"observed_at":"2026-10-16T12:00:00+08:00"}
  ]
}
```

`kind` 为图文 `post` 或视频 `video` 时，coverage至少含 `research`。coverage是阅读观察记录，快照须实际包含对应材料；无配图、无评论也在快照中记录实际观察，不捏造。程序保存来源文件哈希并在审查/授权前复核。

`draft` 文件（完成创作后注册）：

```json
{"fields":{"content":"实际完整初稿"},"intent":"本条业务意图","humanizer_rules":"/实际源码/skills/humanizer-zh/SKILL.md"}
```

`draft` 注册时绑定当前配置版本、已确认声音样例、规则文件哈希与该条意图。Humanizer规则须起草前读取，注册并不证明已读取。帖子fields必须含 `title`、`content`、`cover_text`、`pages=[{"page":1,"text":"逐页准确图字"},...]`、`interaction`、`cta`；无互动或CTA时用空字符串，复核说明原因。

`edited` 文件（Humanizer对实际初稿编辑后另行注册）：

```json
{"fields":{"content":"实际编辑后稿"},"edit_notes":["本条实际删改或保留的具体原因"]}
```

编辑后可以保持原文字节不变，不要求改字比例；仍须留下本条实际核对的简短记录。补写事实、扩写和新增例句属于创作，应回draft阶段注册新稿，再编辑。不可倒写较差初稿制造对照。

`final` 文件（业务复核后固定）：

```json
{
  "fields":{"content":"实际编辑后稿"},
  "payload":{"feed_id":"真实原帖编号","content":"实际编辑后稿","xsec_token":"私有令牌"},
  "comment_type":"B",
  "need":"与source中同一需求",
  "selling_point_ids":["point-1"],
  "business_review":{
    "reviewer":"实际审读者",
    "source_fit":"如何回应原帖具体需求",
    "voice":"态度和作者声音的实际核对",
    "facts":"事实依据和未新增无依据事实的核对",
    "cta":"引导为何自然，或本条无需引导",
    "field_checks":{"content":"该字段实际复核结果"},
    "result":"pass"
  }
}
```

B/C须引用已核实卖点；C另须 `demand_evidence` 说明真实需求在何处。A不强塞卖点。帖子final另带 `image_reviews=[{"page":1,"asset":"/本批目录/final/1.png","visible_text":"与pages对应的准确文字","observation":"实际打开该图后的核对记录"},...]`；每页图字、每个文字字段须逐项复核，图像顺序须与payload一致。最终fields必须等于edited，最终payload文字必须与fields一致；改文案须先重新编辑和复核。

## 视频保留独立契约

`publish_with_video` 沿用同一来源、实际草稿、编辑后稿、配置版本和最终payload绑定。source用 `kind="video"`。draft、edited、final的fields均含 `title`、`content`、`interaction`、`cta` 与 `video_texts`；视频使用实际画面文字和字幕清单，不要求图文pages或image_reviews。

```json
{
  "fields": {
    "title": "准确发布标题", "content": "准确发布正文", "interaction": "", "cta": "",
    "video_texts": [{"kind":"on_screen","text":"准确画面字"},{"kind":"subtitle","text":"准确字幕"}]
  },
  "payload": {"title":"准确发布标题","content":"准确发布正文","video":"/本批目录/final/video.mp4","schedule_at":"原预约时段"},
  "video_review": {
    "asset":"/本批目录/final/video.mp4", "asset_sha256":"实际视频文件的64位SHA256",
    "video_texts":[{"kind":"on_screen","text":"准确画面字"},{"kind":"subtitle","text":"准确字幕"}],
    "visual_observation":"实际观看视频后的画面核对记录", "text_observation":"逐项核对画面字、字幕及其完整性的记录"
  },
  "business_review": {
    "reviewer":"实际审读者", "source_fit":"来源与需求核对", "voice":"作者声音核对",
    "facts":"事实依据核对", "cta":"互动与引导核对", "result":"pass",
    "field_checks":{"title":"标题核对","content":"正文核对","interaction":"无互动的原因","cta":"无CTA的原因","video_texts":"完整清单核对"}
  }
}
```

清单按实际出现顺序留存，可用多项记录每段字幕；程序比较清单字节内容，不用自动转写结果冒充观看。确实没有字幕时，清单可只有画面字或为空，但fields必须另含 `subtitle_absence_reason`，说明实际原因并在field_checks中复核。视频须为本批自有目录内的真实文件，与已有素材封存限制一致不超过64MiB。review核对给定哈希并绑定文件，改视频、改标题/正文或改清单后须重新审查。画面、字幕和事实是否正确仍由agent实际观看和审读，文件与记录齐备不证明判断正确。

## 审查与授权

```json
{"run_id":"原编号","payloads":[{"tool":"post_comment_to_feed","payload":{"feed_id":"真实原帖编号","content":"实际编辑后稿","xsec_token":"私有令牌"},"content_id":"comment-01"}],"evidence":"实际跨条审读结果与必要返工记录"}
```

`review`拒绝缺配置确认、来源、阶段链、业务复核、逐页图字或批次策略的材料。旧 `checks.humanizer_checked=true` 等布尔值不再构成准入依据。review保存配置版本、计划哈希、阶段及源文件哈希、固定最终payload指纹；`authorize`重新核对，任何必要材料、计划、配置、最终文字或素材变化都会使审查失效。

分批审查会保留仍有效的同批已审稿，合并检查类型数量与计划上限，不因只传新稿而丢掉旧记录。只审部分内容不意味着整批已验收；语义质量、跨条重复与说教口吻由agent实际审读。技术通过、内容验收、真实发送回执分开报告。

## 已授权独立评论的正常归账

若原批未知发送阻止自动队列，而用户又明确授权独立发送指定新评论，先完成上述制作、审查与授权，再用原run通过正常 `submit_task` 以 `auto_run=false` 创建真实任务。确需替换旧稿时，只通过原批 `operation_run cancel` 取消仍未执行的旧queued任务并记录替换映射；不改原unknown或成功记录，也不增加原批数量。

独立调用正常 `post_comment_to_feed` 时，使用该任务保存的准确payload和 `operation_id=task_id`。随后调用正常 `reconcile_task`（或CLI的 `reconcile <task_id>`），程序只读Go持久回执：仅接受同账号现有run内、已有授权、无owner和租约、attempts=0且auto_run=0的queued评论，逐项核对operation ID、工具、按Go相同JSON序列化计算的完整feed/content指纹、回执时间，以及成功回执的feed_id和success=true；CAS确认任务仍未变化后才保存整份回执并将attempts记为1。成功/confirmed保留原run名额，重复核对幂等；真实unknown保存完整证据并继续阻止自动队列。

未找到回执、查询失败、字段不匹配或尚未确认的回执都不能转为成功，不重发，也不恢复runner。该入口不替代发送前的内容审读与授权，不自动执行queued任务；已有unknown的原核对语义保持不变。逐条核对并记录真实发送结果后，再更新原批内容项的发布状态和旧任务到新任务的映射。


## 0.5.11：检查实际 CTA、福利和本批真人封面

账户确认这些要求后，content_profile 增加以下字段及 confirmation_sources 对应来源；完整资料存包外，不以索引代替原文：

- product_materials：非空数组，每项 {file, sha256, source_ref}，指向包含完整原始产品描述、福利和用户示范句的私人文件，读取全文；程序核对实际哈希。
- content_requirements：{post_cta: 完整正文CTA, comment_offer_phrases: [已确认福利邀请短句], comment_positioning_terms: [产品定位提示词], post_cta_components: {意图编号: 用户确认的完整意图}, min_person_covers: 每个图文交付批次至少真人封面数}。最低数大于0时必须已允许真人且提供实际参考照。
- promotion_review / cover_review 的完整字段与实看要求见[内容制作规则](../skills/xiaohongshu-operations/references/content-requirements.md)。

最终提交正文实际缺 CTA，或 B/C 缺已确认的福利意图（含经实际摘录核对的自然改写），即使 business_review.result=pass 也拒绝通过。每个图文 final 声明本次交付 batch_id 和全体 batch_content_ids；同批图文需一起审查，不能漏掉真人封面后先发其他帖子。每组独立计数，不能拿旧组真人封面补新组。参考照、第一张封面字节及完整产品材料绑定审查；发送前再次核对。程序不能辨认人脸或判断话术好坏，agent必须完成真实观察和语义审读。旧账号未配置这些字段时保持原约定，不擅自替其确认。

0.5.12保留原CTA可轻改及评论自然表达：post_cta是默认原文，福利短句与定位词是示范，不是措辞白名单。轻改CTA用final.cta_review按post_cta_components逐项保存真实excerpt和meaning_check；评论改写用promotion_review.meaning_checks.offer/positioning说明实际摘录如何满足已确认意图。程序核对字段、实际摘录与文件，agent核对语义；缺摘录、摘录不在实际文案或只填通过仍被拒绝。已有原句与示范表达沿用原简短记录，不增加审批。
