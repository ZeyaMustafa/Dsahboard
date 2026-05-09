import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os
import glob
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns

# Set style for plots
sns.set_style("whitegrid")
plt.rcParams['figure.figsize'] = (12, 6)

def load_data():
    """Load all CSV files from the data directory"""
    data_dir = 'data'
    if not os.path.exists(data_dir):
        print(f"Data directory '{data_dir}' not found.")
        return None
    
    # Get all CSV files in the data directory
    csv_files = glob.glob(os.path.join(data_dir, '*.csv'))
    if not csv_files:
        print(f"No CSV files found in '{data_dir}'.")
        return None
    
    # Combine all CSV files
    df_list = []
    for file in csv_files:
        try:
            df = pd.read_csv(file)
            df_list.append(df)
        except Exception as e:
            print(f"Error reading {file}: {e}")
    
    if not df_list:
        print("No valid data loaded.")
        return None
    
    df = pd.concat(df_list, ignore_index=True)
    
    # Convert Date column to datetime
    df['Date'] = pd.to_datetime(df['Date'])
    
    # Sort by date
    df = df.sort_values('Date')
    
    return df

def daily_report(df, target_date=None):
    """Generate daily sales and profit report for a specific date or the latest date"""
    if target_date is None:
        target_date = df['Date'].max()
    else:
        target_date = pd.to_datetime(target_date)
    
    daily_data = df[df['Date'] == target_date]
    
    if daily_data.empty:
        print(f"No data found for {target_date.strftime('%Y-%m-%d')}")
        return
    
    total_sales = daily_data['Total_Sale'].sum()
    total_profit = daily_data['Total_Profit'].sum()
    
    # Top product by units sold
    top_product = daily_data.groupby('Product_Name')['Units_Sold'].sum().idxmax()
    top_product_units = daily_data.groupby('Product_Name')['Units_Sold'].sum().max()
    
    print(f"\n=== Daily Report for {target_date.strftime('%Y-%m-%d')} ===")
    print(f"Total Sales: ${total_sales:,.2f}")
    print(f"Total Profit: ${total_profit:,.2f}")
    print(f"Top Product: {top_product} ({top_product_units} units sold)")
    print("=" * 50)

def weekly_report(df, end_date=None):
    """Generate weekly sales and profit report ending on end_date (or latest date)"""
    if end_date is None:
        end_date = df['Date'].max()
    else:
        end_date = pd.to_datetime(end_date)
    
    start_date = end_date - timedelta(days=6)  # Last 7 days including end_date
    weekly_data = df[(df['Date'] >= start_date) & (df['Date'] <= end_date)]
    
    if weekly_data.empty:
        print(f"No data found for week {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        return
    
    total_sales = weekly_data['Total_Sale'].sum()
    total_profit = weekly_data['Total_Profit'].sum()
    
    # Top 10 products by units sold
    top_products = weekly_data.groupby('Product_Name')['Units_Sold'].sum().nlargest(10)
    
    print(f"\n=== Weekly Report ({start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}) ===")
    print(f"Total Sales: ${total_sales:,.2f}")
    print(f"Total Profit: ${total_profit:,.2f}")
    print("\nTop 10 Products Sold (by units):")
    for i, (product, units) in enumerate(top_products.items(), 1):
        print(f"  {i}. {product}: {units} units")
    print("=" * 60)

def monthly_report(df, year=None, month=None):
    """Generate monthly sales and profit report for a specific year and month"""
    if year is None or month is None:
        # Default to current month
        now = datetime.now()
        year = now.year
        month = now.month
    
    # Filter data for the given year and month
    mask = (df['Date'].dt.year == year) & (df['Date'].dt.month == month)
    monthly_data = df[mask]
    
    if monthly_data.empty:
        print(f"No data found for {year}-{month:02d}")
        return
    
    total_sales = monthly_data['Total_Sale'].sum()
    total_profit = monthly_data['Total_Profit'].sum()
    
    # Top 10 products by units sold
    top_products = monthly_data.groupby('Product_Name')['Units_Sold'].sum().nlargest(10)
    
    print(f"\n=== Monthly Report ({year}-{month:02d}) ===")
    print(f"Total Sales: ${total_sales:,.2f}")
    print(f"Total Profit: ${total_profit:,.2f}")
    print("\nTop 10 Products Sold (by units):")
    for i, (product, units) in enumerate(top_products.items(), 1):
        print(f"  {i}. {product}: {units} units")
    print("=" * 50)

def ensure_plots_dir():
    """Ensure the plots directory exists"""
    plots_dir = 'plots'
    if not os.path.exists(plots_dir):
        os.makedirs(plots_dir)
    return plots_dir

def plot_daily_trend(df, save=True):
    """Plot daily sales and profit trend over time"""
    # Aggregate by date
    daily = df.groupby('Date').agg({
        'Total_Sale': 'sum',
        'Total_Profit': 'sum'
    }).reset_index()
    
    fig, ax1 = plt.subplots()
    
    color = 'tab:blue'
    ax1.set_xlabel('Date')
    ax1.set_ylabel('Sales ($)', color=color)
    ax1.plot(daily['Date'], daily['Total_Sale'], color=color, label='Sales')
    ax1.tick_params(axis='y', labelcolor=color)
    
    ax2 = ax1.twinx()
    color = 'tab:green'
    ax2.set_ylabel('Profit ($)', color=color)
    ax2.plot(daily['Date'], daily['Total_Profit'], color=color, label='Profit')
    ax2.tick_params(axis='y', labelcolor=color)
    
    fig.tight_layout()
    plt.title('Daily Sales and Profit Trend')
    fig.autofmt_xdate()
    
    if save:
        plots_dir = ensure_plots_dir()
        plt.savefig(os.path.join(plots_dir, 'daily_trend.png'), dpi=300, bbox_inches='tight')
        plt.close()
        print("Saved daily trend plot to plots/daily_trend.png")
    else:
        plt.show()

def plot_weekly_trend(df, save=True):
    """Plot weekly sales and profit trend"""
    # Convert to weekly frequency
    weekly = df.set_index('Date').resample('W').agg({
        'Total_Sale': 'sum',
        'Total_Profit': 'sum'
    }).reset_index()
    
    fig, ax1 = plt.subplots()
    
    color = 'tab:blue'
    ax1.set_xlabel('Week Ending')
    ax1.set_ylabel('Sales ($)', color=color)
    ax1.plot(weekly['Date'], weekly['Total_Sale'], color=color, marker='o', label='Sales')
    ax1.tick_params(axis='y', labelcolor=color)
    
    ax2 = ax1.twinx()
    color = 'tab:green'
    ax2.set_ylabel('Profit ($)', color=color)
    ax2.plot(weekly['Date'], weekly['Total_Profit'], color=color, marker='s', label='Profit')
    ax2.tick_params(axis='y', labelcolor=color)
    
    fig.tight_layout()
    plt.title('Weekly Sales and Profit Trend')
    fig.autofmt_xdate()
    
    if save:
        plots_dir = ensure_plots_dir()
        plt.savefig(os.path.join(plots_dir, 'weekly_trend.png'), dpi=300, bbox_inches='tight')
        plt.close()
        print("Saved weekly trend plot to plots/weekly_trend.png")
    else:
        plt.show()

def plot_monthly_trend(df, save=True):
    """Plot monthly sales and profit trend"""
    # Convert to monthly frequency
    monthly = df.set_index('Date').resample('ME').agg({
        'Total_Sale': 'sum',
        'Total_Profit': 'sum'
    }).reset_index()
    
    fig, ax1 = plt.subplots()
    
    color = 'tab:blue'
    ax1.set_xlabel('Month')
    ax1.set_ylabel('Sales ($)', color=color)
    ax1.plot(monthly['Date'], monthly['Total_Sale'], color=color, marker='o', label='Sales')
    ax1.tick_params(axis='y', labelcolor=color)
    
    ax2 = ax1.twinx()
    color = 'tab:green'
    ax2.set_ylabel('Profit ($)', color=color)
    ax2.plot(monthly['Date'], monthly['Total_Profit'], color=color, marker='s', label='Profit')
    ax2.tick_params(axis='y', labelcolor=color)
    
    fig.tight_layout()
    plt.title('Monthly Sales and Profit Trend')
    fig.autofmt_xdate()
    
    if save:
        plots_dir = ensure_plots_dir()
        plt.savefig(os.path.join(plots_dir, 'monthly_trend.png'), dpi=300, bbox_inches='tight')
        plt.close()
        print("Saved monthly trend plot to plots/monthly_trend.png")
    else:
        plt.show()

def plot_top_products_bar(df, period='monthly', save=True):
    """Plot top 10 products by units sold for a given period"""
    if period == 'daily':
        # For daily, we need a specific date - use the latest date
        target_date = df['Date'].max()
        period_data = df[df['Date'] == target_date]
        title_suffix = f"for {target_date.strftime('%Y-%m-%d')}"
    elif period == 'weekly':
        # Last 7 days
        end_date = df['Date'].max()
        start_date = end_date - timedelta(days=6)
        period_data = df[(df['Date'] >= start_date) & (df['Date'] <= end_date)]
        title_suffix = f"for week {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
    else:  # monthly
        # Current month
        now = datetime.now()
        year, month = now.year, now.month
        mask = (df['Date'].dt.year == year) & (df['Date'].dt.month == month)
        period_data = df[mask]
        title_suffix = f"for {now.strftime('%B %Y')}"
    
    if period_data.empty:
        print(f"No data for {period} period to plot top products.")
        return
    
    # Top 10 products by units sold
    top_products = period_data.groupby('Product_Name')['Units_Sold'].sum().nlargest(10)
    
    plt.figure()
    # Create a bar plot
    sns.barplot(x=top_products.values, y=top_products.index, palette='viridis')
    plt.xlabel('Units Sold')
    plt.ylabel('Product')
    plt.title(f'Top 10 Products Sold {title_suffix}')
    plt.tight_layout()
    
    if save:
        plots_dir = ensure_plots_dir()
        plt.savefig(os.path.join(plots_dir, f'top_products_{period}.png'), dpi=300, bbox_inches='tight')
        plt.close()
        print(f"Saved top products {period} plot to plots/top_products_{period}.png")
    else:
        plt.show()

def plot_category_analysis(df, period='monthly', save=True):
    """Plot sales and profit by category for a given period"""
    if period == 'daily':
        target_date = df['Date'].max()
        period_data = df[df['Date'] == target_date]
        title_suffix = f"for {target_date.strftime('%Y-%m-%d')}"
    elif period == 'weekly':
        end_date = df['Date'].max()
        start_date = end_date - timedelta(days=6)
        period_data = df[(df['Date'] >= start_date) & (df['Date'] <= end_date)]
        title_suffix = f"for week {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
    else:  # monthly
        now = datetime.now()
        year, month = now.year, now.month
        mask = (df['Date'].dt.year == year) & (df['Date'].dt.month == month)
        period_data = df[mask]
        title_suffix = f"for {now.strftime('%B %Y')}"
    
    if period_data.empty:
        print(f"No data for {period} period to plot category analysis.")
        return
    
    # Aggregate by category
    category_stats = period_data.groupby('Category').agg({
        'Total_Sale': 'sum',
        'Total_Profit': 'sum',
        'Units_Sold': 'sum'
    }).reset_index()
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
    
    # Sales by category
    sns.barplot(data=category_stats, x='Total_Sale', y='Category', ax=ax1, palette='Blues_d')
    ax1.set_title(f'Sales by Category {title_suffix}')
    ax1.set_xlabel('Sales ($)')
    ax1.set_ylabel('Category')
    
    # Profit by category
    sns.barplot(data=category_stats, x='Total_Profit', y='Category', ax=ax2, palette='Greens_d')
    ax2.set_title(f'Profit by Category {title_suffix}')
    ax2.set_xlabel('Profit ($)')
    ax2.set_ylabel('Category')
    
    plt.tight_layout()
    
    if save:
        plots_dir = ensure_plots_dir()
        plt.savefig(os.path.join(plots_dir, f'category_analysis_{period}.png'), dpi=300, bbox_inches='tight')
        plt.close()
        print(f"Saved category analysis {period} plot to plots/category_analysis_{period}.png")
    else:
        plt.show()

def generate_all_plots(df):
    """Generate all visualization plots"""
    print("\nGenerating visualization plots...")
    plot_daily_trend(df)
    plot_weekly_trend(df)
    plot_monthly_trend(df)
    plot_top_products_bar(df, 'daily')
    plot_top_products_bar(df, 'weekly')
    plot_top_products_bar(df, 'monthly')
    plot_category_analysis(df, 'daily')
    plot_category_analysis(df, 'weekly')
    plot_category_analysis(df, 'monthly')
    print("All plots generated and saved to the 'plots' directory.")

def main():
    """Main function to run the dashboard"""
    print("Loading supermarket sales data...")
    df = load_data()
    
    if df is None:
        print("Failed to load data. Please check the data directory.")
        return
    
    print(f"Loaded {len(df)} records from {df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}")
    
    # Generate text reports
    daily_report(df)  # Latest day
    weekly_report(df)  # Last 7 days
    monthly_report(df)  # Current month
    
    # Generate visualization plots
    generate_all_plots(df)
    
    print("\nDashboard execution complete. Reports printed above and plots saved in 'plots/' directory.")

if __name__ == "__main__":
    main()
