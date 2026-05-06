import os
import glob
import secrets
import sqlite3
import hashlib
import time
import threading
from itertools import combinations
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response, send_file
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_dance.contrib.google import make_google_blueprint, google
from flask_dance.contrib.facebook import make_facebook_blueprint, facebook
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json

app = Flask(__name__)
os.makedirs(app.instance_path, exist_ok=True)

def load_secret_key():
    configured_key = os.environ.get('SECRET_KEY')
    if configured_key:
        return configured_key

    secret_path = os.path.join(app.instance_path, 'secret_key')
    if os.path.exists(secret_path):
        with open(secret_path, 'r', encoding='utf-8') as secret_file:
            return secret_file.read().strip()

    secret_key = secrets.token_hex(32)
    with open(secret_path, 'w', encoding='utf-8') as secret_file:
        secret_file.write(secret_key)
    return secret_key

app.config['SECRET_KEY'] = load_secret_key()
app.config['USER_DATABASE'] = os.path.join(app.instance_path, 'users.db')
app.config['BUSINESS_DATABASE'] = os.path.join(app.instance_path, 'marketpulse.db')
app.config['SALES_CSV_PATH'] = os.environ.get(
    'SALES_CSV_PATH',
    os.path.join('data', 'random_supermarket_sales_1000.csv'),
)
app.config['HDPOS_IMPORT_FOLDER'] = os.path.join('data', 'hdpos_imports')
app.config['AI_DECISION_CACHE_TTL_SECONDS'] = int(os.environ.get('AI_DECISION_CACHE_TTL_SECONDS', 300))
AI_DECISION_CACHE = {}
os.makedirs(app.config['HDPOS_IMPORT_FOLDER'], exist_ok=True)
SQL_SERVER_SYNC_THREAD = None
SQL_SERVER_SYNC_STOP = threading.Event()
SQL_SERVER_SYNC_LOCK = threading.Lock()

PERISHABLE_CATEGORIES = {
    'bakery',
    'dairy',
    'deli',
    'fresh',
    'fruit',
    'fruits',
    'meat',
    'meat & seafood',
    'meat and seafood',
    'produce',
    'seafood',
    'vegetable',
    'vegetables',
}

DEFAULT_SHELF_LIFE_DAYS = {
    'bakery': 4,
    'beverages': 60,
    'dairy': 7,
    'deli': 5,
    'frozen': 45,
    'fruit': 5,
    'fruits': 5,
    'meat': 4,
    'meat & seafood': 4,
    'meat and seafood': 4,
    'produce': 5,
    'seafood': 3,
    'snacks': 45,
    'vegetable': 5,
    'vegetables': 5,
}

def infer_is_perishable(category):
    return 1 if str(category or '').strip().lower() in PERISHABLE_CATEGORIES else 0

def default_shelf_life_for_category(category):
    category_key = str(category or '').strip().lower()
    return DEFAULT_SHELF_LIFE_DAYS.get(category_key, 30)

# Configure OAuth
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

# Google OAuth
google_bp = make_google_blueprint(
    client_id=os.environ.get('GOOGLE_OAUTH_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET'),
    scope=['profile', 'email']
)
app.register_blueprint(google_bp, url_prefix='/google_login')

# Facebook OAuth
facebook_bp = make_facebook_blueprint(
    client_id=os.environ.get('FACEBOOK_OAUTH_CLIENT_ID'),
    client_secret=os.environ.get('FACEBOOK_OAUTH_CLIENT_SECRET'),
    scope=['email']
)
app.register_blueprint(facebook_bp, url_prefix='/facebook_login')

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, email, password_hash=None, provider=None, provider_user_id=None):
        self.id = str(id)
        self.email = email
        self.password_hash = password_hash
        self.provider = provider
        self.provider_user_id = provider_user_id

    def check_password(self, password):
        return bool(self.password_hash) and check_password_hash(self.password_hash, password)

def get_user_db():
    connection = sqlite3.connect(app.config['USER_DATABASE'])
    connection.row_factory = sqlite3.Row
    return connection

def init_user_store():
    with get_user_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT,
                provider TEXT,
                provider_user_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_provider ON users(provider, provider_user_id)"
        )

def user_from_row(row):
    if row is None:
        return None
    return User(
        id=row['id'],
        email=row['email'],
        password_hash=row['password_hash'],
        provider=row['provider'],
        provider_user_id=row['provider_user_id'],
    )

def find_user_by_id(user_id):
    with get_user_db() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return user_from_row(row)

def find_user_by_email(email):
    with get_user_db() as connection:
        row = connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return user_from_row(row)

def create_user(email, password_hash=None, provider=None, provider_user_id=None):
    with get_user_db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO users (email, password_hash, provider, provider_user_id)
            VALUES (?, ?, ?, ?)
            """,
            (email, password_hash, provider, provider_user_id),
        )
        user_id = cursor.lastrowid
    return User(user_id, email, password_hash, provider, provider_user_id)

@login_manager.user_loader
def load_user(user_id):
    return find_user_by_id(user_id)

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember Me')
    submit = SubmitField('Sign In')

class SignupForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    password2 = PasswordField('Repeat Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Sign Up')

    def validate_email(self, email):
        if find_user_by_email(email.data):
            raise ValidationError('That email is already taken. Please choose a different one.')

init_user_store()

@app.context_processor
def inject_template_globals():
    return {'current_year': datetime.now().year}

def get_business_db():
    connection = sqlite3.connect(app.config['BUSINESS_DATABASE'])
    connection.row_factory = sqlite3.Row
    return connection

def get_integration_setting(key, default=None):
    try:
        with get_business_db() as connection:
            row = connection.execute(
                "SELECT setting_value FROM integration_settings WHERE setting_key = ?",
                (key,),
            ).fetchone()
        return row['setting_value'] if row else default
    except sqlite3.Error:
        return default

def set_integration_setting(key, value):
    with get_business_db() as connection:
        connection.execute(
            """
            INSERT INTO integration_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, value),
        )

def integration_settings_snapshot(keys):
    return {key: get_integration_setting(key, '') for key in keys}

CSV_COLUMN_ALIASES = {
    'Date': [
        'date', 'sale_date', 'sales_date', 'transaction_date', 'order_date',
        'invoice_date', 'bill_date', 'created_at', 'bill_datetime',
        'invoice_datetime'
    ],
    'Product_ID': [
        'product_id', 'productid', 'sku', 'item_id', 'itemid',
        'product_code', 'item_code', 'code'
    ],
    'Barcode': [
        'barcode', 'bar_code', 'ean', 'upc', 'item_barcode', 'product_barcode'
    ],
    'Product_Name': [
        'product_name', 'product', 'item_name', 'item', 'name', 'description',
        'product_description', 'item_description'
    ],
    'Category': [
        'category', 'product_category', 'department', 'dept', 'section',
        'group', 'family'
    ],
    'Units_Sold': [
        'units_sold', 'quantity', 'qty', 'units', 'sold_qty', 'quantity_sold',
        'items_sold', 'sales_units'
    ],
    'Unit_Price': [
        'unit_price', 'price', 'selling_price', 'sale_price', 'retail_price',
        'rate', 'unit_rate', 'mrp'
    ],
    'Profit_Per_Unit': [
        'profit_per_unit', 'unit_profit', 'profit_unit', 'margin_per_unit',
        'gross_profit_per_unit'
    ],
    'Cost_Price': [
        'cost_price', 'unit_cost', 'cost', 'purchase_price', 'buying_price',
        'wholesale_price'
    ],
    'Total_Sale': [
        'total_sale', 'total_sales', 'sales', 'revenue', 'amount',
        'line_total', 'net_sales', 'total_amount', 'sales_amount'
    ],
    'Total_Profit': [
        'total_profit', 'profit', 'gross_profit', 'margin', 'line_profit',
        'profit_amount'
    ],
    'Invoice_No': [
        'invoice_no', 'invoice_number', 'bill_no', 'bill_number', 'receipt_no',
        'receipt_number', 'voucher_no', 'transaction_id'
    ],
    'Batch_No': [
        'batch_no', 'batch_number', 'lot_no', 'lot_number', 'batch', 'lot'
    ],
    'Store_ID': [
        'store_id', 'store', 'location', 'location_id', 'branch', 'branch_id',
        'outlet', 'outlet_id'
    ],
    'Supplier': [
        'supplier', 'supplier_name', 'vendor', 'vendor_name'
    ],
    'Tax_Amount': [
        'tax_amount', 'tax', 'gst', 'gst_amount', 'vat', 'vat_amount',
        'cgst', 'sgst', 'igst'
    ],
    'Discount_Amount': [
        'discount', 'discount_amount', 'line_discount', 'scheme_discount',
        'offer_discount'
    ],
    'Payment_Mode': [
        'payment_mode', 'payment_type', 'payment', 'mode_of_payment',
        'tender_type'
    ],
    'Customer_ID': [
        'customer_id', 'customer', 'customer_code', 'mobile', 'phone',
        'loyalty_id', 'member_id'
    ],
    'Cashier_ID': [
        'cashier', 'cashier_id', 'employee', 'employee_id', 'salesperson',
        'user'
    ],
    'Is_Return': [
        'is_return', 'return', 'returned', 'sale_type', 'transaction_type',
        'document_type'
    ],
    'Expiry_Date': [
        'expiry_date', 'expiration_date', 'exp_date', 'best_before',
        'use_by'
    ],
    'Current_Stock': [
        'current_stock', 'stock', 'stock_on_hand', 'closing_stock',
        'available_stock', 'qty_on_hand'
    ],
    'Reorder_Level': [
        'reorder_level', 'minimum_stock', 'min_stock', 'reorder_point'
    ],
}

OPTIONAL_SALES_COLUMNS = [
    'Invoice_No', 'Barcode', 'Batch_No', 'Store_ID', 'Supplier', 'Tax_Amount',
    'Discount_Amount', 'Payment_Mode', 'Customer_ID', 'Cashier_ID',
    'Is_Return', 'Expiry_Date', 'Current_Stock', 'Reorder_Level'
]

def normalized_column_key(column_name):
    return ''.join(char for char in str(column_name).strip().lower() if char.isalnum())

def clean_numeric_series(series):
    return pd.to_numeric(
        series.astype(str).str.replace(r'[$,₹£€\s]', '', regex=True),
        errors='coerce',
    )

def _safe_float(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def _safe_int(value, default=0):
    try:
        if pd.isna(value):
            return default
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default

def normalize_sales_dataframe(df, source_name='CSV'):
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    df.columns = [str(column).strip() for column in df.columns]
    normalized_columns = {column: normalized_column_key(column) for column in df.columns}
    for canonical, aliases in CSV_COLUMN_ALIASES.items():
        accepted_keys = {normalized_column_key(canonical), *[normalized_column_key(alias) for alias in aliases]}
        matched_columns = [column for column, key in normalized_columns.items() if key in accepted_keys]
        if matched_columns:
            df[canonical] = df[matched_columns].bfill(axis=1).iloc[:, 0]

    if 'Date' not in df.columns:
        app.logger.error("%s is missing a usable date column.", source_name)
        return None

    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    df = df.dropna(subset=['Date'])

    if 'Product_Name' not in df.columns:
        app.logger.error("%s is missing a usable product name column.", source_name)
        return None
    df['Product_Name'] = df['Product_Name'].astype(str).str.strip()
    df = df[df['Product_Name'] != '']

    if 'Category' not in df.columns:
        df['Category'] = 'Uncategorized'
    else:
        df['Category'] = df['Category'].fillna('Uncategorized').astype(str).str.strip().replace('', 'Uncategorized')

    for column in [
        'Units_Sold', 'Unit_Price', 'Profit_Per_Unit', 'Cost_Price',
        'Total_Sale', 'Total_Profit', 'Tax_Amount', 'Discount_Amount',
        'Current_Stock', 'Reorder_Level'
    ]:
        if column in df.columns:
            df[column] = clean_numeric_series(df[column])

    if 'Units_Sold' not in df.columns and {'Total_Sale', 'Unit_Price'}.issubset(df.columns):
        df['Units_Sold'] = np.where(df['Unit_Price'] > 0, df['Total_Sale'] / df['Unit_Price'], np.nan)
    elif {'Units_Sold', 'Total_Sale', 'Unit_Price'}.issubset(df.columns):
        missing_units = df['Units_Sold'].isna()
        df.loc[missing_units, 'Units_Sold'] = np.where(
            df.loc[missing_units, 'Unit_Price'] > 0,
            df.loc[missing_units, 'Total_Sale'] / df.loc[missing_units, 'Unit_Price'],
            np.nan,
        )

    if 'Unit_Price' not in df.columns and {'Total_Sale', 'Units_Sold'}.issubset(df.columns):
        df['Unit_Price'] = np.where(df['Units_Sold'] > 0, df['Total_Sale'] / df['Units_Sold'], np.nan)
    elif {'Unit_Price', 'Total_Sale', 'Units_Sold'}.issubset(df.columns):
        missing_price = df['Unit_Price'].isna()
        df.loc[missing_price, 'Unit_Price'] = np.where(
            df.loc[missing_price, 'Units_Sold'] > 0,
            df.loc[missing_price, 'Total_Sale'] / df.loc[missing_price, 'Units_Sold'],
            np.nan,
        )

    required_numeric = ['Units_Sold', 'Unit_Price']
    missing_numeric = [column for column in required_numeric if column not in df.columns]
    if missing_numeric:
        app.logger.error("%s is missing required sales columns after mapping: %s", source_name, missing_numeric)
        return None

    df = df.dropna(subset=['Units_Sold', 'Unit_Price'])
    df = df[(df['Units_Sold'] >= 0) & (df['Unit_Price'] >= 0)]

    if 'Total_Sale' not in df.columns:
        df['Total_Sale'] = df['Units_Sold'] * df['Unit_Price']
    else:
        df['Total_Sale'] = df['Total_Sale'].fillna(df['Units_Sold'] * df['Unit_Price'])

    if 'Profit_Per_Unit' not in df.columns:
        if 'Total_Profit' in df.columns:
            df['Profit_Per_Unit'] = np.where(df['Units_Sold'] > 0, df['Total_Profit'] / df['Units_Sold'], 0)
        else:
            df['Profit_Per_Unit'] = np.nan
        missing_profit_per_unit = pd.Series(df['Profit_Per_Unit']).isna()
        if 'Cost_Price' in df.columns:
            df.loc[missing_profit_per_unit, 'Profit_Per_Unit'] = (
                df.loc[missing_profit_per_unit, 'Unit_Price'] - df.loc[missing_profit_per_unit, 'Cost_Price']
            )
        if df['Profit_Per_Unit'].isna().any():
            app.logger.warning("%s has rows without profit or cost fields; those profit metrics will show as 0.", source_name)
        df['Profit_Per_Unit'] = df['Profit_Per_Unit'].fillna(0)
    else:
        missing_profit_per_unit = df['Profit_Per_Unit'].isna()
        if 'Total_Profit' in df.columns:
            df.loc[missing_profit_per_unit, 'Profit_Per_Unit'] = np.where(
                df.loc[missing_profit_per_unit, 'Units_Sold'] > 0,
                df.loc[missing_profit_per_unit, 'Total_Profit'] / df.loc[missing_profit_per_unit, 'Units_Sold'],
                np.nan,
            )
            missing_profit_per_unit = df['Profit_Per_Unit'].isna()
        if 'Cost_Price' in df.columns:
            df.loc[missing_profit_per_unit, 'Profit_Per_Unit'] = (
                df.loc[missing_profit_per_unit, 'Unit_Price'] - df.loc[missing_profit_per_unit, 'Cost_Price']
            )
        df['Profit_Per_Unit'] = df['Profit_Per_Unit'].fillna(0)

    if 'Total_Profit' not in df.columns:
        df['Total_Profit'] = df['Units_Sold'] * df['Profit_Per_Unit']
    else:
        df['Total_Profit'] = df['Total_Profit'].fillna(df['Units_Sold'] * df['Profit_Per_Unit'])

    if 'Expiry_Date' in df.columns:
        df['Expiry_Date'] = pd.to_datetime(df['Expiry_Date'], errors='coerce').dt.strftime('%Y-%m-%d')

    if 'Is_Return' in df.columns:
        return_text = df['Is_Return'].astype(str).str.strip().str.lower()
        df['Is_Return'] = return_text.isin({'1', 'true', 'yes', 'return', 'returned', 'sales return', 'refund'}).astype(int)

    if 'Product_ID' not in df.columns:
        if 'Barcode' in df.columns:
            product_keys = df['Barcode'].fillna('').astype(str).str.strip()
            missing_barcode = product_keys == ''
            product_keys.loc[missing_barcode] = (
                df.loc[missing_barcode, 'Product_Name'].str.lower() + '|' +
                df.loc[missing_barcode, 'Category'].str.lower()
            )
        else:
            product_keys = (df['Product_Name'].str.lower() + '|' + df['Category'].str.lower())
        df['Product_ID'] = pd.factorize(product_keys)[0] + 1
        df['Product_ID'] = df['Product_ID'].map(lambda value: f"P{int(value):05d}")
    else:
        missing_ids = df['Product_ID'].isna() | (df['Product_ID'].astype(str).str.strip() == '')
        if missing_ids.any():
            generated_ids = pd.factorize((df.loc[missing_ids, 'Product_Name'].str.lower() + '|' + df.loc[missing_ids, 'Category'].str.lower()))[0] + 1
            df.loc[missing_ids, 'Product_ID'] = [f"P{int(value):05d}" for value in generated_ids]
        df['Product_ID'] = df['Product_ID'].astype(str).str.strip()

    canonical_columns = [
        'Date', 'Product_ID', 'Product_Name', 'Category', 'Units_Sold',
        'Unit_Price', 'Profit_Per_Unit', 'Total_Sale', 'Total_Profit'
    ]
    for column in OPTIONAL_SALES_COLUMNS:
        if column in df.columns and column not in canonical_columns:
            canonical_columns.append(column)
    normalized = df[canonical_columns].copy()
    normalized['Units_Sold'] = normalized['Units_Sold'].round().astype(int)
    for column in OPTIONAL_SALES_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = None
    invoice_values = normalized['Invoice_No'].fillna('').astype(str).str.strip()
    missing_invoice = invoice_values == ''
    if missing_invoice.any():
        generated_sequence = normalized.groupby(normalized['Date'].dt.strftime('%Y%m%d')).cumcount() + 1
        source_token = hashlib.sha1(str(source_name).encode('utf-8')).hexdigest()[:6].upper()
        generated_invoice = (
            'AUTO-' +
            source_token +
            '-' +
            normalized['Date'].dt.strftime('%Y%m%d') +
            '-' +
            generated_sequence.astype(str).str.zfill(5)
        )
        normalized.loc[missing_invoice, 'Invoice_No'] = generated_invoice.loc[missing_invoice]
    normalized['Invoice_No'] = normalized['Invoice_No'].astype(str).str.strip()
    return normalized.sort_values('Date')

def read_csv_sales_data():
    configured_csv = get_integration_setting('active_sales_csv_path') or app.config.get('SALES_CSV_PATH')
    if configured_csv and os.path.exists(configured_csv):
        csv_files = [configured_csv]
    else:
        csv_files = glob.glob(os.path.join('data', '*.csv'))
    if not csv_files:
        return None

    frames = []
    for csv_file in csv_files:
        try:
            normalized = normalize_sales_dataframe(pd.read_csv(csv_file), csv_file)
            if normalized is not None and not normalized.empty:
                frames.append(normalized)
        except Exception as exc:
            app.logger.warning("Could not read %s: %s", csv_file, exc)

    if not frames:
        return None

    df = pd.concat(frames, ignore_index=True)
    return df.sort_values('Date')

def init_business_store():
    with get_business_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL UNIQUE,
                product_name TEXT NOT NULL,
                category TEXT NOT NULL,
                unit_price REAL NOT NULL,
                profit_per_unit REAL NOT NULL,
                cost_price REAL NOT NULL,
                current_stock INTEGER NOT NULL DEFAULT 0,
                reorder_level INTEGER NOT NULL DEFAULT 10,
                barcode TEXT,
                supplier TEXT,
                expiry_date TEXT,
                is_perishable INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        existing_columns = {
            row['name']
            for row in connection.execute("PRAGMA table_info(inventory_items)").fetchall()
        }
        if 'expiry_date' not in existing_columns:
            connection.execute("ALTER TABLE inventory_items ADD COLUMN expiry_date TEXT")
        if 'is_perishable' not in existing_columns:
            connection.execute("ALTER TABLE inventory_items ADD COLUMN is_perishable INTEGER")
        if 'barcode' not in existing_columns:
            connection.execute("ALTER TABLE inventory_items ADD COLUMN barcode TEXT")
        if 'supplier' not in existing_columns:
            connection.execute("ALTER TABLE inventory_items ADD COLUMN supplier TEXT")
        connection.execute(
            """
            UPDATE inventory_items
            SET is_perishable = CASE
                WHEN LOWER(category) IN ('bakery', 'dairy', 'deli', 'fresh', 'fruit', 'fruits', 'meat',
                                         'meat & seafood', 'meat and seafood', 'produce', 'seafood',
                                         'vegetable', 'vegetables')
                THEN 1 ELSE 0 END
            WHERE is_perishable IS NULL
            """
        )
        connection.execute("DROP INDEX IF EXISTS idx_inventory_product_name_unique")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sales_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_date TEXT NOT NULL,
                product_id TEXT NOT NULL,
                product_name TEXT NOT NULL,
                category TEXT NOT NULL,
                units_sold INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                profit_per_unit REAL NOT NULL,
                total_sale REAL NOT NULL,
                total_profit REAL NOT NULL,
                invoice_no TEXT,
                barcode TEXT,
                batch_no TEXT,
                store_id TEXT,
                supplier TEXT,
                tax_amount REAL NOT NULL DEFAULT 0,
                discount_amount REAL NOT NULL DEFAULT 0,
                payment_mode TEXT,
                customer_id TEXT,
                cashier_id TEXT,
                is_return INTEGER NOT NULL DEFAULT 0,
                expiry_date TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        sales_columns = {
            row['name']
            for row in connection.execute("PRAGMA table_info(sales_records)").fetchall()
        }
        sales_column_defaults = {
            'invoice_no': 'TEXT',
            'barcode': 'TEXT',
            'batch_no': 'TEXT',
            'store_id': 'TEXT',
            'supplier': 'TEXT',
            'tax_amount': 'REAL NOT NULL DEFAULT 0',
            'discount_amount': 'REAL NOT NULL DEFAULT 0',
            'payment_mode': 'TEXT',
            'customer_id': 'TEXT',
            'cashier_id': 'TEXT',
            'is_return': 'INTEGER NOT NULL DEFAULT 0',
            'expiry_date': 'TEXT',
        }
        for column, definition in sales_column_defaults.items():
            if column not in sales_columns:
                connection.execute(f"ALTER TABLE sales_records ADD COLUMN {column} {definition}")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sales_date ON sales_records(sale_date)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sales_product ON sales_records(product_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sales_store ON sales_records(store_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sales_invoice ON sales_records(invoice_no)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS integration_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS integration_import_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                file_name TEXT,
                rows_imported INTEGER NOT NULL DEFAULT 0,
                rows_failed INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                message TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        item_count = connection.execute("SELECT COUNT(*) FROM inventory_items").fetchone()[0]
        sales_count = connection.execute("SELECT COUNT(*) FROM sales_records").fetchone()[0]

    if item_count == 0 and sales_count == 0:
        seed_business_store_from_csv()

def seed_business_store_from_csv():
    df = read_csv_sales_data()
    if df is None or df.empty:
        return

    with get_business_db() as connection:
        product_rows = df.groupby(['Product_ID', 'Product_Name', 'Category'], as_index=False).agg({
            'Unit_Price': 'mean',
            'Profit_Per_Unit': 'mean',
            'Units_Sold': 'sum',
        })
        for _, row in product_rows.iterrows():
            current_stock = int(max(20, row['Units_Sold'] * 0.2))
            unit_price = float(row['Unit_Price'])
            profit_per_unit = float(row['Profit_Per_Unit'])
            connection.execute(
                """
                INSERT OR IGNORE INTO inventory_items
                    (product_id, product_name, category, unit_price, profit_per_unit, cost_price, current_stock, reorder_level, barcode, supplier, expiry_date, is_perishable)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row['Product_ID'],
                    row['Product_Name'],
                    row['Category'],
                    unit_price,
                    profit_per_unit,
                    max(0, unit_price - profit_per_unit),
                    current_stock,
                    10,
                    row.get('Barcode'),
                    row.get('Supplier'),
                    row.get('Expiry_Date'),
                    infer_is_perishable(row['Category']),
                ),
            )

        for _, row in df.iterrows():
            connection.execute(
                """
                INSERT INTO sales_records
                    (sale_date, product_id, product_name, category, units_sold, unit_price, profit_per_unit, total_sale, total_profit,
                     invoice_no, barcode, batch_no, store_id, supplier, tax_amount, discount_amount, payment_mode, customer_id, cashier_id, is_return, expiry_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pd.to_datetime(row['Date']).strftime('%Y-%m-%d'),
                    row['Product_ID'],
                    row['Product_Name'],
                    row['Category'],
                    int(row['Units_Sold']),
                    float(row['Unit_Price']),
                    float(row['Profit_Per_Unit']),
                    float(row['Total_Sale']),
                    float(row['Total_Profit']),
                    row.get('Invoice_No'),
                    row.get('Barcode'),
                    row.get('Batch_No'),
                    row.get('Store_ID'),
                    row.get('Supplier'),
                    _safe_float(row.get('Tax_Amount')),
                    _safe_float(row.get('Discount_Amount')),
                    row.get('Payment_Mode'),
                    row.get('Customer_ID'),
                    row.get('Cashier_ID'),
                    _safe_int(row.get('Is_Return')),
                    row.get('Expiry_Date'),
                ),
            )

def ensure_inventory_products_for_sales(df):
    """Add inventory rows for products in the active sales source without overwriting existing stock."""
    if df is None or df.empty:
        return

    product_rows = df.groupby(['Product_ID', 'Product_Name', 'Category'], as_index=False).agg({
        'Unit_Price': 'mean',
        'Profit_Per_Unit': 'mean',
        'Units_Sold': 'sum',
    })

    with get_business_db() as connection:
        existing_rows = connection.execute(
            "SELECT product_id, product_name, category FROM inventory_items"
        ).fetchall()
        existing_ids = {str(row['product_id']).strip().lower() for row in existing_rows}
        existing_name_categories = {
            (str(row['product_name']).strip().lower(), str(row['category']).strip().lower())
            for row in existing_rows
        }

        for _, row in product_rows.iterrows():
            product_id = str(row['Product_ID']).strip()
            product_key = (
                str(row['Product_Name']).strip().lower(),
                str(row['Category']).strip().lower(),
            )
            if product_id.lower() in existing_ids or product_key in existing_name_categories:
                continue

            units_sold = float(row['Units_Sold'])
            current_stock = int(max(20, round(units_sold * 0.20)))
            if 'Current_Stock' in row.index and not pd.isna(row.get('Current_Stock')):
                current_stock = max(0, _safe_int(row.get('Current_Stock')))
            reorder_level = int(max(10, round(units_sold * 0.04)))
            if 'Reorder_Level' in row.index and not pd.isna(row.get('Reorder_Level')):
                reorder_level = max(0, _safe_int(row.get('Reorder_Level')))
            unit_price = float(row['Unit_Price'])
            profit_per_unit = float(row['Profit_Per_Unit'])
            connection.execute(
                """
                INSERT OR IGNORE INTO inventory_items
                    (product_id, product_name, category, unit_price, profit_per_unit, cost_price, current_stock, reorder_level, barcode, supplier, expiry_date, is_perishable)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    row['Product_Name'],
                    row['Category'],
                    unit_price,
                    profit_per_unit,
                    max(0, unit_price - profit_per_unit),
                    current_stock,
                    reorder_level,
                    row.get('Barcode'),
                    row.get('Supplier'),
                    row.get('Expiry_Date'),
                    infer_is_perishable(row['Category']),
                ),
            )
            existing_ids.add(product_id.lower())
            existing_name_categories.add(product_key)

def load_data():
    """Load sales records from the configured CSV, falling back to database records."""
    csv_df = read_csv_sales_data()
    if csv_df is not None and not csv_df.empty:
        ensure_inventory_products_for_sales(csv_df)
        return csv_df

    with get_business_db() as connection:
        rows = connection.execute(
            """
            SELECT
                sale_date AS Date,
                product_id AS Product_ID,
                product_name AS Product_Name,
                category AS Category,
                units_sold AS Units_Sold,
                unit_price AS Unit_Price,
                profit_per_unit AS Profit_Per_Unit,
                total_sale AS Total_Sale,
                total_profit AS Total_Profit,
                invoice_no AS Invoice_No,
                barcode AS Barcode,
                batch_no AS Batch_No,
                store_id AS Store_ID,
                supplier AS Supplier,
                tax_amount AS Tax_Amount,
                discount_amount AS Discount_Amount,
                payment_mode AS Payment_Mode,
                customer_id AS Customer_ID,
                cashier_id AS Cashier_ID,
                is_return AS Is_Return,
                expiry_date AS Expiry_Date
            FROM sales_records
            ORDER BY sale_date
            """
        ).fetchall()

    if rows:
        df = pd.DataFrame([dict(row) for row in rows])
        df['Date'] = pd.to_datetime(df['Date'])
        return df.sort_values('Date')

    return read_csv_sales_data()

init_business_store()

def log_integration_import(source_name, file_name, rows_imported, rows_failed, status, message):
    with get_business_db() as connection:
        connection.execute(
            """
            INSERT INTO integration_import_logs
                (source_name, file_name, rows_imported, rows_failed, status, message)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source_name, file_name, rows_imported, rows_failed, status, message),
        )
    cleanup_integration_logs()

def cleanup_integration_logs(retention_days=5):
    cutoff = (datetime.now() - timedelta(days=retention_days)).strftime('%Y-%m-%d %H:%M:%S')
    with get_business_db() as connection:
        connection.execute(
            """
            DELETE FROM integration_import_logs
            WHERE datetime(created_at) < datetime(?)
            """,
            (cutoff,),
        )

def load_integration_logs(limit=12):
    cleanup_integration_logs()
    with get_business_db() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM integration_import_logs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]

def integration_summary():
    active_source = get_integration_setting('active_sales_csv_path') or app.config.get('SALES_CSV_PATH')
    df = read_csv_sales_data()
    sql_sync_enabled = get_integration_setting('sql_server_sync_enabled', '0') == '1'
    sql_last_sync = get_integration_setting('sql_server_last_sync_at', '')
    sql_last_status = get_integration_setting('sql_server_last_status', '')
    sql_interval = get_integration_setting('sql_server_sync_interval_minutes', '15')
    if df is None or df.empty:
        return {
            'active_source': active_source or 'Not configured',
            'rows': 0,
            'products': 0,
            'stores': 0,
            'date_range': 'No data loaded',
            'has_hdpos_fields': False,
            'sql_sync_enabled': sql_sync_enabled,
            'sql_last_sync': sql_last_sync,
            'sql_last_status': sql_last_status,
            'sql_interval': sql_interval,
        }

    optional_presence = {
        column: column in df.columns and df[column].notna().any()
        for column in OPTIONAL_SALES_COLUMNS
    }
    return {
        'active_source': active_source,
        'rows': len(df),
        'products': df['Product_ID'].nunique(),
        'stores': df['Store_ID'].dropna().nunique() if 'Store_ID' in df.columns else 0,
        'date_range': f"{df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}",
        'has_hdpos_fields': any(optional_presence.values()),
        'optional_presence': optional_presence,
        'sql_sync_enabled': sql_sync_enabled,
        'sql_last_sync': sql_last_sync,
        'sql_last_status': sql_last_status,
        'sql_interval': sql_interval,
    }

def save_normalized_import(file_storage, source_name='HDPOS CSV'):
    if not file_storage or not file_storage.filename:
        return None, 'Choose a CSV file to import.'

    filename = secure_filename(file_storage.filename)
    if not filename.lower().endswith('.csv'):
        return None, 'Only CSV imports are supported in this version.'

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    raw_path = os.path.join(app.config['HDPOS_IMPORT_FOLDER'], f"{timestamp}_{filename}")
    normalized_path = os.path.join(app.config['HDPOS_IMPORT_FOLDER'], f"{timestamp}_normalized_{filename}")
    file_storage.save(raw_path)

    try:
        raw_df = pd.read_csv(raw_path)
        normalized = normalize_sales_dataframe(raw_df, source_name)
    except Exception as exc:
        log_integration_import(source_name, filename, 0, 0, 'failed', f'Could not read file: {exc}')
        return None, f'Could not read file: {exc}'

    if normalized is None or normalized.empty:
        log_integration_import(source_name, filename, 0, len(raw_df) if 'raw_df' in locals() else 0, 'failed', 'No usable sales rows found.')
        return None, 'No usable sales rows found. Check date, product, quantity, and price columns.'

    normalized.to_csv(normalized_path, index=False)
    set_integration_setting('active_sales_csv_path', normalized_path)
    set_integration_setting('integration_mode', 'csv')
    set_integration_setting('last_source_name', source_name)
    ensure_inventory_products_for_sales(normalized)
    log_integration_import(source_name, filename, len(normalized), max(0, len(raw_df) - len(normalized)), 'success', 'Import normalized and activated.')
    AI_DECISION_CACHE.clear()
    return normalized_path, f'Imported {len(normalized)} rows and activated this source.'

def sqlite_table_names(database_path):
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
    return [row[0] for row in rows]

def score_sqlite_table(columns):
    normalized_columns = {normalized_column_key(column) for column in columns}
    score = 0
    required_groups = [
        ('Date', 4),
        ('Product_Name', 4),
        ('Units_Sold', 4),
        ('Unit_Price', 3),
        ('Total_Sale', 2),
    ]
    for canonical, weight in required_groups:
        aliases = CSV_COLUMN_ALIASES.get(canonical, [])
        accepted = {normalized_column_key(canonical), *[normalized_column_key(alias) for alias in aliases]}
        if normalized_columns.intersection(accepted):
            score += weight
    for canonical in OPTIONAL_SALES_COLUMNS:
        aliases = CSV_COLUMN_ALIASES.get(canonical, [])
        accepted = {normalized_column_key(canonical), *[normalized_column_key(alias) for alias in aliases]}
        if normalized_columns.intersection(accepted):
            score += 1
    return score

def quote_sqlite_identifier(identifier):
    return '"' + str(identifier).replace('"', '""') + '"'

def choose_sqlite_sales_table(database_path, preferred_table=''):
    table_names = sqlite_table_names(database_path)
    if not table_names:
        return None, [], 'No tables found in this SQLite database.'

    if preferred_table:
        exact_match = next((table for table in table_names if table.lower() == preferred_table.lower()), None)
        if not exact_match:
            return None, table_names, f'Table "{preferred_table}" was not found in this SQLite database.'
        return exact_match, table_names, None

    scored_tables = []
    with sqlite3.connect(database_path) as connection:
        for table_name in table_names:
            quoted_table = quote_sqlite_identifier(table_name)
            try:
                columns = [row[1] for row in connection.execute(f'PRAGMA table_info({quoted_table})').fetchall()]
                row_count = connection.execute(f'SELECT COUNT(*) FROM {quoted_table}').fetchone()[0]
            except sqlite3.Error:
                continue
            scored_tables.append((score_sqlite_table(columns), row_count, table_name))

    scored_tables.sort(reverse=True)
    if not scored_tables or scored_tables[0][0] < 12:
        return None, table_names, 'Could not detect a sales table. Enter the exact sales table name and try again.'
    return scored_tables[0][2], table_names, None

def save_normalized_sqlite_import(file_storage, source_name='HDPOS SQLite', preferred_table=''):
    if not file_storage or not file_storage.filename:
        return None, 'Choose a SQLite database file to import.'

    filename = secure_filename(file_storage.filename)
    allowed_extensions = ('.db', '.sqlite', '.sqlite3')
    if not filename.lower().endswith(allowed_extensions):
        return None, 'Only .db, .sqlite, or .sqlite3 files are supported for SQLite import.'

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    raw_path = os.path.join(app.config['HDPOS_IMPORT_FOLDER'], f"{timestamp}_{filename}")
    normalized_filename = f"{os.path.splitext(filename)[0]}_normalized.csv"
    normalized_path = os.path.join(app.config['HDPOS_IMPORT_FOLDER'], f"{timestamp}_{normalized_filename}")
    file_storage.save(raw_path)

    selected_table = None
    try:
        selected_table, table_names, table_error = choose_sqlite_sales_table(raw_path, preferred_table.strip())
        if table_error:
            log_integration_import(source_name, filename, 0, 0, 'failed', table_error)
            return None, f'{table_error} Available tables: {", ".join(table_names[:12]) or "none"}.'
        with sqlite3.connect(raw_path) as connection:
            raw_df = pd.read_sql_query(f'SELECT * FROM {quote_sqlite_identifier(selected_table)}', connection)
        normalized = normalize_sales_dataframe(raw_df, f'{source_name}:{selected_table}')
    except Exception as exc:
        log_integration_import(source_name, filename, 0, 0, 'failed', f'Could not read SQLite database: {exc}')
        return None, f'Could not read SQLite database: {exc}'

    if normalized is None or normalized.empty:
        log_integration_import(source_name, filename, 0, len(raw_df) if 'raw_df' in locals() else 0, 'failed', f'No usable sales rows found in table {selected_table}.')
        return None, f'No usable sales rows found in table "{selected_table}". Check date, product, quantity, and price columns.'

    normalized.to_csv(normalized_path, index=False)
    set_integration_setting('active_sales_csv_path', normalized_path)
    set_integration_setting('integration_mode', 'sqlite_snapshot')
    set_integration_setting('last_source_name', source_name)
    set_integration_setting('last_sqlite_table', selected_table)
    ensure_inventory_products_for_sales(normalized)
    log_integration_import(
        source_name,
        f'{filename}::{selected_table}',
        len(normalized),
        max(0, len(raw_df) - len(normalized)),
        'success',
        'SQLite table normalized and activated.',
    )
    AI_DECISION_CACHE.clear()
    return normalized_path, f'Imported {len(normalized)} rows from SQLite table "{selected_table}" and activated this source.'

SQL_SERVER_SETTING_KEYS = [
    'sql_server_driver',
    'sql_server_host',
    'sql_server_port',
    'sql_server_database',
    'sql_server_username',
    'sql_server_password',
    'sql_server_encrypt',
    'sql_server_trust_certificate',
    'sql_server_query',
    'sql_server_sync_enabled',
    'sql_server_sync_interval_minutes',
    'sql_server_last_sync_at',
    'sql_server_last_status',
]

DEFAULT_SQL_SERVER_QUERY = """SELECT
    h.InvoiceNo AS Invoice_No,
    h.InvoiceDate AS Date,
    d.ProductCode AS Product_ID,
    p.ProductName AS Product_Name,
    p.CategoryName AS Category,
    d.Quantity AS Units_Sold,
    d.UnitPrice AS Unit_Price,
    d.LineTotal AS Total_Sale,
    d.LineProfit AS Total_Profit,
    d.DiscountAmount AS Discount_Amount,
    d.TaxAmount AS Tax_Amount,
    h.PaymentMode AS Payment_Mode,
    h.CustomerID AS Customer_ID,
    h.CashierID AS Cashier_ID,
    h.StoreID AS Store_ID
FROM InvoiceHeader h
JOIN InvoiceDetail d ON d.InvoiceID = h.InvoiceID
LEFT JOIN Products p ON p.ProductCode = d.ProductCode
WHERE h.InvoiceDate >= DATEADD(day, -90, GETDATE())"""

def sql_server_settings():
    settings = integration_settings_snapshot(SQL_SERVER_SETTING_KEYS)
    return {
        'driver': settings.get('sql_server_driver') or 'ODBC Driver 18 for SQL Server',
        'host': settings.get('sql_server_host') or '',
        'port': settings.get('sql_server_port') or '1433',
        'database': settings.get('sql_server_database') or '',
        'username': settings.get('sql_server_username') or '',
        'password': settings.get('sql_server_password') or '',
        'encrypt': settings.get('sql_server_encrypt') or 'yes',
        'trust_certificate': settings.get('sql_server_trust_certificate') or 'yes',
        'query': settings.get('sql_server_query') or DEFAULT_SQL_SERVER_QUERY,
        'sync_enabled': settings.get('sql_server_sync_enabled') == '1',
        'sync_interval_minutes': settings.get('sql_server_sync_interval_minutes') or '15',
        'last_sync_at': settings.get('sql_server_last_sync_at') or '',
        'last_status': settings.get('sql_server_last_status') or '',
    }

def save_sql_server_settings_from_form(form):
    fields = {
        'sql_server_driver': form.get('sql_server_driver', '').strip() or 'ODBC Driver 18 for SQL Server',
        'sql_server_host': form.get('sql_server_host', '').strip(),
        'sql_server_port': form.get('sql_server_port', '').strip() or '1433',
        'sql_server_database': form.get('sql_server_database', '').strip(),
        'sql_server_username': form.get('sql_server_username', '').strip(),
        'sql_server_password': form.get('sql_server_password', '').strip(),
        'sql_server_encrypt': form.get('sql_server_encrypt', 'yes').strip() or 'yes',
        'sql_server_trust_certificate': form.get('sql_server_trust_certificate', 'yes').strip() or 'yes',
        'sql_server_query': form.get('sql_server_query', '').strip() or DEFAULT_SQL_SERVER_QUERY,
        'sql_server_sync_enabled': '1' if form.get('sql_server_sync_enabled') == '1' else '0',
        'sql_server_sync_interval_minutes': str(max(1, _safe_int(form.get('sql_server_sync_interval_minutes'), 15))),
    }
    for key, value in fields.items():
        set_integration_setting(key, value)
    return fields

def validate_read_only_sql(query):
    compact = ' '.join((query or '').strip().split())
    lowered = compact.lower()
    if not compact:
        return False, 'Enter a SQL Server SELECT query.'
    if not (lowered.startswith('select ') or lowered.startswith('with ')):
        return False, 'Only read-only SELECT queries are allowed.'
    forbidden_tokens = [
        ' insert ', ' update ', ' delete ', ' drop ', ' alter ', ' create ', ' truncate ',
        ' merge ', ' exec ', ' execute ', ' grant ', ' revoke ', ' into '
    ]
    padded = f' {lowered} '
    if ';' in compact or any(token in padded for token in forbidden_tokens):
        return False, 'The query must be a single read-only SELECT without write/DDL statements.'
    return True, ''

def import_pyodbc():
    try:
        import pyodbc
        return pyodbc, None
    except ImportError:
        return None, 'pyodbc is not installed. Run: pip install -r requirements.txt'

def sql_server_connection_string(settings):
    server = settings['host']
    if settings.get('port'):
        server = f"{server},{settings['port']}"
    return (
        f"DRIVER={{{settings['driver']}}};"
        f"SERVER={server};"
        f"DATABASE={settings['database']};"
        f"UID={settings['username']};"
        f"PWD={settings['password']};"
        f"Encrypt={settings['encrypt']};"
        f"TrustServerCertificate={settings['trust_certificate']};"
        "Connection Timeout=10;"
    )

def read_sql_server_sales(settings):
    is_valid, validation_message = validate_read_only_sql(settings.get('query', ''))
    if not is_valid:
        raise ValueError(validation_message)

    pyodbc, import_error = import_pyodbc()
    if import_error:
        raise RuntimeError(import_error)

    required = ['host', 'database', 'username', 'password']
    missing = [field for field in required if not settings.get(field)]
    if missing:
        raise ValueError(f"Missing SQL Server settings: {', '.join(missing)}")

    connection = pyodbc.connect(sql_server_connection_string(settings))
    try:
        return pd.read_sql_query(settings['query'], connection)
    finally:
        connection.close()

def test_sql_server_connection(settings):
    pyodbc, import_error = import_pyodbc()
    if import_error:
        return False, import_error
    required = ['host', 'database', 'username', 'password']
    missing = [field for field in required if not settings.get(field)]
    if missing:
        return False, f"Missing SQL Server settings: {', '.join(missing)}"
    try:
        connection = pyodbc.connect(sql_server_connection_string(settings))
        try:
            cursor = connection.cursor()
            cursor.execute('SELECT 1')
            cursor.fetchone()
        finally:
            connection.close()
        is_valid, validation_message = validate_read_only_sql(settings.get('query', ''))
        if not is_valid:
            return False, validation_message
        return True, 'SQL Server connection succeeded and the import query is read-only.'
    except Exception as exc:
        return False, f'Could not connect to SQL Server: {exc}'

def sync_sql_server_sales(source_name='SQL Server POS'):
    with SQL_SERVER_SYNC_LOCK:
        settings = sql_server_settings()
        try:
            raw_df = read_sql_server_sales(settings)
            normalized = normalize_sales_dataframe(raw_df, source_name)
            if normalized is None or normalized.empty:
                raise ValueError('No usable sales rows returned. Check query aliases and invoice line fields.')

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            normalized_path = os.path.join(app.config['HDPOS_IMPORT_FOLDER'], f'{timestamp}_sql_server_pos_normalized.csv')
            normalized.to_csv(normalized_path, index=False)
            set_integration_setting('active_sales_csv_path', normalized_path)
            set_integration_setting('integration_mode', 'sql_server')
            set_integration_setting('last_source_name', source_name)
            set_integration_setting('sql_server_last_sync_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            set_integration_setting('sql_server_last_status', f'Success: {len(normalized)} rows')
            ensure_inventory_products_for_sales(normalized)
            log_integration_import(
                source_name,
                f"{settings.get('host')}::{settings.get('database')}",
                len(normalized),
                max(0, len(raw_df) - len(normalized)),
                'success',
                'SQL Server POS sync normalized and activated.',
            )
            AI_DECISION_CACHE.clear()
            return True, f'Synced {len(normalized)} SQL Server POS rows and activated this source.'
        except Exception as exc:
            message = f'SQL Server sync failed: {exc}'
            set_integration_setting('sql_server_last_status', message)
            log_integration_import(source_name, 'SQL Server', 0, 0, 'failed', message)
            return False, message

def sql_server_sync_loop():
    while not SQL_SERVER_SYNC_STOP.is_set():
        if get_integration_setting('sql_server_sync_enabled', '0') == '1':
            sync_sql_server_sales('SQL Server POS Scheduled Sync')
        interval_minutes = max(1, _safe_int(get_integration_setting('sql_server_sync_interval_minutes', '15'), 15))
        SQL_SERVER_SYNC_STOP.wait(interval_minutes * 60)

def start_sql_server_scheduler():
    global SQL_SERVER_SYNC_THREAD
    if SQL_SERVER_SYNC_THREAD and SQL_SERVER_SYNC_THREAD.is_alive():
        return
    SQL_SERVER_SYNC_STOP.clear()
    SQL_SERVER_SYNC_THREAD = threading.Thread(target=sql_server_sync_loop, name='sql-server-pos-sync', daemon=True)
    SQL_SERVER_SYNC_THREAD.start()

def apply_date_filter(df):
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    selected_range = request.args.get('range', '')
    valid_ranges = {'today', 'yesterday', 'last7days', 'thismonth', 'lastmonth', 'ytd', 'custom'}
    if selected_range not in valid_ranges:
        selected_range = ''

    min_date = df['Date'].min()
    max_date = df['Date'].max()
    start_date = min_date
    end_date = max_date

    if start_date_str and end_date_str:
        try:
            start_date = max(pd.to_datetime(start_date_str), min_date)
            end_date = min(pd.to_datetime(end_date_str), max_date)
            if start_date > end_date:
                start_date, end_date = end_date, start_date
        except Exception:
            start_date = min_date
            end_date = max_date

    filtered_df = df[(df['Date'] >= start_date) & (df['Date'] <= end_date)]
    if filtered_df.empty:
        filtered_df = df
        start_date = min_date
        end_date = max_date

    if not selected_range and start_date_str and end_date_str:
        anchor_date = max_date.normalize()
        preset_ranges = {
            'today': (anchor_date, anchor_date),
            'yesterday': (anchor_date - pd.Timedelta(days=1), anchor_date - pd.Timedelta(days=1)),
            'last7days': (anchor_date - pd.Timedelta(days=6), anchor_date),
            'thismonth': (anchor_date.replace(day=1), anchor_date),
            'lastmonth': (
                (anchor_date.replace(day=1) - pd.DateOffset(months=1)).normalize(),
                (anchor_date.replace(day=1) - pd.Timedelta(days=1)).normalize(),
            ),
            'ytd': (anchor_date.replace(month=1, day=1), anchor_date),
        }
        for range_name, (preset_start, preset_end) in preset_ranges.items():
            preset_start = max(preset_start, min_date)
            preset_end = min(preset_end, max_date)
            if start_date.normalize() == preset_start.normalize() and end_date.normalize() == preset_end.normalize():
                selected_range = range_name
                break
        if not selected_range:
            selected_range = 'custom'

    is_custom_range = selected_range == 'custom'

    return filtered_df, {
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'min_date': min_date.strftime('%Y-%m-%d'),
        'max_date': max_date.strftime('%Y-%m-%d'),
        'period_label': f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}",
        'is_custom_range': is_custom_range,
        'selected_range': selected_range,
    }

def load_inventory_items():
    with get_business_db() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM inventory_items
            ORDER BY product_name
            """
        ).fetchall()
    return [dict(row) for row in rows]

def inventory_metrics(items):
    total_products = len(items)
    total_units = sum(int(item['current_stock']) for item in items)
    inventory_value = sum(float(item['current_stock']) * float(item['cost_price']) for item in items)
    low_stock = sum(1 for item in items if int(item['current_stock']) <= int(item['reorder_level']))
    return {
        'inventory_total_products': total_products,
        'inventory_total_units': total_units,
        'inventory_value': inventory_value,
        'inventory_low_stock': low_stock,
    }

def build_product_performance(df):
    if df is not None and not df.empty:
        performance = df.groupby(['Product_ID', 'Product_Name', 'Category'], as_index=False).agg({
            'Units_Sold': 'sum',
            'Total_Sale': 'sum',
            'Total_Profit': 'sum',
        })
    else:
        performance = pd.DataFrame(columns=['Product_ID', 'Product_Name', 'Category', 'Units_Sold', 'Total_Sale', 'Total_Profit'])

    inventory = pd.DataFrame(load_inventory_items())
    if not inventory.empty:
        inventory = inventory.rename(columns={
            'product_id': 'Product_ID',
            'product_name': 'Product_Name',
            'category': 'Category',
            'current_stock': 'Current_Stock',
            'reorder_level': 'Reorder_Level',
        })
        inventory = inventory[['Product_ID', 'Product_Name', 'Category', 'Current_Stock', 'Reorder_Level']]
        performance = performance.merge(inventory, on=['Product_ID', 'Product_Name', 'Category'], how='left')

        missing_stock = performance['Current_Stock'].isna()
        if missing_stock.any():
            fallback_inventory = (
                inventory.groupby(['Product_Name', 'Category'], as_index=False)
                .agg({'Current_Stock': 'sum', 'Reorder_Level': 'max'})
                .rename(columns={
                    'Current_Stock': 'Fallback_Current_Stock',
                    'Reorder_Level': 'Fallback_Reorder_Level',
                })
            )
            performance = performance.merge(fallback_inventory, on=['Product_Name', 'Category'], how='left')
            performance['Current_Stock'] = performance['Current_Stock'].fillna(performance['Fallback_Current_Stock'])
            performance['Reorder_Level'] = performance['Reorder_Level'].fillna(performance['Fallback_Reorder_Level'])
            performance = performance.drop(columns=['Fallback_Current_Stock', 'Fallback_Reorder_Level'])
    else:
        performance['Current_Stock'] = 0
        performance['Reorder_Level'] = 0

    for column in ['Units_Sold', 'Total_Sale', 'Total_Profit', 'Current_Stock', 'Reorder_Level']:
        performance[column] = performance[column].fillna(0)

    records = performance.to_dict('records')
    top_products = sorted(records, key=lambda item: (item['Units_Sold'], item['Total_Sale']), reverse=True)[:10]
    worst_products = sorted(records, key=lambda item: (item['Units_Sold'], item['Total_Sale'], item['Total_Profit']))[:10]
    return {
        'top_products_list': top_products,
        'worst_products_list': worst_products,
    }

def build_invoice_line_frame(df):
    if df is None or df.empty:
        return pd.DataFrame()

    invoice_df = df.copy()
    invoice_df['Date'] = pd.to_datetime(invoice_df['Date'], errors='coerce')
    invoice_df = invoice_df.dropna(subset=['Date'])
    if invoice_df.empty:
        return invoice_df

    if 'Invoice_No' not in invoice_df.columns:
        invoice_df['Invoice_No'] = ''
    invoice_values = invoice_df['Invoice_No'].fillna('').astype(str).str.strip()
    missing_invoice = invoice_values == ''
    if missing_invoice.any():
        sequence = pd.Series(range(1, len(invoice_df) + 1), index=invoice_df.index).astype(str).str.zfill(5)
        invoice_df.loc[missing_invoice, 'Invoice_No'] = 'LINE-' + invoice_df.loc[missing_invoice, 'Date'].dt.strftime('%Y%m%d') + '-' + sequence.loc[missing_invoice]
    invoice_df['Invoice_No'] = invoice_df['Invoice_No'].astype(str).str.strip()
    return invoice_df

def build_invoice_analytics(df):
    invoice_df = build_invoice_line_frame(df)
    if invoice_df.empty:
        return {
            'invoice_count': 0,
            'avg_invoice_value': 0,
            'items_per_invoice': 0,
            'highest_invoice': None,
            'top_invoice_products': [],
            'invoice_bundle_candidates': [],
            'invoice_bundle_threshold': 100,
        }

    invoice_totals = invoice_df.groupby('Invoice_No', as_index=False).agg({
        'Date': 'max',
        'Total_Sale': 'sum',
        'Total_Profit': 'sum',
        'Units_Sold': 'sum',
        'Product_Name': pd.Series.nunique,
    }).rename(columns={'Product_Name': 'Unique_Items'})
    invoice_totals = invoice_totals.sort_values('Total_Sale', ascending=False)
    highest_invoice = invoice_totals.iloc[0].to_dict() if not invoice_totals.empty else None
    invoice_sales_total = float(invoice_totals['Total_Sale'].sum()) if not invoice_totals.empty else 0.0
    top_5_invoices = invoice_totals.head(5)
    high_value_floor = float(invoice_totals['Total_Sale'].quantile(0.90)) if len(invoice_totals) >= 5 else 0.0

    top_invoice_products = []
    if highest_invoice:
        top_invoice_products = (
            invoice_df[invoice_df['Invoice_No'] == highest_invoice['Invoice_No']]
            .groupby('Product_Name', as_index=False)
            .agg({'Units_Sold': 'sum', 'Total_Sale': 'sum'})
            .sort_values('Total_Sale', ascending=False)
            .head(5)
            .to_dict('records')
        )

    recent_cutoff = invoice_df['Date'].max() - pd.Timedelta(days=6)
    recent_df = invoice_df[invoice_df['Date'] >= recent_cutoff]
    bundle_candidates = calculate_invoice_bundle_candidates(recent_df, min_pair_count=100)

    return {
        'invoice_count': int(len(invoice_totals)),
        'avg_invoice_value': float(invoice_totals['Total_Sale'].mean()) if not invoice_totals.empty else 0,
        'items_per_invoice': float(invoice_totals['Unique_Items'].mean()) if not invoice_totals.empty else 0,
        'highest_invoice': highest_invoice,
        'top_invoice_products': top_invoice_products,
        'invoice_bundle_candidates': bundle_candidates,
        'invoice_bundle_threshold': 100,
        'top_invoice_share_pct': (
            (float(highest_invoice['Total_Sale']) / invoice_sales_total) * 100
            if highest_invoice and invoice_sales_total else 0
        ),
        'top_5_invoice_share_pct': (
            (float(top_5_invoices['Total_Sale'].sum()) / invoice_sales_total) * 100
            if invoice_sales_total else 0
        ),
        'top_5_invoice_sales': float(top_5_invoices['Total_Sale'].sum()) if not top_5_invoices.empty else 0,
        'high_value_invoice_count': int((invoice_totals['Total_Sale'] >= high_value_floor).sum()) if high_value_floor else 0,
        'high_value_invoice_floor': high_value_floor,
    }

def calculate_invoice_bundle_candidates(df, min_pair_count=100, limit=12):
    invoice_df = build_invoice_line_frame(df)
    if invoice_df.empty or invoice_df['Invoice_No'].nunique() == 0:
        return []

    invoice_products = (
        invoice_df.groupby('Invoice_No')['Product_Name']
        .apply(lambda values: sorted(set(str(value).strip() for value in values if str(value).strip())))
        .reset_index(name='products')
    )
    invoice_products = invoice_products[invoice_products['products'].apply(len) >= 2]
    total_invoices = len(invoice_products)
    if total_invoices == 0:
        return []

    product_invoice_counts = {}
    pair_counts = {}
    pair_sales = {}
    for _, row in invoice_products.iterrows():
        products = row['products']
        invoice_no = row['Invoice_No']
        invoice_sale = float(invoice_df.loc[invoice_df['Invoice_No'] == invoice_no, 'Total_Sale'].sum())
        for product in products:
            product_invoice_counts[product] = product_invoice_counts.get(product, 0) + 1
        for product_a, product_b in combinations(products, 2):
            pair_key = (product_a, product_b)
            pair_counts[pair_key] = pair_counts.get(pair_key, 0) + 1
            pair_sales[pair_key] = pair_sales.get(pair_key, 0.0) + invoice_sale

    candidates = []
    for (product_a, product_b), pair_count in pair_counts.items():
        if pair_count < min_pair_count:
            continue
        a_count = max(product_invoice_counts.get(product_a, 0), 1)
        b_count = max(product_invoice_counts.get(product_b, 0), 1)
        confidence_ab = pair_count / a_count
        confidence_ba = pair_count / b_count
        anchor_product = product_a if confidence_ab >= confidence_ba else product_b
        add_on_product = product_b if confidence_ab >= confidence_ba else product_a
        support = pair_count / total_invoices
        lift = confidence_ab / (b_count / total_invoices) if b_count else 0
        recommendation = (
            f"Bundle {anchor_product} with {add_on_product}; they appeared together in "
            f"{pair_count} invoices during the last 7 days."
        )
        candidates.append({
            'product_a': product_a,
            'product_b': product_b,
            'pair_count': int(pair_count),
            'support': support,
            'confidence_ab': max(confidence_ab, confidence_ba),
            'lift_ab': lift,
            'anchor_product': anchor_product,
            'add_on_product': add_on_product,
            'invoice_sales': round(pair_sales.get((product_a, product_b), 0.0), 2),
            'recommendation': recommendation,
        })

    candidates.sort(key=lambda item: (item['pair_count'], item['lift_ab'], item['invoice_sales']), reverse=True)
    return candidates[:limit]

def build_forecast_owner_plan(context, ai_decision):
    forecast_sales = _safe_float(context.get('forecast_sales_total'))
    forecast_profit = _safe_float(context.get('forecast_profit_total'))
    daily_sales = _safe_float(context.get('daily_avg_sales'))
    daily_profit = _safe_float(context.get('daily_avg_profit'))
    sales_delta_pct = ((forecast_sales / (daily_sales * 7)) - 1) * 100 if daily_sales > 0 else 0
    profit_delta_pct = ((forecast_profit / (daily_profit * 7)) - 1) * 100 if daily_profit > 0 else 0
    product_actions = ai_decision.get('product_actions', []) if ai_decision else []
    first_stock_action = next(
        (item for item in product_actions if item.get('action_type') in {'Restock now', 'Prevent stockout', 'Reorder soon'}),
        product_actions[0] if product_actions else None,
    )
    first_margin_action = next(
        (item for item in product_actions if item.get('action_type') in {'Bundle', 'Optimize margin', 'Protect price'}),
        None,
    )
    return {
        'sales_delta_pct': sales_delta_pct,
        'profit_delta_pct': profit_delta_pct,
        'stock_action': first_stock_action,
        'margin_action': first_margin_action,
    }

def build_pricing_owner_summary(pricing_data):
    action_labels = {
        'price_increase': 'Raise Price',
        'bundle_margin': 'Bundle',
        'reduce_or_replace': 'Reduce or Replace',
        'visibility_push': 'Increase Visibility',
        'protect_price': 'Protect Price',
        'hold_price': 'Hold',
    }
    action_mix = []
    for action_key, label in action_labels.items():
        matching = [item for item in pricing_data if item.get('pricing_opportunity') == action_key]
        if matching:
            action_mix.append({
                'label': label,
                'count': len(matching),
                'sales': sum(float(item.get('Total_Sale', 0) or 0) for item in matching),
            })

    top_actions = [
        item for item in pricing_data
        if item.get('pricing_opportunity') != 'hold_price'
    ][:4]
    low_margin_sales = sum(
        float(item.get('Total_Sale', 0) or 0)
        for item in pricing_data
        if float(item.get('profit_margin_pct', 0) or 0) < 20
    )
    return {
        'action_mix': action_mix,
        'top_actions': top_actions,
        'low_margin_sales': low_margin_sales,
    }

def build_cross_sell_owner_summary(cross_sell_data):
    top_candidates = cross_sell_data[:3]
    return {
        'top_bundle': cross_sell_data[0] if cross_sell_data else None,
        'top_candidates': top_candidates,
        'shared_invoice_total': sum(int(item.get('pair_count', 0) or 0) for item in top_candidates),
        'high_confidence_count': sum(
            1 for item in cross_sell_data
            if float(item.get('confidence_ab', 0) or 0) >= 0.50
        ),
    }

def category_options(df):
    if df is None or df.empty or 'Category' not in df.columns:
        return []
    return sorted(category for category in df['Category'].dropna().astype(str).unique() if category)

def apply_category_filter(df, selected_category):
    categories = category_options(df)
    if selected_category and selected_category in categories:
        return df[df['Category'] == selected_category], selected_category, categories
    return df, '', categories

def chart_json(fig):
    return fig.to_json(remove_uids=True)

CHART_COLORS = ['#2563eb', '#059669', '#d97706', '#7c3aed', '#e11d48', '#0891b2']
CHART_SOFT_COLORS = ['rgba(37, 99, 235, 0.14)', 'rgba(5, 150, 105, 0.14)', 'rgba(217, 119, 6, 0.16)', 'rgba(124, 58, 237, 0.14)']

def apply_chart_theme(fig):
    fig.update_layout(title_text='')
    fig.update_layout(
        template='plotly_white',
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(family='Inter, Segoe UI, Arial, sans-serif', color='#111827', size=12),
        title=dict(font=dict(size=17, color='#111827'), x=0.02, xanchor='left', y=0.96),
        colorway=CHART_COLORS,
        margin=dict(l=56, r=28, t=82, b=48),
        legend=dict(orientation='h', yanchor='bottom', y=1.08, xanchor='right', x=1),
        hovermode='x unified',
        hoverlabel=dict(bgcolor='#ffffff', bordercolor='#dbe4f0', font=dict(color='#111827', size=12)),
        bargap=0.28,
    )
    fig.update_xaxes(showgrid=False, linecolor='#dbe4f0', tickfont=dict(color='#697586'), title_font=dict(color='#697586'))
    fig.update_yaxes(gridcolor='#eef2f7', zerolinecolor='#e5e7eb', tickfont=dict(color='#697586'), title_font=dict(color='#697586'))
    return fig

def money_hover(label):
    return f"<b>{label}</b><br>%{{x|%b %d, %Y}}<br>$%{{y:,.2f}}<extra></extra>"

def horizontal_bar_hover(value_label):
    return "<b>%{y}</b><br>" + value_label + ": %{x:,.0f}<extra></extra>"

def daily_totals(df, start_date=None, end_date=None):
    invoice_df = build_invoice_line_frame(df)
    daily = invoice_df.groupby('Date', as_index=False).agg({
        'Total_Sale': 'sum',
        'Total_Profit': 'sum',
        'Units_Sold': 'sum',
        'Invoice_No': pd.Series.nunique,
    })
    daily = daily.rename(columns={'Invoice_No': 'Invoice_Count'})
    if daily.empty and (start_date is None or end_date is None):
        return daily

    if start_date is not None and end_date is not None:
        full_dates = pd.date_range(pd.to_datetime(start_date), pd.to_datetime(end_date), freq='D')
    else:
        full_dates = pd.date_range(daily['Date'].min(), daily['Date'].max(), freq='D')
    daily = daily.set_index('Date').reindex(full_dates, fill_value=0).rename_axis('Date').reset_index()
    return daily

def forecast_next_7_days(daily, value_column):
    if daily.empty:
        return pd.DataFrame(columns=['Date', 'Forecast'])

    history = daily[['Date', value_column]].copy()
    history[value_column] = history[value_column].clip(lower=0)
    selling_history = history[history[value_column] > 0].copy()
    if selling_history.empty:
        last_date = history['Date'].max()
        return pd.DataFrame([
            {'Date': last_date + pd.Timedelta(days=step), 'Forecast': 0.0}
            for step in range(1, 8)
        ])

    raw_values = selling_history[value_column].astype(float)
    if len(raw_values) >= 6:
        lower_bound = raw_values.quantile(0.05)
        upper_bound = raw_values.quantile(0.95)
        selling_history['forecast_value'] = raw_values.clip(lower=lower_bound, upper=upper_bound)
    else:
        selling_history['forecast_value'] = raw_values

    values = selling_history['forecast_value'].astype(float).to_numpy()
    forecast_rows = []

    recent_window = min(14, len(values))
    recent_values = pd.Series(values).tail(recent_window)
    weighted_recent = float(recent_values.ewm(span=min(7, recent_window), adjust=False).mean().iloc[-1])
    median_recent = float(recent_values.median())
    baseline = (0.65 * weighted_recent) + (0.35 * median_recent)

    trend_slope = 0.0
    if len(values) >= 6:
        trend_window = min(24, len(values))
        x = np.arange(trend_window)
        y = values[-trend_window:]
        trend_slope = float(np.polyfit(x, y, 1)[0])
        max_daily_drift = baseline * 0.08
        trend_slope = float(np.clip(trend_slope, -max_daily_drift, max_daily_drift))

    previous_7 = values[-14:-7] if len(values) >= 14 else values[:-7]
    recent_7 = values[-7:] if len(values) >= 7 else values
    momentum = 1.0
    if len(previous_7) > 0 and previous_7.mean() > 0:
        momentum = float(np.clip(recent_7.mean() / previous_7.mean(), 0.85, 1.15))

    overall_average = float(np.mean(values)) if len(values) else 0.0
    selling_history['weekday'] = selling_history['Date'].dt.dayofweek
    weekday_average = selling_history.groupby('weekday')['forecast_value'].mean().to_dict()
    last_date = history['Date'].max()

    for step in range(1, 8):
        forecast_date = last_date + pd.Timedelta(days=step)
        seasonal = float(weekday_average.get(forecast_date.dayofweek, baseline or overall_average))
        seasonal = (0.70 * seasonal) + (0.30 * baseline)
        trend_projection = max(0.0, baseline + (trend_slope * step))
        predicted = (0.42 * seasonal) + (0.38 * baseline) + (0.20 * trend_projection)
        predicted *= 1 + ((momentum - 1) * (step / 7))
        forecast_rows.append({'Date': forecast_date, 'Forecast': round(max(0.0, predicted), 2)})

    return pd.DataFrame(forecast_rows)

def build_forecast_context(daily):
    sales_forecast = forecast_next_7_days(daily, 'Total_Sale')
    profit_forecast = forecast_next_7_days(daily, 'Total_Profit')
    return {
        'sales_forecast_data': sales_forecast.to_dict('records'),
        'profit_forecast_data': profit_forecast.to_dict('records'),
        'next_day_sales_forecast': sales_forecast['Forecast'].iloc[0] if not sales_forecast.empty else 0,
        'next_day_profit_forecast': profit_forecast['Forecast'].iloc[0] if not profit_forecast.empty else 0,
        'forecast_sales_total': sales_forecast['Forecast'].sum() if not sales_forecast.empty else 0,
        'forecast_profit_total': profit_forecast['Forecast'].sum() if not profit_forecast.empty else 0,
    }

def build_charts(df):
    daily = daily_totals(df)
    chart_daily = daily.tail(120)
    product_performance = build_product_performance(df)
    top_products = pd.DataFrame(product_performance['top_products_list'])
    worst_products = pd.DataFrame(product_performance['worst_products_list'])
    if top_products.empty:
        top_products = pd.DataFrame(columns=['Product_Name', 'Units_Sold'])
    if worst_products.empty:
        worst_products = pd.DataFrame(columns=['Product_Name', 'Units_Sold'])
    category = df.groupby('Category', as_index=False).agg({'Total_Sale': 'sum', 'Total_Profit': 'sum', 'Units_Sold': 'sum'})

    top_products = top_products.sort_values('Units_Sold', ascending=True).tail(10)
    worst_products = worst_products.sort_values('Units_Sold', ascending=False).head(10).sort_values('Units_Sold', ascending=True)
    category = category.sort_values('Total_Sale', ascending=False)

    trend = go.Figure()
    trend.add_trace(go.Scatter(
        x=chart_daily['Date'],
        y=chart_daily['Total_Sale'],
        mode='lines',
        name='Sales',
        line=dict(color=CHART_COLORS[0], width=3, shape='spline', smoothing=0.55),
        fill='tozeroy',
        fillcolor=CHART_SOFT_COLORS[0],
        hovertemplate=money_hover('Sales'),
    ))
    trend.add_trace(go.Scatter(
        x=chart_daily['Date'],
        y=chart_daily['Total_Profit'],
        mode='lines',
        name='Profit',
        line=dict(color=CHART_COLORS[1], width=3, shape='spline', smoothing=0.55),
        fill='tozeroy',
        fillcolor=CHART_SOFT_COLORS[1],
        hovertemplate=money_hover('Profit'),
    ))
    trend.update_layout(title='Sales and Profit Trend', xaxis_title='', yaxis_title='Amount ($)')

    top = go.Figure(go.Bar(
        x=top_products['Units_Sold'],
        y=top_products['Product_Name'],
        orientation='h',
        marker=dict(color=top_products['Units_Sold'], colorscale=[[0, '#bfdbfe'], [1, CHART_COLORS[0]]], line=dict(color='rgba(255,255,255,0.65)', width=1)),
        text=top_products['Units_Sold'].round(0),
        textposition='outside',
        cliponaxis=False,
        hovertemplate=horizontal_bar_hover('Units sold'),
    ))
    top.update_layout(title='Top Products by Units Sold', xaxis_title='Units', yaxis_title='')

    category_fig = go.Figure()
    category_fig.add_trace(go.Bar(
        x=category['Category'],
        y=category['Total_Sale'],
        name='Sales',
        marker=dict(color=CHART_COLORS[0], line=dict(color='rgba(255,255,255,0.65)', width=1)),
        hovertemplate='<b>%{x}</b><br>Sales: $%{y:,.2f}<extra></extra>',
    ))
    category_fig.add_trace(go.Bar(
        x=category['Category'],
        y=category['Total_Profit'],
        name='Profit',
        marker=dict(color=CHART_COLORS[1], line=dict(color='rgba(255,255,255,0.65)', width=1)),
        hovertemplate='<b>%{x}</b><br>Profit: $%{y:,.2f}<extra></extra>',
    ))
    category_fig.update_layout(title='Category Performance', barmode='group', xaxis_title='', yaxis_title='Amount ($)', hovermode='closest')

    worst = go.Figure(go.Bar(
        x=worst_products['Units_Sold'],
        y=worst_products['Product_Name'],
        orientation='h',
        marker=dict(color=worst_products['Units_Sold'], colorscale=[[0, '#fecdd3'], [1, CHART_COLORS[4]]], line=dict(color='rgba(255,255,255,0.65)', width=1)),
        text=worst_products['Units_Sold'].round(0),
        textposition='outside',
        cliponaxis=False,
        hovertemplate=horizontal_bar_hover('Units sold'),
    ))
    worst.update_layout(title='Lowest-Selling Products', xaxis_title='Units', yaxis_title='')

    forecast_context = build_forecast_context(daily)
    sales_forecast = pd.DataFrame(forecast_context['sales_forecast_data'])
    profit_forecast = pd.DataFrame(forecast_context['profit_forecast_data'])

    forecast_fig = go.Figure()
    forecast_fig.add_trace(go.Scatter(
        x=chart_daily['Date'],
        y=chart_daily['Total_Sale'],
        mode='lines',
        name='Historical Sales',
        line=dict(color=CHART_COLORS[0], width=3, shape='spline', smoothing=0.45),
        hovertemplate=money_hover('Historical Sales'),
    ))
    forecast_fig.add_trace(go.Scatter(
        x=chart_daily['Date'],
        y=chart_daily['Total_Profit'],
        mode='lines',
        name='Historical Profit',
        line=dict(color=CHART_COLORS[1], width=3, shape='spline', smoothing=0.45),
        hovertemplate=money_hover('Historical Profit'),
    ))
    if not sales_forecast.empty:
        forecast_fig.add_trace(go.Scatter(
            x=sales_forecast['Date'],
            y=sales_forecast['Forecast'],
            mode='lines+markers',
            name='Predicted Sales',
            line=dict(color=CHART_COLORS[2], dash='dash', width=3, shape='spline', smoothing=0.45),
            marker=dict(size=8, line=dict(color='#ffffff', width=1.5)),
            hovertemplate=money_hover('Predicted Sales'),
        ))
    if not profit_forecast.empty:
        forecast_fig.add_trace(go.Scatter(
            x=profit_forecast['Date'],
            y=profit_forecast['Forecast'],
            mode='lines+markers',
            name='Predicted Profit',
            line=dict(color=CHART_COLORS[3], dash='dot', width=3, shape='spline', smoothing=0.45),
            marker=dict(size=8, line=dict(color='#ffffff', width=1.5)),
            hovertemplate=money_hover('Predicted Profit'),
        ))
    forecast_fig.update_layout(
        title_text='',
        xaxis_title='',
        yaxis_title='Amount ($)',
        margin=dict(l=56, r=28, t=78, b=48),
        legend=dict(orientation='h', yanchor='bottom', y=1.08, xanchor='center', x=0.5),
    )

    daily_performance = go.Figure(go.Bar(
        x=chart_daily['Date'],
        y=chart_daily['Invoice_Count'],
        marker=dict(color=chart_daily['Invoice_Count'], colorscale=[[0, '#fed7aa'], [1, CHART_COLORS[2]]], line=dict(color='rgba(255,255,255,0.55)', width=1)),
        hovertemplate='<b>%{x|%b %d, %Y}</b><br>Invoices: %{y:,.0f}<extra></extra>',
    ))
    daily_performance.update_layout(title='Daily Invoice Volume', xaxis_title='', yaxis_title='Invoices', hovermode='closest')

    product_stats = df.groupby('Product_Name', as_index=False).agg({'Units_Sold': 'sum', 'Total_Profit': 'sum', 'Total_Sale': 'sum'})
    product_scatter = go.Figure(go.Scatter(
        x=product_stats['Units_Sold'],
        y=product_stats['Total_Profit'],
        mode='markers',
        text=product_stats['Product_Name'],
        marker=dict(
            size=np.clip(product_stats['Total_Sale'] / max(product_stats['Total_Sale'].max(), 1) * 34, 9, 34),
            color=product_stats['Total_Profit'],
            colorscale=[[0, '#c4b5fd'], [1, CHART_COLORS[3]]],
            showscale=False,
            line=dict(color='rgba(255,255,255,0.9)', width=1.5),
            opacity=0.88,
        ),
        hovertemplate='<b>%{text}</b><br>Units: %{x:,.0f}<br>Profit: $%{y:,.2f}<extra></extra>',
    ))
    product_scatter.update_layout(title='Product Sales vs Profit', xaxis_title='Units sold', yaxis_title='Profit ($)', hovermode='closest')

    for fig in (trend, top, worst, category_fig, forecast_fig, daily_performance, product_scatter):
        apply_chart_theme(fig)

    return {
        'trend_plot': chart_json(trend),
        'top_products_plot': chart_json(top),
        'worst_products_plot': chart_json(worst),
        'category_plot': chart_json(category_fig),
        'forecast_plot': chart_json(forecast_fig),
        'daily_performance_plot': chart_json(daily_performance),
        'product_scatter_plot': chart_json(product_scatter),
        **forecast_context,
    }

def build_metrics(df, period_start=None, period_end=None):
    total_sales = df['Total_Sale'].sum()
    total_profit = df['Total_Profit'].sum()
    total_units = df['Units_Sold'].sum()
    invoice_analytics = build_invoice_analytics(df)
    invoice_df = build_invoice_line_frame(df)
    product_totals = invoice_df.groupby('Product_Name', as_index=False).agg({
        'Total_Sale': 'sum',
        'Total_Profit': 'sum',
        'Units_Sold': 'sum',
        'Invoice_No': pd.Series.nunique,
    })
    product_totals = product_totals.rename(columns={'Invoice_No': 'Invoice_Count'})
    daily = daily_totals(df, period_start, period_end)
    category = invoice_df.groupby('Category', as_index=False).agg({
        'Total_Sale': 'sum',
        'Total_Profit': 'sum',
        'Units_Sold': 'sum',
        'Invoice_No': pd.Series.nunique,
    }).rename(columns={'Invoice_No': 'Invoice_Count'})
    period_day_count = len(daily) if not daily.empty else 0
    selling_day_count = int((daily['Total_Sale'] > 0).sum()) if not daily.empty else 0

    def latest_vs_previous_selling_day(column):
        if len(daily) < 2:
            return 0

        latest_value = float(daily[column].iloc[-1])
        previous_nonzero = daily[column].iloc[:-1][daily[column].iloc[:-1] > 0]
        if previous_nonzero.empty:
            return 100 if latest_value > 0 else 0

        previous_value = float(previous_nonzero.iloc[-1])
        return ((latest_value - previous_value) / previous_value) * 100

    sales_growth = latest_vs_previous_selling_day('Total_Sale')
    profit_growth = latest_vs_previous_selling_day('Total_Profit')

    top_by_units = product_totals.nlargest(1, 'Units_Sold')
    return {
        'total_sales': total_sales,
        'total_profit': total_profit,
        'total_units': total_units,
        'profit_margin': (total_profit / total_sales * 100) if total_sales else 0,
        'avg_transaction_value': invoice_analytics['avg_invoice_value'] if invoice_analytics['invoice_count'] else (total_sales / len(df) if len(df) else 0),
        'avg_line_value': total_sales / len(df) if len(df) else 0,
        **invoice_analytics,
        'daily_avg_sales': (total_sales / period_day_count) if period_day_count else 0,
        'daily_avg_profit': (total_profit / period_day_count) if period_day_count else 0,
        'daily_avg_units': (total_units / period_day_count) if period_day_count else 0,
        'selling_day_avg_sales': (total_sales / selling_day_count) if selling_day_count else 0,
        'selling_day_avg_profit': (total_profit / selling_day_count) if selling_day_count else 0,
        'selling_day_avg_units': (total_units / selling_day_count) if selling_day_count else 0,
        'period_day_count': period_day_count,
        'selling_day_count': selling_day_count,
        'sales_growth': sales_growth,
        'profit_growth': profit_growth,
        'top_product': top_by_units.iloc[0]['Product_Name'] if not top_by_units.empty else 'N/A',
        'top_product_units': int(top_by_units.iloc[0]['Units_Sold']) if not top_by_units.empty else 0,
        'top_product_revenue': product_totals.nlargest(1, 'Total_Sale').to_dict('records')[0] if not product_totals.empty else None,
        'top_product_profit': product_totals.nlargest(1, 'Total_Profit').to_dict('records')[0] if not product_totals.empty else None,
        'category_performance': category.to_dict('records'),
    }

def build_period_movement(source_df, start_date, end_date, selected_category=''):
    if source_df is None or source_df.empty:
        return {
            'sales_growth': 0,
            'profit_growth': 0,
            'movement_reference_label': 'No comparison data',
        }

    start_date = pd.to_datetime(start_date).normalize()
    end_date = pd.to_datetime(end_date).normalize()
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    period_days = (end_date - start_date).days + 1
    previous_end = start_date - pd.Timedelta(days=1)
    previous_start = previous_end - pd.Timedelta(days=period_days - 1)

    comparison_df = source_df.copy()
    if selected_category:
        comparison_df = comparison_df[comparison_df['Category'] == selected_category]

    current_df = comparison_df[(comparison_df['Date'] >= start_date) & (comparison_df['Date'] <= end_date)]
    previous_df = comparison_df[(comparison_df['Date'] >= previous_start) & (comparison_df['Date'] <= previous_end)]

    def percent_change(current_value, previous_value):
        current_value = float(current_value or 0)
        previous_value = float(previous_value or 0)
        if previous_value == 0:
            return 0
        return ((current_value - previous_value) / previous_value) * 100

    previous_has_data = not previous_df.empty
    return {
        'sales_growth': percent_change(current_df['Total_Sale'].sum(), previous_df['Total_Sale'].sum()),
        'profit_growth': percent_change(current_df['Total_Profit'].sum(), previous_df['Total_Profit'].sum()),
        'movement_reference_label': (
            f"Selected period vs {previous_start.strftime('%Y-%m-%d')} to {previous_end.strftime('%Y-%m-%d')}"
            if previous_has_data else
            'No previous equivalent period in the data'
        ),
    }

def data_context():
    df = load_data()
    if df is None:
        return None, None
    filtered_df, range_context = apply_date_filter(df)
    inventory_items = load_inventory_items()
    return filtered_df, {
        **range_context,
        **build_metrics(filtered_df, range_context['start_date'], range_context['end_date']),
        **build_charts(filtered_df),
        **build_product_performance(filtered_df),
        **inventory_metrics(inventory_items),
    }

# ------------------- Authentication Routes -------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = find_user_by_email(form.email.data)
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember.data)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('index'))
        else:
            flash('Login failed. Please check your email and password', 'danger')
    return render_template('login.html', form=form)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = SignupForm()
    if form.validate_on_submit():
        hashed_password = generate_password_hash(form.password.data)
        user = create_user(form.email.data, password_hash=hashed_password)
        login_user(user)
        flash('Congratulations, you are now a registered user!', 'success')
        return redirect(url_for('index'))
    return render_template('signup.html', form=form)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

# ------------------- OAuth Callback Routes -------------------
@app.route('/google_login/authorized')
def google_logged_in():
    if not google.authorized:
        return redirect(url_for('google.login'))
    resp = google.get('/oauth2/v2/userinfo')
    if not resp.ok:
        flash('Failed to fetch user info from Google.', 'danger')
        return redirect(url_for('login'))
    
    google_info = resp.json()
    user_email = google_info.get('email')
    if not user_email:
        flash('Google account does not have an email associated.', 'danger')
        return redirect(url_for('login'))
    
    user = find_user_by_email(user_email)
    
    if not user:
        user = create_user(user_email, provider='google', provider_user_id=google_info.get('id'))
    
    login_user(user)
    flash('Successfully signed in with Google!', 'success')
    return redirect(url_for('index'))

@app.route('/facebook_login/authorized')
def facebook_logged_in():
    if not facebook.authorized:
        return redirect(url_for('facebook.login'))
    resp = facebook.get('/me?fields=id,name,email')
    if not resp.ok:
        flash('Failed to fetch user info from Facebook.', 'danger')
        return redirect(url_for('login'))
    
    facebook_info = resp.json()
    user_email = facebook_info.get('email')
    if not user_email:
        flash('Facebook account does not have an email associated.', 'danger')
        return redirect(url_for('login'))
    
    user = find_user_by_email(user_email)
    
    if not user:
        user = create_user(user_email, provider='facebook', provider_user_id=facebook_info.get('id'))
    
    login_user(user)
    flash('Successfully signed in with Facebook!', 'success')
    return redirect(url_for('index'))

# ------------------- Main Application Routes -------------------
@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    df, context = data_context()
    if df is None:
        return "No data found. Please add CSV files to the data folder.", 404
    return render_template('index.html', **context)

@app.route('/export.csv')
@login_required
def export_csv():
    df = load_data()
    if df is None:
        return "No data found. Please add CSV files to the data folder.", 404
    filtered_df, _ = apply_date_filter(df)
    return Response(
        filtered_df.to_csv(index=False),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=supermarket_dashboard_export.csv'}
    )

@app.route('/integration', methods=['GET', 'POST'])
@login_required
def integration():
    if request.method == 'POST':
        action = request.form.get('action', 'import')
        if action == 'reset_sample':
            set_integration_setting('active_sales_csv_path', app.config['SALES_CSV_PATH'])
            set_integration_setting('integration_mode', 'sample_csv')
            log_integration_import('Sample data', app.config['SALES_CSV_PATH'], 0, 0, 'success', 'Dashboard source reset to sample supermarket CSV.')
            AI_DECISION_CACHE.clear()
            flash('Dashboard data source reset to the sample supermarket CSV.', 'success')
            return redirect(url_for('integration'))

        if action == 'import_sqlite':
            source_name = request.form.get('sqlite_source_name', 'HDPOS SQLite').strip() or 'HDPOS SQLite'
            table_name = request.form.get('sqlite_table_name', '').strip()
            _, message = save_normalized_sqlite_import(request.files.get('hdpos_sqlite_file'), source_name, table_name)
            flash(message, 'success' if message.startswith('Imported') else 'danger')
            return redirect(url_for('integration'))

        if action in {'save_sql_server', 'test_sql_server', 'sync_sql_server'}:
            save_sql_server_settings_from_form(request.form)
            settings = sql_server_settings()
            if action == 'save_sql_server':
                if settings['sync_enabled']:
                    start_sql_server_scheduler()
                flash('SQL Server POS settings saved.', 'success')
                return redirect(url_for('integration'))

            if action == 'test_sql_server':
                ok, message = test_sql_server_connection(settings)
                flash(message, 'success' if ok else 'danger')
                return redirect(url_for('integration'))

            ok, message = sync_sql_server_sales('SQL Server POS Manual Sync')
            if settings['sync_enabled']:
                start_sql_server_scheduler()
            flash(message, 'success' if ok else 'danger')
            return redirect(url_for('integration'))

        source_name = request.form.get('source_name', 'HDPOS CSV').strip() or 'HDPOS CSV'
        _, message = save_normalized_import(request.files.get('hdpos_file'), source_name)
        flash(message, 'success' if message.startswith('Imported') else 'danger')
        return redirect(url_for('integration'))

    return render_template(
        'integration.html',
        summary=integration_summary(),
        sql_server=sql_server_settings(),
        logs=load_integration_logs(),
        required_fields=[
            'Date / invoice date',
            'Product name',
            'Quantity / units sold',
            'Unit price or total sale',
        ],
        recommended_fields=[
            'SKU or item code',
            'Barcode',
            'Invoice number',
            'Store / branch',
            'Category / department',
            'Cost price or profit',
            'Tax/GST/VAT',
            'Discount',
            'Payment mode',
            'Customer ID / loyalty ID',
            'Cashier / employee',
            'Batch / lot',
            'Expiry date',
            'Current stock',
            'Reorder level',
            'Supplier/vendor',
        ],
    )

@app.route('/inventory/add', methods=['POST'])
@login_required
def add_inventory_item():
    product_name = request.form.get('product_name', '').strip()
    category = request.form.get('category', '').strip()
    product_id = request.form.get('product_id', '').strip()
    barcode = request.form.get('barcode', '').strip() or None
    supplier = request.form.get('supplier', '').strip() or None
    expiry_date_raw = request.form.get('expiry_date', '').strip()
    is_perishable = 1 if request.form.get('is_perishable') == 'on' else 0
    try:
        unit_price = float(request.form.get('unit_price', 0))
        cost_price = float(request.form.get('cost_price', 0))
        current_stock = int(request.form.get('current_stock', 0))
        reorder_level = int(request.form.get('reorder_level', 10))
    except ValueError:
        flash('Please enter valid numeric values for price, cost, stock, and reorder level.', 'danger')
        return redirect(url_for('inventory'))

    if not product_name or not category or unit_price <= 0 or cost_price < 0:
        flash('Product name, category, unit price, and cost price are required.', 'danger')
        return redirect(url_for('inventory'))

    expiry_date = None
    if expiry_date_raw:
        try:
            expiry_date = pd.to_datetime(expiry_date_raw).strftime('%Y-%m-%d')
            is_perishable = 1
        except Exception:
            flash('Expiry date must be a valid date, or leave it blank for products without expiry tracking.', 'danger')
            return redirect(url_for('inventory'))

    if not product_id:
        product_id = f"P{secrets.token_hex(4).upper()}"

    profit_per_unit = max(0, unit_price - cost_price)
    try:
        with get_business_db() as connection:
            connection.execute(
                """
                INSERT INTO inventory_items
                    (product_id, product_name, category, unit_price, profit_per_unit, cost_price, current_stock, reorder_level, barcode, supplier, expiry_date, is_perishable)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    product_name,
                    category,
                    unit_price,
                    profit_per_unit,
                    cost_price,
                    current_stock,
                    reorder_level,
                    barcode,
                    supplier,
                    expiry_date,
                    is_perishable,
                ),
            )
        flash(f'{product_name} was added to inventory.', 'success')
    except sqlite3.IntegrityError:
        flash('That SKU/Product ID already exists. Use a different SKU or leave it blank to auto-generate one.', 'danger')
    return redirect(url_for('inventory'))

@app.route('/sales/record', methods=['POST'])
@login_required
def record_sale():
    product_id = request.form.get('product_id', '').strip()
    sale_date = request.form.get('sale_date', datetime.now().strftime('%Y-%m-%d'))
    invoice_no = request.form.get('invoice_no', '').strip()
    try:
        units_sold = int(request.form.get('units_sold', 0))
    except ValueError:
        flash('Units sold must be a number.', 'danger')
        return redirect(url_for('inventory'))

    if not product_id or units_sold <= 0:
        flash('Choose a product and enter units sold.', 'danger')
        return redirect(url_for('inventory'))

    with get_business_db() as connection:
        item = connection.execute("SELECT * FROM inventory_items WHERE product_id = ?", (product_id,)).fetchone()
        if item is None:
            flash('Selected product was not found.', 'danger')
            return redirect(url_for('inventory'))

        current_stock = int(item['current_stock'])
        if current_stock <= 0:
            flash(f"{item['product_name']} is out of stock. Add inventory before recording a sale.", 'danger')
            return redirect(url_for('inventory'))

        if units_sold > current_stock:
            flash(f"Only {current_stock} unit(s) of {item['product_name']} are in stock. Reduce the sale quantity or restock first.", 'danger')
            return redirect(url_for('inventory'))

        total_sale = units_sold * float(item['unit_price'])
        total_profit = units_sold * float(item['profit_per_unit'])
        if not invoice_no:
            invoice_no = f"INV-{pd.to_datetime(sale_date).strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
        update_result = connection.execute(
            """
            UPDATE inventory_items
            SET current_stock = current_stock - ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE product_id = ?
              AND current_stock >= ?
            """,
            (units_sold, product_id, units_sold),
        )
        if update_result.rowcount != 1:
            flash('Stock changed before the sale could be recorded. Please refresh and try again.', 'danger')
            return redirect(url_for('inventory'))

        connection.execute(
            """
            INSERT INTO sales_records
                (sale_date, product_id, product_name, category, units_sold, unit_price, profit_per_unit, total_sale, total_profit, invoice_no)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pd.to_datetime(sale_date).strftime('%Y-%m-%d'),
                item['product_id'],
                item['product_name'],
                item['category'],
                units_sold,
                float(item['unit_price']),
                float(item['profit_per_unit']),
                total_sale,
                total_profit,
                invoice_no,
            ),
        )

    flash(f'Sale recorded on invoice {invoice_no} and inventory updated.', 'success')
    return redirect(url_for('inventory'))

@app.route('/assets/plotly.min.js')
def plotly_js():
    import plotly

    plotly_path = os.path.join(os.path.dirname(plotly.__file__), 'package_data', 'plotly.min.js')
    return send_file(plotly_path, mimetype='application/javascript')

# ------------------- Advanced Analytics Helper Functions -------------------
def calculate_inventory_optimization(df):
    """Calculate inventory optimization recommendations."""
    if df is None or len(df) == 0:
        return []
    
    # Calculate average daily demand per product
    daily_demand = df.groupby(['Product_Name', 'Category'])['Units_Sold'].mean().reset_index()
    daily_demand.columns = ['Product_Name', 'Category', 'avg_daily_demand']
    
    # Estimate current stock (simplified - in real scenario, this would come from inventory system)
    # For demo, we'll use a random factor of daily demand
    np.random.seed(42)  # for consistent results
    daily_demand['current_stock'] = daily_demand['avg_daily_demand'] * np.random.uniform(5, 15, len(daily_demand))
    
    # Calculate optimal stock (assuming 10 days of supply as optimal)
    daily_demand['optimal_stock'] = daily_demand['avg_daily_demand'] * 10
    
    # Determine stock status
    def get_stock_status(row):
        if row['current_stock'] < row['optimal_stock'] * 0.8:
            return 'Understock'
        elif row['current_stock'] > row['optimal_stock'] * 1.2:
            return 'Overstock'
        else:
            return 'Optimal'
    
    daily_demand['stock_status'] = daily_demand.apply(get_stock_status, axis=1)
    
    # Convert to list of dictionaries for template
    inventory_data = daily_demand.to_dict('records')
    return inventory_data

def calculate_waste_reduction(df):
    """Calculate waste recommendations without marking items expired unless an expiry date exists."""
    if df is None or len(df) == 0:
        return []

    product_stats = df.groupby(['Product_Name', 'Category']).agg({
        'Total_Sale': 'sum',
        'Units_Sold': 'sum'
    }).reset_index()

    inventory = pd.DataFrame(load_inventory_items())
    if not inventory.empty:
        if 'expiry_date' not in inventory.columns:
            inventory['expiry_date'] = None
        if 'is_perishable' not in inventory.columns:
            inventory['is_perishable'] = inventory['category'].map(infer_is_perishable)
        inventory['parsed_expiry_date'] = pd.to_datetime(inventory['expiry_date'], errors='coerce')
        inventory = inventory.groupby(['product_name', 'category'], as_index=False).agg({
            'current_stock': 'sum',
            'is_perishable': 'max',
            'parsed_expiry_date': 'min',
        })
        inventory = inventory.rename(columns={
            'product_name': 'Product_Name',
            'category': 'Category',
            'current_stock': 'current_inventory',
            'parsed_expiry_date': 'expiry_date',
        })
        product_stats = product_stats.merge(inventory, on=['Product_Name', 'Category'], how='left')
    else:
        product_stats['current_inventory'] = 0
        product_stats['is_perishable'] = 0
        product_stats['expiry_date'] = pd.NaT

    product_stats['current_inventory'] = product_stats['current_inventory'].fillna(0)
    product_stats['is_perishable'] = product_stats['is_perishable'].fillna(
        product_stats['Category'].map(infer_is_perishable)
    ).astype(int)

    date_span = max((df['Date'].max() - df['Date'].min()).days + 1, 1)
    daily_sales = product_stats['Units_Sold'] / date_span
    product_stats['days_of_inventory'] = np.where(daily_sales > 0, 
                                                 product_stats['current_inventory'] / daily_sales, 
                                                 0)

    product_stats['shelf_life'] = product_stats['Category'].map(default_shelf_life_for_category)
    today = pd.Timestamp(datetime.now().date())
    product_stats['has_expiry_date'] = product_stats['expiry_date'].notna()
    product_stats['days_to_expiry'] = np.where(
        product_stats['has_expiry_date'],
        (product_stats['expiry_date'] - today).dt.days,
        np.nan,
    )
    product_stats['freshness_cover_days'] = np.where(
        product_stats['is_perishable'] == 1,
        product_stats['shelf_life'] - product_stats['days_of_inventory'],
        np.nan,
    )
    product_stats['sales_rank'] = product_stats['Total_Sale'].rank(pct=True)

    def get_waste_risk(row):
        if float(row['current_inventory'] or 0) <= 0:
            return 'Low'

        if bool(row['has_expiry_date']):
            days_to_expiry = float(row['days_to_expiry'])
            if days_to_expiry < 0:
                return 'Expired'
            if days_to_expiry <= 1:
                return 'Very High'
            if days_to_expiry <= 3:
                return 'High'
            if days_to_expiry <= 7:
                return 'Medium'
            return 'Low'

        if int(row['is_perishable']) == 1:
            freshness_cover = float(row['freshness_cover_days'])
            if freshness_cover <= 1 and row['sales_rank'] < 0.35:
                return 'Very High'
            if freshness_cover <= 2:
                return 'High'
            if freshness_cover <= 5:
                return 'Medium'
            return 'Low'

        if row['days_of_inventory'] >= 120 and row['sales_rank'] < 0.25:
            return 'Medium'
        return 'Low'
    
    product_stats['waste_risk'] = product_stats.apply(get_waste_risk, axis=1)

    def get_days_until_expiry(row):
        if row['has_expiry_date']:
            return row['days_to_expiry']
        if int(row['is_perishable']) == 1:
            return row['freshness_cover_days']
        return np.nan

    def get_expiry_label(row):
        if row['has_expiry_date']:
            return pd.to_datetime(row['expiry_date']).strftime('%Y-%m-%d')
        if int(row['is_perishable']) == 1:
            return 'Not set'
        return 'Not applicable'

    def get_tracking_label(row):
        if int(row['is_perishable']) == 1:
            return 'Expiry tracked' if row['has_expiry_date'] else 'Freshness estimated'
        return 'Non-perishable'

    product_stats['days_until_expiry'] = product_stats.apply(get_days_until_expiry, axis=1)
    product_stats['expiry_label'] = product_stats.apply(get_expiry_label, axis=1)
    product_stats['tracking_label'] = product_stats.apply(get_tracking_label, axis=1)
    
    def get_recommended_action(row):
        days_until_expiry = row['days_until_expiry']
        days = max(0, float(days_until_expiry)) if pd.notna(days_until_expiry) else 0
        stock = int(round(row['current_inventory']))
        avg_daily_sales = row['Units_Sold'] / date_span if row['Units_Sold'] else 0
        if row['waste_risk'] == 'Expired':
            return 'Stop selling, remove from shelf, and write off today'
        elif row['waste_risk'] == 'Very High':
            if row['has_expiry_date']:
                return f'Run same-day clearance before expiry; move {stock} units today'
            return f'Verify freshness, discount 20-30%, and move {stock} units before quality drops'
        elif row['waste_risk'] == 'High':
            if row['has_expiry_date']:
                return f'Discount 15-25% and target {max(1, int(round(avg_daily_sales * 2)))} units/day before expiry'
            return 'Feature this perishable item and reduce the next reorder if stock cover stays above shelf life'
        elif row['waste_risk'] == 'Medium':
            if int(row['is_perishable']) == 1:
                return f'Bundle with a fast mover for the next {int(round(days)) if days else 3} days'
            return 'Slow-moving non-perishable: reduce reorder quantity and shelf space before discounting'
        else:
            return 'Keep price steady and reorder only after current stock drops below normal demand cover'
    
    product_stats['recommended_action'] = product_stats.apply(get_recommended_action, axis=1)
    return product_stats.to_dict('records')

def calculate_dynamic_pricing_opportunities(df):
    """Calculate dynamic pricing opportunities with price elasticity modeling."""
    if df is None or len(df) == 0:
        return []
    
    # Calculate product statistics
    product_stats = df.groupby(['Product_Name', 'Category']).agg({
        'Unit_Price': 'mean',
        'Total_Sale': 'sum',
        'Total_Profit': 'sum',
        'Units_Sold': 'sum',
        'Profit_Per_Unit': 'mean'
    }).reset_index()
    
    # Calculate profit margin percentage
    product_stats['profit_margin_pct'] = (product_stats['Profit_Per_Unit'] / product_stats['Unit_Price']) * 100
    
    # Calculate monthly units sold (approximate)
    product_stats['Monthly_Units_Sold'] = product_stats['Units_Sold']  # assuming data is for ~1 month
    
    # Calculate price elasticity proxy (inverse relationship between price and units sold)
    # For simplicity, we'll use correlation within categories
    price_elasticity = {}
    for category in df['Category'].unique():
        cat_data = df[df['Category'] == category]
        if len(cat_data) > 1 and cat_data['Unit_Price'].nunique() > 1 and cat_data['Units_Sold'].nunique() > 1:
            # Calculate correlation between price and units sold (negative = elastic)
            correlation = cat_data['Unit_Price'].corr(cat_data['Units_Sold'])
            price_elasticity[category] = correlation if not pd.isna(correlation) else 0
        else:
            price_elasticity[category] = 0
    
    # Map elasticity to each product
    product_stats['price_elasticity'] = product_stats['Category'].map(price_elasticity)
    
    high_volume_cutoff = product_stats['Units_Sold'].quantile(0.7)
    low_volume_cutoff = product_stats['Units_Sold'].quantile(0.3)
    high_price_cutoff = product_stats['Unit_Price'].quantile(0.7)

    # Determine pricing opportunity based on margin, volume, and elasticity
    def get_pricing_opportunity(row):
        # High margin + stable demand: seller can safely test a small increase.
        if row['profit_margin_pct'] >= 40 and row['price_elasticity'] >= -0.3:
            return 'price_increase'
        # Low margin and strong volume: preserve unit movement, improve basket value with bundles.
        elif row['profit_margin_pct'] < 25 and row['Units_Sold'] >= high_volume_cutoff:
            return 'bundle_margin'
        # Low volume and healthy margin: feature it instead of discounting deeply.
        elif row['Units_Sold'] <= low_volume_cutoff and row['profit_margin_pct'] >= 30:
            return 'visibility_push'
        # Premium product: protect positioning.
        elif row['profit_margin_pct'] >= 35 and row['Unit_Price'] >= high_price_cutoff:
            return 'protect_price'
        # Low margin and weak movement: reduce exposure.
        elif row['profit_margin_pct'] < 20 and row['Units_Sold'] <= low_volume_cutoff:
            return 'reduce_or_replace'
        else:
            return 'hold_price'
    
    product_stats['pricing_opportunity'] = product_stats.apply(get_pricing_opportunity, axis=1)

    opportunity_labels = {
        'price_increase': 'Increase',
        'bundle_margin': 'Bundle',
        'visibility_push': 'Promote',
        'protect_price': 'Protect',
        'reduce_or_replace': 'Review',
        'hold_price': 'Hold',
    }
    product_stats['opportunity_label'] = product_stats['pricing_opportunity'].map(opportunity_labels)
    
    # Determine suggested action
    def get_suggested_action(row):
        price = float(row['Unit_Price'])
        if row['pricing_opportunity'] == 'price_increase':
            return f'Test a 5% price increase to ${price * 1.05:.2f} for 7 days; keep it if units stay within 10% of normal'
        elif row['pricing_opportunity'] == 'bundle_margin':
            return f'Keep shelf price at ${price:.2f}; bundle with a high-margin item instead of discounting this product'
        elif row['pricing_opportunity'] == 'visibility_push':
            return 'Move to eye-level or checkout display for 7 days before cutting price'
        elif row['pricing_opportunity'] == 'protect_price':
            return f'Protect the ${price:.2f} price; use premium placement, not discounts'
        elif row['pricing_opportunity'] == 'reduce_or_replace':
            return 'Do not reorder heavily; test a replacement or reduce shelf space'
        else:
            return f'Hold price at ${price:.2f}; review again after the next sales cycle'
    
    product_stats['suggested_action'] = product_stats.apply(get_suggested_action, axis=1)
    
    # Convert to list of dictionaries for template
    pricing_data = product_stats.to_dict('records')
    return pricing_data

def build_ai_decision_cache_key(df, context):
    if df is None or df.empty:
        return 'empty'

    inventory_snapshot = [
        (
            str(item.get('product_id', '')),
            _safe_int(item.get('current_stock')),
            _safe_int(item.get('reorder_level')),
        )
        for item in load_inventory_items()
    ]
    payload = {
        'rows': int(len(df)),
        'date_min': str(df['Date'].min()),
        'date_max': str(df['Date'].max()),
        'sales': round(_safe_float(df['Total_Sale'].sum()), 2),
        'profit': round(_safe_float(df['Total_Profit'].sum()), 2),
        'units': int(_safe_float(df['Units_Sold'].sum())),
        'period': context.get('period_label', ''),
        'inventory': inventory_snapshot,
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode('utf-8')).hexdigest()

def forecast_product_units(df, product_name):
    product_daily = df[df['Product_Name'] == product_name].groupby('Date', as_index=False).agg({'Units_Sold': 'sum'})
    if product_daily.empty:
        return 0

    full_dates = pd.date_range(df['Date'].min(), df['Date'].max(), freq='D')
    product_daily = (
        product_daily.set_index('Date')
        .reindex(full_dates, fill_value=0)
        .rename_axis('Date')
        .reset_index()
    )
    forecast = forecast_next_7_days(product_daily, 'Units_Sold')
    return int(round(_safe_float(forecast['Forecast'].sum()) if not forecast.empty else 0))

def forecast_all_product_units(df):
    if df is None or df.empty:
        return {}

    max_date = df['Date'].max()
    recent_start = max_date - pd.Timedelta(days=13)
    previous_start = max_date - pd.Timedelta(days=27)
    recent = df[df['Date'] >= recent_start].groupby('Product_Name')['Units_Sold'].sum()
    previous = df[(df['Date'] >= previous_start) & (df['Date'] < recent_start)].groupby('Product_Name')['Units_Sold'].sum()
    lifetime_days = max((df['Date'].max() - df['Date'].min()).days + 1, 1)
    lifetime_daily = df.groupby('Product_Name')['Units_Sold'].sum() / lifetime_days

    product_names = df['Product_Name'].dropna().unique()
    forecasts = {}
    for product_name in product_names:
        recent_daily = _safe_float(recent.get(product_name, 0)) / 14
        previous_daily = _safe_float(previous.get(product_name, 0)) / 14
        stable_daily = _safe_float(lifetime_daily.get(product_name, 0))
        if previous_daily > 0:
            momentum = float(np.clip(recent_daily / previous_daily, 0.75, 1.25))
        else:
            momentum = 1.08 if recent_daily > 0 else 1.0
        projected_daily = ((0.62 * recent_daily) + (0.38 * stable_daily)) * momentum
        forecasts[product_name] = int(round(max(0, projected_daily * 7)))
    return forecasts

def build_seller_decision_intelligence(df, context):
    """Fast local decision engine for AI-style predictions, insights, and seller actions."""
    if df is None or df.empty:
        return {
            'source': 'Local AI decision engine',
            'rendering_note': 'No data available for recommendations.',
            'summary': [],
            'product_actions': [],
            'category_actions': [],
            'forecast_actions': [],
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'cached': False,
        }

    cache_key = build_ai_decision_cache_key(df, context)
    cached = AI_DECISION_CACHE.get(cache_key)
    cache_ttl = app.config.get('AI_DECISION_CACHE_TTL_SECONDS', 300)
    if cached and time.time() - cached['created_at'] < cache_ttl:
        result = cached['result'].copy()
        result['cached'] = True
        return result

    date_span = max((df['Date'].max() - df['Date'].min()).days + 1, 1)
    inventory = pd.DataFrame(load_inventory_items())
    if not inventory.empty:
        inventory = inventory.rename(columns={
            'product_id': 'Product_ID',
            'product_name': 'Product_Name',
            'category': 'Category',
            'current_stock': 'Current_Stock',
            'reorder_level': 'Reorder_Level',
        })
        inventory = inventory[['Product_ID', 'Product_Name', 'Category', 'Current_Stock', 'Reorder_Level']]

    product_stats = df.groupby(['Product_ID', 'Product_Name', 'Category'], as_index=False).agg({
        'Units_Sold': 'sum',
        'Total_Sale': 'sum',
        'Total_Profit': 'sum',
        'Unit_Price': 'mean',
        'Profit_Per_Unit': 'mean',
    })
    if not inventory.empty:
        product_stats = product_stats.merge(inventory, on=['Product_ID', 'Product_Name', 'Category'], how='left')
        missing_stock = product_stats['Current_Stock'].isna()
        if missing_stock.any():
            fallback_inventory = (
                inventory.groupby(['Product_Name', 'Category'], as_index=False)
                .agg({'Current_Stock': 'sum', 'Reorder_Level': 'max'})
                .rename(columns={
                    'Current_Stock': 'Fallback_Current_Stock',
                    'Reorder_Level': 'Fallback_Reorder_Level',
                })
            )
            product_stats = product_stats.merge(fallback_inventory, on=['Product_Name', 'Category'], how='left')
            product_stats['Current_Stock'] = product_stats['Current_Stock'].fillna(product_stats['Fallback_Current_Stock'])
            product_stats['Reorder_Level'] = product_stats['Reorder_Level'].fillna(product_stats['Fallback_Reorder_Level'])
            product_stats = product_stats.drop(columns=['Fallback_Current_Stock', 'Fallback_Reorder_Level'])
    else:
        product_stats['Current_Stock'] = 0
        product_stats['Reorder_Level'] = 0

    for column in ['Current_Stock', 'Reorder_Level', 'Units_Sold', 'Total_Sale', 'Total_Profit']:
        product_stats[column] = product_stats[column].fillna(0)

    product_stats['profit_margin_pct'] = np.where(
        product_stats['Total_Sale'] > 0,
        (product_stats['Total_Profit'] / product_stats['Total_Sale']) * 100,
        0,
    )
    product_stats['daily_velocity'] = product_stats['Units_Sold'] / date_span
    product_stats['days_stock_cover'] = np.where(
        product_stats['daily_velocity'] > 0,
        product_stats['Current_Stock'] / product_stats['daily_velocity'],
        np.inf,
    )

    unit_high = product_stats['Units_Sold'].quantile(0.72) if len(product_stats) else 0
    unit_low = product_stats['Units_Sold'].quantile(0.28) if len(product_stats) else 0
    margin_low = product_stats['profit_margin_pct'].quantile(0.28) if len(product_stats) else 0
    margin_high = product_stats['profit_margin_pct'].quantile(0.72) if len(product_stats) else 0

    pricing_lookup = {
        item['Product_Name']: item
        for item in calculate_dynamic_pricing_opportunities(df)
    }
    product_unit_forecasts = forecast_all_product_units(df)
    product_actions = []
    for _, row in product_stats.iterrows():
        product_name = row['Product_Name']
        forecast_units = product_unit_forecasts.get(product_name, 0)
        stock = _safe_int(row['Current_Stock'])
        reorder_level = _safe_int(row['Reorder_Level'])
        units = _safe_int(row['Units_Sold'])
        margin = _safe_float(row['profit_margin_pct'])
        daily_velocity = _safe_float(row['daily_velocity'])
        days_cover = _safe_float(row['days_stock_cover'], 9999)
        revenue = _safe_float(row['Total_Sale'])
        profit = _safe_float(row['Total_Profit'])
        unit_price = _safe_float(row['Unit_Price'])
        pricing = pricing_lookup.get(product_name, {})

        if stock <= 0 and forecast_units > 0:
            action_type = 'Restock now'
            suggested_action = f"Restock {product_name} before taking more sales; forecast demand is {forecast_units} units next week."
            reason = 'zero stock with forecast demand'
            priority = 100
            confidence = 0.94
            impact = forecast_units * unit_price
        elif forecast_units > stock and forecast_units > 0:
            shortfall = forecast_units - stock
            action_type = 'Prevent stockout'
            suggested_action = f"Order at least {shortfall} more units; current stock covers about {max(days_cover, 0):.1f} days."
            reason = 'forecast demand exceeds available stock'
            priority = 92
            confidence = 0.88
            impact = shortfall * unit_price
        elif stock <= reorder_level and daily_velocity > 0:
            action_type = 'Reorder soon'
            suggested_action = f"Reorder {product_name}; stock is at {stock} against a reorder level of {reorder_level}."
            reason = 'stock at or below reorder level'
            priority = 84
            confidence = 0.82
            impact = revenue * 0.08
        elif units >= unit_high and margin <= margin_low:
            action_type = 'Bundle'
            suggested_action = f"Keep the ${unit_price:.2f} shelf price and bundle with a higher-margin add-on to lift basket profit."
            reason = 'high demand but low margin'
            priority = 78
            confidence = 0.79
            impact = revenue * 0.10
        elif units <= unit_low and stock > max(forecast_units * 2, reorder_level * 2) and daily_velocity > 0:
            action_type = 'Reduce exposure'
            suggested_action = f"Reduce reorder quantity and use a small promotion to clear excess stock before adding more shelf space."
            reason = 'slow movement with excess cover'
            priority = 74
            confidence = 0.76
            impact = stock * unit_price * 0.05
        elif margin >= margin_high and units >= unit_high:
            action_type = 'Protect price'
            suggested_action = f"Protect the ${unit_price:.2f} price; use premium placement instead of discounting a strong-margin seller."
            reason = 'strong volume and strong margin'
            priority = 68
            confidence = 0.74
            impact = profit * 0.06
        elif pricing and pricing.get('pricing_opportunity') != 'hold_price':
            action_type = pricing.get('opportunity_label', 'Pricing action')
            suggested_action = pricing.get('suggested_action', 'Review pricing action.')
            reason = 'pricing model detected an opportunity'
            priority = 62
            confidence = 0.70
            impact = revenue * 0.05
        else:
            action_type = 'Hold'
            suggested_action = f"Hold current plan for {product_name}; no urgent demand, stock, or margin issue detected."
            reason = 'balanced stock and sales behavior'
            priority = 30
            confidence = 0.66
            impact = 0

        product_actions.append({
            'Product_ID': row['Product_ID'],
            'Product_Name': product_name,
            'Category': row['Category'],
            'action_type': action_type,
            'suggested_action': suggested_action,
            'reason': reason,
            'confidence': round(confidence * 100, 0),
            'priority_score': round(priority + min(18, impact / 100), 1),
            'forecast_units_7d': forecast_units,
            'current_stock': stock,
            'days_stock_cover': None if np.isinf(days_cover) else round(days_cover, 1),
            'profit_margin_pct': round(margin, 1),
            'revenue_impact': round(impact, 2),
        })

    product_actions = sorted(product_actions, key=lambda item: item['priority_score'], reverse=True)

    category_stats = df.groupby('Category', as_index=False).agg({
        'Total_Sale': 'sum',
        'Total_Profit': 'sum',
        'Units_Sold': 'sum',
    })
    category_stats['profit_margin_pct'] = np.where(
        category_stats['Total_Sale'] > 0,
        (category_stats['Total_Profit'] / category_stats['Total_Sale']) * 100,
        0,
    )
    total_sales = max(_safe_float(category_stats['Total_Sale'].sum()), 1.0)
    category_actions = []
    for _, row in category_stats.sort_values('Total_Sale', ascending=False).iterrows():
        share = (_safe_float(row['Total_Sale']) / total_sales) * 100
        margin = _safe_float(row['profit_margin_pct'])
        if share >= 22 and margin < _safe_float(category_stats['profit_margin_pct'].median()):
            action = f"Improve {row['Category']} margins with bundles and fewer broad discounts."
        elif share >= 22:
            action = f"Protect {row['Category']} availability; it is a major sales driver."
        elif margin >= _safe_float(category_stats['profit_margin_pct'].quantile(0.75)):
            action = f"Give {row['Category']} more visibility because margin is above average."
        else:
            action = f"Keep {row['Category']} steady and review weak products inside the category."
        category_actions.append({
            'Category': row['Category'],
            'sales_share_pct': round(share, 1),
            'profit_margin_pct': round(margin, 1),
            'units_sold': _safe_int(row['Units_Sold']),
            'suggested_action': action,
        })

    forecast_total = _safe_float(context.get('forecast_sales_total'))
    current_daily_sales = _safe_float(context.get('daily_avg_sales'))
    forecast_ratio = forecast_total / (current_daily_sales * 7) if current_daily_sales > 0 else 0
    forecast_actions = []
    if forecast_ratio >= 1.12:
        forecast_actions.append({
            'title': 'Demand is expected to rise',
            'suggested_action': 'Increase replenishment for high-velocity products before starting promotions.',
            'confidence': 82,
        })
    elif forecast_ratio <= 0.88 and forecast_ratio > 0:
        forecast_actions.append({
            'title': 'Demand is expected to soften',
            'suggested_action': 'Reduce fresh/perishable reorders and use targeted offers instead of storewide discounts.',
            'confidence': 80,
        })
    else:
        forecast_actions.append({
            'title': 'Demand looks stable',
            'suggested_action': 'Focus on margin improvement, stockout prevention, and product-level actions.',
            'confidence': 74,
        })

    summary = []
    if product_actions:
        top_action = product_actions[0]
        summary.append(f"{top_action['Product_Name']}: {top_action['suggested_action']}")
    invoice_bundles = context.get('invoice_bundle_candidates', [])
    if invoice_bundles:
        top_bundle = invoice_bundles[0]
        summary.append(
            f"Invoice baskets show {top_bundle['anchor_product']} + {top_bundle['add_on_product']} together "
            f"{top_bundle['pair_count']} times in the last 7 days; make it a promoted bundle."
        )
    if category_actions:
        summary.append(category_actions[0]['suggested_action'])
    if forecast_actions:
        summary.append(forecast_actions[0]['suggested_action'])

    result = {
        'source': 'Local AI decision engine',
        'rendering_note': 'Runs locally and is cached, so it should not noticeably slow rendering for normal dashboard data.',
        'summary': summary[:3],
        'product_actions': product_actions[:12],
        'all_product_actions': product_actions,
        'category_actions': category_actions[:8],
        'forecast_actions': forecast_actions,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'cached': False,
    }
    AI_DECISION_CACHE[cache_key] = {'created_at': time.time(), 'result': result}
    return result

def calculate_cross_selling_opportunities(df):
    """Calculate cross-selling opportunities using invoice baskets when available."""
    if df is None or len(df) == 0:
        return []

    invoice_df = build_invoice_line_frame(df)
    if 'Invoice_No' in invoice_df.columns and invoice_df.groupby('Invoice_No')['Product_Name'].nunique().max() >= 2:
        invoice_candidates = calculate_invoice_bundle_candidates(invoice_df, min_pair_count=2, limit=20)
        if invoice_candidates:
            return invoice_candidates

    # Fall back to same-day co-occurrence for older demo data that lacks true basket IDs.
    cross_sell_data = []

    daily_products = df.groupby('Date')['Product_Name'].apply(list).reset_index()
    top_products = df.groupby('Product_Name')['Units_Sold'].sum().nlargest(10).index.tolist()
    filtered_daily = daily_products[daily_products['Product_Name'].apply(lambda x: any(p in top_products for p in x))]

    for i, product_a in enumerate(top_products):
        for product_b in top_products[i+1:]:
            # Count co-occurrences
            both_bought = filtered_daily['Product_Name'].apply(
                lambda x: product_a in x and product_b in x
            ).sum()
            
            a_bought = filtered_daily['Product_Name'].apply(
                lambda x: product_a in x
            ).sum()
            
            b_bought = filtered_daily['Product_Name'].apply(
                lambda x: product_b in x
            ).sum()
            
            total_transactions = len(filtered_daily)
            
            if a_bought > 0 and b_bought > 0 and total_transactions > 0:
                support = both_bought / total_transactions
                confidence_ab = both_bought / a_bought if a_bought > 0 else 0
                confidence_ba = both_bought / b_bought if b_bought > 0 else 0
                lift_ab = confidence_ab / (b_bought / total_transactions) if (b_bought / total_transactions) > 0 else 0
                lift_ba = confidence_ba / (a_bought / total_transactions) if (a_bought / total_transactions) > 0 else 0
                
                # Use the higher lift and recommend the strongest direction for the seller.
                lift = max(lift_ab, lift_ba)
                confidence = max(confidence_ab, confidence_ba)
                
                if lift > 1.1:  # Only include meaningful associations
                    anchor_product = product_a if confidence_ab >= confidence_ba else product_b
                    add_on_product = product_b if confidence_ab >= confidence_ba else product_a
                    if lift >= 2.0:
                        recommendation = f'Create a checkout bundle: {anchor_product} + {add_on_product} with a small add-on discount'
                    elif lift >= 1.5:
                        recommendation = f'Place {add_on_product} beside {anchor_product} and promote as a pair'
                    else:
                        recommendation = f'Test a shelf tag: "Often bought with {add_on_product}" near {anchor_product}'
                    
                    cross_sell_data.append({
                        'product_a': product_a,
                        'product_b': product_b,
                        'support': support,
                        'confidence_ab': confidence,
                        'lift_ab': lift,
                        'anchor_product': anchor_product,
                        'add_on_product': add_on_product,
                        'recommendation': recommendation
                    })
    
    if not cross_sell_data:
        product_stats = df.groupby(['Product_Name', 'Category'], as_index=False).agg({
            'Units_Sold': 'sum',
            'Total_Profit': 'sum',
            'Total_Sale': 'sum',
        })
        product_stats['profit_margin_pct'] = np.where(
            product_stats['Total_Sale'] > 0,
            (product_stats['Total_Profit'] / product_stats['Total_Sale']) * 100,
            0,
        )
        fast_movers = product_stats.sort_values('Units_Sold', ascending=False).head(8).to_dict('records')
        margin_items = product_stats.sort_values('profit_margin_pct', ascending=False).head(10).to_dict('records')
        used_pairs = set()

        for anchor in fast_movers:
            candidates = [
                item for item in margin_items
                if item['Product_Name'] != anchor['Product_Name']
                and item['Category'] != anchor['Category']
            ]
            if not candidates:
                continue

            add_on = candidates[0]
            pair_key = tuple(sorted([anchor['Product_Name'], add_on['Product_Name']]))
            if pair_key in used_pairs:
                continue
            used_pairs.add(pair_key)

            total_units = max(float(product_stats['Units_Sold'].sum()), 1.0)
            support = min(float(anchor['Units_Sold']), float(add_on['Units_Sold'])) / total_units
            confidence = min(0.95, max(0.25, float(add_on['Units_Sold']) / max(float(anchor['Units_Sold']), 1.0)))
            lift = min(2.5, 1.15 + (float(add_on['profit_margin_pct']) / 100))
            recommendation = (
                f"Pair fast-moving {anchor['Product_Name']} with high-margin {add_on['Product_Name']} "
                "as an end-cap or checkout bundle"
            )
            cross_sell_data.append({
                'product_a': anchor['Product_Name'],
                'product_b': add_on['Product_Name'],
                'support': support,
                'confidence_ab': confidence,
                'lift_ab': lift,
                'anchor_product': anchor['Product_Name'],
                'add_on_product': add_on['Product_Name'],
                'recommendation': recommendation,
            })

    # Sort by lift descending
    cross_sell_data.sort(key=lambda x: x['lift_ab'], reverse=True)
    
    # Limit to top 20 for display
    return cross_sell_data[:20]

def build_dynamic_insights(df, context):
    common_params = {
        'start_date': context['start_date'],
        'end_date': context['end_date'],
        'range': context['selected_range'],
    }
    insights = []

    def add(priority, type_name, icon, title, text, action, url):
        insights.append({
            'priority': priority,
            'type': type_name,
            'icon': icon,
            'title': title,
            'text': text,
            'action': action,
            'url': url,
        })

    ai_decision = build_seller_decision_intelligence(df, context)
    product_actions = ai_decision.get('product_actions', [])
    if product_actions:
        best_action = product_actions[0]
        action_type = best_action.get('action_type', 'Recommended action')
        insight_type = 'danger' if action_type in {'Restock now', 'Prevent stockout'} else 'warning'
        if action_type in {'Protect price', 'Hold'}:
            insight_type = 'success'
        add(
            98,
            insight_type,
            'wand-magic-sparkles',
            f"Best next action: {action_type}",
            f"{best_action['Product_Name']}: {best_action['suggested_action']} Confidence {best_action['confidence']:.0f}%.",
            'Open Inventory' if action_type in {'Restock now', 'Prevent stockout', 'Reorder soon'} else 'Review Pricing',
            url_for('inventory', start_date=context['start_date'], end_date=context['end_date'])
            if action_type in {'Restock now', 'Prevent stockout', 'Reorder soon'}
            else url_for('pricing', start_date=context['start_date'], end_date=context['end_date']),
        )

    invoice_bundles = context.get('invoice_bundle_candidates', [])
    if invoice_bundles:
        top_bundle = invoice_bundles[0]
        add(
            96,
            'success',
            'receipt',
            'Invoice-proven bundle',
            f"{top_bundle['anchor_product']} and {top_bundle['add_on_product']} were purchased together in {top_bundle['pair_count']} invoices over the last 7 days. Create a bundle, shelf pairing, or checkout prompt.",
            'View Cross-Sell',
            url_for('cross_sell', **common_params),
        )

    highest_invoice = context.get('highest_invoice')
    if highest_invoice and float(highest_invoice.get('Total_Sale', 0) or 0) > 0:
        add(
            72,
            'info',
            'file-invoice-dollar',
            'Largest invoice reveals basket size',
            f"Invoice {highest_invoice['Invoice_No']} reached ${float(highest_invoice['Total_Sale']):,.2f} with {int(highest_invoice.get('Unique_Items', 0) or 0)} unique items. Review its mix for premium basket patterns.",
            'Open Analytics',
            url_for('analytics', **common_params),
        )

    sales_growth = float(context.get('sales_growth', 0) or 0)
    if sales_growth <= -15:
        add(
            95,
            'danger',
            'chart-line',
            'Sales dropped sharply',
            f"Sales are down {abs(sales_growth):.1f}% versus the previous selling day. Check staffing, stockouts, pricing, and promotion changes immediately.",
            'Review Trend',
            url_for('analytics', **common_params),
        )
    elif sales_growth >= 15:
        add(
            84,
            'success',
            'chart-line',
            'Sales momentum is strong',
            f"Sales are up {sales_growth:.1f}%. Protect stock levels for fast-moving products so the store does not miss demand.",
            'Review Trend',
            url_for('analytics', **common_params),
        )
    else:
        add(
            45,
            'primary',
            'chart-line',
            'Sales are stable',
            f"Sales movement is {sales_growth:.1f}%. Focus on margin, category mix, and product-level improvements.",
            'Review Trend',
            url_for('analytics', **common_params),
        )

    product_performance = build_product_performance(df)
    top_products = product_performance['top_products_list']
    if top_products:
        top = top_products[0]
        total_units = max(float(context.get('total_units', 0) or 0), 1.0)
        share = (float(top['Units_Sold']) / total_units) * 100
        add(
            82 if share >= 12 else 65,
            'primary',
            'trophy',
            'Top seller needs stock protection',
            f"{top['Product_Name']} leads with {int(top['Units_Sold'])} units sold ({share:.1f}% of units). Keep it visible and avoid stockouts.",
            'View Products',
            url_for('inventory', start_date=context['start_date'], end_date=context['end_date']),
        )

    category_rows = sorted(context.get('category_performance', []), key=lambda row: row.get('Total_Sale', 0), reverse=True)
    if category_rows:
        top_category = category_rows[0]
        total_sales = max(float(context.get('total_sales', 0) or 0), 1.0)
        category_share = (float(top_category['Total_Sale']) / total_sales) * 100
        category_margin = (
            (float(top_category['Total_Profit']) / float(top_category['Total_Sale'])) * 100
            if float(top_category['Total_Sale']) > 0 else 0
        )
        add(
            76 if category_share >= 30 else 55,
            'info',
            'layer-group',
            'Category mix opportunity',
            f"{top_category['Category']} drives {category_share:.1f}% of sales with {category_margin:.1f}% margin. Use the category filter to inspect product winners and weak spots.",
            'Inspect Category',
            url_for('analytics', category=top_category['Category'], **common_params),
        )

    profit_margin = float(context.get('profit_margin', 0) or 0)
    if profit_margin < 25:
        add(
            88,
            'warning',
            'percent',
            'Margin is below target',
            f"Overall margin is {profit_margin:.1f}%. Review low-margin high-volume products before discounting further.",
            'Optimize Pricing',
            url_for('pricing', start_date=context['start_date'], end_date=context['end_date']),
        )
    elif profit_margin >= 38:
        add(
            66,
            'success',
            'percent',
            'Margin is healthy',
            f"Overall margin is {profit_margin:.1f}%. Protect premium products and use bundles instead of broad discounts.",
            'Optimize Pricing',
            url_for('pricing', start_date=context['start_date'], end_date=context['end_date']),
        )

    forecast_total = float(context.get('forecast_sales_total', 0) or 0)
    daily_avg_sales = float(context.get('daily_avg_sales', 0) or 0)
    if daily_avg_sales > 0 and forecast_total > 0:
        forecast_ratio = forecast_total / (daily_avg_sales * 7)
        if forecast_ratio >= 1.12:
            add(
                78,
                'success',
                'chart-area',
                'Demand forecast is rising',
                f"Next 7-day sales are forecast at ${forecast_total:,.2f}, about {(forecast_ratio - 1) * 100:.1f}% above the current daily pace.",
                'Review Forecast',
                url_for('forecast', **common_params),
            )
        elif forecast_ratio <= 0.88:
            add(
                78,
                'warning',
                'chart-area',
                'Forecast points lower',
                f"Next 7-day sales are forecast at ${forecast_total:,.2f}, about {(1 - forecast_ratio) * 100:.1f}% below the current daily pace. Plan promos carefully.",
                'Review Forecast',
                url_for('forecast', **common_params),
            )

    waste_data = calculate_waste_reduction(df)
    risky_waste = [item for item in waste_data if item.get('waste_risk') in {'Expired', 'Very High', 'High'}]
    if risky_waste:
        worst_waste = sorted(risky_waste, key=lambda item: item.get('days_until_expiry', 999))[0]
        add(
            92,
            'danger',
            'recycle',
            'Waste risk needs action',
            f"{worst_waste['Product_Name']} is marked {worst_waste['waste_risk']}. {worst_waste['recommended_action']}",
            'Reduce Waste',
            url_for('waste', start_date=context['start_date'], end_date=context['end_date']),
        )

    pricing_data = calculate_dynamic_pricing_opportunities(df)
    action_pricing = [item for item in pricing_data if item.get('pricing_opportunity') != 'hold_price']
    if action_pricing:
        priority_order = {'price_increase': 5, 'bundle_margin': 4, 'reduce_or_replace': 3, 'visibility_push': 2, 'protect_price': 1}
        best_pricing = sorted(action_pricing, key=lambda item: priority_order.get(item.get('pricing_opportunity'), 0), reverse=True)[0]
        add(
            74,
            'warning' if best_pricing['pricing_opportunity'] in {'bundle_margin', 'reduce_or_replace'} else 'success',
            'tags',
            'Pricing action available',
            f"{best_pricing['Product_Name']}: {best_pricing['suggested_action']}",
            'Optimize Pricing',
            url_for('pricing', start_date=context['start_date'], end_date=context['end_date']),
        )

    cross_sell_data = calculate_cross_selling_opportunities(df)
    if cross_sell_data:
        best_bundle = cross_sell_data[0]
        add(
            68,
            'primary',
            'basket-shopping',
            'Bundle opportunity',
            best_bundle['recommendation'],
            'View Cross-Sell',
            url_for('cross_sell', start_date=context['start_date'], end_date=context['end_date']),
        )

    insights = sorted(insights, key=lambda item: item['priority'], reverse=True)
    for insight in insights:
        insight.pop('priority', None)
    return insights[:6]

# ------------------- Advanced Analytics Routes -------------------
@app.route('/analytics')
@login_required
def analytics():
    df = load_data()
    if df is None:
        return "No data found. Please add CSV files to the data folder.", 404
    filtered_df, range_context = apply_date_filter(df)
    selected_category = request.args.get('category', '').strip()
    analytics_df, selected_category, categories = apply_category_filter(filtered_df, selected_category)
    movement_context = build_period_movement(
        df,
        range_context['start_date'],
        range_context['end_date'],
        selected_category,
    )
    context = {
        **range_context,
        **build_metrics(analytics_df, range_context['start_date'], range_context['end_date']),
        **movement_context,
        **build_charts(analytics_df),
        **build_product_performance(analytics_df),
        **inventory_metrics(load_inventory_items()),
        'categories': categories,
        'selected_category': selected_category,
        'category_filter_label': selected_category or 'All Categories',
    }
    return render_template('analytics.html', **context)

@app.route('/forecast')
@login_required
def forecast():
    df, context = data_context()
    if df is None:
        return "No data found. Please add CSV files to the data folder.", 404
    context['last_sales_ma'] = context['forecast_sales_total'] / 7 if context['forecast_sales_total'] else 0
    context['last_profit_ma'] = context['forecast_profit_total'] / 7 if context['forecast_profit_total'] else 0
    context['ai_decision'] = build_seller_decision_intelligence(df, context)
    context['forecast_owner_plan'] = build_forecast_owner_plan(context, context['ai_decision'])
    return render_template('forecast.html', **context)

@app.route('/insights')
@login_required
def insights():
    df, context = data_context()
    if df is None:
        return "No data found. Please add CSV files to the data folder.", 404
    insights_data = build_dynamic_insights(df, context)
    ai_decision = build_seller_decision_intelligence(df, context)
    return render_template('insights.html', insights_data=insights_data, ai_decision=ai_decision, **context)

@app.route('/inventory')
@login_required
def inventory():
    df = load_data()
    if df is None or df.empty:
        filtered_df = pd.DataFrame(columns=['Date', 'Product_ID', 'Product_Name', 'Category', 'Units_Sold', 'Unit_Price', 'Profit_Per_Unit', 'Total_Sale', 'Total_Profit'])
        range_context = {
            'period_label': 'No sales recorded yet',
            'start_date': datetime.now().strftime('%Y-%m-%d'),
            'end_date': datetime.now().strftime('%Y-%m-%d'),
        }
    else:
        filtered_df, range_context = apply_date_filter(df)

    inventory_items = load_inventory_items()
    performance = build_product_performance(filtered_df)
    item_by_id = {item['product_id']: item for item in inventory_items}
    inventory_rows = []
    for item in inventory_items:
        sold_row = next((row for row in performance['top_products_list'] + performance['worst_products_list'] if row['Product_ID'] == item['product_id']), None)
        units_sold = int(sold_row['Units_Sold']) if sold_row else 0
        current_stock = int(item['current_stock'])
        reorder_level = int(item['reorder_level'])
        if current_stock <= 0:
            stock_status = 'Out of Stock'
        elif current_stock <= reorder_level:
            stock_status = 'Low Stock'
        else:
            stock_status = 'In Stock'
        inventory_rows.append({
            **item,
            'units_sold': units_sold,
            'stock_status': stock_status,
        })

    return render_template('inventory.html',
        inventory_items=inventory_rows,
        **inventory_metrics(inventory_items),
        **performance,
        **range_context
    )

@app.route('/waste')
@login_required
def waste():
    df = load_data()
    if df is None:
        return render_template('error.html', message="No data found. Please add CSV files to the 'data/' folder."), 404
    
    # Get date range from query params or use defaults
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    if start_date_str and end_date_str:
        try:
            start_date = pd.to_datetime(start_date_str)
            end_date = pd.to_datetime(end_date_str)
            # Validate dates are in our data range
            min_date = df['Date'].min()
            max_date = df['Date'].max()
            if start_date < min_date:
                start_date = min_date
            if end_date > max_date:
                end_date = max_date
            if start_date > end_date:
                # Swap if start > end
                start_date, end_date = end_date, start_date
        except:
            # Invalid dates, fall back to defaults
            start_date = None
            end_date = None
    else:
        start_date = None
        end_date = None

    # Filter data based on date range
    if start_date is not None and end_date is not None:
        filtered_df = df[(df['Date'] >= start_date) & (df['Date'] <= end_date)]
        period_label = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
    else:
        # Default to showing all data
        filtered_df = df
        period_label = f"{df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}"

    # Calculate waste reduction
    waste_data = calculate_waste_reduction(filtered_df)
    
    return render_template('waste.html',
        waste_data=waste_data,
        period_label=period_label,
        start_date=start_date_str or filtered_df['Date'].min().strftime('%Y-%m-%d'),
        end_date=end_date_str or filtered_df['Date'].max().strftime('%Y-%m-%d')
    )

@app.route('/pricing')
@login_required
def pricing():
    df = load_data()
    if df is None:
        return render_template('error.html', message="No data found. Please add CSV files to the 'data/' folder."), 404
    
    # Get date range from query params or use defaults
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    if start_date_str and end_date_str:
        try:
            start_date = pd.to_datetime(start_date_str)
            end_date = pd.to_datetime(end_date_str)
            # Validate dates are in our data range
            min_date = df['Date'].min()
            max_date = df['Date'].max()
            if start_date < min_date:
                start_date = min_date
            if end_date > max_date:
                end_date = max_date
            if start_date > end_date:
                # Swap if start > end
                start_date, end_date = end_date, start_date
        except:
            # Invalid dates, fall back to defaults
            start_date = None
            end_date = None
    else:
        start_date = None
        end_date = None

    # Filter data based on date range
    if start_date is not None and end_date is not None:
        filtered_df = df[(df['Date'] >= start_date) & (df['Date'] <= end_date)]
        period_label = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
    else:
        # Default to showing all data
        filtered_df = df
        period_label = f"{df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}"

    # Calculate dynamic pricing opportunities
    pricing_data = calculate_dynamic_pricing_opportunities(filtered_df)
    ai_context = {
        'start_date': start_date_str or filtered_df['Date'].min().strftime('%Y-%m-%d'),
        'end_date': end_date_str or filtered_df['Date'].max().strftime('%Y-%m-%d'),
        'period_label': period_label,
        'selected_range': request.args.get('range', ''),
        **build_metrics(
            filtered_df,
            start_date_str or filtered_df['Date'].min().strftime('%Y-%m-%d'),
            end_date_str or filtered_df['Date'].max().strftime('%Y-%m-%d'),
        ),
        **build_forecast_context(daily_totals(filtered_df)),
    }
    ai_decision = build_seller_decision_intelligence(filtered_df, ai_context)
    action_lookup = {
        (item.get('Product_Name'), item.get('Category')): item
        for item in ai_decision.get('all_product_actions', ai_decision.get('product_actions', []))
    }
    for item in pricing_data:
        seller_action = action_lookup.get((item.get('Product_Name'), item.get('Category')))
        if seller_action:
            item['seller_action_type'] = seller_action['action_type']
            item['seller_suggested_action'] = seller_action['suggested_action']
            item['seller_priority_score'] = seller_action['priority_score']
            item['seller_confidence'] = seller_action['confidence']
        else:
            item['seller_action_type'] = item.get('opportunity_label', 'Hold')
            item['seller_suggested_action'] = item.get('suggested_action', 'Hold current plan.')
            fallback_priority = {
                'price_increase': 72,
                'bundle_margin': 68,
                'reduce_or_replace': 64,
                'visibility_push': 58,
                'protect_price': 52,
                'hold_price': 35,
            }.get(item.get('pricing_opportunity'), 35)
            item['seller_priority_score'] = fallback_priority
            item['seller_confidence'] = 60 if item.get('pricing_opportunity') != 'hold_price' else 54

    opportunity_priority = {
        'price_increase': 5,
        'bundle_margin': 4,
        'reduce_or_replace': 3,
        'visibility_push': 2,
        'protect_price': 1,
        'hold_price': 0,
    }
    pricing_data = sorted(
        pricing_data,
        key=lambda item: (
            item.get('seller_priority_score', 0),
            opportunity_priority.get(item.get('pricing_opportunity'), 0),
            item.get('Units_Sold', 0),
            item.get('Total_Sale', 0),
        ),
        reverse=True,
    )
    actionable_pricing = [item for item in pricing_data if item.get('pricing_opportunity') != 'hold_price']
    pricing_summary = {
        'products_analyzed': len(pricing_data),
        'actionable_count': len(actionable_pricing),
        'bundle_count': sum(1 for item in pricing_data if item.get('pricing_opportunity') == 'bundle_margin'),
        'avg_profit_margin': (
            sum(float(item.get('profit_margin_pct', 0) or 0) for item in pricing_data) / len(pricing_data)
            if pricing_data else 0
        ),
        'revenue_impact': sum(float(item.get('Total_Sale', 0) or 0) for item in actionable_pricing) * 0.1,
    }
    
    return render_template('pricing.html',
        pricing_data=pricing_data,
        pricing_summary=pricing_summary,
        pricing_owner_summary=build_pricing_owner_summary(pricing_data),
        ai_decision=ai_decision,
        period_label=period_label,
        start_date=start_date_str or filtered_df['Date'].min().strftime('%Y-%m-%d'),
        end_date=end_date_str or filtered_df['Date'].max().strftime('%Y-%m-%d')
    )

@app.route('/cross-sell')
@login_required
def cross_sell():
    df = load_data()
    if df is None:
        return render_template('error.html', message="No data found. Please add CSV files to the 'data/' folder."), 404
    
    # Get date range from query params or use defaults
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    if start_date_str and end_date_str:
        try:
            start_date = pd.to_datetime(start_date_str)
            end_date = pd.to_datetime(end_date_str)
            # Validate dates are in our data range
            min_date = df['Date'].min()
            max_date = df['Date'].max()
            if start_date < min_date:
                start_date = min_date
            if end_date > max_date:
                end_date = max_date
            if start_date > end_date:
                # Swap if start > end
                start_date, end_date = end_date, start_date
        except:
            # Invalid dates, fall back to defaults
            start_date = None
            end_date = None
    else:
        start_date = None
        end_date = None

    # Filter data based on date range
    if start_date is not None and end_date is not None:
        filtered_df = df[(df['Date'] >= start_date) & (df['Date'] <= end_date)]
        period_label = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
    else:
        # Default to showing all data
        filtered_df = df
        period_label = f"{df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}"

    # Calculate cross-selling opportunities
    cross_sell_data = calculate_cross_selling_opportunities(filtered_df)
    
    return render_template('cross_sell.html',
        cross_sell_data=cross_sell_data,
        cross_sell_owner_summary=build_cross_sell_owner_summary(cross_sell_data),
        period_label=period_label,
        start_date=start_date_str or filtered_df['Date'].min().strftime('%Y-%m-%d'),
        end_date=end_date_str or filtered_df['Date'].max().strftime('%Y-%m-%d')
    )

@app.route('/about')
def about():
    return render_template('about.html')


# ------------------- Run -------------------
if __name__ == '__main__':
    # Use host='0.0.0.0' to make it accessible on your network if desired
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        start_sql_server_scheduler()
    app.run(host='127.0.0.1', port=5000, debug=True)
