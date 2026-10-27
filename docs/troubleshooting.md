# jshERP 接口自动化——问题排查手册(troubleshooting)

> 记录框架搭建与用例开发全过程遇到的问题、根因与解决方法,按主题分类。
> 每条含:【现象】【原因】【解决】【预防】。新增问题请按此结构追加。
> 缺陷类问题(产品本身的 bug)见禅道与《接口测试用例.xlsx》关键词追踪,本文档侧重**框架/环境/工程**层面。
> 更新日期:2026-10-27

---

## 一、环境与连接类

### 1.1 PyMySQL 连不上 jsherp 数据库(Unknown database 'jsh_erp')

- 【现象】`localhost:3306` 连接成功但库里没有 `jsh_erp`,列出的是一堆无关项目库。
- 【原因】Windows 本机 3306 跑着另一个 MySQL;jshERP 的 MySQL 容器端口映射在 **WSL2 侧**,需求文档里"localhost:3306 映射至宿主机"的说法与实际部署不符。
- 【解决】用 WSL2 IP 连接:框架 `config/env.yaml` 里 `db.host: auto-wsl`,`common/config.py` 通过 `wsl -d Ubuntu hostname -I` 取第一个 IP 自动解析。
- 【预防】WSL 重启后 IP 会变,不要写死 IP;`env.local.yaml` 里写死值优先级更高,仅作逃生通道。

### 1.2 DB 查询读不到被测系统刚写入的数据(间歇性"API 成功但 DB 查 0 行")

- 【现象】接口建单成功,DB 查该单返回 0 行;**单独重跑该用例又通过**。
- 【原因】PyMySQL 默认 `autocommit=False`,连接的第一个查询开启 REPEATABLE READ 快照事务后永不提交,后续所有查询停在旧快照上。
- 【解决】`utils/db.py` 连接加 `autocommit=True`(只读断言连接也必须开)。
- 【预防】症状特征是"顺序跑失败、单跑通过",见到这个组合先查事务快照。

### 1.3 WSL 命令执行失败(execvpe bash failed)

- 【现象】`wsl -e bash -lc "docker ..."` 报错。
- 【原因】WSL 默认发行版是 docker-desktop,不是 Ubuntu。
- 【解决】显式指定发行版:`wsl -d Ubuntu ...`;Redis 取证用 `docker exec jsherp-redis redis-cli -a <密码>`,密码取自容器启动参数 `--requirepass`。
- 【预防】涉及 WSL 的命令一律带 `-d Ubuntu`。

### 1.4 Allure 报告双击 index.html 打不开

- 【现象】生成 `reports/allure-report` 后直接双击 index.html 白屏。
- 【原因】Allure 报告用 fetch 加载数据,必须走 HTTP。
- 【解决】`allure serve reports\allure-results`(快速看)或 `allure generate ... --clean` + `allure open reports\allure-report`(持久化)。`pytest.ini` 已配 `--clean-alluredir`,results 始终是最近一轮数据,每次跑完需重新 generate。

---

## 二、请求与会话类

### 2.1 同一秒内连续请求被强制登出(响应纯文本 loginOut)

- 【现象】接口偶发返回非 JSON 的 `loginOut`,无任何提示;限速 1100ms 仍复现。
- 【原因】产品缺陷 DEF-EXP-06:同用户+同模块+同 IP 同一秒第二次操作,防攻击机制删除 Redis 会话 userId 强制注销;时序敏感非必现。
- 【解决】HttpClient 统一限速 `request_min_interval: 2.0`。
- 【预防】任何新增并发/批量场景(如并发取号用例)先评估是否触发该机制。

### 2.2 同账号登录互踢

- 【现象】用 jsh 账号做"登录成功"反例后,conftest 共享会话失效。
- 【解决】全程共用 `auth` fixture 会话(session 级,登录一次);**登录/鉴权反例一律不成功登录 jsh**——如"连续失败 N 次后正确密码仍可登录"这类步骤直接省略或用自建测试用户。

### 2.3 无 token / 坏 token 的断言方式

- 【现象】无 token 访问业务接口,HTTP 状态码 200/500 不统一(且 getUserList 已从文档记录的 200 漂移为 500)。
- 【解决】鉴权失效断言只看响应体是否为纯文本 `loginOut`(`HttpClient.is_login_out`),不依赖状态码;登录成败断言落 `msgTip`(业务失败时 HTTP 与 code 都可能是 200,REQ-API-004)。

---

## 三、接口报文与业务口径类

### 3.1 单据报文基本约定

- 入库单 `totalPrice`/`changeAmount` 为**负值**,出库单为正值;
- `info`/`rows` 是**JSON 字符串**(不是嵌套对象);
- 删除/审核类接口 `ids` 参数为**带尾逗号的字符串**(如 `"123,");batchSetStatus/batchSetLastDebt 传数组形式返回"未知异常"500。

### 3.2 欠款(debt)的三个口径

- `jsh_depot_head.debt/last_debt` 列**不落库**(NULL),接口返回的 debt 是计算值(含税合计−本次付款)→ DB 侧欠款断言改断 `total_price/discount_last_money/change_amount`;
- 收款核销的联动通道是 `GET /depotHead/debtList`:`debt`=未核销余额、`finishDebt`=已核销,**足额核销后该行直接从 debtList 消失**;
- `jsh_supplier.all_need_get/all_need_pay` 列恒 NULL,不随业务联动,不能作为应收断言通道。

### 3.3 财务单明细与余额

- 明细 `each_amount` 落库取**绝对值**(付款传 -80 落 +80),收支方向看头表 `total_price` 符号;
- 未审核财务单不动 DB 余额(F9),**审核后 DB `current_amount` 仍不动**——页面余额走 `listWithBalance` 实时计算,与 current_amount 列不同源(环境含历史账户余额 829/-1681,全局对账不可用,账户统计用 name 过滤单账户对账)。

### 3.4 报表接口是 GET 平铺参数(不是 search JSON)

- `getInOutStock/buyIn/saleOut/retailOut/findStockWarningCount` 需要 `mpList` 空串、`materialParam`;
- `findInOutDetail`/`findInOutMaterialCount` 需带 **remark 空串**(缺它直接 500"未知异常");
- `getListWithStock` 需带 **zeroStock**;
- `getStatementAccount` 用 `supplierType`(客户/供应商)+`organId`;
- **getInOutStock/getListWithStock 必须显式传 `depotIds`**(见 5.2,不传时走服务端"当前用户有权限仓库"隐式逻辑,该逻辑失效即 510/空集);
- 行字段口径:`thisSum`=结存、`inSum`=累计入、`outSum`=累计出(公式 `thisSum=inSum-outSum`);buyIn 行 `inSum/inSumPrice`;saleOut/retailOut 行 `outInSumPrice`=金额。

### 3.5 提取数据 ID 的可靠路径

- 供应商/客户/会员:走 `GET /supplier/list + search`(按名过滤);**`/supplier/findBySelect_*` 缺参会返回"未知异常"(NPE),勿用**;
- 账户:`GET /account/findBySelect` 返回键为**大写** `Id/AccountName`;
- 单据明细:`GET /depotItem/getDetailList?headerId=`;注意 `getDetailByNumber` 返回的 `materialsList` 是**全量可选商品列表**,不是单据明细;
- 调拨的调入仓 `anotherDepotId` 在**明细行**上(info 上传报 8500005"调入仓库不能为空")。

### 3.6 会员预存的正确操作姿势

- 充值/消费走**收预付款财务单**(type=收预付款,billNo 前缀 SKQ),`advance_in` 实时增减(500→消费 480),删充值单实时回 0;
- **禁止用 `batchSetAdvanceIn` 做充值**:直接设值会被实时重算覆盖(DEF-EXP-01,设 970 建充值单后归 500,期初丢失);
- 预存不足时零售被 8500016 拦截。

---

## 四、用例编写与断言类

### 4.1 不轻信用例集预期,先探针取证

采购入库数量 0/负/小数/1e9、负单价全部放行(用例集预期"被拒");供应商重名/空名可建;商品空名/空条码可建;收入单缺收支项目可建。**统一采用"缺陷回归式断言"**:断当前实际行为 + yaml/注释写明"修复后翻转",用 `expect_created` 等开关预留翻转位。

### 4.2 探针脚本自身的两类误判(教训)

- **操作顺序污染**:探针里先做了反审核再测"已审核订单删除",误判为"放行"——8500011 实际对所有单据一致。结论:**验证规则的单据必须独立新建,不复用被其它操作改过状态的单**;
- **异常分支短路**:期望触发"缺 type 的 NPE",却传了空明细,被更早的 9500003(明细不能为空)拦截。结论:触发深层分支前先确认前置校验链。

### 4.3 顺序链用例的顺序错位

- 【现象】"重复审核"断言 status=1 失败(实际=3)。
- 【原因】部分转单用例先把状态推到 3,重复审核用例排在它后面;且插入新版方法时忘删旧版,同名用例执行两次。
- 【解决】顺序链方法名带序号(执行序即编号序),增删方法后核对全文件;断言"状态不变"类用例放在状态变更用例之前。

### 4.4 DB 断言的取值类型

- `delete_flag` 是字符串 `'0'`/`'1'`(与 int 比较恒 False);
- 金额列是 `Decimal`(直接 float() 可,与 int 字面量比较注意);
- 字段可能不落库(NULL):`debt/last_debt/discount_last_money(漏传时)`,断言前先确认口径。

### 4.5 yaml 数据驱动约定

- 参数矩阵进 `data/*.yaml`,断言逻辑留代码,`parametrize(ids=case_id)` 展开;
- 超长字符串用 `char+len` 规格生成,不在 yaml 手写;
- 实测取证结论写在 yaml 头部注释(带日期);
- 修复类用例用 `expect_created` 等布尔开关预留预期翻转。

---

## 五、数据管理与清理类

### 5.1 残留自愈机制(residue_guard)

- conftest 的 `residue_guard`(session autouse)每轮开跑前调 `utils/cleanup.cleanup_residue`:按自动化命名特征扫描(EXPF 前缀资料 + CGRK/XSCK 等单据前缀),接口删除为主,系统规则拒删的(8500011/8500013,已完成/部分执行单)用 SQL 逻辑删除兜底;
- 可独立运行:`python -m utils.cleanup`;
- **教训:fixture 曾定义为普通 session fixture 且无任何用例引用,守卫从未执行**,导致残留跨轮累积(见 5.3)。autouse 必须显式写。

### 5.2 fixture 造数失败会跳过清理

- 【现象】开发期多轮失败运行后库里有活残留(单据/资料)。
- 【原因】teardown 写在 fixture 的 `yield` 之后,**造数阶段(yield 前)断言失败时清理代码不会执行**;另有运行被强杀的情况。
- 【解决】守卫自愈兜底;关键 fixture 的造数失败路径可考虑 try/except 内清理后再 raise(视重要性)。

### 5.3 清理工具误删事故(2026-10-27,已恢复)

- 【现象】cleanup 第一版把 7 个初始往来单位(含预存 970 的会员)置了逻辑删除。
- 【原因】SQL SELECT 漏取名称列,"空名分支"把所有行误判为空名。
- 【解决】按 id 精确恢复;工具立铁律:**候选行必须取到真实名称且匹配自动化特征,取不到名称一律跳过并告警,宁漏勿误删**(已写进源码注释)。
- 【预防】清理类工具的每条 SQL 自审:SELECT 是否带了做匹配所需的全部列。

### 5.4 状态机死角:已完成/部分执行单接口删不掉

- 8500011(仅未审核可删)/8500013(仅已审核可反审核)拦截,teardown 对这类单走 SQL 兜底;teardown 删除失败记录 log 不中断(避免掩盖测试失败)。

---

## 六、环境级缺陷与级联故障

### 6.1 DEF-EXP-09:仓库授权列表膨胀导致报表接口整体失效

- 【现象】getInOutStock 返回 510"请选择仓库,再进行查询"(显式传单个 depotId 也无效)、getListWithStock 空集;**同一份代码白天绿、晚上挂**(渐进恶化)。
- 【原因】jshERP 新增仓库自动追加进 `jsh_user_business`(UserDepot)授权列表,**删除仓库不回收**;每轮测试 +10~20 个 ID,累积 200+ 后"当前用户有权限的仓库"解析服务失效。取证:Redis 会话只有 userId/clientIp(排除会话缓存)、后端日志无业务 SQL(排除查询问题)、显式传 `depotIds` 后立即恢复(实锤授权解析)。
- 【解决】三重:①UserDepot value 重置为 `[14][15][17]`;②ReportApi 的 `in_out_stock/list_with_stock` **显式传 depot_ids**,不依赖隐式默认;③residue_guard 改 autouse 遏制膨胀源。
- 【预防】凡接口存在"隐式默认范围"类参数(默认=用户授权/配置),自动化一律显式传参;环境数据(授权表、配置表)纳入每轮巡检。

### 6.2 DEF-EXP-08:NULL 字段单据使首页统计 500(缺陷级联)

- 【现象】全量回归中首页统计用例失败,后端 `BigDecimal.add` NPE。
- 【原因】PUR-023(漏传 discountLastMoney)建单成功(DEF-EXP-03)→ 字段落 NULL → 首页统计累加 NPE;且该用例曾未登记 teardown,PUR-023 的触发单残留 3 张,跨模块污染了报表用例。
- 【解决】PUR-023 补登记 teardown;新增 **DEF-EXP-08 级联回归用例**(构造触发单→断言 500→删单→断言恢复 200),自含环境清理。
- 【预防】"制造脏数据的用例"必须保证数据随 teardown 走;跨模块影响的用例开头自清同类残留。

### 6.3 禅道记录与追踪

已实证缺陷 19 条(DEF-EXP-01~03、06~09 及校验缺失系列),清单见禅道提交记录;每条的关键词含用例编号(PUR-023/BAS-002 等),用例集 Excel 的"缺陷编号"列回填禅道号形成追踪闭环。

---

## 七、快速排障索引

| 症状 | 先查 |
| --- | --- |
| API 成功但 DB 查 0 行,单跑通过 | db.py autocommit(1.2) |
| 响应体纯文本 loginOut | DEF-EXP-06 限速 / 会话失效(2.1) |
| code 510"请选择仓库" | 显式传 depotIds + 授权列表检查(6.1) |
| code 300"数据查询异常" | /depotHead/list 缺 type+subType(3.4) |
| code 500"未知异常" | 缺参 NPE:findBySelect_*/remark/ids 数组(3.5、缺陷 15) |
| 报表全挂/空集 | 授权列表膨胀(6.1) |
| 用例顺序跑挂、单跑过 | DB 快照(1.2)或顺序链错位(4.3) |
