# Foreground 服务 ready/ownership 竞态回归

## 已观察失败与根因

最终全量测试曾出现754项中1项失败：
`BootstrapTests.test_foreground_host_service_is_ready_stoppable_and_preserves_binding`
断言stop返回stopped，实际not_managed。不能归咎测试环境或反复重跑到绿。

实际实现先启动native子进程，再安装信号处理器、计算runtime指纹和原子发布
`managed-process.json`。子进程的`/runtime`可以在记录落盘前ready；旧stop把
“记录暂时不存在”误当成“没有托管服务”，即使supervisor.lock已经被启动者持有。

## 通用修复

缺记录的stop现在先检查supervisor锁：无持有者仍返回not_managed；有持有者
最多等待5秒发布记录。等待结束后仍完整执行原有配置、PID、runtime SHA和
实际命令核验，之后才向被验证的自有supervisor发送一次SIGTERM。超时返回
ERR_SERVICE_STARTUP_PENDING，不发信号，不杀未知进程，不动Chrome或账号队列。
锁在等待中释放且无记录时退出，不能无限等待或用超时作为所有权证据。

## 确定性验证

`tests/test_service_bootstrap.py`新增：
- `test_stop_waits_for_ready_child_ownership_record`：临时账号、真实native子进程，
  在发布记录处使用文件barrier，在stop等待处使用threading.Event同步；不靠随机sleep撞竞态。
- `test_stop_starting_timeout_does_not_signal`：受控时钟超时且绝无os.kill。
- `test_stop_startup_lock_released_without_record_does_not_signal`：锁释放但无记录，绝无os.kill。

对第一项注入旧“只看文件存在”行为可稳定失败（stop未等待ownership）；恢复修复后
bootstrap全组13项通过。原先stopped断言保留，外部进程身份校验没有放宽。
所有启动/停止均限测试生成的临时服务，不对真实运营实例执行。
