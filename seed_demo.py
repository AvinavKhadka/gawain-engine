"""
Run this once to create a demo SalesDB in SQL Server for testing.
Usage: python seed_demo.py
"""
import pyodbc
from config import DB_SERVER, DB_DRIVER, DB_USER, DB_PASSWORD
import random
from datetime import date, timedelta

def get_master_conn():
    if DB_USER and DB_PASSWORD:
        cs = f"DRIVER={{{DB_DRIVER}}};SERVER={DB_SERVER};DATABASE=master;UID={DB_USER};PWD={DB_PASSWORD};"
    else:
        cs = f"DRIVER={{{DB_DRIVER}}};SERVER={DB_SERVER};DATABASE=master;Trusted_Connection=yes;"
    conn = pyodbc.connect(cs, autocommit=True)
    return conn

DDL = """
IF DB_ID('SalesDB') IS NULL CREATE DATABASE SalesDB;
"""

TABLES = """
USE SalesDB;

IF OBJECT_ID('dbo.FactSales', 'U') IS NOT NULL DROP TABLE dbo.FactSales;
IF OBJECT_ID('dbo.DimProduct', 'U') IS NOT NULL DROP TABLE dbo.DimProduct;
IF OBJECT_ID('dbo.DimCustomer', 'U') IS NOT NULL DROP TABLE dbo.DimCustomer;
IF OBJECT_ID('dbo.DimRegion', 'U') IS NOT NULL DROP TABLE dbo.DimRegion;
IF OBJECT_ID('dbo.DimDate', 'U') IS NOT NULL DROP TABLE dbo.DimDate;

CREATE TABLE dbo.DimDate (
    DateKey   INT PRIMARY KEY,
    FullDate  DATE NOT NULL,
    Year      INT,
    Month     INT,
    MonthName NVARCHAR(20),
    Quarter   INT,
    WeekDay   NVARCHAR(20)
);

CREATE TABLE dbo.DimProduct (
    ProductKey  INT PRIMARY KEY IDENTITY(1,1),
    ProductName NVARCHAR(100),
    Category    NVARCHAR(50),
    SubCategory NVARCHAR(50),
    UnitCost    DECIMAL(10,2)
);

CREATE TABLE dbo.DimCustomer (
    CustomerKey  INT PRIMARY KEY IDENTITY(1,1),
    CustomerName NVARCHAR(100),
    Segment      NVARCHAR(50)
);

CREATE TABLE dbo.DimRegion (
    RegionKey  INT PRIMARY KEY IDENTITY(1,1),
    RegionName NVARCHAR(50),
    Country    NVARCHAR(50)
);

CREATE TABLE dbo.FactSales (
    SaleID      INT PRIMARY KEY IDENTITY(1,1),
    DateKey     INT REFERENCES dbo.DimDate(DateKey),
    ProductKey  INT REFERENCES dbo.DimProduct(ProductKey),
    CustomerKey INT REFERENCES dbo.DimCustomer(CustomerKey),
    RegionKey   INT REFERENCES dbo.DimRegion(RegionKey),
    Quantity    INT,
    UnitPrice   DECIMAL(10,2),
    TotalAmount DECIMAL(10,2),
    Discount    DECIMAL(5,2)
);
"""

def seed():
    conn = get_master_conn()
    cursor = conn.cursor()
    cursor.execute(DDL)
    conn.close()

    if DB_USER and DB_PASSWORD:
        cs = f"DRIVER={{{DB_DRIVER}}};SERVER={DB_SERVER};DATABASE=SalesDB;UID={DB_USER};PWD={DB_PASSWORD};"
    else:
        cs = f"DRIVER={{{DB_DRIVER}}};SERVER={DB_SERVER};DATABASE=SalesDB;Trusted_Connection=yes;"
    conn = pyodbc.connect(cs, autocommit=True)
    cursor = conn.cursor()

    for stmt in TABLES.split(";"):
        s = stmt.strip()
        if s:
            cursor.execute(s)

    # Seed DimDate (last 90 days)
    today = date.today()
    months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    for i in range(90):
        d = today - timedelta(days=89-i)
        cursor.execute(
            "INSERT INTO DimDate VALUES (?,?,?,?,?,?,?)",
            int(d.strftime("%Y%m%d")), d, d.year, d.month,
            months[d.month-1], (d.month-1)//3+1, d.strftime("%A")
        )

    # Seed dimensions
    products = [
        ("Laptop Pro 15","Electronics","Computers",800),
        ("Wireless Mouse","Electronics","Peripherals",20),
        ("Office Chair","Furniture","Seating",250),
        ("Standing Desk","Furniture","Desks",400),
        ("Monitor 27in","Electronics","Displays",350),
        ("Headphones","Electronics","Audio",120),
        ("Keyboard Mech","Electronics","Peripherals",90),
        ("Webcam HD","Electronics","Peripherals",60),
    ]
    for name, cat, sub, cost in products:
        cursor.execute("INSERT INTO DimProduct (ProductName,Category,SubCategory,UnitCost) VALUES (?,?,?,?)",
                       name, cat, sub, cost)

    customers = [
        ("Acme Corp","Enterprise"), ("Global Inc","Enterprise"),
        ("StartupXYZ","SMB"), ("Local Shop","SMB"),
        ("MegaCorp","Enterprise"), ("Jane Doe","Consumer"),
        ("John Smith","Consumer"), ("TechFirm Ltd","Enterprise"),
    ]
    for name, seg in customers:
        cursor.execute("INSERT INTO DimCustomer (CustomerName,Segment) VALUES (?,?)", name, seg)

    regions = [
        ("North","USA"), ("South","USA"),
        ("East","USA"), ("West","USA"), ("EMEA","Europe"),
    ]
    for name, country in regions:
        cursor.execute("INSERT INTO DimRegion (RegionName,Country) VALUES (?,?)", name, country)

    # Seed FactSales — drop sales on TODAY to simulate a drop
    dates = [today - timedelta(days=i) for i in range(89, -1, -1)]
    date_keys = [int(d.strftime("%Y%m%d")) for d in dates]

    random.seed(42)
    for dk, d in zip(date_keys, dates):
        is_today = (d == today)
        is_weekend = d.weekday() >= 5
        row_count = random.randint(2, 5) if (is_today or is_weekend) else random.randint(8, 20)
        # Today simulates a drop: only 3 rows and only small products
        for _ in range(row_count):
            prod_id = random.choice([2, 3, 7]) if is_today else random.randint(1, 8)
            cust_id = random.randint(1, 8)
            region_id = random.randint(1, 5)
            qty = random.randint(1, 5)
            price = [800,20,250,400,350,120,90,60][prod_id-1] * random.uniform(0.9, 1.2)
            discount = round(random.choice([0, 0, 0, 5, 10]), 2)
            total = round(qty * price * (1 - discount/100), 2)
            cursor.execute(
                "INSERT INTO FactSales (DateKey,ProductKey,CustomerKey,RegionKey,Quantity,UnitPrice,TotalAmount,Discount) "
                "VALUES (?,?,?,?,?,?,?,?)",
                dk, prod_id, cust_id, region_id, qty, round(price,2), total, discount
            )

    conn.close()
    print("Demo SalesDB seeded successfully.")
    print(f"Today ({today}) has artificially reduced sales — try asking: 'Why did sales drop today?'")

if __name__ == "__main__":
    seed()
