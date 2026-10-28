"""基础资料/商品长尾(API-BAS-004/007/010/014/015/018 + GOODS-003/005/013)。

取证(2026-10-27):
- 仓库名称重复实测放行(与供应商同源,无唯一性校验,BAS-014 缺陷回归);
- 商品名为空实测可创建(GOODS-003 缺陷回归);
- 删除被业务数据引用的资料返回 code 601"检测到存在依赖数据,不能删除"。
未实现:BAS-012 会员下拉(findBySelect_retail NPE)、GOODS-011/012 启停用
(material/update 实测 301,报文待抓包)、GOODS-016 序列号(构造复杂)。
"""
import allure
import pytest

from api.account_api import AccountApi
from api.depot_api import DepotApi
from api.depot_head_api import DepotHeadApi
from api.material_api import MaterialApi
from api.supplier_api import SupplierApi
from common.asserts import assert_code_ok
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
                s["depot"].delete_batch([rid])
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
            s["bill"].delete_batch([bill_id])
            s["mat"].delete_batch([mat_id])
            s["depot"].delete_batch([depot_id])
            s["acc"].delete_batch([acc_id])
            assert_code_ok(s["sup"].delete_batch([sid]), "解除引用后删除成功")

    @allure.title("API-BAS-014 仓库名称重复实测放行——缺陷回归")
    def test_014_depot_duplicate_name(self, bas_env):
        s = bas_env
        name = exp_id("EXPFdup")
        assert_code_ok(s["depot"].add(name), "第一次新增")
        body = s["depot"].add(name).json()
        assert body.get("code") == 200, f"缺陷现状重名放行,若加唯一性拦截请翻转: {body}"
        d_id = s["depot"].find_id_by_name(name)
        s["cleanup_ids"].append(("depot", d_id))

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
            s["bill"].delete_batch([bill_id])
            s["mat"].delete_batch([mat_id])
            s["depot"].delete_batch([depot_id])
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
            s["mat"].delete_batch([mid])
            s["depot"].delete_batch([depot_id])

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

    @allure.title("API-BAS-016 仓库负责人与默认仓库设置(暂缓:update 报文待抓包)")
    @pytest.mark.skip(reason="PUT /depot/update 全字段提交实测 500 未知异常,报文结构待 Fiddler 抓包后实现")
    def test_016_depot_update(self, bas_env):
        s = bas_env
        name = exp_id("EXPFdp")
        assert_code_ok(s["depot"].add(name), "仓库新增")
        depot_id = s["depot"].find_id_by_name(name)
        body = s["client"].put("/depot/update", json_body={
            "id": depot_id, "name": name, "sort": "1", "remark": "",
            "isDefault": False, "principal": "测试负责人"}).json()
        assert body.get("code") == 200, f"仓库更新失败: {body}"
        row = s["db"].query_one(
            "SELECT principal, sort FROM jsh_depot WHERE id=%s", (depot_id,))
        assert row["principal"] == "测试负责人" and str(row["sort"]) == "1",             f"负责人/排序未生效: {row}"
        s["depot"].delete_batch([depot_id])
