// ============================================================
// Zabbix Webhook Media Type 脚本模板（架构文档 §11.3）
// 在 Zabbix：Alerts → Media types → Create media type → Type: Webhook
// 需要在 UI 中定义的参数（Parameters）：
//   ops-doctor_url    = http://<ops-doctor主机>:8000/webhook/zabbix
//   opsdoctor_token  = <data/webhook_token 文件内容>
//   event_id        = {EVENT.ID}
//   event_kind      = {EVENT.OBJECT}（1=PROBLEM 0=RECOVERY 需映射）
//   host            = {HOST.NAME}
//   problem         = {EVENT.NAME}
//   severity        = {EVENT.SEVERITY}
//   occurred_at     = {EVENT.DATE}T{EVENT.TIME}
//   r_event_id      = {EVENT.RECOVERY.ID}
// 注意：切勿把 opsdoctor_token 打印到日志；先在测试主机验证。
// ============================================================

var params = JSON.parse(value);

// Zabbix 事件对象映射：OBJECT_PROBLEM=1, OBJECT_RECOVERY=0（以实际版本文档为准）
var kindMap = { "0": "RECOVERY", "1": "PROBLEM" };

var req = new HttpRequest();
req.addHeader('Content-Type: application/json');
req.addHeader('X-OpsDoctor-Token: ' + params.opsdoctor_token);

var payload = {
    source: 'zabbix',
    event_id: String(params.event_id),
    // 恢复事件必须携带【原问题事件ID】作为 event_id，保证与 PROBLEM 关联
    event_kind: kindMap[String(params.event_kind)] || 'PROBLEM',
    host: params.host,
    service: params.service || '',
    problem: params.problem,
    severity: params.severity,
    occurred_at: params.occurred_at,
    metric: params.metric || '',
    value: params.value ? Number(params.value) : null,
    threshold: params.threshold ? Number(params.threshold) : null,
    tags: {env: 'lab', system: 'mall'}
};

var result = req.post(params.ops-doctor_url, JSON.stringify(payload));
var status = req.getStatus();
if (status < 200 || status >= 300) {
    throw 'OpsDoctor request failed: HTTP ' + status + ' body=' + result;
}
return JSON.stringify({ok: true, response: result});
