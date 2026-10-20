"""Demo metric definitions shared by query planning and seeded dashboards."""

INVENTORY_SNAPSHOT = (
    "f.date_id = (SELECT MAX(date_id) FROM fact_inventory WHERE date_id <= ?)"
)
NEAR_EXPIRY_SQL = (
    "SELECT COUNT(*) AS near_expiry FROM fact_inventory "
    "WHERE date_id=(SELECT MAX(date_id) FROM fact_inventory WHERE date_id<=as_of_date()) "
    "AND expiry_date BETWEEN as_of_date() AND date(as_of_date(), '+180 days')"
)
SALES_METRICS = {
    "销售额": "ROUND(SUM(f.amount), 2)",
    "销量": "SUM(f.quantity)",
    "成本": "ROUND(SUM(f.cost_amount), 2)",
    "毛利": "ROUND(SUM(f.amount - f.cost_amount), 2)",
    "毛利率(%)": "ROUND((1-SUM(f.cost_amount)/NULLIF(SUM(f.amount),0))*100,2)",
    "平均销售单价": "ROUND(SUM(f.amount)/NULLIF(SUM(f.quantity),0),2)",
}
