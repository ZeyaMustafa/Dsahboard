# Supermarket Sales Dashboard

A Python-based dashboard for analyzing supermarket sales data with daily, weekly, and monthly reports, plus visualizations.

## Features
- Daily sales and profit reports
- Top product identification per day
- Weekly and monthly sales/profit reports
- Top 10 products analysis
- Category-wise sales and profit analysis
- Visualization plots (trends, top products, category analysis)
- Sample data generator for testing

## Setup
1. Install required packages:
   ```bash
   pip install pandas matplotlib seaborn
   ```

2. Generate sample data (optional):
   ```bash
   python generate_sample_data.py
   ```

3. Run the dashboard:
   ```bash
   python dashboard.py
   ```

## Output
- Text reports printed to console
- Visualization plots saved in the `plots/` directory:
  - Daily/weekly/monthly sales and profit trends
  - Top 10 products for daily/weekly/monthly periods
  - Sales and profit by category for daily/weekly/monthly periods

## Data Format
Place CSV files in the `data/` directory. The dashboard normalizes common export formats into this internal schema:
- Date
- Product_ID
- Product_Name
- Category
- Units_Sold
- Unit_Price
- Profit_Per_Unit
- Total_Sale
- Total_Profit

Accepted alternate column names include common fields such as `transaction_date`, `order_date`, `sku`, `item`, `product`, `department`, `qty`, `quantity`, `selling_price`, `revenue`, `cost_price`, `unit_cost`, `profit`, and `gross_profit`.

Minimum useful data:
- a date column
- a product/item name column
- quantity/units sold
- unit price, or total sales/revenue that can be divided by quantity

Optional fields:
- category defaults to `Uncategorized`
- product ID is generated when missing
- total sales is calculated from units and price when missing
- profit is calculated from total profit or cost price when available; otherwise profit metrics show as 0
