from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


OUTPUT = Path("data/hdpos_imports/sql_server_pos_test_10000_basket_snapshot.csv")
INVOICE_COUNT = 1800
LINE_COUNT = 10000
START_DATE = datetime(2026, 4, 1)


PRODUCTS = {
    "P001": ("Bananas", "Produce", "890000000001", "Fresh Valley", 0.39, 0.62, 1620, 180),
    "P002": ("Apples", "Produce", "890000000002", "Fresh Valley", 0.78, 1.15, 1350, 160),
    "P003": ("Milk 1L", "Dairy", "890000000003", "Daily Dairy", 1.63, 2.25, 980, 150),
    "P004": ("Bread Loaf", "Bakery", "890000000004", "Bake House", 1.55, 2.10, 860, 140),
    "P005": ("Eggs 12 Pack", "Dairy", "890000000005", "Farm Fresh", 2.85, 3.80, 920, 120),
    "P006": ("Butter", "Dairy", "890000000006", "Daily Dairy", 3.35, 4.40, 760, 100),
    "P007": ("Peanut Butter", "Pantry", "890000000007", "NutriFoods", 3.60, 5.25, 560, 75),
    "P008": ("Strawberry Jam", "Pantry", "890000000008", "NutriFoods", 3.30, 4.75, 580, 75),
    "P009": ("Pasta", "Pantry", "890000000009", "Urban Foods", 1.65, 2.35, 1020, 150),
    "P010": ("Tomato Sauce", "Pantry", "890000000010", "Urban Foods", 2.08, 2.90, 1010, 145),
    "P011": ("Coffee", "Beverages", "890000000011", "Morning Co", 6.10, 8.90, 410, 65),
    "P012": ("Sugar", "Pantry", "890000000012", "Sweet Mill", 1.68, 2.20, 1200, 180),
    "P013": ("Tea Bags", "Beverages", "890000000013", "Morning Co", 3.10, 4.30, 540, 85),
    "P014": ("Biscuits", "Snacks", "890000000014", "Crunchy Bite", 1.82, 2.60, 1420, 190),
    "P015": ("Chips", "Snacks", "890000000015", "Crunchy Bite", 1.99, 2.85, 1380, 185),
    "P016": ("Salsa", "Snacks", "890000000016", "Urban Foods", 2.90, 3.95, 740, 105),
    "P017": ("Rice 5kg", "Staples", "890000000017", "Grain Depot", 10.40, 12.50, 380, 55),
    "P018": ("Cooking Oil", "Staples", "890000000018", "Grain Depot", 7.90, 9.80, 410, 55),
    "P019": ("Chicken Breast", "Meat", "890000000019", "Prime Meat", 5.45, 7.20, 360, 50),
    "P020": ("Yogurt Cups", "Dairy", "890000000020", "Daily Dairy", 2.52, 3.40, 780, 120),
    "P021": ("Orange Juice", "Beverages", "890000000021", "Fresh Valley", 2.80, 4.10, 620, 95),
    "P022": ("Cereal", "Breakfast", "890000000022", "Morning Co", 3.25, 4.95, 640, 90),
    "P023": ("Cheese Slices", "Dairy", "890000000023", "Daily Dairy", 3.90, 5.60, 520, 80),
    "P024": ("Frozen Peas", "Frozen", "890000000024", "Cold Chain", 1.95, 2.95, 700, 100),
}


BASKETS = {
    0: ["P004", "P006", "P003", "P005", "P020"],
    1: ["P004", "P006", "P003", "P005", "P020"],
    2: ["P009", "P010", "P018", "P012", "P024"],
    3: ["P015", "P016", "P021", "P014", "P022"],
    4: ["P011", "P012", "P013", "P014", "P022"],
    5: ["P007", "P008", "P004", "P003", "P021"],
    6: ["P001", "P002", "P020", "P023", "P003"],
    7: ["P017", "P018", "P019", "P024", "P010"],
    8: ["P021", "P022", "P011", "P013", "P014"],
    9: ["P003", "P004", "P005", "P006", "P023"],
}


def product_code_for_line(invoice_id, line_in_invoice):
    basket = BASKETS[invoice_id % 10]
    return basket[(line_in_invoice - 1) % len(basket)]


def build_rows():
    rows = []
    for n in range(1, LINE_COUNT + 1):
        invoice_id = ((n - 1) % INVOICE_COUNT) + 1
        line_in_invoice = ((n - 1) // INVOICE_COUNT) + 1
        invoice_date = START_DATE + timedelta(
            days=(invoice_id - 1) % 21,
            minutes=(invoice_id * 11) % 780,
        )
        product_code = product_code_for_line(invoice_id, line_in_invoice)
        name, category, barcode, supplier, cost, unit_price, current_stock, reorder_level = PRODUCTS[product_code]
        quantity = ((n * 3) % 4) + 1
        discount_rate = 0.05 if n % 17 == 0 else 0.03 if n % 23 == 0 else 0
        gross_sale = unit_price * quantity
        discount = round(gross_sale * discount_rate, 2)
        line_total = round(gross_sale - discount, 2)
        tax = round(line_total * 0.05, 2)
        line_profit = round(((unit_price - cost) * quantity) - discount, 2)

        rows.append({
            "Invoice_No": f"INV-{invoice_date:%Y%m%d}-{invoice_id:05d}",
            "Date": invoice_date.strftime("%Y-%m-%d %H:%M:%S"),
            "Product_ID": product_code,
            "Product_Name": name,
            "Category": category,
            "Units_Sold": quantity,
            "Unit_Price": unit_price,
            "Profit_Per_Unit": round(unit_price - cost, 2),
            "Total_Sale": line_total,
            "Total_Profit": line_profit,
            "Discount_Amount": discount,
            "Tax_Amount": tax,
            "Payment_Mode": ["Cash", "Card", "UPI", "Wallet"][invoice_id % 4],
            "Customer_ID": f"CU{(invoice_id % 650) + 1:04d}",
            "Cashier_ID": f"C{(invoice_id % 10) + 1:02d}",
            "Store_ID": "STORE-003" if invoice_id % 6 == 0 else "STORE-002" if invoice_id % 5 == 0 else "STORE-001",
            "Barcode": barcode,
            "Supplier": supplier,
            "Current_Stock": current_stock,
            "Reorder_Level": reorder_level,
        })
    return rows


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(build_rows()).to_csv(OUTPUT, index=False)
    print(OUTPUT)


if __name__ == "__main__":
    main()
