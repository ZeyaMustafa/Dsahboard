import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os

def generate_sample_data():
    """Generate sample supermarket sales data for testing"""
    
    # Create data directory if it doesn't exist
    os.makedirs('data', exist_ok=True)
    
    # Product catalog
    products = [
        {'id': 'P001', 'name': 'Fresh Milk', 'category': 'Dairy', 'price': 3.50, 'profit': 1.20},
        {'id': 'P002', 'name': 'Whole Wheat Bread', 'category': 'Bakery', 'price': 2.80, 'profit': 0.90},
        {'id': 'P003', 'name': 'Free Range Eggs', 'category': 'Dairy', 'price': 4.20, 'profit': 1.50},
        {'id': 'P004', 'name': 'Organic Apples', 'category': 'Produce', 'price': 5.50, 'profit': 2.00},
        {'id': 'P005', 'name': 'Chicken Breast', 'category': 'Meat', 'price': 8.90, 'profit': 3.00},
        {'id': 'P006', 'name': 'Brown Rice', 'category': 'Pantry', 'price': 4.50, 'profit': 1.80},
        {'id': 'P007', 'name': 'Olive Oil', 'category': 'Pantry', 'price': 12.00, 'profit': 4.50},
        {'id': 'P008', 'name': 'Greek Yogurt', 'category': 'Dairy', 'price': 1.80, 'profit': 0.60},
        {'id': 'P009', 'name': 'Almond Butter', 'category': 'Pantry', 'price': 7.50, 'profit': 2.80},
        {'id': 'P010', 'name': 'Salmon Fillet', 'category': 'Seafood', 'price': 12.50, 'profit': 4.20},
        {'id': 'P011', 'name': 'Quinoa', 'category': 'Pantry', 'price': 6.80, 'profit': 2.50},
        {'id': 'P012', 'name': 'Avocado', 'category': 'Produce', 'price': 2.20, 'profit': 0.80},
        {'id': 'P013', 'name': 'Bell Peppers', 'category': 'Produce', 'price': 3.00, 'profit': 1.10},
        {'id': 'P014', 'name': 'Greek Olives', 'category': 'Pantry', 'price': 5.50, 'profit': 2.00},
        {'id': 'P015', 'name': 'Dark Chocolate', 'category': 'Snacks', 'price': 4.50, 'profit': 1.80}
    ]
    
    # Generate data for the last 30 days
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)
    
    data_rows = []
    
    current_date = start_date
    while current_date <= end_date:
        # Generate between 50-200 transactions per day
        num_transactions = np.random.randint(50, 201)
        
        for _ in range(num_transactions):
            # Pick random product
            product = np.random.choice(products)
            
            # Generate quantity sold (1-10 units)
            units_sold = np.random.randint(1, 11)
            
            # Calculate total sale and profit
            total_sale = units_sold * product['price']
            total_profit = units_sold * product['profit']
            
            data_rows.append({
                'Date': current_date.strftime('%Y-%m-%d'),
                'Product_ID': product['id'],
                'Product_Name': product['name'],
                'Category': product['category'],
                'Units_Sold': units_sold,
                'Unit_Price': product['price'],
                'Profit_Per_Unit': product['profit'],
                'Total_Sale': round(total_sale, 2),
                'Total_Profit': round(total_profit, 2)
            })
        
        current_date += timedelta(days=1)
    
    # Create DataFrame and save to CSV
    df = pd.DataFrame(data_rows)
    df.to_csv('data/sales_data.csv', index=False)
    
    print(f"Generated {len(df)} sales records for {df['Date'].nunique()} days")
    print(f"Date range: {df['Date'].min()} to {df['Date'].max()}")
    print("Sample data saved to data/sales_data.csv")

if __name__ == "__main__":
    generate_sample_data()