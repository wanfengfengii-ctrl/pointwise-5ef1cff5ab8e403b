# 事故窗口排队核查材料

本包是封存日志分析工作区。Python 标准库即可读取和核验，无外部服务或数据。source.py 只读取材料、建立确定性线性表达式和核验给定时钟；audit.py 只检查证书，不搜索时间轴或计算最优值。保留这两个文件、checks.py、本文及 data/ 的内容与断言；新增本批分析代码，允许更新运行清单。

## 可信业务口径

data/incident.json 是唯一主事故：三条目标请求、三个服务、六台主机。offsets 的闭区间来自同一事故窗口的同步测量，单位毫秒。reference 主机为时间基准，偏移严格为零。每台主机窗口内偏移固定，无漂移、重置、事件丢失或重复。真实时刻 = local_ms - 该主机偏移；偏移允许非整数有理数，不能向整数或浮点网格取整。窗口描述是选取封存材料的背景，不额外约束事件真实时刻。

event_file 指向同目录完整 CSV：event 是唯一事件 ID，request 是请求 ID，host 是记录主机，local_ms 是整数毫秒，kind 为 enqueue/start/send/receive，service 为排队所属服务。queues 明确给出每段排队的 enqueue/start 事件；服务与请求由这两个事件共同确定。所有 queues 都属于 requests 指定的目标集合，每段恰计一次，包括零时长。按服务累加真实 start-enqueue 得到累计排队耗时，不把网络传输时间计作排队。links 是可靠匹配的发送/接收事件，发送不晚于接收；排队入队也不得晚于开始。探测请求仅提供时钟因果关系，不参与排队汇总。除这些关系和同步区间外没有额外时差假设，不从 CSV 行序推断事件发生顺序。

整批事件必须共享一组主机偏移。各服务下界和上界分别是在同一联合可行域上可达到的最小值和最大值，不把单事件或单请求区间相加；不同端点可以使用不同时间轴。可能最大者指存在一组完整可行偏移，使该服务耗时不小于其他所有服务，允许并列。必然唯一最大者指在每组完整可行偏移下都严格大于其他每个服务；某个服务不能必然唯一最大时，须给出另一服务不小于它的完整反例。区间重叠不代表最大者不确定，几个服务各自的端点不能拼作联合见证。

data/certain.json 与其 CSV 是两主机正常对照：alpha 的锐界为 [2,6]，beta 为 [0,4]，区间重叠但 alpha 恒比 beta 多 2，只有 alpha 可能最大且必然唯一最大。data/uncertain.json 中 alpha、beta 的锐界均为 [2,6]，gamma 恒为 5；B 偏移为 0 时三者为 6、2、5，为 4 时为 2、6、5，为 2 时为 4、4、5。三者都可能最大，没有必然唯一最大者；gamma 只在 B 属于 [1,3] 时可能最大，仅查看原可行域的两个端点会漏掉它。data/conflict.json 的两条请求分别可解释，但第一条要求 B<=0，第二条要求 B>=1，整批矛盾。不可行时只给冲突证据并停止归因，不交部分服务边界。

## 入口与结果

python3 source.py data/incident.json 展示真实事件及来源行号。
python3 checks.py normal 核对读取、时延计算、公开对照证书及错误证据拒绝。
分析入口固定为 python3 analysis.py 输入JSON路径 输出JSON路径，须支持上述四份材料；重新读取材料，不硬编码主事故结论。对主事故调用还生成 REPORT.md；其他三份调用不覆盖主事故报告。交付 result.json、REPORT.md、analysis.py 及必要的新增分析模块。报告解释本批同步与调用关系如何收紧边界、哪些瓶颈结论确定或仍可能，并引用具体 CSV 行号、事件 ID 和 JSON 同步记录位置；这里的瓶颈仅指目标请求的累计排队耗时最大，不声称吞吐能力或事故的其他原因。

全部结果及证书用整数或分数字符串表示，禁止浮点容差。source.linear(d) 返回两个对象：rows 为约束 ID -> (稀疏系数 a,右端 b)，含义 a*offset<=b；totals 为服务 -> (常数 c,稀疏系数 a)，含义 Q=c+a*offset。sync:主机:lo/hi、queue:队列ID、link:链路ID 是稳定证据 ID。source.witness(d,offsets) 精确验证全部约束并返回各服务耗时。完整时间轴见证用包含全部主机的偏移对象表示，逐事件真实时刻由该对象和 CSV 唯一重建，不必重复存储。

可行结果：
{"status":"ok","witness":完整偏移对象,"services":{服务:条目}}
条目包含 lo、hi、lo_witness、hi_witness、lo_proof、hi_proof、maximum、dominance。边界见证须达到所报端点。
maximum 为 {"possible":true,"witness":完整偏移对象}，或 {"possible":false,"proof":不可行证书}。
dominance 为 {"always_unique":true,"margins":{其他服务:{"value":严格正有理下界,"proof":证书}}}，证明本服务减去相应服务始终至少为 value；下界无需最紧。否则为 {"always_unique":false,"witness":完整偏移对象,"rival":不小于本服务的另一服务}。
不可行结果只有 {"status":"infeasible","proof":不可行证书}。
checks.control_result() 给出 certain 小材料的完整结果与证书示范，主事故没有预置答案。

证书都是 [[约束ID,非负有理权重],...]，ID 不重复。audit.dual 精确累加不等式：lo_proof 要得到 -a*offset<=c-lo，hi_proof 要得到 a*offset<=hi-c；右端更强也允许。不可行证书须得到 0<=严格负数。排除某服务可能最大时，在基础约束外加 max:本服务:另一服务，表示 Q另一服务-Q本服务<=0，须证明这些比较同时成立不可行；可用 audit.candidate_rows(d,服务) 取得行。dominance 中的证书须证明 Q另一服务-Q本服务<=-value。不能只附可行见证而不证明边界或排除结论；不要求最小冲突或完整理论证明。

## 验证与演示

新增 python3 validate.py 作为可重跑独立核验入口：用小材料的有理顶点枚举互核边界及最大者判断，不能调用分析器内部求解函数充当参照；检查全部 local_ms 同加一个整数、CSV 行序重排后结论不变；把 certain 的 B 同步上界改为 3 时，重算应为 alpha [3,6]、beta [1,4] 且 alpha 仍必然唯一最大。变换使用临时副本，原材料不改。独立核验可调用分析入口和原 audit，但期望值计算不得调用分析器。

python3 audit.py 输入JSON路径 结果JSON路径 独立核对现有证据。
交付时运行清单 verify 改为 ["python3","checks.py","report"]，demo 改为 ["python3","checks.py","demo"]，setup 仍为空。report/demo 都会重跑四份分析并经原 audit 校验证据，检查主事故报告与材料不被改写，展示真实边界与归因。report 还会执行新增的 validate.py；demo 展示核验后的四份实际结果。运行清单保持 version=1、kind="cli"、setup 为 argv 数组列表、verify/demo 各为一个 argv 数组。
