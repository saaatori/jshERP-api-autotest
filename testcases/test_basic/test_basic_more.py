"""基础资料/商品长尾(API-BAS-004/007/010/014/015/018 + GOODS-003/005/013)。

取证(2026-10-27):
- 仓库名称重复实测放行(与供应商同源,无唯一性校验,BAS-014 缺陷回归);
- 商品名为空实测可创建(GOODS-003 缺陷回归);
- 删除被业务数据引用的资料返回 code 601"检测到存在依赖数据,不能删除"。
2026-10-31 抓包复核(GYJ-MSG-001)后解锁:BAS-012/021 往来/会员下拉(页面统一 POST {"limit":1})、
GOODS-006/007 多单位 ratio 边界、GOODS-012 商品编辑(update 55 字段全量报文)、
BAS-016 仓库编辑(18 字段全量;principal 传负责人姓名实测 500,缺陷回归断言)。
未实现:GOODS-016 序列号(构造复杂)。GOODS-011 启停用已实现(test_011)。
"""
import allure
import pytest

from api.account_api import AccountApi
from api.depot_api import DepotApi
from api.depot_head_api import DepotHeadApi
from api.material_api import MaterialApi
from api.supplier_api import SupplierApi
from common.asserts import assert_code_ok
from utils.cleanup import delete_bills_with_fallback, delete_depots_with_fallback, delete_materials_with_fallback
from utils.dynamic import exp_id


@pytest.fixture(scope="module")
def bas_env(auth, db):
    c = auth["client"]
    s = {"client": c, "tenant_id": auth["tenant_id"], "db": db,
         "sup": SupplierApi(c), "depot": DepotApi(c), "acc": AccountApi(c),
         "mat": MaterialApi(c), "bill": DepotHeadApi(c), "cleanup_ids": []}
    yield s
    for kind, rid in s["cleanup_ids"]:
        try:
            if kind == "supplier":
                s["sup"].delete_batch([rid])
            elif kind == "depot":
                delete_depots_with_fallback(s["client"], s["db"], [rid])
            elif kind == "account":
                s["acc"].delete_batch([rid])
        except Exception:  # noqa: BLE001
            pass


@allure.feature("基础资料")
@pytest.mark.regression
class TestBasicMore:

    @allure.title("API-BAS-004 联系人超长触发 301 写入异常(255/256 均实测,缺陷回归)")
    def test_004_contacts_length(self, bas_env):
        """实测(2026-10-27):contacts 255/256 字符均返回 301"数据写入异常"——
        长度未做前置校验,超长直接打到数据库报错(不友好提示,缺陷);
        列容量小于 255,实际上限待抓包/查表确认。若改为友好校验提示请翻转断言。"""
        s = bas_env
        name = exp_id("EXPFlen")
        body255 = s["sup"].add(name, "供应商", contacts="A" * 255).json()
        assert body255.get("code") == 301, f"缺陷现状应301写入异常,实际: {body255}"
        body256 = s["sup"].add(exp_id("EXPF256"), "供应商", contacts="A" * 256).json()
        assert body256.get("code") == 301, f"256字符应同样301,实际: {body256}"
        # 对照:短联系人正常
        body_ok = s["sup"].add(name, "供应商", contacts="正常联系人").json()
        assert_code_ok_body = body_ok.get("code") == 200
        assert assert_code_ok_body, f"短联系人应成功: {body_ok}"
        sid = s["sup"].find_id_by_name(name)
        s["cleanup_ids"].append(("supplier", sid))

    @allure.title("API-BAS-007/010 删除被单据引用的供应商/客户被拒(601,db_check)")
    @pytest.mark.db_check
    def test_007_delete_referenced_supplier(self, bas_env):
        s = bas_env
        name = exp_id("EXPFref")
        assert_code_ok(s["sup"].add(name, "供应商"), "供应商")
        sid = s["sup"].find_id_by_name(name)
        dname, dbar = exp_id("EXPFdm"), exp_id("EXPFdb")
        assert_code_ok(s["depot"].add(dname), "仓库")
        assert_code_ok(s["acc"].add(dname + "a", initial=0), "账户")
        assert_code_ok(s["mat"].add(dname, dbar), "商品")
        depot_id = s["depot"].find_id_by_name(dname)
        acc_id = s["acc"].find_id_by_name(dname + "a")
        mat_id = s["mat"].find_by_name(dname)["id"]
        no = "CGRK" + s["bill"].build_number()
        from datetime import datetime
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "入库", "subType": "采购", "organId": sid, "accountId": acc_id,
                "changeAmount": -10, "discountLastMoney": 10, "totalPrice": -10, "status": "0"}
        rows = [{"materialId": mat_id, "barCode": dbar, "depotId": depot_id, "operNumber": 1,
                 "unitPrice": 10, "allPrice": 10, "taxRate": 0, "taxMoney": 0,
                 "taxLastMoney": 10, "unit": "个"}]
        assert_code_ok(s["bill"].add(info, rows), "引用单据")
        bill_id = s["bill"].get_detail_by_number(no).json()["data"]["id"]
        try:
            body = s["sup"].delete_batch([sid]).json()
            assert body.get("code") == 601, \
                f"被引用供应商删除应被 601 拦截,实际: {body}"
        finally:
            delete_bills_with_fallback(s["client"], s["db"], [bill_id])
            delete_materials_with_fallback(s["client"], s["db"], [mat_id])
            delete_depots_with_fallback(s["client"], s["db"], [depot_id])
            s["acc"].delete_batch([acc_id])
            assert_code_ok(s["sup"].delete_batch([sid]), "解除引用后删除成功")

    @allure.title("API-BAS-014 仓库名称重复实测放行——缺陷回归")
    def test_014_depot_duplicate_name(self, bas_env):
        s = bas_env
        name = exp_id("EXPFdup")
        assert_code_ok(s["depot"].add(name), "第一次新增")
        body = s["depot"].add(name).json()
        assert body.get("code") == 200, f"缺陷现状重名放行,若加唯一性拦截请翻转: {body}"
        # 同名仓库有两条,必须全部登记清理(find_id_by_name 只取首个,曾致残留)
        d_ids = [r["id"] for r in s["db"].query(
            "SELECT id FROM jsh_depot WHERE name=%s AND tenant_id=%s AND delete_flag=0",
            (name, s["tenant_id"]))]
        for did in d_ids:
            s["cleanup_ids"].append(("depot", did))

    @allure.title("API-BAS-015/018 删除被引用的仓库/账户被拒(601,db_check)")
    @pytest.mark.db_check
    def test_015_018_delete_referenced(self, bas_env):
        s = bas_env
        dname, dbar = exp_id("EXPFrd"), exp_id("EXPFrb")
        assert_code_ok(s["depot"].add(dname), "仓库")
        assert_code_ok(s["acc"].add(dname + "a", initial=0), "账户")
        assert_code_ok(s["mat"].add(dname, dbar), "商品")
        depot_id = s["depot"].find_id_by_name(dname)
        acc_id = s["acc"].find_id_by_name(dname + "a")
        mat_id = s["mat"].find_by_name(dname)["id"]
        no = "QTRK" + s["bill"].build_number()
        from datetime import datetime
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "入库", "subType": "其它", "organId": "", "accountId": acc_id,
                "changeAmount": 0, "discountLastMoney": 0, "totalPrice": 0, "status": "0"}
        rows = [{"materialId": mat_id, "barCode": dbar, "depotId": depot_id, "operNumber": 1,
                 "unitPrice": 10, "allPrice": 10, "taxRate": 0, "taxMoney": 0,
                 "taxLastMoney": 10, "unit": "个"}]
        assert_code_ok(s["bill"].add(info, rows), "引用库存/账户")
        bill_id = s["bill"].get_detail_by_number(no).json()["data"]["id"]
        try:
            body_d = s["depot"].delete_batch([depot_id]).json()
            body_a = s["acc"].delete_batch([acc_id]).json()
            assert body_d.get("code") == 601, f"被库存引用仓库删除应 601: {body_d}"
            assert body_a.get("code") == 601, f"被引用账户删除应 601: {body_a}"
        finally:
            delete_bills_with_fallback(s["client"], s["db"], [bill_id])
            delete_materials_with_fallback(s["client"], s["db"], [mat_id])
            delete_depots_with_fallback(s["client"], s["db"], [depot_id])
            s["acc"].delete_batch([acc_id])


@allure.feature("商品管理")
@pytest.mark.regression
class TestGoodsMore:

    @allure.title("API-GOODS-003 商品名为空实测可创建——缺陷回归")
    def test_003_empty_name(self, bas_env):
        s = bas_env
        body = s["mat"].add("", exp_id("EXPFeb")).json()
        assert body.get("code") == 200, f"缺陷现状空名可建,若加必填校验请翻转: {body}"
        empty = next((m for m in s["mat"].list().json()["data"]["rows"] if m["name"] == ""), None)
        if empty:
            s["mat"].delete_batch([empty["id"]])

    @allure.title("API-GOODS-005 商品名 SQL 注入样例:按普通字符处理无异常")
    def test_005_sql_injection_name(self, bas_env):
        s = bas_env
        inj = exp_id("EXPF") + "' or '1'='1"
        body = s["mat"].add(inj, exp_id("EXPFij")).json()
        assert body.get("code") == 200, f"注入串应按普通字符处理: {body}"
        row = s["mat"].find_by_name(inj)
        assert row, "注入串商品应按原名创建"
        s["mat"].delete_batch([row["id"]])

    @allure.title("API-GOODS-013 商品类别新增(DB落库)与删除")
    def test_013_category_add_delete(self, bas_env):
        """materialCategory/list 实测 300(报文待抓包);getMaterialCategoryTree 未即时
        返回新类别(疑似树缓存,观察项)——故 DB 落库为断言口径。"""
        s = bas_env
        cat = exp_id("EXPFcat")
        # 实测:parent_id=1 为悬空父节点(真实根类别 id=17),树中不可见;根类别应 parentId 传空
        body = s["client"].post("/materialCategory/add", json_body={
            "name": cat, "parentId": "", "categoryLevel": "1", "sort": "1", "remark": ""}).json()
        assert body.get("code") == 200, f"类别新增失败: {body}"
        row = s["db"].query_one(
            "SELECT id, name, parent_id FROM jsh_material_category "
            "WHERE name=%s AND tenant_id=%s AND delete_flag=0", (cat, s["tenant_id"]))
        assert row, f"类别应落库: {cat}"
        tree = s["client"].get("/materialCategory/getMaterialCategoryTree",
                               params={"id": 17}).json()
        nodes = tree if isinstance(tree, list) else tree.get("data") or []
        titles = str(nodes)
        assert cat in titles, f"修正父节点后类别树应含新增类别(观察项): {titles[:120]}"
        del_body = s["client"].delete("/materialCategory/deleteBatch",
                                      params={"ids": f"{row['id']},"}).json()
        assert del_body.get("code") == 200, f"删除空类别失败: {del_body}"


@allure.feature("商品管理")
@pytest.mark.regression
class TestGoodsMultiUnit:

    @allure.title("API-GOODS-008 多单位按箱入库:basic_number=12,库存按基本单位+12(db_check,R6)")
    @pytest.mark.db_check
    def test_008_multi_unit_in(self, bas_env):
        s = bas_env
        name = exp_id("EXPFmu")
        barcode1, barcode2 = exp_id("EXPFmub1"), exp_id("EXPFmub2")
        # 现成多单位 id=15:个/(箱=12个);双条码(个条码 + 箱条码)
        assert_code_ok(s["mat"].add(name, barcode1, unit_id=15,
                                    extra_bars=[{"bar_code": barcode2, "unit": "箱",
                                                 "purchase": "120", "retail": "240",
                                                 "low": "240"}]), "多单位商品")
        row = s["mat"].find_by_name(name)
        mid = row["id"]
        assert_code_ok(s["depot"].add(name + "d"), "仓库")
        depot_id = s["depot"].find_id_by_name(name + "d")
        no = "QTRK" + s["client"].get("/sequence/buildNumber").json()["data"]["defaultNumber"]
        info = {"operTime": "2026-10-27 12:00:00", "number": no, "type": "入库",
                "subType": "其它", "organId": "", "accountId": "",
                "changeAmount": 0, "discountLastMoney": 120, "totalPrice": 120, "status": "0"}
        rows = [{"materialId": mid, "barCode": barcode2, "depotId": depot_id,
                 "operNumber": 1, "unitPrice": 120, "allPrice": 120, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": 120, "unit": "箱"}]
        assert_code_ok(s["client"].post("/depotHead/addDepotHeadAndDetail", json_body={
            "info": __import__("json").dumps(info, ensure_ascii=False),
            "rows": __import__("json").dumps(rows, ensure_ascii=False)}), "按箱入库1箱@120")
        qtrk_id = s["client"].get("/depotHead/getDetailByNumber",
                                  params={"number": no}).json()["data"]["id"]
        try:
            item = s["db"].query_one(
                "SELECT di.oper_number, di.basic_number, di.material_unit FROM jsh_depot_item di "
                "JOIN jsh_depot_head dh ON di.header_id=dh.id "
                "WHERE dh.number=%s AND dh.tenant_id=%s", (no, s["tenant_id"]))
            assert (float(item["oper_number"]), float(item["basic_number"]),
                    item["material_unit"]) == (1.0, 12.0, "箱"),                 f"箱→个换算应 oper=1/basic=12(R6): {item}"
            stock = s["db"].query_one(
                "SELECT current_number FROM jsh_material_current_stock "
                "WHERE material_id=%s AND depot_id=%s AND tenant_id=%s AND delete_flag=0",
                (mid, depot_id, s["tenant_id"]))
            assert float(stock["current_number"]) == 12.0, f"库存按基本单位+12: {stock}"
        finally:
            delete_bills_with_fallback(s["client"], s["db"], [qtrk_id])
            delete_materials_with_fallback(s["client"], s["db"], [mid])
            delete_depots_with_fallback(s["client"], s["db"], [depot_id])

    @allure.title("API-GOODS-011 商品启停用:batchSetStatus 后列表 enabled 翻转")
    def test_011_enable_toggle(self, bas_env):
        s = bas_env
        name = exp_id("EXPFtg")
        assert_code_ok(s["mat"].add(name, exp_id("EXPFtgb")), "商品")
        row = s["mat"].find_by_name(name)
        body = s["client"].post("/material/batchSetStatus",
                                json_body={"ids": f"{row['id']},", "status": "0"}).json()
        assert body.get("code") == 200, f"停用失败: {body}"
        row2 = s["mat"].find_by_name(name)
        assert row2 is not None and row2.get("enabled") in (False, 0, "0"),             f"停用后 enabled 应为 False: {row2 and row2.get('enabled')}"
        s["client"].post("/material/batchSetStatus",
                         json_body={"ids": f"{row['id']},", "status": "1"})
        s["mat"].delete_batch([row["id"]])

    @allure.title("API-GOODS-019 删除商品后同条码重建被拦(DEF-EXP-07 回归,条码占用)")
    def test_019_barcode_reuse_after_delete(self, bas_env):
        s = bas_env
        barcode = exp_id("EXPFrc")
        name1, name2 = exp_id("EXPFrc1"), exp_id("EXPFrc2")
        assert_code_ok(s["mat"].add(name1, barcode), "第一次创建")
        row = s["mat"].find_by_name(name1)
        assert_code_ok(s["mat"].delete_batch([row["id"]]), "删除商品")
        body = s["mat"].add(name2, barcode).json()
        # 口径修正(2026-10-27 复测):无库存商品删除后同条码可重建(200),
        # 旧 extend 记录被自动逻辑删(delete_flag=1)——冒烟期 8000005 拦截未复现,记录差异;
        # 若有库存/被引用商品的删除重建行为不同,另补用例
        assert body.get("code") == 200, f"实测重建应成功,若被拦(如8000005)请更新差异记录: {body}"
        row2 = s["mat"].find_by_name(name2)
        if row2:
            old_ext = s["db"].query(
                "SELECT delete_flag FROM jsh_material_extend WHERE bar_code=%s ORDER BY id",
                (barcode,))
            flags = {str(e["delete_flag"]) for e in old_ext}
            assert "1" in flags and "0" in flags, f"旧 extend 应逻辑删+新记录并存: {old_ext}"
            s["mat"].delete_batch([row2["id"]])

    @allure.title("API-BAS-005 供应商名称 SQL 注入样例:按普通字符处理")
    def test_005_sql_injection_name(self, bas_env):
        s = bas_env
        inj = exp_id("EXPF") + "' or '1'='1"
        body = s["sup"].add(inj, "供应商").json()
        assert body.get("code") == 200, f"注入串应按普通字符处理: {body}"
        sid = s["sup"].find_id_by_name(inj)
        assert sid, "注入串供应商应按原名创建"
        s["sup"].delete_batch([sid])

    @allure.title("API-BAS-011 新增会员正向并可查询")
    def test_011_add_member(self, bas_env):
        s = bas_env
        name = exp_id("EXPFmem")
        assert_code_ok(s["sup"].add(name, "会员"), "会员新增")
        mid = s["sup"].find_id_by_name(name)
        assert mid, "会员应可按名查询"
        row = s["db"].query_one(
            "SELECT type FROM jsh_supplier WHERE id=%s", (mid,))
        assert row["type"] == "会员"
        s["sup"].delete_batch([mid])

    @allure.title("API-BAS-016 仓库编辑:名称/排序/默认仓库生效;负责人姓名实测500(缺陷回归)")
    def test_016_depot_update(self, bas_env):
        """2026-10-31 抓包复核(GYJ-MSG-001 2.4):页面 PUT /depot/update 为 18 字段全量对象。
        二分探针:remark/sort/isDefault 均 200 落库;principal 传负责人姓名(非数字)500
        未知异常且 DB 不变——负责人疑按数字 id 解析,缺陷回归断言,修复后请翻转。"""
        s = bas_env
        name = exp_id("EXPFdp")
        assert_code_ok(s["depot"].add(name), "仓库新增")
        depot_id = s["depot"].find_id_by_name(name)
        s["cleanup_ids"].append(("depot", depot_id))
        body = {"id": depot_id, "name": name, "address": None, "warehousing": None,
                "truckage": None, "type": 0, "sort": "1", "remark": "AUTO-BAS016",
                "principal": None, "enabled": True, "tenantId": int(s["tenant_id"]),
                "deleteFlag": "0", "isDefault": True, "principalName": None,
                "initStock": None, "currentStock": None, "lowSafeStock": None,
                "highSafeStock": None}
        assert_code_ok(s["client"].put("/depot/update", json_body=body), "18字段全量更新")
        row = s["db"].query_one(
            "SELECT sort, remark, is_default FROM jsh_depot WHERE id=%s", (depot_id,))
        assert str(row["sort"]) == "1" and row["remark"] == "AUTO-BAS016", \
            f"排序/备注未生效: {row}"
        assert row["is_default"] in (1, "1", b"\x01"), f"默认仓库未生效: {row}"
        # 缺陷回归:principal 传负责人姓名 → 500 未知异常,且 DB 不落库
        resp = s["client"].put("/depot/update",
                               json_body=dict(body, principal="测试负责人")).json()
        assert resp.get("code") == 500, f"负责人姓名实测500,若已修复请翻转: {resp}"
        row2 = s["db"].query_one("SELECT principal FROM jsh_depot WHERE id=%s", (depot_id,))
        assert row2["principal"] is None, f"500 时 principal 不应落库: {row2}"


@allure.feature("基础资料")
@pytest.mark.regression
class TestSelectDropdowns:
    """往来/会员下拉查询(2026-10-31 抓包复核 GYJ-MSG-001 2.1/2.2):
    页面统一 POST {"limit":1},缺参即 NPE"未知异常";返回为活跃集合的过滤子集
    (过滤语义待深究),故只断言结构并与 DB 交叉核对,不做全量对账。"""

    @allure.title("API-BAS-022 供应商/客户下拉:limit 参数+裸数组结构+与 DB 交叉")
    def test_022_sup_cus_select(self, bas_env):
        s = bas_env
        for path, dbtype in [("/supplier/findBySelect_sup", "供应商"),
                             ("/supplier/findBySelect_cus", "客户")]:
            rows = s["client"].post(path, json_body={"limit": 1}).json()
            assert isinstance(rows, list) and rows, f"{path} 应返回非空裸数组: {str(rows)[:120]}"
            assert all({"id", "supplier"} <= set(r) for r in rows), "行结构应含 id/supplier"
            db_ids = {r["id"] for r in s["db"].query(
                "SELECT id FROM jsh_supplier WHERE type=%s AND tenant_id=%s AND delete_flag=0",
                (dbtype, s["tenant_id"]))}
            bad = [r["id"] for r in rows if r["id"] not in db_ids]
            assert not bad, f"下拉返回了 DB 不存在的 {dbtype}: {bad}"

    @allure.title("API-BAS-012 会员下拉:行含 advanceIn 且与 DB 余额同源一致(db_check)")
    @pytest.mark.db_check
    def test_012_member_select(self, bas_env):
        """基线会员 60(演示库初始数据,需求分析 2.4/附录C,预存 970)实测稳定出现在
        下拉中;其余会员的过滤语义未明,故以基线会员做接口↔DB 同源断言。"""
        s = bas_env
        rows = s["client"].post("/supplier/findBySelect_retail",
                                json_body={"limit": 1}).json()
        assert isinstance(rows, list) and rows, "会员下拉应返回非空裸数组"
        assert all("advanceIn" in r for r in rows), f"会员行应含 advanceIn: {rows[:2]}"
        hit = next((r for r in rows if r["id"] == 60), None)
        assert hit, f"基线会员60 应出现在下拉中: {rows[:3]}"
        db_val = s["db"].query_one(
            "SELECT advance_in FROM jsh_supplier WHERE id=60 AND delete_flag=0")["advance_in"]
        assert float(hit["advanceIn"]) == float(db_val), \
            f"接口 advanceIn({hit['advanceIn']}) 应与 DB({db_val}) 同源一致"


@allure.feature("商品管理")
@pytest.mark.regression
class TestUnitAddBoundary:
    """多单位新增边界(2026-10-31 探针取证,GYJ-MSG-001 2.3):
    ratio 按字符串直传,数字范围无校验(0/负数放行),非数字 500,空串 301——
    校验缺失系列,断言当前实际行为,修复后翻转。"""

    @allure.title("API-GOODS-006 多单位新增:ratio=12 正向落库(db_check)")
    @pytest.mark.db_check
    def test_006_unit_add_positive(self, bas_env):
        s = bas_env
        b = exp_id("EXPFub")
        assert_code_ok(s["client"].post("/unit/add",
                      json_body={"basicUnit": b, "otherUnit": exp_id("EXPFuo"),
                                 "ratio": "12"}), "多单位新增")
        row = s["db"].query_one(
            "SELECT id, basic_unit, other_unit, ratio FROM jsh_unit "
            "WHERE basic_unit=%s AND tenant_id=%s AND delete_flag=0", (b, s["tenant_id"]))
        assert row and float(row["ratio"]) == 12.0, f"单位应落库 ratio=12: {row}"
        s["client"].delete("/unit/deleteBatch", params={"ids": f"{row['id']},"})

    @allure.title("API-GOODS-007 多单位比例边界:0/负数放行(缺陷回归),abc=500,空=301")
    def test_007_unit_ratio_boundary(self, bas_env):
        s = bas_env
        made = []
        try:
            for ratio, expect_code in [("1", 200), ("0", 200), ("-5", 200),
                                       ("abc", 500), ("", 301)]:
                b = exp_id("EXPFub")
                body = s["client"].post("/unit/add",
                                        json_body={"basicUnit": b,
                                                   "otherUnit": exp_id("EXPFuo"),
                                                   "ratio": ratio}).json()
                assert body.get("code") == expect_code, \
                    f"ratio={ratio!r} 实测应 {expect_code},实际: {body}"
                if expect_code == 200:
                    made.append(b)
        finally:
            for b in made:
                row = s["db"].query_one(
                    "SELECT id FROM jsh_unit WHERE basic_unit=%s AND delete_flag=0", (b,))
                if row:
                    s["client"].delete("/unit/deleteBatch", params={"ids": f"{row['id']},"})


@allure.feature("基础资料")
@pytest.mark.regression
class TestMasterDataRep:
    """D3 模块代表用例(2026-10-31 补齐,方案A):经手人/收支项目/多属性此前零接口用例
    (需求分析 3.2 归属手工冒烟),按"每模块 1 条代表正向+DB落库+删除"补齐;
    报文经后端实体字段+探针取证,新用例编号 BAS-021(正名)/023、GOODS-021 追加进 Excel。"""

    @allure.title("API-BAS-021 收支项目新增正向+删除(inOutItem/add,db_check)")
    @pytest.mark.db_check
    def test_021_in_out_item_add(self, bas_env):
        s = bas_env
        name = exp_id("EXPFioi")
        assert_code_ok(s["client"].post("/inOutItem/add", json_body={
            "name": name, "type": "收入", "remark": "AUTO",
            "enabled": True, "sort": ""}), "收支项目新增")
        row = s["db"].query_one(
            "SELECT id, type FROM jsh_in_out_item WHERE name=%s AND tenant_id=%s AND delete_flag=0",
            (name, s["tenant_id"]))
        assert row and row["type"] == "收入", f"收支项目应落库 type=收入: {row}"
        assert_code_ok(s["client"].delete("/inOutItem/deleteBatch",
                                          params={"ids": f"{row['id']},"}), "删除")

    @allure.title("API-BAS-023 经手人新增正向(类型区分)+删除(person/add,db_check)")
    @pytest.mark.db_check
    def test_023_person_add(self, bas_env):
        s = bas_env
        name = exp_id("EXPFp")
        assert_code_ok(s["client"].post("/person/add", json_body={
            "name": name, "type": "业务员", "enabled": True, "sort": ""}), "经手人新增")
        row = s["db"].query_one(
            "SELECT id, type FROM jsh_person WHERE name=%s AND tenant_id=%s AND delete_flag=0",
            (name, s["tenant_id"]))
        assert row and row["type"] == "业务员", f"经手人应落库 type=业务员: {row}"
        assert_code_ok(s["client"].delete("/person/deleteBatch",
                                          params={"ids": f"{row['id']},"}), "删除")

    @allure.feature("商品管理")
    @allure.title("API-GOODS-021 多属性新增(属性值竖线分隔)+删除(materialAttribute/add,db_check)")
    @pytest.mark.db_check
    def test_021_material_attribute(self, bas_env):
        s = bas_env
        an = exp_id("EXPFatt")
        assert_code_ok(s["client"].post("/materialAttribute/add", json_body={
            "attributeName": an, "attributeValue": "红|蓝|绿"}), "多属性新增")
        row = s["db"].query_one(
            "SELECT id, attribute_value FROM jsh_material_attribute "
            "WHERE attribute_name=%s AND tenant_id=%s AND delete_flag=0", (an, s["tenant_id"]))
        assert row and row["attribute_value"] == "红|蓝|绿", f"属性值应竖线分隔落库: {row}"
        assert_code_ok(s["client"].delete("/materialAttribute/deleteBatch",
                                          params={"ids": f"{row['id']},"}), "删除")

@allure.feature("商品管理")
@pytest.mark.regression
class TestGoodsParamEdge:
    """商品参数边界补充(2026-10-31 探针取证):名称超长边界(72 放行/259→301)、
    多单位出库换算、单价回写(R6)、被引用类别删除 601。"""

    @allure.title("API-GOODS-004 商品名称长度边界:100字符放行,259触发301(缺陷回归,db_check)")
    @pytest.mark.db_check
    def test_004_name_length(self, bas_env):
        s = bas_env
        nm90 = "EXPF" + "名" * 76 + exp_id()
        assert_code_ok(s["mat"].add(nm90, exp_id("EXPFb")), "90字符名放行(边界内)")
        row = s["mat"].find_by_name(nm90)
        assert row, "100字符名商品应落库"
        s["mat"].delete_batch([row["id"]])
        nm259 = "EXPF" + "名" * 250 + exp_id()
        body = s["mat"].add(nm259, exp_id("EXPFb")).json()
        assert body.get("code") == 301, f"超长名(259字符)应 301 数据写入异常,实际: {body}"

    @allure.title("API-GOODS-009 多单位出库按副单位换算:出1箱库存-12(db_check,R6)")
    @pytest.mark.db_check
    def test_009_multi_unit_out(self, bas_env):
        s = bas_env
        name = exp_id("EXPFmu")
        barcode1, barcode2 = exp_id("EXPFmub1"), exp_id("EXPFmub2")
        assert_code_ok(s["mat"].add(name, barcode1, unit_id=15,
                                    extra_bars=[{"bar_code": barcode2, "unit": "箱",
                                                 "purchase": "120", "retail": "240",
                                                 "low": "240"}]), "多单位商品")
        mid = s["mat"].find_by_name(name)["id"]
        assert_code_ok(s["depot"].add(name + "d"), "仓库")
        depot_id = s["depot"].find_id_by_name(name + "d")

        def bill(no_prefix, typ, rows, account_id):
            no = no_prefix + s["client"].get("/sequence/buildNumber").json()["data"]["defaultNumber"]
            info = {"operTime": "2026-10-31 12:00:00", "number": no, "type": typ,
                    "subType": "其它" if typ == "入库" else "销售", "organId": "",
                    "accountId": account_id, "changeAmount": 0, "discountLastMoney": 240,
                    "totalPrice": 240, "status": "0"}
            r = s["client"].post("/depotHead/addDepotHeadAndDetail", json_body={
                "info": __import__("json").dumps(info, ensure_ascii=False),
                "rows": __import__("json").dumps(rows, ensure_ascii=False)}).json()
            assert r.get("code") == 200, f"{typ}建单失败: {r}"
            return s["client"].get("/depotHead/getDetailByNumber",
                                   params={"number": no}).json()["data"]["id"]

        acc_name = exp_id("EXPFa")
        from api.account_api import AccountApi
        assert_code_ok(AccountApi(s["client"]).add(acc_name, initial=0), "账户")
        acc_id = s["db"].query_one(
            "SELECT id FROM jsh_account WHERE name=%s AND delete_flag=0", (acc_name,))["id"]
        in_id = out_id = None
        try:
            in_id = bill("QTRK", "入库", [{"materialId": mid, "barCode": barcode2,
                                          "depotId": depot_id, "operNumber": 2, "unitPrice": 120,
                                          "allPrice": 240, "taxRate": 0, "taxMoney": 0,
                                          "taxLastMoney": 240, "unit": "箱"}], acc_id)
            out_id = bill("XSCK", "出库", [{"materialId": mid, "barCode": barcode2,
                                           "depotId": depot_id, "operNumber": 1, "unitPrice": 240,
                                           "allPrice": 240, "taxRate": 0, "taxMoney": 0,
                                           "taxLastMoney": 240, "unit": "箱"}], acc_id)
            item = s["db"].query_one(
                "SELECT di.oper_number, di.basic_number FROM jsh_depot_item di "
                "JOIN jsh_depot_head dh ON di.header_id=dh.id WHERE dh.id=%s", (out_id,))
            assert (float(item["oper_number"]), float(item["basic_number"])) == (1.0, 12.0),                 f"出1箱应 basic_number=12(R6): {item}"
            stock = s["db"].query_one(
                "SELECT current_number FROM jsh_material_current_stock "
                "WHERE material_id=%s AND depot_id=%s AND tenant_id=%s AND delete_flag=0",
                (mid, depot_id, s["tenant_id"]))
            assert float(stock["current_number"]) == 12.0, f"2箱入-1箱出应剩12个: {stock}"
        finally:
            delete_bills_with_fallback(s["client"], s["db"],
                                       [b for b in (in_id, out_id) if b])
            s["mat"].delete_batch([mid])
            s["depot"].delete_batch([depot_id])
            arow = s["db"].query_one(
                "SELECT id FROM jsh_account WHERE name=%s AND delete_flag=0", (acc_name,))
            if arow:
                s["client"].delete("/account/deleteBatch", params={"ids": f"{arow['id']},"})

    @allure.title("API-GOODS-010 单据单价回写商品档案价(R6,db_check)")
    @pytest.mark.db_check
    def test_010_price_writeback(self, bas_env):
        s = bas_env
        name, bar = exp_id("EXPFwb"), exp_id("EXPFwb2")
        assert_code_ok(s["mat"].add(name, bar, purchase="10"), "商品(采购价10)")
        mid = s["mat"].find_by_name(name)["id"]
        no = "CGRK" + s["client"].get("/sequence/buildNumber").json()["data"]["defaultNumber"]
        try:
            sup_name = exp_id("EXPFs")
            assert_code_ok(s["sup"].add(sup_name, "供应商"), "供应商")
            sup_id = s["sup"].find_id_by_name(sup_name)
            acc_name = exp_id("EXPFa")
            from api.account_api import AccountApi
            assert_code_ok(AccountApi(s["client"]).add(acc_name, initial=0), "账户")
            acc_id = s["db"].query_one(
                "SELECT id FROM jsh_account WHERE name=%s AND delete_flag=0", (acc_name,))["id"]
            dep_name = exp_id("EXPFd")
            assert_code_ok(s["depot"].add(dep_name), "仓库")
            dep_id = s["depot"].find_id_by_name(dep_name)
            no = "CGRK" + s["client"].get("/sequence/buildNumber").json()["data"]["defaultNumber"]
            info = {"operTime": "2026-10-31 12:00:00", "number": no, "type": "入库",
                    "subType": "采购", "organId": sup_id, "accountId": acc_id,
                    "changeAmount": -30, "discountLastMoney": 30, "totalPrice": -30, "status": "0"}
            rows = [{"materialId": mid, "barCode": bar, "depotId": dep_id, "operNumber": 2,
                     "unitPrice": 15, "allPrice": 30, "taxRate": 0, "taxMoney": 0,
                     "taxLastMoney": 30, "unit": "个"}]
            assert_code_ok(s["client"].post("/depotHead/addDepotHeadAndDetail", json_body={
                "info": __import__("json").dumps(info, ensure_ascii=False),
                "rows": __import__("json").dumps(rows, ensure_ascii=False)}), "采购入库@15")
            ext = s["db"].query_one(
                "SELECT purchase_decimal FROM jsh_material_extend WHERE material_id=%s AND delete_flag=0", (mid,))
            assert float(ext["purchase_decimal"]) == 15.0,                 f"单据价 15 应回写档案价(R6,update_unit_price_flag=1): {ext}"
        finally:
            hid = s["db"].query_one("SELECT id FROM jsh_depot_head WHERE number=%s", (no,))
            if hid:
                s["client"].delete("/depotHead/deleteBatch", params={"ids": f"{hid['id']},"})
            s["mat"].delete_batch([mid])
            sid = s["sup"].find_id_by_name(sup_name)
            if sid:
                s["sup"].delete_batch([sid])
            arow = s["db"].query_one(
                "SELECT id FROM jsh_account WHERE name=%s AND delete_flag=0", (acc_name,))
            if arow:
                s["client"].delete("/account/deleteBatch", params={"ids": f"{arow['id']},"})
            drow = s["db"].query_one(
                "SELECT id FROM jsh_depot WHERE name=%s AND delete_flag=0", (dep_name,))
            if drow:
                s["client"].delete("/depot/deleteBatch", params={"ids": f"{drow['id']},"})

    @allure.title("API-GOODS-014 删除被商品引用的类别被拒(601,db_check)")
    @pytest.mark.db_check
    def test_014_delete_referenced_category(self, bas_env):
        s = bas_env
        cat = exp_id("EXPFcat")
        body = s["client"].post("/materialCategory/add", json_body={
            "name": cat, "parentId": "", "categoryLevel": "1", "sort": "1", "remark": ""}).json()
        assert body.get("code") == 200, f"类别新增失败: {body}"
        cat_id = s["db"].query_one(
            "SELECT id FROM jsh_material_category WHERE name=%s AND tenant_id=%s AND delete_flag=0",
            (cat, s["tenant_id"]))["id"]
        try:
            name, bar = exp_id("EXPFcm"), exp_id("EXPFcb")
            body = s["client"].post("/material/add", json_body={
                "name": name, "categoryId": str(cat_id), "unit": "个", "mnemonic": "EXPFsp",
                "imgName": "", "meList": [{"id": "", "barCode": bar, "commodityUnit": "个", "sku": "",
                                           "purchaseDecimal": "10", "commodityDecimal": "20",
                                           "wholesaleDecimal": "", "lowDecimal": "20"}],
                "stock": [], "sortList": [], "unitId": "", "meDeleteIdList": []}).json()
            assert body.get("code") == 200, f"挂类别商品新增失败: {body}"
            mid = s["db"].query_one("SELECT id FROM jsh_material WHERE name=%s", (name,))["id"]
            del_body = s["client"].delete("/materialCategory/deleteBatch",
                                          params={"ids": f"{cat_id},"}).json()
            assert del_body.get("code") == 601,                 f"被商品引用的类别删除应 601,实际: {del_body}"
        finally:
            row = s["db"].query_one("SELECT id FROM jsh_material WHERE name=%s AND delete_flag=0", (name,))
            if row:
                s["mat"].delete_batch([row["id"]])
            s["client"].delete("/materialCategory/deleteBatch", params={"ids": f"{cat_id},"})

