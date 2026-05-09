from pathlib import Path


OUTPUT = Path("data/sql_server_pos_test_5000.sql")


sql = r"""/*
MarketPulse SQL Server POS test database

How to create the .bak in SSMS:
1. Create C:\Temp on the SQL Server machine if it does not already exist.
2. Open this script in SSMS.
3. Execute it against the server.
4. The script creates MarketPulsePOSTest with:
   - 5000 invoice line entries in dbo.InvoiceDetail
   - invoice headers in dbo.InvoiceHeader
   - products in dbo.Products
5. It then writes:
   C:\Temp\MarketPulsePOSTest_5000.bak

The dashboard SQL Server import query can use the final SELECT at the bottom.
*/

USE master;
GO

IF DB_ID(N'MarketPulsePOSTest') IS NOT NULL
BEGIN
    ALTER DATABASE MarketPulsePOSTest SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE MarketPulsePOSTest;
END
GO

CREATE DATABASE MarketPulsePOSTest;
GO

USE MarketPulsePOSTest;
GO

CREATE TABLE dbo.Products (
    ProductCode NVARCHAR(30) NOT NULL PRIMARY KEY,
    ProductName NVARCHAR(120) NOT NULL,
    CategoryName NVARCHAR(80) NOT NULL,
    Barcode NVARCHAR(60) NULL,
    Supplier NVARCHAR(120) NULL,
    CostPrice DECIMAL(12, 2) NOT NULL,
    UnitPrice DECIMAL(12, 2) NOT NULL,
    CurrentStock INT NOT NULL,
    ReorderLevel INT NOT NULL
);
GO

CREATE TABLE dbo.InvoiceHeader (
    InvoiceID INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    InvoiceNo NVARCHAR(40) NOT NULL UNIQUE,
    InvoiceDate DATETIME2(0) NOT NULL,
    PaymentMode NVARCHAR(30) NOT NULL,
    CustomerID NVARCHAR(30) NULL,
    CashierID NVARCHAR(30) NOT NULL,
    StoreID NVARCHAR(30) NOT NULL
);
GO

CREATE TABLE dbo.InvoiceDetail (
    InvoiceDetailID INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    InvoiceID INT NOT NULL,
    ProductCode NVARCHAR(30) NOT NULL,
    Quantity INT NOT NULL,
    UnitPrice DECIMAL(12, 2) NOT NULL,
    DiscountAmount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    TaxAmount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    LineTotal DECIMAL(12, 2) NOT NULL,
    LineProfit DECIMAL(12, 2) NOT NULL,
    CONSTRAINT FK_InvoiceDetail_Header FOREIGN KEY (InvoiceID) REFERENCES dbo.InvoiceHeader(InvoiceID),
    CONSTRAINT FK_InvoiceDetail_Product FOREIGN KEY (ProductCode) REFERENCES dbo.Products(ProductCode)
);
GO

INSERT INTO dbo.Products
    (ProductCode, ProductName, CategoryName, Barcode, Supplier, CostPrice, UnitPrice, CurrentStock, ReorderLevel)
VALUES
    (N'P001', N'Bananas', N'Produce', N'890000000001', N'Fresh Valley', 0.39, 0.62, 820, 120),
    (N'P002', N'Apples', N'Produce', N'890000000002', N'Fresh Valley', 0.78, 1.15, 650, 100),
    (N'P003', N'Milk 1L', N'Dairy', N'890000000003', N'Daily Dairy', 1.63, 2.25, 480, 90),
    (N'P004', N'Bread Loaf', N'Bakery', N'890000000004', N'Bake House', 1.55, 2.10, 360, 80),
    (N'P005', N'Eggs 12 Pack', N'Dairy', N'890000000005', N'Farm Fresh', 2.85, 3.80, 420, 70),
    (N'P006', N'Butter', N'Dairy', N'890000000006', N'Daily Dairy', 3.35, 4.40, 310, 60),
    (N'P007', N'Peanut Butter', N'Pantry', N'890000000007', N'NutriFoods', 3.60, 5.25, 260, 45),
    (N'P008', N'Strawberry Jam', N'Pantry', N'890000000008', N'NutriFoods', 3.30, 4.75, 280, 45),
    (N'P009', N'Pasta', N'Pantry', N'890000000009', N'Urban Foods', 1.65, 2.35, 520, 90),
    (N'P010', N'Tomato Sauce', N'Pantry', N'890000000010', N'Urban Foods', 2.08, 2.90, 510, 85),
    (N'P011', N'Coffee', N'Beverages', N'890000000011', N'Morning Co', 6.10, 8.90, 210, 40),
    (N'P012', N'Sugar', N'Pantry', N'890000000012', N'Sweet Mill', 1.68, 2.20, 600, 100),
    (N'P013', N'Tea Bags', N'Beverages', N'890000000013', N'Morning Co', 3.10, 4.30, 240, 40),
    (N'P014', N'Biscuits', N'Snacks', N'890000000014', N'Crunchy Bite', 1.82, 2.60, 720, 120),
    (N'P015', N'Chips', N'Snacks', N'890000000015', N'Crunchy Bite', 1.99, 2.85, 680, 110),
    (N'P016', N'Salsa', N'Snacks', N'890000000016', N'Urban Foods', 2.90, 3.95, 340, 65),
    (N'P017', N'Rice 5kg', N'Staples', N'890000000017', N'Grain Depot', 10.40, 12.50, 180, 35),
    (N'P018', N'Cooking Oil', N'Staples', N'890000000018', N'Grain Depot', 7.90, 9.80, 210, 35),
    (N'P019', N'Chicken Breast', N'Meat', N'890000000019', N'Prime Meat', 5.45, 7.20, 160, 30),
    (N'P020', N'Yogurt Cups', N'Dairy', N'890000000020', N'Daily Dairy', 2.52, 3.40, 380, 70);
GO

DECLARE @InvoiceCount INT = 900;
DECLARE @LineCount INT = 5000;
DECLARE @StartDate DATE = '2026-04-01';

;WITH InvoiceNumbers AS (
    SELECT TOP (@InvoiceCount)
        ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS n
    FROM sys.all_objects a
    CROSS JOIN sys.all_objects b
)
INSERT INTO dbo.InvoiceHeader
    (InvoiceNo, InvoiceDate, PaymentMode, CustomerID, CashierID, StoreID)
SELECT
    CONCAT(N'INV-', FORMAT(DATEADD(day, (n - 1) % 14, @StartDate), 'yyyyMMdd'), N'-', RIGHT(CONCAT(N'00000', n), 5)),
    DATEADD(minute, (n * 11) % 720, CAST(DATEADD(day, (n - 1) % 14, @StartDate) AS DATETIME2(0))),
    CASE n % 4 WHEN 0 THEN N'Cash' WHEN 1 THEN N'Card' WHEN 2 THEN N'UPI' ELSE N'Wallet' END,
    CONCAT(N'CU', RIGHT(CONCAT(N'0000', (n % 320) + 1), 4)),
    CONCAT(N'C', RIGHT(CONCAT(N'00', (n % 8) + 1), 2)),
    CASE WHEN n % 5 = 0 THEN N'STORE-002' ELSE N'STORE-001' END
FROM InvoiceNumbers;
GO

;WITH LineNumbers AS (
    SELECT TOP (5000)
        ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS n
    FROM sys.all_objects a
    CROSS JOIN sys.all_objects b
),
LineProducts AS (
    SELECT
        n,
        ((n - 1) % 900) + 1 AS InvoiceID,
        CASE
            WHEN n % 10 IN (0, 1) THEN N'P004'
            WHEN n % 10 IN (2, 3) THEN N'P006'
            WHEN n % 10 = 4 THEN N'P003'
            WHEN n % 10 = 5 THEN N'P005'
            WHEN n % 10 = 6 THEN N'P009'
            WHEN n % 10 = 7 THEN N'P010'
            WHEN n % 20 = 8 THEN N'P007'
            WHEN n % 20 = 9 THEN N'P008'
            WHEN n % 20 = 10 THEN N'P015'
            WHEN n % 20 = 11 THEN N'P016'
            ELSE CONCAT(N'P', RIGHT(CONCAT(N'000', ((n * 7) % 20) + 1), 3))
        END AS ProductCode,
        ((n * 3) % 4) + 1 AS Quantity,
        CASE WHEN n % 17 = 0 THEN 0.05 WHEN n % 23 = 0 THEN 0.03 ELSE 0 END AS DiscountRate
    FROM LineNumbers
)
INSERT INTO dbo.InvoiceDetail
    (InvoiceID, ProductCode, Quantity, UnitPrice, DiscountAmount, TaxAmount, LineTotal, LineProfit)
SELECT
    lp.InvoiceID,
    lp.ProductCode,
    lp.Quantity,
    p.UnitPrice,
    CAST((p.UnitPrice * lp.Quantity) * lp.DiscountRate AS DECIMAL(12, 2)) AS DiscountAmount,
    CAST(((p.UnitPrice * lp.Quantity) - ((p.UnitPrice * lp.Quantity) * lp.DiscountRate)) * 0.05 AS DECIMAL(12, 2)) AS TaxAmount,
    CAST((p.UnitPrice * lp.Quantity) - ((p.UnitPrice * lp.Quantity) * lp.DiscountRate) AS DECIMAL(12, 2)) AS LineTotal,
    CAST(((p.UnitPrice - p.CostPrice) * lp.Quantity) - ((p.UnitPrice * lp.Quantity) * lp.DiscountRate) AS DECIMAL(12, 2)) AS LineProfit
FROM LineProducts lp
JOIN dbo.Products p ON p.ProductCode = lp.ProductCode;
GO

CREATE INDEX IX_InvoiceHeader_Date ON dbo.InvoiceHeader(InvoiceDate);
CREATE INDEX IX_InvoiceDetail_InvoiceID ON dbo.InvoiceDetail(InvoiceID);
CREATE INDEX IX_InvoiceDetail_ProductCode ON dbo.InvoiceDetail(ProductCode);
GO

SELECT COUNT(*) AS InvoiceCount FROM dbo.InvoiceHeader;
SELECT COUNT(*) AS InvoiceLineEntryCount FROM dbo.InvoiceDetail;
GO

-- Dashboard import query:
SELECT
    h.InvoiceNo AS Invoice_No,
    h.InvoiceDate AS Date,
    d.ProductCode AS Product_ID,
    p.ProductName AS Product_Name,
    p.CategoryName AS Category,
    d.Quantity AS Units_Sold,
    d.UnitPrice AS Unit_Price,
    CAST(d.UnitPrice - p.CostPrice AS DECIMAL(12, 2)) AS Profit_Per_Unit,
    d.LineTotal AS Total_Sale,
    d.LineProfit AS Total_Profit,
    d.DiscountAmount AS Discount_Amount,
    d.TaxAmount AS Tax_Amount,
    h.PaymentMode AS Payment_Mode,
    h.CustomerID AS Customer_ID,
    h.CashierID AS Cashier_ID,
    h.StoreID AS Store_ID,
    p.Barcode AS Barcode,
    p.Supplier AS Supplier,
    p.CurrentStock AS Current_Stock,
    p.ReorderLevel AS Reorder_Level
FROM dbo.InvoiceHeader h
JOIN dbo.InvoiceDetail d ON d.InvoiceID = h.InvoiceID
JOIN dbo.Products p ON p.ProductCode = d.ProductCode
ORDER BY h.InvoiceDate, h.InvoiceNo, d.InvoiceDetailID;
GO

USE master;
GO

BACKUP DATABASE MarketPulsePOSTest
TO DISK = N'C:\Temp\MarketPulsePOSTest_5000.bak'
WITH INIT, FORMAT, COMPRESSION, STATS = 10;
GO
"""


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(sql, encoding="utf-8", newline="\n")
    print(OUTPUT)


if __name__ == "__main__":
    main()
