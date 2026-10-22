# 云端交接快照完整性检查

这是静止既有快照的本地核对工具，不是备份服务、恢复工具或允许外发的授权。
不初始化账号，不创建/迁移数据库，不覆盖回执，不上传文件，不重启浏览器。
合法新账号初始化不使用此交接检查，不能把它的“缺历史”错误套到首次安装。

## 使用前提

先按已有升级规则停止新领取并等待在途动作落盘。对已经生成且确认静止的
完整私有快照运行，不对活动库运行。SQLite 本体和任何非空 WAL/journal/SHM
同时出现时拒绝检查，不能自行删除sidecar；先按原支持流程取得一致快照。
本工具不调用业务运行时构造器，不会因缺库自动创建空库。

显式生成（output必须是新文件，父目录必须已存在）：

    python scripts/cloud_handoff.py manifest --config /private/snapshot/config.json --output /private/handoff.json --required-file /private/snapshot/source-pool.json

再次只读核验：

    python scripts/cloud_handoff.py check --manifest /private/handoff.json

manifest为0600私有JSON；不传入连接凭证，不保存完整配置、payload、cookie、
authorization、claim token或xsec_token。配置只存文件指纹，账号标识、绝对路径、
unknown操作ID属于私有交接元数据，不能把manifest放进公共仓库或发行包。
stdout只输出错误码/数量/账号hash，不输出未知笔记正文、token或文件内容。

## 检查覆盖

- 精确配置账号，现有 cloud-actions / cloud-batches / cloud-pacing 三库存在。
- 只读SQLite quick_check、schema摘要、动作/限频事件count/max ID、批次内容阶段seq高水位，现有账号历史。
- 动作状态计数、unknown/dispatching操作ID、unknown任务投影、限频attempt和unknown/in-flight步骤ID。
- 三库及配置文件SHA/大小，VERSION及runtime/scripts/schemas/skills/plugin元数据指纹。
- 绑定动作media_manifest全部素材、当前profile的face_reference和product_materials。
- `--required-file`指定的附加材料。完整来源池、source/draft/edited/final阶段稿、
  操作截图、授权原消息证据等不保证自动发现，必须显式列入。缺少这些列表时，
  报告匹配仅代表已覆盖范围匹配，不证明整个业务资料完整。

缺库、空历史、错误账号、损坏数据库、必需素材缺失或hash不符都会拒绝生成。
已有manifest发现变化返回review_required，并禁止把“匹配旧快照”当作外发许可。
源码升级独立标为CODE_VERSION_REVIEW_REQUIRED，表示需要兼容性复核，不能宣称
记录丢失或数据损坏。修复投影和核对平台事实仍按各自正式机制执行。

## 证据与不可证明边界

manifest/check均用SQLite mode=ro、immutable=1、query_only。逐库事务只保证单库
静止快照读取；前后文件hash和sidecar检查发现常见并发变化，不构成跨库原子快照，
也不能证明不存在变化后还原的ABA。运行者必须保证文件静止且路径可信，不能把
这项检查当作对并发恶意文件替换的安全隔离措施。

本版使用同机绝对路径，不自动改写账号或素材路径，不提供跨机迁移，也不证明
备份已上传异机。即使全部匹配，也不能证明这是最新账号状态；缺少已知最新外部
检查点时，旧但自洽的快照仍可能通过。未知仍未知，不补写成功或重新提交。
