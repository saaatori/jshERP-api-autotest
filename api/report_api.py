"""报表与统计查询(GET 平铺参数封装,2026-10-27 取证:报表接口不用 search JSON)。

关键口径:
- /depotItem/getInOutStock 行字段:thisSum=结存数量、inSum=累计入库、outSum=累计出库,
  对账公式 thisSum = inSum - outSum(期初 0 时);
- /depotItem/buyIn 行:inSum=数量、inSumPrice=金额;saleOut/retailOut 行:outInSumPrice=金额;
- /depotHead/findInOutDetail 需带 remark 空串(缺失报 500 未知异常,缺陷同源模式),
  汇总字段 operNumberTotal;findInOutMaterialCount 汇总字段 numSumTotal;
- /material/getListWithStock 需带 zeroStock;/account/getStatistics 用 name/serialNo 空串;
- getInOutStock/getListWithStock 缺省仓库范围为服务端"当前用户有权限仓库"逻辑,
  该逻辑在 user_business.UserDepot 授权列表膨胀后会整体失效(返回 510/空集,DEF-EXP-09),
  因此这两个查询必须显式传 depot_ids,不依赖隐式默认。
"""
from common.client import HttpClient


class ReportApi:
    def __init__(self, client: HttpClient):
        self.c = client

    def _page(self, path, extra=None):
        params = {"currentPage": 1, "pageSize": 50}
        params.update(extra or {})
        return self.c.get(path, params=params).json()

    def in_out_stock(self, begin="2026-01-01", end="2026-12-31", material_param="",
                     depot_ids=None):
        extra = {"beginTime": begin, "endTime": end,
                 "materialParam": material_param, "mpList": ""}
        if depot_ids:
            extra["depotIds"] = depot_ids
        return self._page("/depotItem/getInOutStock", extra).get("data") or {}

    def buy_in(self, begin="2026-01-01", end="2026-12-31", material_param=""):
        return self._page("/depotItem/buyIn", {
            "beginTime": begin, "endTime": end,
            "materialParam": material_param, "mpList": ""}).get("data") or {}

    def sale_out(self, begin="2026-01-01", end="2026-12-31", material_param=""):
        return self._page("/depotItem/saleOut", {
            "beginTime": begin, "endTime": end,
            "materialParam": material_param, "mpList": ""}).get("data") or {}

    def retail_out(self, begin="2026-01-01", end="2026-12-31", material_param=""):
        return self._page("/depotItem/retailOut", {
            "beginTime": begin, "endTime": end,
            "materialParam": material_param, "mpList": ""}).get("data") or {}

    def find_in_out_detail(self, type_, begin="2026-01-01", end="2026-12-31", material_param=""):
        return self._page("/depotHead/findInOutDetail", {
            "beginTime": begin, "endTime": end, "type": type_,
            "number": "", "materialParam": material_param, "remark": ""}).get("data") or {}

    def in_out_material_count(self, type_, begin="2026-01-01", end="2026-12-31", material_param=""):
        return self._page("/depotHead/findInOutMaterialCount", {
            "beginTime": begin, "endTime": end, "type": type_,
            "number": "", "materialParam": material_param, "remark": ""}).get("data") or {}

    def statement_account(self, supplier_type, organ_id=None,
                          begin="2026-01-01", end="2026-12-31"):
        extra = {"beginTime": begin, "endTime": end, "supplierType": supplier_type, "hasDebt": ""}
        if organ_id:
            extra["organId"] = organ_id
        return self._page("/depotHead/getStatementAccount", extra).get("data") or {}

    def stock_warning(self):
        return self._page("/depotItem/findStockWarningCount", {
            "materialParam": "", "mpList": ""}).get("data") or {}

    def list_with_stock(self, material_param="", depot_ids=None):
        extra = {"materialParam": material_param, "zeroStock": "0", "depotIds": ""}
        if depot_ids:
            extra["depotIds"] = depot_ids
        return self._page("/material/getListWithStock", extra).get("data") or {}

    def account_statistics(self, name=""):
        return self._page("/account/getStatistics", {
            "name": name, "serialNo": ""}).get("data") or {}

    def buy_and_sale_statistics(self):
        return self.c.get("/depotHead/getBuyAndSaleStatistics").json().get("data") or {}
