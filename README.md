# jshERP 接口自动化(项目一·阶段三)

管伊佳 ERP(JSH_ERP)V3.6 接口自动化框架:Python 3.11 + Requests + Pytest + Allure + logging + PyMySQL + PyYAML。
上游交付物:《管伊佳ERP接口测试用例.xlsx》(GYJ-TC-API-001 V1.1,191 条)、Postman 冒烟集(postman/)。

## 环境准备

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

配置:`config/env.yaml` 为基线(非敏感);口令与 DB 地址放 `config/env.local.yaml`(已 gitignore)。
DB 的 `host: auto-wsl` 会自动解析 WSL2 IP(容器 3306 仅绑定在 WSL 侧,Windows 本机 localhost:3306 是另一个 MySQL)。

## 运行

```bash
.venv\Scripts\python -m pytest -m smoke        # 冒烟主链路(9 条,对应 Postman 集)
.venv\Scripts\python -m pytest -m regression   # 回归套件(状态迁移/参数矩阵/查询边界)
.venv\Scripts\python -m pytest -m db_check     # 数据库对账
.venv\Scripts\python -m pytest                 # 全量(65 条,约 10 分钟,受 2s 限速)
.venv\Scripts\allure serve reports\allure-results   # 查看报告(需本机安装 Allure CLI)
```

## 模块与标记(mark)对照

| 目录 | 模块 | 标记 |
| --- | --- | --- |
| testcases/smoke/ | 冒烟主链路 | smoke |
| testcases/test_login/ | 登录鉴权 | smoke + regression |
| testcases/test_basic/ | 基础资料/商品 | smoke + regression |
| testcases/test_purchase/ | 采购参数矩阵、状态迁移与转单链 | regression(+db_check) |
| testcases/test_sale/ | 销售状态迁移、库存边界两态 | regression(+db_check) |
| testcases/test_common/ | 通用查询分页边界(yaml 矩阵) | regression |
| testcases/test_finance/ | 财务核销/优惠/余额时点 | db_check(+regression) |
| testcases/test_retail/ | 零售散客/会员/预付/折扣/退货 | regression(+db_check) |
| testcases/test_depot/ | 调拨守恒/两态、其它出入库、组装 | regression(+db_check) |
| testcases/test_report/ | 报表对账(进销存/统计/对账/首页) | db_check(+regression) |

## 框架结构

```text
config/        env.yaml(基线)+ env.local.yaml(敏感,不入 Git)
common/        config.py(合并加载)、client.py(X-Access-Token/限速/日志)、asserts.py(分层断言)、dataloader.py(yaml 用例加载)
api/           auth/supplier/material/depot/account/depot_head 六组接口封装
data/          yaml 用例数据(login_negative 登录矩阵、purchase_param_matrix 采购参数矩阵),加用例=加行
utils/         db.py(MySQL 只读)、logger.py、dynamic.py(EXPF 动态数据)
testcases/     smoke/ 冒烟链路;test_login/ 鉴权;test_basic/ 基础资料;test_purchase/ 采购矩阵
conftest.py    cfg/db/client(裸)/auth(唯一会话)/env_check(开关快照)fixture
pytest.ini     标记:smoke/regression/permission/db_check/manual
Jenkinsfile    gyj-api-pytest Job 入口
```

数据驱动约定(计划 3.3 约定 3):参数矩阵写 `data/*.yaml`,`pytest.mark.parametrize` 展开
(ids 显示用例编号);**断言逻辑留在代码,业务数据在 yaml**;超长字符串用 `char*len` 规格生成不手写;
取证日期与实测结论写在 yaml 头部注释,修复类用例用 `expect_created` 等开关预留预期翻转。

## 关键约定(踩坑结论,勿违反)

1. **请求限速 2.0s**:DEF-EXP-06——同用户+同模块+同IP 同一秒第二次操作会被强制注销(响应 `loginOut`),实测 1100ms 仍不安全。
2. **同账号登录互踢**:全程共用 `auth` 会话;反例用例绝不成功登录 jsh(AUTH-018 因此不做"正确密码仍可登录"步骤)。
3. **业务失败判定**:HTTP 与 code 均可能仍为 200(REQ-API-004),断言必须落到 msgTip/具体错误码;鉴权失效只断响应体 `loginOut`(状态码 200/500 不统一,实测 getUserList 已从 200 漂移为 500)。
4. **数据自建自清**:业务数据一律 `EXPF+随机` 动态命名,用例内 teardown 删除;DB 仅作断言读取(autocommit 必须开,否则停在旧快照读不到新数据)。
5. **报文要点**:入库单 totalPrice/changeAmount 为负、出库单为正;info/rows 是 JSON 字符串;ids 参数带尾逗号(**batchSetStatus 的 ids 数组形式会"未知异常"**);DB 的 delete_flag 是字符串 '0';欠款(debt)不落库,是接口计算值;核销联动看 `depotHead/debtList` 的 debt(未核销余额)/finishDebt(已核销),足额核销后行从 debtList 消失;财务明细 each_amount 落库取绝对值,方向看头表 total_price 符号;`/depotHead/list` 的 search 缺 type/subType 会 code 300;**报表接口是 GET 平铺参数(不用 search JSON)**,getInOutStock 行 thisSum=结存/inSum=累计入/outSum=累计出,findInOutDetail 缺 remark 参数报 500;调拨的调入仓 anotherDepotId 在明细行;会员预存走"收预付款"财务单,advance_in 实时增减。
6. **缺陷回归用例**(断言当前缺陷行为,修复后需翻转预期):BAS-002/003(供应商无唯一性/必填校验)、GOODS-020(空条码可入库)、采购数量 0/负/小数/超大与负单价全放行(PUR-004~008)、财务缺 type 触发 NPE(FIN-008)、非法 search JSON 触发 500(COM-009)、findBySelect_* 缺参"未知异常"、batchSetAdvanceIn 直接设值被实时重算覆盖(DEF-EXP-01)。
7. **状态机结论**(F6 实测复核):已审核单删除一律 8500011(订单与入库单无差异);8500013 仅已审核可反审核;转单支持部分执行(源单 1→3→2),超量转单放行(R3)。
8. **残留自愈**:conftest 的 residue_guard 每轮开跑前按自动化命名特征清上一轮残留(EXPF 前缀+单据前缀),工具 `python -m utils.cleanup` 可独立运行;匹配不到名称的行一律跳过(宁漏勿误删,2026-10-27 误删事故教训)。

## 已知遗留

- `/supplier/findBySelect_*` 系列缺参即"未知异常"(NPE),参数语义待 Fiddler 抓包确认后补测(对应用例集 12_接口回填记录)。
- 权限反例(permission 标记)与报表对账用例按用例集排期分批补充。
